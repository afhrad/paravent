"""Running costs: the businesses, strongholds and garrisons characters own.

A property costs its owner every day, whatever the party is doing: the 2014 guide's
*Maintenance Costs* table prices an abbey, a farm, a keep or a shop by the day (its
hirelings' wages included), the 2024 guide's three fortifications. The gamemaster gives
a character a property of the table or with a price of its own; each day the world's
clock passes is paid from the owner's purse, and what the purse lacks is logged as owed,
as lifestyle is (app/services/lifestyle.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence

from app.models.gamestate import Property
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import Rows, copper
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.services.text import strip_tags
from app.store.gamestate import GameStateStore

# The table, by caption and the books that print it, newest rules first where both are.
TABLE = "Maintenance Costs"
BOOKS = ("DMG", "XDMG")


class UpkeepError(Translatable):
    """A property that cannot be given as asked."""


def costs(rows: Rows | None) -> list[tuple[str, int]]:
    """The *Maintenance Costs* table as ``(property, copper a day)``, in its order."""
    out: list[tuple[str, int]] = []
    for row in rows or []:
        if len(row) >= 2 and (name := strip_tags(row[0]).strip()) and (price := copper(row[1])):
            out.append((name, price))
    return out


class UpkeepService:
    """Keeps the world's properties (``library/properties.json``) and charges their
    owners for the days that pass."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        purse: Callable[[str], int],
        pay: Callable[[str, int], None],
    ) -> None:
        """``purse(character)`` what it holds in copper; ``pay(character, copper)``
        takes coins from it."""
        self._store = store
        self._protocol = protocol
        self._purse = purse
        self._pay = pay

    def of_world(self, world_id: str | None) -> list[Property]:
        """The world's properties, by owner and name."""
        if world_id is None:
            return []
        found = self._store.properties.list(lambda p: p.world_id == world_id)
        return sorted(found, key=lambda p: (p.character_id, p.name.casefold()))

    def of_character(self, character_id: str) -> list[Property]:
        """What a character owns."""
        found = self._store.properties.list(lambda p: p.character_id == character_id)
        return sorted(found, key=lambda p: p.name.casefold())

    def give(
        self,
        world_id: str,
        character_id: str,
        name: str,
        per_day: int,
        kind: str = "",
        by: str = "",
        by_name: str = "",
    ) -> Property:
        """A property for a character of the world, at ``per_day`` copper; logged."""
        name = " ".join(name.split())[:120] or " ".join(kind.split())[:120]
        pc = self._store.characters.find(character_id)
        if not name or pc is None or pc.world_id != world_id or not 0 <= per_day <= 100_000_000:
            raise UpkeepError("upkeep.error.bad")
        held = Property(
            id=uuid.uuid4().hex[:12],
            world_id=world_id,
            character_id=character_id,
            name=name,
            kind=kind[:120],
            per_day=per_day,
        )
        saved = self._store.properties.create(held)
        self._log(world_id, by, by_name, pc.id, pc.name, f"upkeep_new:{_safe(name)}:{per_day}")
        return saved

    def remove(self, world_id: str, property_id: str) -> None:
        """Take a property off the books (sold, lost, given up)."""
        held = self._store.properties.find(property_id)
        if held is not None and held.world_id == world_id:
            self._store.properties.delete(property_id)

    def charge(self, world_id: str, days: int, by: str, by_name: str) -> list[tuple[str, int, int]]:
        """``days`` of every property's running costs from its owner's purse:
        ``(property, paid, owed)`` each, logged as ``upkeep:<name>:<days>:<paid>:<owed>``."""
        if days <= 0:
            return []
        out: list[tuple[str, int, int]] = []
        for held in self.of_world(world_id):
            pc = self._store.characters.find(held.character_id)
            if pc is None or not held.per_day:
                continue
            due = held.per_day * days
            paid = min(due, self._purse(pc.id))
            if paid:
                self._pay(pc.id, paid)
            out.append((held.id, paid, due - paid))
            self._log(
                world_id,
                by,
                by_name,
                pc.id,
                pc.name,
                f"upkeep:{_safe(held.name)}:{days}:{paid}:{due - paid}",
            )
        return out

    def _log(
        self, world_id: str, by: str, by_name: str, character_id: str, name: str, what: str
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{character_id}",
                    subject_name=name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            ]
        )


def _safe(text: str) -> str:
    return text.replace(":", " ")


def priced(rows: Sequence[tuple[str, int]], kind: str) -> int | None:
    """The table's price a day for ``kind``; None when the table has no such row."""
    return next((price for name, price in rows if name.lower() == kind.lower()), None)


__all__ = ["BOOKS", "TABLE", "UpkeepError", "UpkeepService", "costs", "priced"]
