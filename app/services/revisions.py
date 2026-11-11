"""Revising an accepted adventure on a draft copy (documentation/MCP.md §9).

Claude writes only into drafts, so to change an adventure it no longer may write to, it
works on a whole copy. The copy remembers which record each of its records came from
(``origins``) and how that record stood (``baseline``); accepting writes what Claude
changed back onto the originals, which keep their ids, pictures, chapter status and the
fights run from them, and the copy goes. What the gamemaster changed since stays theirs.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from app.models.gamestate import (
    Adventure,
    Chapter,
    Encounter,
    Record,
    Scope,
    new_id,
)
from app.services.adventures import AdventureService
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore

Kind = Literal["adventure", "chapter", "npc", "place", "encounter", "treasure"]
Action = Literal["changed", "new", "removed"]

# What a revision may change; everything else about a record is the table's.
FIELDS: dict[Kind, tuple[str, ...]] = {
    "adventure": ("name", "summary", "body", "level", "party"),
    "chapter": ("title", "body", "order"),
    "npc": ("name", "notes", "roles", "statblock", "race", "culture", "given_name"),
    "place": ("name", "notes", "tags"),
    "encounter": ("name", "notes", "monsters"),
    "treasure": ("name", "notes", "items", "coins"),
}
# The field a rename shows in, and the tag it follows into the texts.
TEXT_FIELD = {"chapter": "body", "adventure": "body"}


class RevisionError(Translatable):
    """A revision that cannot be started or accepted."""


def _fingerprint(record: Record, kind: Kind, chapter: str | None = None) -> str:
    data = record.model_dump(mode="json", include=set(FIELDS[kind]))
    return hashlib.sha256(
        json.dumps([data, chapter], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def _title(record: Any) -> str:
    return str(getattr(record, "title", None) or record.name)


def text_of(record: Any, kind: Kind) -> str:
    """The text of a record, for comparing."""
    return str(getattr(record, TEXT_FIELD.get(kind, "notes"), ""))


def diff_lines(old: str, new: str) -> list[str]:
    """What changed between two texts, line by line, ``+`` and ``-``; no context."""
    return [line for line in difflib.ndiff(old.splitlines(), new.splitlines()) if line[:1] in "+-"]


@dataclass(frozen=True)
class Change:
    """One record that differs from its original."""

    kind: Kind
    action: Action
    title: str
    # The record on the original; None for what the revision added.
    origin: str | None = None
    # True when the gamemaster changed the original since: theirs stays.
    conflict: bool = False
    # A fight run from it stays, however the revision removed it.
    held: bool = False
    diff: tuple[str, ...] = ()


class Revisions:
    """Starting, comparing, accepting."""

    def __init__(self, store: GameStateStore, adventures: AdventureService) -> None:
        """Bind to the library and the adventures."""
        self._store = store
        self._adventures = adventures

    def _objects(self, adventure_id: str) -> list[tuple[Kind, Any]]:
        def inside(record: Any) -> bool:
            scope = record.scope
            return scope is not None and scope.adventure_id == adventure_id

        store = self._store
        return [
            *(("npc", r) for r in store.npcs.list(inside)),
            *(("place", r) for r in store.locations.list(inside)),
            *(("encounter", r) for r in store.encounters.list(inside)),
            *(("treasure", r) for r in store.treasures.list(inside)),
        ]

    def _collection(self, kind: Kind) -> Any:
        store = self._store
        return {
            "npc": store.npcs,
            "place": store.locations,
            "encounter": store.encounters,
            "treasure": store.treasures,
            "chapter": store.chapters,
        }[kind]

    def open_for(self, adventure_id: str) -> Adventure | None:
        """The revision waiting on an adventure, if one is."""
        original = self._adventures.get(adventure_id)
        found = self._adventures.adventures(original.world_id)
        return next((a for a in found if a.revision_of == adventure_id), None)

    def start(self, adventure_id: str, by: str) -> Adventure:
        """A draft copy of an adventure Claude wrote; the open one, if there is one."""
        original = self._adventures.get(adventure_id)
        if original.status == "draft":
            raise RevisionError("revision.error.draft")
        if not original.drafted_by:
            raise RevisionError("revision.error.not_claudes")
        if original.revision_of:
            raise RevisionError("revision.error.of_revision")
        waiting = self.open_for(adventure_id)
        if waiting is not None:
            return waiting
        copy = self._adventures.create(original.world_id, original.name)
        origins: dict[str, str] = {copy.id: original.id}
        baseline = {original.id: _fingerprint(original, "adventure")}
        chapters: dict[str, str] = {}
        for chapter in self._adventures.chapters(adventure_id):
            made = self._store.chapters.create(
                Chapter(
                    world_id=original.world_id,
                    adventure_id=copy.id,
                    title=chapter.title,
                    body=chapter.body,
                    order=chapter.order,
                    status=chapter.status,
                    section=chapter.section,
                )
            )
            chapters[chapter.id] = made.id
            origins[made.id] = chapter.id
            baseline[chapter.id] = _fingerprint(chapter, "chapter")
        for kind, record in self._objects(adventure_id):
            chapter_id = record.scope.chapter_id
            where = Scope(
                world_id=original.world_id,
                adventure_id=copy.id,
                chapter_id=chapters.get(chapter_id) if chapter_id else None,
            )
            made_id = new_id()
            self._collection(kind).create(self._placed(kind, record, made_id, where))
            origins[made_id] = record.id
            baseline[record.id] = _fingerprint(record, kind, chapter_id)
        return self._store.adventures.update(
            copy.id,
            status="draft",
            drafted_by=by,
            summary=original.summary,
            body=original.body,
            level=original.level,
            party=original.party,
            based_on=original.based_on,
            source_set=original.source_set,
            revision_of=original.id,
            origins=origins,
            baseline=baseline,
        )

    @staticmethod
    def _placed(kind: Kind, record: Any, record_id: str, where: Scope) -> Any:
        """A record as the copy holds it: its pictures live beside the original."""
        update: dict[str, Any] = {"id": record_id}
        if isinstance(record, Encounter):
            update |= {
                "adventure_id": where.adventure_id,
                "chapter_id": where.chapter_id,
            }
        else:
            update["scope"] = where
        if kind == "npc":
            update["portrait"] = None
        if kind == "place":
            update["picture"] = None
        return record.model_copy(update=update)

    def _pairs(self, copy: Adventure) -> list[tuple[Kind, Any]]:
        chapters = self._adventures.chapters(copy.id)
        return [*(("chapter", c) for c in chapters), *self._objects(copy.id)]

    def _chapter_of(self, record: Any, copy: Adventure) -> str | None:
        """Which chapter of the original a copy's record lies in; a new chapter's own id."""
        chapter_id = record.scope.chapter_id if record.scope else None
        return copy.origins.get(chapter_id, chapter_id) if chapter_id else None

    def _original(self, kind: Kind, record_id: str) -> Any:
        return self._collection(kind).find(record_id)

    def changes(self, copy: Adventure) -> list[Change]:
        """What differs from the originals; nothing for a record no one touched."""
        out: list[Change] = []
        original = self._adventures.get(copy.revision_of or "")
        if _fingerprint(copy, "adventure") != copy.baseline.get(original.id):
            out.append(
                Change(
                    "adventure",
                    "changed",
                    copy.name,
                    original.id,
                    conflict=_fingerprint(original, "adventure") != copy.baseline.get(original.id),
                    diff=tuple(diff_lines(original.body, copy.body)),
                )
            )
        seen: set[str] = set()
        for kind, record in self._pairs(copy):
            origin_id = copy.origins.get(record.id)
            if origin_id is None:
                out.append(Change(kind, "new", _title(record)))
                continue
            seen.add(origin_id)
            chapter = None if kind == "chapter" else self._chapter_of(record, copy)
            before = copy.baseline.get(origin_id)
            if _fingerprint(record, kind, chapter) == before:
                continue
            found = self._original(kind, origin_id)
            if found is None:  # gone from the original since
                out.append(Change(kind, "changed", _title(record), origin_id, conflict=True))
                continue
            if isinstance(found, Chapter):
                now = _fingerprint(found, kind)
            else:
                now = _fingerprint(found, kind, found.scope.chapter_id if found.scope else None)
            diff = diff_lines(text_of(found, kind), text_of(record, kind))
            conflict = now != before
            out.append(
                Change(kind, "changed", _title(record), origin_id, conflict, diff=tuple(diff))
            )
        for kind, origin_id, record in self._removed(copy, seen):
            now = _fingerprint(record, kind, None if kind == "chapter" else record.scope.chapter_id)
            out.append(
                Change(
                    kind,
                    "removed",
                    _title(record),
                    origin_id,
                    conflict=now != copy.baseline[origin_id],
                    held=kind == "encounter" and self._fought(origin_id),
                )
            )
        return out

    def _removed(self, copy: Adventure, seen: set[str]) -> list[tuple[Kind, str, Any]]:
        """The originals the copy no longer has."""
        original_id = copy.revision_of or ""
        current = [
            *(("chapter", c) for c in self._adventures.chapters(original_id)),
            *self._objects(original_id),
        ]
        return [
            (kind, record.id, record)
            for kind, record in current
            if record.id in copy.baseline and record.id not in seen
        ]

    def _fought(self, encounter_id: str) -> bool:
        return bool(self._store.combats.list(lambda c: c.encounter_id == encounter_id))

    def accept(self, copy_id: str) -> list[Change]:
        """Write the copy back onto the original and remove it; answers what happened.
        A change that met the gamemaster's own stays out, and a fought fight stays."""
        copy = self._adventures.get(copy_id)
        if not copy.revision_of:
            raise RevisionError("revision.error.not_revision")
        changes = self.changes(copy)
        original = self._adventures.get(copy.revision_of)
        by_origin = {c.origin: c for c in changes if c.origin}
        # New chapters first: an object may lie in one.
        chapters: dict[str, str] = {}
        records = self._pairs(copy)
        for kind, record in records:
            if kind == "chapter" and record.id not in copy.origins:
                made = self._adventures.create_chapter(original.id, record.title)
                chapters[record.id] = made.id
        rewrite = self._adventures.rewrite_references
        tags = {"npc": "npc", "place": "place", "treasure": "treasure", "encounter": "encounter"}
        for kind, record in records:
            if kind == "chapter":
                continue
            origin_id = copy.origins.get(record.id)
            change = by_origin.get(origin_id or "")
            chapter_id = self._chapter_of(record, copy)
            chapter_id = chapters.get(chapter_id or "", chapter_id)
            where = Scope(
                world_id=original.world_id, adventure_id=original.id, chapter_id=chapter_id
            )
            if origin_id is None:
                self._collection(kind).create(self._placed(kind, record, new_id(), where))
            elif change is not None and not change.conflict:
                found = self._original(kind, origin_id)
                old = found.name
                self._collection(kind).update(origin_id, **self._written(kind, record, where))
                if old != record.name:
                    rewrite(original.world_id, tags[kind], old, record.name)
        for kind, record in records:  # last, so a rename's rewrite does not undo the text
            if kind != "chapter":
                continue
            origin_id = copy.origins.get(record.id)
            target = chapters.get(record.id) or origin_id or ""
            change = by_origin.get(origin_id or "")
            if origin_id is None or (change is not None and not change.conflict):
                old = self._original("chapter", target).title
                self._store.chapters.update(
                    target, title=record.title, body=record.body, order=record.order
                )
                if old != record.title:
                    rewrite(original.world_id, "chapter", old, record.title)
        self._remove(changes, copy)
        if not any(c.kind == "adventure" and c.conflict for c in changes):
            self._store.adventures.update(
                original.id,
                name=copy.name,
                summary=copy.summary,
                body=copy.body,
                level=copy.level,
                party=copy.party,
            )
        self._adventures.delete(copy.id, contents=True)
        return changes

    def _written(self, kind: Kind, record: Any, where: Scope) -> dict[str, Any]:
        values = {name: getattr(record, name) for name in FIELDS[kind]}
        if kind == "encounter":
            return {**values, "chapter_id": where.chapter_id}
        return {**values, "scope": where}

    def _remove(self, changes: list[Change], copy: Adventure) -> None:
        for change in changes:
            if change.action != "removed" or change.conflict or change.held:
                continue
            if change.kind == "chapter":
                self._adventures.delete_chapter(change.origin or "")
            else:
                self._collection(change.kind).delete(change.origin)


__all__ = ["Change", "FIELDS", "RevisionError", "Revisions", "diff_lines"]
