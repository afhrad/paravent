"""Deleted NPCs, places, treasure, encounters, maps, chapters and adventures, kept a while
to restore.

The objects of a world are records, so a deleted one is kept here as it was
(``library/trash.json``), with its world and when it went, for ``KEEP_DAYS``. Restoring
puts it back where it was — in its chapter or adventure while that is still there, else in
its world — under its old id; an adventure's map comes back only with its adventure, and
the picture a map was made of is erased when the trash forgets the map. Chapters and
adventures are files, moved to
``library/.trash/`` (a world's folder goes there too, but has no world to be listed in);
their entry here names that file and the adventure or world it came from, and restoring
moves it back, with whatever was deleted together with it. One whose world is gone cannot
come back and is cleared with the rest of the old ones. The trash folder's files are
erased once they are ``KEEP_DAYS`` old too (:meth:`Trash.clear_old`, by the time stamp
:meth:`ProseStore.delete` gives their name), a deleted world's folder among them, so the
folder and every backup holding it stop growing; a backup made before still has them.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.gamestate import Record, Scope, TrashedObject, WorldMap
from app.services.i18n import Translatable
from app.services.map_tiles import remove_original
from app.store.gamestate import Collection, GameStateStore

KEEP_DAYS = 30
# How :meth:`ProseStore.delete` names what it moves to the trash folder: the moment, then
# the file's or folder's own name. Nothing else there is ever erased.
_STAMPED = re.compile(r"^(\d{8}-\d{6})-.")

logger = logging.getLogger(__name__)

# Where each kind lives on the game-state store.
COLLECTIONS: dict[str, str] = {
    "npc": "npcs",
    "place": "locations",
    "treasure": "treasures",
    "encounter": "encounters",
    "quest": "quests",
    "faction": "factions",
    "map": "maps",
}
# The kinds kept as files: their collection, and the field naming their parent.
FILES: dict[str, tuple[str, str]] = {
    "chapter": ("chapters", "adventure_id"),
    "adventure": ("adventures", "world_id"),
    "world": ("worlds", ""),  # a world has no parent: it goes back into the worlds' folder
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

    def keep(
        self,
        kind: str,
        record: Any,
        part_of: str | None = None,
        links: dict[str, list[str]] | None = None,
    ) -> str | None:
        """Keep ``record`` (about to be deleted) to restore, ``part_of`` the chapter or
        adventure deleted with it, ``links`` what pointed at it (a faction's quests and
        contacts); clears what is too old. The entry's id."""
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
                links=links or {},
            )
        )
        return kept.id

    def keep_file(
        self, kind: str, record: Any, kept: Path, lifted: list[dict[str, Any]] | None = None
    ) -> str:
        """Keep a chapter, adventure or world :meth:`ProseStore.delete` moved to ``kept``;
        ``lifted`` what its delete moved up a level, to put back on a restore."""
        self.clear_old()
        parent = FILES[kind][1]
        entry = self._store.trash.create(
            TrashedObject(
                kind=kind,
                # A world is filed under its own id: it is listed apart (:meth:`worlds`).
                world_id=record.id if kind == "world" else record.world_id,
                name=str(getattr(record, "title", None) or getattr(record, "name", "")),
                record={
                    "id": record.id,
                    "kept": kept.name,
                    "parent_id": getattr(record, parent) if parent else "",
                },
                lifted=lifted or [],
            )
        )
        return entry.id

    def worlds(self) -> list[TrashedObject]:
        """The worlds deleted lately, newest first."""
        found = self._store.trash.list(lambda t: t.kind == "world")
        return sorted(found, key=lambda t: t.deleted_at, reverse=True)

    def entry_of(self, kind: str, record_id: str) -> str | None:
        """The trash entry keeping the ``kind`` record ``record_id``, newest; for the
        **Undo** after a delete."""
        found = self._store.trash.list(
            lambda t: t.kind == kind and str(t.record.get("id", "")) == record_id
        )
        return max(found, key=lambda t: t.deleted_at).id if found else None

    def of_world(self, world_id: str) -> list[TrashedObject]:
        """What the world's trash holds, newest first."""
        found = self._store.trash.list(lambda t: t.world_id == world_id)
        return sorted(found, key=lambda t: t.deleted_at, reverse=True)

    def restore(self, trashed_id: str) -> tuple[str, Record]:
        """Put a deleted object back; ``(kind, record)``. Raises :class:`TrashError`."""
        trashed = self._store.trash.find(trashed_id)
        if trashed is None:
            raise TrashError("trash.error.gone")
        if trashed.kind != "world" and self._store.worlds.find(trashed.world_id) is None:
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
        elif trashed.kind == "map":
            # An adventure's map sticks to it (documentation/MAPS.md §3): it comes back
            # with the adventure or not at all.
            where = Scope.model_validate(data.get("scope", {}))
            if where.adventure_id and self._store.adventures.find(where.adventure_id) is None:
                raise TrashError("trash.error.adventure")
        elif isinstance(data.get("scope"), dict):
            place = self._still_there(Scope.model_validate(data["scope"]))
            data["scope"] = place.model_dump(mode="json")
        try:
            record = collection._model.model_validate(data)
        except ValidationError as exc:
            raise TrashError("trash.error.unreadable") from exc
        collection.create(record)
        self._relink(trashed)
        self._store.trash.delete(trashed_id)
        return trashed.kind, record

    def _relink(self, trashed: TrashedObject) -> None:
        """A faction back: the quests and contacts it was unlinked from point at it again,
        those still there and still of no faction."""
        if trashed.kind != "faction":
            return
        faction_id = str(trashed.record.get("id", ""))
        for name in ("quests", "contacts"):
            collection = getattr(self._store, name)
            for ident in trashed.links.get(name, []):
                found = collection.find(ident)
                if found is not None and found.faction_id is None:
                    collection.update(ident, faction_id=faction_id)

    def _put_back(self, trashed: TrashedObject) -> tuple[str, Record]:
        """A chapter's file or an adventure's folder back where it was, and what was
        deleted with it."""
        name, _parent = FILES[trashed.kind]
        collection = getattr(self._store, name)
        parent_id = str(trashed.record.get("parent_id") or "")
        parents = self._store.adventures if trashed.kind == "chapter" else self._store.worlds
        if trashed.kind != "world" and parents.find(parent_id) is None:
            raise TrashError("trash.error.adventure")
        if collection.find(str(trashed.record.get("id", ""))) is not None:
            raise TrashError("trash.error.taken")
        record = collection.put_back(str(trashed.record.get("kept", "")), parent_id or None)
        if record is None:
            raise TrashError("trash.error.file")
        self._store.trash.delete(trashed.id)
        self._lower(trashed.lifted)
        for part in self._store.trash.list(lambda t: t.part_of == trashed.id):
            try:
                self.restore(part.id)
            except TrashError:
                continue  # taken meanwhile: it stays in the trash, to restore by hand
        return trashed.kind, record

    def _lower(self, lifted: list[dict[str, Any]]) -> None:
        """Put what a delete moved up a level back where it was, where it still is where
        the delete put it (one moved since stays where it was moved)."""
        for moved in lifted:
            collection = getattr(self._store, str(moved.get("collection", "")), None)
            if not isinstance(collection, Collection):
                continue
            record = collection.find(str(moved.get("id", "")))
            if record is None:
                continue
            try:
                before, after = (
                    Scope.model_validate(moved["from"]),
                    Scope.model_validate(moved["to"]),
                )
            except (KeyError, ValidationError):
                continue
            if record.scope != after:
                continue
            if moved.get("collection") == "encounters":
                collection.update(
                    record.id, adventure_id=before.adventure_id, chapter_id=before.chapter_id
                )
            else:
                collection.update(record.id, scope=before)

    def _still_there(self, scope: Scope) -> Scope:
        """``scope``, or the widest part of it that still exists."""
        if scope.chapter_id and self._store.chapters.find(scope.chapter_id) is not None:
            return scope
        if scope.adventure_id and self._store.adventures.find(scope.adventure_id) is not None:
            return Scope(world_id=scope.world_id, adventure_id=scope.adventure_id)
        return Scope(world_id=scope.world_id)

    def empty(self, world_id: str) -> int:
        """Forget everything the world's trash holds; how many."""
        gone = self.of_world(world_id)
        for trashed in gone:
            self._forget(trashed)
        return len(gone)

    def _forget(self, trashed: TrashedObject) -> None:
        """Drop an entry for good; a map's picture goes with it, which nothing else
        shows (documentation/MAPS.md §3)."""
        self._store.trash.delete(trashed.id)
        if trashed.kind != "map":
            return
        try:
            found = WorldMap.model_validate(trashed.record)
        except ValidationError:
            return
        if self._store.maps.find(found.id) is None:
            remove_original(self._store, found)

    def clear_old(self, now: datetime | None = None) -> int:
        """Forget what was deleted more than ``KEEP_DAYS`` ago, and erase the trash
        folder's files of that age; how many files went."""
        limit = (now or datetime.now(UTC)) - timedelta(days=KEEP_DAYS)
        for trashed in self._store.trash.list(lambda t: t.deleted_at < limit):
            self._forget(trashed)
        return self._erase_files(limit)

    def _erase_files(self, limit: datetime) -> int:
        folder = self._store.prose.trash
        if not folder.is_dir():
            return 0
        erased = 0
        for path in folder.iterdir():
            stamped = _STAMPED.match(path.name)
            if stamped is None or path.is_symlink():
                continue
            try:
                went = datetime.strptime(stamped.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
            except ValueError:
                continue
            if went >= limit:
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:  # a file that will not go stays until the next time
                logger.warning("Could not erase %s from the trash: %s", path, exc)
                continue
            erased += 1
        if erased:
            logger.info("Erased %d item(s) older than %d days from %s", erased, KEEP_DAYS, folder)
        return erased


__all__ = ["COLLECTIONS", "FILES", "KEEP_DAYS", "Trash", "TrashError"]
