# Octupus benchmarks (real, same machine)

Engine: `python 3.14.7` on `Linux x86_64`, 2026-09-28.
Method: median of 15 runs after 3 warmups. Same HTML inputs for every library.

## Test 1 - CSS text extract, 5000 nodes

| library | median ms | vs octupus |
|---|---|---|
| selectolax naive | 25.30 | 0.97x |
| octupus extract | 26.02 | 1.00x |
| parsel (scrapy) | 40.71 | 1.56x |
| scrapling | 48.75 | 1.87x |
| bs4+lxml | 290.09 | 11.15x |

```
selectolax naive             ### 25.3ms
octupus extract              ### 26.0ms
parsel (scrapy)              ##### 40.7ms
scrapling                    ###### 48.8ms
bs4+lxml                     ######################################## 290.1ms
```

Lower is better. Octupus uses selectolax-Lexbor `::text` fast path.

## Test 2 - Full page extract (title + meta + h1 + 200 links)

| library | median ms | vs octupus |
|---|---|---|
| lxml full | 1.16 | 0.26x |
| parsel full | 2.01 | 0.44x |
| octupus parse_html | 4.53 | 1.00x |
| bs4 full | 11.50 | 2.54x |

```
lxml full                    #### 1.2ms
parsel full                  ####### 2.0ms
octupus parse_html           ############### 4.5ms
bs4 full                     ######################################## 11.5ms
```

Octupus `parse_html` does all fields in one C parse.

## Test 3 - Static fetch, localhost (noisy toy server, read carefully)

Same 30 URLs, concurrency 15, `python -m http.server` (single box, no keep-alive tuning):

```
octupus:   0.15s - 1.18s across runs (pool of 8 sessions, fast 0.2s blip-retry)
scrapling: 0.06s - 1.09s across runs (fresh session per request)
```

Verdict: TIE - whoever hits the server's ~1s backlog stall loses that round,
it alternates run to run. Both curl-based; no engine gap proven here.
Internet runs: both ~7-8 pages/s, 10/10 ok. Improvement shipped from this
digging: connection blips (reset/refused) now retry in 0.2s, real 429/5xx in 1s.

## Test 4 - XPath extract, 5000 nodes

| library | median ms | vs octupus |
|---|---|---|
| octupus xpath | 20.21 | 1.00x |
| lxml xpath | 21.29 | 1.05x |
| scrapling xpath | 36.22 | 1.79x |
| parsel xpath | 40.48 | 2.00x |

```
octupus xpath                ################### 20.2ms
lxml xpath                   ##################### 21.3ms
scrapling xpath              ################################### 36.2ms
parsel xpath                 ######################################## 40.5ms
```

## Test 5 - Markdown export (article + hidden prompt-injection div)

| library | median ms | vs octupus |
|---|---|---|
| octupus markdown | 0.43 | 1.00x |

```
octupus markdown             ######################################## 0.4ms
```

Octupus strips hidden/aria/template/comments/zero-width during export.

## Test 6 - JSONL serialize 200 page dicts

| library | median ms | vs octupus |
|---|---|---|
| orjson dumps | 4.48 | 1.00x |
| stdlib json | 46.78 | 10.43x |

```
orjson dumps                 ### 4.5ms
stdlib json                  ######################################## 46.8ms
```

orjson writes bytes directly - used by octupus `to_jsonl`.

## Test 7 - Find element by text (2000 rows, target last)

| library | median ms | vs octupus |
|---|---|---|
| octupus find_by_text | 8.06 | 1.00x |
| scrapling find_by_text | 22.08 | 2.74x |

```
octupus find_by_text         ############## 8.1ms
scrapling find_by_text       ######################################## 22.1ms
```

## Reproduce

```bash
.venv/bin/python benchmarks_full.py
.venv/bin/python benchmarks_battle.py
.venv/bin/python benchmarks_static.py --n 10
```
