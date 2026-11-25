"""Listens to the Telegram channels you follow, using YOUR account in READ-ONLY mode.

This module only reads: it never sends, reacts, joins or leaves anything with your account.
Publishing is done separately by the bot (publisher.py).
"""
from __future__ import annotations

import logging
from datetime import timezone
from pathlib import Path
from typing import Awaitable, Callable, List

from telethon import TelegramClient, events

from .models import Item

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 9 * 1024 * 1024


def make_client(data_dir: Path, api_id: int, api_hash: str) -> TelegramClient:
    return TelegramClient(str(data_dir / "reader"), api_id, api_hash,
                          device_model="COINBABA Reader", flood_sleep_threshold=120)


async def start_listener(client: TelegramClient, channels: List[str],
                         submit: Callable[[Item], Awaitable[None]],
                         alert: Callable[[str], Awaitable[None]]) -> int:
    """Connects, resolves the channel list and registers the handler. Returns how many channels work."""
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("حساب القراءة ما مربوطش. شغّل أولاً:  python3 login.py")

    entities = []
    for ref in channels:
        try:
            entity = await client.get_entity(ref)
            entities.append(entity)
            log.info("Watching channel: %s", getattr(entity, "title", ref))
        except Exception as exc:
            await alert(f"ما قدرتش نلقى القناة {ref}: {exc}")
    if not entities:
        return 0

    @client.on(events.NewMessage(chats=entities, incoming=True))
    async def on_new_message(event):
        msg = event.message
        text = (msg.message or "").strip()
        if not text:
            return  # photo without caption (e.g. extra photos of an album) or service message
        image = None
        try:
            if msg.photo:
                image = await msg.download_media(file=bytes)
            elif msg.document and (msg.document.mime_type or "").startswith("image/") \
                    and (msg.document.size or 0) <= MAX_IMAGE_BYTES:
                image = await msg.download_media(file=bytes)
        except Exception as exc:
            log.info("Could not download image: %s", exc)
        chat = await event.get_chat()
        await submit(Item(
            key=f"tg:{event.chat_id}:{msg.id}",
            source=getattr(chat, "title", None) or str(event.chat_id),
            text=text,
            published_at=msg.date.astimezone(timezone.utc),
            image=image,
        ))

    return len(entities)
