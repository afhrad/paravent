"""Images that belong to a world: covers, portraits, maps (documentation/ADVENTURES.md §5.5).

Media lives inside the world's folder, beside the prose that shows it, and is addressed
by a path **relative to the record's own folder** — ``media/cover.webp`` in a world, in
an adventure the adventure's ``media/``. Two rules keep that safe:

* **Confined to the world folder.** A path that resolves outside it — ``../../users.json``,
  an absolute path, a symlink pointing away — is refused, never served.
* **Images only, checked by content.** The extension must be an image type and the first
  bytes must say the same; an SVG is refused, because it can carry script.
"""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

from app.services.i18n import Translatable
from app.store.prose import MEDIA_DIR, ProseStore

MAX_BYTES = 20 * 1024 * 1024

IMAGE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}

_NAME = re.compile(r"[^a-z0-9]+")


class MediaError(Translatable):
    """An upload that cannot be stored."""


def sniff(data: bytes) -> str | None:
    """The image type the bytes themselves declare, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    return None


def resolve(store: ProseStore, record_id: str, relative: str) -> Path | None:
    """The image file ``relative`` names for this record, or None when it may not be served."""
    folder = store.folder(record_id)
    boundary = store.world_folder(record_id)
    if folder is None or boundary is None or not relative or "\\" in relative:
        return None
    if relative.startswith("/") or Path(relative).is_absolute():
        return None
    candidate = (folder / relative).resolve()
    try:
        candidate.relative_to(boundary.resolve())
    except ValueError:
        return None
    if candidate.suffix.lower() not in IMAGE_TYPES or not candidate.is_file():
        return None
    return candidate


def find(store: ProseStore, record_id: str, world_id: str, name: str) -> tuple[str, str] | None:
    """Obsidian's ``![[name]]``: ``(record, path)`` of the image it means, or None.

    As written or in ``media/`` first; then, for a bare file name, the first file of that
    name in the record's folder, then anywhere in its world — served as the world's.
    """
    for relative in (name, f"{MEDIA_DIR}/{name}"):
        if resolve(store, record_id, relative) is not None:
            return record_id, relative
    if "/" in name or "\\" in name or Path(name).suffix.lower() not in IMAGE_TYPES:
        return None
    for owner in dict.fromkeys((record_id, world_id)):
        folder = store.folder(owner)
        if folder is None:
            continue
        for path in sorted(p for p in folder.rglob("*") if p.name == name):
            relative = path.relative_to(folder).as_posix()
            if resolve(store, owner, relative) is not None:
                return owner, relative
    return None


def media_type(path: Path) -> str:
    """The Content-Type to serve an image with."""
    return IMAGE_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or ""


def store_upload(store: ProseStore, record_id: str, filename: str, data: bytes) -> str:
    """Save an uploaded image into the record's ``media/`` folder.

    Returns the path to write into front matter or Markdown (``media/map.webp``). An
    existing file is never overwritten; a second ``map.webp`` becomes ``map-2.webp``.
    """
    folder = store.folder(record_id)
    if folder is None:
        raise MediaError("media.error.record")
    suffix = checked_image(filename, data)
    stem = _NAME.sub("-", Path(filename).stem.lower()).strip("-")[:48] or "image"
    target_dir = folder / MEDIA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}{suffix}"
    number = 2
    while target.exists():
        target = target_dir / f"{stem}-{number}{suffix}"
        number += 1
    write_image(target, data)
    return f"{MEDIA_DIR}/{target.name}"


def checked_image(filename: str, data: bytes) -> str:
    """The upload's extension (``.webp``) once it is an image type, its bytes say the
    same, and it is not too large; raises :class:`MediaError` otherwise."""
    suffix = Path(filename).suffix.lower()
    declared = IMAGE_TYPES.get(suffix)
    if declared is None:
        raise MediaError("media.error.type")
    if len(data) > MAX_BYTES:
        raise MediaError("media.error.size", mb=MAX_BYTES // (1024 * 1024))
    if sniff(data) != declared:
        raise MediaError("media.error.content")
    return suffix


def write_image(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` whole or not at all (a temp file, then a rename)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


__all__ = [
    "IMAGE_TYPES",
    "MAX_BYTES",
    "MediaError",
    "checked_image",
    "media_type",
    "resolve",
    "sniff",
    "store_upload",
    "write_image",
]
