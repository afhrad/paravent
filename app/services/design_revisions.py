"""Revisions of the builders' designs (MCP.md §10): Claude's draft copy of a design the
gamemaster accepted, changed freely while the design itself stays as it is, published
and in use, until the gamemaster accepts the copy, which is then written onto the
design (same id and name) and dropped.

Shared by the item, spell and background builders; each design record carries
``drafted_by`` and ``revision_of``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar


class Revisable(Protocol):
    id: str
    name: str
    world_id: str | None
    drafted_by: str | None
    revision_of: str | None

    def model_dump(self) -> dict[str, Any]: ...

    def touch(self) -> None: ...


D = TypeVar("D", bound=Revisable)

# A design's own bookkeeping, never copied into or written back from a revision.
OWN_FIELDS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "published_at",
        "published_hash",
        "drafted_by",
        "revision_of",
    }
)
# What a revision keeps from its design.
KEPT = OWN_FIELDS | {"name", "world_id"}


class Designs(Protocol[D]):
    """The collection a builder keeps its designs in."""

    def list(self, where: Callable[[D], bool] | None = None) -> list[D]: ...

    def find(self, record_id: str) -> D | None: ...

    def create(self, record: D) -> D: ...

    def replace(self, record: D) -> D: ...

    def delete(self, record_id: str) -> Any: ...


def open_revision(designs: Designs[D], design_id: str) -> D | None:
    """The open revision of a design, if Claude has one."""
    return next((d for d in designs.list() if d.revision_of == design_id), None)


def start(
    designs: Designs[D], original: D, drafted_by: str, model: Callable[[dict[str, Any]], D]
) -> D:
    """A draft copy of ``original`` for Claude, or the copy already open."""
    found = open_revision(designs, original.id)
    if found is not None:
        return found
    dumped = {k: v for k, v in original.model_dump().items() if k not in OWN_FIELDS}
    return designs.create(model({**dumped, "drafted_by": drafted_by, "revision_of": original.id}))


def changes(designs: Designs[D], revision: D) -> list[str]:
    """The fields a revision changes against its design, in the design's order."""
    original = designs.find(revision.revision_of or "")
    if original is None:
        return []
    mine, theirs = revision.model_dump(), original.model_dump()
    return [k for k in mine if k not in KEPT and mine[k] != theirs.get(k)]


def accept(designs: Designs[D], revision: D, copy: Callable[[D, dict[str, Any]], D]) -> D:
    """Write a revision onto its design and drop the copy; ``copy(design, update)``
    makes the changed design. Returns it."""
    original = designs.find(revision.revision_of or "")
    if original is None:
        raise LookupError(revision.revision_of)
    update = {k: getattr(revision, k) for k in changes(designs, revision)}
    changed = copy(original, update)
    changed.touch()
    designs.replace(changed)
    designs.delete(revision.id)
    return changed


def name_clash(designs: Designs[D], name: str, own_id: str | None, revision_of: str | None) -> bool:
    """Whether another design already bears ``name`` — a design shares its name only
    with its own revision."""
    kin = {own_id, revision_of} - {None}
    folded = name.casefold()
    return any(
        d.name.casefold() == folded
        and d.id not in kin
        and not (own_id is not None and d.revision_of == own_id)
        and not (revision_of is not None and d.revision_of == revision_of)
        for d in designs.list()
    )


__all__ = ["KEPT", "OWN_FIELDS", "accept", "changes", "name_clash", "open_revision", "start"]
