"""JS-needed detection: skip the browser when static HTML already has the data.

Checks one static fetch for framework markers before paying browser cost.
"""
import json as _json
import re as _re

NEXT_RE = _re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', _re.S)
NUXT_RE = _re.compile(r"window\.__NUXT__", _re.S)
STATE_RE = _re.compile(r"window\.__(?:INITIAL_|PRELOADED_|APOLLO_)?STATE__", _re.S)
LDJSON_RE = _re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', _re.S | _re.I)
API_RE = _re.compile(r'["\']((?:/api/|/_next/data/)[^"\']+?\.json[^"\']*)["\']')
SHELL_RE = _re.compile(r'<div id="(root|app|__next)"[^>]*>\s*(<!--.*?-->)?\s*</div>', _re.S | _re.I)


def analyze(html: str) -> dict:
    """Return {decision, reasons, api_hints, next_data}.

    decision: static | api-direct | browser
    """
    html = html or ""
    reasons: list[str] = []
    # Cloudflare / challenge shell always needs browser (or hybrid solve)
    low = html[:8000].lower()
    if "just a moment" in low or "challenges.cloudflare.com" in html or "cf-turnstile" in html or "cType: 'managed'" in html:
        return {"decision": "browser", "reasons": ["cloudflare-challenge"], "api_hints": [], "next_data": None}
    next_data = None
    m = NEXT_RE.search(html)
    if m:
        try:
            next_data = _json.loads(m.group(1))
            reasons.append("__NEXT_DATA__")
        except Exception:
            pass
    ld_blocks = []
    for mm in LDJSON_RE.finditer(html):
        try:
            ld_blocks.append(_json.loads(mm.group(1)))
        except Exception:
            pass
    if ld_blocks:
        reasons.append("json-ld")
    api_hints = list(dict.fromkeys(API_RE.findall(html)[:20]))
    if api_hints:
        reasons.append("api-hints")
    if NUXT_RE.search(html) or STATE_RE.search(html):
        reasons.append("spa-state")
    # shell with no data -> browser
    if SHELL_RE.search(html) and not (next_data or ld_blocks or api_hints):
        return {"decision": "browser", "reasons": ["empty-shell"], "api_hints": api_hints, "next_data": None}
    if next_data or ld_blocks or api_hints:
        return {"decision": "api-direct", "reasons": reasons, "api_hints": api_hints, "next_data": next_data}
    return {"decision": "static", "reasons": ["plain-html"], "api_hints": [], "next_data": None}
