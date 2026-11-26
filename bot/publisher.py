"""Publishes posts to the COINBABA channel through the Telegram Bot API.

Posts are sent as NEW messages (sendPhoto / sendMessage), never forwarded,
so they never show "Forwarded from".
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Union

import httpx

from .reactions import react
from .textutils import to_telegram_html

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


class PublishError(Exception):
    pass


class Publisher:
    def __init__(self, bot_token: str, channel: Union[str, int, None], dry_run: bool = False):
        if not dry_run and not bot_token:
            raise PublishError("BOT_TOKEN is missing in .env")
        if not dry_run and channel in (None, ""):
            raise PublishError("target_channel is missing in config.yaml")
        self.base = f"https://api.telegram.org/bot{bot_token}/"
        self.channel = channel
        self.dry_run = dry_run
        self._client = httpx.AsyncClient(timeout=120)
        self.facebook = None  # optional FacebookPage: posts with `fb_kind` are copied there too

    async def _call(self, method: str, data: dict, files: Optional[dict] = None) -> dict:
        for attempt in range(4):
            try:
                resp = await self._client.post(self.base + method, data=data, files=files)
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 3:
                    raise PublishError(f"{method}: network error {exc}")
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if payload.get("ok"):
                return payload["result"]
            retry_after = (payload.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry_after and attempt < 3:
                log.warning("Telegram asked to wait %ss", retry_after)
                await asyncio.sleep(int(retry_after) + 1)
                continue
            raise PublishError(f"{method}: {payload.get('description', resp.text[:200])}")
        raise PublishError(f"{method}: gave up after retries")

    async def send_post(self, text: str, image: Optional[bytes], silent: bool = False,
                        is_html: bool = False, fb_kind: Optional[str] = None,
                        importance: Optional[int] = None) -> Optional[int]:
        """Sends the post; returns the Telegram message id (None in dry-run).
        silent=True: followers get it without a notification sound.
        is_html=True: `text` is already Telegram HTML (bold, separators...) and is sent as is.
        fb_kind ("news", "analysis", "lesson", "morning"): also copy it to the Facebook Page,
        if Facebook is on and its limits allow it. A Facebook problem never stops the Telegram post."""
        message_id = await self._send_telegram_post(text, image, silent, is_html)
        if self.facebook and fb_kind:
            await self.facebook.maybe_post(text, image, fb_kind, importance, is_html)
        return message_id

    async def _send_telegram_post(self, text: str, image: Optional[bytes], silent: bool,
                                  is_html: bool) -> Optional[int]:
        quiet = {"disable_notification": "true"} if silent else {}
        to_html = (lambda t: t) if is_html else to_telegram_html
        if self.dry_run:
            print("\n" + "=" * 60 + f"\n[DRY RUN] {'with image (' + str(len(image)) + ' bytes)' if image else 'text only'}\n" + text + "\n" + "=" * 60)
            return None

        if image:
            try:
                result = await self._call(
                    "sendPhoto",
                    data={"chat_id": str(self.channel), "caption": to_html(text), "parse_mode": "HTML",
                          **quiet},
                    files={"photo": ("photo.jpg", image)},
                )
                await react(self._client, self.base, self.channel, result.get("message_id"))
                return result.get("message_id")
            except PublishError as exc:
                # A broken image should not lose the news: fall back to text only.
                log.warning("Photo upload failed (%s); sending text only", exc)

        result = await self._call(
            "sendMessage",
            data={
                "chat_id": str(self.channel),
                "text": to_html(text),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                **quiet,
            },
        )
        await react(self._client, self.base, self.channel, result.get("message_id"))
        return result.get("message_id")

    async def send_reply(self, html_text: str, reply_to: Optional[int]) -> Optional[int]:
        """A short HTML message under an earlier post (used for trade-idea results)."""
        if self.dry_run:
            print(f"\n[DRY RUN reply to {reply_to}]\n{html_text}")
            return None
        data = {"chat_id": str(self.channel), "text": html_text, "parse_mode": "HTML",
                "disable_web_page_preview": "true"}
        if reply_to:
            data["reply_parameters"] = '{"message_id": %d, "allow_sending_without_reply": true}' % int(reply_to)
        result = await self._call("sendMessage", data=data)
        return result.get("message_id")

    async def send_promo(self, text: str, buttons: List[dict], image_url: Optional[str] = None) -> Optional[int]:
        """Sends the daily promo post with clickable link buttons under it."""
        import json
        keyboard = {"inline_keyboard": [[{"text": b["text"], "url": b["url"]}] for b in buttons if b.get("url")]}
        if self.dry_run:
            print(f"\n[DRY RUN promo]\n{text}\nbuttons: {[b['text'] for b in buttons]}")
            return None
        markup = json.dumps(keyboard, ensure_ascii=False)
        if image_url:
            result = await self._call("sendPhoto", data={
                "chat_id": str(self.channel), "photo": image_url, "caption": text, "reply_markup": markup})
        else:
            result = await self._call("sendMessage", data={
                "chat_id": str(self.channel), "text": text, "reply_markup": markup,
                "disable_web_page_preview": "true"})
        return result.get("message_id")

    async def send_poll(self, question: str, options: List[str], correct_index: Optional[int] = None,
                        explanation: str = "", chat_id: Union[str, int, None] = None) -> Optional[int]:
        """A channel poll. With `correct_index` it becomes a quiz that reveals the right answer."""
        import json
        question = question.strip()[:300]
        options = [o.strip()[:100] for o in options if o.strip()][:10]
        if self.dry_run:
            print(f"\n[DRY RUN poll] {question}\n  " + "\n  ".join(options) + f"\n  correct={correct_index}")
            return None
        data = {
            "chat_id": str(chat_id or self.channel),
            "question": question,
            "options": json.dumps([{"text": o} for o in options], ensure_ascii=False),
            "is_anonymous": "true",
        }
        if correct_index is not None and 0 <= correct_index < len(options):
            data["type"] = "quiz"
            data["correct_option_id"] = str(correct_index)
            if explanation:
                data["explanation"] = explanation.strip()[:200]
        result = await self._call("sendPoll", data=data)
        await react(self._client, self.base, data["chat_id"], result.get("message_id"))
        return result.get("message_id")

    async def send_private(self, chat_id: int, text: str) -> None:
        if self.dry_run:
            print(f"[DRY RUN alert to owner] {text}")
            return
        await self._call("sendMessage", data={"chat_id": str(chat_id), "text": text})

    async def find_chats(self) -> List[dict]:
        """Lists channels/groups the bot recently saw (used to discover the channel id)."""
        updates = await self._call("getUpdates", data={"allowed_updates": '["my_chat_member","channel_post","message"]'})
        chats: Dict[int, dict] = {}
        for upd in updates:
            for key in ("my_chat_member", "channel_post", "message"):
                chat = (upd.get(key) or {}).get("chat")
                if chat:
                    chats[chat["id"]] = chat
        return list(chats.values())

    async def close(self) -> None:
        await self._client.aclose()
        if self.facebook:
            await self.facebook.close()


class Alerter:
    """Sends short problem reports to the owner in private, at most once per hour per message."""

    def __init__(self, publisher: Publisher, owner_chat_id: Optional[int]):
        self.publisher = publisher
        self.owner_chat_id = owner_chat_id
        self._last: Dict[str, float] = {}

    async def __call__(self, text: str) -> None:
        log.warning("ALERT: %s", text)
        if not self.owner_chat_id:
            return
        now = time.monotonic()
        if now - self._last.get(text, -1e9) < 3600:
            return
        self._last[text] = now
        try:
            await self.publisher.send_private(self.owner_chat_id, "⚠️ COINBABA Bot\n" + text)
        except Exception as exc:  # never crash because an alert failed
            log.error("Could not send alert: %s", exc)
