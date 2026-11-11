"""CRUD storage for game state, backed by one JSON file per collection.

The compendium is read-mostly and lives in RAM; game state is the opposite — the combat
tracker writes on every HP change. This store therefore keeps an in-memory copy for
reads but persists on every mutation, atomically (see :mod:`app.store.jsonfile`).

Each collection is one file, ``{gamestate_dir}/{name}.json``, holding a JSON object
keyed by record id. Keying by id rather than using an array makes a single-record update
a dict assignment instead of a linear scan, and keeps the file diffable.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import ValidationError

from app.models.gamestate import Record
from app.store.errors import DuplicateEntry, EntryNotFound, InvalidEntry
from app.store.jsonfile import file_lock, read_json, write_json

if TYPE_CHECKING:
    from app.store.characters import CharacterStore
    from app.store.prose import ProseStore

logger = logging.getLogger(__name__)

R = TypeVar("R", bound=Record)


class Collection(Generic[R]):
    """A persisted, id-keyed collection of one record type.

    Reads are served from memory; every mutation writes the whole collection back. That
    is the right trade at this scale — a campaign's encounters are kilobytes, and
    rewriting the file wholesale is what makes the atomic-rename guarantee possible.
    """

    def __init__(self, path: Path, model: type[R]) -> None:
        """``path`` is the backing JSON file; ``model`` validates every record."""
        self._path = path
        self._model = model
        self._items: dict[str, R] = {}
        self._lock = threading.RLock()
        self._loaded = False

    @property
    def path(self) -> Path:
        """The JSON file backing this collection."""
        return self._path

    @property
    def name(self) -> str:
        """The collection's name, taken from its filename."""
        return self._path.stem

    # -- persistence -------------------------------------------------------------

    def load(self) -> None:
        """Read the collection from disk, replacing whatever is in memory.

        A record that fails validation is skipped with a warning rather than aborting
        the load: one corrupt encounter must not make the rest of a campaign
        unreachable.
        """
        with self._lock:
            raw = read_json(self._path, default={}) or {}
            items: dict[str, R] = {}
            for key, value in raw.items():
                try:
                    items[key] = self._model.model_validate(value)
                except ValidationError as exc:
                    logger.warning(
                        "Skipping invalid %s record %s: %s", self.name, key, exc.error_count()
                    )
            self._items = items
            self._loaded = True

    def _ensure_loaded(self) -> None:
        """Load lazily on first access."""
        if not self._loaded:
            self.load()

    def _flush(self) -> None:
        """Persist the whole collection atomically."""
        payload = {key: record.model_dump(mode="json") for key, record in self._items.items()}
        write_json(self._path, payload)

    # -- CRUD --------------------------------------------------------------------

    def create(self, record: R) -> R:
        """Insert a new record, refusing to overwrite an existing id."""
        with self._lock, file_lock(self._path):
            self._ensure_loaded()
            if record.id in self._items:
                raise DuplicateEntry(self.name, record.id)
            self._items[record.id] = record
            self._flush()
            return record

    def get(self, record_id: str) -> R:
        """Fetch one record by id, raising when it does not exist."""
        with self._lock:
            self._ensure_loaded()
            record = self._items.get(record_id)
            if record is None:
                raise EntryNotFound(self.name, record_id)
            return record

    def find(self, record_id: str) -> R | None:
        """Fetch one record by id, or None when it does not exist."""
        with self._lock:
            self._ensure_loaded()
            return self._items.get(record_id)

    def list(self, predicate: Callable[[R], bool] | None = None) -> list[R]:
        """Every record, optionally filtered."""
        with self._lock:
            self._ensure_loaded()
            records = list(self._items.values())
        return [r for r in records if predicate(r)] if predicate else records

    def update(self, record_id: str, **changes: Any) -> R:
        """Apply field changes to a record and persist it.

        Validation runs through the model, so an invalid change is rejected before it
        reaches disk rather than after.
        """
        with self._lock, file_lock(self._path):
            self._ensure_loaded()
            record = self._items.get(record_id)
            if record is None:
                raise EntryNotFound(self.name, record_id)
            try:
                updated = record.model_copy(update=changes)
                # model_copy skips validation, so re-validate the result explicitly.
                updated = self._model.model_validate(updated.model_dump(mode="json"))
            except ValidationError as exc:
                raise InvalidEntry(self.name, str(exc)) from exc
            updated.touch()
            self._items[record_id] = updated
            self._flush()
            return updated

    def replace(self, record: R) -> R:
        """Overwrite a record wholesale, requiring that it already exists."""
        with self._lock, file_lock(self._path):
            self._ensure_loaded()
            if record.id not in self._items:
                raise EntryNotFound(self.name, record.id)
            record.touch()
            self._items[record.id] = record
            self._flush()
            return record

    def upsert(self, record: R) -> R:
        """Insert or overwrite, whichever applies."""
        with self._lock, file_lock(self._path):
            self._ensure_loaded()
            if record.id in self._items:
                record.touch()
            self._items[record.id] = record
            self._flush()
            return record

    def delete(self, record_id: str) -> None:
        """Remove a record, raising when it does not exist."""
        with self._lock, file_lock(self._path):
            self._ensure_loaded()
            if record_id not in self._items:
                raise EntryNotFound(self.name, record_id)
            del self._items[record_id]
            self._flush()

    def mutate(self, record_id: str, mutator: Callable[[R], None]) -> R:
        """Mutate a record in place under the collection lock, then persist.

        This is the safe path for read-modify-write cycles such as applying damage::

            store.encounters.mutate(eid, lambda e: e.combatants[0].apply_damage(7))

        Doing the same through ``get`` then ``replace`` would leave a window in which a
        concurrent request could overwrite the change.
        """
        with self._lock, file_lock(self._path):
            self._ensure_loaded()
            record = self._items.get(record_id)
            if record is None:
                raise EntryNotFound(self.name, record_id)
            mutator(record)
            record.touch()
            self._items[record_id] = record
            self._flush()
            return record

    def clear(self) -> None:
        """Remove every record."""
        with self._lock, file_lock(self._path):
            self._items = {}
            self._loaded = True
            self._flush()

    def __len__(self) -> int:
        """Number of records held."""
        with self._lock:
            self._ensure_loaded()
            return len(self._items)

    def __iter__(self) -> Iterator[R]:
        """Iterate the records."""
        return iter(self.list())

    def __contains__(self, record_id: object) -> bool:
        """True when a record with this id exists."""
        with self._lock:
            self._ensure_loaded()
            return record_id in self._items


