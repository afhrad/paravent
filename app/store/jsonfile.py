"""Atomic JSON file primitives.

Everything Paravent persists lives in JSON files rather than a database daemon. That
puts the durability burden here: a half-written ``game.json`` after a crash mid-combat
loses the session. Every write therefore goes to a temporary file in the same
directory, is flushed to disk, and is then renamed over the target — ``os.replace`` is
atomic on POSIX and Windows, so a reader sees either the old file or the new one, never
a truncated one.

Writes are serialised per-path by a lock, because two HTTP requests touching the same
combat tracker would otherwise interleave read-modify-write cycles and lose an update.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# One lock per resolved path, created on demand. A dict of locks needs its own lock to
# stay race-free during creation.
_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    """Return the process-wide write lock for ``path``, creating it if needed."""
    resolved = path.resolve()
    with _locks_guard:
        lock = _locks.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _locks[resolved] = lock
        return lock


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold the write lock for ``path`` across a read-modify-write cycle."""
    with _lock_for(path):
        yield


def read_json(path: Path, default: Any = None) -> Any:
    """Read and parse ``path``.

    Returns ``default`` when the file does not exist, so callers can treat a missing
    game-state file as an empty one without a separate existence check.
    """
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write ``data`` to ``path`` atomically.

    The temporary file is created in the destination directory so that the rename stays
    within one filesystem — ``os.replace`` across devices is not atomic and would raise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")

    with _lock_for(path):
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=indent, ensure_ascii=False, sort_keys=False)
                handle.write("\n")
                # flush + fsync before the rename: without this the rename can land
                # before the data does, and a power loss leaves an empty file.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


@contextmanager
def update_json(path: Path, default: Any = None) -> Iterator[Any]:
    """Read, yield for mutation, then atomically write back.

    The lock is held for the whole cycle, so concurrent updates to one file serialise
    instead of clobbering each other::

        with update_json(path, default={}) as state:
            state["hp"] = 12
    """
    with _lock_for(path):
        data = read_json(path, default)
        yield data
        write_json(path, data)


def iter_json_files(root: Path, pattern: str = "*.json") -> Iterator[Path]:
    """Yield JSON files under ``root``, skipping our own temp files and dot-dirs."""
    if not root.exists():
        return
    for path in sorted(root.rglob(pattern)):
        if path.name.startswith("."):
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        yield path
