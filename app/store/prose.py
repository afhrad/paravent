"""Worlds, adventures and chapters as Markdown files with front matter.

documentation/ADVENTURES.md §3. Prose is meant to be written in whatever editor the
gamemaster likes, so it lives in real ``.md`` files, one folder per world::

    library/worlds/sword-coast/world.md
    library/worlds/sword-coast/media/cover.webp
    library/worlds/sword-coast/adventures/lost-mine/adventure.md
    library/worlds/sword-coast/adventures/lost-mine/chapters/goblin-arrows.md

The front matter (YAML between ``---`` lines) holds the record's fields; the body is
its Markdown. Where a file sits says what it is and whose it is — an adventure belongs
to the world whose folder it is in — so moving a folder moves the record, and identity
is the ``id`` in the front matter, never the path.

Hand edits are handled the way the character store handles them:

* **A dropped-in file appears.** One without an ``id`` gets one, written back.
* **A copied file is a new record.** A second file claiming a known id gets a fresh one.
* **A broken edit keeps the last valid version** and records why, so a YAML typo never
  makes a chapter vanish mid-session. Fixing the file clears it.
* **A deleted file disappears**, and so does everything inside a deleted folder.

The app rewrites front matter, never the body: saving a record writes the body back as
it was read, give or take the whitespace around it, which Markdown ignores.
Comments inside the front matter are not kept when the app saves it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar, cast

import yaml
from pydantic import Field, ValidationError

from app.models.gamestate import (
    Adventure,
    Chapter,
    GameStateModel,
    LevelRange,
    Record,
    SourceSet,
    Status,
    World,
    _new_id,
)
from app.store.characters import describe_error
from app.store.errors import EntryNotFound, InvalidEntry
from app.store.jsonfile import file_lock

logger = logging.getLogger(__name__)

Kind = Literal["world", "adventure", "chapter"]

WORLD_FILE = "world.md"
ADVENTURE_FILE = "adventure.md"
ADVENTURES_DIR = "adventures"
CHAPTERS_DIR = "chapters"
MEDIA_DIR = "media"

# Called with (kind, record id, revision); revision None means the record is gone.
ProseListener = Callable[[str, str, str | None], None]

_SLUG = re.compile(r"[^a-z0-9]+")
_FENCE = re.compile(r"^(?:---|\.\.\.)[ \t]*$")


class FrontMatterError(ValueError):
    """The front matter is not YAML, or not a mapping."""


# -- front matter ---------------------------------------------------------------------


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """``(fields, body)`` of a Markdown file; a file without front matter has none.

    The body has its leading blank lines removed — they separate it from the front
    matter and mean nothing in Markdown.
    """
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text.lstrip("\n")
    for index in range(1, len(lines)):
        if _FENCE.match(lines[index]):
            raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            break
    else:
        raise FrontMatterError("The front matter has no closing --- line")
    try:
        fields = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise FrontMatterError(f"The front matter is not valid YAML: {problem}{where}") from exc
    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        raise FrontMatterError("The front matter must be a list of key: value lines")
    return {str(k): v for k, v in fields.items()}, body.lstrip("\n")


def compose(fields: dict[str, Any], body: str) -> str:
    """The text of a Markdown file with these fields as front matter."""
    lines = []
    for key, value in fields.items():
        if isinstance(value, list | dict):
            # Short collections stay on their key's line: ``books: [PHB, MM]``.
            inline = yaml.safe_dump(
                value, default_flow_style=True, allow_unicode=True, sort_keys=False, width=10**6
            )
            lines.append(f"{key}: {inline.strip()}")
        else:
            lines.append(yaml.safe_dump({key: value}, allow_unicode=True, width=10**6).strip())
    head = "\n".join(lines) + "\n" if lines else ""
    body = body.strip("\n")
    return f"---\n{head}---\n" + (f"\n{body}\n" if body else "")


def revision_of(fields: dict[str, Any], body: str) -> str:
    """A short, stable fingerprint of a record's content."""
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(f"{canonical}\n{body.strip()}".encode()).hexdigest()[:12]


def write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, as ``write_json`` does for JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with file_lock(path):
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def slugify(name: str, fallback: str) -> str:
    """A folder- or file-name-safe version of a title."""
    return _SLUG.sub("-", name.lower()).strip("-")[:48].strip("-") or fallback


def humanize(slug: str) -> str:
    """A title from a file or folder name, for a file that names nothing itself."""
    words = slug.replace("_", " ").replace("-", " ").split()
    text = " ".join(words)
    return text[:1].upper() + text[1:] if text else slug


