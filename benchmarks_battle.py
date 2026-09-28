"""Head-to-head parser benchmark: ours vs Scrapling vs raw selectolax.

Run: .venv/bin/python benchmarks_battle.py
"""
import time

HTML_5K = "<html><body>" + "".join(f'<div class="item">text {i}</div>' for i in range(5000)) + "</body></html>"


def bench(fn, n=30):
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1000


def main():
    from selectolax.parser import HTMLParser

    from octupus.parser import extract_texts

    try:
        from scrapling import Selector as S

        has_scrapling = True
    except ImportError:
        has_scrapling = False

    t_ours = bench(lambda: extract_texts(HTML_5K, ".item::text"))
    t_naive = bench(lambda: [n.text() for n in HTMLParser(HTML_5K).css(".item")])
    print(f"ours extract_texts (.item::text, 5k nodes): {t_ours:.2f} ms")
    print(f"selectolax naive loop (their bench method): {t_naive:.2f} ms")
    if has_scrapling:
        t_scrap = bench(lambda: S(HTML_5K, adaptive=False).css(".item::text").getall())
        print(f"scrapling Selector.css(.item::text):       {t_scrap:.2f} ms")
        print(f"ours vs scrapling: {t_scrap/max(t_ours,0.01):.2f}x {'FASTER' if t_ours < t_scrap else 'slower'}")
    else:
        print("scrapling not installed, skipping")


if __name__ == "__main__":
    main()
