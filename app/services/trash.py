"""Deleted NPCs, places, treasure, encounters, chapters and adventures, kept a while to
restore.

The objects of a world are records, so a deleted one is kept here as it was
(``library/trash.json``), with its world and when it went, for ``KEEP_DAYS``. Restoring
puts it back where it was — in its chapter or adventure while that is still there, else in
its world — under its old id. Chapters and adventures are files, moved to
``library/.trash/`` (a world's folder goes there too, but has no world to be listed in);
their entry here names that file and the adventure or world it came from, and restoring
moves it back, with whatever was deleted together with it. One whose world is gone cannot
come back and is cleared with the rest of the old ones; the files stay in the trash
folder, nothing is erased.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
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
# The kinds kept as files: their collection, and the field naming their parent.
FILES: dict[str, tuple[str, str]] = {
    "chapter": ("chapters", "adventure_id"),
    "adventure": ("adventures", "world_id"),
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

    def keep(self, kind: str, record: Any, part_of: str | None = None) -> str | None:
        """Keep ``record`` (about to be deleted) to restore, ``part_of`` the chapter or
        adventure deleted with it; clears what is too old. The entry's id."""
        scope = getattr(record, "scope", None)
        world_id = scope.world_id if isinstance(scope, Scope) else getattr(record, "world_id", None)
        if not world_id:
            return None  # an encounter of no world: nothing would show it again
        self.clear_old()
        kept = self._store.trash.create(
            TrashedObject(
                kind=kind,
                world_id=world_id,
                name=str(getattr(record, "name", "")),
                record=record.model_dump(mode="json"),
                part_of=part_of,
            )
        )
        return kept.id

    def keep_file(self, kind: str, record: Any, kept: Path) -> str:
        """Keep a chapter or adventure :meth:`ProseStore.delete` moved to ``kept``."""
        self.clear_old()
        parent = FILES[kind][1]
        entry = self._store.trash.create(
            TrashedObject(
                kind=kind,
                world_id=record.world_id,
                name=str(getattr(record, "title", None) or getattr(record, "name", "")),
                record={"id": record.id, "kept": kept.name, "parent_id": getattr(record, parent)},
            )
        )
        return entry.id

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
        if trashed.kind in FILES:
            return self._put_back(trashed)
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

    def _put_back(self, trashed: TrashedObject) -> tuple[str, Record]:
        """A chapter's file or an adventure's folder back where it was, and what was
        deleted with it."""
        name, _parent = FILES[trashed.kind]
        collection = getattr(self._store, name)
        parent_id = str(trashed.record.get("parent_id") or "")
        parents = self._store.adventures if trashed.kind == "chapter" else self._store.worlds
        if parents.find(parent_id) is None:
            raise TrashError("trash.error.adventure")
        if collection.find(str(trashed.record.get("id", ""))) is not None:
            raise TrashError("trash.error.taken")
        record = collection.put_back(str(trashed.record.get("kept", "")), parent_id)
        if record is None:
            raise TrashError("trash.error.file")
        self._store.trash.delete(trashed.id)
        for part in self._store.trash.list(lambda t: t.part_of == trashed.id):
            try:
                self.restore(part.id)
            except TrashError:
                continue  # taken meanwhile: it stays in the trash, to restore by hand
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


__all__ = ["COLLECTIONS", "FILES", "KEEP_DAYS", "Trash", "TrashError"]
