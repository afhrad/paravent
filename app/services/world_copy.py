"""Copying or moving a piece of one world into another (documentation/CURRENT-WORLD.md §9c).

A whole world travels by export and import (app/services/world_transfer.py); this takes one
piece across: an **adventure** with its chapters, its pictures and everything placed in it,
or an **NPC**, **place**, **treasure**, **encounter**, **quest**, **faction** or
**creature design**. The copy is made the way an import makes a world — every id it carries
given anew and replaced wherever it is written (front matter, records naming each other,
Markdown) — into a world that already exists.

* **What a text names comes along**, when asked: the world-level NPCs, places, treasure
  and encounters its Markdown links to (``{@npc Mara}``) or its adventure uses, and a
  quest's faction. One the target world already has by that name is not copied: the link
  finds the target's own, as links go by name.
* **A name the target has** is given " (2)", " (3)" …, as Duplicate does; for a creature
  design any design of the table's counts, as published creatures share one name space.
* **Pictures and tokens** are copied into the target's folder; a file of the same name
  there that differs is not overwritten, the copy taking a free name.
* **What stays behind**: fights, the log, a quest's characters and progress, renown.
* **The target's books** are checked first: creatures, items and statblocks outside them
  are listed (they are copied as written and show "not in your books" there).

**Move** is a copy and then a delete of what was copied, into *Recently deleted*, so Undo
and Restore work as for any delete; what came along by name stays where it was too. A
quest, faction or creature design has no trash and moves in place, keeping its id.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from app.models.gamestate import (
    Adventure,
    Record,
    Scope,
    World,
    new_id,
)
from app.services.i18n import Translatable
from app.services.media import IMAGE_TYPES, sniff
from app.store.gamestate import Collection, GameStateStore
from app.store.prose import ADVENTURES_DIR, slugify

Kind = Literal["adventure", "npc", "place", "treasure", "encounter", "quest", "faction", "creature"]
KINDS: tuple[Kind, ...] = (
    "adventure", "npc", "place", "treasure", "encounter", "quest", "faction", "creature",
)  # fmt: skip
# Where each record kind lives on the store.
COLLECTIONS: dict[str, str] = {
    "npc": "npcs",
    "place": "locations",
    "treasure": "treasures",
    "encounter": "encounters",
    "quest": "quests",
    "faction": "factions",
    "creature": "creatures",
}
# The kinds placed by a scope (world, adventure, chapter), and the ones that sit in a
# world by ``world_id``.
SCOPED = ("npc", "place", "treasure")
# What a Markdown link names, by its tag.
_LINK = re.compile(r"\{@(npc|place|treasure|encounter) ([^}|]+)")
# A Markdown link to an object's page (``[Hob](/gm/npcs/<id>)``), by its address.
_ADDRESS = re.compile(r"/gm/(npcs|places|treasure|encounters)/([0-9a-f]{12})")
_ADDRESS_KIND = {
    "npcs": "npc",
    "places": "place",
    "treasure": "treasure",
    "encounters": "encounter",
}
_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
# The picture field of each kind, a path relative to its owner's folder.
PICTURES = {"npc": "portrait", "place": "picture", "encounter": "cover", "quest": "cover",
            "faction": "cover"}  # fmt: skip
# The kinds that carry a token of the table's own (app/services/tokens.py OWN_KINDS).
TOKENS = ("npc", "treasure", "creature")
# The kinds a delete keeps in Recently deleted; the others move in place.
TRASHED = ("adventure", "npc", "place", "treasure", "encounter")


class WorldCopyError(Translatable):
    """A copy that cannot be made: no such piece, the same world, no such target."""


@dataclass(frozen=True)
class Piece:
    """One thing in a copy: its kind, id and name."""

    kind: str
    id: str
    name: str


@dataclass
class Plan:
    """What a copy will do, shown before it is made."""

    kind: str
    id: str
    name: str
    source: World
    target: World
    # Copied: the piece and what lies in it (an adventure's chapters and objects).
    pieces: list[Piece] = field(default_factory=list)
    # World-level objects its texts name, taken along (or offered, when not bringing).
    brought: list[Piece] = field(default_factory=list)
    # Named, but the target has one of that name: links find the target's.
    reused: list[Piece] = field(default_factory=list)
    # References outside the target's books (``Goblin|MM``), each once.
    outside: list[str] = field(default_factory=list)
    # The name the copy takes where the target has the piece's (None: its own).
    renamed: str | None = None
    bring: bool = True


@dataclass
class Copied:
    """What a copy made: the plan, and the id of the copy of the piece itself."""

    plan: Plan
    id: str
    moved: bool = False
    files: int = 0


class WorldCopy:
    """Copies or moves one piece of a world into another."""

    def __init__(
        self,
        store: GameStateStore,
        token_folder: Callable[[str | None, str], Path | None],
        delete: Callable[[str, str], None],
    ) -> None:
        """``token_folder(world, kind)`` is where a world's own tokens of a kind live;
        ``delete(kind, id)`` deletes one piece the way its page does, into the trash."""
        self._store = store
        self._token_folder = token_folder
        self._delete = delete

    # -- reading -----------------------------------------------------------------

    def _collection(self, kind: str) -> Collection[Any]:
        collection = getattr(self._store, COLLECTIONS[kind])
        assert isinstance(collection, Collection)
        return collection

    def find(self, kind: str, record_id: str) -> Record | None:
        """The piece, or None."""
        if kind == "adventure":
            return self._store.adventures.find(record_id)
        if kind not in COLLECTIONS:
            return None
        found: Record | None = self._collection(kind).find(record_id)
        return found

    def world_of(self, kind: str, record: Record) -> str | None:
        """The world a piece belongs to."""
        scope = getattr(record, "scope", None)
        if isinstance(scope, Scope):
            return scope.world_id
        return getattr(record, "world_id", None)

    def targets(self, kind: str, record_id: str) -> list[World]:
        """The worlds a piece may go to: every other one, by name."""
        record = self.find(kind, record_id)
        here = self.world_of(kind, record) if record is not None else None
        worlds = [w for w in self._store.worlds.list() if w.id != here]
        return sorted(worlds, key=lambda w: w.name.casefold())

    # -- planning ------------------------------------------------------------------

    def plan(self, kind: str, record_id: str, target_id: str, *, bring: bool = True) -> Plan:
        """What copying the piece into ``target_id`` would do. Raises
        :class:`WorldCopyError` for a piece or world that is not there, or the same world."""
        record = self.find(kind, record_id)
        if record is None:
            raise WorldCopyError("copy.error.no_piece")
        source = self._store.worlds.find(self.world_of(kind, record) or "")
        target = self._store.worlds.find(target_id)
        if source is None or target is None:
            raise WorldCopyError("copy.error.no_world")
        if source.id == target.id:
            raise WorldCopyError("copy.error.same_world")
        name = _name_of(record)
        plan = Plan(kind, record_id, name, source, target, bring=bring)
        plan.pieces = self._gathered(kind, record)
        own = {p.id for p in plan.pieces}
        for piece in self._named(plan, own):
            if self._target_has(target.id, piece.kind, piece.name) is not None:
                plan.reused.append(piece)
            else:
                plan.brought.append(piece)
        plan.renamed = self._free_name(kind, record, target.id)
        carried = plan.pieces + (plan.brought if bring else [])
        plan.outside = self._outside(carried, target)
        return plan

    def _gathered(self, kind: str, record: Record) -> list[Piece]:
        """The piece, and for an adventure its chapters and what is placed in it."""
        pieces = [Piece(kind, record.id, _name_of(record))]
        if kind != "adventure":
            return pieces
        store = self._store
        for chapter in store.chapters.list(lambda c: c.adventure_id == record.id):
            pieces.append(Piece("chapter", chapter.id, chapter.title))
        for inner in ("npc", "place", "treasure", "encounter"):
            for found in self._collection(inner).list():
                scope = found.scope
                if scope is not None and scope.adventure_id == record.id:
                    pieces.append(Piece(inner, found.id, _name_of(found)))
        return pieces

    def _texts(self, pieces: Iterable[Piece]) -> list[str]:
        texts: list[str] = []
        for piece in pieces:
            record = self._record(piece)
            for key in ("body", "summary", "notes", "text", "note"):
                value = getattr(record, key, None)
                if isinstance(value, str) and value:
                    texts.append(value)
        return texts

    def _record(self, piece: Piece) -> Record | None:
        if piece.kind == "chapter":
            return self._store.chapters.find(piece.id)
        return self.find(piece.kind, piece.id)

    def _named(self, plan: Plan, own: set[str]) -> list[Piece]:
        """The world-level objects the pieces' texts link to, an adventure uses, or a
        quest serves (its faction) — not those copied anyway."""
        world = plan.source.id
        found: dict[str, Piece] = {}
        for text in self._texts(plan.pieces):
            for tag, name in _LINK.findall(text):
                hit = self._world_level(world, tag, name.strip())
                if hit is not None and hit.id not in own:
                    found.setdefault(hit.id, Piece(tag, hit.id, _name_of(hit)))
            for address, ident in _ADDRESS.findall(text):
                kind = _ADDRESS_KIND[address]
                hit = self._collection(kind).find(ident)
                scope = hit.scope if hit is not None else None
                if (
                    hit is not None
                    and ident not in own
                    and scope is not None
                    and scope.world_id == world
                ):
                    found.setdefault(ident, Piece(kind, ident, _name_of(hit)))
        adventure = self._store.adventures.find(plan.id) if plan.kind == "adventure" else None
        for used in adventure.uses if adventure is not None else []:
            for kind in ("npc", "place", "treasure", "encounter"):
                hit = self._collection(kind).find(used)
                if hit is not None and hit.id not in own:
                    found.setdefault(hit.id, Piece(kind, hit.id, _name_of(hit)))
        if plan.kind == "quest":
            quest = self._store.quests.find(plan.id)
            faction = (
                self._store.factions.find(quest.faction_id) if quest and quest.faction_id else None
            )
            if faction is not None:
                found.setdefault(faction.id, Piece("faction", faction.id, faction.name))
        return list(found.values())

    def _world_level(self, world_id: str, kind: str, name: str) -> Record | None:
        """The world's own object of that kind and name (not one inside an adventure)."""
        wanted = name.casefold()
        for record in self._collection(kind).list():
            scope = record.scope if kind != "faction" else None
            if kind == "faction":
                if record.world_id == world_id and record.name.casefold() == wanted:
                    return record  # type: ignore[no-any-return]
                continue
            if scope is None or scope.world_id != world_id or scope.adventure_id is not None:
                continue
            if _name_of(record).casefold() == wanted:
                return record  # type: ignore[no-any-return]
        return None

    def _target_has(self, world_id: str, kind: str, name: str) -> Record | None:
        return self._world_level(world_id, kind, name)

    def _free_name(self, kind: str, record: Record, target_id: str) -> str | None:
        """The copy's name where the target has the piece's already, else None."""
        name = _name_of(record)
        if kind == "adventure":
            taken = {
                a.name.casefold()
                for a in self._store.adventures.list(lambda a: a.world_id == target_id)
            }
        elif kind == "creature":
            taken = {d.name.casefold() for d in self._store.creatures.list() if d.id != record.id}
        elif kind in ("quest", "faction"):
            taken = {
                _name_of(r).casefold()
                for r in self._collection(kind).list()
                if getattr(r, "world_id", None) == target_id
            }
        else:
            hit = self._target_has(target_id, kind, name)
            taken = {name.casefold()} if hit is not None else set()
            if hit is not None:
                taken |= {
                    _name_of(r).casefold()
                    for r in self._collection(kind).list()
                    if r.scope is not None and r.scope.world_id == target_id
                }
        if name.casefold() not in taken:
            return None
        number = 2
        while f"{name} ({number})".casefold() in taken:
            number += 1
        return f"{name} ({number})"

    def _outside(self, pieces: Iterable[Piece], target: World) -> list[str]:
        """The references the pieces make that the target's books lack, each once."""
        books = target.source_set
        if books.is_unrestricted:
            return []
        missing: dict[str, None] = {}
        for piece in pieces:
            record = self._record(piece)
            for ref in _references(piece.kind, record):
                source = ref.rpartition("|")[2]
                if source and not books.allows(source):
                    missing.setdefault(ref, None)
        return list(missing)

    # -- copying ---------------------------------------------------------------------

    def copy(
        self, kind: str, record_id: str, target_id: str, *, bring: bool = True, fresh: bool = True
    ) -> Copied:
        """Copy the piece (and what comes with it) into the target world; ``fresh``, as a
        new group meets it: an adventure and its chapters planned again."""
        plan = self.plan(kind, record_id, target_id, bring=bring)
        carried = plan.pieces + (plan.brought if bring else [])
        ids = {p.id: _text_id() for p in carried}
        reused = {
            p.id: hit.id
            for p in plan.reused
            if (hit := self._target_has(plan.target.id, p.kind, p.name))
        }
        remap = _remapper(ids | reused)
        done = Copied(plan, ids[record_id])
        if kind == "adventure":
            done.files += self._copy_adventure(plan, remap, fresh)
        for piece in carried:
            if piece.kind in ("adventure", "chapter"):
                continue
            done.files += self._copy_record(plan, piece, ids, reused, remap)
        return done

    def move(self, kind: str, record_id: str, target_id: str, *, bring: bool = True) -> Copied:
        """Copy the piece into the target, then delete it here into the trash; a quest,
        faction or creature design moves in place."""
        if kind not in TRASHED:
            return self._move_in_place(kind, record_id, target_id, bring=bring)
        done = self.copy(kind, record_id, target_id, bring=bring, fresh=False)
        self._delete(kind, record_id)
        done.moved = True
        return done

    def _move_in_place(self, kind: str, record_id: str, target_id: str, *, bring: bool) -> Copied:
        plan = self.plan(kind, record_id, target_id, bring=bring)
        record = self.find(kind, record_id)
        assert record is not None
        changes: dict[str, Any] = {"world_id": target_id}
        if plan.renamed:
            changes["name"] = plan.renamed
        if kind == "quest":
            faction = getattr(record, "faction_id", None)
            changes.update(adventure_id=None, characters=[], status="open")
            if faction is not None:
                found = self._store.factions.find(faction)
                hit = self._target_has(target_id, "faction", found.name) if found else None
                if hit is not None:
                    changes["faction_id"] = hit.id
                elif bring and found is not None:
                    changes["faction_id"] = self.copy("faction", faction, target_id).id
                else:
                    changes["faction_id"] = None
        if kind == "faction":
            changes["renown"] = {}
        files = self._carry_files(
            plan, Piece(kind, record_id, plan.name), record, record_id, changes
        )
        self._collection(kind).update(record_id, **changes)
        return Copied(plan, record_id, moved=True, files=files)

    def _copy_adventure(self, plan: Plan, remap: Callable[[str], str], fresh: bool) -> int:
        """The adventure's folder into the target's, text with its ids replaced."""
        store = self._store
        source = store.prose.folder(plan.id)
        world_folder = store.prose.world_folder(plan.target.id)
        if source is None or world_folder is None:
            raise WorldCopyError("copy.error.no_world")
        adventure = store.adventures.find(plan.id)
        slug = slugify(plan.renamed or (adventure.name if adventure else "adventure"), "adventure")
        folder = _free(world_folder / ADVENTURES_DIR / slug)
        files = 0
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            relative = path.relative_to(source)
            suffix = path.suffix.lower()
            content = path.read_bytes()
            if suffix in (".md", ".json"):
                content = remap(content.decode("utf-8", "replace")).encode("utf-8")
            elif suffix not in IMAGE_TYPES or sniff(content) is None:
                continue
            target = folder / remap(relative.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            files += 1
        store.prose.reload_paths(folder.rglob("*.md"))
        new_id = remap(plan.id)
        copied = store.adventures.find(new_id)
        if copied is not None:
            uses = [u for u in (remap(x) for x in copied.uses) if self._held(u)]
            changes: dict[str, Any] = {"uses": uses, "drafted_by": None, "revision_of": None}
            if plan.renamed:
                changes["name"] = plan.renamed
            if fresh:
                changes["status"] = "planned"
                for chapter in store.chapters.list(lambda c: c.adventure_id == new_id):
                    if chapter.status != "planned":
                        store.chapters.update(chapter.id, status="planned")
            store.adventures.update(new_id, **changes)
        return files

    def _held(self, record_id: str) -> bool:
        return any(self._collection(k).find(record_id) is not None for k in SCOPED + ("encounter",))

    def _copy_record(
        self,
        plan: Plan,
        piece: Piece,
        ids: dict[str, str],
        reused: dict[str, str],
        remap: Callable[[str], str],
    ) -> int:
        """One record into the target, its world and links pointed there."""
        record = self.find(piece.kind, piece.id)
        if record is None:
            return 0
        collection = self._collection(piece.kind)
        data = json.loads(remap(json.dumps(record.model_dump(mode="json"))))
        target = plan.target.id
        copied_ids = set(ids.values())
        if piece.kind in SCOPED:
            scope = data["scope"]
            inside = scope.get("adventure_id") in copied_ids
            data["scope"] = {
                "world_id": target,
                "adventure_id": scope.get("adventure_id") if inside else None,
                "chapter_id": scope.get("chapter_id") if inside else None,
            }
        else:
            data["world_id"] = target
        if piece.kind == "encounter":
            inside = data.get("adventure_id") in copied_ids
            if not inside:
                data["adventure_id"] = data["chapter_id"] = None
        if piece.kind == "quest":
            data.update(adventure_id=None, characters=[], status="open")
            data["steps"] = [{**s, "done": False} for s in data.get("steps", [])]
            if data.get("faction_id") not in copied_ids | set(reused.values()):
                data["faction_id"] = None
        if piece.kind == "faction":
            data["renown"] = {}
        if piece.kind == "creature":
            data.update(published_at=None, published_hash=None)
        if piece.id == plan.id and plan.renamed:
            data["name"] = plan.renamed
        files = self._carry_files(plan, piece, record, ids[piece.id], data)
        collection.upsert_many([type(record).model_validate(data)])
        return files

    def _carry_files(
        self, plan: Plan, piece: Piece, record: Record, new_id: str, data: dict[str, Any]
    ) -> int:
        """A record's picture and token into the target's folder; ``data`` gets the
        picture's path where it had to take another name."""
        store = self._store
        files = 0
        key = PICTURES.get(piece.kind)
        raw = getattr(record, key, None) if key else None
        old_owner = _owner(piece.kind, record)
        new_owner = _owner_of_data(piece.kind, data)
        if isinstance(raw, str) and raw and old_owner and new_owner and old_owner != new_owner:
            relative, bar, effects = raw.partition("|")
            source = store.prose.folder(old_owner)
            # A new owner inside the copied adventure has its folder already, with the file.
            target = store.prose.folder(new_owner)
            if source is not None and target is not None:
                src = source / relative
                placed = _place(src, target, relative)
                if placed is not None:
                    data[key or ""] = placed + bar + effects
                    files += 1
        if piece.kind in TOKENS:
            old_world = self.world_of(piece.kind, record)
            new_world = plan.target.id
            files += self._copy_token(piece.kind, old_world, piece.id, new_world, new_id)
        return files

    def _copy_token(
        self, kind: str, old_world: str | None, old_id: str, new_world: str, new_id: str
    ) -> int:
        source = self._token_folder(old_world, kind)
        target = self._token_folder(new_world, kind)
        if source is None or target is None or not source.is_dir():
            return 0
        count = 0
        for path in source.glob(f"{old_id}.*"):
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target / f"{new_id}{path.suffix}")
            count += 1
        return count


