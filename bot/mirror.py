"""Copies every post of a public channel (e.g. @HashnetArab) to your own channel, as new messages.

Rules: formatting (bold, italic) and media (photos, albums, videos) are kept; links from allowed
domains (e.g. zoom.us) are kept; every other link or @mention is removed; your registration link is
added at the end of each post.
"""
from __future__ import annotations

import asyncio
import html as htmllib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from .rss_source import HEADERS

log = logging.getLogger(__name__)
CAPTION_LIMIT = 1024


@dataclass
class MirrorPost:
    msg_id: int
    html_text: str                                # Telegram HTML, already cleaned
    photos: List[str] = field(default_factory=list)
    videos: List[str] = field(default_factory=list)
    audios: List[str] = field(default_factory=list)   # voice messages / audio files


def _allowed(url: str, keep_domains: List[str]) -> bool:
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in keep_domains)


def clean_html(raw: str, keep_domains: List[str]) -> str:
    """t.me/s message HTML -> Telegram HTML with only allowed links."""
    text = re.sub(r'<i class="emoji"[^>]*>(?:<b>)?(.*?)(?:</b>)?</i>', r"\1", raw, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text)

    def link(m: re.Match) -> str:
        url, label = htmllib.unescape(m.group(1)), m.group(2)
        if _allowed(url, keep_domains):
            return f'<a href="{htmllib.escape(url)}">{label}</a>'
        plain = re.sub(r"<[^>]+>", "", label).strip()
        looks_like_link = (plain.startswith(("@", "http", "www.", "t.me")) or plain == url
                           or bool(re.search(r"[\w-]+\.[a-z]{2,}(/|$)", plain, re.I)))
        return "" if looks_like_link else label
    text = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', link, text, flags=re.S)
    text = re.sub(r"</?(?!(?:b|i|u|s|code|a)\b)[a-z][^>]*>", "", text)      # other tags
    # Bare URLs / @mentions that were not wrapped in a link (outside of kept <a> tags)
    parts = re.split(r"(<a [^>]+>.*?</a>)", text, flags=re.S)
    for i, part in enumerate(parts):
        if not part.startswith("<a "):
            part = re.sub(r"(?<![\w/])(https?://\S+|www\.\S+|t\.me/\S+)",
                          lambda m: m.group(0) if _allowed(m.group(0), keep_domains) else "", part)
            parts[i] = re.sub(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,}", "", part)
    text = "".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def visible_len(html_text: str) -> int:
    return len(htmllib.unescape(re.sub(r"<[^>]+>", "", html_text)))