# -- the three file formats -------------------------------------------------------------
#
# Strict, like everything in the library: an unknown key is an error, reported against
# the file, never silently dropped. Field names are what a person writes by hand —
# ``books``, not ``source_set.sources``.


class _Front(GameStateModel):
    id: str | None = None
    cover: str | None = None


class WorldFront(_Front):
    """``world.md``."""

    name: str | None = None
    language: str = "en"
    books: list[str] = Field(default_factory=list)
    # The books players may read (documentation/PLAYER-VIEW.md); absent: the defaults.
    player_books: list[str] | None = None
    rerolls: bool = False
    # The naming styles names come from (documentation/NAMES.md §5b); absent: every one.
    names: list[str] = Field(default_factory=list)


class AdventureFront(_Front):
    """``adventure.md``."""

    name: str | None = None
    summary: str = ""
    order: int = 0
    status: Status = "planned"
    level: LevelRange | None = None
    based_on: str | None = None
    books: list[str] | None = None
    party: list[str] | None = None
    drafted_by: str | None = None
    revision_of: str | None = None
    origins: dict[str, str] = {}
    baseline: dict[str, str] = {}


class ChapterFront(_Front):
    """A chapter file."""

    title: str | None = None
    order: int = 0
    status: Status = "planned"
    section: str | None = None


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    """Leave out what is empty, so a file shows only what was set."""
    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


def to_file(record: Record) -> tuple[dict[str, Any], str]:
    """The front matter and body a record is written as."""
    if isinstance(record, World):
        fields: dict[str, Any] = {
            "id": record.id,
            "name": record.name,
            "language": record.language,
            "cover": record.cover,
            "books": list(record.source_set.sources),
            "rerolls": record.allow_rerolls or None,
            "names": list(record.naming_styles),
        }
        front = _clean(fields)
        if record.player_sources is not None:  # an empty list is a choice: nothing
            front["player_books"] = list(record.player_sources)
        return front, record.description
    if isinstance(record, Adventure):
        fields = {
            "id": record.id,
            "name": record.name,
            "summary": record.summary,
            "order": record.order,
            "status": record.status,
            "level": record.level.model_dump() if record.level else None,
            "cover": record.cover,
            "based_on": record.based_on,
            "party": record.party,
            "drafted_by": record.drafted_by,
            "revision_of": record.revision_of,
            "origins": record.origins,
            "baseline": record.baseline,
        }
        out = _clean(fields)
        if record.source_set is not None:
            out["books"] = list(record.source_set.sources)
        return out, record.body
    if isinstance(record, Chapter):
        fields = {
            "id": record.id,
            "title": record.title,
            "order": record.order,
            "status": record.status,
            "cover": record.cover,
            "section": record.section,
        }
        return _clean(fields), record.body
    raise TypeError(f"Not a prose record: {type(record).__name__}")


# -- where a file sits ------------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    """What a path is, and the folders that say whose it is."""

    kind: Kind
    world_dir: Path
    adventure_dir: Path | None = None


