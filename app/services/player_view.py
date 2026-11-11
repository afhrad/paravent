"""What a player may read (documentation/PLAYER-VIEW.md).

A player reads an entry when its kind is player material and its book is one of their
player books, or when it is their character's own. The gamemaster reads everything, so
for them there is no :class:`Reader` at all.

Player books are per world: the ticks the gamemaster set (``World.player_sources``), or,
until then, the player's handbooks and the supplements among the world's books.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import World
from app.models.pc import ChosenOption, PlayerCharacter
from app.services.auth import Identity

# The player's handbooks; with the supplements, a world's player books until the
# gamemaster says otherwise.
HANDBOOKS = frozenset({"PHB", "XPHB"})
DEFAULT_GROUPS = frozenset({"supplement"})

# What a player chooses or uses. Anything else, a category added later included, is the
# gamemaster's.
READABLE = frozenset(
    {
        "action",
        "background",
        "baseitem",
        "charoption",
        "class",
        "classFeature",
        "condition",
        "deity",
        "disease",
        "feat",
        "item",
        "itemGroup",
        "itemMastery",
        "itemProperty",
        "itemType",
        "language",
        "optionalfeature",
        "race",
        "sense",
        "skill",
        "spell",
        "status",
        "subclass",
        "subclassFeature",
        "subrace",
        "table",
        "variantrule",
    }
)
# Of these, only the mundane ones: a magic item is read once a character has it.
ITEMS = frozenset({"item", "itemGroup", "baseitem"})
MUNDANE = frozenset({"none", "unknown", ""})


def default_player_book(code: str, group: str) -> bool:
    """Whether a book is a player book while the gamemaster has set nothing."""
    return code.upper() in HANDBOOKS or group in DEFAULT_GROUPS


def player_books(world: World, groups: Mapping[str, str]) -> frozenset[str]:
    """A world's player books (upper case). ``groups`` is every loaded book's picker group
    by code; a world declaring nothing draws on all of them."""
    declared = [s.upper() for s in world.source_set.sources] or [c.upper() for c in groups]
    if world.player_sources is not None:
        ticked = {s.upper() for s in world.player_sources}
        return frozenset(c for c in declared if c in ticked)
    return frozenset(c for c in declared if default_player_book(c, groups.get(c, "")))


def player_ticks(world: World | None, groups: Mapping[str, str]) -> frozenset[str]:
    """The "Players may read" ticks the world form starts with, for every loaded book:
    the world's choice for its declared books, the defaults for the rest."""
    declared = {s.upper() for s in world.source_set.sources} if world is not None else set()
    chosen = (
        {s.upper() for s in world.player_sources}
        if world is not None and world.player_sources is not None
        else None
    )

    def ticked(code: str, group: str) -> bool:
        if chosen is not None and code in declared:
            return code in chosen
        return default_player_book(code, group)

    return frozenset(code for code, group in groups.items() if ticked(code, group))


def player_choice(
    ticked: Iterable[str], declared: Iterable[str], groups: Mapping[str, str]
) -> list[str] | None:
    """What the world form's ticks store: None while they are the defaults for the
    declared books, else the declared books ticked."""
    books = [c.upper() for c in declared] or list(groups)
    wanted = {c.upper() for c in ticked}
    chosen = [c for c in books if c in wanted]
    defaults = [c for c in books if default_player_book(c, groups.get(c, ""))]
    return None if chosen == defaults else chosen


def _data(entry: Any) -> Mapping[str, Any]:
    if isinstance(entry, Mapping):
        return entry
    dump = getattr(entry, "model_dump", None)
    return dump(by_alias=True, exclude_none=True) if dump is not None else {}


def _key(name: Any, source: Any) -> tuple[str, str]:
    return (str(name or "").lower(), str(source or "").lower())


