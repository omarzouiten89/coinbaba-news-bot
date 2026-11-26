"""💡 Trade ideas on big US stocks, for learning on a demo account.

Every number is computed from real daily candles (Yahoo Finance), never written by the AI:
  - trend: 20 and 50 day moving averages
  - support / resistance: recent swing lows / highs
  - stop loss: beyond the support/resistance, at least 1 ATR (the stock's normal daily move) away
  - targets: next resistance/support, and never less than 1.5x the risk
An idea is only posted when the trend is clear and the reward is at least 1.5x the risk.

After posting, each idea is followed: when a target or the stop is reached (or after 10 trading days)
the bot replies to the original post with the honest result, losses included.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .lessons import due_slot

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; COINBABA-Bot/1.0)"}
LTR = "⁦{}⁩"
SEP = "━━━━━━━━━━━━━━"

NAMES = {
    "NVDA": "إنفيديا NVIDIA", "MSFT": "مايكروسوفت Microsoft", "AAPL": "آبل Apple",
    "AMZN": "أمازون Amazon", "GOOGL": "جوجل Alphabet", "META": "ميتا Meta",
    "TSLA": "تسلا Tesla", "AMD": "إي إم دي AMD", "NFLX": "نتفليكس Netflix", "PLTR": "بالانتير Palantir",
}
DEFAULT_TICKERS = list(NAMES)

TIPS = [
    "وقف الخسارة يُحدَّد قبل الدخول، ولا يُحرَّك أبداً في اتجاه الخسارة.",
    "لا تخاطر بأكثر من 1% إلى 2% من رصيد الحساب في الصفقة الواحدة.",
    "حجم الصفقة = المبلغ الذي تقبل خسارته ÷ المسافة بين الدخول ووقف الخسارة.",
    "عند تحقق الهدف الأول، يمكن نقل وقف الخسارة إلى نقطة الدخول لحماية الصفقة.",
    "نسبة العائد إلى المخاطرة 1:2 تعني أنك تربح حتى لو نجحت 4 صفقات فقط من كل 10.",
    "الدعم منطقة يكثر فيها الشراء، والمقاومة منطقة يكثر فيها البيع، وليست خطاً دقيقاً.",
    "إذا لم يصل السعر إلى منطقة الدخول، فلا تطارده: الفرصة القادمة ستأتي.",
    "سجّل كل صفقة في دفتر: سبب الدخول، النتيجة، وما تعلمته منها.",
    "البيع (Short) يعني الربح من هبوط السعر، ووقف الخسارة يكون فوق سعر الدخول.",
    "المتوسط المتحرك 50 يوماً يوضح الاتجاه العام: السعر فوقه = اتجاه صاعد غالباً.",
]


# ---------------------------------------------------------------- indicators

def sma(values: List[float], n: int) -> Optional[float]:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[:n], closes[1:n + 1]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    for a, b in zip(closes[n:], closes[n + 1:]):
        d = b - a
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def atr(highs: List[float], lows: List[float], closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) <= n:
        return None
    trs = [max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])]
    value = sum(trs[:n]) / n
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def swing_levels(highs: List[float], lows: List[float], window: int = 3, lookback: int = 120):
    """Swing highs (resistance) and swing lows (support) of the last `lookback` candles."""
    start = max(window, len(highs) - lookback)
    res, sup = [], []
    for i in range(start, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            res.append(highs[i])
        if lows[i] == min(lows[i - window:i + window + 1]):
            sup.append(lows[i])
    return sorted(set(sup)), sorted(set(res))


# ---------------------------------------------------------------- data

@dataclass
class Candles:
    ticker: str
    ts: List[int]
    opens: List[float]
    highs: List[float]
    lows: List[float]
    closes: List[float]
    price: float
    market_open: bool


async def fetch_candles(client: httpx.AsyncClient, ticker: str, rng: str = "1y", interval: str = "1d") -> Candles:
    r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", headers=HEADERS,
                         params={"range": rng, "interval": interval})
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = [(t, o, h, l, c) for t, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"])
            if None not in (o, h, l, c)]
    meta = res["meta"]
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    now = time.time()
    is_open = bool(period) and period.get("start", 0) <= now <= period.get("end", 0)
    return Candles(ticker, [x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows],
                   [x[3] for x in rows], [x[4] for x in rows],
                   float(meta.get("regularMarketPrice") or rows[-1][4]), is_open)


# ---------------------------------------------------------------- idea

@dataclass
class Idea:
    ticker: str
    side: str                 # "long" or "short"
    entry_low: float
    entry_high: float
    stop: float
    tp1: float
    tp2: float
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0
    rsi: float = 0.0
    sma20: float = 0.0
    sma50: float = 0.0
    level: float = 0.0        # the support (long) / resistance (short) the idea leans on

    @property
    def entry(self) -> float:
        return (self.entry_low + self.entry_high) / 2

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def rr(self) -> float:
        return abs(self.tp1 - self.entry) / self.risk if self.risk else 0


def _targets(entry: float, risk: float, levels: List[float], sign: int, min_rr: float):
    """TP1: the first level between 1.5R and 3R away (else 2R). TP2: the next level up to 4R (else 3R)."""
    dists = sorted(sign * (x - entry) for x in levels if sign * (x - entry) > 0)
    in_range = [d for d in dists if min_rr * risk <= d <= 3 * risk]
    d1 = in_range[0] if in_range else 2 * risk
    later = [d for d in dists if d1 + 0.5 * risk <= d <= 4 * risk]
    d2 = later[0] if later else max(3 * risk, d1 + risk)
    return entry + sign * d1, entry + sign * d2


def build_idea(c: Candles, min_rr: float = 1.5, max_risk_pct: float = 7.0) -> Optional[Idea]:
    """A clear-trend setup near a level, or None when there is nothing clean to show."""
    closes, highs, lows = c.closes, c.highs, c.lows
    s20, s50, r, a = sma(closes, 20), sma(closes, 50), rsi(closes), atr(highs, lows, closes)
    if None in (s20, s50, r, a) or a <= 0:
        return None
    price = c.price
    supports, resistances = swing_levels(highs, lows)
    band = 0.15 * a

    if price > s50 and s20 > s50 and 40 <= r <= 68:
        side = "long"
        below = [s for s in supports if price - 3 * a <= s < price]
        level = max(below) if below else None
        stop = min(level - 0.5 * a, price - a) if level else price - 1.5 * a
        if price - stop > 3 * a:
            stop = price - 1.5 * a
        tp1, tp2 = _targets(price, price - stop, resistances, 1, min_rr)
        dist = (price - level) / a if level else 2.0
        reasons = ["السعر فوق المتوسط المتحرك 50 يوماً (اتجاه صاعد)",
                   "المتوسط 20 يوماً فوق المتوسط 50 يوماً (زخم إيجابي)"]
        if level:
            reasons.append(f"قريب من منطقة دعم عند {LTR.format(_usd(level))}")
    elif price < s50 and s20 < s50 and 32 <= r <= 60:
        side = "short"
        above = [x for x in resistances if price < x <= price + 3 * a]
        level = min(above) if above else None
        stop = max(level + 0.5 * a, price + a) if level else price + 1.5 * a
        if stop - price > 3 * a:
            stop = price + 1.5 * a
        tp1, tp2 = _targets(price, stop - price, supports, -1, min_rr)
        dist = (level - price) / a if level else 2.0
        reasons = ["السعر تحت المتوسط المتحرك 50 يوماً (اتجاه هابط)",
                   "المتوسط 20 يوماً تحت المتوسط 50 يوماً (زخم سلبي)"]
        if level:
            reasons.append(f"قريب من منطقة مقاومة عند {LTR.format(_usd(level))}")
    else:
        return None

    if tp2 <= 0 or stop <= 0 or abs(price - stop) / price * 100 > max_risk_pct:
        return None   # too wild to be a good learning example
    zone = "معتدل" if 45 <= r <= 60 else ("قوي" if r > 60 else "ضعيف")
    reasons.append(f"مؤشر RSI = {r:.0f} (زخم {zone})")
    idea = Idea(c.ticker, side, price - band, price + band, stop, tp1, tp2, reasons,
                rsi=r, sma20=s20, sma50=s50, level=level or 0.0)
    if idea.rr < min_rr - 1e-9:
        return None
    # Best ideas: close to the level, with a good reward/risk.
    idea.score = idea.rr - 0.4 * min(dist, 3)
    return idea


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _pct(x: float, ref: float) -> str:
    return LTR.format(f"{(x - ref) / ref * 100:+.1f}%")


def build_text(idea: Idea, tip: str, handle: str = "") -> str:
    name = NAMES.get(idea.ticker, idea.ticker)
    side = "🟢 شراء (Long)" if idea.side == "long" else "🔴 بيع (Short)"
    e = idea.entry
    lines = [
        f"💡 <b>فكرة تداولية: {name}</b>",
        f"{side}  |  إطار يومي  |  {LTR.format('$' + idea.ticker)}",
        SEP,
        f"📍 <b>منطقة الدخول:</b> {LTR.format(_usd(idea.entry_low) + ' – ' + _usd(idea.entry_high))}",
        f"🛑 <b>وقف الخسارة:</b> {LTR.format(_usd(idea.stop))}  ({_pct(idea.stop, e)})",
        f"🎯 <b>الهدف الأول:</b> {LTR.format(_usd(idea.tp1))}  ({_pct(idea.tp1, e)})",
        f"🎯 <b>الهدف الثاني:</b> {LTR.format(_usd(idea.tp2))}  ({_pct(idea.tp2, e)})",
        f"⚖️ <b>العائد / المخاطرة:</b> {LTR.format(f'1 : {idea.rr:.1f}')}",
        SEP,
        "🔎 <b>لماذا هذه الفكرة؟</b>",
        *[f"• {r}" for r in idea.reasons],
        SEP,
        f"🎓 <b>درس سريع:</b> {tip}",
        "",
        "⚠️ <i>فكرة تعليمية للتطبيق على الحساب التجريبي، وليست نصيحة استثمارية. "
        "إذا لم يصل السعر إلى منطقة الدخول خلال يومين، تُلغى الفكرة.</i>",
        "",
        f"#{idea.ticker} #فكرة_تداولية #الأسهم_الأمريكية",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- chart

def render_chart(c: Candles, idea: Idea, handle: str = "", bars: int = 70) -> bytes:
    from PIL import Image, ImageDraw
    from .card import ASSETS, GOLD, WHITE, _ar, _font, _latin_font

    W, H = 1280, 860
    L, R, T, B = 96, 1010, 150, 800   # candles area; labels go in the right gutter
    img = Image.new("RGB", (W, H), (8, 14, 32))
    d = ImageDraw.Draw(img)
    for y in range(H):  # soft vertical gradient
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(12 - 6 * k), int(22 - 12 * k), int(48 - 26 * k)))

    o, h, lo, cl = (x[-bars:] for x in (c.opens, c.highs, c.lows, c.closes))
    s20 = [sma(c.closes[:len(c.closes) - bars + i + 1], 20) for i in range(len(cl))]
    s50 = [sma(c.closes[:len(c.closes) - bars + i + 1], 50) for i in range(len(cl))]
    levels = [idea.stop, idea.tp1, idea.tp2, idea.entry_low, idea.entry_high]
    top = max(max(h), *levels)
    bot = min(min(lo), *levels)
    pad = (top - bot) * 0.06
    top, bot = top + pad, bot - pad
    y_of = lambda v: T + (top - v) / (top - bot) * (B - T)  # noqa: E731
    step = (R - L) / len(cl)

    small = _latin_font(22)
    for i in range(6):  # grid + price scale
        v = bot + (top - bot) * i / 5
        y = y_of(v)
        d.line([(L, y), (R, y)], fill=(30, 42, 72), width=1)
        d.text((L - 12, y), f"{v:,.0f}", font=small, fill=(130, 140, 170), anchor="rm")

    # entry zone
    d.rectangle([L, y_of(idea.entry_high), R, y_of(idea.entry_low)], fill=(70, 62, 30))

    for i in range(len(cl)):
        x = L + step * (i + 0.5)
        up = cl[i] >= o[i]
        col = (38, 196, 133) if up else (234, 76, 92)
        d.line([(x, y_of(h[i])), (x, y_of(lo[i]))], fill=col, width=2)
        y1, y2 = sorted((y_of(o[i]), y_of(cl[i])))
        d.rectangle([x - step * 0.32, y1, x + step * 0.32, max(y2, y1 + 2)], fill=col)

    for series, col in ((s20, (110, 170, 255)), (s50, GOLD)):
        pts = [(L + step * (i + 0.5), y_of(v)) for i, v in enumerate(series) if v]
        if len(pts) > 1:
            d.line(pts, fill=col, width=3)

    label_font = _latin_font(24)
    for value, col, tag in ((idea.tp2, (38, 196, 133), "TP2"), (idea.tp1, (38, 196, 133), "TP1"),
                            (idea.entry, GOLD, "ENTRY"), (idea.stop, (234, 76, 92), "SL")):
        y = y_of(value)
        for x in range(L, R + 14, 18):  # dashed line
            d.line([(x, y), (min(x + 10, R + 14), y)], fill=col, width=3)
        text = f"{tag}  {value:,.2f}"
        d.rounded_rectangle([R + 14, y - 18, W - 24, y + 18], radius=8, fill=col)
        d.text(((R + 14 + W - 24) / 2, y), text, font=label_font, fill=(8, 14, 32), anchor="mm")

    # header
    side_col = (38, 196, 133) if idea.side == "long" else (234, 76, 92)
    d.text((40, 58), idea.ticker, font=_latin_font(64), fill=WHITE, anchor="lm")
    tw = d.textlength(idea.ticker, font=_latin_font(64))
    side_txt = "LONG" if idea.side == "long" else "SHORT"
    sw = d.textlength(side_txt, font=_latin_font(30))
    d.rounded_rectangle([40 + tw + 24, 36, 40 + tw + 24 + sw + 36, 82], radius=22, fill=side_col)
    d.text((40 + tw + 42, 59), side_txt, font=_latin_font(30), fill=(8, 14, 32), anchor="lm")
    d.text((40, 112), "Daily  ·  MA20 (blue)  ·  MA50 (gold)", font=small, fill=(150, 160, 190), anchor="lm")

    badge = _ar("فكرة تداولية")
    bf = _font(34)
    bw = d.textlength(badge, font=bf)
    d.rounded_rectangle([W - 40 - bw - 48, 34, W - 40, 92], radius=29, fill=GOLD)
    d.text((W - 64, 63), badge, font=bf, fill=(8, 14, 32), anchor="rm")

    mark = ASSETS / "logo-mark.png"
    if mark.exists():
        m = Image.open(mark).convert("RGBA").resize((56, 56))
        img.paste(m, (W - 96, H - 74), m)
    foot = _ar("للتعلّم على الحساب التجريبي، وليست نصيحة استثمارية")
    d.text((40, H - 46), foot, font=_font(24), fill=(150, 160, 190), anchor="lm")
    if handle:
        d.text((W - 110, H - 46), handle, font=_latin_font(26), fill=GOLD, anchor="rm")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()


# ---------------------------------------------------------------- posting

async def pick_ideas(client: httpx.AsyncClient, tickers: List[str], recent: set, count: int):
    found = []
    for t in tickers:
        if t in recent:
            continue
        try:
            c = await fetch_candles(client, t)
        except Exception as exc:  # one bad ticker never stops the others
            log.info("No data for %s: %s", t, exc)
            continue
        idea = build_idea(c)
        if idea:
            found.append((idea, c))
    found.sort(key=lambda x: x[0].score, reverse=True)
    return found[:count]


def _channel(cfg) -> str:
    return str(cfg.get("channel") or "")


async def post_due_idea(settings, storage, publisher, force: bool = False) -> int:
    cfg = getattr(settings, "ideas", {}) or {}
    if not cfg.get("enabled") or not _channel(cfg):
        return 0
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    times = [str(t) for t in cfg.get("times") or ["18:45", "20:30"]]
    done = {k[len("idea-slot:"):] for k in storage.keys_with_prefix("idea-slot:")}
    slot = due_slot(now, times, done, grace_hours=1.5)
    if slot is None and not force:
        return 0
    slot_key = f"idea-slot:{now:%Y-%m-%d}:{slot}"
    tickers = [str(t).upper() for t in cfg.get("tickers") or DEFAULT_TICKERS]
    # one idea per stock at a time, and not the same stock again for 5 days
    recent = set()
    for key in storage.keys_with_prefix("idea:"):
        data = json.loads(storage.get(key) or "{}")
        if data.get("status") == "open" or time.time() - data.get("posted_at", 0) < 5 * 86400:
            recent.add(data.get("ticker"))
    per_slot = int(cfg.get("per_slot", 1))
    async with httpx.AsyncClient(timeout=30) as client:
        probe = await fetch_candles(client, "SPY", rng="5d")
        if not probe.market_open and not force:
            storage.set(slot_key, "market-closed")
            log.info("US market closed: no trade idea this slot")
            return 0
        picks = await pick_ideas(client, tickers, recent, per_slot)
    if not picks:
        storage.set(slot_key, "no-setup")
        log.info("No clean setup this slot")
        return 0
    handle = str(cfg.get("handle") or "")
    posted = 0
    for idea, candles in picks:
        tip = random.choice(TIPS)
        chart = await asyncio.to_thread(render_chart, candles, idea, handle)
        msg_id = await publisher.send_post(build_text(idea, tip, handle), chart, is_html=True)
        storage.set(f"idea:{now:%Y-%m-%d}:{idea.ticker}", json.dumps({
            **{k: v for k, v in asdict(idea).items() if k != "reasons"},
            "entry": idea.entry, "message_id": msg_id, "posted_at": time.time(),
            "status": "open", "filled": False, "tp1_hit": False,
        }))
        posted += 1
        log.info("Trade idea posted: %s %s", idea.side, idea.ticker)
    if slot is not None:
        storage.set(slot_key, "1")
    return posted


# ---------------------------------------------------------------- follow-up

def evaluate(data: dict, bars: List[tuple]) -> List[str]:
    """Walks hourly bars (ts, high, low) after the post; updates `data` and returns events.
    If one bar touches both the stop and a target, the stop counts (the cautious reading)."""
    events = []
    long = data["side"] == "long"
    for ts, high, low in bars:
        if ts <= data.get("checked_ts", data["posted_at"]):
            continue
        data["checked_ts"] = ts
        if not data["filled"]:
            if low <= data["entry_high"] and high >= data["entry_low"]:
                data["filled"] = True
            elif ts - data["posted_at"] > 2 * 86400:
                data["status"] = "cancelled"
                events.append("cancelled")
                break
            else:
                continue
        stop = data["entry"] if data["tp1_hit"] else data["stop"]
        if (low <= stop) if long else (high >= stop):
            data["status"] = "breakeven" if data["tp1_hit"] else "stopped"
            events.append(data["status"])
            break
        if not data["tp1_hit"] and ((high >= data["tp1"]) if long else (low <= data["tp1"])):
            data["tp1_hit"] = True
            events.append("tp1")
        if data["tp1_hit"] and ((high >= data["tp2"]) if long else (low <= data["tp2"])):
            data["status"] = "tp2"
            events.append("tp2")
            break
    if data["status"] == "open" and time.time() - data["posted_at"] > 14 * 86400:
        data["status"] = "expired"
        events.append("expired")
    return events


def result_text(data: dict, event: str, price: float) -> str:
    t, e = data["ticker"], data["entry"]
    sign = 1 if data["side"] == "long" else -1
    pct = lambda v: LTR.format(f"{sign * (v - e) / e * 100:+.1f}%")  # noqa: E731
    if event == "tp1":
        return (f"✅ <b>{t}: تحقق الهدف الأول</b> {LTR.format(_usd(data['tp1']))} ({pct(data['tp1'])})\n"
                f"🎓 الخطوة التعليمية: نقل وقف الخسارة إلى نقطة الدخول {LTR.format(_usd(e))} "
                "لتصبح الصفقة بلا مخاطرة.")
    if event == "tp2":
        return f"🏆 <b>{t}: تحقق الهدف الثاني</b> {LTR.format(_usd(data['tp2']))} ({pct(data['tp2'])}). اكتملت الفكرة."
    if event == "stopped":
        return (f"❌ <b>{t}: ضرب وقف الخسارة</b> {LTR.format(_usd(data['stop']))} ({pct(data['stop'])})\n"
                "🎓 الخسارة الصغيرة جزء من التداول، ووقف الخسارة هو ما حمى الحساب من خسارة أكبر.")
    if event == "breakeven":
        return f"⚪️ <b>{t}: خرجت الصفقة عند نقطة الدخول</b> بعد تحقيق الهدف الأول. نتيجة إيجابية بلا خسارة."
    if event == "cancelled":
        return f"⏸ <b>{t}: أُلغيت الفكرة</b> لأن السعر لم يصل إلى منطقة الدخول خلال يومين."
    return (f"⏱ <b>{t}: انتهت مدة الفكرة</b> (أسبوعان). السعر الآن {LTR.format(_usd(price))} "
            f"({pct(price)} من الدخول).")


async def follow_ideas(settings, storage, publisher) -> int:
    cfg = getattr(settings, "ideas", {}) or {}
    channel = _channel(cfg)
    if not cfg.get("enabled") or not channel:
        return 0
    sent = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for key in storage.keys_with_prefix("idea:"):
            data = json.loads(storage.get(key) or "{}")
            if data.get("status") != "open":
                continue
            try:
                c = await fetch_candles(client, data["ticker"], rng="1mo", interval="1h")
            except Exception as exc:
                log.info("Follow-up data failed for %s: %s", data.get("ticker"), exc)
                continue
            events = evaluate(data, list(zip(c.ts, c.highs, c.lows)))
            for ev in events:
                await publisher.send_reply(result_text(data, ev, c.price), data.get("message_id"))
                sent += 1
            storage.set(key, json.dumps(data))
    return sent


async def ideas_loop(settings, storage, publisher, alert) -> None:
    while True:
        try:
            await post_due_idea(settings, storage, publisher)
            await follow_ideas(settings, storage, publisher)
        except Exception as exc:
            await alert(f"Trade ideas: {exc}")
        await asyncio.sleep(300)
