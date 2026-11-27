"""Copying or moving pieces of one world into another, or into a file for another table
(documentation/CURRENT-WORLD.md §9c).

A whole world travels by export and import (app/services/world_transfer.py); this takes
pieces across: an **adventure** with its chapters, its pictures and everything placed in
it (its maps among it), or **NPCs**, **places**, **treasure**, **encounters**, **quests**,
**factions**, a world's **maps** and **creature, item, spell and background designs**, one
or several of a list at once. They
are gathered into a :class:`Bundle` — records as data, an adventure's folder as files,
pictures and tokens as bytes — which is written into a world, or saved as a file
(``*.paravent-pieces.zip``) and read back into a world at any table. Writing gives every
id new and replaces it wherever it is written (front matter, records naming each other,
Markdown), as an import does.

* **What a text names comes along**, when asked: the world-level NPCs, places, treasure
  and encounters its Markdown links to (``{@npc Mara}``, ``[Mara](/gm/npcs/<id>)``) or its
  adventure uses, and a quest's faction. One the target world already has by that name is
  not copied: the link finds the target's own.
* **A name the target has** is given " (2)", " (3)" …, as Duplicate does; for a creature
  design any design of the table's counts, as published creatures share one name space.
* **Pictures and tokens** are copied into the target's folder; a file of the same name
  there that differs is not overwritten, the copy taking a free name.
* **What stays behind**: fights, the log, a quest's characters and progress, renown. A copy
  starts planned (an adventure and its chapters); a move keeps its state.
* **The target's books** are checked first: creatures, items and statblocks outside them
  are listed (they are copied as written and show "not in your books" there).

**Move** is a copy and then a delete of the pieces chosen, into *Recently deleted*, so Undo
and Restore work as for any delete; what came along by name stays where it was too. A
quest, faction or creature design has no trash and moves in place, keeping its id.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any, Literal

from pydantic import ValidationError

from app import __version__
from app.models.gamestate import Adventure, Record, Scope, World, WorldMap, new_id
from app.services.i18n import Translatable
from app.services.media import IMAGE_TYPES, sniff
from app.services.world_transfer import WorldTransferError, _checked
from app.store.gamestate import Collection, GameStateStore
from app.store.prose import ADVENTURE_FILE, ADVENTURES_DIR, slugify

Kind = Literal[
    "adventure", "npc", "place", "treasure", "encounter", "quest", "faction", "creature",
    "item_design", "spell_design", "background_design", "map",
]  # fmt: skip
KINDS: tuple[Kind, ...] = (
    "adventure", "npc", "place", "treasure", "encounter", "quest", "faction", "creature",
    "item_design", "spell_design", "background_design", "map",
)  # fmt: skip
# The builders' designs: one name space each across the table, published to homebrew.
DESIGNS = ("creature", "item_design", "spell_design", "background_design")
# Where each record kind lives on the store.
COLLECTIONS: dict[str, str] = {
    "npc": "npcs",
    "place": "locations",
    "treasure": "treasures",
    "encounter": "encounters",
    "quest": "quests",
    "faction": "factions",
    "creature": "creatures",
    "item_design": "item_designs",
    "spell_design": "spell_designs",
    "background_design": "background_designs",
    "map": "maps",
}
# The kinds placed by a scope (world, adventure, chapter).
SCOPED = ("npc", "place", "treasure", "map")
# The kinds an adventure holds.
HELD = ("npc", "place", "treasure", "encounter")
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
_FRONT_ID = re.compile(r"^id:\s*['\"]?([0-9a-f]{12})", re.MULTILINE)
# The picture field of each kind, a path relative to its owner's folder.
PICTURES = {
    "npc": "portrait",
    "place": "picture",
    "encounter": "cover",
    "quest": "cover",
    "faction": "cover",
    "map": "image",
}
# The kinds that carry a token of the table's own (app/services/tokens.py OWN_KINDS).
TOKENS = ("npc", "treasure", "creature")
# The kinds a delete keeps in Recently deleted; the others move in place.
TRASHED = ("adventure", "npc", "place", "treasure", "encounter", "map")

# The file a bundle is saved as.
FORMAT = 1
MANIFEST = "paravent-pieces.json"
ENTRIES = "entries.json"
ADVENTURES = "adventures/"
PICTURE_DIR = "pictures/"
TOKEN_DIR = "tokens/"
SUFFIX = ".paravent-pieces.zip"

Role = Literal["main", "inside", "named"]


class WorldCopyError(Translatable):
    """A copy that cannot be made: no such piece, the same world, no such target, a file
    that is not a bundle."""


@dataclass(frozen=True)
class Piece:
    """One thing in a copy: its kind, id and name."""

    kind: str
    id: str
    name: str


@dataclass
class Entry:
    """One piece in a bundle: chosen (``main``), inside a chosen adventure, or named by a
    text (``named``). An adventure or chapter has no ``data``: it is in the bundle's files."""

    kind: str
    id: str
    name: str
    role: Role
    data: dict[str, Any] | None = None
    # A picture outside a carried adventure's folder: its path and bytes.
    picture: tuple[str, bytes] | None = None
    # Its own tokens: (suffix, bytes).
    tokens: list[tuple[str, bytes]] = field(default_factory=list)
    # References to the books that its data alone does not show (an adventure's base).
    refs: list[str] = field(default_factory=list)

    @property
    def piece(self) -> Piece:
        return Piece(self.kind, self.id, self.name)


