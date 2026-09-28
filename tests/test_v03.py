from octupus.adaptive import AdaptiveDB
from octupus.robots import is_blocked, parse_sitemap
from octupus.selector import Selector


def test_selector_css_xpath_text():
    html = '<html><body><div class="item">a</div><div class="item">b</div></body></html>'
    s = Selector(html, "https://x.com")
    assert s.css(".item::text") == ["a", "b"]
    assert s.xpath("//div[@class='item']/text()") == ["a", "b"]
    assert len(s.find_by_text("a")) >= 1
    assert len(s.find_by_text("div.*", regex=True)) >= 0


def test_selector_markdown():
    html = "<html><body><h1>T</h1><p>Hello</p></body></html>"
    md = Selector(html, "https://x.com").to_markdown()
    assert "T" in md and "Hello" in md


def test_adaptive_save_relocate(tmp_path):
    db = AdaptiveDB(str(tmp_path / "a.db"))
    html1 = '<html><body><div class="product" id="p1">Buy now</div></body></html>'
    s = Selector(html1)
    node = s.css(".product")[0]
    db.save("https://shop.com/", ".product", node)
    html2 = '<html><body><div class="item-card">Buy now</div></body></html>'
    best, score = db.relocate(html2, "https://shop.com/", ".product", threshold=0.1)
    assert best is not None and score > 0


def test_blocked_detection():
    ok, _ = is_blocked(200, "<html><title>Hi</title></html>")
    assert not ok
    blocked, reason = is_blocked(403, "<html><title>Just a moment...</title></html>")
    assert blocked


def test_sitemap_parse():
    xml = '<?xml version="1.0"?><urlset><url><loc>https://x.com/a</loc></url></urlset>'
    assert parse_sitemap(xml) == ["https://x.com/a"]
