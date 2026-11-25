"""Hybrid mode: while the Mac bot runs, it tells GitHub "I'm alive" every few minutes.

It force-pushes one empty commit (dated now) to the branch `heartbeat` of the bot's own
repository. The GitHub job reads that date and skips its run while the Mac is alive.
No file of the project is changed and the history of `main` is not touched.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

BRANCH = "heartbeat"


def _git(repo: Path, *args: str, stdin: str = "") -> str:
    env = dict(os.environ,
               GIT_AUTHOR_NAME="coinbaba-bot", GIT_AUTHOR_EMAIL="coinbaba-bot@users.noreply.github.com",
               GIT_COMMITTER_NAME="coinbaba-bot", GIT_COMMITTER_EMAIL="coinbaba-bot@users.noreply.github.com")
    out = subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True, text=True,
                         env=env, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300])
    return out.stdout.strip()


def send_heartbeat(repo: Path) -> None:
    empty_tree = _git(repo, "mktree")
    commit = _git(repo, "commit-tree", empty_tree, "-m", "mac alive")
    _git(repo, "push", "--force", "--quiet", "origin", f"{commit}:refs/heads/{BRANCH}")


async def heartbeat_loop(repo: Path, every_seconds: int = 300) -> None:
    failures = 0
    while True:
        try:
            await asyncio.to_thread(send_heartbeat, repo)
            failures = 0
            log.info("Heartbeat sent to GitHub (Mac is the active bot)")
        except Exception as exc:
            failures += 1
            if failures in (1, 12):
                log.warning("Heartbeat failed (%s); GitHub may also post meanwhile", exc)
        await asyncio.sleep(every_seconds)
