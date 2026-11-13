"""A parsed snapshot of the corpus, so a restart need not read it again (TECHSTACK.md §3).

Reading the corpus means parsing ~114 MB of JSON, resolving copies, validating every
entry and making the magic-variant items; the fluff alone takes a second. The result is
kept in ``reference-cache/`` (derived data, disposable) as one pickle, and the next start
takes it when nothing it was made from has changed: every JSON file under the
compendium's roots (the corpus and homebrew) by path, size and modification time, the
app's version and Python's, and the code that reads the corpus (its models and stores, by
content, so a change in development counts before any commit raises the version).
Anything else — a new release, a published creature, an edited file, an update of
Paravent — makes a new one. A snapshot that cannot be read is ignored and replaced;
writing one that fails costs nothing but the next start's time.

Only this app writes and reads it, in its own cache folder: the pickle is never taken
from anywhere else.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import platform
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PREFIX = "corpus-snapshot-"


def fingerprint(roots: Iterable[Path], version: str, code: Iterable[Path] = ()) -> str:
    """What a snapshot is made from, as one key: every JSON file under ``roots`` (path,
    size, modification time), the app's version and Python's, and the content of every
    Python file under ``code``."""
    digest = hashlib.sha256(f"{version}|{platform.python_version()}".encode())
    for folder in code:
        for path in sorted(folder.rglob("*.py")):
            try:
                digest.update(f"code|{path.name}|".encode() + path.read_bytes())
            except OSError:
                continue
    for root in roots:
        if not root.is_dir():
            continue
        digest.update(f"root|{root.resolve()}".encode())
        for path in sorted(root.rglob("*.json")):
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:32]


class Snapshot:
    """The snapshot for one state of the roots, in ``folder``."""

    def __init__(self, folder: Path, key: str) -> None:
        self.folder = folder
        self.path = folder / f"{PREFIX}{key}.pickle"

    def read(self) -> Any | None:
        """What was saved for this state, or None when there is none or it is unreadable."""
        try:
            with self.path.open("rb") as handle:
                return pickle.load(handle)  # noqa: S301 - our own file, in our own cache
        except FileNotFoundError:
            return None
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError) as exc:
            logger.info("Corpus snapshot %s not read: %s", self.path.name, exc)
            return None

    def write(self, state: Any) -> None:
        """Save ``state`` for this state of the roots, atomically, and remove older
        snapshots. A failure is logged and otherwise ignored."""
        temp: str | None = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(dir=self.folder, prefix=".snapshot-")
            with os.fdopen(handle, "wb") as out:
                pickle.dump(state, out, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temp, self.path)
            temp = None
        except (OSError, pickle.PicklingError, TypeError) as exc:
            logger.warning("Corpus snapshot not written: %s", exc)
            return
        finally:
            if temp is not None:
                Path(temp).unlink(missing_ok=True)
        for old in self.folder.glob(f"{PREFIX}*.pickle"):
            if old != self.path:
                old.unlink(missing_ok=True)


__all__ = ["PREFIX", "Snapshot", "fingerprint"]
