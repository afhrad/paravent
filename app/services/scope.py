"""Where an object belongs, and which one a name means (documentation/ADVENTURES.md §4).

An NPC, a treasure or an encounter belongs to a world and optionally to one adventure
and one chapter of it. A name is looked up **nearest first** from where it is written:
the chapter's own objects, then the adventure's, then the world's. Last of all comes the
rest of the world — a chapter-local NPC mentioned from another chapter still links,
rather than going dead, but anything nearer wins.

In forms a scope travels as one string: ``w:<world>``, ``a:<adventure>`` or
``c:<chapter>``; the parents of an adventure or chapter are filled in from the records,
so a form cannot claim a chapter in someone else's adventure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from app.models.gamestate import Scope
from app.services.adventures import AdventureService
from app.services.i18n import Translatable

T = TypeVar("T")

# How near a record is to where it is mentioned; lower is nearer.
CHAPTER, ADVENTURE, WORLD, ELSEWHERE = 0, 1, 2, 3


class ScopeError(Translatable):
    """A scope that does not exist or is not in this world."""


def nearness(scope: Scope, here: Scope) -> int | None:
    """How near ``scope`` is to ``here``, or None when it is another world's."""
    if scope.world_id != here.world_id:
        return None
    if scope.chapter_id is not None:
        return CHAPTER if scope.chapter_id == here.chapter_id else ELSEWHERE
    if scope.adventure_id is not None:
        return ADVENTURE if scope.adventure_id == here.adventure_id else ELSEWHERE
    return WORLD


def nearest(
    records: Iterable[T],
    name: str,
    here: Scope,
    scope_of: Callable[[T], Scope | None],
    name_of: Callable[[T], str],
) -> T | None:
    """The record called ``name`` (any case) nearest to ``here``."""
    wanted = name.strip().casefold()
    best: tuple[int, T] | None = None
    for record in records:
        scope = scope_of(record)
        if scope is None or name_of(record).casefold() != wanted:
            continue
        rank = nearness(scope, here)
        if rank is not None and (best is None or rank < best[0]):
            best = (rank, record)
    return best[1] if best else None


def within(scope: Scope, here: Scope) -> bool:
    """True when ``scope`` lies inside ``here`` — an adventure's objects include its chapters'."""
    if scope.world_id != here.world_id:
        return False
    if here.chapter_id is not None:
        return scope.chapter_id == here.chapter_id
    if here.adventure_id is not None:
        return scope.adventure_id == here.adventure_id
    return True


def encode(scope: Scope) -> str:
    """The form value for a scope."""
    if scope.chapter_id:
        return f"c:{scope.chapter_id}"
    if scope.adventure_id:
        return f"a:{scope.adventure_id}"
    return f"w:{scope.world_id}"


def decode(value: str, adventures: AdventureService, world_id: str | None = None) -> Scope:
    """The scope a form value names, its parents filled in from the records.

    With ``world_id`` given the scope must lie in that world. A bare id (no prefix) is
    read as a world, which is what older forms sent.
    """
    kind, _, ident = value.partition(":")
    if not ident:
        kind, ident = "w", kind
    try:
        if kind == "c":
            chapter = adventures.get_chapter(ident)
            scope = Scope(
                world_id=chapter.world_id,
                adventure_id=chapter.adventure_id,
                chapter_id=chapter.id,
            )
        elif kind == "a":
            adventure = adventures.get(ident)
            scope = Scope(world_id=adventure.world_id, adventure_id=adventure.id)
        else:
            scope = Scope(world_id=adventures.world(ident).id)
    except LookupError as exc:
        raise ScopeError("scope.error.unknown") from exc
    if world_id is not None and scope.world_id != world_id:
        raise ScopeError("scope.error.unknown")
    return scope


def choices(adventures: AdventureService, world_id: str) -> list[tuple[str, str, int]]:
    """Every scope in a world as ``(value, label, depth)``, in reading order."""
    world = adventures.world(world_id)
    out = [(f"w:{world.id}", world.name, 0)]
    for adventure in adventures.adventures(world_id):
        out.append((f"a:{adventure.id}", adventure.name, 1))
        for chapter in adventures.chapters(adventure.id):
            # The adventure's name too: a closed <select> shows one line, without indents.
            out.append((f"c:{chapter.id}", f"{adventure.name} › {chapter.title}", 2))
    return out


def label(adventures: AdventureService, scope: Scope) -> list[str]:
    """The names along a scope, world first: ``["Sunken Marches", "Drowned Bell", "Grey Reed"]``."""
    names = []
    try:
        names.append(adventures.world(scope.world_id).name)
        if scope.adventure_id:
            names.append(adventures.get(scope.adventure_id).name)
        if scope.chapter_id:
            names.append(adventures.get_chapter(scope.chapter_id).title)
    except LookupError:
        pass  # a file deleted by hand: show as much of the path as still exists
    return names


__all__ = [
    "ADVENTURE",
    "CHAPTER",
    "ELSEWHERE",
    "WORLD",
    "ScopeError",
    "choices",
    "decode",
    "encode",
    "label",
    "nearest",
    "nearness",
    "within",
]
