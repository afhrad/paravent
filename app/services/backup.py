"""Backups of the library — the user's own data — on a schedule (``.env``).

Only ``PARAVENT_LIBRARY_DIR`` is backed up: worlds, adventures, chapters, characters,
combats, encounters, users and sessions, homebrew, media, translations and the log.
The 5eTools corpus is somebody else's replaceable snapshot and the reference cache is
disposable (documentation/TECHSTACK.md §3), so neither is ever included.

* ``PARAVENT_BACKUP_DIR`` — where the zip files go. Empty: no backups.
* ``PARAVENT_BACKUP_AT`` — when, as local times of day: ``03:00`` or ``03:00,15:00``.
  Empty: only on demand (``python -m app.cli backup run``).
* ``PARAVENT_BACKUP_KEEP`` — how many to keep; older ones are deleted (default 14).

A backup is ``paravent-library-2026-10-26_030000.zip``, the library's files at their
paths plus a ``paravent-backup.json`` manifest. Each file is read under the lock its
writer takes (app/store/jsonfile.py), so no file is caught half-written; the zip is
written under a temporary name and renamed when complete. A scheduled backup is skipped
when nothing in the library changed since the last one.

It holds password hashes and the session signing key (``.secret_key``), so the files
are created readable by their owner only. To restore: stop Paravent, unzip into an empty
library folder (leaving out ``paravent-backup.json``), start it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from app.store.jsonfile import file_lock

logger = logging.getLogger(__name__)

PREFIX = "paravent-library-"
MANIFEST = "paravent-backup.json"
STAMP = "%Y-%m-%d_%H%M%S"
_NAME = re.compile(rf"^{PREFIX}\d{{4}}-\d{{2}}-\d{{2}}_\d{{6}}\.zip$")
# Not data: a write in progress, and the files macOS and Windows drop into folders.
_SKIP = re.compile(r"^(\..+\.tmp|\.DS_Store|Thumbs\.db)$")


class BackupError(ValueError):
    """A backup setting that cannot work: a bad time, a folder inside the library."""


def parse_times(text: str) -> tuple[time, ...]:
    """``"03:00, 15:30"`` → the times of day, sorted; empty for ``""``."""
    out: set[time] = set()
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        found = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if not found or int(found.group(1)) > 23 or int(found.group(2)) > 59:
            raise BackupError(f"PARAVENT_BACKUP_AT: {part!r} is not a time like 03:00")
        out.add(time(int(found.group(1)), int(found.group(2))))
    return tuple(sorted(out))


def next_run(times: tuple[time, ...], now: datetime) -> datetime | None:
    """The first of ``times`` after ``now`` — today, or tomorrow's first."""
    if not times:
        return None
    for at in times:
        candidate = datetime.combine(now.date(), at)
        if candidate > now:
            return candidate
    return datetime.combine(now.date() + timedelta(days=1), times[0])


@dataclass(frozen=True)
class Backup:
    """One backup file."""

    path: Path
    made: datetime
    size: int


