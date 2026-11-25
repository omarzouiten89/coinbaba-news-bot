"""☀️ Morning brief (08:00): real prices, the night's top headlines and today's key economic events.

Prices come from free public sources (CoinGecko, Yahoo Finance) and the calendar from ForexFactory's
public JSON. Numbers are never written by the AI: they are inserted as received.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import List, Optional

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .card import render_card
from .lessons import due_slot
from .publisher import CAPTION_LIMIT
from .textutils import fit

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; COINBABA-NewsBot/1.0)"}

AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر",
             "أكتوبر", "نوفمبر", "ديسمبر"]

MARKETS = [  # (label, yahoo symbol, decimals)
    ("🥇 الذهب", "GC=F", 0),
    ("🥈 الفضة", "SI=F", 2),
    ("🇺🇸 العقود الآجلة لـ S&P 500", "ES=F", 0),
    ("💵 مؤشر الدولار", "DX-Y.NYB", 2),
    ("🛢 النفط (WTI)", "CL=F", 2),
]


def arabic_date(d: datetime) -> str:
    return f"{AR_DAYS[d.weekday()]} {d.day} {AR_MONTHS[d.month - 1]}"


def _fmt(price: float, decimals: int) -> str:
    return f"{price:,.{decimals}f}"


def _change(pct: Optional[float]) -> str:
    if pct is None:
        return ""
    arrow = "🟢" if pct >= 0 else "🔴"
    return f" {arrow} {pct:+.1f}%"


async def crypto_lines(client: httpx.AsyncClient) -> List[str]:
    r = await client.get("https://api.coingecko.com/api/v3/simple/price", headers=HEADERS, params={
        "ids": "bitcoin,ethereum", "vs_currencies": "usd", "include_24hr_change": "true"})
    r.raise_for_status()
    d = r.json()
    out = []
    for key, label in (("bitcoin", "₿ البيتكوين"), ("ethereum", "Ξ الإيثيريوم")):
        if key in d:
            out.append(f"{label}: ${_fmt(d[key]['usd'], 0)}{_change(d[key].get('usd_24h_change'))}")
    return out


async def market_line(client: httpx.AsyncClient, label: str, symbol: str, decimals: int) -> Optional[str]:
    r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", headers=HEADERS,
                         params={"range": "5d", "interval": "1d"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    price = res["meta"]["regularMarketPrice"]
    closes = [c for c in res["indicators"]["quote"][0].get("close") or [] if c is not None]
    prev = closes[-2] if len(closes) >= 2 else None
    pct = (price - prev) / prev * 100 if prev else None
    unit = "$" if symbol not in ("DX-Y.NYB", "ES=F") else ""
    return f"{label}: {unit}{_fmt(price, decimals)}{_change(pct)}"


async def todays_events(client: httpx.AsyncClient, tz: ZoneInfo, today) -> List[tuple]:
    r = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=HEADERS)
    r.raise_for_status()
    events = []
    for e in r.json():
        if e.get("impact") != "High" or e.get("country") not in ("USD", "EUR", "CNY"):
            continue
        when = datetime.fromisoformat(e["date"]).astimezone(tz)
        if when.date() == today:
            events.append((when, e.get("country"), e.get("title", ""), e.get("forecast") or "",
                           e.get("previous") or ""))
    return sorted(events)[:5]


async def build_brief(settings, rewriter, pipeline) -> str:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    parts = [f"☀️ ملخص الصباح | {arabic_date(now)}", ""]
    async with httpx.AsyncClient(timeout=20) as client:
        prices: List[str] = []
        try:
            prices += await crypto_lines(client)
        except Exception as exc:
            log.info("Crypto prices failed: %s", exc)
        for label, symbol, dec in MARKETS:
            try:
                line = await market_line(client, label, symbol, dec)
                if line:
                    prices.append(line)
            except Exception as exc:
                log.info("Price %s failed: %s", symbol, exc)
        if prices:
            parts += ["📊 الأسواق الآن:", *prices, ""]

        try:
            events = await todays_events(client, tz, now.date())
        except Exception as exc:
            log.info("Calendar failed: %s", exc)
            events = None

    headlines = [h for h in await pipeline.published_headlines()
                 if not h.startswith(("📚", "📊", "☀️"))][:3]
    if headlines:
        parts += ["📰 أبرز ما حدث:", *[f"• {h.lstrip('🚨📰📈📉🇺🇸 ').strip()}" for h in headlines], ""]

    if events is not None:
        if events:
            titles = [t for _, _, t, _, _ in events]
            arabic = await translate_titles(rewriter, titles)
            parts.append("🗓 أحداث اليوم المهمة (بتوقيت الإمارات):")
            for (when, cur, _, fc, prev), name in zip(events, arabic):
                extra = f" | المتوقع {fc}" if fc else ""
                parts.append(f"⏰ {when:%H:%M} {cur}: {name}{extra}")
        else:
            parts.append("🗓 لا توجد أحداث اقتصادية كبرى اليوم.")
        parts.append("")

    parts.append("#ملخص_الصباح #بيتكوين #الذهب #الأسواق_المالية")
    return "\n".join(parts).strip()


async def translate_titles(rewriter, titles: List[str]) -> List[str]:
    """Event names (e.g. 'CPI m/m') to short Arabic; falls back to the English names."""
    try:
        body = {
            "contents": [{"role": "user", "parts": [{"text":
                "Translate these economic calendar event names to short Modern Standard Arabic financial terms. "
                "Reply with a JSON array of strings in the same order.\n" + json.dumps(titles)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }

        def parse(text: str) -> List[str]:
            data = json.loads(text)
            if not isinstance(data, list) or len(data) != len(titles):
                raise ValueError("bad translation")
            return [str(x) for x in data]
        return await rewriter._generate(body, parse)
    except Exception:
        return titles


async def post_due_brief(settings, storage, rewriter, publisher, pipeline) -> bool:
    cfg = settings.morning
    if not cfg.get("enabled"):
        return False
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    times = [str(cfg.get("time") or "08:00")]
    done = {k[len("brief:"):] for k in storage.keys_with_prefix("brief:")}
    if due_slot(now, times, done) is None:
        return False
    key = f"brief:{now:%Y-%m-%d}:0"
    # Hybrid safety: the other bot may have posted it already.
    if any("ملخص الصباح" in h and arabic_date(now) in h for h in await pipeline.published_headlines()):
        storage.set(key, "posted-by-other")
        return False
    text = fit(await build_brief(settings, rewriter, pipeline), CAPTION_LIMIT)
    card = await asyncio.to_thread(render_card, f"ملخص الصباح {arabic_date(now)}", "صباح الخير",
                                   f"@{settings.target_channel_public}" if settings.target_channel_public else "")
    await publisher.send_post(text, card)
    storage.set(key, "1")
    storage.add_post(f"{now:%Y-%m-%d}", "morning", key, "lesson", text.splitlines()[0], text, None)
    log.info("Morning brief posted")
    return True


async def morning_loop(settings, storage, rewriter, publisher, pipeline, alert) -> None:
    while True:
        try:
            await post_due_brief(settings, storage, rewriter, publisher, pipeline)
        except Exception as exc:
            log.warning("Morning brief failed: %s", exc)
            await alert(f"ما قدرتش ننشر ملخص الصباح: {str(exc)[:200]}")
            await asyncio.sleep(600)
        await asyncio.sleep(60)
