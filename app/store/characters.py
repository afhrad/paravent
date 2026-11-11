"""Characters as one JSON file each, in ``library/characters/``.

One file per character, not one shared collection, because the file is meant to be
opened and edited by hand (documentation/CHARACTER-FORMAT.md). A person editing Lyra
should see Lyra, not scroll past four other characters, and a backup of one character is
a copy of one file.

The store keeps every character in memory, writes atomically on every change, and
re-reads a single file when the watcher reports it changed. Three things a hand edit can
do are handled deliberately:

* **Introduce a mistake.** Invalid JSON or a validation failure keeps the last good
  version in memory and records the error against the character, so the sheet can say
  "your edit at line 12 is not valid" instead of going blank. Fixing the file clears it.
* **Create a file.** A file without an ``id`` takes its filename as the id.
* **Delete a file.** The character is dropped and listeners are told.

Every change produces a **revision** — a hash of the canonical content — so a screen can
tell whether what it shows is current, and a write that changes nothing notifies nobody.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.pc import PlayerCharacter
from app.store.errors import DuplicateEntry, EntryNotFound, InvalidEntry
from app.store.jsonfile import file_lock, iter_json_files, write_json

logger = logging.getLogger(__name__)

# Called with (character id, revision); revision None means the character is gone.
ChangeListener = Callable[[str, str | None], None]

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """A filename-safe version of a character name."""
    return _SLUG.sub("-", name.lower()).strip("-")[:40] or "character"


def serialize(character: PlayerCharacter) -> dict[str, Any]:
    """The file content for a character, in the canonical form."""
    data = character.model_dump(mode="json")
    if data.get("$schema") is None:
        data.pop("$schema", None)
    return data


def revision_of(data: dict[str, Any]) -> str:
    """A short, stable fingerprint of a character's content."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(canonical.encode()).hexdigest()[:12]


def describe_error(exc: Exception) -> str:
    """A one-line, human explanation of why a file could not be read."""
    if isinstance(exc, json.JSONDecodeError):
        return f"Not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "file"
        more = f" (and {exc.error_count() - 1} more)" if exc.error_count() > 1 else ""
        return f"{where}: {first['msg']}{more}"
    return str(exc)


# -- console reports ------------------------------------------------------------------
#
# The sheet shows one line ("state.hp: Input should be a valid integer"); the server
# console shows *where*: the file, the line, the text on it, and the rejected value, so
# whoever edited the file by hand can go straight to the mistake.


def _excerpt(text: str, line: int, column: int | None = None, context: int = 1) -> str:
    """The numbered lines around ``line`` (1-based), with a caret under ``column``."""
    lines = text.splitlines()
    first, last = max(1, line - context), min(len(lines), line + context)
    width = len(str(last))
    out = []
    for number in range(first, last + 1):
        marker = ">" if number == line else " "
        out.append(f"  {marker} {number:>{width}} | {lines[number - 1]}")
        if number == line and column is not None:
            out.append(f"    {' ' * width} | {' ' * max(0, column - 1)}^")
    return "\n".join(out)


def _line_of(text: str, loc: tuple[int | str, ...]) -> tuple[int, int] | None:
    """Where the field at ``loc`` appears in ``text``: (line, column), 1-based.

    Follows the location's keys in order through the text and, for a list position,
    counts that many items on; good enough to land on the offending line of a
    hand-written character file without a position-tracking JSON parser.
    """
    position = 0
    found: int | None = None
    for part in loc:
        if isinstance(part, str):
            hit = text.find(f'"{part}"', position)
            if hit < 0:
                break
            found = position = hit
        else:
            # Step into a list: skip to the n-th element after the opening bracket.
            start = text.find("[", position)
            if start < 0:
                break
            position = start + 1
            for _ in range(part):
                comma = _next_top_level_comma(text, position)
                if comma < 0:
                    break
                position = comma + 1
            while position < len(text) and text[position] in " \t\r\n":
                position += 1
            found = position
    if found is None:
        return None
    line = text.count("\n", 0, found) + 1
    column = found - (text.rfind("\n", 0, found) + 1) + 1
    return line, column


def _next_top_level_comma(text: str, start: int) -> int:
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            if depth == 0:
                return -1
            depth -= 1
        elif char == "," and depth == 0:
            return index
    return -1


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def report_error(path: Path, exc: Exception) -> str:
    """A console report for a character file that could not be loaded."""
    head = f"Character file {path} not loaded — the last valid version stays on screen."
    text = _read_text(path)
    if isinstance(exc, json.JSONDecodeError):
        return "\n".join(
            [
                head,
                f"  {exc.msg} at line {exc.lineno}, column {exc.colno}:",
                _excerpt(text, exc.lineno, exc.colno),
            ]
        )
    if isinstance(exc, ValidationError):
        out = [head]
        for error in exc.errors():
            where = ".".join(str(p) for p in error["loc"]) or "(file)"
            out.append(f"  {where}: {error['msg']}")
            if "input" in error and not isinstance(error["input"], dict | list):
                out.append(f"    value: {error['input']!r}")
            spot = _line_of(text, tuple(error["loc"]))
            if spot is not None:
                out.append(_excerpt(text, spot[0], spot[1], context=0))
        return "\n".join(out)
    return f"{head}\n  {exc}"