@dataclass
class Bundle:
    """Pieces gathered to be written into a world or saved as a file."""

    entries: list[Entry] = field(default_factory=list)
    # Each carried adventure's folder: its id → {path in the folder: bytes}.
    adventures: dict[str, dict[str, bytes]] = field(default_factory=dict)
    source: str = ""  # the world it came from, by name
    world_id: str = ""  # and by id, where it came from this table

    @property
    def mains(self) -> list[Entry]:
        return [e for e in self.entries if e.role == "main"]

    def entry(self, record_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == record_id), None)


@dataclass
class Plan:
    """What writing a bundle into a world will do, shown before it is made."""

    pieces: list[Piece]
    target: World | None
    bring: bool = True
    # World-level objects its texts name, taken along (or left behind, when not bringing).
    brought: list[Piece] = field(default_factory=list)
    # Named, but the target has one of that name: links find the target's.
    reused: list[Piece] = field(default_factory=list)
    # References outside the target's books (``Goblin|MM``), each once.
    outside: list[str] = field(default_factory=list)
    # The names the chosen pieces take where the target has theirs (id → name).
    renamed: dict[str, str] = field(default_factory=dict)
    source: str = ""


@dataclass
class Copied:
    """What a copy made: the plan, the chosen pieces' copies (old id → new), and how much."""

    plan: Plan
    ids: dict[str, str]
    kinds: dict[str, str] = field(default_factory=dict)
    moved: bool = False
    files: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def first(self) -> tuple[str, str] | None:
        """The kind and new id of the first piece chosen, to lead to."""
        for old, new in self.ids.items():
            return self.kinds.get(old, ""), new
        return None


