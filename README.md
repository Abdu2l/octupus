# Octupus v0.5.1

Blazing-fast CLI scraper: async session pool, hybrid Cloudflare, tentacle crawls, RAG markdown.

Why not just use the big frameworks: solve Cloudflare ONCE per domain in the
browser, export `cf_clearance` + UA, then continue at static speed instead of
running every page through a browser. `__NEXT_DATA__`/JSON-LD/API-hint
detection skips the browser entirely, and `--dev-cache` replays at 1000+ pages/s.

## Install

```bash
git clone https://github.com/Abdu2l/octupus.git
cd octupus
python3 -m venv .venv && .venv/bin/pip install -e .
# optional: browser engine + version-matched headers
.venv/bin/pip install -e '.[all]' && .venv/bin/playwright install chromium
```

## Run

```bash
.venv/bin/octupus urls.txt --out out.jsonl --concurrency 150
.venv/bin/octupus urls.txt --out out.jsonl --hybrid --browser --browser-on-block
.venv/bin/octupus --url https://example.com --out out.md --markdown
.venv/bin/octupus sitemaps.txt --out pages.jsonl --sitemap --max-pages 50
.venv/bin/python benchmarks_full.py
```

## Measured (local, see BENCHMARKS.md for method + charts)

```
CSS text x5000:   octupus 19.2ms | scrapling 34.6ms | parsel 32.7ms | bs4 211.8ms
XPath x5000:      octupus 20.2ms | lxml 21.3ms | scrapling 36.2ms | parsel 40.5ms
Full extract:     lxml 0.63ms | parsel 1.10ms | octupus 4.0ms | bs4 8.05ms
Text search:      octupus 8.1ms | scrapling 22.1ms (2.7x)
JSON 200 rows:    orjson 4.5ms vs stdlib 46.8ms (10.4x, used in to_jsonl)
Fetch localhost:  tie (both curl-based, toy-server noise dominates)
```

Octupus wins CSS-text, loses full-extract to raw lxml/parsel (we resolve
absolute URLs + validate schema - costs ~3ms, still 250 pages/s/core).
Fetch is a tie: both curl-based. Honest details + method in BENCHMARKS.md.
