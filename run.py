"""COINBABA news bot.

    python3 run.py                 start the bot (keeps running)
    python3 run.py find-channel    show the id of the channel where you added the bot
    python3 run.py test-rss        dry test: rewrite the latest website news and PRINT them (publishes nothing)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from bot.config import BASE_DIR, load_settings
from bot.pipeline import Pipeline
from bot.publisher import Alerter, Publisher
from bot.rewriter import FakeRewriter, GeminiRewriter
from bot.rss_source import fetch_feed, rss_loop
from bot.storage import Storage


def setup_logging(settings) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = RotatingFileHandler(settings.data_dir / "bot.log", maxBytes=5_000_000, backupCount=3,
                                       encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)


async def run_bot(settings) -> None:
    log = logging.getLogger("run")
    storage = Storage(settings.data_dir / "bot.db")
    storage.prune_seen()
    publisher = Publisher(settings.bot_token, settings.target_channel, settings.dry_run)
    alert = Alerter(publisher, settings.owner_chat_id)
    rewriter = GeminiRewriter(settings.gemini_api_key, settings.gemini_model, settings.llm_min_interval_seconds)
    pipeline = Pipeline(settings, storage, rewriter, publisher, alert)

    tasks = [asyncio.create_task(pipeline.worker())]
    if settings.rss_feeds:
        tasks.append(asyncio.create_task(
            rss_loop(settings.rss_feeds, storage, pipeline.submit, settings.rss_interval_seconds, alert)))

    if (BASE_DIR / ".git").exists() and not settings.dry_run:
        from bot.heartbeat import heartbeat_loop
        tasks.append(asyncio.create_task(heartbeat_loop(BASE_DIR)))

    if settings.lessons.get("enabled"):
        from bot.lessons import lessons_loop
        tasks.append(asyncio.create_task(
            lessons_loop(settings, storage, rewriter, publisher, pipeline, alert)))

    if settings.polls.get("enabled"):
        from bot.polls import polls_loop
        tasks.append(asyncio.create_task(polls_loop(settings, storage, rewriter, publisher, alert, pipeline)))

    if settings.promo.get("enabled"):
        from bot.promo import promo_loop
        tasks.append(asyncio.create_task(
            promo_loop(settings.promo, settings.timezone, storage, publisher, alert)))

    client = None
    use_account = bool(settings.api_id and settings.api_hash and (settings.data_dir / "reader.session").exists())
    if settings.telegram_channels and not use_account:
        # Default: read the public channel pages, no Telegram account needed.
        from bot.web_channel_source import web_channels_loop
        tasks.append(asyncio.create_task(web_channels_loop(
            settings.telegram_channels, storage, pipeline.submit, settings.channel_check_seconds, alert)))
        log.info("Watching %d public channel(s) through t.me/s", len(settings.telegram_channels))  # noqa
    elif settings.telegram_channels:
        from bot.telegram_source import make_client, start_listener
        client = make_client(settings.data_dir, settings.api_id, settings.api_hash)
        from bot.ingest import source_entries
        count = await start_listener(client, [r for r, _ in source_entries(settings.telegram_channels)],
                                     pipeline.submit, alert)
        log.info("Listening to %d Telegram channel(s)", count)
        tasks.append(asyncio.create_task(client.run_until_disconnected()))

    log.info("Bot started%s. Press Ctrl+C to stop.", " in DRY RUN mode" if settings.dry_run else "")
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()  # re-raise the error that stopped a task
    finally:
        for task in tasks:
            task.cancel()
        if client:
            await client.disconnect()
        await pipeline.close()
        await rewriter.close()
        await publisher.close()


async def run_once(settings) -> None:
    """GitHub Actions mode: check every source one time, publish what is new, then exit."""
    import httpx
    from bot.ingest import ingest, source_entries
    from bot.models import SourceRules
    from bot.web_channel_source import channel_username, fetch_channel
    log = logging.getLogger("once")
    storage = Storage(settings.data_dir / "bot.db")
    storage.prune_seen()
    publisher = Publisher(settings.bot_token, settings.target_channel, settings.dry_run)
    alert = Alerter(publisher, settings.owner_chat_id)
    rewriter = GeminiRewriter(settings.gemini_api_key, settings.gemini_model, settings.llm_min_interval_seconds)
    pipeline = Pipeline(settings, storage, rewriter, publisher, alert)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for feed in settings.rss_feeds:
                try:
                    items = await fetch_feed(client, feed)
                    await ingest(storage, f"seeded:{feed['name']}", f"Feed {feed['name']}", items, pipeline.submit,
                                 SourceRules.from_config(feed))
                except Exception as exc:
                    log.warning("Feed %s failed: %s", feed["name"], exc)
            for ref, rules in source_entries(settings.telegram_channels):
                name = channel_username(ref)
                try:
                    items = await fetch_channel(client, name)
                    await ingest(storage, f"seeded:tg:{name}", f"Channel @{name}", items, pipeline.submit, rules)
                except Exception as exc:
                    log.warning("Channel %s failed: %s", name, exc)
        log.info("%d new item(s) to check", pipeline.queue.qsize())
        await pipeline.drain_once()
        if settings.lessons.get("enabled"):
            from bot.lessons import post_due_lesson
            try:
                await post_due_lesson(settings, storage, rewriter, publisher, pipeline)
            except Exception as exc:
                log.warning("Lesson failed (will retry next run): %s", exc)
        if settings.polls.get("enabled"):
            from bot.polls import post_due_poll
            try:
                await post_due_poll(settings, storage, rewriter, publisher, pipeline)
            except Exception as exc:
                log.warning("Poll failed (will retry next run): %s", exc)
        log.info("Run finished: %d published", pipeline.published)
    finally:
        await pipeline.close()
        await rewriter.close()
        await publisher.close()


async def find_channel(settings) -> None:
    """Lists the channels YOU own/admin (read through the reader account), with their ids."""
    if settings.api_id and settings.api_hash and (settings.data_dir / "reader.session").exists():
        from telethon.utils import get_peer_id
        from bot.telegram_source import make_client
        client = make_client(settings.data_dir, settings.api_id, settings.api_hash)
        await client.connect()
        try:
            if await client.is_user_authorized():
                print("القنوات اللي أنت مولاها أو Admin فيها:")
                async for dialog in client.iter_dialogs():
                    ent = dialog.entity
                    if dialog.is_channel and not dialog.is_group and (getattr(ent, "creator", False) or getattr(ent, "admin_rights", None)):
                        print(f"  {get_peer_id(ent)}   {dialog.name}")
                print("\nنسخ الرقم ديال القناة (كيبدا بـ -100) وحطو في config.yaml عند target_channel")
                return
        finally:
            await client.disconnect()
    publisher = Publisher(settings.bot_token, "unused")
    try:
        chats = await publisher.find_chats()
    finally:
        await publisher.close()
    if not chats:
        print("ما لقيت حتى قناة. زيد البوت كـ Admin في القناة، نشر فيها أي رسالة، وعاود جرب.")
        return
    for chat in chats:
        print(f"{chat.get('type'):10} {chat['id']}   {chat.get('title') or chat.get('username') or ''}")
    print("\nنسخ الرقم ديال القناة (كيبدا بـ -100) وحطو في config.yaml عند target_channel")


async def test_rss(settings, per_feed: int = 2) -> None:
    """Takes the latest articles from each website and prints the rewritten posts. Publishes nothing."""
    import httpx
    settings.dry_run = True
    storage = Storage(":memory:")
    publisher = Publisher("", None, dry_run=True)
    rewriter = (GeminiRewriter(settings.gemini_api_key, settings.gemini_model, settings.llm_min_interval_seconds)
                if settings.gemini_api_key else FakeRewriter())
    if isinstance(rewriter, FakeRewriter):
        print("(ما كاينش GEMINI_API_KEY، كنستعمل كاتب تجريبي بلا ذكاء اصطناعي)")
    settings.max_item_age_hours = 10_000  # test mode: accept older articles
    pipeline = Pipeline(settings, storage, rewriter, publisher, Alerter(publisher, None))
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for feed in settings.rss_feeds:
                items = await fetch_feed(client, feed)
                print(f"\n### {feed['name']}: {len(items)} articles in feed")
                for item in items[-per_feed:]:
                    await pipeline.handle(item)
    finally:
        await pipeline.close()
        await rewriter.close()
        await publisher.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="COINBABA news bot")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "once", "find-channel", "test-rss"])
    args = parser.parse_args()
    settings = load_settings()
    setup_logging(settings)
    try:
        if args.command == "find-channel":
            asyncio.run(find_channel(settings))
        elif args.command == "once":
            asyncio.run(run_once(settings))
        elif args.command == "test-rss":
            asyncio.run(test_rss(settings))
        else:
            asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        print("\nتم إيقاف البوت.")


if __name__ == "__main__":
    main()
