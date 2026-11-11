"""The log's files: append only, one per world (documentation/PROTOCOL.md §3).

``library/protocol/<world id>.jsonl`` holds a world's entries, ``global.jsonl`` those
with no world — one JSON object per line, oldest first. An entry is appended under the
file's lock, flushed and fsynced; nothing is ever rewritten, so a roll costs one short
write however long the evening has been. This is the one library collection that is not
a JSON object rewritten whole (TECHSTACK.md §3): that rule never had to pay for a file
that grows with every click.

A crash mid-append leaves at most one torn last line. Reading skips it (and any line a
hand edit broke) and counts it; the next append starts on a fresh line, so a torn line
never swallows the entry after it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.models.protocol import ProtocolEntry
from app.store.jsonfile import file_lock

logger = logging.getLogger(__name__)

GLOBAL = "global"
# A world id, or "global": nothing that could climb out of the directory.
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def log_key(world_id: str | None) -> str:
    """The file a world's entries go to: its id, or ``global`` for none."""
    return world_id or GLOBAL


@dataclass
class _Loaded:
    """One file as read: its entries and what it looked like on disk then."""

    stamp: tuple[int, int]
    entries: list[ProtocolEntry] = field(default_factory=list)
    broken: int = 0


class ProtocolStore:
    """Appends entries and reads them back, newest first, per world."""

    def __init__(self, root: Path) -> None:
        """``root`` is ``library/protocol``; created on the first append."""
        self._root = root
        self._loaded: dict[str, _Loaded] = {}
        self._listeners: list[Callable[[str, ProtocolEntry], None]] = []

    def subscribe(self, listener: Callable[[str, ProtocolEntry], None]) -> None:
        """Call ``listener(key, entry)`` after every append."""
        self._listeners.append(listener)

    def path(self, key: str) -> Path:
        """The file for ``key`` (a world id or ``global``)."""
        if not _KEY.match(key):
            raise ValueError(f"not a log key: {key!r}")
        return self._root / f"{key}.jsonl"

    def append(self, entry: ProtocolEntry) -> None:
        """Write ``entry`` to the end of its world's file, durably."""
        key = log_key(entry.scope.world)
        path = self.path(key)
        line = entry.model_dump_json(exclude_defaults=True) + "\n"
        with file_lock(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            known = self._loaded.get(key)
            unchanged = known is not None and known.stamp == _stamp(path)
            with path.open("ab") as handle:
                # A torn last line (a crash mid-append) must not swallow this entry.
                if handle.tell() and not _ends_with_newline(path):
                    handle.write(b"\n")
                handle.write(line.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if unchanged and known is not None:
                known.entries.append(entry)
                known.stamp = _stamp(path)
            else:
                self._loaded.pop(key, None)
        for listener in self._listeners:
            listener(key, entry)

    def append_all(self, entries: Iterable[ProtocolEntry]) -> None:
        """Append several, in order."""
        for entry in entries:
            self.append(entry)

    def entries(self, key: str) -> list[ProtocolEntry]:
        """Every entry of ``key``'s file, oldest first. Re-read when the file changed
        on disk (a hand edit, another process), otherwise kept in memory."""
        path = self.path(key)
        stamp = _stamp(path)
        known = self._loaded.get(key)
        if known is None or known.stamp != stamp:
            known = self._read(path, stamp)
            self._loaded[key] = known
        return known.entries

    def newest(
        self,
        key: str,
        limit: int = 50,
        where: Callable[[ProtocolEntry], bool] | None = None,
    ) -> list[ProtocolEntry]:
        """Up to ``limit`` entries, newest first, that ``where`` accepts."""
        found: list[ProtocolEntry] = []
        for entry in reversed(self.entries(key)):
            if where is None or where(entry):
                found.append(entry)
                if len(found) >= limit:
                    break
        return found

    def broken(self, key: str) -> int:
        """How many lines of ``key``'s file could not be read (and were skipped)."""
        self.entries(key)
        known = self._loaded.get(key)
        return known.broken if known is not None else 0

    def logs(self) -> list[str]:
        """Every log on disk: world ids and ``global``."""
        if not self._root.exists():
            return []
        return sorted(p.stem for p in self._root.glob("*.jsonl") if _KEY.match(p.stem))

    @staticmethod
    def _read(path: Path, stamp: tuple[int, int]) -> _Loaded:
        loaded = _Loaded(stamp)
        if not path.exists():
            return loaded
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    loaded.entries.append(ProtocolEntry.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValidationError) as exc:
                    loaded.broken += 1
                    logger.warning("%s line %d skipped: %s", path.name, number, exc)
        return loaded


def _stamp(path: Path) -> tuple[int, int]:
    """Size and modification time: what tells a changed file from a known one."""
    try:
        status = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (status.st_size, status.st_mtime_ns)


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) == b"\n"


__all__ = ["GLOBAL", "ProtocolStore", "log_key"]
