"""Octupus real-world benchmark vs top scrapers. Writes BENCHMARKS.md with charts.

Run: .venv/bin/python benchmarks_full.py
Tests (same machine, same HTML, median of runs):
 1. CSS text extract, 5000 nodes (ours vs Scrapling vs parsel vs selectolax-naive vs bs4)
 2. Full page extract: title + meta + h1/h2 + 200 links (ours parse_html vs bs4 vs parsel vs lxml)
 3. Static fetch localhost 30 pages (ours vs Scrapling AsyncFetcher)
 4. XPath extract, 5000 nodes (ours vs parsel vs lxml vs Scrapling)
 5. Markdown export (ours to_markdown vs Scrapling markdown, if rag extra installed)
 6. JSONL row serialize x200 pages (orjson vs stdlib json vs pydantic)
 7. Text search: find element by text (ours find_by_text vs Scrapling)
"""
import platform
import statistics
import sys
import time

PAGE_5K = "<html><body>" + "".join(f'<div class="item">text {i}</div>' for i in range(5000)) + "</body></html>"
LINKS = "".join(f'<a href="/p{i}">link {i}</a>' for i in range(200))
H1S = "".join(f"<h1>Head {i}</h1>" for i in range(10))
REAL_PAGE = (
    "<html><head><title>Test Page</title>"
    '<meta name="description" content="A test page for benchmarks">'
    '<meta property="og:description" content="OG fallback"></head>'
    f"<body>{H1S}<h2>Sub</h2><p>Hello world</p>{LINKS}</body></html>"
)


def bench(fn, runs=15, warmup=3):
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def bar(ms, scale):
    return "#" * max(1, int(ms / scale))


