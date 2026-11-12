"""Looking up one reference entry for the reference page, in the right world.

A link carries the category, source and name of the entry it resolved to, and the
world it was rendered in — so links *inside* the entry resolve against the same books
the page that linked to it used (documentation/TECHSTACK.md §6c).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import SourceSet
from app.services.tokens import Token
from app.store.copyresolver import CopyResolver
from app.store.gamestate import GameStateStore
from app.store.published import PublishedBooks, table_anchor


class ReferenceNotFound(LookupError):
    """No entry of that category, name and source is loaded."""


@dataclass(frozen=True)
class ReferenceEntry:
    """One entry as the reference page shows it."""

    category: str
    data: dict[str, Any]
    world_id: str | None
    # A legendary creature's lair and regional actions (its ``legendaryGroup``), which
    # the monster record points at rather than repeating.
    lair: dict[str, Any] | None = None
    # A deck's cards in its order, each with how many the deck holds.
    cards: tuple[tuple[dict[str, Any], int], ...] = ()
    # Where a table is printed, readable here: (url, book name, chapter name).
    printed_in: tuple[str, str, str] | None = None
    # More text about an item type from a later book in the world's books, for the type
    # and its items: (book, entries).
    more: tuple[tuple[str, list[Any]], ...] = ()
    # Its round token (documentation/TOKENS.md); None where nobody asked for one.
    token: Token | None = None

    @property
    def name(self) -> str:
        return str(self.data.get("name", ""))

    @property
    def source(self) -> str:
        return str(self.data.get("source", ""))


@dataclass(frozen=True)
class Printing:
    """Another printing of an entry: its name and book, and whether it is the one the
    world's links lead to."""

    name: str
    source: str
    used: bool


# Categories reached through their parent, never by a bare name (TECHSTACK.md §6c).
COMPOSITE_CATEGORIES = frozenset({"subclass", "classFeature", "subclassFeature", "card"})