def _name_of(record: Record | None) -> str:
    if record is None:
        return ""
    return str(getattr(record, "name", None) or getattr(record, "title", "") or "")


def _references(kind: str, record: Record | None) -> list[str]:
    """The ``Name|SOURCE`` references a piece makes to the books."""
    if record is None:
        return []
    if kind == "encounter":
        return [f"{m.name}|{m.source}" for m in getattr(record, "monsters", [])]
    if kind == "npc":
        statblock = getattr(record, "statblock", None)
        return [statblock] if statblock and "|" in statblock else []
    if kind == "treasure":
        return [f"{i.name}|{i.source}" for i in getattr(record, "items", [])]
    if kind == "adventure" and isinstance(record, Adventure) and record.based_on:
        return [f"{record.name}|{record.based_on}"]
    return []


def _owner(kind: str, record: Record) -> str | None:
    """The record whose folder holds the piece's picture."""
    scope = getattr(record, "scope", None)
    if isinstance(scope, Scope):
        return scope.record_id
    return getattr(record, "world_id", None)


def _owner_of_data(kind: str, data: dict[str, Any]) -> str | None:
    scope = data.get("scope")
    if isinstance(scope, dict):
        return scope.get("chapter_id") or scope.get("adventure_id") or scope.get("world_id")
    return data.get("world_id")


