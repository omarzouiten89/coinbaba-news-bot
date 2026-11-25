"""Reads news websites through their RSS feeds (Cointelegraph, CoinDesk, ...)."""
from __future__ import annotations

import asyncio
import calendar
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Awaitable, List, Optional

import feedparser
import httpx

from .ingest import ingest
from .models import Item, SourceRules
from .storage import Storage
from .textutils import strip_html

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; COINBABA-NewsBot/1.0)"}
MAX_IMAGE_BYTES = 9 * 1024 * 1024  # Telegram accepts photos up to 10 MB


def _entry_time(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _entry_image(entry) -> Optional[str]:
    for media in entry.get("media_content") or []:
        url = media.get("url")
        if url and (media.get("medium") == "image" or "image" in (media.get("type") or "")):
            return url
    for thumb in entry.get("media_thumbnail") or []:
        if thumb.get("url"):
            return thumb["url"]
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and "image" in (link.get("type") or ""):
            return link.get("href")
    match = re.search(r'<img[^>]+src="([^"]+)"', entry.get("summary") or "")
    return match.group(1) if match else None


_EMPTY_TAGS_RE = re.compile(rb"<(dc:description|content:encoded)\s*/>|<(dc:description|content:encoded)>\s*</\2>")


def parse_feed(name: str, content: bytes) -> List[Item]:
    # Some feeds (CoinDesk) include empty <dc:description/> tags that make feedparser
    # drop the real <description>; removing them keeps the article summary.
    content = _EMPTY_TAGS_RE.sub(b"", content)
    parsed = feedparser.parse(content)
    items = []
    for entry in parsed.entries:
        uid = entry.get("id") or entry.get("link")
        title = strip_html(entry.get("title") or "")
        if not uid or not title:
            continue
        summary = strip_html(entry.get("summary") or "")
        text = title if not summary or summary == title else f"{title}\n\n{summary}"
        items.append(Item(
            key=f"rss:{name}:{uid}",
            source=name,
            text=text,
            published_at=_entry_time(entry),
            link=entry.get("link"),
            image_url=_entry_image(entry),
        ))
    items.sort(key=lambda i: i.published_at)  # oldest first, so posts come out in order
    return items


async def fetch_feed(client: httpx.AsyncClient, feed: dict) -> List[Item]:
    resp = await client.get(feed["url"], headers=HEADERS, follow_redirects=True)
    resp.raise_for_status()
    return parse_feed(feed["name"], resp.content)


async def find_og_image(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Finds the main image of an article page (og:image) when the feed has none."""
    try:
        resp = await client.get(url, headers=HEADERS, follow_redirects=True)
        match = re.search(
            r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)', resp.text
        ) or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image', resp.text)
        return match.group(1) if match else None
    except httpx.HTTPError:
        return None


async def download_image(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    try:
        resp = await client.get(url, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("Image download failed %s: %s", url, exc)
        return None
    if not resp.headers.get("content-type", "").startswith("image/") or len(resp.content) > MAX_IMAGE_BYTES:
        return None
    return resp.content


async def rss_loop(feeds: List[dict], storage: Storage, submit: Callable[[Item], Awaitable[None]],
                   interval: int, alert: Callable[[str], Awaitable[None]]) -> None:
    """Checks every feed every `interval` seconds and submits new items."""
    failures = {f["name"]: 0 for f in feeds}
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            for feed in feeds:
                name = feed["name"]
                try:
                    items = await fetch_feed(client, feed)
                except Exception as exc:
                    failures[name] += 1
                    log.warning("Feed %s failed: %s", name, exc)
                    if failures[name] == 5:
                        await alert(f"المصدر {name} ما كيجاوبش (5 محاولات متتالية): {exc}")
                    continue
                failures[name] = 0
                await ingest(storage, f"seeded:{name}", f"Feed {name}", items, submit,
                             SourceRules.from_config(feed))
            await asyncio.sleep(interval)
