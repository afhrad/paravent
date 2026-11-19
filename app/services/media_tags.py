"""Tags on a world's pictures, so the image browser finds them by more than a file name.

One file per world, ``media/tags.json`` in its folder, mapping each picture's path from
the world's folder to its tags: ``{"the-drowned-bell/media/bell.png": ["bell", "night"]}``.
The pictures are never touched; the file is backed up and moves with the world. A tag
is the gamemaster's own word, kept lower case, trimmed and short. Tags of a picture that
is gone are dropped the next time the file is written. Besides its own tags, a picture
shows automatic ones it never stores: its orientation (landscape, portrait, square).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.services.media import IMAGE_TYPES
from app.store.jsonfile import file_lock, read_json, write_json
from app.store.prose import MEDIA_DIR, ProseStore

TAGS_FILE = "tags.json"
MOST = 40  # the longest a tag may be
_SPACES = re.compile(r"\s+")
_NOT_A_TAG = re.compile(r"[^\w \-]", re.UNICODE)


def clean_tag(text: str) -> str:
    """A tag as kept: lower case, single spaces, letters, digits, spaces and hyphens,
    at most 40 characters; "" when nothing is left."""
    text = _NOT_A_TAG.sub("", _SPACES.sub(" ", text.strip().lower()))
    return text.strip()[:MOST].strip()


def clean_tags(texts: Iterable[str]) -> list[str]:
    """Tags as kept, each once, in alphabetical order. A comma separates several in one
    text (``"tavern, night"``)."""
    out = {clean_tag(part) for text in texts for part in text.split(",")}
    return sorted(t for t in out if t)


def orientation(path: Path) -> str:
    """``landscape``, ``portrait`` or ``square`` from a picture's size (its header only),
    or "" when it cannot be read."""
    try:
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, UnidentifiedImageError):
        return ""
    if abs(width - height) <= 0.05 * max(width, height):
        return "square"
    return "landscape" if width > height else "portrait"


class MediaTags:
    """Reads and writes the tags of a world's pictures."""

    def __init__(self, prose: ProseStore) -> None:
        self._prose = prose

    def _file(self, record_id: str) -> Path | None:
        world = self._prose.world_folder(record_id)
        return world / MEDIA_DIR / TAGS_FILE if world is not None else None

    def world_path(self, record_id: str, relative: str) -> str | None:
        """A path as ``record_id`` writes it (``../media/bell.png``) from its world's
        folder (``the-drowned-bell/media/bell.png``), or None outside the world."""
        world, folder = self._prose.world_folder(record_id), self._prose.folder(record_id)
        if world is None or folder is None or not relative:
            return None
        try:
            return (folder / relative).resolve().relative_to(world.resolve()).as_posix()
        except ValueError:
            return None

    def all(self, record_id: str) -> dict[str, list[str]]:
        """Every tagged picture of the record's world, by its path from the world's folder;
        none from a file that does not read (a hand edit gone wrong)."""
        path = self._file(record_id)
        data = _read(path) if path is not None else {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): clean_tags(str(t) for t in v) for k, v in data.items() if isinstance(v, list)
        }

    def of(self, record_id: str, world_path: str) -> list[str]:
        """One picture's tags."""
        return self.all(record_id).get(world_path, [])

    def vocabulary(self, record_id: str) -> Counter[str]:
        """Every tag of the world, with how many pictures carry it."""
        return Counter(tag for tags in self.all(record_id).values() for tag in tags)

    def set(self, record_id: str, world_path: str, tags: Iterable[str]) -> list[str]:
        """Give one picture these tags (none: untagged); the tags kept."""
        kept = clean_tags(tags)
        self._write(record_id, {world_path: kept})
        return kept

    def change(
        self,
        record_id: str,
        world_paths: Iterable[str],
        *,
        add: Iterable[str] = (),
        remove: Iterable[str] = (),
    ) -> None:
        """Add and remove tags on several pictures at once (the browser's selection)."""
        plus, minus = clean_tags(add), set(clean_tags(remove))
        current = self.all(record_id)
        self._write(
            record_id,
            {p: sorted((set(current.get(p, [])) | set(plus)) - minus) for p in world_paths},
        )

    def _write(self, record_id: str, changes: Mapping[str, list[str]]) -> None:
        path = self._file(record_id)
        world = self._prose.world_folder(record_id)
        if path is None or world is None:
            return
        with file_lock(path):
            data = _read(path)
            if not isinstance(data, dict):
                return  # a file that does not read is left as it is, to be mended by hand
            for key, tags in changes.items():
                if tags:
                    data[key] = tags
                else:
                    data.pop(key, None)
            # Tags of a picture that is gone go with it.
            for key in [k for k in data if not _picture(world / k)]:
                data.pop(key, None)
            write_json(path, dict(sorted(data.items())))


def _read(path: Path) -> object:
    """The file's content: {} when missing, None when it does not read."""
    if not path.is_file():
        return {}
    try:
        return read_json(path, {})
    except (OSError, ValueError):
        return None


def _picture(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_TYPES


__all__ = ["MediaTags", "TAGS_FILE", "clean_tag", "clean_tags", "orientation"]