@dataclass(frozen=True)
class Reader:
    """One player's view: the books they read and what their characters have."""

    books: frozenset[str]
    # (name, source) of everything the characters have, lower case; source "" when the
    # file names none.
    owned: frozenset[tuple[str, str]] = frozenset()
    classes: frozenset[str] = frozenset()
    # (class, subclass short name or name), lower case.
    subclasses: frozenset[tuple[str, str]] = frozenset()
    # The one world the player's characters live in, if one: where their pages resolve
    # when the address names none.
    home: str | None = None

    def reads_book(self, code: str) -> bool:
        """Whether the player reads this book's chapters."""
        return code.upper() in self.books

    def readable(self, category: str, entry: Any) -> bool:
        """Whether the player may read ``entry`` of ``category``."""
        data = _data(entry)
        name, source = _key(data.get("name"), data.get("source"))
        if (name, source) in self.owned or (name, "") in self.owned:
            return True
        if category == "classFeature" and str(data.get("className", "")).lower() in self.classes:
            return True
        if category == "subclassFeature":
            owner = str(data.get("className", "")).lower()
            short = str(data.get("subclassShortName", "")).lower()
            if (owner, short) in self.subclasses:
                return True
        if category not in READABLE or source.upper() not in self.books:
            return False
        if category in ITEMS:
            return str(data.get("rarity") or "").lower() in MUNDANE
        return True


def _options(pc: PlayerCharacter) -> Iterable[ChosenOption]:
    yield from (o for o in (pc.species, pc.subspecies, pc.background) if o is not None)
    yield from pc.feats
    yield from pc.optional_features
    yield from pc.spells.usable
    for granted in pc.spells.granted:
        yield from granted.spells
    for item in pc.inventory:
        yield item.item


def reader_for(
    characters: Iterable[PlayerCharacter],
    worlds: Iterable[World],
    groups: Mapping[str, str],
    subclass_names: Mapping[tuple[str, str], str] | None = None,
) -> Reader:
    """A player's view from their characters and the worlds those live in (every world
    when they have none; with no world at all, the default books of everything loaded).
    ``subclass_names`` maps (class, subclass name) to its short name, lower case, as
    subclass features name their subclass."""
    owned_pcs = list(characters)
    worlds = list(worlds)
    homes = {pc.world_id for pc in owned_pcs if pc.world_id}
    chosen = [w for w in worlds if w.id in homes] or worlds
    books: set[str] = set()
    for world in chosen:
        books |= player_books(world, groups)
    if not worlds:  # no world yet reads like one that declares nothing
        books = {c for c, g in groups.items() if default_player_book(c, g)}
    owned: set[tuple[str, str]] = set()
    classes: set[str] = set()
    subclasses: set[tuple[str, str]] = set()
    names = subclass_names or {}
    for pc in owned_pcs:
        owned.update(_key(o.name, o.source) for o in _options(pc))
        for entry in pc.classes:
            owned.add(_key(entry.name, entry.source))
            cls = entry.name.lower()
            classes.add(cls)
            if entry.subclass is not None:
                owned.add(_key(entry.subclass.name, entry.subclass.source))
                sub = entry.subclass.name.lower()
                subclasses.add((cls, sub))
                subclasses.add((cls, names.get((cls, sub), sub)))
    home = chosen[0].id if len(chosen) == 1 else None
    return Reader(
        frozenset(books), frozenset(owned), frozenset(classes), frozenset(subclasses), home
    )


def readable_names(reader: Reader, compendium: Any) -> dict[str, frozenset[str]]:
    """Per category, the English names of the entries ``reader`` may read: the names a
    player meets, to give first."""
    out: dict[str, frozenset[str]] = {}
    for category in compendium.categories():
        names = frozenset(
            str(model.name)
            for model in compendium.all(category)
            if reader.readable(category, model)
        )
        if names:
            out[category] = names
    return out


def needs_reader(identity: Identity | None) -> bool:
    """Whether ``identity`` reads through a :class:`Reader`: everyone but the gamemaster."""
    return identity is None or not identity.is_gamemaster


__all__ = [
    "DEFAULT_GROUPS",
    "HANDBOOKS",
    "READABLE",
    "Reader",
    "default_player_book",
    "needs_reader",
    "player_books",
    "player_choice",
    "player_ticks",
    "readable_names",
    "reader_for",
]
