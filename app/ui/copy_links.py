"""The address of the page that copies or moves a piece into another world
(app/services/world_copy.py), for any page to link to without importing that page."""

from __future__ import annotations

from urllib.parse import urlencode

COPY_URL = "/gm/copy"
FILE_URL = "/gm/copy/file"
IMPORT_URL = "/gm/copy/import"


def copy_link(kind: str, record_id: str, mode: str = "copy") -> str:
    """The page that copies (or moves) a piece into another world, or saves it as a file."""
    return f"{COPY_URL}?{urlencode({'kind': kind, 'id': record_id, 'mode': mode})}"


__all__ = ["COPY_URL", "FILE_URL", "IMPORT_URL", "copy_link"]
