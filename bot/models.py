from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class SourceRules:
    """Per-source options from config.yaml."""
    kind: str = "news"                 # "news" or "analysis"
    daily_max: Optional[int] = None    # at most N posts a day from this source
    min_importance: int = 0            # 1-10 score the AI must give (e.g. 8 = only important news)
    keywords: List[str] = field(default_factory=list)  # only items mentioning one of these words

    @classmethod
    def from_config(cls, cfg: dict) -> "SourceRules":
        return cls(
            kind=str(cfg.get("kind") or "news"),
            daily_max=int(cfg["daily_max"]) if cfg.get("daily_max") is not None else None,
            min_importance=int(cfg.get("min_importance") or 0),
            keywords=[str(k).lower() for k in cfg.get("keywords") or []],
        )


@dataclass
class Item:
    """One piece of news coming from an RSS feed or a Telegram channel."""

    key: str                      # unique id, e.g. "tg:-100123:456" or "rss:CoinDesk:<guid>"
    source: str                   # human name of the source (for logs only, never published)
    text: str                     # original text
    published_at: datetime
    link: Optional[str] = None    # article link (used only to find the image)
    image: Optional[bytes] = None
    image_url: Optional[str] = None
    rules: SourceRules = field(default_factory=SourceRules)

    def age_hours(self) -> float:
        return (datetime.now(timezone.utc) - self.published_at).total_seconds() / 3600
