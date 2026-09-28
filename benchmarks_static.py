"""Fair static showdown: ours vs Scrapling AsyncFetcher on same URLs.

Run: .venv/bin/python benchmarks_static.py [--n 10]
Static-only pages so browser/JS is not a factor. Measures wall time + ok rate.
"""
import asyncio
import sys
import time

URLS = [
    "https://example.com",
    "https://httpbin.org/html",
    "https://en.wikipedia.org/wiki/Web_scraping",
    "https://quotes.toscrape.com/",
    "https://books.toscrape.com/",
]


async def bench_ours(urls: list[str], concurrency: int = 20):
    from octupus.fetcher import fetch_all

    t0 = time.monotonic()
    res = await fetch_all(urls, concurrency=concurrency, per_host=10, timeout=20)
    dt = time.monotonic() - t0
    ok = sum(1 for r in res if r.status and 200 <= r.status < 400 and r.html)
    return dt, ok, len(res)


async def bench_scrapling(urls: list[str], concurrency: int = 20):
    from scrapling.fetchers import AsyncFetcher

    sem = asyncio.Semaphore(concurrency)

    async def one(u: str):
        async with sem:
            try:
                p = await AsyncFetcher.get(u, timeout=20000)
                html = getattr(p, "html_content", "") or ""
                st = int(getattr(p, "status", 0) or 0)
                return st, html
            except Exception:
                return 0, ""

    t0 = time.monotonic()
    out = await asyncio.gather(*[one(u) for u in urls])
    dt = time.monotonic() - t0
    ok = sum(1 for st, html in out if 200 <= st < 400 and html)
    return dt, ok, len(out)


async def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10
    urls = (URLS * ((n // len(URLS)) + 1))[:n]
    # cache-bust so dedupe/CDN doesn't skew: query param per repeat
    urls = [f"{u}?b={i}" if "?" not in u else f"{u}&b={i}" for i, u in enumerate(urls)]

    dt1, ok1, tot1 = await bench_ours(urls)
    dt2, ok2, tot2 = await bench_scrapling(urls)
    print(f"urls: {tot1} static pages, concurrency 20")
    print(f"ours:      {dt1:.2f}s = {tot1/max(dt1,0.01):.1f} pages/s ok={ok1}/{tot1}")
    print(f"scrapling: {dt2:.2f}s = {tot2/max(dt2,0.01):.1f} pages/s ok={ok2}/{tot2}")
    if dt1 < dt2 and ok1 >= ok2:
        print("winner static: ours (faster + >= success)")
    elif dt2 < dt1 and ok2 >= ok1:
        print("winner static: scrapling")
    else:
        print("mixed: compare speed vs ok rate above")


if __name__ == "__main__":
    asyncio.run(main())
