"""☀️ Morning brief (08:00): real prices, the night's top headlines and today's key economic events.

Prices come from free public sources (CoinGecko, Yahoo Finance) and the calendar from ForexFactory's
public JSON. Numbers are never written by the AI: they are inserted as received.
The post is built as Telegram HTML (bold titles, separators, numbered headlines).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .card import render_card
from .lessons import due_slot

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; COINBABA-NewsBot/1.0)"}

AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر",
             "أكتوبر", "نوفمبر", "ديسمبر"]

MARKETS = [  # (emoji, Arabic name, yahoo symbol, decimals, dollar sign)
    ("🥇", "الذهب", "GC=F", 0, True),
    ("🥈", "الفضة", "SI=F", 2, True),
    ("🇺🇸", "S&P 500 (عقود آجلة)", "ES=F", 0, False),
    ("💵", "مؤشر الدولار", "DX-Y.NYB", 2, False),
    ("🛢", "النفط WTI", "CL=F", 2, True),
]

LTR = "⁦{}⁩"   # keeps "$58,543" and "-2.9%" in the right order inside Arabic lines
SEP = "━━━━━━━━━━━━━━"
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "CNY": "🇨🇳"}


def arabic_date(d: datetime) -> str:
    return f"{AR_DAYS[d.weekday()]} {d.day} {AR_MONTHS[d.month - 1]}"


def day_look(d: datetime) -> dict:
    """Card title, theme and post header of the day: Friday and Saturday get their own look."""
    if d.weekday() == 4:
        return {"title": "جمعة طيبة", "theme": "friday", "header": "☀️ <b>صباح الخير وجمعة طيبة</b> 🌙"}
    if d.weekday() == 5:
        return {"title": "نهاية أسبوع موفقة", "theme": "weekend",
                "header": "☀️ <b>صباح الخير ونهاية أسبوع موفقة</b> ☕"}
    return {"title": "ملخص الصباح", "theme": "", "header": "☀️ <b>ملخص الصباح</b>"}


def _fmt(price: float, decimals: int) -> str:
    return f"{price:,.{decimals}f}"


def price_line(emoji: str, name: str, value: str, pct: Optional[float]) -> str:
    """'₿ <b>البيتكوين</b>:  $58,543   🔴 -2.9%' with the numbers kept left-to-right."""
    line = f"{emoji} <b>{html.escape(name)}</b>:  {LTR.format(html.escape(value))}"
    if pct is not None:
        line += f"  {LTR.format(f'({pct:+.1f}%)')}"
    return line


async def crypto_lines(client: httpx.AsyncClient) -> List[str]:
    r = await client.get("https://api.coingecko.com/api/v3/simple/price", headers=HEADERS, params={
        "ids": "bitcoin,ethereum", "vs_currencies": "usd", "include_24hr_change": "true"})
    r.raise_for_status()
    d = r.json()
    out = []
    for key, emoji, name in (("bitcoin", "₿", "البيتكوين"), ("ethereum", "Ξ", "الإيثيريوم")):
        if key in d:
            out.append(price_line(emoji, name, f"${_fmt(d[key]['usd'], 0)}", d[key].get("usd_24h_change")))
    return out


async def market_line(client: httpx.AsyncClient, emoji: str, name: str, symbol: str, decimals: int,
                      dollar: bool) -> Optional[str]:
    r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", headers=HEADERS,
                         params={"range": "5d", "interval": "1d"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    price = res["meta"]["regularMarketPrice"]
    closes = [c for c in res["indicators"]["quote"][0].get("close") or [] if c is not None]
    prev = closes[-2] if len(closes) >= 2 else None
    pct = (price - prev) / prev * 100 if prev else None
    return price_line(emoji, name, f"{'$' if dollar else ''}{_fmt(price, decimals)}", pct)


async def todays_events(client: httpx.AsyncClient, tz: ZoneInfo, today) -> List[tuple]:
    r = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=HEADERS)
    r.raise_for_status()
    events = []
    for e in r.json():
        if e.get("impact") != "High" or e.get("country") not in FLAGS:
            continue
        when = datetime.fromisoformat(e["date"]).astimezone(tz)
        if when.date() == today:
            events.append((when, e.get("country"), e.get("title", ""), e.get("forecast") or "",
                           e.get("previous") or ""))
    return sorted(events)[:5]


def _clean_headline(h: str) -> str:
    """Drops the leading emoji/flag of a channel headline."""
    return re.sub(r"^[^\w؀-ۿ\"«(]+", "", h).strip()


async def build_brief(settings, rewriter, pipeline) -> str:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    look = day_look(now)
    parts = [f"{look['header']}  |  {html.escape(arabic_date(now))}", SEP]
    async with httpx.AsyncClient(timeout=20) as client:
        prices: List[str] = []
        try:
            prices += await crypto_lines(client)
        except Exception as exc:
            log.info("Crypto prices failed: %s", exc)
        for emoji, name, symbol, dec, dollar in MARKETS:
            try:
                line = await market_line(client, emoji, name, symbol, dec, dollar)
                if line:
                    prices.append(line)
            except Exception as exc:
                log.info("Price %s failed: %s", symbol, exc)
        if prices:
            parts += ["📊 <b>الأسواق الآن</b>", "", *prices]
            if now.weekday() >= 5:
                parts += ["", "ℹ️ الأسواق التقليدية مغلقة اليوم، والعملات الرقمية تتداول على مدار الساعة."]
            parts.append(SEP)

        try:
            events = await todays_events(client, tz, now.date())
        except Exception as exc:
            log.info("Calendar failed: %s", exc)
            events = None

    headlines = [h for h in await pipeline.published_headlines()
                 if not h.startswith(("📚", "📊", "☀️"))][:3]
    if headlines:
        parts += ["📰 <b>أبرز الأخبار</b>", ""]
        parts += [f"{NUM_EMOJI[i]} {html.escape(_clean_headline(h))}" for i, h in enumerate(headlines)]
        parts.append(SEP)

    if events is not None:
        parts += ["🗓 <b>أجندة اليوم</b> (بتوقيت الإمارات)", ""]
        if events:
            arabic = await translate_titles(rewriter, [t for _, _, t, _, _ in events])
            for (when, cur, _, fc, _), name in zip(events, arabic):
                line = f"⏰ <b>{LTR.format(f'{when:%H:%M}')}</b>  {FLAGS.get(cur, '')} {html.escape(name)}"
                if fc:
                    line += f"\n      ↳ المتوقع: {LTR.format(html.escape(fc))}"
                parts.append(line)
        else:
            parts.append("✅ لا توجد أحداث اقتصادية كبرى اليوم.")
        parts.append(SEP)

    parts.append("#ملخص_الصباح #بيتكوين #الذهب #الأسواق_المالية #BTC")
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


def visible_length(html_text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


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
    if any(("ملخص الصباح" in h or "صباح الخير" in h) and arabic_date(now) in h
           for h in await pipeline.published_headlines()):
        storage.set(key, "posted-by-other")
        return False
    text = await build_brief(settings, rewriter, pipeline)
    while visible_length(text) > 1024 and SEP in text:  # too long for a photo caption: drop the agenda
        text = text.rsplit(SEP, 2)[0] + SEP + "\n" + text.rsplit("\n", 1)[-1]
    look = day_look(now)
    card = await asyncio.to_thread(render_card, look["title"], "صباح الخير",
                                   f"@{settings.target_channel_public}" if settings.target_channel_public else "",
                                   arabic_date(now), look["theme"])
    await publisher.send_post(text, card, is_html=True, fb_kind="morning")
    storage.set(key, "1")
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    storage.add_post(f"{now:%Y-%m-%d}", "morning", key, "lesson", plain.splitlines()[0], plain, None)
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
