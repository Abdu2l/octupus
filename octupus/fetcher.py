"""Async pooled fetcher with browser impersonation (curl_cffi).

Tuned per research: single reused AsyncSession(max_clients=200, trust_env=False),
no per-request impersonate churn (rotate only on retry), H2 default,
max_redirects=3, bytes body path, libcurl DNS cache, queue worker pool.
"""
import asyncio
import random
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from .frontier import AutoThrottle, PerHostLimiter, ProxyRotator

IMPERSONATE_POOL = ("chrome", "chrome131", "safari18_0")

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: int = 0
    html: str = ""
    final_url: str = ""
    error: str = ""


from functools import lru_cache as _lru


@_lru(maxsize=1)
def _cached_bf_headers() -> dict:
    try:
        from browserforge.headers import HeaderGenerator as _Hg

        gen = _Hg(browser=["chrome"], os=["windows", "linux", "macos"])
        return dict(gen.generate())
    except Exception:
        return {}


def _headers_for(url: str, stealth: bool, google_search: bool) -> dict:
    # Their win: stealth always sets a Google referer (fixed, no urlparse cost).
    # Ours: same default, site-specific only when explicitly asked.
    h = dict(STEALTH_HEADERS if stealth else BASE_HEADERS)
    if stealth and not google_search:
        h.setdefault("Referer", "https://www.google.com/")
    elif google_search:
        try:
            host = urlparse(url).netloc
            h["Referer"] = f"https://www.google.com/search?q=site:{host}"
        except Exception:
            pass
    # version-matched real headers when browserforge installed: cached once,
    # not per-request (per-request generation costs ~1.5s and kills throughput)
    if stealth:
        try:
            for k, v in _cached_bf_headers().items():
                if k.lower() in ("user-agent", "referer"):
                    continue
                h.setdefault(k, v)
        except Exception:
            pass
    return h


def _retry_after(resp) -> float:
    try:
        v = resp.headers.get("retry-after", "") if getattr(resp, "headers", None) else ""
        return max(0.0, min(30.0, float(str(v).strip())))
    except Exception:
        return 0.0


async def _fetch_one(
    session,
    url: str,
    timeout: int,
    retries: int,
    host_limiter: PerHostLimiter,
    throttle: AutoThrottle,
    proxies: ProxyRotator,
    stealth: bool,
    google_search: bool,
    impersonate_default: str = "chrome",
    rotate_impersonate: bool = True,
    cookies: dict | None = None,
    user_agent: str = "",
) -> FetchResult:
    from curl_cffi.requests.exceptions import RequestException

    attempt = 0
    headers = _headers_for(url, stealth, google_search)
    if user_agent:
        headers = dict(headers)
        headers["User-Agent"] = user_agent  # pin to solving browser UA (clearance is UA-bound)
    impersonate_eff = impersonate_default
    while True:
        sema = await host_limiter.acquire(url)
        t0 = time.monotonic()
        try:
            await throttle.wait(url)
            proxy = await proxies.next() if proxies else None
            kwargs: dict = {
                "headers": headers,
                "allow_redirects": True,
                "timeout": timeout,
                "max_redirects": 3,
            }
            if proxy:
                kwargs["proxy"] = proxy
            if cookies:
                kwargs["cookies"] = cookies
            # rotate fingerprint only on retry (new TLS handshake kills reuse)
            if attempt > 0 and rotate_impersonate:
                kwargs["impersonate"] = random.choice(IMPERSONATE_POOL)
            elif impersonate_eff:
                kwargs["impersonate"] = impersonate_eff
            resp = await session.get(url, **kwargs)
            latency = time.monotonic() - t0
            status = int(getattr(resp, "status_code", 0) or 0)
            final_url = str(getattr(resp, "url", url) or url)
            await throttle.mark(url, latency, status)

            if status in RETRYABLE_STATUS and attempt < retries:
                attempt += 1
                ra = _retry_after(resp)
                # fixed 1s like theirs (faster than exponential on transient 429/5xx)
                await asyncio.sleep(max(ra, 1.0 + random.uniform(0, 0.3)))
                continue
            try:
                raw = getattr(resp, "content", b"") or b""
                text = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
            except Exception:
                try:
                    text = resp.text or ""
                except Exception:
                    text = ""
            if status >= 400 and status not in RETRYABLE_STATUS:
                return FetchResult(url=url, status=status, html=text[:2_000_000], final_url=final_url)
            return FetchResult(url=url, status=status, html=text[:2_000_000], final_url=final_url)
        except RequestException as e:
            await throttle.mark(url, time.monotonic() - t0, 503)
            if attempt < retries:
                attempt += 1
                # local blips (reset/refused/closed) retry fast; real blocks wait 1s
                msg = str(e).lower()
                fast = any(k in msg for k in ("reset", "refused", "closed", "eof", "broken"))
                await asyncio.sleep(0.2 + random.uniform(0, 0.1) if fast else 1.0 + random.uniform(0, 0.3))
                continue
            return FetchResult(url=url, status=0, html="", final_url=url, error="request_error")
        except Exception as e:
            name = type(e).__name__.lower()
            msg = str(e).lower()
            retryable = any(k in name for k in ("timeout", "connect", "resolve", "closed", "reset", "proxy"))
            await throttle.mark(url, time.monotonic() - t0, 503 if retryable else 400)
            if retryable and attempt < retries:
                attempt += 1
                fast = any(k in msg for k in ("reset", "refused", "closed", "eof", "broken")) or "timeout" in name
                await asyncio.sleep(0.2 + random.uniform(0, 0.1) if fast else 1.0 + random.uniform(0, 0.3))
                continue
            return FetchResult(url=url, status=0, html="", final_url=url, error=f"{type(e).__name__}"[:120])
        finally:
            try:
                sema.release()
            except Exception:
                pass


