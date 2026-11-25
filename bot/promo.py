"""Posts the COINBABA course promo (text + link buttons) once a day at a fixed time."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

log = logging.getLogger(__name__)


def is_due(now: datetime, time_hhmm: str, last_sent_date: str) -> bool:
    hour, minute = (int(x) for x in time_hhmm.split(":"))
    return now.strftime("%Y-%m-%d") != last_sent_date and (now.hour, now.minute) >= (hour, minute)


async def promo_loop(promo: dict, timezone: str, storage, publisher, alert) -> None:
    buttons = [b for b in promo.get("buttons") or [] if b.get("url")]
    if not buttons or not promo.get("text"):
        log.warning("Daily promo enabled but text or button links are missing in config.yaml; skipping")
        return
    tz = ZoneInfo(timezone)
    while True:
        now = datetime.now(tz)
        if is_due(now, str(promo.get("time", "20:00")), storage.get("promo_date") or ""):
            try:
                await publisher.send_promo(promo["text"].strip(), buttons, promo.get("image_url") or None)
                storage.set("promo_date", now.strftime("%Y-%m-%d"))
                log.info("Daily promo posted")
            except Exception as exc:
                await alert(f"ما قدرتش ننشر الإعلان اليومي ديال الدورات: {exc}")
                await asyncio.sleep(600)
        await asyncio.sleep(60)