def report_duplicate(path: Path, existing: Path, character_id: str) -> str:
    """A console report for two files claiming the same id."""
    out = [
        f"Character file {path} ignored: its id {character_id!r} is already used by {existing}.",
        '  Give one of them a different "id" (or remove it to use the filename).',
    ]
    for file in (path, existing):
        text = _read_text(file)
        spot = _line_of(text, ("id",))
        if spot is not None:
            out.append(f"  {file.name}:")
            out.append(_excerpt(text, spot[0], spot[1], context=0))
    return "\n".join(out)


class CharacterStore:
    """Every character, one file each, with change notification."""

    name = "characters"

    def __init__(self, root: Path) -> None:
        """``root`` is the ``characters`` directory inside the library.

        Every path is kept absolute. The library directory may be configured relative
        (``data/library``) while the file watcher reports absolute paths; comparing the
        two forms literally made an edited file look like a second file with its own id.
        """
        self.root = root.resolve()
        self._items: dict[str, PlayerCharacter] = {}
        self._paths: dict[str, Path] = {}
        self._revisions: dict[str, str] = {}
        self._errors: dict[str, str] = {}
        self._listeners: list[ChangeListener] = []
        self._lock = threading.RLock()
        self._loaded = False

    # -- notification ------------------------------------------------------------

    def subscribe(self, listener: ChangeListener) -> None:
        """Be told about every change, whoever made it."""
        self._listeners.append(listener)

    def _notify(self, character_id: str, revision: str | None) -> None:
        for listener in self._listeners:
            try:
                listener(character_id, revision)
            except Exception:  # noqa: BLE001 - a listener must not break a write
                logger.exception("Character change listener failed")

    # -- reading files -----------------------------------------------------------

    def _read(self, path: Path) -> PlayerCharacter:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and not raw.get("id"):
            raw["id"] = path.stem
        return PlayerCharacter.model_validate(raw)

    def load(self) -> None:
        """Read every character file, replacing what is in memory."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self._items, self._paths, self._revisions, self._errors = {}, {}, {}, {}
            for path in iter_json_files(self.root):
                self._load_path(path, notify=False)
            self._loaded = True

    def _load_path(self, path: Path, *, notify: bool) -> None:
        path = path.resolve()
        owner = self._owner_of(path)
        try:
            character = self._read(path)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            message = describe_error(exc)
            key = owner or path.stem
            logger.warning("%s", report_error(path, exc))
            self._errors[key] = message
            if owner is not None and notify:
                # The last good version stays; the screens learn there is a problem.
                self._notify(owner, self._revisions.get(owner))
            return
        cid = character.id
        clash = self._paths.get(cid)
        if clash is not None and clash != path:
            self._errors[path.stem] = f"Duplicate id {cid!r}; already used by {clash.name}"
            logger.warning("%s", report_duplicate(path, clash, cid))
            return
        if owner is not None and owner != cid:
            # The id inside the file was changed by hand: the old character is gone.
            self._forget(owner, notify=notify)
        revision = revision_of(serialize(character))
        changed = self._revisions.get(cid) != revision or cid in self._errors
        self._items[cid], self._paths[cid], self._revisions[cid] = character, path, revision
        self._errors.pop(cid, None)
        self._errors.pop(path.stem, None)
        if notify and changed:
            self._notify(cid, revision)

    def _owner_of(self, path: Path) -> str | None:
        resolved = path.resolve()
        for cid, known in self._paths.items():
            if known == resolved:
                return cid
        return None

    def _forget(self, character_id: str, *, notify: bool) -> None:
        self._items.pop(character_id, None)
        self._paths.pop(character_id, None)
        self._revisions.pop(character_id, None)
        self._errors.pop(character_id, None)
        if notify:
            self._notify(character_id, None)

    def reload_paths(self, paths: Iterable[Path]) -> None:
        """Re-read the files the watcher reported, notifying for real changes only."""
        with self._lock:
            self._ensure_loaded()
            for path in (p.resolve() for p in paths):
                if path.exists():
                    self._load_path(path, notify=True)
                else:
                    owner = self._owner_of(path)
                    if owner is not None:
                        self._forget(owner, notify=True)
                    self._errors.pop(path.stem, None)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- writing -----------------------------------------------------------------

    def _path_for(self, character: PlayerCharacter) -> Path:
        known = self._paths.get(character.id)
        if known is not None:
            return known
        return self.root / f"{slugify(character.name)}-{character.id}.json"

    def _write(self, character: PlayerCharacter) -> PlayerCharacter:
        path = self._path_for(character)
        data = serialize(character)
        with file_lock(path):
            write_json(path, data)
        revision = revision_of(data)
        previous = self._revisions.get(character.id)
        self._items[character.id] = character
        self._paths[character.id] = path
        self._revisions[character.id] = revision
        self._errors.pop(character.id, None)
        if revision != previous:
            self._notify(character.id, revision)
        return character

    def create(self, character: PlayerCharacter) -> PlayerCharacter:
        """Write a new character file, refusing to reuse an id."""
        with self._lock:
            self._ensure_loaded()
            if character.id in self._items:
                raise DuplicateEntry(self.name, character.id)
            return self._write(character)

    def replace(self, character: PlayerCharacter) -> PlayerCharacter:
        """Overwrite an existing character wholesale."""
        with self._lock:
            self._ensure_loaded()
            if character.id not in self._items:
                raise EntryNotFound(self.name, character.id)
            character.touch()
            return self._write(character)

    def update(self, character_id: str, **changes: Any) -> PlayerCharacter:
        """Apply field changes, validated before anything is written."""
        with self._lock:
            current = self.get(character_id)
            try:
                updated = PlayerCharacter.model_validate(
                    {**current.model_dump(), **changes, "id": character_id}
                )
            except ValidationError as exc:
                raise InvalidEntry(self.name, describe_error(exc)) from exc
            updated.created_at = current.created_at
            updated.touch()
            return self._write(updated)

    def mutate(
        self, character_id: str, mutator: Callable[[PlayerCharacter], None]
    ) -> PlayerCharacter:
        """Change a character in place under the lock, re-validate, and persist.

        The mutator works on a copy, and the result is validated as a whole before it
        is written, so a mutation that breaks a rule leaves the file untouched.
        """
        with self._lock:
            current = self.get(character_id)
            working = current.model_copy(deep=True)
            try:
                # Assignment is validated as it happens, so a bad value can fail inside
                # the mutator; the final check catches combinations of valid values.
                mutator(working)
                checked = PlayerCharacter.model_validate(working.model_dump())
            except ValidationError as exc:
                raise InvalidEntry(self.name, describe_error(exc)) from exc
            checked.touch()
            return self._write(checked)

    def delete(self, character_id: str) -> None:
        """Remove a character's file."""
        with self._lock:
            self._ensure_loaded()
            path = self._paths.get(character_id)
            if path is None:
                raise EntryNotFound(self.name, character_id)
            with file_lock(path):
                path.unlink(missing_ok=True)
            self._forget(character_id, notify=True)

    # -- reading -----------------------------------------------------------------

    def get(self, character_id: str) -> PlayerCharacter:
        """One character, raising when it does not exist."""
        found = self.find(character_id)
        if found is None:
            raise EntryNotFound(self.name, character_id)
        return found

    def find(self, character_id: str) -> PlayerCharacter | None:
        """One character, or None."""
        with self._lock:
            self._ensure_loaded()
            return self._items.get(character_id)

    def list(
        self, predicate: Callable[[PlayerCharacter], bool] | None = None
    ) -> list[PlayerCharacter]:
        """Every character, optionally filtered."""
        with self._lock:
            self._ensure_loaded()
            records = list(self._items.values())
        return [r for r in records if predicate(r)] if predicate else records

    def revision(self, character_id: str) -> str | None:
        """The current revision of a character, None when it does not exist."""
        with self._lock:
            self._ensure_loaded()
            return self._revisions.get(character_id)

    def error(self, character_id: str) -> str | None:
        """Why the file on disk is not what is shown, if it is not."""
        with self._lock:
            return self._errors.get(character_id)

    def path(self, character_id: str) -> Path | None:
        """The file a character lives in."""
        with self._lock:
            self._ensure_loaded()
            return self._paths.get(character_id)

    @property
    def errors(self) -> dict[str, str]:
        """Every unreadable file, keyed by character id or filename."""
        with self._lock:
            return dict(self._errors)

    def __len__(self) -> int:
        """How many characters are loaded."""
        with self._lock:
            self._ensure_loaded()
            return len(self._items)

    def __contains__(self, character_id: object) -> bool:
        """True when a character with this id exists."""
        with self._lock:
            self._ensure_loaded()
            return character_id in self._items


__all__ = [
    "CharacterStore",
    "report_duplicate",
    "report_error",
    "ChangeListener",
    "describe_error",
    "revision_of",
    "serialize",
    "slugify",
]
