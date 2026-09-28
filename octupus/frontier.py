"""URL frontier: dedupe + per-host throttling + autothrottle + proxies + link extraction."""
import asyncio
import re
import sqlite3
import time
from urllib.parse import urljoin, urlparse


def normalize_urls(raw_urls: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for raw in raw_urls:
        u = (raw or "").strip()
        if not u:
            continue
        if "://" not in u:
            u = "https://" + u
        if not (u.startswith("http://") or u.startswith("https://")):
            continue
        if u not in seen:
            seen[u] = None
    return list(seen.keys())


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


class PerHostLimiter:
    """Lazily-created semaphore per host to avoid hammering one domain."""

    def __init__(self, per_host: int):
        self.per_host = max(1, per_host)
        self._semas: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, url: str):
        host = host_of(url) or "__default__"
        async with self._lock:
            sema = self._semas.get(host)
            if sema is None:
                sema = asyncio.Semaphore(self.per_host)
                self._semas[host] = sema
        await sema.acquire()
        return sema

    def release(self, url: str):
        host = host_of(url) or "__default__"
        sema = self._semas.get(host)
        if sema is not None:
            try:
                sema.release()
            except ValueError:
                pass


class AutoThrottle:
    """Adaptive per-host delay: latency EMA + backoff on 429/5xx + Retry-After friendly.

    Rise fast, fall slow. Starts at base (0), grows only when host is
    slow or rate-limiting, shrinks when healthy.
    """

    def __init__(self, enabled: bool = True, base: float = 0.0, max_delay: float = 10.0,
                 target_concurrency: float = 2.0, start_delay: float = 0.0):
        self.enabled = enabled
        self.base = max(0.0, base)
        self.max_delay = max_delay
        self.target_concurrency = max(0.5, target_concurrency)
        self._delays: dict[str, float] = {}
        self._emas: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._lock = asyncio.Lock()
        if start_delay:
            self.base = max(self.base, start_delay)

    async def wait(self, url: str):
        if not self.enabled:
            if self.base > 0:
                await asyncio.sleep(0)
            return
        host = host_of(url) or "__default__"
        async with self._lock:
            delay = self._delays.get(host, self.base)
            prev = self._last.get(host, 0.0)
        now = time.monotonic()
        wait = delay - (now - prev)
        if wait > 0:
            await asyncio.sleep(wait)

    async def mark(self, url: str, latency: float, status: int):
        host = host_of(url) or "__default__"
        async with self._lock:
            cur = self._delays.get(host, self.base)
            ema = self._emas.get(host, latency)
            # EMA update on success only
            if 200 <= status < 400:
                ema = 0.3 * latency + 0.7 * ema
                self._emas[host] = ema
                target = ema / self.target_concurrency
                new = (cur + target) / 2.0
                new = max(target, new)  # rise fast
                if latency < 0.5 and cur > self.base:
                    new = max(self.base, cur * 0.9)  # fall slow
                cur = min(self.max_delay, max(self.base if self.base else 0.0, new))
            elif status == 429 or status in (500, 502, 503, 504):
                cur = min(self.max_delay, max(1.0, cur * 4.0 if cur > 0 else 1.0))
            elif latency > 2.0:
                cur = min(self.max_delay, cur + 0.25)
            self._delays[host] = cur
            self._last[host] = time.monotonic()

    def delay_for(self, url: str) -> float:
        return self._delays.get(host_of(url) or "__default__", self.base)


class ProxyRotator:
    """Cyclic proxy rotation. Proxies as http://user:pass@host:port strings."""

    def __init__(self, proxies: list[str] | None = None):
        self.proxies = [p.strip() for p in (proxies or []) if p and p.strip()]
        self._idx = 0
        self._lock = asyncio.Lock()

    def __bool__(self):
        return bool(self.proxies)

    async def next(self) -> str | None:
        if not self.proxies:
            return None
        async with self._lock:
            p = self.proxies[self._idx % len(self.proxies)]
            self._idx += 1
            return p


class LinkExtractor:
    """Scrapling-style allow/deny link extraction for --follow-links crawl."""

    def __init__(
        self,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        deny_domains: list[str] | None = None,
        same_domain_only: bool = True,
    ):
        self.allow = [re.compile(p) for p in (allow or [])]
        self.deny = [re.compile(p) for p in (deny or [])]
        self.deny_domains = {d.lower() for d in (deny_domains or [])}
        self.same_domain_only = same_domain_only

    def extract(self, base_url: str, hrefs: list[str], limit: int = 200) -> list[str]:
        base_host = host_of(base_url)
        out: list[str] = []
        for raw in hrefs:
            if len(out) >= limit:
                break
            try:
                abs_url = urljoin(base_url, (raw or "").strip())
            except Exception:
                continue
            if not abs_url.startswith(("http://", "https://")):
                continue
            h = host_of(abs_url)
            if h in self.deny_domains:
                continue
            if self.same_domain_only and h != base_host:
                continue
            if self.allow and not any(p.search(abs_url) for p in self.allow):
                continue
            if self.deny and any(p.search(abs_url) for p in self.deny):
                continue
            out.append(abs_url.split("#")[0])
        # dedupe preserve order
        return list(dict.fromkeys(out))


class SeenDB:
    """SQLite seen-URL store for --resume (pause/resume crawls)."""

    def __init__(self, path: str):
        self.path = path
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY)")
        con.commit()
        con.close()

    def is_seen(self, url: str) -> bool:
        con = sqlite3.connect(self.path)
        try:
            row = con.execute("SELECT 1 FROM seen WHERE url=?", (url,)).fetchone()
            return row is not None
        finally:
            con.close()

    def filter_new(self, urls: list[str]) -> list[str]:
        con = sqlite3.connect(self.path)
        try:
            out = []
            for u in urls:
                if not con.execute("SELECT 1 FROM seen WHERE url=?", (u,)).fetchone():
                    out.append(u)
            return out
        finally:
            con.close()

    def mark_many(self, urls: list[str]):
        if not urls:
            return
        con = sqlite3.connect(self.path)
        try:
            con.executemany("INSERT OR IGNORE INTO seen (url) VALUES (?)", [(u,) for u in urls])
            con.commit()
        finally:
            con.close()
