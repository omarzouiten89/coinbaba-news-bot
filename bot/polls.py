"""Daily opinion poll to make followers participate: Bitcoin direction, gold, market direction,
or the most important recent economic event (taken from the channel's own latest news)."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .lessons import due_slot
from .rss_source import HEADERS

log = logging.getLogger(__name__)

# The theme changes every day in this order.
THEMES = [
    ("bitcoin", "Followers' expectation for Bitcoin's direction this week: up, down or sideways"),
    ("gold", "Followers' expectation for the price of gold in the coming days"),
    ("event", "The most important economic event in the recent channel headlines: what do followers think "
              "its impact on the markets will be"),
    ("market", "Vote on the general market direction this week (US stocks, dollar or crypto): bullish, "
               "bearish or uncertain"),
]


def theme_for_day(day, slot: int = 0, slots_per_day: int = 1) -> tuple:
    return THEMES[(day.toordinal() * slots_per_day + slot) % len(THEMES)]


async def poll_already_in_channel(public_name: str, since_utc: datetime) -> bool:
    """Hybrid safety: did the other bot already post a poll in the channel since `since_utc`?"""
    if not public_name:
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            page = (await client.get(f"https://t.me/s/{public_name}", headers=HEADERS, follow_redirects=True)).text
    except httpx.HTTPError:
        return False
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        if "tgme_widget_message_poll" not in block:
            continue
        m = re.search(r'<time[^>]+datetime="([^"]+)"', block)
        if m and datetime.fromisoformat(m.group(1)).astimezone(timezone.utc) >= since_utc:
            return True
    return False


async def post_due_poll(settings, storage, rewriter, publisher, pipeline=None) -> bool:
    cfg = settings.polls
    if not cfg.get("enabled"):
        return False
    times = [str(t) for t in cfg.get("times") or ["19:00"]]
    now = datetime.now(ZoneInfo(settings.timezone))
    done = {k[len("poll:"):] for k in storage.keys_with_prefix("poll:")}
    slot = due_slot(now, times, done)
    if slot is None:
        return False
    key = f"poll:{now:%Y-%m-%d}:{slot}"
    h, m = (int(x) for x in times[slot].split(":"))
    slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0).astimezone(timezone.utc)
    if await poll_already_in_channel(settings.target_channel_public.lstrip("@"), slot_start - timedelta(minutes=1)):
        storage.set(key, "posted-by-other")
        return False
    kind, topic = theme_for_day(now.date(), slot, len(times))
    if kind == "event" and pipeline is not None:
        headlines = [h for h in await pipeline.published_headlines() if not h.startswith("📚")][:15]
        if headlines:
            topic += "\nRecent channel headlines:\n" + "\n".join(f"- {h}" for h in headlines)
        else:
            kind, topic = THEMES[0]
    poll = await rewriter.write_poll("opinion", topic)
    await publisher.send_poll(poll.question, poll.options)
    storage.set(key, "1")
    log.info("Poll posted (%s): %s", kind, poll.question)
    return True


async def polls_loop(settings, storage, rewriter, publisher, alert, pipeline=None) -> None:
    while True:
        try:
            await post_due_poll(settings, storage, rewriter, publisher, pipeline)
        except Exception as exc:
            log.warning("Poll failed: %s", exc)
            await alert(f"ما قدرتش ننشر الاستطلاع اليومي: {str(exc)[:200]}")
            await asyncio.sleep(600)
        await asyncio.sleep(60)
