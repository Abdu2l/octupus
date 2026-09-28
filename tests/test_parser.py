from octupus.frontier import normalize_urls
from octupus.parser import parse_html


def test_normalize_dedupes():
    urls = normalize_urls(["https://a.com", "https://a.com ", "", "example.com", "https://a.com"])
    assert urls[0] == "https://a.com"
    assert "https://example.com" in urls
    assert len(urls) == 2


def test_parse_basic():
    html = """
    <html><head><title>Hi</title>
    <meta name="description" content="desc here">
    </head><body>
    <h1>Main</h1><h2>Sub</h2>
    <a href="/x">go</a>
    </body></html>
    """
    p = parse_html("https://example.com/", html, 200, "https://example.com/")
    assert p.title == "Hi"
    assert p.meta_description == "desc here"
    assert p.h1 == ["Main"]
    assert p.h2 == ["Sub"]
    assert p.links[0].href == "https://example.com/x"
    assert p.links[0].text == "go"


def test_parse_og_fallback():
    html = '<html><head><meta property="og:description" content="ogd"></head><body></body></html>'
    p = parse_html("https://example.com", html, 200)
    assert p.meta_description == "ogd"
