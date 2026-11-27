"""A map's picture: storing the original, and cutting a large one into tiles
(documentation/MAPS.md §4).

The original is kept as uploaded in its scope's ``media/maps/``, streamed to disk rather
than read into memory, its size read from its header, so a picture too large for the
server is refused before anything decodes it. A picture up to :data:`SINGLE_LIMIT` pixels
on its long side is shown whole; a larger one is cut into a pyramid of 256 px WebP tiles,
the deepest level at the original's resolution and each above it half the one below,
down to the level where the whole map fits one tile. The tiles are disposable: they live
under the map cache, outside the library and its backups, in a folder named by the
original's digest, and are made again when that folder is missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from app.models.gamestate import WorldMap
from app.services import media
from app.services.i18n import Translatable
from app.services.media import sniff
from app.store.gamestate import GameStateStore
from app.store.prose import MEDIA_DIR, ProseStore

logger = logging.getLogger(__name__)

TILE = 256
# The longest side shown as one picture, without tiles.
SINGLE_LIMIT = 4096
# The largest file taken: the same as a world package's largest file
# (app/services/world_transfer.py), so every map a table holds can be exported.
MAX_BYTES = 200 * 1024 * 1024
MAPS_DIR = "maps"
# A large map's thumbnail, beside its tiles.
THUMB = "thumb.webp"
# The types a map may be: what Pillow reads and every browser shows.
MAP_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_CHUNK = 1024 * 1024
_NAME = re.compile(r"[^a-z0-9]+")


class MapImageError(Translatable):
    """A map picture that cannot be taken."""


@dataclass(frozen=True)
class StoredImage:
    """An original as stored: its path from the record's folder, size and digest."""

    path: str
    width: int
    height: int
    digest: str


def max_zoom(width: int, height: int) -> int:
    """The zoom at which one pixel of the original is one pixel on screen; zoom 0 shows
    the whole map in one tile."""
    return max(0, math.ceil(math.log2(max(width, height) / TILE)))


def needs_tiles(width: int, height: int) -> bool:
    """Whether a picture of this size is cut into tiles rather than shown whole."""
    return max(width, height) > SINGLE_LIMIT


def _allow(pixels: int) -> None:
    """Let Pillow open pictures up to ``pixels``: its own guard refuses large ones as a
    safeguard against decompression bombs, and maps are large on purpose. The size is
    checked against the table's limit before anything decodes."""
    current = Image.MAX_IMAGE_PIXELS
    if current is not None and current < pixels:
        Image.MAX_IMAGE_PIXELS = pixels


