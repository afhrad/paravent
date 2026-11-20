"""A whole world as one file, and back (documentation/CURRENT-WORLD.md §9).

**Export** packs a world into a zip: its folder (the world, its adventures and chapters as
Markdown, its pictures, tags and tokens), every record filed under it (``records.json``),
its log (``protocol.jsonl``) and the pictures of its handouts. Characters are not in it:
they belong to the table, not to a world.

**Import** makes a new world of such a zip, every id inside it given anew, so it never
clashes with what the table has, not even with the world it came from. It takes the
campaign — adventures, chapters, pictures, NPCs, places, treasure, encounters, factions,
quests, the instructions for Claude, the world's naming styles and creatures — and, if
asked, the play so far: the log, the clock, fights, checks, the journal's handouts and
party notes, session notes, what the party learned of creatures, the shop. Without the
play, quests start open with nobody on them and factions with no renown.

**Duplicate** is an export read straight back in: the same campaign for a second group.

Ids are twelve hex digits (``new_id``). Every one the package holds — the world's, its
adventures' and chapters', each record's — is mapped to a fresh one and replaced wherever
it is written: in front matter, in records that name each other (an NPC's scope, a quest's
faction, a fight's encounter), in Markdown links and in token file names. Anything else
that looks like an id (a character's) is left as it is.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any

from pydantic import ValidationError

from app import __version__
from app.models.gamestate import Record, World, new_id
from app.services.i18n import Translatable
from app.services.media import IMAGE_TYPES, sniff
from app.store.gamestate import Collection, GameStateStore
from app.store.prose import WORLD_FILE, slugify, write_text

FORMAT = 1
MANIFEST = "paravent-world.json"
RECORDS = "records.json"
PROTOCOL = "protocol.jsonl"
WORLD_DIR = "world/"
HANDOUTS_DIR = "handouts/"

# What a package may hold, so a file from elsewhere cannot fill the disk.
MAX_FILES = 10_000
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
TEXT_SUFFIXES = (".md", ".json")

_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
_FRONT_ID = re.compile(r"^id:\s*['\"]?([0-9a-f]{12})", re.MULTILINE)

# Records filed under a world, by how they name it. ``scope``: an NPC's, place's or
# treasure's scope; ``world``: a ``world_id`` field; ``id``: the record's id
# is the world's; ``for_world``: a naming style made for it.
CAMPAIGN: dict[str, str] = {
    "npcs": "scope",
    "locations": "scope",
    "treasures": "scope",
    "encounters": "world",
    "instructions": "world",
    "factions": "world",
    "quests": "world",
    "creatures": "world",
    "naming_styles": "for_world",
    "safety": "id",
}
PLAY: dict[str, str] = {
    "combats": "world",
    "session_notes": "world",
    "checks": "world",
    "handouts": "world",
    "knowledge": "id",
    "party_notes": "id",
    "clocks": "id",
    "chases": "id",
    "play_sessions": "id",
    "shops": "id",
}


class WorldTransferError(Translatable):
    """A package that cannot be read, or a world that cannot be packed."""


@dataclass
class Imported:
    """What an import made: the new world and how much came with it."""

    world: World
    records: int = 0
    files: int = 0
    skipped: list[str] = field(default_factory=list)  # files left out, by name
    creatures_kept: list[str] = field(default_factory=list)  # designs the table had


class WorldTransfer:
    """Packs a world into a zip and makes a new world of one."""

    def __init__(
        self,
        store: GameStateStore,
        protocol_dir: Path,
        handouts_dir: Path,
    ) -> None:
        self._store = store
        self._protocol_dir = protocol_dir
        self._handouts_dir = handouts_dir

    # -- export ------------------------------------------------------------------

    def _collection(self, name: str) -> Collection[Any]:
        collection = getattr(self._store, name)
        assert isinstance(collection, Collection)
        return collection

    def _owned(self, world_id: str, how: str, record: Any) -> bool:
        if how == "scope":
            return bool(record.scope.world_id == world_id)
        if how == "world":
            return getattr(record, "world_id", None) == world_id
        if how == "for_world":
            return getattr(record, "for_world", None) == world_id
        return bool(record.id == world_id)

    def records(self, world_id: str) -> dict[str, list[Any]]:
        """Every record filed under the world, by collection."""
        found: dict[str, list[Any]] = {}
        for name, how in (CAMPAIGN | PLAY).items():
            rows = [r for r in self._collection(name).list() if self._owned(world_id, how, r)]
            if rows:
                found[name] = rows
        return found

    def export(self, world_id: str) -> bytes:
        """The world as a zip file, in memory."""
        out = io.BytesIO()
        self.export_to(world_id, out)
        return out.getvalue()

    def export_to(self, world_id: str, out: IO[bytes]) -> None:
        """Write the world as a zip file into ``out`` (a file for a large world)."""
        world = self._store.worlds.find(world_id)
        folder = self._store.prose.world_folder(world_id)
        if world is None or folder is None:
            raise WorldTransferError("transfer.error.no_world")
        records = self.records(world_id)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as pack:
            manifest = {
                "paravent": "world",
                "format": FORMAT,
                "app": __version__,
                "exported_at": datetime.now(UTC).isoformat(),
                "world": {"id": world.id, "name": world.name},
            }
            pack.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
            dumped = {
                name: [r.model_dump(mode="json") for r in rows] for name, rows in records.items()
            }
            pack.writestr(RECORDS, json.dumps(dumped, ensure_ascii=False))
            for path in sorted(folder.rglob("*")):
                if path.is_file() and not path.name.startswith("."):
                    pack.write(path, WORLD_DIR + path.relative_to(folder).as_posix())
            log = self._protocol_dir / f"{world_id}.jsonl"
            if log.is_file():
                pack.write(log, PROTOCOL)
            for handout in records.get("handouts", []):
                own = self._handouts_dir / handout.id
                for path in sorted(own.rglob("*")) if own.is_dir() else ():
                    if path.is_file():
                        relative = path.relative_to(self._handouts_dir).as_posix()
                        pack.write(path, HANDOUTS_DIR + relative)

    # -- import ------------------------------------------------------------------

    def import_world(
        self, data: bytes | IO[bytes], *, name: str = "", play: bool = False
    ) -> Imported:
        """Make a new world of a package; ``play`` brings the play so far too.

        Raises :class:`WorldTransferError` for anything that is not a world package.
        """
        try:
            pack = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data)
        except zipfile.BadZipFile as exc:
            raise WorldTransferError("transfer.error.not_zip") from exc
        with pack:
            members = _checked(pack.infolist())
            manifest = _json(pack, MANIFEST, members)
            if not isinstance(manifest, dict) or manifest.get("paravent") != "world":
                raise WorldTransferError("transfer.error.not_world")
            if int(manifest.get("format", 0)) > FORMAT:
                raise WorldTransferError("transfer.error.newer")
            raw_records = _json(pack, RECORDS, members) if RECORDS in members else {}
            if not isinstance(raw_records, dict):
                raise WorldTransferError("transfer.error.not_world")
            world_files = {
                n[len(WORLD_DIR) :]: members[n] for n in members if n.startswith(WORLD_DIR)
            }
            if WORLD_FILE not in world_files:
                raise WorldTransferError("transfer.error.not_world")
            ids = self._ids(pack, world_files, raw_records, manifest)
            remap = _remapper(ids)
            done = Imported(world=World(name="-"))
            folder = self._write_world(pack, world_files, remap, done)
            self._store.prose.reload_paths(folder.rglob("*.md"))
            new_world_id = ids.get(str(manifest.get("world", {}).get("id", "")), "")
            world = self._store.worlds.find(new_world_id)
            if world is None:  # the package named another id than its world.md
                found = self._store.prose.find("world", _front_id(folder / WORLD_FILE) or "")
                if not isinstance(found, World):
                    raise WorldTransferError("transfer.error.not_world")
                world = found
            wanted = self._free_name(name.strip() or world.name, world.id)
            if wanted != world.name:
                world = self._store.worlds.update(world.id, name=wanted)
            self._write_records(raw_records, remap, world.id, play, done)
            if play:
                self._write_play_files(pack, members, remap, world.id, done)
        done.world = world
        return done

    def _free_name(self, name: str, own_id: str) -> str:
        """``name``, or ``name (2)``, ``(3)`` … when another world has it: two worlds of one
        name could not be told apart in the world menu."""
        taken = {w.name.casefold() for w in self._store.worlds.list() if w.id != own_id}
        candidate, number = name, 2
        while candidate.casefold() in taken:
            candidate = f"{name} ({number})"
            number += 1
        return candidate

    def duplicate(self, world_id: str, *, name: str, play: bool = False) -> Imported:
        """A copy of the world with new ids, under ``name``."""
        return self.import_world(self.export(world_id), name=name, play=play)

    def _ids(
        self,
        pack: zipfile.ZipFile,
        world_files: dict[str, zipfile.ZipInfo],
        raw_records: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, str]:
        """Every id the package holds, each mapped to a fresh one."""
        old: set[str] = set()
        world = manifest.get("world", {})
        if isinstance(world, dict) and isinstance(world.get("id"), str):
            old.add(world["id"])
        for relative, info in world_files.items():
            if relative.endswith(".md"):
                old.update(_FRONT_ID.findall(pack.read(info).decode("utf-8", "replace")))
        for rows in raw_records.values():
            for row in rows if isinstance(rows, list) else ():
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    old.add(row["id"])
        return {o: _text_id() for o in old if _ID.fullmatch(o)}

    def _write_world(
        self,
        pack: zipfile.ZipFile,
        world_files: dict[str, zipfile.ZipInfo],
        remap: Callable[[str], str],
        done: Imported,
    ) -> Path:
        """The world's folder, under a free name: text with its ids replaced, pictures
        as they are (a token's file named by its element's new id)."""
        front = pack.read(world_files[WORLD_FILE]).decode("utf-8", "replace")
        name = re.search(r"^name:\s*(.+)$", front, re.MULTILINE)
        slug = slugify(name.group(1).strip("'\" ") if name else "world", "world")
        folder = _free(self._store.prose.root / slug)
        for relative, info in world_files.items():
            suffix = PurePosixPath(relative).suffix.lower()
            target = folder / remap(relative)
            content = pack.read(info)
            if suffix in TEXT_SUFFIXES:
                content = remap(content.decode("utf-8", "replace")).encode("utf-8")
            elif suffix not in IMAGE_TYPES or sniff(content) is None:
                done.skipped.append(relative)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # The world file last, so the folder is read as a whole once it is there.
            if relative != WORLD_FILE:
                target.write_bytes(content)
            done.files += 1
        write_text(folder / WORLD_FILE, remap(front))
        return folder

    def _write_records(
        self,
        raw_records: dict[str, Any],
        remap: Callable[[str], str],
        world_id: str,
        play: bool,
        done: Imported,
    ) -> None:
        kinds = CAMPAIGN | (PLAY if play else {})
        designs = {d.name.casefold() for d in self._store.creatures.list()}
        for name, rows in raw_records.items():
            if name not in kinds or not isinstance(rows, list):
                continue
            collection = self._collection(name)
            model = collection._model
            records: list[Record] = []
            for row in rows:
                try:
                    record = model.model_validate(json.loads(remap(json.dumps(row))))
                except ValidationError:
                    done.skipped.append(f"{name}: {row.get('id', '?')}")
                    continue
                if name == "creatures":
                    # The table's own design of that name stays: its statblock is the
                    # one the homebrew file holds (one creature per name and source).
                    design = str(getattr(record, "name", ""))
                    if design.casefold() in designs:
                        done.creatures_kept.append(design)
                        continue
                    record = record.model_copy(
                        update={"world_id": world_id, "published_at": None, "published_hash": None}
                    )
                if not play:
                    record = _fresh(name, record)
                records.append(record)
            collection.upsert_many(records)
            done.records += len(records)

    def _write_play_files(
        self,
        pack: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        remap: Callable[[str], str],
        world_id: str,
        done: Imported,
    ) -> None:
        if PROTOCOL in members:
            text = pack.read(members[PROTOCOL]).decode("utf-8", "replace")
            write_text(self._protocol_dir / f"{world_id}.jsonl", remap(text))
        for member, info in members.items():
            if not member.startswith(HANDOUTS_DIR):
                continue
            relative = remap(member[len(HANDOUTS_DIR) :])
            content = pack.read(info)
            if PurePosixPath(relative).suffix.lower() not in IMAGE_TYPES or sniff(content) is None:
                done.skipped.append(member)
                continue
            target = self._handouts_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            done.files += 1


def _text_id() -> str:
    """A fresh id with a letter in it. Front matter is rewritten as text, and YAML reads
    an id of digits alone (one in 270) as a number, which loses the record."""
    while True:
        ident = new_id()
        if not ident.isdigit():
            return ident


def _fresh(name: str, record: Record) -> Record:
    """A campaign record as a new group meets it: quests open with nobody on them,
    factions with no renown, no suggestions waiting."""
    if name == "quests":
        steps = [s.model_copy(update={"done": False}) for s in getattr(record, "steps", [])]
        return record.model_copy(update={"status": "open", "characters": [], "steps": steps})
    if name == "factions":
        return record.model_copy(update={"renown": {}})
    if name == "safety":
        return record.model_copy(update={"suggested": []})
    return record


def _remapper(ids: dict[str, str]) -> Callable[[str], str]:
    def remap(text: str) -> str:
        return _ID.sub(lambda m: ids.get(m.group(0), m.group(0)), text)

    return remap


def _checked(infos: Iterable[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """The package's files by name, refusing paths that leave it and sizes past the
    limits."""
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            raise WorldTransferError("transfer.error.unsafe")
        if any(part.startswith(".") for part in path.parts):
            continue
        total += info.file_size
        if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(members) >= MAX_FILES:
            raise WorldTransferError("transfer.error.too_big")
        members[info.filename] = info
    return members


def _json(pack: zipfile.ZipFile, name: str, members: dict[str, zipfile.ZipInfo]) -> Any:
    if name not in members:
        raise WorldTransferError("transfer.error.not_world")
    try:
        return json.loads(pack.read(members[name]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise WorldTransferError("transfer.error.not_world") from exc


def _front_id(path: Path) -> str | None:
    match = _FRONT_ID.search(path.read_text(encoding="utf-8")) if path.is_file() else None
    return match.group(1) if match else None


def _free(candidate: Path) -> Path:
    number = 2
    base = candidate
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{number}")
        number += 1
    return candidate


__all__ = ["CAMPAIGN", "PLAY", "Imported", "WorldTransfer", "WorldTransferError"]
