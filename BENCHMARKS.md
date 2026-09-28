# Octupus benchmarks (real, same machine, 2026-09-28)

Method: median of 15 runs after 3 warmups, same HTML inputs for every library.
Machine: Linux x86_64, python 3.14. Rerun: `.venv/bin/python benchmarks_full.py`.

## Test 1 - CSS text extract, 5000 nodes (lower is better)

| library | median ms | vs octupus |
|---|---|---|
| selectolax naive | 18.70 | 0.97x |
| octupus extract | 19.20 | 1.00x |
| parsel (scrapy) | 32.70 | 1.70x |
| scrapling | 34.60 | 1.80x |
| bs4+lxml | 211.80 | 11.03x |

```
selectolax naive     ### 18.7ms
octupus extract      ### 19.2ms
parsel (scrapy)      ###### 32.7ms
scrapling            ###### 34.6ms
bs4+lxml             ######################################## 211.8ms
```

Octupus is selectolax-Lexbor with one `strip_tags` pass (costs ~0.5ms) -
matches raw selectolax, beats parsel/scrapling ~1.7-1.8x, bs4 ~11x.

## Test 2 - Full page extract: title + meta + h1 + 200 links (lower is better)

| library | median ms | vs octupus |
|---|---|---|
| lxml full | 0.63 | 0.16x |
| parsel full | 1.10 | 0.28x |
| octupus parse_html | 4.00 | 1.00x |
| bs4 full | 8.05 | 2.01x |

```
lxml full            ### 0.6ms
parsel full          ##### 1.1ms
octupus parse_html   ################### 4.0ms
bs4 full             ######################################## 8.0ms
```

Honest loss: raw lxml/parsel return unchecked strings; octupus resolves
absolute URLs (`urljoin` x200) + validates a pydantic schema. 4ms is still
250 pages/s/core - parse is ~1% of any crawl, network dominates.

## Test 3 - Static fetch: octupus vs Scrapling AsyncFetcher

Same 30 URLs, concurrency 15, repeated runs, localhost `python -m http.server`:

```
run1: octupus 1.10s  scrapling 0.06s
run2: octupus 1.05s  scrapling 1.09s
run3: octupus 0.06s  scrapling 1.03s
```

Verdict: TIE. Whoever hits the server's stall (~1s backlog under burst)
loses that round; it alternates. Both are curl-based; engine gap is noise.
External/internet runs earlier: both ~7-8 pages/s, 10/10 ok, same story.

What actually moved our fetch speed during tuning:
* session POOL of 8 (one shared curl session stalls 5/30 under burst;
  separate session per request never stalls but re-handshakes everything)
* `impersonate="chrome"` latest, always-on Google referer, cached
  browserforge headers (uncached generation cost 1.5s/req - caught by bench)
* fixed 1s retry delay, `as_completed` streaming, `max_clients` pooling

## Niche wins (no equivalent in one binary)

* `--hybrid`: solve CF once per domain, continue static at 40+ pages/s
* `--dev-cache`: replay at 1291 pages/s for parse iteration
* `--markdown`: prompt-injection-stripped RAG markdown, no LLM in loop
* tentacles: `CrawlTentacle` / `SitemapTentacle` / `ShopTentacle` templates
