"""Reads PUBLIC Telegram channels through their public web page (https://t.me/s/<channel>).

No Telegram account, no api_id/api_hash and no login are needed. The trade-off: posts are
picked up by checking every minute or so instead of instantly.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, List

import httpx

from .ingest import ingest, source_entries
from .models import Item
from .rss_source import HEADERS
from .storage import Storage
from .textutils import strip_html

log = logging.getLogger(__name__)

_POST_RE = re.compile(r'data-post="([^"]+)"')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_PHOTO_RE = re.compile(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)")
_TIME_RE = re.compile(r'<time[^>]+datetime="([^"]+)"')
_TITLE_RE = re.compile(r'tgme_channel_info_header_title"><span dir="auto">([^<]*)')


def channel_username(ref: str) -> str:
    """'@Name', 'Name', 'https://t.me/Name' or 't.me/s/Name' -> 'Name'."""
    ref = ref.strip().rstrip("/")
    ref = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.me/(s/)?", "", ref)
    return ref.lstrip("@")


def parse_channel_page(name: str, page: str) -> List[Item]:
    title_match = _TITLE_RE.search(page)
    source = html.unescape(title_match.group(1)) if title_match else name
    items = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        post = _POST_RE.search(block)
        text_match = _TEXT_RE.search(block)
        if not post or not text_match:
            continue  # service message or media without caption
        text = strip_html(text_match.group(1))
        time_match = _TIME_RE.search(block)
        try:
            published = datetime.fromisoformat(time_match.group(1)) if time_match else datetime.now(timezone.utc)
        except ValueError:
            published = datetime.now(timezone.utc)
        photo = _PHOTO_RE.search(block)
        items.append(Item(
            key=f"tg:{post.group(1)}",
            source=source,
            text=text,
            published_at=published.astimezone(timezone.utc),
            image_url=html.unescape(photo.group(1)) if photo else None,
        ))
    return items


async def fetch_channel(client: httpx.AsyncClient, name: str) -> List[Item]:
    resp = await client.get(f"https://t.me/s/{name}", headers=HEADERS, follow_redirects=True)
    resp.raise_for_status()
    if "tgme_channel_info" not in resp.text:
        raise ValueError("القناة ما عندهاش صفحة عامة (يمكن خاصة أو السمية غالطة)")
    return parse_channel_page(name, resp.text)


async def web_channels_loop(channels: list, storage: Storage, submit: Callable[[Item], Awaitable[None]],
                            interval: int, alert: Callable[[str], Awaitable[None]]) -> None:
    entries = [(channel_username(ref), rules) for ref, rules in source_entries(channels)]
    failures = {n: 0 for n, _ in entries}
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            for name, rules in entries:
                try:
                    items = await fetch_channel(client, name)
                except Exception as exc:
                    failures[name] += 1
                    log.warning("Channel %s failed: %s", name, exc)
                    if failures[name] == 5:
                        await alert(f"القناة @{name} ما قدرتش نقراها (5 محاولات): {exc}")
                    continue
                failures[name] = 0
                await ingest(storage, f"seeded:tg:{name}", f"Channel @{name}", items, submit, rules)
                await asyncio.sleep(2)  # be gentle with Telegram
            await asyncio.sleep(interval)
