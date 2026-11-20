"""Deleted NPCs, places, treasure and encounters, kept a while to restore.

Worlds and chapters go to ``library/.trash/`` as files; the objects of a world are records,
so a deleted one is kept here as it was (``library/trash.json``), with its world and when
it went, for ``KEEP_DAYS``. Restoring puts it back where it was — in its chapter or
adventure while that is still there, else in its world — under its old id. One whose world
is gone cannot come back and is cleared with the rest of the old ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.models.gamestate import Record, Scope, TrashedObject
from app.services.i18n import Translatable
from app.store.gamestate import Collection, GameStateStore

KEEP_DAYS = 30

# Where each kind lives on the game-state store.
COLLECTIONS: dict[str, str] = {
    "npc": "npcs",
    "place": "locations",
    "treasure": "treasures",
    "encounter": "encounters",
}


class TrashError(Translatable):
    """Something that cannot be restored."""


class Trash:
    """Keeps what is deleted, and gives it back."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def _collection(self, kind: str) -> Collection[Any]:
        collection = getattr(self._store, COLLECTIONS[kind])
        assert isinstance(collection, Collection)
        return collection

    def keep(self, kind: str, record: Any) -> None:
        """Keep ``record`` (about to be deleted) to restore; clears what is too old."""
        scope = getattr(record, "scope", None)
        world_id = scope.world_id if isinstance(scope, Scope) else getattr(record, "world_id", None)
        if not world_id:
            return  # an encounter of no world: nothing would show it again
        self.clear_old()
        self._store.trash.create(
            TrashedObject(
                kind=kind,
                world_id=world_id,
                name=str(getattr(record, "name", "")),
                record=record.model_dump(mode="json"),
            )
        )

    def of_world(self, world_id: str) -> list[TrashedObject]:
        """What the world's trash holds, newest first."""
        found = self._store.trash.list(lambda t: t.world_id == world_id)
        return sorted(found, key=lambda t: t.deleted_at, reverse=True)

    def restore(self, trashed_id: str) -> tuple[str, Record]:
        """Put a deleted object back; ``(kind, record)``. Raises :class:`TrashError`."""
        trashed = self._store.trash.find(trashed_id)
        if trashed is None:
            raise TrashError("trash.error.gone")
        if self._store.worlds.find(trashed.world_id) is None:
            raise TrashError("trash.error.world")
        collection = self._collection(trashed.kind)
        data = dict(trashed.record)
        if collection.find(str(data.get("id", ""))) is not None:
            raise TrashError("trash.error.taken")
        if trashed.kind == "encounter":
            # An encounter keeps its place in fields of its own, not a scope.
            place = self._still_there(
                Scope(
                    world_id=trashed.world_id,
                    adventure_id=data.get("adventure_id"),
                    chapter_id=data.get("chapter_id"),
                )
            )
            data["adventure_id"], data["chapter_id"] = place.adventure_id, place.chapter_id
        elif isinstance(data.get("scope"), dict):
            place = self._still_there(Scope.model_validate(data["scope"]))
            data["scope"] = place.model_dump(mode="json")
        try:
            record = collection._model.model_validate(data)
        except ValidationError as exc:
            raise TrashError("trash.error.unreadable") from exc
        collection.create(record)
        self._store.trash.delete(trashed_id)
        return trashed.kind, record

    def _still_there(self, scope: Scope) -> Scope:
        """``scope``, or the widest part of it that still exists."""
        if scope.chapter_id and self._store.chapters.find(scope.chapter_id) is not None:
            return scope
        if scope.adventure_id and self._store.adventures.find(scope.adventure_id) is not None:
            return Scope(world_id=scope.world_id, adventure_id=scope.adventure_id)
        return Scope(world_id=scope.world_id)

    def empty(self, world_id: str) -> int:
        """Forget everything the world's trash holds; how many."""
        gone = [t.id for t in self.of_world(world_id)]
        for trashed_id in gone:
            self._store.trash.delete(trashed_id)
        return len(gone)

    def clear_old(self, now: datetime | None = None) -> None:
        """Forget what was deleted more than ``KEEP_DAYS`` ago."""
        limit = (now or datetime.now(UTC)) - timedelta(days=KEEP_DAYS)
        for trashed in self._store.trash.list(lambda t: t.deleted_at < limit):
            self._store.trash.delete(trashed.id)


__all__ = ["COLLECTIONS", "KEEP_DAYS", "Trash", "TrashError"]
