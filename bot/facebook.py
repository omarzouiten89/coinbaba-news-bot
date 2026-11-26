"""Copies the channel posts to the COINBABA Facebook Page (Meta Graph API).

Safe by design, so the Page is never flagged as spam:
- a daily cap and a minimum gap between two posts, counted from the Page itself
  (so the Mac bot and the GitHub bot share the same limits);
- only important news (importance threshold), plus the morning brief and lessons;
- no links and no @mentions, ever; a few relevant hashtags at the end;
- a Facebook problem never stops the Telegram post.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

import httpx

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from .textutils import remove_links_and_mentions, strip_html

log = logging.getLogger(__name__)

# (words to look for in the post, hashtag). The first matches win; at most `max_hashtags`.
TOPIC_TAGS = [
    (("بيتكوين", "البيتكوين", "bitcoin", "btc"), "#بيتكوين"),
    (("إيثريوم", "الإيثريوم", "ايثريوم", "ethereum", "eth"), "#إيثريوم"),
    (("سولانا", "solana"), "#سولانا"),
    (("ريبل", "xrp"), "#ريبل"),
    (("ذهب", "الذهب", "gold", "xau"), "#الذهب"),
    (("الفيدرالي", "الفائدة", "التضخم", "fed", "باول"), "#الاقتصاد"),
    (("صندوق", "صناديق", "etf"), "#ETF"),
    (("ترامب", "trump"), "#ترامب"),
    (("أسهم", "الأسهم", "ناسداك", "nasdaq", "s&p"), "#الأسهم"),
    (("اختراق", "سرقة", "احتيال", "hack"), "#أمان_رقمي"),
]
KIND_TAGS = {
    "news": ["#أخبار_الكريبتو", "#العملات_الرقمية"],
    "analysis": ["#تحليل", "#العملات_الرقمية"],
    "lesson": ["#تعلم_الكريبتو", "#العملات_الرقمية"],
    "morning": ["#ملخص_السوق", "#العملات_الرقمية"],
}
BRAND_TAG = "#كوين_بابا"


def _mentions(low: str, word: str) -> bool:
    # Arabic words carry prefixes (ال، لل، ب، و): plain search. Latin tickers (eth, btc): whole word only.
    if not word.isascii():
        return word in low
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low) is not None


def hashtags_for(text: str, kind: str, max_tags: int = 5) -> List[str]:
    low = text.lower()
    tags: List[str] = [BRAND_TAG]
    for words, tag in TOPIC_TAGS:
        if len(tags) >= max_tags - 1:
            break
        if any(_mentions(low, w) for w in words):
            tags.append(tag)
    for tag in KIND_TAGS.get(kind, KIND_TAGS["news"]):
        if len(tags) >= max_tags:
            break
        if tag not in tags:
            tags.append(tag)
    return tags


def facebook_text(text: str, kind: str, max_tags: int = 5, is_html: bool = False) -> str:
    """Plain text for Facebook: no HTML, no links, no @channel, no existing #tags, then our hashtags."""
    plain = strip_html(text) if is_html else text
    plain = remove_links_and_mentions(plain)
    plain = re.sub(r"(?<!\w)#[\w_]+", "", plain)  # Telegram hashtags are replaced by ours
    plain = "\n".join(line.rstrip() for line in plain.splitlines()).strip()
    return plain + "\n\n" + " ".join(hashtags_for(plain, kind, max_tags))


