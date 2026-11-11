"""The gamemaster's own picture of a kind of creature, per world (documentation/COMBAT.md §5).

One image per creature kind (``Goblin|MM``), kept in the world's folder beside its other
media, at ``media/creatures/<name>--<source>.<ext>`` — named after the creature, so no
index is needed and a look in the folder says which picture is whose. A fight outside
any world keeps its pictures in the library's own ``media/creatures/``.

Where one is set it takes the place of the books' picture in the players' Details. The
upload rules are the world media's (``app/services/media.py``): an image type its bytes
confirm, no SVG, at most 20 MB.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.media import IMAGE_TYPES, checked_image, write_image
from app.store.prose import MEDIA_DIR, ProseStore

FOLDER = "creatures"
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(ref: str) -> str:
    """``Goblin|MM`` → ``goblin--mm``: the picture's file name without its extension."""
    name, _, source = ref.partition("|")
    parts = [_SLUG.sub("-", part.lower()).strip("-") for part in (name, source)]
    return "--".join(part for part in parts if part)[:120] or "creature"


class CreaturePictures:
    """Reads, sets and removes the pictures."""

    def __init__(self, prose: ProseStore, library_media: Path) -> None:
        """``prose`` finds a world's folder; ``library_media`` serves a fight without one."""
        self._prose = prose
        self._library_media = library_media

    def folder(self, world_id: str | None) -> Path | None:
        """Where a world's creature pictures live; None for a world that is gone."""
        if world_id is None:
            return self._library_media / FOLDER
        world = self._prose.world_folder(world_id)
        return world / MEDIA_DIR / FOLDER if world is not None else None

    def find(self, world_id: str | None, ref: str) -> Path | None:
        """The picture of ``ref`` in this world, or None."""
        folder = self.folder(world_id)
        if folder is None:
            return None
        stem = slug(ref)
        for suffix in IMAGE_TYPES:
            path = folder / f"{stem}{suffix}"
            if path.is_file():
                return path
        return None

    def version(self, world_id: str | None, ref: str) -> int | None:
        """A number that changes with the picture (its modification time), for the
        address the browser caches it under; None without a picture."""
        path = self.find(world_id, ref)
        return path.stat().st_mtime_ns if path is not None else None

    def set(self, world_id: str | None, ref: str, filename: str, data: bytes) -> Path:
        """Store an upload as the picture of ``ref``, replacing the one before it
        (whatever its type). Raises :class:`~app.services.media.MediaError`."""
        suffix = checked_image(filename, data)
        folder = self.folder(world_id)
        if folder is None:
            raise FileNotFoundError(world_id)
        target = folder / f"{slug(ref)}{suffix}"
        write_image(target, data)
        for other in IMAGE_TYPES:
            if other != suffix:
                (folder / f"{slug(ref)}{other}").unlink(missing_ok=True)
        return target

    def remove(self, world_id: str | None, ref: str) -> None:
        """The books' picture shows again."""
        found = self.find(world_id, ref)
        if found is not None:
            found.unlink(missing_ok=True)


__all__ = ["FOLDER", "CreaturePictures", "slug"]