class ReferenceService:
    """Entries by category/source/name, and how to render links inside them."""

    def __init__(
        self,
        compendium: Any,
        store: GameStateStore,
        books: PublishedBooks | None = None,
        token: Callable[[str, dict[str, Any], str | None], Token] | None = None,
    ) -> None:
        """Bind to the loaded compendium, the library (for worlds), the rulebooks (for
        where a table is printed) and ``token(category, data, world)`` for the entry's
        round token."""
        self._token = token
        self._compendium = compendium
        self._store = store
        self._books = books

    def source_set(self, world_id: str | None) -> SourceSet | None:
        """The world's declared books, or None (everything imported)."""
        world = self._store.worlds.find(world_id) if world_id else None
        return world.source_set if world is not None else None

    def resolver(self) -> Any:
        """A resolver over the loaded compendium."""
        return self._compendium.resolver()

    def known_world(self, world_id: str | None) -> str | None:
        """``world_id`` if such a world exists, else None — so links carry no stale id."""
        return world_id if world_id and self._store.worlds.find(world_id) else None

    def get(self, category: str, source: str, name: str, world_id: str | None) -> ReferenceEntry:
        """The entry, or :class:`ReferenceNotFound`."""
        if category not in self._compendium.categories():
            raise ReferenceNotFound(f"{category}/{source}/{name}")
        entry = None
        if source and source != "-":
            entry = self._compendium.find(category, name, source)
        if entry is None:
            result = self._compendium.resolver().resolve_parts(
                category, name, None if source in ("", "-") else source, self.source_set(world_id)
            )
            entry = result.entry
        if entry is None:
            entry = self._by_shown_name(category, source, name)
        if entry is None:
            raise ReferenceNotFound(f"{category}/{source}/{name}")
        data = entry.model_dump(by_alias=True, exclude_none=True)
        lair = self.legendary_group(data.get("legendaryGroup")) if category == "monster" else None
        cards = self.deck_cards(data) if category == "deck" else ()
        printed = self.printed_in(data) if category in ("table", "tableGroup") else None
        more = self.type_entries(category, data, world_id)
        token = self._token(category, data, world_id) if self._token else None
        return ReferenceEntry(category, data, world_id, lair, cards, printed, more, token)

    def type_entries(
        self, category: str, data: dict[str, Any], world_id: str | None
    ) -> tuple[tuple[str, list[Any]], ...]:
        """More text about an item type from a later book (``itemTypeAdditionalEntries``),
        for the type's page and its items', when the world uses that book. Matched by the
        type's abbreviation, so the 2024 gaming sets read Xanathar's words too."""
        if category == "itemType":
            code = str(data.get("abbreviation", ""))
        elif category in ("item", "baseitem"):
            code = str(data.get("type", "")).split("|", 1)[0]
        else:
            return ()
        if not code:
            return ()
        books = self.source_set(world_id)
        found: list[tuple[str, list[Any]]] = []
        for extra in self._compendium.all("itemTypeAdditionalEntries"):
            applies = str(getattr(extra, "applies_to", "")).split("|", 1)[0]
            source = str(getattr(extra, "source", ""))
            if applies.lower() == code.lower() and (books is None or books.allows(source)):
                found.append((source, list(getattr(extra, "entries", []))))
        return tuple(found)

    def _by_shown_name(self, category: str, source: str, name: str) -> Any:
        """An entry keyed by a code (an item property, ``v|xphb``) found by the name its
        page shows ("Versatile"), which is what a link to it carries."""
        wanted, book = name.lower(), source.lower()
        for entry in self._compendium.all(category):
            if getattr(entry, "ref_name", None) == getattr(entry, "name", None):
                return None  # this category is keyed by name: nothing more to find
            if str(entry.name).lower() == wanted and book in ("", "-", entry.source.lower()):
                return entry
        return None

    def printed_in(self, table: dict[str, Any]) -> tuple[str, str, str] | None:
        """The rulebook chapter a table is printed in, with the heading it sits under:
        ``("/books/DMG/7#h-artifacts", "Dungeon Master's Guide", "Treasure")``.

        The corpus's own table index (``gendata-tables.json``) gives the chapter by
        position; adventures are not rulebooks and get no link.
        """
        chapter = table.get("chapter")
        index = chapter.get("index") if isinstance(chapter, dict) else None
        if self._books is None or not isinstance(index, int):
            return None
        book = self._books.get(str(table.get("source", "")))
        body = self._books.body(book.id) if book is not None else None
        if book is None or body is None or not 0 <= index < len(body.sections):
            return None
        section = body.sections[index]
        anchor = table_anchor(section, str(table.get("caption") or table.get("name", "")))
        url = f"/books/{book.id}/{index}" + (f"#{anchor}" if anchor else "")
        return url, book.name, section.name

    def other_printings(self, entry: ReferenceEntry) -> list[Printing]:
        """Other printings of the entry in the world's books (TECHSTACK.md §6c).

        5eTools models the 2024 books as reprints of the 2014 ones, so a world declaring
        both resolves "fireball" to the 2024 spell and sets the 2014 one aside. That is
        the right default but a decision, and a declared book is never dropped silently:
        on the chosen printing this lists the ones set aside, and on a set-aside one it
        names the printing the world uses. An entry the world resolves alone has none.
        """
        if entry.category in COMPOSITE_CATEGORIES:
            return []
        named = str(entry.data.get("abbreviation") or entry.name)
        result = self._compendium.resolver().resolve_parts(
            entry.category, named, None, self.source_set(entry.world_id)
        )
        chosen = result.entry
        if chosen is None:
            return []
        source = entry.source.lower()
        if str(chosen.source).lower() == source:
            return [Printing(str(a.name), str(a.source), False) for a in result.alternatives]
        if any(str(a.source).lower() == source for a in result.alternatives):
            return [Printing(str(chosen.name), str(chosen.source), True)]
        return []

    def deck_cards(self, deck: dict[str, Any]) -> tuple[tuple[dict[str, Any], int], ...]:
        """A deck's cards, read from its ``cards`` list.

        Each is ``"Name|Deck"`` or ``"Name|Deck|SOURCE"``, bare or as a dict's ``uid``
        with a ``count``. A card naming no source is the deck's own printing: the 2014
        and 2024 *Deck of Many Things* hold cards of the same names. A deck may list
        another deck's cards — the 13-card variant draws from the full deck.
        """
        by_key: dict[tuple[str, str, str], Any] = {}
        for card in self._compendium.all("card"):
            key = (card.name.lower(), card.set.lower(), card.source.lower())
            by_key[key] = card
        out: list[tuple[dict[str, Any], int]] = []
        for listed in deck.get("cards") or []:
            uid = listed.get("uid", "") if isinstance(listed, dict) else str(listed)
            count = int(listed.get("count", 1)) if isinstance(listed, dict) else 1
            name, set_name, source = ([*uid.split("|"), "", ""])[:3]
            source = source or str(deck.get("source", ""))
            wanted = (name.lower(), set_name.lower())
            card = by_key.get((*wanted, source.lower())) or next(
                (c for (n, s, _), c in by_key.items() if (n, s) == wanted), None
            )
            if card is not None:
                out.append((card.model_dump(by_alias=True, exclude_none=True), count))
        return tuple(out)

    def card(self, deck: ReferenceEntry, name: str) -> ReferenceEntry | None:
        """One card of a deck, as an entry of its own — for its preview."""
        wanted = name.lower()
        for data, _count in deck.cards:
            if str(data.get("name", "")).lower() == wanted:
                return ReferenceEntry("card", data, deck.world_id)
        return None

    def legendary_group(self, pointer: Any) -> dict[str, Any] | None:
        """The lair and regional actions a creature's ``legendaryGroup`` points at."""
        if not isinstance(pointer, dict) or not pointer.get("name"):
            return None
        base = pointer.get("_base")
        template = (
            self._compendium.find(
                "legendaryGroupTemplate", str(pointer["name"]), pointer.get("source") or None
            )
            if isinstance(base, dict)
            else None
        )
        if template is not None and isinstance(base, dict):
            # A template's group adds to the creature's own (a legendary shadow dragon
            # keeps its kind's lair actions and gains the shadow dragon's).
            own = self.legendary_group(base)
            if own is not None:
                mods = (template.apply or {}).get("_mod") or {}
                CopyResolver({}).apply_mods(own, mods)
                return own
        group = self._compendium.find(
            "legendaryGroup", str(pointer["name"]), pointer.get("source") or None
        )
        if group is None:
            return None
        data: dict[str, Any] = group.model_dump(by_alias=True, exclude_none=True)
        return data


__all__ = ["Printing", "ReferenceEntry", "ReferenceNotFound", "ReferenceService"]