def classify(root: Path, path: Path) -> Place | None:
    """The kind of record ``path`` holds, or None for any other file."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if any(p.startswith(".") for p in parts) or path.suffix.lower() != ".md":
        return None
    world_dir = root / parts[0]
    if len(parts) == 2 and parts[1] == WORLD_FILE:
        return Place("world", world_dir)
    if len(parts) == 4 and parts[1] == ADVENTURES_DIR and parts[3] == ADVENTURE_FILE:
        return Place("adventure", world_dir, world_dir / ADVENTURES_DIR / parts[2])
    if len(parts) == 5 and parts[1] == ADVENTURES_DIR and parts[3] == CHAPTERS_DIR:
        return Place("chapter", world_dir, world_dir / ADVENTURES_DIR / parts[2])
    return None


_DEPTH = {"world": 0, "adventure": 1, "chapter": 2}


@dataclass
class _Doc:
    kind: Kind
    record: Record
    path: Path
    revision: str


class ProseStore:
    """Every world, adventure and chapter file under ``library/worlds/``."""

    name = "worlds"

    def __init__(self, root: Path, trash: Path | None = None) -> None:
        """``root`` is ``library/worlds``; deleted records are moved into ``trash``."""
        self.root = root.resolve()
        self.trash = (trash or self.root.parent / ".trash").resolve()
        self._docs: dict[str, _Doc] = {}
        self._errors: dict[str, str] = {}
        self._failed: set[Path] = set()
        self._listeners: list[ProseListener] = []
        self._lock = threading.RLock()
        self._loaded = False

    # -- notification ------------------------------------------------------------

    def subscribe(self, listener: ProseListener) -> None:
        """Be told about every change, whoever made it."""
        self._listeners.append(listener)

    def _notify(self, kind: str, record_id: str, revision: str | None) -> None:
        for listener in self._listeners:
            try:
                listener(kind, record_id, revision)
            except Exception:  # noqa: BLE001 - a listener must not break a write
                logger.exception("Prose change listener failed")

    # -- reading files -----------------------------------------------------------

    def load(self) -> None:
        """Read every file, replacing what is in memory."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self._docs, self._errors, self._failed = {}, {}, set()
            self._loaded = True
            self._reconcile(notify=False)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _candidates(self) -> list[tuple[Place, Path]]:
        found = []
        for path in self.root.rglob("*.md"):
            place = classify(self.root, path)
            if place is not None:
                found.append((place, path))
        return sorted(found, key=lambda pair: (_DEPTH[pair[0].kind], str(pair[1])))

    def _reconcile(self, *, notify: bool) -> None:
        """Bring memory in line with the tree after a batch of changes.

        Forgets records whose parent is gone, re-derives parent ids from folders, and
        loads any file not yet held — the adventures inside a world folder that only now
        got its ``world.md``, say. Files that failed and have not changed are skipped.
        """
        self._drop_orphans(notify=notify)
        for place, path in self._candidates():
            if self._owner_of(path) is None and path not in self._failed:
                self._load_path(path, place, notify=notify)
        self._relink(notify=notify)

    def _parent_ids(self, place: Place) -> tuple[str | None, str | None]:
        world = self._at(place.world_dir / WORLD_FILE)
        adventure = self._at(place.adventure_dir / ADVENTURE_FILE) if place.adventure_dir else None
        return world, adventure

    def _load_path(self, path: Path, place: Place, *, notify: bool) -> None:
        owner = self._owner_of(path)
        key = owner or self._relative(path)
        try:
            text = path.read_text(encoding="utf-8")
            fields, body = split_front_matter(text)
            world_id, adventure_id = self._parent_ids(place)
            if place.kind == "world":
                world_id = ""
            elif world_id is None:
                raise FrontMatterError(f"There is no {WORLD_FILE} in {place.world_dir.name}/")
            if place.kind == "chapter" and adventure_id is None:
                raise FrontMatterError(f"There is no {ADVENTURE_FILE} next to chapters/")
            record = self._build(place, path, fields, body, world_id, adventure_id)
        except (OSError, FrontMatterError, ValidationError) as exc:
            message = describe_error(exc) if isinstance(exc, ValidationError) else str(exc)
            logger.warning("Prose file %s not loaded: %s", path, message)
            self._errors[key] = message
            self._failed.add(path)
            if owner is not None and notify:
                # The last good version stays; the screens learn there is a problem.
                self._notify(self._docs[owner].kind, owner, self._docs[owner].revision)
            return
        self._failed.discard(path)
        self._errors.pop(self._relative(path), None)
        if place.kind != "chapter":
            # Files inside this folder that failed for want of it get another chance.
            folder = path.parent
            self._failed = {p for p in self._failed if not p.is_relative_to(folder)}

        clash = self._docs.get(record.id)
        needs_write = not fields.get("id")
        if clash is not None and clash.path != path:
            # A copied file: the copy is a new record, not a second claim on the old one.
            logger.info("Prose file %s copies the id of %s; giving it a new id", path, clash.path)
            record = record.model_copy(update={"id": _new_id()})
            needs_write = True
        if owner is not None and owner != record.id:
            # The id inside the file was changed by hand: the old record is gone.
            self._forget(owner, notify=notify)
        if needs_write:
            fields, body = to_file(record)
            write_text(path, compose(fields, body))
        else:
            fields, body = to_file(record)
        revision = revision_of(fields, body)
        previous = self._docs.get(record.id)
        changed = previous is None or previous.revision != revision or record.id in self._errors
        self._docs[record.id] = _Doc(place.kind, record, path, revision)
        self._errors.pop(record.id, None)
        if notify and changed:
            self._notify(place.kind, record.id, revision)

    def _build(
        self,
        place: Place,
        path: Path,
        fields: dict[str, Any],
        body: str,
        world_id: str,
        adventure_id: str | None,
    ) -> Record:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        base: dict[str, Any] = {"updated_at": stamp}
        if place.kind == "world":
            w = WorldFront.model_validate(fields)
            return World(
                **base,
                **({"id": w.id} if w.id else {}),
                name=w.name or humanize(place.world_dir.name),
                description=body,
                language=w.language,
                cover=w.cover,
                source_set=SourceSet(sources=w.books),
                allow_rerolls=w.rerolls,
                player_sources=w.player_books,
                naming_styles=w.names,
            )
        if place.kind == "adventure":
            a = AdventureFront.model_validate(fields)
            assert place.adventure_dir is not None
            return Adventure(
                **base,
                **({"id": a.id} if a.id else {}),
                world_id=world_id,
                name=a.name or humanize(place.adventure_dir.name),
                summary=a.summary,
                body=body,
                order=a.order,
                status=a.status,
                level=a.level,
                cover=a.cover,
                based_on=a.based_on,
                source_set=SourceSet(sources=a.books) if a.books is not None else None,
                party=a.party or None,
                drafted_by=a.drafted_by,
                revision_of=a.revision_of,
                origins=a.origins,
                baseline=a.baseline,
            )
        c = ChapterFront.model_validate(fields)
        assert adventure_id is not None
        return Chapter(
            **base,
            **({"id": c.id} if c.id else {}),
            world_id=world_id,
            adventure_id=adventure_id,
            title=c.title or humanize(path.stem),
            body=body,
            order=c.order,
            status=c.status,
            cover=c.cover,
            section=c.section,
        )

    def _drop_orphans(self, *, notify: bool) -> None:
        for record_id, doc in list(self._docs.items()):
            if doc.kind == "world":
                continue
            place = classify(self.root, doc.path)
            if place is None:
                continue
            world_id, adventure_id = self._parent_ids(place)
            if world_id is None or (doc.kind == "chapter" and adventure_id is None):
                self._forget(record_id, notify=notify)

    def _relink(self, *, notify: bool) -> None:
        """Parent ids follow the folders: a world's id edited by hand reaches its children."""
        for record_id, doc in list(self._docs.items()):
            place = classify(self.root, doc.path)
            if place is None or doc.kind == "world":
                continue
            world_id, adventure_id = self._parent_ids(place)
            update: dict[str, Any] = {}
            if getattr(doc.record, "world_id", None) != world_id:
                update["world_id"] = world_id
            if doc.kind == "chapter" and getattr(doc.record, "adventure_id", None) != adventure_id:
                update["adventure_id"] = adventure_id
            if update:
                doc.record = doc.record.model_copy(update=update)
                if notify:
                    self._notify(doc.kind, record_id, doc.revision)

    def _at(self, path: Path) -> str | None:
        return self._owner_of(path)

    def _owner_of(self, path: Path) -> str | None:
        for record_id, doc in self._docs.items():
            if doc.path == path:
                return record_id
        return None

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def _forget(self, record_id: str, *, notify: bool) -> None:
        doc = self._docs.pop(record_id, None)
        self._errors.pop(record_id, None)
        if doc is not None and notify:
            self._notify(doc.kind, record_id, None)

    def reload_paths(self, paths: Iterable[Path]) -> None:
        """Re-read what the watcher reported, deletions first so a move keeps its id."""
        with self._lock:
            self._ensure_loaded()
            resolved = {p.resolve() for p in paths}
            for path in sorted(p for p in resolved if not p.exists()):
                self._failed.discard(path)
                self._errors.pop(self._relative(path), None)
                owner = self._owner_of(path)
                if owner is not None:
                    self._forget(owner, notify=True)
            present = [
                (place, path)
                for path in resolved
                if path.exists() and (place := classify(self.root, path)) is not None
            ]
            for place, path in sorted(present, key=lambda pp: (_DEPTH[pp[0].kind], str(pp[1]))):
                self._failed.discard(path)
                self._load_path(path, place, notify=True)
            self._reconcile(notify=True)

    # -- writing -----------------------------------------------------------------

    def _write(self, kind: Kind, record: Record, path: Path) -> Record:
        fields, body = to_file(record)
        write_text(path, compose(fields, body))
        revision = revision_of(fields, body)
        previous = self._docs.get(record.id)
        self._docs[record.id] = _Doc(kind, record, path, revision)
        self._errors.pop(record.id, None)
        self._failed.discard(path)
        if previous is None or previous.revision != revision:
            self._notify(kind, record.id, revision)
        return record

    @staticmethod
    def _free(parent: Path, slug: str, suffix: str = "") -> Path:
        candidate = parent / f"{slug}{suffix}"
        number = 2
        while candidate.exists():
            candidate = parent / f"{slug}-{number}{suffix}"
            number += 1
        return candidate

    def _new_path(self, kind: Kind, record: Record) -> Path:
        if isinstance(record, World):
            return self._free(self.root, slugify(record.name, "world")) / WORLD_FILE
        if isinstance(record, Adventure):
            world = self._doc(record.world_id, "world")
            parent = world.path.parent / ADVENTURES_DIR
            return self._free(parent, slugify(record.name, "adventure")) / ADVENTURE_FILE
        if isinstance(record, Chapter):
            adventure = self._doc(record.adventure_id, "adventure")
            if cast(Adventure, adventure.record).world_id != record.world_id:
                raise InvalidEntry("chapters", "world_id does not match the adventure's")
            parent = adventure.path.parent / CHAPTERS_DIR
            return self._free(parent, slugify(record.title, "chapter"), ".md")
        raise TypeError(kind)

    def _doc(self, record_id: str, kind: Kind) -> _Doc:
        doc = self._docs.get(record_id)
        if doc is None or doc.kind != kind:
            raise EntryNotFound(f"{kind}s", record_id)
        return doc

    def create(self, kind: Kind, record: Record) -> Record:
        """Write a new record into a new file where its parent says."""
        with self._lock:
            self._ensure_loaded()
            if record.id in self._docs:
                raise InvalidEntry(f"{kind}s", f"id {record.id!r} is taken")
            return self._write(kind, record, self._new_path(kind, record))

    def save(self, kind: Kind, record: Record) -> Record:
        """Rewrite an existing record's file in place."""
        with self._lock:
            doc = self._doc(record.id, kind)
            record.touch()
            return self._write(kind, record, doc.path)

    def delete(self, kind: Kind, record_id: str) -> Path:
        """Move a record's file — a world's or adventure's whole folder — to the trash.

        Nothing is erased: the media and chapters inside go with it, and can be moved
        back by hand. Returns where it went.
        """
        with self._lock:
            doc = self._doc(record_id, kind)
            source = doc.path if kind == "chapter" else doc.path.parent
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            target = self._free(self.trash, f"{stamp}-{source.name}")
            self.trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            self._forget(record_id, notify=True)
            self._drop_orphans(notify=True)
            return target

    # -- migration ---------------------------------------------------------------

    def adopt_json(self, library_dir: Path) -> int:
        """Move worlds and adventures out of ``worlds.json``/``adventures.json``, once.

        Each record becomes a file with the same id, so characters and encounters that
        name a world keep naming it. The JSON files are then renamed to
        ``.worlds.json.migrated`` — kept, not deleted, and ignored from then on.
        Returns how many records were written.
        """
        written = 0
        with self._lock:
            self._ensure_loaded()
            for filename, kind in (("worlds.json", "world"), ("adventures.json", "adventure")):
                source = library_dir / filename
                if not source.exists():
                    continue
                try:
                    raw = json.loads(source.read_text(encoding="utf-8")) or {}
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Not migrating %s: %s", source, exc)
                    continue
                for value in raw.values() if isinstance(raw, dict) else ():
                    written += self._adopt(cast(Kind, kind), value)
                source.rename(source.with_name(f".{filename}.migrated"))
        return written

    def _adopt(self, kind: Kind, value: Any) -> int:
        if not isinstance(value, dict) or value.get("id") in self._docs:
            return 0
        books = (value.get("source_set") or {}).get("sources", [])
        try:
            record: Record
            if kind == "world":
                record = World(
                    id=value["id"],
                    name=value["name"],
                    description=value.get("description", ""),
                    language=value.get("language", "en"),
                    source_set=SourceSet(sources=books),
                )
            else:
                if self.find("world", value.get("world_id")) is None:
                    logger.warning("Not migrating adventure %s: its world is gone", value.get("id"))
                    return 0
                record = Adventure(
                    id=value["id"],
                    world_id=value["world_id"],
                    name=value["name"],
                    summary=value.get("summary", ""),
                    body=value.get("body", ""),
                    source_set=SourceSet(sources=books) if value.get("source_set") else None,
                )
        except (KeyError, ValidationError) as exc:
            logger.warning("Not migrating %s record %s: %s", kind, value.get("id"), exc)
            return 0
        self.create(kind, record)
        return 1

    # -- reading -----------------------------------------------------------------

    def records(self, kind: Kind) -> list[Record]:
        """Every record of one kind."""
        with self._lock:
            self._ensure_loaded()
            return [d.record for d in self._docs.values() if d.kind == kind]

    def find(self, kind: Kind, record_id: str | None) -> Record | None:
        """One record, or None."""
        with self._lock:
            self._ensure_loaded()
            doc = self._docs.get(record_id) if record_id else None
            return doc.record if doc is not None and doc.kind == kind else None

    def path(self, record_id: str) -> Path | None:
        """The file a record lives in."""
        with self._lock:
            self._ensure_loaded()
            doc = self._docs.get(record_id)
            return doc.path if doc is not None else None

    def folder(self, record_id: str) -> Path | None:
        """The folder a record's relative paths (``media/…``) start from."""
        path = self.path(record_id)
        return path.parent if path is not None else None

    def world_folder(self, record_id: str) -> Path | None:
        """The folder of the world a record belongs to — the boundary for its media."""
        path = self.path(record_id)
        if path is None:
            return None
        return self.root / path.relative_to(self.root).parts[0]

    def revision(self, record_id: str) -> str | None:
        """The current revision of a record, None when it does not exist."""
        with self._lock:
            self._ensure_loaded()
            doc = self._docs.get(record_id)
            return doc.revision if doc is not None else None

    def error(self, record_id: str) -> str | None:
        """Why the file on disk is not what is shown, if it is not."""
        with self._lock:
            return self._errors.get(record_id)

    @property
    def errors(self) -> dict[str, str]:
        """Every unreadable file, keyed by record id or path under the root."""
        with self._lock:
            return dict(self._errors)


