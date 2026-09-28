# EliteScraper v0.4.0

Static speed + full JS engine + spiders + adaptive + RAG.

## Run

```bash
.venv/bin/elitescrape urls.txt --out out.jsonl --concurrency 150
.venv/bin/elitescrape --url https://example.com --out out.md --markdown
.venv/bin/elitescrape sitemaps.txt --out pages.jsonl --sitemap --max-pages 50
.venv/bin/elitescrape urls.txt --out out.jsonl --browser --browser-on-block --capture-xhr 'api|products'
.venv/bin/python benchmarks_battle.py
.venv/bin/python benchmarks_static.py --n 10
```

## Measured (local)

* Parser 5k nodes: ours 22.1ms vs Scrapling 43.9ms = 1.99x faster
* Static 10 pages: ours 7.1 vs Scrapling 8.3 pages/s, both 10/10 - tie/noise, they took this round
* JS: tab-pool Chromium works (httpbin + example 200, tab reuse ok)

## Features

* Static: `AsyncSession(max_clients=200, trust_env=False)`, queue pool, EMA AutoThrottle, Retry-After, impersonate rotate-on-retry only, bytes body, Lexbor bytes + strip_tags, orjson + uvloop
* JS (`browser.py`): per-proxy contexts, tab reuse, stealth JS (webdriver/chrome/plugins/languages/permissions), canvas-noise opt, resource block (images/fonts/trackers only), CF detect + iframe click solver (best-effort), XHR capture, CDP + executable_path, wait_selector/network_idle
* Parse: CSS + XPath + find_by_text/regex + find_similar + markdown + adaptive SQLite relocate
* Crawl: robots obey, sitemap expand, Shopify products.json, LinkExtractor, CrawlSpider/SitemapSpider/ShopifySpider, resume DB, proxy rotation, blocked detection
* Ops: Dockerfile, `benchmarks_battle.py`, `benchmarks_static.py`, 12 tests
