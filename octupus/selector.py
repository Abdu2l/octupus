"""Unified Selector: CSS + XPath + text/regex search + similarity + markdown.

Beats Scrapling parity without their overhead: selectolax for CSS speed,
lxml for XPath, stdlib for text search.
"""
import re
from urllib.parse import urljoin

try:
    from selectolax.lexbor import LexborHTMLParser as _SLParser
except ImportError:
    try:
        from selectolax.parser import LexborHTMLParser as _SLParser
    except ImportError:
        from selectolax.parser import HTMLParser as _SLParser

try:
    from lxml import html as _lxml_html
except ImportError:
    _lxml_html = None


def _parse_selectolax(html: str):
    if _SLParser is None:
        raise RuntimeError("selectolax not installed")
    if isinstance(html, str):
        html = html.encode("utf-8", errors="ignore")
    return _SLParser(html)


class Selector:
    def __init__(self, html: str, url: str = ""):
        self.html = html or ""
        self.url = url
        self._tree = None
        self._lxml = None

    @property
    def tree(self):
        if self._tree is None:
            self._tree = _parse_selectolax(self.html)
        return self._tree

    @property
    def lxml_tree(self):
        if _lxml_html is None:
            raise RuntimeError("lxml not installed, pip install lxml for XPath")
        if self._lxml is None:
            self._lxml = _lxml_html.fromstring(self.html or "<html></html>")
        return self._lxml

    def css(self, selector: str, limit: int = 5000) -> list:
        sel = selector.replace("::text", "").replace("::attr(href)", "").strip() or "*"
        try:
            nodes = self.tree.css(sel)[:limit]
        except Exception:
            return []
        if "::text" in selector:
            return [(_node_text(n) or "") for n in nodes]
        if "::attr(href)" in selector:
            out = []
            for n in nodes:
                try:
                    out.append(n.attributes.get("href", ""))
                except Exception:
                    out.append("")
            return out
        return list(nodes)

    def css_first(self, selector: str):
        try:
            return self.tree.css_first(selector.replace("::text", "").strip())
        except Exception:
            return None

    def xpath(self, expr: str, limit: int = 5000) -> list:
        try:
            res = self.lxml_tree.xpath(expr)
        except Exception:
            return []
        out = []
        for r in res[:limit]:
            if isinstance(r, str):
                out.append(r)
            else:
                try:
                    out.append(r.text_content().strip())
                except Exception:
                    out.append(str(r))
        return out

    def find_by_text(self, text: str, regex: bool = False, limit: int = 100) -> list:
        """Find elements containing text (substring or regex)."""
        out = []
        try:
            for n in self.tree.css("*"):
                if len(out) >= limit:
                    break
                try:
                    t = n.text(strip=True) or ""
                except Exception:
                    continue
                if not t:
                    continue
                if regex:
                    try:
                        if re.search(text, t):
                            out.append(n)
                    except re.error:
                        continue
                elif text.lower() in t.lower():
                    out.append(n)
        except Exception:
            pass
        return out

    def find_similar(self, node, limit: int = 10) -> list:
        """Find elements with same tag + similar class set (cheap similarity)."""
        try:
            tag = getattr(node, "tag", "")
            attrs = getattr(node, "attributes", {}) or {}
            cls = set((attrs.get("class") or "").split())
        except Exception:
            return []
        scored = []
        try:
            for cand in self.tree.css(tag or "*"):
                if cand is node:
                    continue
                try:
                    c_attrs = cand.attributes or {}
                    c_cls = set((c_attrs.get("class") or "").split())
                except Exception:
                    continue
                score = len(cls & c_cls) - 0.1 * abs(len(cls) - len(c_cls))
                if tag and getattr(cand, "tag", "") == tag:
                    score += 1.0
                if score > 0:
                    scored.append((score, cand))
        except Exception:
            pass
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:limit]]

    def links(self, limit: int = 500) -> list[tuple[str, str]]:
        out = []
        try:
            for a in self.tree.css("a[href]"):
                if len(out) >= limit:
                    break
                try:
                    href = (a.attributes.get("href") or "").strip()
                    text = a.text(strip=True) or ""
                except Exception:
                    continue
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                out.append((text[:200], urljoin(self.url, href)))
        except Exception:
            pass
        return out

    def to_markdown(self, main_content_only: bool = True) -> str:
        """Clean HTML -> LLM-ready markdown. Strips scripts/styles/nav + hidden/prompt-injection."""
        import re as _re

        try:
            tree = self.lxml_tree
        except Exception:
            return ""
        try:
            for el in tree.xpath("//script|//style|//noscript|//header|//footer|//nav|//template|//svg|//iframe"):
                try:
                    el.drop_tree()
                except Exception:
                    pass
            # hidden / aria-hidden / inline display:none (prompt-injection vectors)
            for el in tree.xpath("//*[@aria-hidden='true']|//*[@hidden]|//*[contains(@style,'display:none')]|//*[contains(@style,'display: none')]|//*[contains(@class,'hidden')]"):
                try:
                    el.drop_tree()
                except Exception:
                    pass
            try:
                from lxml import etree as _etree

                for c in tree.xpath("//comment()"):
                    try:
                        c.getparent().remove(c)
                    except Exception:
                        pass
            except Exception:
                pass
            root = tree
            if main_content_only:
                for xp in ("//main", "//article", "//*[@role='main']"):
                    try:
                        found = tree.xpath(xp)
                        if found:
                            root = found[0]
                            break
                    except Exception:
                        pass
            lines: list[str] = []
            for el in root.iter():
                tag = (el.tag or "").lower() if isinstance(el.tag, str) else ""
                if tag in ("h1", "h2", "h3"):
                    t = (el.text_content() or "").strip()
                    if t:
                        prefix = "#" * (int(tag[1]) if tag[1].isdigit() else 1)
                        lines.append(f"{prefix} {t}")
                elif tag == "p":
                    t = (el.text_content() or "").strip()
                    if t:
                        lines.append(t)
                elif tag == "li":
                    t = (el.text_content() or "").strip()
                    if t:
                        lines.append(f"- {t}")
                elif tag == "a":
                    t = (el.text_content() or "").strip()
                    href = el.get("href", "")
                    if t and href:
                        lines.append(f"[{t}]({urljoin(self.url, href)})")
            # fallback: plain text blocks
            if not lines:
                t = (root.text_content() or "").strip()
                t = _re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
                return "\n\n".join([l.strip() for l in t.split("\n") if l.strip()][:200])
            # dedupe preserve order + strip zero-width/control chars
            seen = set()
            clean = []
            for l in lines:
                l = _re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\x00-\x08\x0b\x0c\x0e-\x1f]", "", l).strip()
                if l and l not in seen:
                    seen.add(l)
                    clean.append(l)
            return "\n\n".join(clean[:2000])
        except Exception:
            return ""


def _node_text(node) -> str:
    try:
        return node.text(strip=True) or ""
    except Exception:
        return ""
