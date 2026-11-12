"""The maps printed in each adventure and rulebook (``generated/gendata-maps.json``).

5eTools indexes every map of a book by the chapter (the body's section) it is printed
in: the picture's path in the image folder, a thumbnail, its title, the id of its image
entry in the body, and its player version — the same map without the secrets, an entry
of its own (``imageType: mapPlayer``) that names its map in ``mapParent``. Read on first
use, for reading only: Paravent shows maps, it does not play on them (no grid, no tokens).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAPS_FILE = "generated/gendata-maps.json"


@dataclass(frozen=True)
class BookMap:
    """One printed map: where it is in the book and its picture, and its player
    version if the book has one."""

    title: str
    path: str
    # The body section it is printed in (the index of the book's chapter), and its name.
    chapter: int
    chapter_name: str
    # The id of its image entry in the body, where it can be found; None when it has none.
    id: str | None = None
    thumbnail: str | None = None
    # The numbered areas drawn on it that lead to their text.
    regions: int = 0
    player: BookMap | None = None


def _path(href: Any) -> str | None:
    if isinstance(href, dict) and href.get("type") == "internal" and href.get("path"):
        return str(href["path"])
    return None


def _maps(book: dict[str, Any]) -> tuple[BookMap, ...]:
    """A book's maps in reading order, each player version hung on its map."""
    out: list[BookMap] = []
    for chapter in book.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        index = chapter.get("ix")
        if not isinstance(index, int):
            continue
        name = str(chapter.get("name", ""))
        for image in chapter.get("images") or []:
            path = _path(image.get("href")) if isinstance(image, dict) else None
            if path is None:
                continue
            found = BookMap(
                title=str(image.get("title") or ""),
                path=path,
                chapter=index,
                chapter_name=name,
                id=image["id"] if isinstance(image.get("id"), str) else None,
                thumbnail=_path(image.get("hrefThumbnail")),
                regions=len(image.get("mapRegions") or []),
            )
            if image.get("imageType") != "mapPlayer":
                out.append(found)
                continue
            # A player version belongs to the map it names, else to the one before it.
            parent = (image.get("mapParent") or {}).get("id")
            at = next(
                (i for i in range(len(out) - 1, -1, -1) if parent and out[i].id == parent),
                len(out) - 1 if out and out[-1].chapter == index else None,
            )
            if at is not None and out[at].player is None:
                held = out[at]
                out[at] = BookMap(
                    held.title,
                    held.path,
                    held.chapter,
                    held.chapter_name,
                    held.id,
                    held.thumbnail,
                    held.regions,
                    found,
                )
    return tuple(out)


class MapIndex:
    """Every book's maps, keyed by the book's or adventure's id (case ignored)."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._maps: dict[str, tuple[BookMap, ...]] | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget what was read, after the corpus changed."""
        with self._lock:
            self._maps = None

    def of(self, book_id: str | None) -> tuple[BookMap, ...]:
        """The maps of the adventure or rulebook ``book_id``; empty when it has none."""
        if not book_id:
            return ()
        return self._all().get(book_id.lower(), ())

    def _all(self) -> dict[str, tuple[BookMap, ...]]:
        with self._lock:
            if self._maps is not None:
                return self._maps
            maps: dict[str, tuple[BookMap, ...]] = {}
            if self._root is not None:
                try:
                    raw = json.loads((self._root / MAPS_FILE).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.info("Maps index %s not read: %s", MAPS_FILE, exc)
                    raw = {}
                for key, book in raw.items() if isinstance(raw, dict) else ():
                    if isinstance(book, dict):
                        maps[str(book.get("id") or key).lower()] = _maps(book)
            self._maps = maps
            return maps


__all__ = ["MAPS_FILE", "BookMap", "MapIndex"]
