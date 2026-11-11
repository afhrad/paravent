"""NPCs and treasure: the objects the story places (documentation/ADVENTURES.md §4).

Both live in the library as JSON records with a :class:`~app.models.gamestate.Scope`.
Their compendium parts — an NPC's statblock, a treasure's items — are references
(``Name|SOURCE``), looked up when shown, so a corpus update re-derives them.

Renaming either rewrites ``{@npc Old}`` / ``{@treasure Old}`` and their ``:::`` blocks in
every text of the world, so a rename never leaves a dead reference behind.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import Coins, Npc, Scope, Treasure, TreasureItem
from app.services import media, scope
from app.services.adventures import AdventureService, check_base, clean_name
from app.services.i18n import Translatable
from app.services.treasure_roll import Rolled
from app.store.gamestate import GameStateStore

# Roles with a meaning of their own; anything else typed is kept as a free tag.
KNOWN_ROLES = ("boss", "villain", "ally", "patron", "rival", "contact")
MAX_COUNT = 999
TAG_LIMIT = 32

# ``(name, source)`` → the compendium entry, or None.
Lookup = Callable[[str, str], Any]


class ObjectError(Translatable):
    """A change an NPC or treasure cannot take."""


class ObjectNotFound(LookupError):
    """No NPC or treasure with that id."""


def clean_roles(roles: list[str]) -> list[str]:
    """Lower-case, trimmed, de-duplicated tags, known roles first."""
    seen: list[str] = []
    for raw in roles:
        for part in raw.split(","):
            tag = " ".join(part.split()).lower()[:TAG_LIMIT]
            if tag and tag not in seen:
                seen.append(tag)
    return sorted(seen, key=lambda r: (KNOWN_ROLES.index(r) if r in KNOWN_ROLES else 99, r))


def split_ref(ref: str) -> tuple[str, str]:
    """``"Goblin|MM"`` → ``("Goblin", "MM")``."""
    name, _, source = ref.partition("|")
    return name.strip(), source.strip()


@dataclass(frozen=True)
class Place:
    """What a page shows about where an object sits."""

    scope: Scope
    names: tuple[str, ...]


class NpcService:
    """NPCs in the library."""

    def __init__(
        self, store: GameStateStore, adventures: AdventureService, monster: Lookup
    ) -> None:
        """Bind to the library, the adventures (for scopes) and a monster lookup."""
        self._store = store
        self._adventures = adventures
        self._monster = monster

    def list(self, world_id: str, within: Scope | None = None) -> list[Npc]:
        """A world's NPCs by name, or only those inside ``within``."""
        found = self._store.npcs.list(lambda n: n.scope.world_id == world_id)
        if within is not None:
            found = [n for n in found if scope.within(n.scope, within)]
        return sorted(found, key=lambda n: n.name.casefold())

    def bosses(self, world_id: str) -> builtins.list[Npc]:
        """The world's NPCs with the ``boss`` role, wherever they are placed."""
        return [n for n in self.list(world_id) if "boss" in n.roles]

    def get(self, npc_id: str) -> Npc:
        """One NPC, or :class:`ObjectNotFound`."""
        npc = self._store.npcs.find(npc_id)
        if npc is None:
            raise ObjectNotFound(npc_id)
        return npc

    def find(self, name: str, here: Scope) -> Npc | None:
        """The NPC called ``name`` nearest to ``here``."""
        return scope.nearest(
            self._store.npcs.list(), name, here, lambda n: n.scope, lambda n: n.name
        )

    def place(self, npc: Npc) -> Place:
        """Where the NPC sits, by name."""
        return Place(npc.scope, tuple(scope.label(self._adventures, npc.scope)))

    def statblock(self, npc: Npc) -> Any:
        """The compendium creature behind the NPC, or None."""
        if not npc.statblock:
            return None
        return self._monster(*split_ref(npc.statblock))

    def create(self, name: str, scope_value: str, world_id: str) -> Npc:
        """A new NPC in a scope of this world."""
        where = scope.decode(scope_value, self._adventures, world_id)
        return self._store.npcs.create(Npc(scope=where, name=clean_name(name)))

    def update(
        self,
        npc_id: str,
        *,
        name: str,
        scope_value: str,
        roles: builtins.list[str],
        statblock: str,
        notes: str,
        base: str | None = None,
        race: str | None = None,
        culture: str | None = None,
    ) -> Npc:
        """Save an NPC; a new name follows into the world's texts. ``base`` is the
        :func:`~app.services.adventures.text_version` of the notes the form opened;
        notes changed since raise :class:`~app.services.adventures.EditConflict`.
        ``race`` and ``culture`` stay as they are when not given."""
        before = self.get(npc_id)
        check_base(before.notes, notes, base)
        where = scope.decode(scope_value, self._adventures, before.scope.world_id)
        ref = statblock.strip()
        if ref:
            creature = self._monster(*split_ref(ref))
            if creature is None:
                raise ObjectError("npcs.error.statblock", ref=ref)
            ref = f"{creature.name}|{creature.source}"
        portrait = before.portrait if where.record_id == before.scope.record_id else None
        saved = self._store.npcs.update(
            npc_id,
            name=clean_name(name),
            scope=where,
            roles=clean_roles(roles),
            statblock=ref or None,
            notes=notes,
            portrait=portrait,
            race=before.race if race is None else race.strip(),
            culture=before.culture if culture is None else culture.strip(),
        )
        if saved.name != before.name:
            self._adventures.rewrite_references(
                where.world_id, "npc", before.name, saved.name, where.adventure_id
            )
        return saved

    def upload_portrait(self, npc_id: str, filename: str, data: bytes) -> str:
        """Store a portrait beside the NPC's scope and use it."""
        npc = self.get(npc_id)
        relative = media.store_upload(self._store.prose, npc.scope.record_id, filename, data)
        self._store.npcs.update(npc_id, portrait=relative)
        return relative

    def clear_portrait(self, npc_id: str) -> None:
        """Show the NPC without a portrait (the image file stays)."""
        self.get(npc_id)
        self._store.npcs.update(npc_id, portrait=None)

    def delete(self, npc_id: str) -> Npc:
        """Delete an NPC; returns what it was."""
        npc = self.get(npc_id)
        self._store.npcs.delete(npc_id)
        return npc


