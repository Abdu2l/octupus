"""Fast HTML extraction backed by selectolax (Lexbor, C) on bytes."""
from urllib.parse import urljoin

try:
    from selectolax.lexbor import LexborHTMLParser as _Parser
except ImportError:
    try:
        from selectolax.parser import LexborHTMLParser as _Parser
    except ImportError:
        from selectolax.parser import HTMLParser as _Parser

from .schemas import Link, ScrapedPage

MAX_LINKS = 500
MAX_TEXT_LEN = 300


def _text(node) -> str:
    try:
        t = node.text(strip=True) or ""
    except Exception:
        t = ""
    return t[:MAX_TEXT_LEN]


def _parse(html: str | bytes):
    # bytes avoids r.text decode; strip scripts/styles once in C
    if isinstance(html, str):
        html = html.encode("utf-8", errors="ignore")
    try:
        tree = _Parser(html)
    except Exception:
        tree = _Parser(b"<html></html>")
    try:
        tree.strip_tags(["script", "style", "noscript", "iframe"])
    except Exception:
        pass
    return tree


def extract_texts(html: str, selector: str, limit: int = 5000) -> list[str]:
    """Minimal selector->texts path for benchmarks (fair vs Scrapling ::text)."""
    try:
        tree = _parse(html)
    except Exception:
        return []
    out: list[str] = []
    try:
        # support "selector::text" shorthand
        sel = selector.replace("::text", "").strip() or "*"
        for n in tree.css(sel):
            if len(out) >= limit:
                break
            try:
                t = n.text(strip=True) or ""
            except Exception:
                continue
            if t:
                out.append(t)
    except Exception:
        pass
    return out


def parse_html(
    url: str,
    html: str,
    status: int = 200,
    final_url: str = "",
    extract_links: bool = True,
    max_links: int = MAX_LINKS,
) -> ScrapedPage:
    if not html:
        return ScrapedPage(url=url, final_url=final_url or url, status=status)

    try:
        tree = _parse(html)
    except Exception:
        return ScrapedPage(url=url, final_url=final_url or url, status=status, error="parse_error")

    title = ""
    try:
        node = tree.css_first("title")
        if node is not None:
            title = _text(node)
    except Exception:
        pass

    meta_description = ""
    try:
        m = tree.css_first('meta[name="description"]')
        if m is not None:
            meta_description = ((m.attributes.get("content") or "").strip())[:MAX_TEXT_LEN]
        if not meta_description:
            m2 = tree.css_first('meta[property="og:description"]')
            if m2 is not None:
                meta_description = ((m2.attributes.get("content") or "").strip())[:MAX_TEXT_LEN]
    except Exception:
        pass

    try:
        h1 = [t for t in (_text(n) for n in tree.css("h1")) if t][:50]
    except Exception:
        h1 = []
    try:
        h2 = [t for t in (_text(n) for n in tree.css("h2")) if t][:100]
    except Exception:
        h2 = []

    links: list[Link] = []
    if extract_links:
        try:
            base = final_url or url
            # fast path: absolute URLs skip urljoin; validate once at the end
            raw_links: list[tuple[str, str]] = []
            for a in tree.css("a[href]"):
                if len(raw_links) >= max_links:
                    break
                try:
                    raw_href = (a.attributes.get("href") or "").strip()
                except Exception:
                    continue
                if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                if raw_href.startswith(("http://", "https://")):
                    href = raw_href
                else:
                    try:
                        href = urljoin(base, raw_href)
                    except Exception:
                        continue
                try:
                    text = a.text(strip=True) or ""
                except Exception:
                    text = ""
                raw_links.append((text[:200], href[:2000]))
            links = [Link.model_construct(text=t, href=h) for t, h in raw_links]
        except Exception:
            pass

    return ScrapedPage(
        url=url,
        final_url=final_url or url,
        status=status,
        title=title,
        meta_description=meta_description,
        h1=h1,
        h2=h2,
        links=links,
    )
