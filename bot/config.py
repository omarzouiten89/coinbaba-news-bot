"""Loads settings from .env (secrets) and config.yaml (non-secret options)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    bot_token: str
    api_id: Optional[int]
    api_hash: str
    gemini_api_key: str
    gemini_model: str
    owner_chat_id: Optional[int]
    target_channel: Union[str, int, None]
    target_channel_public: str = ""
    telegram_channels: list = field(default_factory=list)  # "@name" or {name, kind, daily_max, ...}
    rss_feeds: List[dict] = field(default_factory=list)
    daily_limit: int = 60
    timezone: str = "Africa/Casablanca"
    rss_interval_seconds: int = 180
    channel_check_seconds: int = 60
    max_item_age_hours: float = 6
    llm_min_interval_seconds: float = 7
    dry_run: bool = False
    data_dir: Path = BASE_DIR / "data"
    promo: dict = field(default_factory=dict)
    lessons: dict = field(default_factory=dict)
    polls: dict = field(default_factory=dict)
    morning: dict = field(default_factory=dict)
    mirrors: list = field(default_factory=list)
    news_min_importance: int = 0
    quiet_hours: dict = field(default_factory=dict)
    silent_news: bool = False


def _int_or_none(value: Optional[str]) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        # Never print the value itself: it may be a secret pasted in the wrong line.
        raise SystemExit("A setting in .env that must be a number contains other text "
                         "(TELEGRAM_API_ID or OWNER_CHAT_ID).")


def _channel_ref(value) -> Union[str, int, None]:
    """Channel ids like -1001234567890 become ints; usernames stay strings."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def load_settings(config_path: Optional[Path] = None) -> Settings:
    load_dotenv(BASE_DIR / ".env")
    config_path = config_path or BASE_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    data_dir = Path(os.getenv("BOT_DATA_DIR", "").strip() or BASE_DIR / "data")
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        api_id=_int_or_none(os.getenv("TELEGRAM_API_ID")),
        api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or "gemini-flash-latest,gemini-3.5-flash,gemini-flash-lite-latest,gemini-3.5-flash-lite",
        owner_chat_id=_int_or_none(os.getenv("OWNER_CHAT_ID")),
        target_channel=_channel_ref(cfg.get("target_channel")),
        target_channel_public=str(cfg.get("target_channel_public") or ""),
        telegram_channels=[c for c in (cfg.get("telegram_channels") or []) if c],
        rss_feeds=list(cfg.get("rss_feeds") or []),
        daily_limit=int(cfg.get("daily_limit", 60)),
        timezone=str(cfg.get("timezone", "Africa/Casablanca")),
        rss_interval_seconds=int(cfg.get("rss_interval_seconds", 180)),
        channel_check_seconds=int(cfg.get("channel_check_seconds", 60)),
        max_item_age_hours=float(cfg.get("max_item_age_hours", 6)),
        llm_min_interval_seconds=float(cfg.get("llm_min_interval_seconds", 7)),
        dry_run=os.getenv("DRY_RUN", "").strip() in ("1", "true", "yes"),
        data_dir=data_dir,
        promo=dict(cfg.get("daily_promo") or {}),
        lessons=dict(cfg.get("daily_lessons") or {}),
        polls=dict(cfg.get("daily_polls") or {}),
        morning=dict(cfg.get("morning_brief") or {}),
        mirrors=list(cfg.get("mirrors") or []),
        news_min_importance=int(cfg.get("news_min_importance") or 0),
        quiet_hours=dict(cfg.get("quiet_hours") or {}),
        silent_news=bool(cfg.get("silent_news", False)),
    )