@dataclass(frozen=True)
class TreasureLine:
    """One line of a treasure, with what the compendium says about it."""

    index: int
    item: TreasureItem
    entry: Any  # the compendium record, None when it no longer resolves


class TreasureService:
    """Treasure in the library."""

    def __init__(self, store: GameStateStore, adventures: AdventureService, item: Lookup) -> None:
        """Bind to the library, the adventures (for scopes) and an item lookup."""
        self._store = store
        self._adventures = adventures
        self._item = item

    def list(self, world_id: str, within: Scope | None = None) -> list[Treasure]:
        """A world's treasure by name, or only that inside ``within``."""
        found = self._store.treasures.list(lambda t: t.scope.world_id == world_id)
        if within is not None:
            found = [t for t in found if scope.within(t.scope, within)]
        return sorted(found, key=lambda t: t.name.casefold())

    def get(self, treasure_id: str) -> Treasure:
        """One treasure, or :class:`ObjectNotFound`."""
        treasure = self._store.treasures.find(treasure_id)
        if treasure is None:
            raise ObjectNotFound(treasure_id)
        return treasure

    def find(self, name: str, here: Scope) -> Treasure | None:
        """The treasure called ``name`` nearest to ``here``."""
        return scope.nearest(
            self._store.treasures.list(), name, here, lambda t: t.scope, lambda t: t.name
        )

    def place(self, treasure: Treasure) -> Place:
        """Where the treasure sits, by name."""
        return Place(treasure.scope, tuple(scope.label(self._adventures, treasure.scope)))

    def lines(self, treasure: Treasure) -> builtins.list[TreasureLine]:
        """The items, each with its compendium entry."""
        return [
            TreasureLine(i, item, self._item(item.name, item.source))
            for i, item in enumerate(treasure.items)
        ]

    def create(self, name: str, scope_value: str, world_id: str) -> Treasure:
        """A new, empty treasure in a scope of this world."""
        where = scope.decode(scope_value, self._adventures, world_id)
        return self._store.treasures.create(Treasure(scope=where, name=clean_name(name)))

    def update(
        self,
        treasure_id: str,
        *,
        name: str,
        scope_value: str,
        coins: Coins,
        notes: str,
        base: str | None = None,
    ) -> Treasure:
        """Save name, scope, coins and notes; a new name follows into the texts. ``base``
        as for an NPC: notes changed since the form opened raise ``EditConflict``."""
        before = self.get(treasure_id)
        check_base(before.notes, notes, base)
        where = scope.decode(scope_value, self._adventures, before.scope.world_id)
        saved = self._store.treasures.update(
            treasure_id, name=clean_name(name), scope=where, coins=coins, notes=notes
        )
        if saved.name != before.name:
            self._adventures.rewrite_references(
                where.world_id, "treasure", before.name, saved.name, where.adventure_id
            )
        return saved

    def add_item(self, treasure_id: str, ref: str, count: int = 1) -> Treasure:
        """Add ``count`` of ``Name|SOURCE``; a second add raises the count."""
        entry = self._item(*split_ref(ref))
        if entry is None:
            raise ObjectError("treasure.error.item", ref=ref)
        self.get(treasure_id)
        count = max(1, min(MAX_COUNT, count))

        def add(treasure: Treasure) -> None:
            for item in treasure.items:
                if (item.name, item.source) == (entry.name, entry.source):
                    item.count = min(MAX_COUNT, item.count + count)
                    return
            treasure.items.append(TreasureItem(name=entry.name, source=entry.source, count=count))

        return self._store.treasures.mutate(treasure_id, add)

    def add_rolled(self, treasure_id: str, rolled: Rolled) -> Treasure:
        """Put a roll into the treasure: coins added, items counted, the rest as notes."""
        self.get(treasure_id)

        def add(treasure: Treasure) -> None:
            coins = treasure.coins.model_copy()
            for coin, amount in rolled.coins.items():
                setattr(coins, coin, getattr(coins, coin) + amount)
            treasure.coins = coins
            for name, source in rolled.items:
                line = next(
                    (i for i in treasure.items if (i.name, i.source) == (name, source)), None
                )
                if line is not None:
                    line.count = min(MAX_COUNT, line.count + 1)
                else:
                    treasure.items.append(TreasureItem(name=name, source=source, count=1))
            if rolled.unresolved:
                extra = "\n".join(f"- {text}" for text in rolled.unresolved)
                treasure.notes = f"{treasure.notes.rstrip()}\n\n{extra}".strip()

        return self._store.treasures.mutate(treasure_id, add)

    def set_items(self, treasure_id: str, items: builtins.list[TreasureItem]) -> Treasure:
        """Replace every item at once (the MCP tools write a treasure whole)."""
        self.get(treasure_id)
        return self._store.treasures.update(treasure_id, items=items)

    def set_count(self, treasure_id: str, index: int, count: int) -> Treasure:
        """Set how many of line ``index`` there are; 0 removes it."""
        treasure = self.get(treasure_id)
        if not 0 <= index < len(treasure.items):
            raise ObjectError("treasure.error.line")

        def change(treasure: Treasure) -> None:
            if count <= 0:
                del treasure.items[index]
            else:
                treasure.items[index].count = min(MAX_COUNT, count)

        return self._store.treasures.mutate(treasure_id, change)

    def delete(self, treasure_id: str) -> Treasure:
        """Delete a treasure; returns what it was."""
        treasure = self.get(treasure_id)
        self._store.treasures.delete(treasure_id)
        return treasure


__all__ = [
    "KNOWN_ROLES",
    "NpcService",
    "ObjectError",
    "ObjectNotFound",
    "Place",
    "TreasureLine",
    "TreasureService",
    "clean_roles",
    "split_ref",
]
