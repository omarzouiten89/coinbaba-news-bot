"""The heart of the bot: new item -> checks -> AI rewrite -> publish."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Awaitable, Callable, List, Optional

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .models import Item
from .publisher import CAPTION_LIMIT, MESSAGE_LIMIT
from .rewriter import RewriterError
from .rss_source import download_image, find_og_image
from .storage import Storage
from .textutils import fingerprint, fit, remove_links_and_mentions

log = logging.getLogger(__name__)

MIN_SOURCE_CHARS = 25


class Pipeline:
    def __init__(self, settings, storage: Storage, rewriter, publisher,
                 alert: Callable[[str], Awaitable[None]]):
        self.settings = settings
        self.storage = storage
        self.rewriter = rewriter
        self.publisher = publisher
        self.alert = alert
        self.queue: "asyncio.Queue[Item]" = asyncio.Queue()
        self.tz = ZoneInfo(settings.timezone)
        self._http = httpx.AsyncClient(timeout=30)
        self.published = 0
        self._retries: dict = {}
        self._channel_cache: tuple = (-1e9, [])

    async def published_headlines(self) -> List[str]:
        """Headlines already in the channel: from our own memory AND from the channel's public page,
        so the Mac bot and the GitHub bot never repeat what the other one posted (hybrid mode)."""
        own = self.storage.recent_headlines()
        public = (self.settings.target_channel_public or "").strip().lstrip("@")
        if not public:
            return own
        now = time.monotonic()
        if now - self._channel_cache[0] > 120:
            try:
                from .web_channel_source import fetch_channel
                items = await fetch_channel(self._http, public)
                self._channel_cache = (now, [i.text.splitlines()[0][:200] for i in items[-25:]])
            except Exception as exc:
                log.info("Could not read own channel page: %s", exc)
        merged = list(dict.fromkeys(self._channel_cache[1][::-1] + own))
        return merged[:45]

    def local_date(self) -> str:
        return datetime.now(self.tz).strftime("%Y-%m-%d")

    async def submit(self, item: Item) -> None:
        """Called by every source. Cheap checks only; the slow work happens in the worker."""
        if not self.storage.mark_seen(item.key):
            return  # already handled
        if len(remove_links_and_mentions(item.text)) < MIN_SOURCE_CHARS:
            log.info("Skip (too short) %s", item.key)
            return
        if not self.storage.mark_seen("fp:" + fingerprint(item.text)):
            log.info("Skip (same text already seen) %s", item.key)
            return
        if item.age_hours() > self.settings.max_item_age_hours:
            log.info("Skip (too old) %s", item.key)
            return
        await self.queue.put(item)

    async def drain_once(self) -> None:
        """GitHub mode: handle everything queued, then return. A news item that failed because
        Gemini was busy is 'forgotten' so the next run (10 minutes later) tries it again."""
        while not self.queue.empty():
            item = self.queue.get_nowait()
            try:
                await self.handle(item)
            except RewriterError as exc:
                log.warning("Gemini busy, %s will be retried next run (%s)", item.key, str(exc)[:120])
                self.storage.unmark_seen(item.key)
                self.storage.unmark_seen("fp:" + fingerprint(item.text))
            except Exception as exc:
                log.exception("Failed on %s", item.key)
                await self.alert(f"خطأ أثناء معالجة خبر من {item.source}: {exc}")

    async def worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                await self.handle(item)
            except RewriterError as exc:
                # Gemini busy: put the news back in the queue and try again later (max 3 times).
                tries = self._retries.get(item.key, 0) + 1
                self._retries[item.key] = tries
                if tries <= 3 and "HTTP 40" not in str(exc):
                    log.warning("Gemini busy, will retry %s in 5 min (try %d/3)", item.key, tries)
                    asyncio.get_running_loop().call_later(300, self.queue.put_nowait, item)
                else:
                    await self.alert(f"مشكل في الذكاء الاصطناعي (Gemini): {str(exc)[:300]}")
            except Exception as exc:
                log.exception("Failed on %s", item.key)
                await self.alert(f"خطأ أثناء معالجة خبر من {item.source}: {exc}")
            finally:
                self.queue.task_done()

    async def handle(self, item: Item) -> Optional[int]:
        today = self.local_date()
        if self.storage.count_posts_on(today) >= self.settings.daily_limit:
            log.info("Daily limit reached, skipping %s", item.key)
            if self.storage.get("limit_alert") != today:
                self.storage.set("limit_alert", today)
                await self.alert(f"وصلنا للحد اليومي ({self.settings.daily_limit} منشور). النشر غادي يرجع غدا.")
            return None
        if item.age_hours() > self.settings.max_item_age_hours:
            return None
        rules = item.rules
        if rules.keywords and not any(k in item.text.lower() for k in rules.keywords):
            log.info("Skip (no keyword match) %s", item.key)
            return None
        if rules.daily_max is not None and self.storage.count_posts_by_source(today, item.source) >= rules.daily_max:
            log.info("Skip (daily max %d reached for %s) %s", rules.daily_max, item.source, item.key)
            return None

        # Image: Telegram items already carry bytes; for websites we download it.
        if item.image is None and not item.image_url and item.link:
            item.image_url = await find_og_image(self._http, item.link)
        if item.image is None and item.image_url:
            item.image = await download_image(self._http, item.image_url)
        has_image = item.image is not None

        limit = CAPTION_LIMIT if has_image else MESSAGE_LIMIT
        target_chars = 950 if has_image else 1800
        decision = await self.rewriter.process(
            item.text, has_image, await self.published_headlines(), target_chars, kind=rules.kind
        )
        if not decision.publish or decision.duplicate:
            log.info("Not published (%s): %s", decision.reason or "rejected", item.key)
            return None
        if decision.importance < rules.min_importance:
            log.info("Not published (importance %d < %d): %s", decision.importance, rules.min_importance, item.key)
            return None

        text = fit(remove_links_and_mentions(decision.post), limit)
        if len(text) < 30:
            log.info("Rewrite too short, skipping %s", item.key)
            return None

        message_id = await self.publisher.send_post(text, item.image)
        headline = text.splitlines()[0][:200]
        self.storage.add_post(today, item.source, item.key, decision.category, headline, text, message_id)
        self.published += 1
        log.info("Published (%s) from %s: %s", decision.category, item.source, headline)
        return message_id or 0

    async def close(self) -> None:
        await self._http.aclose()