class WorldCopy:
    """Copies or moves pieces of a world into another, or into a file and back."""

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

    # -- reading the store -----------------------------------------------------------

    def _collection(self, kind: str) -> Collection[Any]:
        collection = getattr(self._store, COLLECTIONS[kind])
        assert isinstance(collection, Collection)
        return collection

    def find(self, kind: str, record_id: str) -> Record | None:
        """The piece, or None."""
        if kind == "adventure":
            return self._store.adventures.find(record_id)
        if kind == "chapter":
            return self._store.chapters.find(record_id)
        if kind not in COLLECTIONS:
            return None
        found: Record | None = self._collection(kind).find(record_id)
        return found

    def world_of(self, record: Record | None) -> str | None:
        """The world a piece belongs to."""
        scope = getattr(record, "scope", None)
        if isinstance(scope, Scope):
            return scope.world_id
        return getattr(record, "world_id", None)

    def targets(self, kind: str, ids: Sequence[str]) -> list[World]:
        """The worlds pieces may go to: every other one, by name."""
        here = self.world_of(self.find(kind, ids[0])) if ids else None
        worlds = [w for w in self._store.worlds.list() if w.id != here]
        return sorted(worlds, key=lambda w: w.name.casefold())

    # -- gathering --------------------------------------------------------------------

    def bundle(self, kind: str, ids: Sequence[str]) -> Bundle:
        """The pieces of one kind and one world, with what lies in them and what their
        texts name. Raises :class:`WorldCopyError` for a piece that is not there."""
        records = [self.find(kind, i) for i in dict.fromkeys(ids)]
        chosen = [r for r in records if r is not None]
        if not chosen or len(chosen) != len(records):
            raise WorldCopyError("copy.error.no_piece")
        worlds = {self.world_of(r) for r in chosen}
        if len(worlds) != 1:
            raise WorldCopyError("copy.error.mixed")
        if any(isinstance(r, WorldMap) and r.scope.adventure_id for r in chosen):
            # An adventure's map goes only with its adventure (documentation/MAPS.md §3).
            raise WorldCopyError("copy.error.map_in_adventure")
        world_id = worlds.pop() or ""
        world = self._store.worlds.find(world_id)
        bundle = Bundle(source=world.name if world is not None else "", world_id=world_id)
        for record in chosen:
            if bundle.entry(record.id) is None:  # an NPC inside an adventure also chosen
                self._gather(bundle, kind, record)
        own = {e.id for e in bundle.entries}
        bundle.entries.extend(self._named(bundle, world_id, own))
        for entry in bundle.entries:
            self._attach_files(bundle, entry, world_id)
        return bundle

    def _gather(self, bundle: Bundle, kind: str, record: Record) -> None:
        if kind != "adventure":
            bundle.entries.append(self._entry(kind, record, "main"))
            return
        assert isinstance(record, Adventure)
        refs = [f"{record.name}|{record.based_on}"] if record.based_on else []
        bundle.entries.append(Entry("adventure", record.id, record.name, "main", refs=refs))
        for chapter in self._store.chapters.list(lambda c: c.adventure_id == record.id):
            bundle.entries.append(Entry("chapter", chapter.id, chapter.title, "inside"))
        for inner in HELD:
            for found in self._collection(inner).list():
                scope = found.scope
                if scope is not None and scope.adventure_id == record.id:
                    bundle.entries.append(self._entry(inner, found, "inside"))
        # Its maps stick to it (documentation/MAPS.md §3); their pictures are in its folder.
        for found in self._store.maps.list(lambda m: m.scope.adventure_id == record.id):
            bundle.entries.append(self._entry("map", found, "inside"))
        folder = self._store.prose.folder(record.id)
        files: dict[str, bytes] = {}
        bundle.adventures[record.id] = files
        if folder is None:
            return
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.name.startswith("."):
                suffix = path.suffix.lower()
                if suffix in (".md", ".json") or suffix in IMAGE_TYPES:
                    files[path.relative_to(folder).as_posix()] = path.read_bytes()

    @staticmethod
    def _entry(kind: str, record: Record, role: Role) -> Entry:
        return Entry(kind, record.id, _name_of(record), role, record.model_dump(mode="json"))

    def _texts(self, bundle: Bundle) -> list[str]:
        texts: list[str] = []
        for entry in bundle.entries:
            if entry.role == "named":
                continue
            record = self.find(entry.kind, entry.id)
            for key in ("body", "summary", "notes", "text", "note"):
                value = getattr(record, key, None)
                if isinstance(value, str) and value:
                    texts.append(value)
        return texts

    def _named(self, bundle: Bundle, world: str, own: set[str]) -> list[Entry]:
        """The world-level objects the pieces' texts link to, an adventure uses, or a
        quest serves (its faction) — not those carried anyway."""
        found: dict[str, Entry] = {}

        def add(kind: str, record: Record | None) -> None:
            if record is not None and record.id not in own:
                found.setdefault(record.id, self._entry(kind, record, "named"))

        for text in self._texts(bundle):
            for tag, name in _LINK.findall(text):
                add(tag, self._world_level(world, tag, name.strip()))
            for address, ident in _ADDRESS.findall(text):
                kind = _ADDRESS_KIND[address]
                hit = self._collection(kind).find(ident)
                if hit is not None and hit.scope is not None and hit.scope.world_id == world:
                    add(kind, hit)
        for entry in bundle.mains:
            if entry.kind == "adventure":
                adventure = self._store.adventures.find(entry.id)
                for used in adventure.uses if adventure is not None else []:
                    for kind in HELD:
                        add(kind, self._collection(kind).find(used))
            faction = (entry.data or {}).get("faction_id") if entry.kind == "quest" else None
            if faction:
                add("faction", self._store.factions.find(faction))
        return list(found.values())

    def _world_level(self, world_id: str, kind: str, name: str) -> Record | None:
        """The world's own object of that kind and name (not one inside an adventure)."""
        wanted = name.casefold()
        for record in self._collection(kind).list():
            if kind in ("faction", "quest"):
                if record.world_id == world_id and _name_of(record).casefold() == wanted:
                    return record  # type: ignore[no-any-return]
                continue
            scope = record.scope
            if scope is None or scope.world_id != world_id or scope.adventure_id is not None:
                continue
            if _name_of(record).casefold() == wanted:
                return record  # type: ignore[no-any-return]
        return None

    def _attach_files(self, bundle: Bundle, entry: Entry, world_id: str) -> None:
        """A record's picture (where no carried adventure's folder holds it) and tokens."""
        if entry.data is None:
            return
        key = PICTURES.get(entry.kind)
        raw = entry.data.get(key) if key else None
        owner = _owner_of_data(entry.data)
        if isinstance(raw, str) and raw and owner and not self._carried_folder(bundle, owner):
            folder = self._store.prose.folder(owner)
            relative = raw.partition("|")[0]
            source = folder / relative if folder is not None else None
            if source is not None and source.is_file():
                entry.picture = (relative, source.read_bytes())
        if entry.kind in TOKENS:
            tokens = self._token_folder(entry.data.get("world_id") or world_id, entry.kind)
            for path in sorted(tokens.glob(f"{entry.id}.*")) if tokens is not None else ():
                entry.tokens.append((path.suffix, path.read_bytes()))

    @staticmethod
    def _carried_folder(bundle: Bundle, owner: str) -> bool:
        """Whether ``owner`` (an adventure or chapter) has its folder in the bundle."""
        entry = bundle.entry(owner)
        return entry is not None and entry.kind in ("adventure", "chapter")

    # -- planning ---------------------------------------------------------------------

    def plan(
        self, bundle: Bundle, target_id: str | None, *, bring: bool = True, moving: bool = False
    ) -> Plan:
        """What writing the bundle into ``target_id`` would do (None: into a file);
        ``moving`` a design keeps its name, which the table's one name space holds."""
        target = self._store.worlds.find(target_id) if target_id else None
        if target_id and target is None:
            raise WorldCopyError("copy.error.no_world")
        if target is not None and target.id == bundle.world_id:
            raise WorldCopyError("copy.error.same_world")
        plan = Plan(
            [e.piece for e in bundle.entries if e.role != "named"],
            target,
            bring=bring,
            source=bundle.source,
        )
        for entry in bundle.entries:
            if entry.role != "named":
                continue
            if target is not None and self._world_level(target.id, entry.kind, entry.name):
                plan.reused.append(entry.piece)
            else:
                plan.brought.append(entry.piece)
        if target is not None:
            plan.renamed = self._renames(bundle, target.id, moving)
            reused = {p.id for p in plan.reused}
            plan.outside = _outside(_carried(bundle, bring, reused), target)
        return plan

    def _renames(self, bundle: Bundle, target_id: str, moving: bool = False) -> dict[str, str]:
        """The names the chosen pieces take where the target (or another chosen piece)
        has theirs."""
        renamed: dict[str, str] = {}
        taken_by_kind: dict[str, set[str]] = {}
        for entry in bundle.mains:
            if entry.kind not in taken_by_kind:
                taken = self._taken(entry.kind, target_id)
                if moving and entry.kind in DESIGNS:  # a moved design leaves its name free
                    taken -= {e.name.casefold() for e in bundle.mains if e.kind == entry.kind}
                taken_by_kind[entry.kind] = taken
            taken = taken_by_kind[entry.kind]
            name = entry.name
            if name.casefold() in taken:
                number = 2
                while f"{name} ({number})".casefold() in taken:
                    number += 1
                name = f"{name} ({number})"
                renamed[entry.id] = name
            taken.add(name.casefold())
        return renamed

    def _taken(self, kind: str, target_id: str) -> set[str]:
        """The names of that kind a piece may not take in the target."""
        if kind == "adventure":
            rows = self._store.adventures.list(lambda a: a.world_id == target_id)
            return {a.name.casefold() for a in rows}
        if kind in DESIGNS:
            return {d.name.casefold() for d in self._collection(kind).list()}
        names: set[str] = set()
        for record in self._collection(kind).list():
            if kind in ("quest", "faction"):
                if record.world_id == target_id:
                    names.add(_name_of(record).casefold())
                continue
            scope = record.scope
            if scope is not None and scope.world_id == target_id and scope.adventure_id is None:
                names.add(_name_of(record).casefold())
        return names

    # -- writing ----------------------------------------------------------------------

    def copy(
        self,
        kind: str,
        ids: Sequence[str],
        target_id: str,
        *,
        bring: bool = True,
        fresh: bool = True,
    ) -> Copied:
        """Copy pieces (and what comes with them) into another world; ``fresh``, as a new
        group meets them: an adventure and its chapters planned again."""
        return self.write(self.bundle(kind, ids), target_id, bring=bring, fresh=fresh)

    def write(
        self, bundle: Bundle, target_id: str, *, bring: bool = True, fresh: bool = True
    ) -> Copied:
        """Write a bundle into a world: every id new, links pointed there."""
        plan = self.plan(bundle, target_id, bring=bring)
        target = plan.target
        if target is None:
            raise WorldCopyError("copy.error.no_world")
        reused_ids = {p.id for p in plan.reused}
        carried = _carried(bundle, bring, reused_ids)
        ids = {e.id: _text_id() for e in carried}
        for files in bundle.adventures.values():  # ids only the files know
            for relative, content in files.items():
                if relative.endswith(".md"):
                    for old in _FRONT_ID.findall(content.decode("utf-8", "replace")):
                        ids.setdefault(old, _text_id())
        reused: dict[str, str] = {}
        for piece in plan.reused:
            hit = self._world_level(target.id, piece.kind, piece.name)
            if hit is not None:
                reused[piece.id] = hit.id
        remap = _remapper(ids | reused)
        done = Copied(
            plan,
            {e.id: ids[e.id] for e in bundle.mains},
            {e.id: e.kind for e in bundle.mains},
        )
        for adventure_id, files in bundle.adventures.items():
            done.files += self._write_adventure(plan, adventure_id, files, remap, fresh)
        copied_ids = set(ids.values())
        for entry in carried:
            if entry.data is not None:
                done.files += self._write_record(plan, entry, ids, reused, copied_ids, remap, done)
        return done

    def move(self, kind: str, ids: Sequence[str], target_id: str, *, bring: bool = True) -> Copied:
        """Copy pieces into the target, then delete them here into the trash; quests,
        factions and creature designs move in place."""
        if kind not in TRASHED:
            return self._move_in_place(kind, ids, target_id, bring=bring)
        done = self.copy(kind, ids, target_id, bring=bring, fresh=False)
        for record_id in dict.fromkeys(ids):
            if self.find(kind, record_id) is not None:  # not gone with an adventure moved
                self._delete(kind, record_id)
        done.moved = True
        return done

    def _move_in_place(
        self, kind: str, ids: Sequence[str], target_id: str, *, bring: bool
    ) -> Copied:
        bundle = self.bundle(kind, ids)
        plan = self.plan(bundle, target_id, bring=bring, moving=True)
        if plan.target is None:
            raise WorldCopyError("copy.error.no_world")
        done = Copied(plan, {}, moved=True)
        for entry in bundle.mains:
            changes: dict[str, Any] = {"world_id": target_id}
            if entry.id in plan.renamed:
                changes["name"] = plan.renamed[entry.id]
            if kind == "quest":
                changes.update(adventure_id=None, characters=[], status="open")
                changes["faction_id"] = self._faction_in(bundle, entry, target_id, bring, done)
            if kind == "faction":
                changes["renown"] = {}
            data = {**(entry.data or {}), **changes}
            done.files += self._place_files(entry, data, target_id, entry.id, None)
            key = PICTURES.get(kind)
            if key and data.get(key) != (entry.data or {}).get(key):
                changes[key] = data[key]
            self._collection(kind).update(entry.id, **changes)
            done.ids[entry.id] = entry.id
            done.kinds[entry.id] = kind
        return done

    def _faction_in(
        self, bundle: Bundle, quest: Entry, target_id: str, bring: bool, done: Copied
    ) -> str | None:
        """The faction a moved quest serves in the target: its namesake there, a copy, or
        none."""
        faction = (quest.data or {}).get("faction_id")
        entry = bundle.entry(faction) if faction else None
        if entry is None:
            return None
        hit = self._world_level(target_id, "faction", entry.name)
        if hit is not None:
            return hit.id
        if not bring:
            return None
        alone = Bundle([replace(entry, role="main")], source=bundle.source)
        made = self.write(alone, target_id)
        done.files += made.files
        return made.ids.get(entry.id)

    def _write_adventure(
        self,
        plan: Plan,
        adventure_id: str,
        files: dict[str, bytes],
        remap: Callable[[str], str],
        fresh: bool,
    ) -> int:
        """An adventure's folder into the target's, text with its ids replaced."""
        store = self._store
        assert plan.target is not None
        world_folder = store.prose.world_folder(plan.target.id)
        if world_folder is None or ADVENTURE_FILE not in files:
            return 0
        name = plan.renamed.get(adventure_id) or _front_name(files[ADVENTURE_FILE])
        folder = _free(world_folder / ADVENTURES_DIR / slugify(name or "adventure", "adventure"))
        written = 0
        for relative, content in files.items():
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative:
                continue
            suffix = path.suffix.lower()
            if suffix in (".md", ".json"):
                content = remap(content.decode("utf-8", "replace")).encode("utf-8")
            elif suffix not in IMAGE_TYPES or sniff(content) is None:
                continue
            target = folder / remap(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            written += 1
        store.prose.reload_paths(folder.rglob("*.md"))
        new_id = remap(adventure_id)
        copied = store.adventures.find(new_id)
        if copied is not None:
            uses = [u for u in (remap(x) for x in copied.uses) if self._held(u)]
            changes: dict[str, Any] = {"uses": uses, "drafted_by": None, "revision_of": None}
            if adventure_id in plan.renamed:
                changes["name"] = plan.renamed[adventure_id]
            if fresh:
                changes["status"] = "planned"
                for chapter in store.chapters.list(lambda c: c.adventure_id == new_id):
                    if chapter.status != "planned":
                        store.chapters.update(chapter.id, status="planned")
            store.adventures.update(new_id, **changes)
        return written

    def _held(self, record_id: str) -> bool:
        return any(self._collection(k).find(record_id) is not None for k in HELD)

    def _write_record(
        self,
        plan: Plan,
        entry: Entry,
        ids: dict[str, str],
        reused: dict[str, str],
        copied_ids: set[str],
        remap: Callable[[str], str],
        done: Copied,
    ) -> int:
        """One record into the target, its world and links pointed there."""
        assert entry.data is not None and plan.target is not None
        target = plan.target.id
        data = json.loads(remap(json.dumps(entry.data)))
        if entry.kind in SCOPED:
            scope = data.get("scope") or {}
            inside = scope.get("adventure_id") in copied_ids
            data["scope"] = {
                "world_id": target,
                "adventure_id": scope.get("adventure_id") if inside else None,
                "chapter_id": scope.get("chapter_id") if inside else None,
            }
        else:
            data["world_id"] = target
        if entry.kind == "encounter" and data.get("adventure_id") not in copied_ids:
            data["adventure_id"] = data["chapter_id"] = None
        if entry.kind == "quest":
            data.update(adventure_id=None, characters=[], status="open")
            data["steps"] = [{**s, "done": False} for s in data.get("steps", [])]
            if data.get("faction_id") not in copied_ids | set(reused.values()):
                data["faction_id"] = None
        if entry.kind == "faction":
            data["renown"] = {}
        if entry.kind in DESIGNS:
            data.update(published_at=None, published_hash=None)
            if entry.kind != "creature":
                data.update(revision_of=None)
        if entry.id in plan.renamed:
            data["name"] = plan.renamed[entry.id]
        files = self._place_files(entry, data, target, ids[entry.id], copied_ids)
        collection = self._collection(entry.kind)
        try:
            record = collection._model.model_validate(data)
        except ValidationError:
            done.skipped.append(f"{entry.kind}: {entry.name}")
            return files
        collection.upsert_many([record])
        return files

    def _place_files(
        self,
        entry: Entry,
        data: dict[str, Any],
        target_world: str,
        new_id: str,
        copied_ids: set[str] | None,
    ) -> int:
        """A record's picture and tokens into the target's folder; ``data`` gets the
        picture's path where it had to take another name."""
        files = 0
        key = PICTURES.get(entry.kind)
        owner = _owner_of_data(data)
        # Inside a copied adventure, its folder brought the picture along.
        inside = copied_ids is not None and owner in copied_ids
        if entry.picture is not None and key and owner and not inside:
            folder = self._store.prose.folder(owner)
            relative, content = entry.picture
            if folder is not None:
                placed = _place(content, folder, relative)
                if placed is not None:
                    raw = str(data.get(key) or "")
                    data[key] = placed + raw[len(raw.partition("|")[0]) :]
                    files += 1
        if entry.tokens:
            tokens = self._token_folder(target_world, entry.kind)
            if tokens is not None:
                tokens.mkdir(parents=True, exist_ok=True)
                for suffix, content in entry.tokens:
                    if suffix.lower() in IMAGE_TYPES and sniff(content) is not None:
                        (tokens / f"{new_id}{suffix.lower()}").write_bytes(content)
                        files += 1
        return files

    # -- files ------------------------------------------------------------------------

    def save(self, bundle: Bundle, out: IO[bytes], *, bring: bool = True) -> None:
        """Write a bundle as a zip file into ``out``; without ``bring``, what its texts
        name stays out."""
        entries = [e for e in bundle.entries if e.role != "named" or bring]
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as pack:
            manifest = {
                "paravent": "pieces",
                "format": FORMAT,
                "app": __version__,
                "saved_at": datetime.now(UTC).isoformat(),
                "source": bundle.source,
                "pieces": [{"kind": e.kind, "name": e.name} for e in bundle.mains],
            }
            pack.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
            rows = []
            for entry in entries:
                row: dict[str, Any] = {
                    "kind": entry.kind,
                    "id": entry.id,
                    "name": entry.name,
                    "role": entry.role,
                    "data": entry.data,
                    "refs": entry.refs,
                }
                if entry.picture is not None:
                    relative, content = entry.picture
                    row["picture"] = relative
                    member = f"{PICTURE_DIR}{entry.id}/{PurePosixPath(relative).name}"
                    pack.writestr(member, content)
                for suffix, content in entry.tokens:
                    pack.writestr(f"{TOKEN_DIR}{entry.id}{suffix}", content)
                rows.append(row)
            pack.writestr(ENTRIES, json.dumps(rows, ensure_ascii=False))
            for adventure_id, files in bundle.adventures.items():
                for relative, content in files.items():
                    pack.writestr(f"{ADVENTURES}{adventure_id}/{relative}", content)

    def saved(self, bundle: Bundle, *, bring: bool = True) -> bytes:
        """A bundle as a zip file, in memory."""
        out = io.BytesIO()
        self.save(bundle, out, bring=bring)
        return out.getvalue()

    def read(self, data: bytes | IO[bytes]) -> Bundle:
        """A bundle from a saved file. Raises :class:`WorldCopyError` for anything else."""
        try:
            pack = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data)
        except zipfile.BadZipFile as exc:
            raise WorldCopyError("copy.error.not_pieces") from exc
        with pack:
            try:
                members = _checked(pack.infolist())
            except WorldTransferError as exc:
                raise WorldCopyError("copy.error.not_pieces") from exc
            manifest = _json(pack, members, MANIFEST)
            if not isinstance(manifest, dict) or manifest.get("paravent") != "pieces":
                raise WorldCopyError("copy.error.not_pieces")
            if int(manifest.get("format", 0)) > FORMAT:
                raise WorldCopyError("copy.error.newer")
            rows = _json(pack, members, ENTRIES)
            if not isinstance(rows, list):
                raise WorldCopyError("copy.error.not_pieces")
            bundle = Bundle(source=str(manifest.get("source", ""))[:120])
            for row in rows:
                entry = _entry_of(row, pack, members)
                if entry is not None and bundle.entry(entry.id) is None:
                    bundle.entries.append(entry)
            for name, info in members.items():
                if not name.startswith(ADVENTURES):
                    continue
                adventure_id, _, relative = name[len(ADVENTURES) :].partition("/")
                held = bundle.entry(adventure_id)
                if held is not None and held.kind == "adventure" and relative:
                    bundle.adventures.setdefault(adventure_id, {})[relative] = pack.read(info)
        if not bundle.mains:
            raise WorldCopyError("copy.error.not_pieces")
        return bundle