def store_original(
    store: ProseStore, record_id: str, filename: str, source: BinaryIO, max_pixels: int
) -> StoredImage:
    """Stream an uploaded map into the record's ``media/maps/``, a second ``coast.jpg``
    becoming ``coast-2.jpg``. Refused, with nothing left behind: another type, more than
    :data:`MAX_BYTES`, bytes that are not the type named, or more than ``max_pixels``."""
    folder = store.folder(record_id)
    if folder is None:
        raise MapImageError("media.error.record")
    suffix = Path(filename).suffix.lower()
    declared = MAP_TYPES.get(suffix)
    if declared is None:
        raise MapImageError("atlas.error.type")
    target_dir = folder / MEDIA_DIR / MAPS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = _NAME.sub("-", Path(filename).stem.lower()).strip("-")[:48] or "map"
    tmp = target_dir / f".{stem}{suffix}.upload"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as handle:
            head = b""
            while chunk := source.read(_CHUNK):
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > MAX_BYTES:
                    raise MapImageError("atlas.error.size", mb=MAX_BYTES // (1024 * 1024))
                digest.update(chunk)
                handle.write(chunk)
        if sniff(head) != declared:
            raise MapImageError("media.error.content")
        width, height = _size(tmp, max_pixels)
        target = target_dir / f"{stem}{suffix}"
        number = 2
        while target.exists():
            target = target_dir / f"{stem}-{number}{suffix}"
            number += 1
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return StoredImage(f"{MEDIA_DIR}/{MAPS_DIR}/{target.name}", width, height, digest.hexdigest())


def _size(path: Path, max_pixels: int) -> tuple[int, int]:
    """The picture's size from its header, refused past ``max_pixels``."""
    _allow(max_pixels)
    try:
        with Image.open(path) as picture:
            width, height = picture.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise MapImageError("media.error.content") from exc
    if width * height > max_pixels:
        raise MapImageError(
            "atlas.error.pixels",
            width=width,
            height=height,
            mp=max_pixels // 1_000_000,
        )
    return width, height


def remove_original(store: GameStateStore, found: WorldMap) -> None:
    """Erase the picture a map was made of, unless another map shows the same file."""
    path = media.resolve(store.prose, found.scope.record_id, found.image)
    if path is None:
        return
    for other in store.maps.list(lambda m: m.id != found.id):
        if media.resolve(store.prose, other.scope.record_id, other.image) == path:
            return
    path.unlink(missing_ok=True)


def level_sizes(width: int, height: int) -> list[tuple[int, int]]:
    """Each zoom level's size in pixels, from zoom 0 up to the original's."""
    top = max_zoom(width, height)
    return [
        (math.ceil(width / 2 ** (top - z)), math.ceil(height / 2 ** (top - z)))
        for z in range(top + 1)
    ]


def tile_count(width: int, height: int) -> int:
    """How many tiles the pyramid holds."""
    return sum(math.ceil(w / TILE) * math.ceil(h / TILE) for w, h in level_sizes(width, height))


def build(
    source: Path,
    target: Path,
    max_pixels: int,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Cut ``source`` into the tile pyramid ``target/{z}/{x}/{y}.webp``, written beside
    it first and put in place whole, so a folder that exists is complete. The deepest
    level is cut from the original, each above it from the one below halved, so the
    peak is about one and a quarter the decoded picture."""
    _allow(max_pixels)
    partial = target.with_name(f"{target.name}.part")
    shutil.rmtree(partial, ignore_errors=True)
    opened = Image.open(source)
    try:
        opened.load()
        has_alpha = opened.mode in ("RGBA", "LA", "PA") or "transparency" in opened.info
        mode = "RGBA" if has_alpha else "RGB"
        # Converted only when it must be, so the picture is held once, not twice.
        level = opened if opened.mode == mode else opened.convert(mode)
    except BaseException:
        opened.close()
        raise
    if level is not opened:
        opened.close()
    width, height = level.size
    top = max_zoom(width, height)
    total = tile_count(width, height)
    done = 0
    for z in range(top, -1, -1):
        w, h = level.size
        for x in range(math.ceil(w / TILE)):
            column = partial / str(z) / str(x)
            column.mkdir(parents=True, exist_ok=True)
            for y in range(math.ceil(h / TILE)):
                box = (x * TILE, y * TILE, min(w, (x + 1) * TILE), min(h, (y + 1) * TILE))
                tile = level.crop(box)
                if tile.size != (TILE, TILE):
                    # An edge tile: filled out with nothing, as Leaflet draws every tile
                    # at the full size.
                    padded = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
                    padded.paste(tile, (0, 0))
                    tile = padded
                tile.save(column / f"{y}.webp", "WEBP", quality=85, method=4)
                done += 1
                if progress is not None:
                    progress(done, total)
        if z:
            level = level.reduce(2)
        else:  # the whole map in one tile's room, unpadded: the lists' thumbnail
            level.save(partial / THUMB, "WEBP", quality=80, method=4)
    shutil.rmtree(target, ignore_errors=True)
    partial.replace(target)


@dataclass
class TileJob:
    """One map being cut: how far it got, and why it failed if it did."""

    map_id: str
    digest: str
    total: int
    done: int = 0
    error: str = ""
    finished: bool = False


class MapTiles:
    """The tile cache and the background cutting: one map at a time, off the event
    loop, kept in memory so the map's page can say how far it got."""

    def __init__(self, cache: Path, max_pixels: int) -> None:
        self._cache = cache
        self._max_pixels = max_pixels
        self._jobs: dict[str, TileJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock: asyncio.Lock | None = None

    def folder(self, map_id: str, digest: str) -> Path:
        """Where the tiles of this picture of the map are."""
        return self._cache / map_id / digest[:16]

    def ready(self, map_id: str, digest: str) -> bool:
        """Whether the tiles of this picture exist."""
        return self.folder(map_id, digest).is_dir()

    def tile(self, map_id: str, digest: str, z: int, x: int, y: int) -> Path | None:
        """One tile's file, or None."""
        path = self.folder(map_id, digest) / str(z) / str(x) / f"{y}.webp"
        return path if path.is_file() else None

    def thumb(self, map_id: str, digest: str) -> Path | None:
        """The large map's thumbnail, or None while its tiles are being cut."""
        path = self.folder(map_id, digest) / THUMB
        return path if path.is_file() else None

    def job(self, map_id: str) -> TileJob | None:
        """The map's newest cutting, finished or not."""
        return self._jobs.get(map_id)

    def ensure(self, map_id: str, digest: str, source: Path, width: int, height: int) -> None:
        """Start cutting the map's tiles unless they exist or are being cut."""
        if self.ready(map_id, digest):
            return
        current = self._jobs.get(map_id)
        if current is not None and current.digest == digest and not current.error:
            return
        job = TileJob(map_id, digest, tile_count(width, height))
        self._jobs[map_id] = job
        if self._lock is None:
            self._lock = asyncio.Lock()
        lock = self._lock

        def progress(done: int, total: int) -> None:
            job.done, job.total = done, total

        async def go() -> None:
            async with lock:  # one map at a time: each may need a gigabyte
                try:
                    await asyncio.to_thread(
                        build, source, self.folder(map_id, digest), self._max_pixels, progress
                    )
                    self._drop_others(map_id, digest)
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    logger.exception("Cutting the tiles of map %s failed", map_id)
                    job.error = str(exc) or type(exc).__name__
                finally:
                    job.finished = True

        task = asyncio.get_running_loop().create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _drop_others(self, map_id: str, digest: str) -> None:
        """Remove the tiles of the map's earlier pictures."""
        keep = digest[:16]
        folder = self._cache / map_id
        for path in folder.iterdir() if folder.is_dir() else ():
            if path.is_dir() and path.name != keep:
                shutil.rmtree(path, ignore_errors=True)

    def forget(self, map_id: str) -> None:
        """Remove every tile of a map, as when it is gone for good."""
        self._jobs.pop(map_id, None)
        shutil.rmtree(self._cache / map_id, ignore_errors=True)


__all__ = [
    "MAP_TYPES",
    "MAX_BYTES",
    "SINGLE_LIMIT",
    "TILE",
    "MapImageError",
    "MapTiles",
    "StoredImage",
    "TileJob",
    "build",
    "level_sizes",
    "max_zoom",
    "needs_tiles",
    "remove_original",
    "store_original",
    "tile_count",
]