class BackupService:
    """Zips the library into the backup folder and keeps the newest few."""

    def __init__(self, library: Path, target: Path | None, keep: int = 14, at: str = "") -> None:
        """Back ``library`` up into ``target`` (None: off), keeping ``keep``, at the
        times of day in ``at``."""
        self.library = library
        self.target = target
        self.keep = max(1, keep)
        self.times = parse_times(at)
        self._lock = threading.Lock()
        if target is not None:
            resolved, library_root = target.resolve(), library.resolve()
            if resolved == library_root or library_root in resolved.parents:
                raise BackupError(
                    f"PARAVENT_BACKUP_DIR {target} lies inside the library {library}: "
                    "every backup would contain the ones before it"
                )

    @property
    def enabled(self) -> bool:
        """True when a backup folder is set."""
        return self.target is not None

    def next_due(self, now: datetime | None = None) -> datetime | None:
        """When the next scheduled backup is due; None without a folder or times."""
        return next_run(self.times, now or datetime.now()) if self.enabled else None

    def backups(self) -> list[Backup]:
        """The backups in the folder, newest first."""
        if self.target is None or not self.target.is_dir():
            return []
        found = []
        for path in self.target.iterdir():
            if _NAME.match(path.name):
                made = datetime.strptime(path.name[len(PREFIX) : -4], STAMP)
                found.append(Backup(path, made, path.stat().st_size))
        return sorted(found, key=lambda b: b.made, reverse=True)

    def _files(self) -> Iterator[Path]:
        for path in sorted(self.library.rglob("*")):
            if path.is_file() and not path.is_symlink() and not _SKIP.match(path.name):
                yield path

    def fingerprint(self) -> str:
        """Every file's path, modification time and size, hashed: what a backup holds."""
        lines = []
        for path in self._files():
            info = path.stat()
            lines.append(_line(path.relative_to(self.library).as_posix(), info))
        return _digest(lines)

    def run(self, *, force: bool = True, now: datetime | None = None) -> Backup | None:
        """Make a backup; with ``force=False`` none when nothing changed since the last.

        Returns the backup made, or None when skipped.
        """
        if self.target is None:
            raise BackupError("PARAVENT_BACKUP_DIR is not set")
        with self._lock:
            now = now or datetime.now()
            previous = self.backups()
            if not force and previous and not self._changed(previous[0]):
                logger.info("Backup skipped: nothing changed since %s", previous[0].path.name)
                return None
            self.target.mkdir(parents=True, exist_ok=True)
            final = self.target / f"{PREFIX}{now.strftime(STAMP)}.zip"
            partial = final.with_name(f".{final.name}.part")
            count, size = self._write(partial, now)
            os.replace(partial, final)
            backup = Backup(final, now.replace(microsecond=0), final.stat().st_size)
            logger.info("Backup %s: %d files, %d bytes of data", final.name, count, size)
            self._prune()
            return backup

    def _changed(self, last: Backup) -> bool:
        """True unless the library is file for file what ``last`` recorded."""
        try:
            with zipfile.ZipFile(last.path) as archive:
                recorded = json.loads(archive.read(MANIFEST)).get("fingerprint")
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            return True
        return bool(recorded != self.fingerprint())

    def _write(self, partial: Path, now: datetime) -> tuple[int, int]:
        count = size = 0
        lines: list[str] = []
        # Owner-only: the library holds password hashes and the signing key.
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with (
                os.fdopen(descriptor, "wb") as handle,
                zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive,
            ):
                for path in self._files():
                    with file_lock(path):
                        info = path.stat()
                        data = path.read_bytes()
                    name = path.relative_to(self.library).as_posix()
                    lines.append(_line(name, info))
                    modified = info.st_mtime
                    # The file's own time, so a restored library keeps it.
                    entry = zipfile.ZipInfo(
                        name,
                        date_time=datetime.fromtimestamp(max(modified, 315532800)).timetuple()[:6],
                    )
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = 0o600 << 16
                    archive.writestr(entry, data)
                    count += 1
                    size += len(data)
                manifest = {
                    "made": now.isoformat(timespec="seconds"),
                    "library": str(self.library),
                    "files": count,
                    "bytes": size,
                    # What the next scheduled run compares with, to skip an unchanged
                    # library: path, modification time and size of every file.
                    "fingerprint": _digest(lines),
                }
                archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return count, size

    def _prune(self) -> None:
        for old in self.backups()[self.keep :]:
            old.path.unlink(missing_ok=True)
            logger.info("Backup %s removed (keeping %d)", old.path.name, self.keep)

    async def schedule(self) -> None:
        """Back up at each of the configured times, until cancelled."""
        while True:
            due = next_run(self.times, datetime.now())
            if due is None or self.target is None:
                return
            await asyncio.sleep(max(0.0, (due - datetime.now()).total_seconds()))
            try:
                await asyncio.to_thread(self.run, force=False)
            except (OSError, zipfile.BadZipFile) as exc:
                logger.error("Backup failed: %s", exc)


def _line(name: str, info: os.stat_result) -> str:
    return f"{name}\t{info.st_mtime_ns}\t{info.st_size}"


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


__all__ = ["Backup", "BackupError", "BackupService", "next_run", "parse_times"]