async def fetch_stream(
    urls: list[str],
    concurrency: int = 100,
    per_host: int = 10,
    timeout: int = 20,
    retries: int = 3,
    impersonate: str = "chrome",
    autothrottle: bool = True,
    base_delay: float = 0.0,
    proxies: list[str] | None = None,
    stealth_headers: bool = True,
    google_search: bool = False,
    cookies: dict | None = None,
    user_agent: str = "",
):
    """Yield FetchResult as each completes. Semaphore + as_completed (low overhead)."""
    from curl_cffi.requests import AsyncSession

    concurrency = max(1, min(concurrency, 500))
    gate = asyncio.Semaphore(concurrency)
    host_limiter = PerHostLimiter(per_host)
    throttle = AutoThrottle(enabled=autothrottle, base=base_delay)
    rotator = ProxyRotator(proxies)
    # Session POOL (not one shared session): curl_cffi async doesn't like many
    # concurrent requests on a single session; separate sessions handshake more
    # but never stall each other. Pool of 8 = reuse + true parallelism.
    POOL_N = 8
    _sess_kw: dict = {"max_clients": max(50, concurrency), "trust_env": False}
    if cookies:
        _sess_kw["cookies"] = cookies
    sessions: list = []
    try:
        for _ in range(POOL_N):
            try:
                sessions.append(AsyncSession(impersonate=impersonate or "chrome", timeout=timeout, **_sess_kw))
            except TypeError:
                sessions.append(AsyncSession(impersonate=impersonate or "chrome", timeout=timeout))
    except TypeError:
        sessions = [AsyncSession(impersonate=impersonate or "chrome", timeout=timeout)]

    async def _one(url: str, idx: int):
        async with gate:
            return await _fetch_one(
                sessions[idx % len(sessions)], url, timeout, retries, host_limiter, throttle, rotator,
                stealth_headers, google_search, impersonate, True, cookies, user_agent,
            )

    try:
        tasks = [asyncio.ensure_future(_one(u, i)) for i, u in enumerate(urls)]
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        for s in sessions:
            try:
                await s.close()
            except Exception:
                pass


async def fetch_all(
    urls: list[str],
    concurrency: int = 100,
    per_host: int = 10,
    timeout: int = 20,
    delay: float = 0.0,
    retries: int = 3,
    impersonate: str = "chrome",
    autothrottle: bool = True,
    proxies: list[str] | None = None,
    stealth_headers: bool = True,
    google_search: bool = False,
    cookies: dict | None = None,
    user_agent: str = "",
) -> list[FetchResult]:
    out: list[FetchResult] = []
    async for r in fetch_stream(
        urls,
        concurrency=concurrency,
        per_host=per_host,
        timeout=timeout,
        retries=retries,
        impersonate=impersonate,
        autothrottle=autothrottle,
        base_delay=delay,
        proxies=proxies,
        stealth_headers=stealth_headers,
        google_search=google_search,
        cookies=cookies,
        user_agent=user_agent,
    ):
        out.append(r)
    # preserve input order
    pos = {u: i for i, u in enumerate(urls)}
    out.sort(key=lambda r: pos.get(r.url, 10**9))
    return out
