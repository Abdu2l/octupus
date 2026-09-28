"""Browser engine: Playwright tab pool with stealth + Cloudflare handling + XHR capture.

Static path stays dependency-free. Only imported when --browser is used.
Install: pip install 'octupus[browser]' && playwright install chromium
"""
import asyncio
import random

# Never block these on first load - Turnstile needs JS/XHR/websocket.
BLOCKED_TYPES = {"image", "media", "font"}
BLOCK_SUBSTR = (
    "google-analytics", "googletagmanager", "doubleclick", "facebook.net",
    "hotjar", "quantserve", "criteo", "outbrain",
)

STEALTH_JS = """() => {
try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch(e) {}
try { delete Object.getPrototypeOf(navigator).webdriver; } catch(e) {}
try { window.chrome = {app:{isInstalled:false}, csi:()=>{}, loadTimes:()=>{}, runtime:{}}; } catch(e) {}
try { Object.defineProperty(navigator, 'plugins', {get: () => [{},{},{}]}); } catch(e) {}
try { Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']}); } catch(e) {}
try {
  const q = window.Notification ? Notification.permission : 'denied';
  const orig = window.navigator.permissions.query;
  window.navigator.permissions.query = (p) => p && p.name === 'notifications'
    ? Promise.resolve({state: q}) : orig(p);
} catch(e) {}
}"""

CANVAS_NOISE_JS = """() => {
try {
  const g = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function(...a) {
    const d = g.apply(this, a);
    d.data[0] += Math.floor(Math.random()*2-1);
    return d;
  };
} catch(e) {}
}"""

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CF_MARKERS = ("just a moment", "checking your browser", "verifying you are human",
              "verify you are human", "challenges.cloudflare.com", "cf-turnstile", "cf-challenge")
CF_TYPES = ("cType: 'non-interactive'", "cType: 'managed'", "cType: 'interactive'")
CF_SCRIPT = "challenges.cloudflare.com/turnstile/v"
CF_FRAME_RE = r"^https?://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*"
CF_MAX_ATTEMPTS = 3


def detect_cf_type(html: str) -> str | None:
    """Locale-independent challenge typing: cType JS string + turnstile script.

    Own implementation of the same public idea (page-embedded cType markers).
    Falls back to legacy title/body markers for odd pages.
    """
    low = (html or "").lower()
    for marker, ctype in ((CF_TYPES[0], "non-interactive"), (CF_TYPES[1], "managed"), (CF_TYPES[2], "interactive")):
        if marker.lower() in low:
            return ctype
    if CF_SCRIPT in (html or ""):
        return "embedded"
    if any(m in low for m in CF_MARKERS):
        return "managed"
    return None


def cf_cleared(html: str, ctype: str) -> bool:
    return ctype == "embedded" or detect_cf_type(html) is None


async def _page_html(page) -> str:
    try:
        return await page.content()
    except Exception:
        return ""


async def _is_challenged(page) -> bool:
    return detect_cf_type(await _page_html(page)) is not None


