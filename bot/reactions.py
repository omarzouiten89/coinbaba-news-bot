"""One reaction from the bot on each post it publishes, with a different emoji each time.

Telegram allows a bot exactly one reaction per message. The emoji is picked among the reactions
the channel has enabled (read once per channel), so it is always accepted.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Dict, List

import httpx

log = logging.getLogger(__name__)

DEFAULT = ["❤", "🔥", "👍", "👏", "💯", "⚡", "🤩", "🏆", "🎉", "🙏", "😍"]
_allowed: Dict[str, List[str]] = {}
_last: Dict[str, str] = {}


async def _channel_emojis(client: httpx.AsyncClient, api: str, chat_id: str) -> List[str]:
    if chat_id not in _allowed:
        try:
            chat = (await client.get(api + "getChat", params={"chat_id": chat_id})).json().get("result", {})
            avail = chat.get("available_reactions")
            emojis = [r["emoji"] for r in avail or [] if r.get("type") == "emoji" and r.get("emoji")]
            _allowed[chat_id] = emojis if avail is not None and emojis else DEFAULT
        except (httpx.HTTPError, ValueError):
            return DEFAULT
    return _allowed[chat_id]


async def react(client: httpx.AsyncClient, api: str, chat_id, message_id) -> None:
    if not message_id:
        return
    chat_id = str(chat_id)
    emojis = await _channel_emojis(client, api, chat_id)
    choices = [e for e in emojis if e != _last.get(chat_id)] or emojis   # never the same twice in a row
    emoji = random.choice(choices)
    try:
        r = (await client.post(api + "setMessageReaction", data={
            "chat_id": chat_id, "message_id": str(message_id),
            "reaction": json.dumps([{"type": "emoji", "emoji": emoji}], ensure_ascii=False),
        })).json()
    except httpx.HTTPError as exc:
        log.info("Reaction failed: %s", exc)
        return
    if r.get("ok"):
        _last[chat_id] = emoji
    else:
        log.info("Reaction %s not added: %s", emoji, r.get("description"))
        _allowed.pop(chat_id, None)  # re-read the channel settings next time
