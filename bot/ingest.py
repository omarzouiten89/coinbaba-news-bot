"""Shared step for every source: first time a source is seen, remember its current posts
without publishing them (no old backlog); afterwards, submit every item."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, List

from .models import Item
from .storage import Storage

log = logging.getLogger(__name__)


async def ingest(storage: Storage, flag: str, name: str, items: List[Item],
                 submit: Callable[[Item], Awaitable[None]]) -> None:
    if storage.get(flag) is None:
        for item in items:
            storage.mark_seen(item.key)
        storage.set(flag, "1")
        log.info("%s initialised with %d existing posts (not published)", name, len(items))
        return
    for item in items:
        await submit(item)