def parse_page(name: str, page: str, keep_domains: List[str]) -> List[MirrorPost]:
    posts = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        m = re.search(rf'data-post="{re.escape(name)}/(\d+)"', block, re.I)
        if not m:
            continue
        t = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        photos = re.findall(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", block)
        videos = re.findall(r'<video[^>]+src="([^"]+)"', block)
        audios = re.findall(r'<audio[^>]+src="([^"]+)"', block)
        text = clean_html(t.group(1), keep_domains) if t else ""
        if text or photos or videos or audios:
            posts.append(MirrorPost(int(m.group(1)), text, [htmllib.unescape(p) for p in photos],
                                    [htmllib.unescape(v) for v in videos],
                                    [htmllib.unescape(a) for a in audios]))
    return posts


async def fetch_all(client: httpx.AsyncClient, name: str, keep_domains: List[str],
                    since_id: int = 0) -> List[MirrorPost]:
    """All posts with id > since_id, oldest first (walks the public pages backwards)."""
    found, before = {}, None
    while True:
        url = f"https://t.me/s/{name}" + (f"?before={before}" if before else "")
        page = (await client.get(url, headers=HEADERS, follow_redirects=True)).text
        posts = parse_page(name, page, keep_domains)
        ids = [int(x) for x in re.findall(rf'data-post="{re.escape(name)}/(\d+)"', page, re.I)]
        for p in posts:
            if p.msg_id > since_id:
                found[p.msg_id] = p
        if not ids or min(ids) <= max(since_id, 1) or before == min(ids):
            break
        before = min(ids)
        await asyncio.sleep(1)
    return [found[k] for k in sorted(found)]


class MirrorPublisher:
    def __init__(self, bot_token: str, target, footer_html: str):
        self.api = f"https://api.telegram.org/bot{bot_token}/"
        self.target = str(target)
        self.footer = footer_html
        self.client = httpx.AsyncClient(timeout=120)

    async def _download(self, url: str) -> Optional[bytes]:
        try:
            r = await self.client.get(url, headers=HEADERS, follow_redirects=True)
            r.raise_for_status()
            return r.content if len(r.content) <= 49 * 1024 * 1024 else None
        except httpx.HTTPError as exc:
            log.info("Download failed %s: %s", url[:80], exc)
            return None

    async def _call(self, method: str, data: dict, files: Optional[dict] = None) -> dict:
        for _ in range(5):
            r = (await self.client.post(self.api + method, data=data, files=files)).json()
            if r.get("ok"):
                return r["result"]
            retry = (r.get("parameters") or {}).get("retry_after")
            if retry:
                await asyncio.sleep(int(retry) + 1)
                continue
            raise RuntimeError(f"{method}: {r.get('description')}")
        raise RuntimeError(f"{method}: too many retries")

    async def post(self, p: MirrorPost, silent: bool) -> None:
        text = (p.html_text + "\n\n" + self.footer).strip() if p.html_text else self.footer
        base = {"chat_id": self.target, "parse_mode": "HTML"}
        if silent:
            base["disable_notification"] = "true"
        # Telegram can't fetch these CDN links itself, so we download and upload the files.
        media = []
        for kind, url in [("photo", u) for u in p.photos] + [("video", u) for u in p.videos]:
            content = await self._download(url)
            if content:
                media.append((kind, content))
        media = media[:10]
        fits = visible_len(text) <= CAPTION_LIMIT
        if p.audios and not media:  # voice message: sent as a voice note with the text as caption
            content = await self._download(p.audios[0])
            if content:
                data = dict(base)
                if fits:
                    data["caption"] = text
                is_ogg = content[:4] == b"OggS"
                try:
                    await self._call("sendVoice" if is_ogg else "sendAudio", data,
                                     {"voice" if is_ogg else "audio": ("voice.ogg" if is_ogg else "audio.mp3", content)})
                    if fits:
                        return
                except RuntimeError as exc:
                    log.warning("Audio failed for post %s (%s)", p.msg_id, exc)
        try:
            if len(media) > 1:
                items, files = [], {}
                for i, (kind, content) in enumerate(media):
                    name = f"f{i}"
                    files[name] = (f"{name}.{'jpg' if kind == 'photo' else 'mp4'}", content)
                    items.append({"type": kind, "media": f"attach://{name}"})
                if fits:
                    items[0].update(caption=text, parse_mode="HTML")
                await self._call("sendMediaGroup", {**{k: v for k, v in base.items() if k != "parse_mode"},
                                                    "media": json.dumps(items, ensure_ascii=False)}, files)
            elif media:
                kind, content = media[0]
                data = dict(base)
                if fits:
                    data["caption"] = text
                await self._call("sendPhoto" if kind == "photo" else "sendVideo", data,
                                 {kind: (f"file.{'jpg' if kind == 'photo' else 'mp4'}", content)})
            else:
                fits = False
        except RuntimeError as exc:
            log.warning("Media failed for post %s (%s); sending text only", p.msg_id, exc)
            fits = False
        if not fits:
            await self._call("sendMessage", {**base, "text": text, "disable_web_page_preview": "true"})

    async def close(self) -> None:
        await self.client.aclose()


def _signature(html_text: str) -> str:
    plain = htmllib.unescape(re.sub(r"<[^>]+>", "", html_text or ""))
    return re.sub(r"\W+", "", plain)[:60]


def _words(html_text: str) -> set:
    plain = htmllib.unescape(re.sub(r"<[^>]+>", " ", html_text or "")).lower()
    return {w for w in re.findall(r"[\w٪%.]+", plain) if len(w) > 1}


def is_duplicate(html_text: str, recent_texts: List[str], threshold: float = 0.6) -> bool:
    """Same post already in the channel? Exact start match, or 60%+ of the words in common."""
    sig = _signature(html_text)
    words = _words(html_text)
    for other in recent_texts:
        if sig and len(sig) >= 15 and sig == _signature(other):
            return True
        ow = _words(other)
        if len(words) >= 8 and len(ow) >= 8 and len(words & ow) / min(len(words), len(ow)) >= threshold:
            return True
    return False


def mirror_footer(cfg: dict) -> str:
    link = htmllib.escape(str(cfg.get("link") or ""))
    label = htmllib.escape(str(cfg.get("footer") or "👈 سجّل الآن من هنا").rstrip(":"))
    footer = f'<a href="{link}">{label}</a>' if link else ""   # blue clickable text, no visible URL
    ask_link = str(cfg.get("inquiry_link") or "")
    if ask_link:  # added after cleaning, so it is never stripped
        ask_label = htmllib.escape(str(cfg.get("inquiry_label") or "💬 للاستفسار تواصل مع المساعد"))
        footer += f'\n\n<a href="{htmllib.escape(ask_link)}">{ask_label}</a>'
    return footer.strip()


async def run_mirror(cfg: dict, bot_token: str, storage, silent: bool = True, backfill: bool = False,
                     delay: float = 3.0, limit: Optional[int] = None) -> int:
    """New posts of the source -> target. First run without backfill only remembers where we are."""
    source = str(cfg["source"]).lstrip("@")
    key = f"mirror:{source}:last_id"
    last = int(storage.get(key) or 0)
    if backfill and not last:
        last = int(cfg.get("start_after_id") or 0)  # copy the old posts after this id
    keep = [str(d).lower() for d in cfg.get("keep_link_domains") or ["zoom.us"]]
    async with httpx.AsyncClient(timeout=30) as client:
        posts = await fetch_all(client, source, keep, since_id=last)
    if not storage.get(key) and not backfill:
        if posts:
            storage.set(key, str(posts[-1].msg_id))
            log.info("Mirror %s initialised at post %s (old posts not copied)", source, posts[-1].msg_id)
        return 0
    if cfg.get("only") == "audio":
        newest = posts[-1].msg_id if posts else None
        posts = [p for p in posts if p.audios]
        if not posts and newest:
            storage.set(key, str(newest))  # nothing to copy, move on
    if limit:
        posts = posts[:limit]
    # No duplicates: skip posts already in the target channel (from this source, the other source,
    # or the other bot in hybrid Mac + GitHub mode). Compared on the whole text, not only the start.
    recent: List[str] = []
    target_public = str(cfg.get("target") or "").lstrip("@")
    if posts and not target_public.lstrip("-").isdigit():
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                page = (await client.get(f"https://t.me/s/{target_public}", headers=HEADERS,
                                         follow_redirects=True)).text
            recent = [tp.html_text for tp in parse_page(target_public, page, keep) if tp.html_text]
        except httpx.HTTPError as exc:
            log.info("Could not read target channel for duplicates: %s", exc)
    pub = MirrorPublisher(bot_token, cfg["target"], mirror_footer(cfg))
    done = 0
    try:
        for p in posts:
            if p.html_text and is_duplicate(p.html_text, recent):
                log.info("Mirror: post %s/%s is a duplicate, skipped", source, p.msg_id)
                storage.set(key, str(p.msg_id))
                continue
            await pub.post(p, silent)
            recent.append(p.html_text)
            storage.set(key, str(p.msg_id))
            done += 1
            await asyncio.sleep(delay)
    finally:
        await pub.close()
    if done:
        log.info("Mirror %s -> %s: %d post(s) copied", source, cfg["target"], done)
    return done


async def mirror_loop(mirrors: List[dict], bot_token: str, storage, alert, interval: int = 120) -> None:
    while True:
        for cfg in mirrors:
            if not cfg.get("enabled", True) or not cfg.get("link"):
                continue
            try:
                await run_mirror(cfg, bot_token, storage, silent=False)
            except Exception as exc:
                log.warning("Mirror failed: %s", exc)
                await alert(f"مشكل فنقل منشورات {cfg.get('source')}: {str(exc)[:200]}")
        await asyncio.sleep(interval)