async def _solve_cf_click(page, timeout_s: int = 15, _attempt: int = 0):
    """Own solver: wait-branch for non-interactive, iframe-first click otherwise.

    Capped recursion (3), spinner-settle waits, fallback selectors, human-like clicks.
    """
    import re as _re

    html = await _page_html(page)
    ctype = detect_cf_type(html)
    if not ctype:
        return True
    if _attempt >= CF_MAX_ATTEMPTS:
        return False
    if ctype == "non-interactive":
        for _ in range(12):
            await page.wait_for_timeout(1000)
            try:
                await page.wait_for_load_state(timeout=5000)
            except Exception:
                pass
            if cf_cleared(await _page_html(page), ctype):
                return True
        return await _solve_cf_click(page, timeout_s, _attempt + 1)

    # managed / interactive / embedded / legacy-managed
    try:
        for _ in range(20):
            try:
                if page.frame(url=_re.compile(CF_FRAME_RE)) is not None or cf_cleared(await _page_html(page), ctype):
                    break
            except Exception:
                break
            await page.wait_for_timeout(500)
    except Exception:
        pass
    box = None
    try:
        fr = page.frame(url=_re.compile(CF_FRAME_RE))
        if fr is not None:
            try:
                el = await fr.frame_element()
                for _ in range(10):
                    try:
                        if el.is_visible():
                            break
                    except Exception:
                        break
                    await page.wait_for_timeout(500)
                box = await el.bounding_box()
            except Exception:
                box = None
    except Exception:
        box = None
    if not box:
        if cf_cleared(await _page_html(page), ctype):
            return True
        sel = "#cf_turnstile div, #cf-turnstile div, .turnstile>div>div" if ctype == "embedded" else ".main-content p+div>div>div"
        try:
            box = await page.locator(sel).last.bounding_box()
        except Exception:
            box = None
        if not box:
            await page.wait_for_timeout(1000)
            return await _solve_cf_click(page, timeout_s, _attempt + 1)
    try:
        x, y = box["x"] + random.randint(26, 28), box["y"] + random.randint(25, 27)
        await page.mouse.click(x, y, delay=random.randint(100, 200), button="left")
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    if ctype != "embedded":
        for _ in range(100):
            if cf_cleared(await _page_html(page), ctype):
                break
            await page.wait_for_timeout(100)
    try:
        await page.wait_for_load_state(timeout=5000)
    except Exception:
        pass
    if cf_cleared(await _page_html(page), ctype):
        return True
    return await _solve_cf_click(page, timeout_s, _attempt + 1)


