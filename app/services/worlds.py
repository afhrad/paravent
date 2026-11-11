"""Worlds and the books they declare (documentation/TECHSTACK.md §6c).

A world names the books legal at its table. A character in that world resolves every
reference — a spell, an item, a feature — against those books only, so a 2014 table does
not quietly get 2024 statblocks and a reference into a book nobody owns stays inert.
An empty declaration means "everything imported": a new table works before anyone has
configured anything.

The book list offered for declaring comes from the corpus itself: the ``books.json`` and
``adventures.json`` index files give titles, groups and publication dates, and the loaded
compendium says which sources actually have content. Both are read-only reference data.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.gamestate import ONE_EDITION_SOURCES, SourceSet, World
from app.services.auth import AuthError
from app.store.gamestate import GameStateStore

logger = logging.getLogger(__name__)

# How the picker groups books, in display order. Anything else lands in "other".
GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("core", frozenset({"core"})),
    ("supplement", frozenset({"supplement", "supplement-alt", "homecraft"})),
    ("setting", frozenset({"setting", "setting-alt"})),
    ("adventure", frozenset()),  # everything from adventures.json
    ("other", frozenset({"other", "organized-play", "screen", "recipe"})),
)


class WorldNotFound(LookupError):
    """No world with that id."""


@dataclass(frozen=True)
class Book:
    """A source the corpus has content for."""

    code: str
    name: str
    group: str
    published: str = ""

    @property
    def is_2024(self) -> bool:
        """True for the 2024 ("one") rules books."""
        return self.code.upper() in ONE_EDITION_SOURCES


class BookCatalog:
    """Every source with loaded content, titled from the corpus's own index files."""

    def __init__(self, reference_dir: Path | None, loaded_sources: set[str]) -> None:
        """Read the index files under ``reference_dir`` and keep the loaded sources."""
        index: dict[str, tuple[str, str, str]] = {}
        if reference_dir is not None:
            for filename, key, adventure in (
                ("books.json", "book", False),
                ("adventures.json", "adventure", True),
            ):
                for entry in _read_index(reference_dir / filename, key):
                    code = str(entry.get("source") or entry.get("id") or "")
                    if code:
                        group = "adventure" if adventure else _group_of(str(entry.get("group", "")))
                        index[code.upper()] = (
                            str(entry.get("name", code)),
                            group,
                            str(entry.get("published", "")),
                        )
        books = []
        for code in loaded_sources:
            name, group, published = index.get(code.upper(), (code, "other", ""))
            books.append(Book(code, name, group, published))
        order = {name: i for i, (name, _) in enumerate(GROUPS)}
        self.books = sorted(books, key=lambda b: (order.get(b.group, 99), b.published, b.name))
        self._by_code = {b.code.upper(): b for b in self.books}

    def get(self, code: str) -> Book | None:
        """The book with ``code`` (any case), if it has loaded content."""
        return self._by_code.get(code.upper())

    def grouped(self) -> list[tuple[str, list[Book]]]:
        """Books by picker group, empty groups left out."""
        out = []
        for name, _ in GROUPS:
            members = [b for b in self.books if b.group == name]
            if members:
                out.append((name, members))
        return out


def _group_of(raw: str) -> str:
    for name, members in GROUPS:
        if raw in members:
            return name
    return "other"


def _players(data: WorldInput) -> list[str] | None:
    return list(data.player_sources) if data.player_sources is not None else None


def _read_index(path: Path, key: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("Book index %s not read: %s", path.name, exc)
        return []
    entries = data.get(key, []) if isinstance(data, dict) else []
    return [e for e in entries if isinstance(e, dict)]


@dataclass(frozen=True)
class WorldInput:
    """What the world form submits."""

    name: str
    description: str = ""
    sources: tuple[str, ...] = ()
    allow_rerolls: bool = False
    # What the world's own texts are written in; Claude writes drafts in it (MCP.md §5).
    language: str = "en"
    # The declared books players may read; None: the defaults (PLAYER-VIEW.md).
    player_sources: tuple[str, ...] | None = None


class WorldService:
    """Creating, editing and deleting worlds, and placing characters in them."""

    def __init__(self, store: GameStateStore) -> None:
        """Bind to the library."""
        self._store = store

    def list(self) -> list[World]:
        """Every world, by name."""
        return sorted(self._store.worlds.list(), key=lambda w: w.name.casefold())

    def get(self, world_id: str) -> World:
        """One world, or :class:`WorldNotFound`."""
        world = self._store.worlds.find(world_id)
        if world is None:
            raise WorldNotFound(world_id)
        return world

    def find(self, world_id: str | None) -> World | None:
        """One world, or None."""
        return self._store.worlds.find(world_id) if world_id else None

    @staticmethod
    def _clean(data: WorldInput) -> tuple[str, SourceSet]:
        name = data.name.strip()
        if not name:
            raise AuthError("error.world_name_required")
        seen: list[str] = []
        for code in data.sources:
            code = code.strip()
            if code and code.upper() not in {s.upper() for s in seen}:
                seen.append(code)
        return name, SourceSet(sources=seen)

    def create(self, data: WorldInput) -> World:
        """A new world, in a folder of its own under ``library/worlds/``."""
        name, sources = self._clean(data)
        world = World(
            name=name,
            description=data.description.strip(),
            source_set=sources,
            allow_rerolls=data.allow_rerolls,
            language=data.language,
            player_sources=_players(data),
        )
        return self._store.worlds.create(world)

    def update(self, world_id: str, data: WorldInput) -> World:
        """Change a world's name, description or books."""
        self.get(world_id)
        name, sources = self._clean(data)
        return self._store.worlds.update(
            world_id,
            name=name,
            description=data.description.strip(),
            source_set=sources,
            allow_rerolls=data.allow_rerolls,
            language=data.language,
            player_sources=_players(data),
        )

    def set_naming(self, world_id: str, keys: Iterable[str]) -> World:
        """The naming styles the world rolls names from; none means every style."""
        self.get(world_id)
        chosen = list(dict.fromkeys(k.strip() for k in keys if k.strip()))
        return self._store.worlds.update(world_id, naming_styles=chosen)

    def character_count(self, world_id: str) -> int:
        """How many characters live in a world."""
        return len(self._store.characters.list(lambda pc: pc.world_id == world_id))

    def delete(self, world_id: str) -> int:
        """Delete a world. Its characters stay, without a world; returns how many.

        The world's folder, media included, is moved to ``library/.trash/``, not erased.
        """
        self.get(world_id)
        adventures = self._store.adventures.list(lambda a: a.world_id == world_id)
        if adventures:
            raise AuthError("error.world_has_adventures", count=len(adventures))
        moved = [pc.id for pc in self._store.characters.list() if pc.world_id == world_id]
        for character_id in moved:
            self._store.characters.update(character_id, world_id=None)
        self._store.worlds.delete(world_id)
        return len(moved)

    def place(self, character_id: str, world_id: str | None) -> None:
        """Put a character in a world, or in none."""
        if world_id:
            self.get(world_id)
        self._store.characters.update(character_id, world_id=world_id or None)


__all__ = ["GROUPS", "Book", "BookCatalog", "WorldInput", "WorldNotFound", "WorldService"]
