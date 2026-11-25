"""Small text helpers: cleaning, fingerprinting, length limits, HTML formatting."""
from __future__ import annotations

import hashlib
import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+)", re.IGNORECASE)
_MENTION_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,}")
_SPACES_RE = re.compile(r"[ \t ]+")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")


def strip_html(text: str) -> str:
    text = re.sub(r"<\s*br\s*/?>|</p>", "\n", text or "", flags=re.IGNORECASE)
    text = html.unescape(_TAG_RE.sub("", text))
    return tidy(text)


def tidy(text: str) -> str:
    lines = [_SPACES_RE.sub(" ", line).strip() for line in (text or "").splitlines()]
    return _MANY_NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def remove_links_and_mentions(text: str) -> str:
    """Removes any URL, t.me link and @username so the post never points to another channel."""
    text = _URL_RE.sub("", text or "")
    text = _MENTION_RE.sub("", text)
    # Lines that became empty labels like "👉" or "Follow:" are dropped.
    cleaned = []
    for line in text.splitlines():
        if line.strip() and not re.search(r"\w", line):
            continue
        cleaned.append(line)
    return tidy("\n".join(cleaned))


def fingerprint(text: str) -> str:
    """Same news copied word-for-word by several channels gets the same fingerprint."""
    text = remove_links_and_mentions(text).lower()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    text = " ".join(text.split())
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def fit(text: str, limit: int) -> str:
    """Cuts text to `limit` characters at a clean boundary (line, sentence or word)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    for sep in ("\n", ". ", "۔ ", "، ", " "):
        pos = cut.rfind(sep)
        if pos > limit * 0.6:
            cut = cut[:pos]
            break
    return cut.rstrip(" ,،.") + "…"


def to_telegram_html(text: str) -> str:
    """Escapes the text for Telegram HTML mode and makes the first line (the headline) bold."""
    lines = text.strip().split("\n", 1)
    headline = html.escape(lines[0])
    rest = html.escape(lines[1]) if len(lines) > 1 else ""
    return f"<b>{headline}</b>" + (f"\n{rest}" if rest else "")
