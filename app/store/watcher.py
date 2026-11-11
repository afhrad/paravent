"""Filesystem watcher that reloads stores when their JSON changes.

documentation/PROGRESS.md requires a watcher on *all* JSON files so that a hand-edit is picked up
without a restart. The watcher runs as a background task on the FastAPI event loop and
calls back into whatever wants to reload.

Two properties matter and are easy to get wrong:

- **Debounce.** An editor writing a file produces several events; our own atomic writes
  produce a create-and-rename pair. Reloading on each would reload the corpus several
  times per save. Events are therefore coalesced over a short window.
- **Ignore our own writes.** ``write_json`` creates a ``.name.tmp`` file next to the
  target. Watching it would make every game-state write trigger a compendium reload, so
  dotfiles are filtered out.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchfiles import awatch

logger = logging.getLogger(__name__)

ReloadCallback = Callable[[set[Path]], Awaitable[None] | None]


WATCHED_SUFFIXES = frozenset({".json", ".md"})


def _is_relevant(change_path: str) -> bool:
    """True for JSON and Markdown files that are not our own temporary write files."""
    path = Path(change_path)
    if path.suffix.lower() not in WATCHED_SUFFIXES:
        return False
    # ``.users.json.tmp`` and any dot-prefixed path are ours or the editor's.
    return not any(part.startswith(".") for part in path.parts)


class JsonWatcher:
    """Watches directories for JSON changes and invokes a callback, debounced."""

    def __init__(
        self,
        roots: list[Path],
        on_change: ReloadCallback,
        *,
        debounce_ms: int = 400,
    ) -> None:
        """``roots`` are the directories to watch; ``on_change`` receives changed paths."""
        self._roots = [r for r in roots if r.exists()]
        self._on_change = on_change
        self._debounce_ms = debounce_ms
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        """Begin watching in the background."""
        if self._task is not None:
            return
        if not self._roots:
            logger.info("File watcher not started: no existing roots to watch")
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="paravent-json-watcher")
        logger.info("Watching %s for JSON changes", ", ".join(str(r) for r in self._roots))

    async def stop(self) -> None:
        """Stop watching and wait for the task to finish."""
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        """The watch loop. Errors are logged but never kill the watcher."""
        try:
            async for changes in awatch(
                *self._roots,
                stop_event=self._stop,
                debounce=self._debounce_ms,
                watch_filter=lambda _change, path: _is_relevant(path),
            ):
                paths = {Path(p) for _, p in changes}
                if not paths:
                    continue
                logger.info("Detected %d JSON change(s); reloading", len(paths))
                try:
                    result = self._on_change(paths)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    # A failed reload must not stop the watcher; the old data stays
                    # live and the next save gets another chance.
                    logger.exception("Reload callback failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("File watcher stopped unexpectedly")


__all__ = ["JsonWatcher"]
