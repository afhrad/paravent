"""The gamemaster's own picture of a kind of creature, per world (documentation/COMBAT.md §5).

One image per creature kind (``Goblin|MM``), kept in the world's folder beside its other
media, at ``media/creatures/<name>--<source>.<ext>`` — named after the creature, so no
index is needed and a look in the folder says which picture is whose. A fight outside
any world keeps its pictures in the library's own ``media/creatures/``.

Where one is set it takes the place of the books' picture in the players' Details. The
upload rules are the world media's (``app/services/media.py``): an image type its bytes
confirm, no SVG, at most 20 MB.

A creature of the builder has its picture under the creature it publishes as
(``Moorhulk|Homebrew``), in its design's world, or in the library's folder for a design
of no world, which then serves every world. It can be uploaded, or fetched once from a
web address (:func:`fetch`, what Claude uses over MCP).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app import __version__
from app.services.creature_builder import HOMEBREW_SOURCE
from app.services.media import (
    IMAGE_TYPES,
    MAX_BYTES,
    MediaError,
    checked_image,
    sniff,
    write_image,
)
from app.store.prose import MEDIA_DIR, ProseStore

FOLDER = "creatures"
FETCH_TIMEOUT = 20.0
# Image hosts such as Wikimedia refuse a client that does not say who it is.
USER_AGENT = f"Paravent/{__version__} (+https://paravent.org)"
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(ref: str) -> str:
    """``Goblin|MM`` → ``goblin--mm``: the picture's file name without its extension."""
    name, _, source = ref.partition("|")
    parts = [_SLUG.sub("-", part.lower()).strip("-") for part in (name, source)]
    return "--".join(part for part in parts if part)[:120] or "creature"


def design_ref(name: str) -> str:
    """The creature a builder design publishes as: ``Moorhulk|Homebrew``."""
    return f"{name}|{HOMEBREW_SOURCE}"


def _suffix(content_type: str) -> str:
    """``.webp`` for ``image/webp``; the first extension of a type."""
    return next((s for s, t in IMAGE_TYPES.items() if t == content_type), "")


async def fetch(url: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, bytes]:
    """Download an image once: a file name to store it under and its bytes. Only
    ``http``/``https``, at most 20 MB, and only what its bytes say is an image; raises
    :class:`~app.services.media.MediaError` otherwise. ``transport`` is for tests."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise MediaError("media.error.address")
    data = bytearray()
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                timeout=FETCH_TIMEOUT,
                transport=transport,
                headers={"User-Agent": USER_AGENT},
            ) as client,
            client.stream("GET", url.strip()) as response,
        ):
            if response.status_code >= 400:
                raise MediaError("media.error.fetch", status=str(response.status_code))
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    raise MediaError("media.error.size", mb=MAX_BYTES // (1024 * 1024))
    except httpx.HTTPError as exc:
        raise MediaError("media.error.fetch", status=type(exc).__name__) from exc
    found = sniff(bytes(data))
    if found is None:
        raise MediaError("media.error.content")
    return f"picture{_suffix(found)}", bytes(data)


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

    def find(self, world_id: str | None, ref: str, shared: bool = True) -> Path | None:
        """The picture of ``ref`` in this world, or None. With ``shared`` a world without
        one of its own takes the library's (a builder design of no world)."""
        folders = [self.folder(world_id)]
        if shared and world_id is not None:
            folders.append(self.folder(None))
        stem = slug(ref)
        for folder in folders:
            if folder is None:
                continue
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
        found = self.find(world_id, ref, shared=False)
        if found is not None:
            found.unlink(missing_ok=True)

    def move(self, world_id: str | None, ref: str, to_world: str | None, to_ref: str) -> None:
        """Keep a picture with its creature when the creature is renamed or moves to
        another world (a builder design); nothing to do without one."""
        found = self.find(world_id, ref, shared=False)
        target = self.folder(to_world)
        if found is None or target is None or (world_id, ref) == (to_world, to_ref):
            return
        target.mkdir(parents=True, exist_ok=True)
        for suffix in IMAGE_TYPES:
            (target / f"{slug(to_ref)}{suffix}").unlink(missing_ok=True)
        found.replace(target / f"{slug(to_ref)}{found.suffix}")


__all__ = ["FOLDER", "CreaturePictures", "design_ref", "fetch", "slug"]