class FacebookPage:
    def __init__(self, page_id: str, token: str, cfg: dict, tz: str, dry_run: bool = False,
                 alert: Optional[Callable[[str], Awaitable[None]]] = None):
        self.page_id = page_id
        self.token = token
        self.cfg = cfg
        self.tz = ZoneInfo(tz)
        self.dry_run = dry_run
        self.alert = alert
        version = str(cfg.get("graph_version") or "v27.0")
        self.base = f"https://graph.facebook.com/{version}/"
        self._client = httpx.AsyncClient(timeout=120)
        self._dry_posts: List[float] = []

    # --- settings -----------------------------------------------------------
    @property
    def daily_max(self) -> int:
        return int(self.cfg.get("daily_max", 10))

    @property
    def min_gap_minutes(self) -> float:
        return float(self.cfg.get("min_gap_minutes", 45))

    @property
    def news_min_importance(self) -> int:
        return int(self.cfg.get("news_min_importance", 7))

    def allowed_kinds(self) -> List[str]:
        return list(self.cfg.get("kinds") or ["news", "analysis", "lesson", "morning"])

    # --- Page state ---------------------------------------------------------
    async def _recent_post_times(self) -> List[float]:
        """Unix times of the Page's posts since local midnight (read from Facebook itself)."""
        if self.dry_run:
            return list(self._dry_posts)
        midnight = datetime.now(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        resp = await self._client.get(self.base + f"{self.page_id}/published_posts", params={
            "fields": "created_time", "since": int(midnight.timestamp()), "limit": 100,
            "access_token": self.token})
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "unknown error"))
        out = []
        for post in data.get("data", []):
            ts = datetime.strptime(post["created_time"], "%Y-%m-%dT%H:%M:%S%z")
            out.append(ts.timestamp())
        return out

    def reason_to_skip(self, kind: str, importance: Optional[int], times: List[float],
                       now: Optional[float] = None) -> Optional[str]:
        now = now or time.time()
        if kind not in self.allowed_kinds():
            return f"kind {kind} not enabled for Facebook"
        if kind == "news" and importance is not None and importance < self.news_min_importance:
            return f"importance {importance} < {self.news_min_importance}"
        if len(times) >= self.daily_max:
            return f"daily max {self.daily_max} reached"
        # Scheduled posts (morning brief, lessons) ignore the gap: they are spread over the day already.
        if kind == "news" and times and now - max(times) < self.min_gap_minutes * 60:
            return f"last post less than {self.min_gap_minutes:.0f} min ago"
        return None

    # --- publishing ---------------------------------------------------------
    async def maybe_post(self, text: str, image: Optional[bytes], kind: str = "news",
                         importance: Optional[int] = None, is_html: bool = False) -> Optional[str]:
        """Posts to the Page if the rules allow it. Never raises: returns the post id or None."""
        try:
            times = await self._recent_post_times()
            why = self.reason_to_skip(kind, importance, times)
            if why:
                log.info("Facebook: skipped (%s)", why)
                return None
            message = facebook_text(text, kind, int(self.cfg.get("max_hashtags", 5)), is_html)
            post_id = await self._publish(message, image)
        except Exception as exc:
            log.warning("Facebook post failed: %s", exc)
            if self.alert:
                await self.alert(f"مشكل في النشر على فيسبوك: {str(exc)[:300]}")
            return None
        if image and self.cfg.get("stories"):
            try:
                await self._publish_story(image)
            except Exception as exc:  # the feed post is already out: a story problem is only reported
                log.warning("Facebook story failed: %s", exc)
                if self.alert:
                    await self.alert(f"مشكل في نشر الستوري على فيسبوك: {str(exc)[:300]}")
        return post_id

    async def _publish_story(self, image: bytes) -> Optional[str]:
        """Same picture as a Page story: upload it unpublished (a story needs its own copy), then publish."""
        if self.dry_run:
            print("[DRY RUN Facebook] + story with the same image")
            return "dry-run-story"
        resp = await self._client.post(self.base + f"{self.page_id}/photos",
                                       data={"published": "false", "access_token": self.token},
                                       files={"source": ("story.jpg", image)})
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "unknown error"))
        resp = await self._client.post(self.base + f"{self.page_id}/photo_stories",
                                       data={"photo_id": data["id"], "access_token": self.token})
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "unknown error"))
        story_id = data.get("post_id") or data.get("id")
        log.info("Facebook: story published %s", story_id)
        return story_id

    async def _publish(self, message: str, image: Optional[bytes]) -> Optional[str]:
        if self.dry_run:
            self._dry_posts.append(time.time())
            print("\n" + "-" * 60 + f"\n[DRY RUN Facebook] {'with image' if image else 'text only'}\n"
                  + message + "\n" + "-" * 60)
            return "dry-run"
        if image:
            resp = await self._client.post(self.base + f"{self.page_id}/photos",
                                           data={"message": message, "access_token": self.token},
                                           files={"source": ("photo.jpg", image)})
        else:
            resp = await self._client.post(self.base + f"{self.page_id}/feed",
                                           data={"message": message, "access_token": self.token})
        data = resp.json()
        if "error" in data:
            raise RuntimeError(data["error"].get("message", "unknown error"))
        post_id = data.get("post_id") or data.get("id")
        log.info("Facebook: published %s", post_id)
        return post_id

    async def close(self) -> None:
        await self._client.aclose()


def facebook_from_settings(settings, alert=None) -> Optional[FacebookPage]:
    cfg = settings.facebook or {}
    if not cfg.get("enabled"):
        return None
    if not settings.dry_run and not (settings.fb_page_id and settings.fb_page_token):
        log.warning("Facebook enabled but FB_PAGE_ID / FB_PAGE_TOKEN missing in .env: skipped")
        return None
    return FacebookPage(settings.fb_page_id, settings.fb_page_token, cfg, settings.timezone,
                        settings.dry_run, alert)
