"""Structured output schemas for elitescraper."""
from datetime import datetime, timezone
from pydantic import BaseModel, Field

try:
    import orjson as _orjson

    def _dumps(obj: dict) -> str:
        return _orjson.dumps(obj).decode("utf-8")
except ImportError:
    import json as _json

    def _dumps(obj: dict) -> str:
        return _json.dumps(obj, ensure_ascii=False)


class Link(BaseModel):
    text: str = ""
    href: str = ""


class ScrapedPage(BaseModel):
    url: str
    final_url: str = ""
    status: int = 0
    title: str = ""
    meta_description: str = ""
    h1: list[str] = Field(default_factory=list)
    h2: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str = ""

    def flatten(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "title": self.title,
            "meta_description": self.meta_description,
            "h1": "|".join(self.h1),
            "h2": "|".join(self.h2),
            "links": "|".join(l.href for l in self.links),
            "link_texts": "|".join(l.text for l in self.links),
            "fetched_at": self.fetched_at,
            "error": self.error,
        }

    def to_jsonl(self) -> str:
        return _dumps(self.model_dump())
