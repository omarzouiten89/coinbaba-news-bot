"""Offline tests: no Telegram, no AI, no internet.   Run:  python3 -m unittest discover tests"""
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import Settings  # noqa: E402
from bot.models import Item  # noqa: E402
from bot.pipeline import Pipeline  # noqa: E402
from bot.rewriter import Decision, FakeRewriter, parse_decision  # noqa: E402
from bot.rss_source import parse_feed  # noqa: E402
from bot.storage import Storage  # noqa: E402
from bot.textutils import fingerprint, fit, remove_links_and_mentions, to_telegram_html  # noqa: E402

SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>T</title>
<item><title>Bitcoin hits $100,000</title><guid>a1</guid><link>https://example.com/a1</link>
<pubDate>Tue, 24 Nov 2026 12:00:00 +0000</pubDate>
<description><![CDATA[<p><img src="https://example.com/img1.png"></p><p>BTC rose 5% today.</p>]]></description>
<media:content url="https://example.com/img1.png" type="image/png" medium="image"/></item>
<item><title>Gold falls</title><guid>a0</guid><link>https://example.com/a0</link>
<pubDate>Tue, 24 Nov 2026 11:00:00 +0000</pubDate><description>Gold fell 1%.</description></item>
</channel></rss>"""


class FakePublisher:
    def __init__(self):
        self.sent = []

    async def send_post(self, text, image, silent=False):
        self.sent.append((text, image))
        self.silent = getattr(self, "silent", []) + [silent]
        return len(self.sent)


def make_settings(**kw):
    base = dict(bot_token="", api_id=None, api_hash="", gemini_api_key="", gemini_model="x",
                owner_chat_id=None, target_channel=None, daily_limit=60, llm_min_interval_seconds=0)
    base.update(kw)
    return Settings(**base)


def make_item(key, text, image=b"img", hours_old=0.1):
    return Item(key=key, source="test", text=text, image=image,
                published_at=datetime.now(timezone.utc) - timedelta(hours=hours_old))


async def no_alert(_):
    pass


class TextTests(unittest.TestCase):
    def test_remove_links_and_mentions(self):
        out = remove_links_and_mentions("JUST IN: Fed cuts rates 🇺🇸\nhttps://t.me/abc\nJoin @WatcherGuru\n👉 t.me/x")
        self.assertNotIn("t.me", out)
        self.assertNotIn("@WatcherGuru", out)
        self.assertIn("Fed cuts rates", out)

    def test_email_not_treated_as_mention(self):
        self.assertIn("a@example.com", remove_links_and_mentions("mail a@example.com"))

    def test_fingerprint_ignores_links_and_case(self):
        self.assertEqual(fingerprint("BTC hits $100K! https://x.com/1"), fingerprint("btc hits $100k @chan"))

    def test_fit_respects_limit(self):
        text = "عنوان\n" + ("جملة طويلة. " * 200)
        self.assertLessEqual(len(fit(text, 1024)), 1024)
        self.assertEqual(fit("قصير", 1024), "قصير")

    def test_html_escape_and_bold(self):
        self.assertEqual(to_telegram_html("A<B\nx & y"), "<b>A&lt;B</b>\nx &amp; y")

    def test_parse_decision_with_extra_text(self):
        d = parse_decision('```json\n{"publish": true, "post": "نص", "category": "gold"}\n```')
        self.assertTrue(d.publish)
        self.assertEqual(d.category, "gold")


class RssTests(unittest.TestCase):
    def test_parse_feed(self):
        items = parse_feed("Demo", SAMPLE_RSS)
        self.assertEqual([i.key for i in items], ["rss:Demo:a0", "rss:Demo:a1"])  # oldest first
        self.assertEqual(items[1].image_url, "https://example.com/img1.png")
        self.assertIn("BTC rose 5%", items[1].text)
        self.assertNotIn("<p>", items[1].text)


SAMPLE_CHANNEL_PAGE = """<div class="tgme_channel_info_header_title"><span dir="auto">Demo &amp; Co</span></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="Demo/10">
<a class="tgme_widget_message_photo_wrap" style="width:100px;background-image:url('https://cdn.example/p.jpg')"></a>
<div class="tgme_widget_message_text js-message_text" dir="auto">JUST IN: <b>Bitcoin</b> ETF sees $1B inflow<br/>More soon</div>
<time datetime="2026-11-24T15:05:00+00:00" class="time">15:05</time></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="Demo/11">
<a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.example/q.jpg')"></a>
<time datetime="2026-11-24T15:06:00+00:00"></time></div></div>"""


class WebChannelTests(unittest.TestCase):
    def test_parse_channel_page(self):
        from bot.web_channel_source import parse_channel_page
        items = parse_channel_page("Demo", SAMPLE_CHANNEL_PAGE)
        self.assertEqual(len(items), 1)  # the photo without caption is skipped
        item = items[0]
        self.assertEqual(item.key, "tg:Demo/10")
        self.assertEqual(item.source, "Demo & Co")
        self.assertEqual(item.image_url, "https://cdn.example/p.jpg")
        self.assertEqual(item.text, "JUST IN: Bitcoin ETF sees $1B inflow\nMore soon")

    def test_channel_username(self):
        from bot.web_channel_source import channel_username
        for ref in ("@WatcherGuru", "WatcherGuru", "https://t.me/WatcherGuru", "t.me/s/WatcherGuru/"):
            self.assertEqual(channel_username(ref), "WatcherGuru")


class PromoTests(unittest.TestCase):
    def test_is_due(self):
        from bot.promo import is_due
        self.assertFalse(is_due(datetime(2026, 11, 24, 19, 59), "20:00", ""))
        self.assertTrue(is_due(datetime(2026, 11, 24, 20, 0), "20:00", ""))
        self.assertTrue(is_due(datetime(2026, 11, 24, 23, 0), "20:00", "2026-11-23"))
        self.assertFalse(is_due(datetime(2026, 11, 24, 23, 0), "20:00", "2026-11-24"))


class LessonTests(unittest.TestCase):
    def test_due_slot(self):
        from bot.lessons import due_slot
        times = ["10:00", "15:00", "21:00"]
        self.assertIsNone(due_slot(datetime(2026, 11, 25, 9, 59), times, set()))
        self.assertEqual(due_slot(datetime(2026, 11, 25, 10, 5), times, set()), 0)
        self.assertIsNone(due_slot(datetime(2026, 11, 25, 10, 5), times, {"2026-11-25:0"}))
        self.assertIsNone(due_slot(datetime(2026, 11, 25, 14, 0), times, set()))  # missed by > 3h
        self.assertEqual(due_slot(datetime(2026, 11, 25, 21, 30), times, {"2026-11-25:0"}), 2)

    def test_topics_rotate_without_repeat_in_a_day(self):
        from datetime import date
        from bot.lessons import slot_topic
        day = date(2026, 11, 25)
        titles = {slot_topic(day, i, 3)[0] for i in range(3)}
        self.assertEqual(len(titles), 3)
        self.assertNotEqual(slot_topic(day, 0, 3), slot_topic(date(2026, 11, 26), 0, 3))

    def test_card_renders(self):
        from bot.card import card_title, render_card
        self.assertEqual(card_title("ما الفرق بين الفوري (Spot) والآجلة (Futures)؟"), "ما الفرق بين الفوري والآجلة؟")
        self.assertTrue(render_card("ما هو التضخم؟").startswith(b"\xff\xd8"))  # a JPEG


class MorningTests(unittest.TestCase):
    def test_day_look(self):
        from bot.morning import day_look
        self.assertEqual(day_look(datetime(2026, 11, 27))["theme"], "friday")    # Friday
        self.assertEqual(day_look(datetime(2026, 11, 28))["theme"], "weekend")   # Saturday
        self.assertEqual(day_look(datetime(2026, 11, 25))["title"], "ملخص الصباح")

    def test_theme_cards_render(self):
        from bot.card import render_card
        for theme in ("friday", "weekend"):
            self.assertTrue(render_card("عنوان", "صباح الخير", "@x", "السبت 28 نوفمبر", theme).startswith(b"\xff\xd8"))


class MirrorTests(unittest.TestCase):
    def test_links_cleaned(self):
        from bot.mirror import clean_html
        out = clean_html('A <a href="https://zoom.us/j/1">https://zoom.us/j/1</a> B '
                         '<a href="https://hashnet.ai/r/x">hashnet.ai/r/x</a> <a href="https://t.me/x">@x</a>', ["zoom.us"])
        self.assertIn("zoom.us/j/1", out)
        self.assertNotIn("hashnet.ai", out)
        self.assertNotIn("@x", out)

    def test_duplicates(self):
        from bot.mirror import is_duplicate
        a = "🛠 طازج من المناجم 🏹 0.38٪ من البيتكوين 💥 6.74٪ حتى الآن في شهر نوفمبر اليوم السادس عشر وقد تجاوزنا"
        b = "طازج من المناجم! 0.38٪ من البيتكوين، 6.74٪ حتى الآن في شهر نوفمبر اليوم السادس عشر 🔥 وقد تجاوزنا الهدف"
        c = "اجتماع اليوم على زوم مع فريق الإدارة لشرح منتج التأجير الجديد وطريقة السحب والإيداع خطوة بخطوة"
        self.assertTrue(is_duplicate(b, [a]))
        self.assertFalse(is_duplicate(c, [a]))


class PollTests(unittest.TestCase):
    def test_parse_poll(self):
        from bot.rewriter import parse_poll
        p = parse_poll('{"question": "كيف تتوقع البيتكوين؟", "options": ["📈 صعود", "📉 هبوط", "↔️ استقرار"], '
                       '"correct_index": null, "explanation": ""}')
        self.assertEqual(len(p.options), 3)
        self.assertIsNone(p.correct_index)
        with self.assertRaises(ValueError):
            parse_poll('{"question": "x", "options": ["only one"]}')

    def test_theme_changes_daily(self):
        from datetime import date
        from bot.polls import theme_for_day
        kinds = {theme_for_day(date(2026, 11, d))[0] for d in range(1, 5)}
        self.assertEqual(kinds, {"bitcoin", "gold", "event", "market"})
        day = date(2026, 11, 25)
        self.assertNotEqual(theme_for_day(day, 0, 2), theme_for_day(day, 1, 2))  # 2 polls, 2 themes


class PipelineTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def build(self, **kw):
        storage = Storage(":memory:")
        publisher = FakePublisher()
        rewriter = FakeRewriter()
        pipeline = Pipeline(make_settings(**kw), storage, rewriter, publisher, no_alert)
        return pipeline, storage, publisher, rewriter

    def test_publish_and_dedup(self):
        async def scenario():
            p, storage, pub, rw = self.build()
            text = "JUST IN: BlackRock buys 1,000 Bitcoin for its ETF https://t.me/source"
            await p.submit(make_item("tg:1:1", text))
            await p.submit(make_item("tg:1:1", text))                 # same message again
            await p.submit(make_item("tg:2:9", text + " @other"))     # same text from another channel
            await p.submit(make_item("tg:3:3", "hi"))                 # too short
            await p.submit(make_item("tg:4:4", "Old news about gold prices rising", hours_old=10))
            self.assertEqual(p.queue.qsize(), 1)
            await p.handle(p.queue.get_nowait())
            await p.close()
            return storage, pub, rw
        storage, pub, rw = self.run_async(scenario())
        self.assertEqual(len(pub.sent), 1)
        text, image = pub.sent[0]
        self.assertNotIn("t.me", text)
        self.assertEqual(image, b"img")
        self.assertEqual(rw.calls, 1)
        self.assertEqual(len(storage.recent_headlines()), 1)

    def test_ad_is_not_published(self):
        async def scenario():
            p, storage, pub, rw = self.build()
            await p.handle(make_item("tg:1:2", "Join our VIP signals group now, 90% accuracy!!!"))
            await p.close()
            return pub
        self.assertEqual(self.run_async(scenario()).sent, [])

    def test_daily_limit(self):
        async def scenario():
            p, storage, pub, rw = self.build(daily_limit=2)
            for n in range(4):
                await p.handle(make_item(f"tg:1:{n}", f"Market update number {n}: Ethereum moves {n}%"))
            await p.close()
            return pub, rw
        pub, rw = self.run_async(scenario())
        self.assertEqual(len(pub.sent), 2)
        self.assertEqual(rw.calls, 2)  # no AI calls wasted after the limit

    def test_source_rules(self):
        from bot.models import SourceRules

        class ScoredRewriter(FakeRewriter):
            async def process(self, text, *a, **k):
                self.calls += 1
                score = 9 if "Fed" in text else 3
                return Decision(publish=True, post="📰 عنوان\nنص الخبر هنا بما يكفي من الطول", importance=score)

        async def scenario():
            storage, pub = Storage(":memory:"), FakePublisher()
            p = Pipeline(make_settings(), storage, ScoredRewriter(), pub, no_alert)
            ct = SourceRules(daily_max=1, min_importance=8)
            for n, text in enumerate(["Minor exchange listing news today", "Fed cuts rates by 50bp today",
                                      "Fed chair speaks again about rates"]):
                item = make_item(f"rss:ct:{n}", text)
                item.source, item.rules = "Cointelegraph", ct
                await p.handle(item)
            gold = make_item("rss:fx:1", "EUR/USD outlook for the week")
            gold.rules = SourceRules(keywords=["gold", "xau"])
            await p.handle(gold)
            await p.close()
            return pub
        pub = self.run_async(scenario())
        self.assertEqual(len(pub.sent), 1)  # minor skipped (importance), 2nd Fed skipped (daily max), no gold keyword

    def test_quiet_hours_and_silent(self):
        class Rw(FakeRewriter):
            async def process(self, text, *a, **k):
                self.calls += 1
                big = "Fed" in text
                return Decision(publish=True, post="📰 عنوان\nنص الخبر هنا بما يكفي من الطول",
                                importance=9 if big else 6, breaking=big)

        async def scenario(quiet):
            pub = FakePublisher()
            s = make_settings(silent_news=True, news_min_importance=6,
                              quiet_hours={"start": "00:00", "end": "07:00"})
            p = Pipeline(s, Storage(":memory:"), Rw(), pub, no_alert)
            p.in_quiet_hours = lambda now=None: quiet
            await p.handle(make_item("a", "Normal market update about stocks today"))
            await p.handle(make_item("b", "Fed emergency rate cut announced now"))
            await p.close()
            return pub
        day = self.run_async(scenario(False))
        self.assertEqual(day.silent, [True, False])       # normal news silent, breaking with sound
        night = self.run_async(scenario(True))
        self.assertEqual(len(night.sent), 1)               # only the breaking news at night

    def test_quiet_window(self):
        async def scenario():
            p = Pipeline(make_settings(quiet_hours={"start": "00:00", "end": "07:00"}), Storage(":memory:"),
                         FakeRewriter(), FakePublisher(), no_alert)
            result = (p.in_quiet_hours(datetime(2026, 11, 25, 3, 0)), p.in_quiet_hours(datetime(2026, 11, 25, 7, 0)))
            await p.close()
            return result
        self.assertEqual(self.run_async(scenario()), (True, False))

    def test_caption_limit_with_image(self):
        class LongRewriter(FakeRewriter):
            async def process(self, *a, **k):
                return Decision(publish=True, post="🚨 عنوان\n" + "كلمة " * 600)
        async def scenario():
            storage = Storage(":memory:")
            pub = FakePublisher()
            p = Pipeline(make_settings(), storage, LongRewriter(), pub, no_alert)
            await p.handle(make_item("tg:1:1", "Long story about the Federal Reserve decision today"))
            await p.close()
            return pub
        text, _ = self.run_async(scenario()).sent[0]
        self.assertLessEqual(len(text), 1024)


if __name__ == "__main__":
    unittest.main()


# ---- trade ideas -------------------------------------------------------------
import unittest as _ut


class TradeIdeaTests(_ut.TestCase):
    def _data(self, **kw):
        base = {"ticker": "TEST", "side": "long", "entry_low": 99, "entry_high": 101, "entry": 100,
                "stop": 95, "tp1": 110, "tp2": 115, "posted_at": 1000, "status": "open",
                "filled": False, "tp1_hit": False}
        base.update(kw)
        return base

    def test_long_hits_tp1_then_breakeven(self):
        from bot.ideas import evaluate
        d = self._data()
        ev = evaluate(d, [(1100, 101, 99), (1200, 111, 104), (1300, 104, 99.5)])
        self.assertEqual(ev, ["tp1", "breakeven"])

    def test_stop_wins_when_same_bar(self):
        from bot.ideas import evaluate
        d = self._data()
        self.assertEqual(evaluate(d, [(1100, 100, 99), (1200, 111, 94)]), ["stopped"])

    def test_short_full_target(self):
        from bot.ideas import evaluate
        d = self._data(side="short", stop=105, tp1=90, tp2=85)
        self.assertEqual(evaluate(d, [(1100, 100.5, 99), (1200, 99, 89), (1300, 95, 84)]), ["tp1", "tp2"])

    def test_cancel_when_entry_not_reached(self):
        from bot.ideas import evaluate
        d = self._data()
        self.assertEqual(evaluate(d, [(1100, 108, 104), (1000 + 3 * 86400, 109, 105)]), ["cancelled"])

    def test_build_idea_uptrend(self):
        from bot.ideas import Candles, build_idea
        closes = [100 + i * 0.5 + (2 if i % 6 < 3 else -2) for i in range(120)]
        c = Candles("T", list(range(120)), closes, [x + 1.5 for x in closes], [x - 1.5 for x in closes],
                    closes, closes[-1], True)
        idea = build_idea(c)
        if idea:  # when produced, it must be coherent
            self.assertEqual(idea.side, "long")
            self.assertLess(idea.stop, idea.entry_low)
            self.assertGreater(idea.tp2, idea.tp1)
            self.assertGreaterEqual(idea.rr, 1.5 - 1e-9)
