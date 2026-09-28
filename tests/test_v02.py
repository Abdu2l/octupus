import asyncio

from elitescraper.frontier import AutoThrottle, LinkExtractor, ProxyRotator
from elitescraper.parser import extract_texts


def test_extract_texts_matches_css():
    html = '<div class="item">a</div><div class="item">b</div>'
    assert extract_texts(html, ".item::text") == ["a", "b"]


def test_link_extractor_same_domain():
    ex = LinkExtractor(same_domain_only=True)
    out = ex.extract("https://a.com/p", ["/x", "https://a.com/y", "https://evil.com/z"])
    assert "https://a.com/x" in out
    assert all("evil.com" not in u for u in out)


def test_proxy_rotator_cycles():
    async def go():
        r = ProxyRotator(["http://1", "http://2"])
        a = await r.next()
        b = await r.next()
        c = await r.next()
        return a, b, c
    a, b, c = asyncio.run(go())
    assert (a, b, c) == ("http://1", "http://2", "http://1")


def test_autothrottle_backs_off():
    async def go():
        t = AutoThrottle(enabled=True, base=0.0)
        await t.mark("https://a.com/", latency=0.1, status=429)
        return t.delay_for("https://a.com/")
    assert asyncio.run(go()) >= 1.0
