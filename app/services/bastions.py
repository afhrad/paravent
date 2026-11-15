"""A character's bastion (the 2024 rules): special facilities and their orders.

From level 5 a character has a bastion. Its special facilities are chosen from the world's
books, each no higher in level than the character; how many follows the level (two at 5,
four at 9, five at 13, six at 17). Each facility may be given one of its orders for the
next bastion turn; the gamemaster runs the turn, which logs the orders for the table and
starts the next. What an order yields is the facility's own text, linked from the page;
basic facilities, defenders and bastion events are left to the table.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import Bastion, BastionFacility, SourceSet
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

FROM_LEVEL = 5
# (character level, special facilities), highest first.
_SPECIAL = ((17, 6), (13, 5), (9, 4), (5, 2))


class BastionError(Translatable):
    """A change the bastion rules refuse."""


def special_slots(level: int) -> int:
    """How many special facilities a character of ``level`` may have."""
    return next((n for start, n in _SPECIAL if level >= start), 0)


@dataclass(frozen=True)
class Facility:
    """A special facility as the bastion page offers it."""

    name: str
    source: str
    level: int
    orders: tuple[str, ...]

    @property
    def reference(self) -> str:
        return f"{self.name}|{self.source}"


def facilities(entries: Iterable[Any], source_set: SourceSet | None, level: int) -> list[Facility]:
    """The special facilities of the world's books a character of ``level`` may build."""
    wanted = (
        None if source_set is None or source_set.is_unrestricted else set(source_set.normalized)
    )
    found = []
    for entry in entries:
        data = entry.model_dump(by_alias=True, exclude_none=True)
        if data.get("facilityType") != "special":
            continue
        if wanted is not None and str(data.get("source", "")).lower() not in wanted:
            continue
        need = int(data.get("level") or FROM_LEVEL)
        if need > level:
            continue
        orders = tuple(str(o) for o in data.get("orders") or [])
        found.append(Facility(str(data["name"]), str(data["source"]), need, orders))
    return sorted(found, key=lambda f: (f.level, f.name.lower()))


class BastionService:
    """Reads and writes ``library/bastions.json``; a bastion turn goes to the log."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def of(self, character_id: str) -> Bastion:
        """A character's bastion (empty until something is chosen)."""
        return self._store.bastions.find(character_id) or Bastion(id=character_id)

    def rename(self, character_id: str, name: str) -> Bastion:
        return self._save(
            self.of(character_id).model_copy(update={"name": " ".join(name.split())[:80]})
        )

    def add(self, character_id: str, level: int, facility: Facility) -> Bastion:
        """Build a special facility, within the level's count and not twice."""
        bastion = self.of(character_id)
        if level < FROM_LEVEL or facility.level > level:
            raise BastionError("bastion.error.level")
        if len(bastion.facilities) >= special_slots(level):
            raise BastionError("bastion.error.full")
        if any(f.ref.lower() == facility.reference.lower() for f in bastion.facilities):
            raise BastionError("bastion.error.twice")
        built = [*bastion.facilities, BastionFacility(ref=facility.reference)]
        return self._save(bastion.model_copy(update={"facilities": built}))

    def remove(self, character_id: str, index: int) -> Bastion:
        bastion = self.of(character_id)
        kept = [f for n, f in enumerate(bastion.facilities) if n != index]
        return self._save(bastion.model_copy(update={"facilities": kept}))

    def order(self, character_id: str, index: int, order: str, allowed: Iterable[str]) -> Bastion:
        """Give a facility one of its orders for the next turn ("" takes it back)."""
        bastion = self.of(character_id)
        if not 0 <= index < len(bastion.facilities) or (order and order not in set(allowed)):
            raise BastionError("bastion.error.order")
        changed = [
            f.model_copy(update={"order": order}) if n == index else f
            for n, f in enumerate(bastion.facilities)
        ]
        return self._save(bastion.model_copy(update={"facilities": changed}))

    def turn(
        self, character_id: str, character_name: str, world_id: str | None, by: str, by_name: str
    ) -> Bastion:
        """A bastion turn: the orders are logged for the table, then cleared."""
        bastion = self.of(character_id)
        orders = [f"{f.ref}={f.order}" for f in bastion.facilities if f.order]
        number = bastion.turns + 1
        if self._protocol is not None:
            self._protocol.append(
                [
                    ProtocolEntry(
                        actor=by,
                        actor_name=by_name,
                        subject=f"character:{character_id}",
                        subject_name=character_name,
                        kind=EntryKind.CUSTOM,
                        what=f"bastion:{number}:{';'.join(orders)}",
                        scope=Scope(world=world_id),
                    )
                ]
            )
        cleared = [f.model_copy(update={"order": ""}) for f in bastion.facilities]
        return self._save(bastion.model_copy(update={"facilities": cleared, "turns": number}))

    def _save(self, bastion: Bastion) -> Bastion:
        bastion.touch()
        return self._store.bastions.upsert(bastion)


__all__ = [
    "FROM_LEVEL",
    "BastionError",
    "BastionService",
    "Facility",
    "facilities",
    "special_slots",
]