def _carried(bundle: Bundle, bring: bool, reused: set[str]) -> list[Entry]:
    """The entries written: all but the named ones left behind or found in the target."""
    return [e for e in bundle.entries if e.role != "named" or (bring and e.id not in reused)]


def _entry_of(row: Any, pack: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo]) -> Entry | None:
    """One saved entry, or None for one that is not of a known kind and shape."""
    if not isinstance(row, dict):
        return None
    kind, ident, role = str(row.get("kind", "")), str(row.get("id", "")), row.get("role")
    if kind not in (*KINDS, "chapter") or not _ID.fullmatch(ident):
        return None
    if role not in ("main", "inside", "named"):
        return None
    data = row.get("data")
    if kind in COLLECTIONS and not isinstance(data, dict):
        return None
    entry = Entry(
        kind,
        ident,
        str(row.get("name", ""))[:200],
        role,
        data if isinstance(data, dict) else None,
    )
    refs = row.get("refs")
    entry.refs = [str(r)[:200] for r in refs] if isinstance(refs, list) else []
    picture = row.get("picture")
    if isinstance(picture, str):
        member = f"{PICTURE_DIR}{ident}/{PurePosixPath(picture).name}"
        if member in members:
            entry.picture = (picture, pack.read(members[member]))
    for name, info in members.items():
        if name.startswith(f"{TOKEN_DIR}{ident}."):
            entry.tokens.append((PurePosixPath(name).suffix, pack.read(info)))
    return entry


