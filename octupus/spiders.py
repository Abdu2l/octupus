"""Spider templates: CrawlSpider / SitemapSpider / ShopifySpider (Scrapling parity)."""
import asyncio

from .fetcher import fetch_all
from .frontier import LinkExtractor, normalize_urls
from .parser import parse_html
from .robots import parse_sitemap, shopify_products_url


async def _fetch_urls(urls: list[str], **kw) -> list:
    return await fetch_all(urls, **kw)


class CrawlSpider:
    """Rule-based link following without hand-written boilerplate."""

    name = "crawl"
    start_urls: list[str] = []
    allow: list[str] | None = None
    deny: list[str] | None = None
    max_pages: int = 200
    max_depth: int = 2

    async def parse(self, page, response_html: str) -> dict | None:
        return {"url": page.url, "title": page.title}

    async def run(self, **fetch_kw) -> list[dict]:
        ex = LinkExtractor(allow=self.allow, deny=self.deny, same_domain_only=True)
        seen = set(normalize_urls(self.start_urls))
        depth = {u: 0 for u in seen}
        pending = list(seen)
        items: list[dict] = []
        while pending and len(items) + len(pending) // 2 < self.max_pages + len(pending):
            batch = []
            while pending and len(batch) < 100 and len(seen) < self.max_pages + 100:
                batch.append(pending.pop(0))
            if not batch:
                break
            results = await _fetch_urls(batch, **fetch_kw)
            for r in results:
                if not (r.status and 200 <= r.status < 400 and r.html):
                    continue
                page = parse_html(r.url, r.html, r.status, r.final_url)
                d = depth.get(r.url, 0)
                try:
                    item = await self.parse(page, r.html)
                except Exception:
                    item = None
                if item:
                    items.append(item)
                if d < self.max_depth:
                    for nxt in ex.extract(r.final_url or r.url, [l.href for l in page.links]):
                        if nxt not in seen and len(seen) < self.max_pages * 2:
                            seen.add(nxt)
                            depth[nxt] = d + 1
                            pending.append(nxt)
            if len(items) >= self.max_pages:
                break
        return items[: self.max_pages]


class SitemapSpider(CrawlSpider):
    """Seed crawl from sitemap.xml / robots.txt sitemaps."""

    sitemap_urls: list[str] = []

    async def seed_from_sitemaps(self, **fetch_kw) -> list[str]:
        seeds = normalize_urls(self.sitemap_urls or list(self.start_urls))
        out: list[str] = []
        results = await _fetch_urls(seeds, **fetch_kw)
        for r in results:
            if r.status and 200 <= r.status < 400 and r.html and ("<url" in r.html[:5000] or "<sitemap" in r.html[:5000]):
                out.extend(parse_sitemap(r.html))
        # if nested sitemap indexes, fetch one level
        nested = [u for u in out if u.endswith(".xml")][:10]
        if nested:
            results2 = await _fetch_urls(nested, **fetch_kw)
            for r in results2:
                if r.status and 200 <= r.status < 400 and r.html:
                    out.extend(parse_sitemap(r.html))
        pages = [u for u in out if not u.endswith(".xml")]
        return normalize_urls(pages)[: self.max_pages]

    async def run(self, **fetch_kw) -> list[dict]:
        seeds = await self.seed_from_sitemaps(**fetch_kw)
        self.start_urls = seeds or self.start_urls
        return await super().run(**fetch_kw)


class ShopifySpider:
    """Pull every product from any Shopify store via /products.json (one item per product)."""

    target_website: str = ""
    max_products: int = 2000

    async def run(self, **fetch_kw) -> list[dict]:
        import json as _json

        base = shopify_products_url(self.target_website)
        # paginate via ?page=N (classic Shopify API)
        items: list[dict] = []
        page_n = 1
        while len(items) < self.max_products and page_n <= 20:
            url = base + f"&page={page_n}" if "?" in base else base
            results = await _fetch_urls([url], **fetch_kw)
            if not results or not results[0].html:
                break
            try:
                data = _json.loads(results[0].html)
                prods = data.get("products", [])
            except Exception:
                break
            if not prods:
                break
            for p in prods:
                items.append({"title": p.get("title", ""), "handle": p.get("handle", ""), "url": p})
                if len(items) >= self.max_products:
                    break
            page_n += 1
        return items
