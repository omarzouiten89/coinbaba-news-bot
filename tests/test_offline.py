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

    async def send_post(self, text, image):
        self.sent.append((text, image))
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