def _json(pack: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], name: str) -> Any:
    if name not in members:
        raise WorldCopyError("copy.error.not_pieces")
    try:
        return json.loads(pack.read(members[name]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WorldCopyError("copy.error.not_pieces") from exc


def _name_of(record: Record | None) -> str:
    if record is None:
        return ""
    return str(getattr(record, "name", None) or getattr(record, "title", "") or "")


def _front_name(content: bytes) -> str:
    found = re.search(r"^name:\s*(.+)$", content.decode("utf-8", "replace"), re.MULTILINE)
    return found.group(1).strip("'\" ") if found else ""


def _references(entry: Entry) -> list[str]:
    """The ``Name|SOURCE`` references a piece makes to the books."""
    data = entry.data or {}
    refs = list(entry.refs)
    if entry.kind == "encounter":
        refs += [f"{m.get('name')}|{m.get('source')}" for m in data.get("monsters") or []]
    if entry.kind == "npc" and "|" in str(data.get("statblock") or ""):
        refs.append(str(data["statblock"]))
    if entry.kind == "treasure":
        refs += [f"{i.get('name')}|{i.get('source')}" for i in data.get("items") or []]
    return refs


def _outside(entries: Iterable[Entry], target: World) -> list[str]:
    """The references the entries make that the target's books lack, each once."""
    books = target.source_set
    if books.is_unrestricted:
        return []
    missing: dict[str, None] = {}
    for entry in entries:
        for ref in _references(entry):
            source = ref.rpartition("|")[2]
            if source and not books.allows(source):
                missing.setdefault(ref, None)
    return list(missing)


def _owner_of_data(data: dict[str, Any]) -> str | None:
    """The record whose folder holds a piece's picture: its scope's narrowest, else its
    world."""
    scope = data.get("scope")
    if isinstance(scope, dict):
        found = scope.get("chapter_id") or scope.get("adventure_id") or scope.get("world_id")
        return str(found) if found else None
    world = data.get("world_id")
    return str(world) if world else None


def _place(content: bytes, folder: Path, relative: str) -> str | None:
    """Write a picture to ``folder/relative``, or to a free name beside it when a different
    file is there; the relative path used. A path that leaves the folder, or bytes that are
    no picture, are refused (None)."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in IMAGE_TYPES:
        return None
    if sniff(content) is None:
        return None
    target = folder / str(path)
    if target.is_file() and target.read_bytes() == content:
        return str(path)
    number = 2
    while target.exists():
        path = PurePosixPath(relative).with_name(
            f"{PurePosixPath(relative).stem}-{number}{PurePosixPath(relative).suffix}"
        )
        target = folder / str(path)
        number += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
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


__all__ = [
    "KINDS",
    "SUFFIX",
    "Bundle",
    "Copied",
    "Entry",
    "Piece",
    "Plan",
    "WorldCopy",
    "WorldCopyError",
]
