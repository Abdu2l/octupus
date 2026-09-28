from elitescraper.hybrid import ClearanceStore
from elitescraper.jsdetect import analyze


def test_jsdetect_next_data():
    html = '<html><body><script id="__NEXT_DATA__" type="application/json">{"page":"x"}</script></body></html>'
    r = analyze(html)
    assert r["decision"] == "api-direct" and r["next_data"] == {"page": "x"}


def test_jsdetect_empty_shell_needs_browser():
    assert analyze('<html><body><div id="root"></div></body></html>')["decision"] == "browser"


def test_jsdetect_plain_static():
    assert analyze("<html><head><title>t</title></head><body><p>hi</p></body></html>")["decision"] == "static"


def test_clearance_ttl_refresh():
    import time

    cs = ClearanceStore(ttl_s=10)
    cs.put("https://a.com/x", None, {"cf_clearance": "abc"})
    assert cs.get("https://a.com/x", None) == {"cf_clearance": "abc"}
    cs._ts[("a.com", "")] = time.monotonic() - 9  # past 60% TTL
    assert cs.get("https://a.com/x", None) is None