class GameStateStore:
    """The collections that make up persistent game state."""

    def __init__(self, root: Path) -> None:
        """``root`` is the directory holding one JSON file per collection."""
        from app.models.gamestate import (
            Adventure,
            Chapter,
            Combat,
            CreatureDesign,
            Encounter,
            Handout,
            Knowledge,
            Location,
            McpToken,
            Npc,
            Place,
            Session,
            SessionNote,
            Sound,
            SoundScene,
            TableSettings,
            Treasure,
            User,
            World,
            WorldInstructions,
        )
        from app.models.naming import NamingStyle
        from app.store.characters import CharacterStore
        from app.store.prose import ProseCollection, ProseStore

        self.root = root
        root.mkdir(parents=True, exist_ok=True)

        self.users: Collection[User] = Collection(root / "users.json", User)
        # Worlds, adventures and chapters are Markdown files, one folder per world
        # (documentation/ADVENTURES.md §3), served through the same interface.
        self.prose = ProseStore(root / "worlds", trash=root / ".trash")
        self.worlds: ProseCollection[World] = ProseCollection(self.prose, "world", World)
        self.adventures: ProseCollection[Adventure] = ProseCollection(
            self.prose, "adventure", Adventure
        )
        self.chapters: ProseCollection[Chapter] = ProseCollection(self.prose, "chapter", Chapter)
        self.encounters: Collection[Encounter] = Collection(root / "encounters.json", Encounter)
        # A running fight writes on every button; the plan rarely (COMBAT.md §2).
        self.combats: Collection[Combat] = Collection(root / "combats.json", Combat)
        self.sessions: Collection[Session] = Collection(root / "sessions.json", Session)
        # Keys for the MCP server (documentation/MCP.md §3), hashed.
        self.mcp_tokens: Collection[McpToken] = Collection(root / "mcp_tokens.json", McpToken)
        # What Claude is told about each world (documentation/MCP.md §5).
        self.instructions: Collection[WorldInstructions] = Collection(
            root / "claude_instructions.json", WorldInstructions
        )
        # What happened at the table, day by day (documentation/ADVENTURES.md §4b).
        self.session_notes: Collection[SessionNote] = Collection(
            root / "session_notes.json", SessionNote
        )
        # Where each user left off, written as they move around (app/services/places.py).
        self.places: Collection[Place] = Collection(root / "places.json", Place)
        # What each world's party learned about kinds of creatures (COMBAT.md §5).
        self.knowledge: Collection[Knowledge] = Collection(root / "knowledge.json", Knowledge)
        self.npcs: Collection[Npc] = Collection(root / "npcs.json", Npc)
        # The campaign's places, ``{@place}`` (documentation/ADVENTURES.md §4).
        self.locations: Collection[Location] = Collection(root / "locations.json", Location)
        self.treasures: Collection[Treasure] = Collection(root / "treasures.json", Treasure)
        # Creature-builder designs; their statblocks are published to homebrew/.
        self.creatures: Collection[CreatureDesign] = Collection(
            root / "creatures.json", CreatureDesign
        )
        # Naming styles, shared by every world (documentation/NAMES.md).
        self.naming_styles: Collection[NamingStyle] = Collection(
            root / "naming_styles.json", NamingStyle
        )
        # The sound desk's library, shared by every world (documentation/SOUND.md).
        # What the gamemaster handed to characters, for the players' journal.
        self.handouts: Collection[Handout] = Collection(root / "handouts.json", Handout)
        # Settings of the whole table, one record (``table``).
        self.table: Collection[TableSettings] = Collection(root / "table.json", TableSettings)
        self.sounds: Collection[Sound] = Collection(root / "sounds.json", Sound)
        self.sound_scenes: Collection[SoundScene] = Collection(
            root / "sound_scenes.json", SoundScene
        )
        # One file per character, so a character can be opened and edited by hand
        # (documentation/CHARACTER-FORMAT.md).
        self.characters = CharacterStore(root / "characters")

    def collections(self) -> dict[str, Collection[Any] | CharacterStore | ProseStore]:
        """Every collection, keyed by name; the prose store stands for all three kinds."""
        return {
            "users": self.users,
            "encounters": self.encounters,
            "combats": self.combats,
            "sessions": self.sessions,
            "mcp_tokens": self.mcp_tokens,
            "session_notes": self.session_notes,
            "naming_styles": self.naming_styles,
            "sounds": self.sounds,
            "table": self.table,
            "handouts": self.handouts,
            "sound_scenes": self.sound_scenes,
            "instructions": self.instructions,
            "places": self.places,
            "knowledge": self.knowledge,
            "npcs": self.npcs,
            "locations": self.locations,
            "treasures": self.treasures,
            "creatures": self.creatures,
            "characters": self.characters,
            "prose": self.prose,
        }

    def load_all(self) -> None:
        """Load every collection from disk."""
        for collection in self.collections().values():
            collection.load()

    def purge_expired_sessions(self) -> int:
        """Delete sessions past their expiry, returning how many were removed."""
        expired = [s.id for s in self.sessions.list() if s.is_expired]
        for session_id in expired:
            self.sessions.delete(session_id)
        return len(expired)


__all__ = ["Collection", "GameStateStore"]
