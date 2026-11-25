"""3 short lessons a day ("معلومة اليوم") with the COINBABA card image.

The topic of each slot is picked from the list below by date, so the Mac bot and the GitHub
bot always choose the same topic and never post it twice (they also check the channel page).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .card import render_card
from .publisher import CAPTION_LIMIT
from .textutils import fit, remove_links_and_mentions

log = logging.getLogger(__name__)

# (title shown on the card, topic sent to Gemini)
TOPICS: List[Tuple[str, str]] = [
    ("كيف تحدد مستويات الدعم والمقاومة؟", "How to find support and resistance levels on a chart"),
    ("ما الفرق بين التداول الفوري والعقود الآجلة؟", "Spot trading vs futures contracts"),
    ("ما الفرق بين السهم وصندوق المؤشرات المتداول؟", "A single stock vs an ETF"),
    ("ما هو التضخم؟", "What inflation is and how it erodes purchasing power"),
    ("كيف تؤثر الفائدة على سعر الذهب؟", "How interest rates affect the price of gold"),
    ("ما هي الشمعة اليابانية؟", "How to read a Japanese candlestick"),
    ("ما هو وقف الخسارة؟", "What a stop-loss order is and why it matters"),
    ("ما هي الرافعة المالية ومخاطرها؟", "Leverage and its risks"),
    ("ما هو التنويع في المحفظة؟", "Portfolio diversification"),
    ("ما الفرق بين المستثمر والمضارب؟", "Investor vs trader/speculator"),
    ("ما هو البيتكوين باختصار؟", "What Bitcoin is, in simple terms"),
    ("ما هو تنصيف البيتكوين؟", "Bitcoin halving and why it matters"),
    ("ما هي العملات المستقرة؟", "What stablecoins are"),
    ("ما هي المحفظة الباردة والساخنة؟", "Hot wallet vs cold wallet in crypto"),
    ("ما هو الاحتياطي الفيدرالي؟", "What the US Federal Reserve does"),
    ("ماذا يعني رفع أو خفض الفائدة؟", "What a rate hike or rate cut means for markets"),
    ("ما هو مؤشر أسعار المستهلك؟", "What the CPI report is"),
    ("ما هو مؤشر إس آند بي 500؟", "What the S&P 500 index is"),
    ("ما هو ناسداك؟", "What the Nasdaq is"),
    ("ما هي القيمة السوقية؟", "What market capitalization means"),
    ("ما هي توزيعات الأرباح؟", "What dividends are"),
    ("ما هو مكرر الربحية؟", "What the P/E ratio is"),
    ("ما هو السوق الصاعد والسوق الهابط؟", "Bull market vs bear market"),
    ("ما هو حجم التداول ولماذا يهم؟", "Trading volume and why it matters"),
    ("ما هو المتوسط المتحرك؟", "What a moving average is"),
    ("ما هو مؤشر القوة النسبية؟", "What the RSI indicator is"),
    ("ما هو الاتجاه في التحليل الفني؟", "What a trend is in technical analysis"),
    ("ما الفرق بين التحليل الفني والأساسي؟", "Technical analysis vs fundamental analysis"),
    ("لماذا يعتبر الذهب ملاذاً آمناً؟", "Why gold is seen as a safe haven"),
    ("ما العلاقة بين الدولار والذهب؟", "The relationship between the US dollar and gold"),
    ("ما هي نسبة المخاطرة إلى العائد؟", "Risk/reward ratio"),
    ("كم تخاطر في الصفقة الواحدة؟", "Position sizing: how much to risk per trade"),
    ("ما هو الاستثمار الدوري المنتظم؟", "Dollar-cost averaging (DCA)"),
    ("ما هي الفجوة السعرية؟", "What a price gap is"),
    ("ما هو الانزلاق السعري؟", "What slippage is"),
    ("ما الفرق بين الأمر السوقي والأمر المحدد؟", "Market order vs limit order"),
    ("ما هي السيولة في الأسواق؟", "What liquidity means in markets"),
    ("ما هو التقلب؟", "What volatility is"),
    ("ما هي سندات الخزانة الأمريكية؟", "What US Treasury bonds are"),
    ("ما هو عائد السندات ولماذا يتابعه المستثمرون؟", "Bond yields and why investors watch them"),
    ("ما هو الركود الاقتصادي؟", "What an economic recession is"),
    ("ما هو الناتج المحلي الإجمالي؟", "What GDP is"),
    ("كيف يؤثر تقرير الوظائف الأمريكي على الأسواق؟", "How the US jobs report (NFP) moves markets"),
    ("ما هو الإيثيريوم وفيم يختلف عن البيتكوين؟", "Ethereum and how it differs from Bitcoin"),
    ("ما هي العقود الذكية؟", "What smart contracts are"),
    ("ما هو التخزين في العملات الرقمية؟", "What staking is in crypto"),
    ("ما هي صناديق البيتكوين المتداولة؟", "What spot Bitcoin ETFs are"),
    ("كيف تحمي نفسك من الاحتيال في الكريبتو؟", "How to protect yourself from crypto scams"),
    ("ما هو الخوف من تفويت الفرصة؟", "FOMO and how it hurts traders"),
    ("لماذا تحتاج خطة تداول؟", "Why you need a trading plan"),
    ("ما هو الهامش ونداء الهامش؟", "Margin and margin call"),
    ("ما هي التصفية في العقود الآجلة؟", "Liquidation in futures trading"),
    ("ما هو معدل التمويل في العقود الدائمة؟", "Funding rate in perpetual futures"),
    ("ما هو الاكتتاب العام الأولي؟", "What an IPO is"),
    ("ما هو تقسيم الأسهم؟", "What a stock split is"),
    ("ما هي الفضة ولماذا يتداولها المستثمرون؟", "Silver as an investment"),
    ("ما هي نسبة الذهب إلى الفضة؟", "The gold/silver ratio"),
    ("ما هو مؤشر الدولار؟", "What the US dollar index (DXY) is"),
    ("ما هي الفائدة المركبة؟", "Compound interest"),
    ("ما الفرق بين الاستثمار طويل وقصير المدى؟", "Long-term vs short-term investing"),
]


def slot_topic(day: date, slot_index: int, slots_per_day: int) -> Tuple[str, str]:
    return TOPICS[(day.toordinal() * slots_per_day + slot_index) % len(TOPICS)]


def due_slot(now: datetime, times: List[str], done: set, grace_hours: float = 3) -> Optional[int]:
    """Index of the first slot whose time has come (and not more than `grace_hours` ago) and not done."""
    for i, hhmm in enumerate(times):
        h, m = (int(x) for x in hhmm.split(":"))
        slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot_time <= now <= slot_time + timedelta(hours=grace_hours) and f"{now:%Y-%m-%d}:{i}" not in done:
            return i
    return None


async def post_due_lesson(settings, storage, rewriter, publisher, pipeline) -> bool:
    """Posts the lesson of the current slot if one is due. Returns True if something was posted."""
    cfg = settings.lessons
    if not cfg.get("enabled"):
        return False
    times = [str(t) for t in cfg.get("times") or ["10:00", "15:00", "21:00"]]
    now = datetime.now(ZoneInfo(settings.timezone))
    done = {k[len("lesson:"):] for k in storage.keys_with_prefix("lesson:")}
    slot = due_slot(now, times, done)
    if slot is None:
        return False
    key = f"lesson:{now:%Y-%m-%d}:{slot}"
    title, topic = slot_topic(now.date(), slot, len(times))

    # Hybrid safety: if the other bot (Mac / GitHub) already posted this lesson, just mark it done.
    if any(title[:25] in h for h in await pipeline.published_headlines()):
        storage.set(key, "posted-by-other")
        return False

    text = await rewriter.write_lesson(topic)
    # The first line is always our exact title, so both bots can recognise the post in the channel.
    lines = remove_links_and_mentions(text).splitlines()
    if lines and lines[0].lstrip().startswith("📚"):
        lines = lines[1:]
    text = fit("\n".join([f"📚 {title}", *lines]).strip(), CAPTION_LIMIT)
    card = await asyncio.to_thread(render_card, title, "معلومة اليوم",
                                   f"@{settings.target_channel_public}" if settings.target_channel_public else "")
    await publisher.send_post(text, card)
    storage.set(key, "1")
    storage.add_post(f"{now:%Y-%m-%d}", "lesson", key, "lesson", f"📚 {title}", text, None)
    log.info("Lesson posted: %s", title)
    return True


async def lessons_loop(settings, storage, rewriter, publisher, pipeline, alert) -> None:
    while True:
        try:
            await post_due_lesson(settings, storage, rewriter, publisher, pipeline)
        except Exception as exc:
            log.warning("Lesson failed: %s", exc)
            await alert(f"ما قدرتش ننشر المعلومة اليومية: {str(exc)[:200]}")
            await asyncio.sleep(600)
        await asyncio.sleep(60)
