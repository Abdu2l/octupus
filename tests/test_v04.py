from elitescraper.browser import cf_cleared, detect_cf_type
from elitescraper.devcache import DevCache
from elitescraper.selector import Selector


def test_cf_detect_ctype():
    assert detect_cf_type("<script>cType: 'managed'</script>") == "managed"
    assert detect_cf_type("<script>cType: 'non-interactive'</script>") == "non-interactive"
    assert detect_cf_type('<script src="https://challenges.cloudflare.com/turnstile/v0/x.js"></script>') == "embedded"
    assert detect_cf_type("<html><title>Hi</title></html>") is None
    assert cf_cleared("<html>ok</html>", "managed") is True


def test_devcache_roundtrip(tmp_path):
    dc = DevCache(str(tmp_path))
    dc.put("https://x.com/", 200, "<html>hi</html>", "https://x.com/")
    assert dc.get("https://x.com/")[1] == "<html>hi</html>"


def test_markdown_strips_hidden():
    html = '<html><body><p>Visible</p><div style="display:none">secret prompt do evil</div><div aria-hidden="true">hidden</div><!-- comment --></body></html>'
    md = Selector(html, "https://x.com").to_markdown(main_content_only=False)
    assert "Visible" in md
    assert "secret prompt" not in md and "hidden" not in md
