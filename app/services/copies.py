"""A copy of a world's object, made from a context menu (documentation/CONTEXT-MENUS.md):
the same NPC, place, treasure or encounter, in the same place, under a new id and a name
of its own — "Mother Ilse (copy)", then "(copy 2)", so a link by name still finds one."""

from __future__ import annotations

from typing import Any, TypeVar

from app.models.gamestate import Record

R = TypeVar("R", bound=Record)


def copy_name(name: str, taken: set[str], suffix: str) -> str:
    """``name`` with ``suffix`` (" (copy)"), numbered past the names already ``taken``
    (lower case)."""
    candidate = f"{name}{suffix}"
    n = 2
    while candidate.casefold() in taken:
        candidate = f"{name}{suffix[:-1]} {n})" if suffix.endswith(")") else f"{name}{suffix} {n}"
        n += 1
    return candidate


def duplicate(collection: Any, record: R, suffix: str) -> R:
    """Store a copy of ``record`` in ``collection``: everything but its id, its times and
    its name, which gets ``suffix``."""
    taken = {str(getattr(r, "name", "")).casefold() for r in collection.list()}
    data = record.model_dump(exclude={"id", "created_at", "updated_at"})
    data["name"] = copy_name(str(getattr(record, "name", "")), taken, suffix)
    copy = type(record).model_validate(data)
    return collection.create(copy)  # type: ignore[no-any-return]


__all__ = ["copy_name", "duplicate"]