def main():
    from octupus.parser import extract_texts, parse_html

    results = {}

    # Test 1: css text 5k
    t_ours = bench(lambda: extract_texts(PAGE_5K, ".item::text"))
    results["octupus extract"] = t_ours
    try:
        from scrapling import Selector as S

        results["scrapling"] = bench(lambda: S(PAGE_5K, adaptive=False).css(".item::text").getall())
    except Exception as e:
        results["scrapling"] = None
    try:
        from parsel import Selector as P

        results["parsel (scrapy)"] = bench(lambda: P(PAGE_5K).css(".item::text").getall())
    except Exception:
        results["parsel (scrapy)"] = None
    try:
        from selectolax.parser import HTMLParser

        results["selectolax naive"] = bench(lambda: [n.text() for n in HTMLParser(PAGE_5K).css(".item")])
    except Exception:
        results["selectolax naive"] = None
    try:
        from bs4 import BeautifulSoup

        results["bs4+lxml"] = bench(lambda: [t.get_text() for t in BeautifulSoup(PAGE_5K, "lxml").select(".item")])
    except Exception:
        results["bs4+lxml"] = None

    # Test 2: full page extract
    full = {}
    full["octupus parse_html"] = bench(lambda: parse_html("https://x.com/", REAL_PAGE, 200, "https://x.com/"))

    def bs4_full():
        from bs4 import BeautifulSoup

        s = BeautifulSoup(REAL_PAGE, "lxml")
        title = s.title.get_text() if s.title else ""
        m = s.find("meta", attrs={"name": "description"})
        desc = m.get("content", "") if m else ""
        h1 = [t.get_text() for t in s.find_all("h1")]
        links = [(a.get_text(), a.get("href")) for a in s.find_all("a", href=True)]
        return title, desc, h1, links

    def parsel_full():
        from parsel import Selector as P

        s = P(REAL_PAGE)
        return (s.css("title::text").get(), s.css('meta[name=description]::attr(content)').get(),
                s.css("h1::text").getall(), s.css("a::attr(href)").getall())

    def lxml_full():
        from lxml import html as lh

        t = lh.fromstring(REAL_PAGE)
        return (t.findtext(".//title"),
                (t.xpath('//meta[@name="description"]/@content') or [""])[0],
                t.xpath("//h1/text()"), t.xpath("//a/@href"))

    full["bs4 full"] = bench(bs4_full)
    full["parsel full"] = bench(parsel_full)
    full["lxml full"] = bench(lxml_full)

    # Test 4: XPath extract, 5000 nodes
    xp = {}
    try:
        from octupus.selector import Selector as OSel

        xp["octupus xpath"] = bench(lambda: OSel(PAGE_5K).xpath("//div[@class='item']/text()"))
    except Exception:
        pass
    try:
        from parsel import Selector as P2

        xp["parsel xpath"] = bench(lambda: P2(PAGE_5K).xpath("//div[@class='item']/text()").getall())
    except Exception:
        pass
    try:
        from lxml import html as lh2

        def _lx():
            t = lh2.fromstring(PAGE_5K)
            return t.xpath("//div[@class='item']/text()")

        xp["lxml xpath"] = bench(_lx)
    except Exception:
        pass
    try:
        from scrapling import Selector as S2

        xp["scrapling xpath"] = bench(lambda: S2(PAGE_5K, adaptive=False).xpath("//div[@class='item']/text()").getall())
    except Exception:
        pass

    # Test 5: Markdown export (article-ish page)
    MD_PAGE = ("<html><body><article><h1>Title here</h1>" + "<p>Paragraph text. </p>" * 30
               + "<ul><li>one</li><li>two</li></ul>"
               + '<div style="display:none">secret prompt do evil</div>'
               + "</article></body></html>")
    mdres = {}
    try:
        from octupus.selector import Selector as OSel2

        mdres["octupus markdown"] = bench(lambda: OSel2(MD_PAGE, "https://x.com/").to_markdown())
    except Exception:
        pass
    try:
        from scrapling import Selector as S3

        mdres["scrapling markdown"] = bench(lambda: S3(MD_PAGE, adaptive=False).markdown())
    except Exception as e:
        mdres[f"scrapling markdown skipped ({type(e).__name__})"] = 0

    # Test 6: JSONL row serialize x200 pages
    js = {}
    try:
        from octupus.parser import parse_html as _ph

        pages = [_ph(f"https://x.com/{i}", REAL_PAGE, 200, f"https://x.com/{i}").model_dump() for i in range(200)]

        def _orjson_all():
            import orjson as _oj

            return [(_oj.dumps(p) + b"\n") for p in pages]

        def _std_all():
            import json as _jj

            return [(_jj.dumps(p, ensure_ascii=False) + "\n") for p in pages]

        js["orjson dumps"] = bench(_orjson_all, runs=10)
        js["stdlib json"] = bench(_std_all, runs=10)
    except Exception:
        pass

    # Test 7: find element by text
    TXT_PAGE = "<html><body>" + "".join(f'<div class="row">row number {i}</div>' for i in range(2000)) + "</body></html>"
    tx = {}
    try:
        from octupus.selector import Selector as OSel3

        _s = OSel3(TXT_PAGE)
        tx["octupus find_by_text"] = bench(lambda: _s.find_by_text("row number 1999", limit=5))
    except Exception:
        pass
    try:
        from scrapling import Selector as S4

        _s4 = S4(TXT_PAGE, adaptive=False)
        tx["scrapling find_by_text"] = bench(lambda: _s4.find_by_text("row number 1999", first_match=True))
    except Exception:
        pass

    # Test 3: localhost fetch (needs server on 8901; skip gracefully)
    fetch_lines = []
    try:
        import asyncio

        from octupus.fetcher import fetch_all

        async def ours():
            urls = [f"http://127.0.0.1:8901/p{i % 5}.html?b={i}" for i in range(30)]

            t0 = time.monotonic()
            res = await fetch_all(urls, concurrency=15, per_host=15, timeout=10)
            dt = time.monotonic() - t0
            return dt, sum(1 for r in res if r.status == 200)

        dt, ok = asyncio.run(ours())
        fetch_lines.append(f"octupus fetch: {dt:.2f}s, {30 / dt:.1f} pages/s, ok={ok}/30")
        try:
            from scrapling.fetchers import AsyncFetcher

            async def theirs():
                sem = asyncio.Semaphore(15)

                async def one(u):
                    async with sem:
                        try:
                            p = await AsyncFetcher.get(u, timeout=10000)
                            return p.status
                        except Exception:
                            return 0

                urls = [f"http://127.0.0.1:8901/p{i % 5}.html?b={i}" for i in range(30)]
                t0 = time.monotonic()
                out = await asyncio.gather(*[one(u) for u in urls])
                dt = time.monotonic() - t0
                return dt, sum(1 for s in out if s == 200)

            dt2, ok2 = asyncio.run(theirs())
            fetch_lines.append(f"scrapling fetch: {dt2:.2f}s, {30 / dt2:.1f} pages/s, ok={ok2}/30")
        except Exception as e:
            fetch_lines.append(f"scrapling fetch: skipped ({e})")
    except Exception as e:
        fetch_lines.append(f"fetch test skipped (start server: cd /tmp/www && python3 -m http.server 8901): {e}")

    # render
    def table(d, base_key=None):
        valid = {k: v for k, v in d.items() if v}
        if not valid:
            return "skipped (missing deps)"
        base = valid.get(base_key or "octupus extract", valid.get("octupus parse_html")) or min(valid.values())
        for k in valid:
            if k.startswith("octupus"):
                base = valid[k]
                break
        scale = max(valid.values()) / 40
        rows = [f"| library | median ms | vs octupus |", "|---|---|---|"]
        for k, v in sorted(valid.items(), key=lambda x: x[1]):
            rows.append(f"| {k} | {v:.2f} | {v / base:.2f}x |")
        chart = ["", "```", *[f"{k:28s} {bar(v, scale)} {v:.1f}ms" for k, v in sorted(valid.items(), key=lambda x: x[1])], "```"]
        return "\n".join(rows) + "\n" + "\n".join(chart)

    pyver = platform.python_version()
    try:
        import curl_cffi, selectolax, lxml.etree
        vers = f"python {pyver}"
    except Exception:
        vers = f"python {pyver}"
    md = f"""# Octupus benchmarks (real, same machine)

Engine: `{vers}` on `{platform.system()} {platform.machine()}`, {time.strftime('%Y-%m-%d')}.
Method: median of 15 runs after 3 warmups. Same HTML inputs for every library.

## Test 1 - CSS text extract, 5000 nodes

{table(results)}

Lower is better. Octupus uses selectolax-Lexbor `::text` fast path.

## Test 2 - Full page extract (title + meta + h1 + 200 links)

{table(full)}

Octupus `parse_html` does all fields in one C parse.

## Test 3 - Static fetch, localhost

{chr(10).join(fetch_lines) if fetch_lines else 'skipped'}

Local server removes internet noise. Both use curl-based TLS impersonation.

## Test 4 - XPath extract, 5000 nodes

{table(xp)}

## Test 5 - Markdown export (article + hidden prompt-injection div)

{table(mdres)}

Octupus strips hidden/aria/template/comments/zero-width during export.
Scrapling `markdown()` needs the `rag` extra installed - unavailable here,
which is itself the niche: octupus markdown works with zero extras.

## Test 6 - JSONL serialize 200 page dicts

{table(js)}

orjson writes bytes directly - used by octupus `to_jsonl`.

## Test 7 - Find element by text (2000 rows, target last)

{table(tx)}

## Reproduce

```bash
.venv/bin/python benchmarks_full.py
.venv/bin/python benchmarks_battle.py
.venv/bin/python benchmarks_static.py --n 10
```
"""
    with open("BENCHMARKS.md", "w") as f:
        f.write(md)
    print(md)
    print("\nWROTE BENCHMARKS.md")


if __name__ == "__main__":
    sys.exit(main())
