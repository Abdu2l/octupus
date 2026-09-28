# EliteScraper v0.5.1

Static speed + hybrid Cloudflare + full JS engine + spiders + RAG.

Niche vs big teams: solve Cloudflare ONCE per domain in browser, export
`cf_clearance` + UA, continue at 40+ pages/s static. They run every page
through the browser (~1-2 pages/s). Plus `__NEXT_DATA__`/JSON-LD/API-hint
detection skips the browser entirely, and `--dev-cache` replays at 1000+ pages/s.

## Run

```bash
.venv/bin/elitescrape urls.txt --out out.jsonl --concurrency 150
.venv/bin/elitescrape urls.txt --out out.jsonl --hybrid --browser --browser-on-block
.venv/bin/elitescrape --url https://example.com --out out.md --markdown
.venv/bin/elitescrape sitemaps.txt --out pages.jsonl --sitemap --max-pages 50
.venv/bin/python benchmarks_battle.py
.venv/bin/python benchmarks_static.py --n 10
```

## Push to GitHub

```bash
cd /home/abdul/elitescraper
gh repo create elitescraper --public --source=. --push
# without gh: create empty repo on github.com, then:
# git remote add origin git@github.com:YOU/elitescraper.git
# git push -u origin master
```

## Measured (local)

* Parser 5k nodes: ours ~18-22ms vs Scrapling ~32-44ms (~1.9x)
* Localhost 50 pages: ~42-45 pages/s tie, both 50/50
* Dev-cache replay: 1291 pages/s
