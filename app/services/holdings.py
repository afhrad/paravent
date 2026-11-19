"""What an adventure holds: its own NPCs, places, encounters and treasure, and the world's
ready-made ones it uses (documentation/ADVENTURES.md §4, 2026-11-19).

An object belongs to one scope — the world, an adventure, a chapter. The world's own
are **ready-made**: an adventure may use any of them, kept as a link in its ``uses``,
without moving it, so several adventures share one innkeeper. A world object is named in
any chapter already, so nothing else changes for the text.

Taking an object out of an adventure never deletes it: a ready-made one used there is
unlinked, and one made inside the adventure (or one of its chapters) moves up to the
world, ready to be used again. Deleting for good stays on the object's own page.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.models.gamestate import Scope
from app.services import image_effects, media
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore

Kind = Literal["npc", "place", "encounter", "treasure"]
KINDS: tuple[Kind, ...] = ("npc", "place", "encounter", "treasure")


class HoldingsError(Translatable):
    """An object that is not the world's, or not in this adventure."""


@dataclass(frozen=True)
class Held:
    """An object in an adventure: its own (``used`` False) or a ready-made one it uses."""

    kind: Kind
    id: str
    name: str
    used: bool


class Holdings:
    """Adds the world's ready-made objects to an adventure, and takes objects out."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def _collection(self, kind: str) -> Any:
        store = self._store
        collections = {
            "npc": store.npcs,
            "place": store.locations,
            "encounter": store.encounters,
            "treasure": store.treasures,
        }
        if kind not in collections:
            raise HoldingsError("holdings.error.kind")
        return collections[kind]

    @staticmethod
    def _scope(record: Any) -> Scope | None:
        return getattr(record, "scope", None)

    def _world_own(self, record: Any, world_id: str) -> bool:
        where = self._scope(record)
        return (
            where is not None
            and where.world_id == world_id
            and where.adventure_id is None
            and where.chapter_id is None
        )

    def used(self, adventure_id: str, kind: str) -> list[Held]:
        """The world's objects of ``kind`` the adventure uses, by name."""
        adventure = self._store.adventures.get(adventure_id)
        collection = self._collection(kind)
        out = [
            Held(kind, record.id, record.name, True)  # type: ignore[arg-type]
            for record in collection.list()
            if record.id in adventure.uses and self._world_own(record, adventure.world_id)
        ]
        return sorted(out, key=lambda h: h.name.lower())

    def ready(self, adventure_id: str, kind: str) -> list[tuple[str, str]]:
        """The world's ready-made objects of ``kind`` it does not use yet, ``(id, name)``."""
        adventure = self._store.adventures.get(adventure_id)
        collection = self._collection(kind)
        out = [
            (record.id, record.name)
            for record in collection.list()
            if record.id not in adventure.uses and self._world_own(record, adventure.world_id)
        ]
        return sorted(out, key=lambda x: x[1].lower())

    def use(self, adventure_id: str, kind: str, object_id: str) -> None:
        """Let the adventure use one of the world's ready-made objects."""
        adventure = self._store.adventures.get(adventure_id)
        record = self._collection(kind).find(object_id)
        if record is None or not self._world_own(record, adventure.world_id):
            raise HoldingsError("holdings.error.not_ready")
        if object_id not in adventure.uses:
            self._store.adventures.update(adventure_id, uses=[*adventure.uses, object_id])

    def drop(self, adventure_id: str, kind: str, object_id: str) -> None:
        """Take an object out of the adventure without deleting it: unlink a ready-made
        one, or move one of its own (or its chapters') up to the world."""
        adventure = self._store.adventures.get(adventure_id)
        if object_id in adventure.uses:
            self._store.adventures.update(
                adventure_id, uses=[x for x in adventure.uses if x != object_id]
            )
            return
        collection = self._collection(kind)
        record = collection.find(object_id)
        where = self._scope(record) if record is not None else None
        if record is None or where is None or where.adventure_id != adventure_id:
            raise HoldingsError("holdings.error.not_here")
        world = Scope(world_id=adventure.world_id)
        changes: dict[str, Any] = {"scope": world}
        # A picture written from the old scope's folder, written again from the world's.
        for field in ("portrait", "picture"):
            value = getattr(record, field, None)
            if value:
                changes[field] = self._rewritten(where.record_id, adventure.world_id, value)
        collection.update(object_id, **changes)

    def _rewritten(self, old_record: str, world_id: str, reference: str) -> str:
        path, effects = image_effects.split(reference)
        prose = self._store.prose
        world, folder = prose.world_folder(old_record), prose.folder(old_record)
        if world is None or folder is None:
            return reference
        try:
            from_world = (folder / path).resolve().relative_to(world.resolve()).as_posix()
        except ValueError:
            return reference
        seen = media.seen_from(prose, world_id, from_world)
        return image_effects.join(seen, list(effects)) if seen else reference


def holdings_of(holdings: Holdings, adventure_id: str) -> dict[str, Sequence[Held]]:
    """The ready-made objects an adventure uses, by kind."""
    return {kind: holdings.used(adventure_id, kind) for kind in KINDS}


__all__ = ["Held", "Holdings", "HoldingsError", "KINDS", "holdings_of"]
