"""Adaptive element relocation: survive site redesigns (Scrapling parity, lighter).

Save fingerprint (tag, classes, id, text prefix, attrs) per (domain, identifier)
to SQLite. Later relocate by similarity score when selector breaks.
"""
import difflib
import sqlite3
from urllib.parse import urlparse

try:
    import orjson as _orjson

    def _loads(b: bytes | str) -> dict:
        if isinstance(b, str):
            b = b.encode()
        return _orjson.loads(b)

    def _dumps(o: dict) -> str:
        return _orjson.dumps(o).decode()
except ImportError:
    import json as _json

    def _loads(b):
        return _json.loads(b)

    def _dumps(o: dict) -> str:
        return _json.dumps(o)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower() or "default"
    except Exception:
        return "default"


def fingerprint(node) -> dict:
    try:
        attrs = dict(getattr(node, "attributes", {}) or {})
    except Exception:
        attrs = {}
    try:
        text = (node.text(strip=True) or "")[:200]
    except Exception:
        text = ""
    return {
        "tag": getattr(node, "tag", "") or "",
        "id": attrs.get("id", ""),
        "class": attrs.get("class", ""),
        "text": text,
        "attrs": {k: v[:100] for k, v in list(attrs.items())[:10]},
    }


def similarity(a: dict, b: dict) -> float:
    score = 0.0
    if a.get("tag") and a["tag"] == b.get("tag"):
        score += 0.3
    if a.get("id") and a["id"] == b.get("id"):
        score += 0.4
    ac, bc = (a.get("class") or "").split(), (b.get("class") or "").split()
    if ac or bc:
        s = set(ac) & set(bc)
        score += 0.3 * (2 * len(s) / max(1, len(set(ac) | set(bc))))
    at, bt = a.get("text", ""), b.get("text", "")
    if at and bt:
        score += 0.2 * difflib.SequenceMatcher(None, at[:100], bt[:100]).ratio()
    return min(1.0, score)


class AdaptiveDB:
    def __init__(self, path: str = ".adaptive.db"):
        self.path = path
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE IF NOT EXISTS elements (domain TEXT, ident TEXT, fp TEXT, PRIMARY KEY(domain, ident))")
        con.commit()
        con.close()

    def save(self, url: str, ident: str, node):
        fp = _dumps(fingerprint(node))
        con = sqlite3.connect(self.path)
        try:
            con.execute("INSERT OR REPLACE INTO elements VALUES (?,?,?)", (_domain(url), ident, fp))
            con.commit()
        finally:
            con.close()

    def load(self, url: str, ident: str) -> dict | None:
        con = sqlite3.connect(self.path)
        try:
            row = con.execute("SELECT fp FROM elements WHERE domain=? AND ident=?", (_domain(url), ident)).fetchone()
            return _loads(row[0]) if row else None
        finally:
            con.close()

    def relocate(self, html: str, url: str, ident: str, threshold: float = 0.4):
        """Return (best_node, score) or (None, top_score)."""
        from .selector import Selector

        want = self.load(url, ident)
        if not want:
            return None, 0.0
        sel = Selector(html, url)
        best, best_score = None, 0.0
        try:
            for cand in sel.tree.css("*"):
                try:
                    s = similarity(want, fingerprint(cand))
                except Exception:
                    continue
                if s > best_score:
                    best, best_score = cand, s
                if best_score >= 0.95:
                    break
        except Exception:
            pass
        if best_score >= threshold:
            return best, best_score
        return None, best_score
