"""Dev cache: record raw HTML on first run, replay on later runs.

Niche win: iterate parse() logic without re-hitting target servers.
Usage: --dev-cache .devcache
"""
import hashlib
from pathlib import Path


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


class DevCache:
    def __init__(self, root: str | None):
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def __bool__(self):
        return self.root is not None

    def get(self, url: str) -> tuple[int, str, str] | None:
        if not self.root:
            return None
        p = self.root / (_key(url) + ".json")
        if not p.exists():
            return None
        try:
            import json as _json

            d = _json.loads(p.read_text(encoding="utf-8"))
            return int(d.get("status", 200)), d.get("html", ""), d.get("final_url", url)
        except Exception:
            return None

    def put(self, url: str, status: int, html: str, final_url: str):
        if not self.root:
            return
        try:
            import json as _json

            (self.root / (_key(url) + ".json")).write_text(
                _json.dumps({"url": url, "status": status, "final_url": final_url, "html": html[:3_000_000]}),
                encoding="utf-8",
            )
        except Exception:
            pass
