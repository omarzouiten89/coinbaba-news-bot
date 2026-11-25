"""Shared step for every source: first time a source is seen, remember its current posts
without publishing them (no old backlog); afterwards, submit every item."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, List, Optional

from .models import Item, SourceRules
from .storage import Storage

log = logging.getLogger(__name__)


def source_entries(raw: list) -> List[tuple]:
    """config.yaml lists accept "@name" or {name: "@name", kind: analysis, daily_max: 3, ...}."""
    entries = []
    for entry in raw or []:
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("url") or "").strip()
            entries.append((name, SourceRules.from_config(entry)))
        elif str(entry).strip():
            entries.append((str(entry).strip(), SourceRules()))
    return entries


async def ingest(storage: Storage, flag: str, name: str, items: List[Item],
                 submit: Callable[[Item], Awaitable[None]], rules: Optional[SourceRules] = None) -> None:
    if rules is not None:
        for item in items:
            item.rules = rules
    if storage.get(flag) is None:
        for item in items:
            storage.mark_seen(item.key)
        storage.set(flag, "1")
        log.info("%s initialised with %d existing posts (not published)", name, len(items))
        return
    for item in items:
        await submit(item)
