"""Hybrid solve-once-then-static: beat per-request browsers on Cloudflare sites.

Flow per domain/proxy:
1. Static fetch. If blocked -> browser solves challenge ONCE.
2. Export cf_clearance/__cf_bm/site cookies + UA from browser context.
3. Continue statically with pinned (cookies, UA, impersonate, proxy).
4. Refresh solve at ~60% TTL or on re-block.

Why faster than Scrapling-style browser-per-request: browser does 1 page
at ~3-8s; static does 40+ pages/s with the same clearance.
"""
import time
from urllib.parse import urlparse

CLEARANCE_NAMES = ("cf_clearance", "__cf_bm")


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


class ClearanceStore:
    """In-memory clearance per (domain, proxy) with TTL refresh."""

    def __init__(self, ttl_s: float = 1500.0):
        self.ttl_s = ttl_s
        self._jar: dict[tuple[str, str], dict] = {}
        self._ts: dict[tuple[str, str], float] = {}

    def key(self, url: str, proxy: str | None) -> tuple[str, str]:
        return (domain_of(url), proxy or "")

    def get(self, url: str, proxy: str | None) -> dict | None:
        k = self.key(url, proxy)
        jar = self._jar.get(k)
        if not jar:
            return None
        age = time.monotonic() - self._ts.get(k, 0)
        if age > self.ttl_s * 0.6:  # refresh at 60% TTL
            return None
        return jar

    def put(self, url: str, proxy: str | None, jar: dict):
        k = self.key(url, proxy)
        self._jar[k] = dict(jar)
        self._ts[k] = time.monotonic()

    def drop(self, url: str, proxy: str | None):
        k = self.key(url, proxy)
        self._jar.pop(k, None)
        self._ts.pop(k, None)


async def solve_domain_once(browser_pool, url: str, proxy: str | None = None) -> tuple[dict, str]:
    """Drive one browser solve, return (cookies jar, user_agent).

    Keeps cf_clearance + __cf_bm + site cookies for the target domain.
    """
    st, html, furl, _cap = await browser_pool.fetch(url, network_idle=False, proxy=proxy)
    jar: dict = {}
    try:
        # pull cookies from every context matching the domain (proxy-pinned contexts)
        for ctx in list(getattr(browser_pool, "_ctxs", {}).values()):
            try:
                for c in await ctx.cookies():
                    try:
                        if domain_of(url) in (c.get("domain") or "").lower():
                            jar[c["name"]] = c["value"]
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    ua = ""
    try:
        # UA used by the solving context (pin it for static reuse)
        ctxs = getattr(browser_pool, "_ctxs", {})
        key = proxy or "__default__"
        ctx = ctxs.get(key)
        if ctx is not None:
            page = await ctx.new_page()
            try:
                ua = await page.evaluate("navigator.userAgent")
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
    except Exception:
        pass
    return jar, ua or ""
