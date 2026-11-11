"""Exceptions raised by the storage layer.

These are domain errors, deliberately not HTTP errors — the routes translate them.
Keeping them separate stops the store from depending on FastAPI.
"""

from __future__ import annotations


class StoreError(Exception):
    """Base class for every storage-layer failure."""


class EntryNotFound(StoreError):
    """A lookup by key found nothing."""

    def __init__(self, category: str, key: str) -> None:
        self.category = category
        self.key = key
        super().__init__(f"No {category} entry with key {key!r}")


class DuplicateEntry(StoreError):
    """A create would have overwritten an existing entry."""

    def __init__(self, category: str, key: str) -> None:
        self.category = category
        self.key = key
        super().__init__(f"A {category} entry with key {key!r} already exists")


class InvalidEntry(StoreError):
    """An entry failed model validation."""

    def __init__(self, category: str, detail: str) -> None:
        self.category = category
        self.detail = detail
        super().__init__(f"Invalid {category} entry: {detail}")


class CopyResolutionError(StoreError):
    """A ``_copy`` directive pointed at an entry that does not exist."""

    def __init__(self, child: str, parent: str) -> None:
        self.child = child
        self.parent = parent
        super().__init__(f"{child!r} copies {parent!r}, which was not found")
