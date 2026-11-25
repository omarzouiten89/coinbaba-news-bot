"""SQLite storage: what we already saw, what we published, small key/value flags."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Union


class Storage:
    def __init__(self, path: Union[str, Path]):
        self.db = sqlite3.connect(str(path))
        if str(path) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                local_date TEXT NOT NULL,
                source TEXT,
                source_key TEXT,
                category TEXT,
                headline TEXT,
                text TEXT,
                message_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS posts_local_date ON posts(local_date);
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self.db.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- seen items -------------------------------------------------------
    def is_seen(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None

    def mark_seen(self, key: str) -> bool:
        """Returns True if the key was new."""
        cur = self.db.execute("INSERT OR IGNORE INTO seen(key, created_at) VALUES (?, ?)", (key, self._now()))
        self.db.commit()
        return cur.rowcount == 1

    def unmark_seen(self, key: str) -> None:
        self.db.execute("DELETE FROM seen WHERE key=?", (key,))
        self.db.commit()

    def prune_seen(self, days: int = 30) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.db.execute("DELETE FROM seen WHERE created_at < ?", (cutoff,))
        self.db.commit()

    # --- posts --------------------------------------------------------------
    def add_post(self, local_date: str, source: str, source_key: str, category: str,
                 headline: str, text: str, message_id: Optional[int]) -> None:
        self.db.execute(
            "INSERT INTO posts(created_at, local_date, source, source_key, category, headline, text, message_id)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (self._now(), local_date, source, source_key, category, headline, text, message_id),
        )
        self.db.commit()

    def count_posts_on(self, local_date: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM posts WHERE local_date=?", (local_date,)).fetchone()[0]

    def count_posts_by_source(self, local_date: str, source: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM posts WHERE local_date=? AND source=?",
                               (local_date, source)).fetchone()[0]

    def recent_headlines(self, hours: int = 24, limit: int = 40) -> List[str]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.db.execute(
            "SELECT headline FROM posts WHERE created_at >= ? ORDER BY id DESC LIMIT ?", (cutoff, limit)
        ).fetchall()
        return [r[0] for r in rows if r[0]]

    # --- flags --------------------------------------------------------------
    def get(self, key: str) -> Optional[str]:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def keys_with_prefix(self, prefix: str) -> List[str]:
        rows = self.db.execute("SELECT key FROM kv WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return [r[0] for r in rows]

    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, value))
        self.db.commit()