def _place(source: Path, folder: Path, relative: str) -> str | None:
    """Copy ``source`` to ``folder/relative``, or to a free name beside it when a different
    file is there; the relative path used, None when there is nothing to copy."""
    if not source.is_file():
        return None
    target = folder / relative
    if target.is_file() and target.read_bytes() == source.read_bytes():
        return relative
    path = PurePosixPath(relative)
    number = 2
    while target.exists():
        path = PurePosixPath(relative).with_name(
            f"{PurePosixPath(relative).stem}-{number}{PurePosixPath(relative).suffix}"
        )
        target = folder / str(path)
        number += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return str(path)


def _free(candidate: Path) -> Path:
    number = 2
    base = candidate
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{number}")
        number += 1
    return candidate


def _text_id() -> str:
    """A fresh id with a letter in it: front matter is rewritten as text, and YAML reads an
    id of digits alone as a number (app/services/world_transfer.py)."""
    while True:
        ident = new_id()
        if not ident.isdigit():
            return ident


def _remapper(ids: dict[str, str]) -> Callable[[str], str]:
    def remap(text: str) -> str:
        return _ID.sub(lambda m: ids.get(m.group(0), m.group(0)), text)

    return remap


__all__ = ["KINDS", "Copied", "Piece", "Plan", "WorldCopy", "WorldCopyError"]
