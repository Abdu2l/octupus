"""CLI: scrape urls.txt --out out.jsonl"""
import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path

from .fetcher import fetch_stream
from .frontier import LinkExtractor, SeenDB, normalize_urls
from .parser import parse_html
from .robots import RobotsCache, is_blocked, parse_sitemap
from .schemas import ScrapedPage
from .selector import Selector

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass


def read_input_urls(args) -> list[str]:
    raw: list[str] = []
    if args.url:
        raw.extend(args.url)
    if args.input:
        p = Path(args.input)
        if not p.exists():
            print(f"input file not found: {p}", file=sys.stderr)
            sys.exit(2)
        raw.extend(p.read_text(encoding="utf-8", errors="ignore").splitlines())
    if not raw and not sys.stdin.isatty():
        raw.extend(sys.stdin.read().splitlines())
    urls = normalize_urls(raw)
    if args.limit and args.limit > 0:
        urls = urls[: args.limit]
    return urls


def load_proxies(path: str | None) -> list[str]:
    if not path:
        return []
    try:
        return [l.strip() for l in Path(path).read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    except Exception as e:
        print(f"proxy file error: {e}", file=sys.stderr)
        return []


async def run(args) -> int:
    urls = read_input_urls(args)
    # --sitemap mode: treat inputs as sitemap.xml URLs, expand to pages
    if args.sitemap:
        from .fetcher import fetch_all as _fetch_all

        print(f"sitemap: expanding {len(urls)} sitemap(s)", file=sys.stderr)
        res = await _fetch_all(urls, concurrency=min(20, args.concurrency), per_host=5, timeout=args.timeout)
        expanded: list[str] = []
        for r in res:
            if r.html:
                expanded.extend(parse_sitemap(r.html))
        urls = normalize_urls(expanded)
        print(f"sitemap: {len(urls)} pages", file=sys.stderr)
    # --shopify mode: treat inputs as shop domains
    if args.shopify:
        from .spiders import ShopifySpider

        all_items: list[dict] = []
        for shop in urls:
            sp = ShopifySpider()
            sp.target_website = shop
            sp.max_products = args.max_pages
            all_items.extend(await sp.run(concurrency=20, per_host=5, timeout=args.timeout))
        out = Path(args.out)
        import json as _json

        with out.open("w", encoding="utf-8") as f:
            for it in all_items:
                f.write(_json.dumps(it, ensure_ascii=False) + "\n")
        print(f"shopify: {len(all_items)} products -> {out}", file=sys.stderr)
        return 0
    if not urls:
        print("no URLs given. Provide --url, input file, or stdin.", file=sys.stderr)
        return 2

    robots = RobotsCache(enabled=args.robots)
    if args.robots:
        before = len(urls)
        urls = [u for u in urls if robots.allowed(u)]
        print(f"robots: {before-len(urls)} disallowed, {len(urls)} left", file=sys.stderr)
        if not urls:
            return 0

    seen_db = SeenDB(args.resume_db) if args.resume_db else None
    if seen_db:
        fresh = seen_db.filter_new(urls)
        skipped = len(urls) - len(fresh)
        if skipped:
            print(f"resume: skipping {skipped} already-seen URLs", file=sys.stderr)
        urls = fresh
        if not urls:
            print("resume: nothing new to scrape", file=sys.stderr)
            return 0

    proxies = load_proxies(args.proxy_file)
    if getattr(args, "browser_fallback", False) and not args.browser:
        args.browser = True
        args.browser_on_block = True
    extractor = LinkExtractor(same_domain_only=args.same_domain) if args.follow_links else None
    from .devcache import DevCache

    devcache = DevCache(getattr(args, "dev_cache", None))
    blocked_domains = [d.strip() for d in (getattr(args, "blocked_domains", "") or "").split(",") if d.strip()]
    from .hybrid import ClearanceStore

    clearance = ClearanceStore()
    clearance_uas: dict[tuple[str, str], str] = {}

    out = Path(args.out)
    is_csv = bool(args.csv or out.suffix.lower() == ".csv")
    f = out.open("w", encoding="utf-8", newline="")
    csv_writer = None
    if is_csv:
        fields = ["url", "final_url", "status", "title", "meta_description", "h1", "h2", "links", "link_texts", "fetched_at", "error"]
        csv_writer = csv.DictWriter(f, fieldnames=fields)
        csv_writer.writeheader()

    # crawl state for --follow-links
    seen = set(urls)
    depth: dict[str, int] = {u: 0 for u in urls}
    pending: list[str] = list(urls)
    total_ok = 0
    total_fail = 0
    total_blocked = 0
    total_done = 0
    t0 = time.monotonic()
    max_pages = args.max_pages or 10_000_000
    browser_pool = None
    if args.browser:
        from .browser import BrowserPool

        browser_pool = BrowserPool(
            headless=not args.no_headless,
            max_pages=args.browser_pages,
            block_resources=not args.no_block_resources,
            solve_cloudflare=not args.no_cf_solve,
            hide_canvas=args.hide_canvas,
            cdp_url=args.cdp_url,
            executable_path=args.executable_path,
            capture_xhr=args.capture_xhr,
            blocked_domains=blocked_domains,
            block_ads=args.block_ads,
            dns_over_https=args.doh,
            storage_state_dir=args.storage_state_dir,
        )
        try:
            await browser_pool.start()
        except Exception as e:
            print(f"browser start failed ({e}), continuing static-only", file=sys.stderr)
            browser_pool = None

    def _write_page(page: ScrapedPage, html_for_md: str = ""):
        nonlocal total_ok, total_fail, total_done
        if args.markdown:
            md = Selector(html_for_md or "", page.final_url or page.url).to_markdown() if html_for_md else ""
            import json as _json

            f.write(_json.dumps({"url": page.url, "title": page.title, "markdown": md[:20000]}, ensure_ascii=False) + "\n")
        elif is_csv:
            assert csv_writer is not None
            csv_writer.writerow(page.flatten())
        else:
            f.write(page.to_jsonl() + "\n")

    try:
        while pending and total_done < max_pages:
            batch, pending = pending[: args.max_pages - total_done][:5000], pending[5000:]
            if not batch:
                break
            if seen_db:
                seen_db.mark_many(batch)
            # dev-cache replay (niche): skip network for cached URLs
            if devcache:
                fresh_batch = []
                for u in batch:
                    hit = devcache.get(u)
                    if hit:
                        st, html, furl = hit
                        page = parse_html(u, html, st, furl, extract_links=not args.no_links, max_links=args.max_links)
                        _write_page(page, html if args.markdown else "")
                        total_done += 1
                        if 200 <= st < 400:
                            total_ok += 1
                        else:
                            total_fail += 1
                    else:
                        fresh_batch.append(u)
                batch = fresh_batch
                if not batch:
                    continue
            n_batch = 0
            async for r in fetch_stream(
                batch,
                concurrency=args.concurrency,
                per_host=args.per_host,
                timeout=args.timeout,
                retries=args.retries,
                impersonate=args.impersonate,
                autothrottle=not args.no_autothrottle,
                base_delay=args.delay,
                proxies=proxies,
                stealth_headers=not args.no_stealth_headers,
                google_search=args.google_search,
                cookies=None,
                user_agent="",
            ):
                # attach pinned clearance for this domain if solved earlier (hybrid)
                # (applied per-batch below on retry; first attempt is clean static)
                if r.html and r.status:
                    page = parse_html(
                        r.url, r.html, r.status, r.final_url, extract_links=not args.no_links, max_links=args.max_links
                    )
                    blocked, reason = is_blocked(r.status, r.html, r.url)
                    if blocked:
                        total_blocked += 1
                        page.error = reason
                        solved = False
                        # HYBRID: solve once per domain, retry static with clearance (fast path)
                        if args.hybrid and browser_pool:
                            from .hybrid import domain_of, solve_domain_once

                            proxy0 = proxies[0] if proxies else None
                            jar = clearance.get(r.url, proxy0)
                            ua = clearance_uas.get((domain_of(r.url), proxy0 or ""), "")
                            if jar is None:
                                try:
                                    jar, ua = await solve_domain_once(browser_pool, r.url, proxy0)
                                    if jar:
                                        clearance.put(r.url, proxy0, jar)
                                        clearance_uas[(domain_of(r.url), proxy0 or "")] = ua
                                except Exception:
                                    jar = None
                            if jar:
                                try:
                                    from .fetcher import fetch_all as _fa

                                    rr = await _fa([r.url], concurrency=1, per_host=1, timeout=args.timeout,
                                                   retries=1, impersonate=args.impersonate, autothrottle=False,
                                                   proxies=[proxy0] if proxy0 else None,
                                                   stealth_headers=not args.no_stealth_headers,
                                                   google_search=args.google_search, cookies=jar, user_agent=ua)
                                    if rr and rr[0].html and not is_blocked(rr[0].status, rr[0].html)[0]:
                                        r = rr[0]
                                        page = parse_html(r.url, r.html, r.status, r.final_url, extract_links=not args.no_links, max_links=args.max_links)
                                        page.error = ""
                                        solved = True
                                    else:
                                        clearance.drop(r.url, proxy0)
                                except Exception:
                                    pass
                        # browser fallback for still-blocked pages
                        if not solved and browser_pool and (args.browser_on_block or args.hybrid):
                            try:
                                st, bhtml, furl, _cap = await browser_pool.fetch(r.url, wait_selector=args.wait_selector or None, network_idle=args.network_idle)
                                if bhtml and not is_blocked(st, bhtml)[0]:
                                    page = parse_html(r.url, bhtml, st, furl, extract_links=not args.no_links, max_links=args.max_links)
                                    page.error = ""
                                    r.html = bhtml
                            except Exception as e:
                                page.error = f"{reason} browser_fail:{type(e).__name__}"
                else:
                    page = ScrapedPage(url=r.url, final_url=r.final_url or r.url, status=r.status, error=r.error)
                    # browser fallback on empty/static failure
                    if browser_pool and args.browser_on_block and r.status in (0, 403, 429):
                        try:
                            st, bhtml, furl, _cap = await browser_pool.fetch(r.url, wait_selector=args.wait_selector or None, network_idle=args.network_idle)
                            if bhtml:
                                page = parse_html(r.url, bhtml, st, furl, extract_links=not args.no_links, max_links=args.max_links)
                                r.html = bhtml
                        except Exception:
                            pass
                _write_page(page, r.html if args.markdown else "")
                if devcache and r.html and r.status and 200 <= r.status < 400:
                    devcache.put(r.url, page.status, r.html, page.final_url or r.url)
                total_done += 1
                n_batch += 1
                if 200 <= page.status < 400:
                    total_ok += 1
                else:
                    total_fail += 1
                # follow links (breadth-first, depth-limited)
                if extractor and 200 <= page.status < 400 and depth.get(r.url, 0) < args.max_depth:
                    hrefs = [l.href for l in page.links]
                    for nxt in extractor.extract(r.final_url or r.url, hrefs, limit=100):
                        if nxt not in seen and total_done + len(pending) < max_pages:
                            seen.add(nxt)
                            depth[nxt] = depth.get(r.url, 0) + 1
                            pending.append(nxt)
                if total_done % 50 == 0:
                    dt = time.monotonic() - t0
                    print(f"\r[{total_done}] {total_done/max(dt,0.01):.1f} pages/s ok={total_ok} fail={total_fail} queued={len(pending)}", end="", file=sys.stderr, flush=True)
            if not extractor:
                break
    finally:
        f.close()
        if browser_pool:
            try:
                await browser_pool.close()
            except Exception:
                pass

    dt = time.monotonic() - t0
    rps = total_done / dt if dt > 0 else 0
    print(f"\nscraped {total_done} -> {out} | ok={total_ok} fail={total_fail} blocked={total_blocked} | {dt:.1f}s {rps:.1f} pages/s", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="elitescrape", description="Elite fast generic scraper v0.3")
    ap.add_argument("input", nargs="?", default=None, help="text file with one URL per line")
    ap.add_argument("--url", action="append", default=[], help="single URL (repeatable)")
    ap.add_argument("--out", default="out.jsonl", help="output file (.jsonl or .csv)")
    ap.add_argument("--csv", action="store_true", help="force CSV output")
    ap.add_argument("--markdown", action="store_true", help="output {url,title,markdown} for RAG (no LLM)")
    ap.add_argument("--concurrency", type=int, default=150, help="global concurrent requests (1-500)")
    ap.add_argument("--per-host", type=int, default=15, help="concurrent requests per host")
    ap.add_argument("--timeout", type=int, default=20, help="per-request timeout seconds")
    ap.add_argument("--delay", type=float, default=0.0, help="base politeness delay (autothrottle adapts on top)")
    ap.add_argument("--no-autothrottle", action="store_true", help="disable adaptive AutoThrottle")
    ap.add_argument("--retries", type=int, default=2, help="retries on 429/5xx/timeout")
    ap.add_argument("--impersonate", default="chrome", help="curl_cffi profile (chrome=latest, safari18_0, ...)")
    ap.add_argument("--no-stealth-headers", action="store_true", help="disable Sec-Fetch stealth headers")
    ap.add_argument("--google-search", action="store_true", help="set Referer as google search (Scrapling parity)")
    ap.add_argument("--proxy-file", default=None, help="file with one proxy per line for rotation")
    ap.add_argument("--limit", type=int, default=0, help="only take first N URLs")
    ap.add_argument("--no-links", action="store_true", help="skip link extraction (2-3x faster parse)")
    ap.add_argument("--max-links", type=int, default=300, help="max links stored per page")
    ap.add_argument("--follow-links", action="store_true", help="crawl mode: follow same-domain links")
    ap.add_argument("--max-depth", type=int, default=1, help="crawl depth when --follow-links")
    ap.add_argument("--same-domain", action="store_true", default=True, help="only follow same domain")
    ap.add_argument("--max-pages", type=int, default=10000, help="cap total pages (crawl budget)")
    ap.add_argument("--resume-db", default=None, help="sqlite path for pause/resume seen URLs")
    ap.add_argument("--robots", action="store_true", help="obey robots.txt + Crawl-delay")
    ap.add_argument("--sitemap", action="store_true", help="treat inputs as sitemap.xml URLs and expand")
    ap.add_argument("--shopify", action="store_true", help="treat inputs as shop domains, dump /products.json")
    ap.add_argument("--browser", action="store_true", help="enable Playwright tab-pool fallback")
    ap.add_argument("--browser-on-block", action="store_true", help="use browser only when static is blocked/empty")
    ap.add_argument("--browser-pages", type=int, default=4, help="browser tab pool size")
    ap.add_argument("--no-headless", action="store_true", help="run browser visible")
    ap.add_argument("--wait-selector", default=None, help="browser: wait for CSS selector attached")
    ap.add_argument("--network-idle", action="store_true", help="browser: wait for networkidle")
    ap.add_argument("--no-block-resources", action="store_true", help="browser: don't block images/fonts")
    ap.add_argument("--no-cf-solve", action="store_true", help="browser: disable Cloudflare click solver")
    ap.add_argument("--hide-canvas", action="store_true", help="browser: add canvas noise")
    ap.add_argument("--cdp-url", default=None, help="browser: connect to remote CDP instead of launch")
    ap.add_argument("--executable-path", default=None, help="browser: custom Chromium executable")
    ap.add_argument("--capture-xhr", default=None, help="browser: regex for XHR URLs to capture")
    ap.add_argument("--blocked-domains", default="", help="browser: comma-separated domains to block")
    ap.add_argument("--block-ads", action="store_true", help="browser: block tracker substrings")
    ap.add_argument("--doh", action="store_true", help="browser: DNS-over-HTTPS via Cloudflare")
    ap.add_argument("--storage-state-dir", default=None, help="browser: persist clearance cookies per proxy")
    ap.add_argument("--dev-cache", default=None, help="disk cache dir: replay HTML without re-hitting servers")
    ap.add_argument("--hybrid", action="store_true", help="solve CF once per domain in browser, continue static with clearance (fast)")
    ap.add_argument("--browser-fallback", action="store_true", help="deprecated alias for --browser --browser-on-block")
    return ap


def main():
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