R = TypeVar("R", bound=Record)


class ProseCollection(Generic[R]):
    """One kind of prose record behind the same interface as a JSON collection.

    Services written against ``store.worlds`` keep working whichever backs it.
    """

    def __init__(self, store: ProseStore, kind: Kind, model: type[R]) -> None:
        """Bind to the store and the kind of record served."""
        self._store = store
        self._kind: Kind = kind
        self._model = model

    @property
    def name(self) -> str:
        """The collection's name, for error messages."""
        return f"{self._kind}s"

    def load(self) -> None:
        """Re-read the files (all three kinds share one store)."""
        self._store.load()

    def list(self, predicate: Callable[[R], bool] | None = None) -> list[R]:
        """Every record, optionally filtered."""
        records = cast(list[R], self._store.records(self._kind))
        return [r for r in records if predicate(r)] if predicate else records

    def find(self, record_id: str | None) -> R | None:
        """One record, or None."""
        return cast(R | None, self._store.find(self._kind, record_id))

    def get(self, record_id: str) -> R:
        """One record, raising when it does not exist."""
        found = self.find(record_id)
        if found is None:
            raise EntryNotFound(self.name, record_id)
        return found

    def create(self, record: R) -> R:
        """Write a new record."""
        return cast(R, self._store.create(self._kind, record))

    def update(self, record_id: str, **changes: Any) -> R:
        """Apply field changes, validated before anything is written."""
        current = self.get(record_id)
        try:
            updated = self._model.model_validate(
                {**current.model_dump(), **changes, "id": record_id}
            )
        except ValidationError as exc:
            raise InvalidEntry(self.name, describe_error(exc)) from exc
        return cast(R, self._store.save(self._kind, updated))

    def mutate(self, record_id: str, mutator: Callable[[R], None]) -> R:
        """Change a copy, re-validate it whole, and write it."""
        working = self.get(record_id).model_copy(deep=True)
        try:
            mutator(working)
            checked = self._model.model_validate(working.model_dump())
        except ValidationError as exc:
            raise InvalidEntry(self.name, describe_error(exc)) from exc
        return cast(R, self._store.save(self._kind, checked))

    def delete(self, record_id: str) -> None:
        """Move the record to the trash."""
        self._store.delete(self._kind, record_id)

    def __len__(self) -> int:
        """How many records there are."""
        return len(self._store.records(self._kind))

    def __contains__(self, record_id: object) -> bool:
        """True when a record with this id exists."""
        return isinstance(record_id, str) and self.find(record_id) is not None


__all__ = [
    "ADVENTURE_FILE",
    "MEDIA_DIR",
    "WORLD_FILE",
    "FrontMatterError",
    "ProseCollection",
    "ProseStore",
    "classify",
    "compose",
    "split_front_matter",
    "to_file",
]
