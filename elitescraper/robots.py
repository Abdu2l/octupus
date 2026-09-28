"""robots.txt obey + sitemap/Shopify helpers + blocked-page detection."""
import re
import time
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

_block_markers = [
    "just a moment", "verify you are human", "cf-challenge", "turnstile",
    "captcha", "access denied", "request blocked", "datadome", "perimeterx",
    "please verify", "are you a robot",
]


def is_blocked(status: int, html: str, url: str = "") -> tuple[bool, str]:
    low = (html or "")[:8000]
    # locale-independent CF typing first
    try:
        from .browser import detect_cf_type

        ctype = detect_cf_type(low)
        if ctype and ctype != "embedded":
            return True, f"blocked:cloudflare-{ctype}"
    except Exception:
        pass
    if status in (401, 403, 429):
        lowl = low.lower()
        for m in _block_markers:
            if m in lowl:
                return True, f"blocked:{m}"
        if status in (401, 403):
            return True, f"blocked:http-{status}"
        return False, ""
    if status in (500, 502, 503, 504):
        return False, ""  # retryable, not blocked
    low = (html or "")[:8000].lower()
    title = ""
    try:
        m = re.search(r"<title>(.*?)</title>", low, re.S)
        title = (m.group(1) if m else "").strip()
    except Exception:
        pass
    for m in _block_markers:
        if m in title or (m in low and len(low) < 20000 and "cf-" in low):
            return True, f"blocked:{m}"
    return False, ""


class RobotsCache:
    """Per-domain robots.txt cache with Crawl-delay support."""

    def __init__(self, enabled: bool = False, user_agent: str = "EliteScraper"):
        self.enabled = enabled
        self.ua = user_agent
        self._parsers: dict[str, robotparser.RobotFileParser] = {}
        self._delays: dict[str, float] = {}
        self._ts: dict[str, float] = {}

    def _load(self, url: str):
        from curl_cffi import requests as creq

        host = urlparse(url).netloc.lower()
        if host in self._parsers and time.time() - self._ts.get(host, 0) < 3600:
            return
        rp = robotparser.RobotFileParser()
        try:
            robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
            r = creq.get(robots_url, timeout=10, impersonate="chrome")
            if r.status_code == 200 and r.text:
                rp.parse(r.text.splitlines())
                try:
                    d = rp.crawl_delay(self.ua)
                    if d:
                        self._delays[host] = float(d)
                except Exception:
                    pass
            else:
                rp.parse([])
        except Exception:
            rp.parse([])
        self._parsers[host] = rp
        self._ts[host] = time.time()

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        try:
            self._load(url)
            return self._parsers[urlparse(url).netloc.lower()].can_fetch(self.ua, url)
        except Exception:
            return True

    def crawl_delay(self, url: str) -> float:
        return self._delays.get(urlparse(url).netloc.lower(), 0.0)

    def sitemap_urls(self, url: str) -> list[str]:
        if not self.enabled:
            return []
        try:
            self._load(url)
            rp = self._parsers.get(urlparse(url).netloc.lower())
            if rp and hasattr(rp, "site_maps"):
                return list(rp.site_maps() or [])
        except Exception:
            pass
        return []


def parse_sitemap(xml_text: str, base: str = "") -> list[str]:
    out: list[str] = []
    try:
        root = ET.fromstring((xml_text or "")[:5_000_000])
    except Exception:
        return []
    for el in root.iter():
        tag = el.tag.lower()
        if tag.endswith("loc") and el.text and el.text.strip().startswith("http"):
            u = el.text.strip()
            # nested sitemap index -> keep too, caller can recurse (cap outside)
            out.append(u)
            if len(out) >= 5000:
                break
    return out


def sitemap_index_filter(urls: list[str]) -> tuple[list[str], list[str]]:
    """Split sitemap locs into (sub_sitemaps, pages)."""
    subs = [u for u in urls if u.endswith(".xml") or "sitemap" in u]
    pages = [u for u in urls if u not in subs]
    return subs, pages


def shopify_products_url(shop: str) -> str:
    shop = shop.strip().rstrip("/")
    if "://" not in shop:
        shop = "https://" + shop
    return shop + "/products.json?limit=250"
