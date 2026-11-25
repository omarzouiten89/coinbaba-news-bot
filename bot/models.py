from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


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

    def age_hours(self) -> float:
        return (datetime.now(timezone.utc) - self.published_at).total_seconds() / 3600