class BrowserPool:
    """1 proxy == 1 context == N reused tabs. Tab reuse keeps cf_clearance cookies."""

    def __init__(
        self,
        headless: bool = True,
        max_pages: int = 4,
        block_resources: bool = True,
        timeout_ms: int = 30000,
        solve_cloudflare: bool = True,
        hide_canvas: bool = False,
        cdp_url: str | None = None,
        executable_path: str | None = None,
        capture_xhr: str | None = None,
        blocked_domains: list[str] | None = None,
        block_ads: bool = False,
        dns_over_https: bool = False,
        storage_state_dir: str | None = None,
    ):
        if solve_cloudflare and timeout_ms < 60000:
            import sys as _sys

            print("warn: --browser with CF solver wants >=60s timeout, bumping to 60000", file=_sys.stderr)
            timeout_ms = 60000
        self.headless = headless
        self.max_pages = max(1, max_pages)
        self.block_resources = block_resources
        self.timeout_ms = timeout_ms
        self.solve_cloudflare = solve_cloudflare
        self.hide_canvas = hide_canvas
        self.cdp_url = cdp_url
        self.executable_path = executable_path
        self.capture_xhr = capture_xhr
        self.blocked_domains = {d.lower() for d in (blocked_domains or [])}
        self.block_ads = block_ads
        self.dns_over_https = dns_over_https
        self.storage_state_dir = storage_state_dir
        self._pw = None
        self._browser = None
        self._ctxs: dict[str, object] = {}
        self._semas: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def start(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError("playwright not installed: pip install 'octupus[browser]'") from e
        self._pw = async_playwright()
        pw = await self._pw.start()
        self._pw_instance = pw
        if self.cdp_url:
            self._browser = await pw.chromium.connect_over_cdp(self.cdp_url)
        else:
            args = ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check"]
            if self.hide_canvas:
                args.append("--fingerprinting-canvas-image-data-noise")
            if self.dns_over_https:
                args.append("--dns-over-https-templates=https://cloudflare-dns.com/dns-query")
            kwargs: dict = {
                "headless": self.headless,
                "args": args,
                "ignore_default_args": ["--enable-automation"],
            }
            if self.executable_path:
                kwargs["executable_path"] = self.executable_path
            self._browser = await pw.chromium.launch(**kwargs)

    async def _ctx_for(self, proxy: str | None):
        key = proxy or "__default__"
        async with self._lock:
            ctx = self._ctxs.get(key)
            sema = self._semas.get(key)
            if ctx is not None and sema is not None:
                return ctx, sema
            assert self._browser is not None, "BrowserPool not started"
            ctx_kwargs: dict = {
                "user_agent": random.choice(UAS),
                "viewport": {"width": 1920, "height": 1080},
                "screen": {"width": 1920, "height": 1080},
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "is_mobile": False,
                "has_touch": False,
                "color_scheme": "light",
            }
            if proxy:
                ctx_kwargs["proxy"] = {"server": proxy}
            if self.storage_state_dir:
                import hashlib as _hl
                import os as _os

                _os.makedirs(self.storage_state_dir, exist_ok=True)
                key = (proxy or "default")
                ctx_kwargs["storage_state"] = None
                # load per-proxy clearance if present
                sp = f"{self.storage_state_dir}/{_hl.sha256(key.encode()).hexdigest()[:16]}.json"
                import os as _os2

                if _os2.path.exists(sp):
                    ctx_kwargs["storage_state"] = sp
                self._state_path = sp
            else:
                self._state_path = None
            ctx = await self._browser.new_context(**ctx_kwargs)
            await ctx.add_init_script(STEALTH_JS)
            if self.hide_canvas:
                await ctx.add_init_script(CANVAS_NOISE_JS)
            if self.block_resources or self.blocked_domains or self.block_ads:
                blocked = set(self.blocked_domains)
                async def _route(route):
                    try:
                        req = route.request
                        try:
                            from urllib.parse import urlparse as _up

                            host = _up(req.url).netloc.lower()
                        except Exception:
                            host = ""
                        if host and any(host == d or host.endswith("." + d) for d in blocked):
                            await route.abort()
                            return
                        if self.block_resources and (
                            req.resource_type in BLOCKED_TYPES or any(s in req.url for s in BLOCK_SUBSTR)
                        ):
                            await route.abort()
                        else:
                            await route.continue_()
                    except Exception:
                        try:
                            await route.continue_()
                        except Exception:
                            pass
                await ctx.route("**/*", _route)
            sema = asyncio.Semaphore(self.max_pages)
            self._ctxs[key] = ctx
            self._semas[key] = sema
            return ctx, sema

    async def fetch(
        self,
        url: str,
        wait_selector: str | None = None,
        network_idle: bool = False,
        proxy: str | None = None,
        page_setup=None,
        page_action=None,
    ) -> tuple[int, str, str, list]:
        ctx, sema = await self._ctx_for(proxy)
        captured: list = []
        async with sema:
            page = await ctx.new_page()
            if self.capture_xhr:
                try:
                    import re as _re

                    pat = _re.compile(self.capture_xhr)

                    async def _on_resp(resp):
                        try:
                            if resp.request.resource_type in ("xhr", "fetch") and pat.search(resp.url) and resp.status == 200:
                                try:
                                    captured.append((resp.url, await resp.text()))
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    page.on("response", _on_resp)
                except Exception:
                    pass
            try:
                if page_setup:
                    try:
                        page_setup(page)
                    except Exception:
                        pass
                resp = await page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                status = resp.status if resp else 0
                if self.solve_cloudflare:
                    try:
                        if await _is_challenged(page):
                            await _solve_cf_click(page, timeout_s=15)
                    except Exception:
                        pass
                if page_action:
                    try:
                        res = page_action(page)
                        import asyncio as _aio

                        if _aio.iscoroutine(res):
                            await res
                    except Exception:
                        pass
                if network_idle:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                if wait_selector:
                    try:
                        await page.locator(wait_selector).first.wait_for(state="attached", timeout=8000)
                    except Exception:
                        pass
                html = await page.content()
                # persist per-proxy clearance for next run
                try:
                    if getattr(self, "_state_path", None):
                        await ctx.storage_state(path=self._state_path)
                except Exception:
                    pass
                return status or 200, html, page.url or url, captured
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

    async def close(self):
        for ctx in list(self._ctxs.values()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._ctxs.clear()
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
