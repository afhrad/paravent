"""How characters live between adventures, and what it costs them each day.

A character keeps a lifestyle, from wretched to aristocratic (the file's
``lifestyle``; none kept costs nothing). Each day the world's clock passes off the road
charges every character of the world that keeps one: the 2014 handbook's *Lifestyle
Expenses* table gives the price a day; under the 2024 rules, which print no such table,
a day is a night's inn stay and three meals of that quality from *Food, Drink, and
Lodging*. Days spent travelling are paid in rations instead (app/services/travel.py).
A purse that cannot pay gives what it holds, and the rest is logged as owed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import Rows, copper
from app.services.protocol import ProtocolService
from app.services.text import strip_tags

LIFESTYLES = ("wretched", "squalid", "poor", "modest", "comfortable", "wealthy", "aristocratic")
# Meals in a day, under the 2024 rules' price of a meal.
MEALS = 3


def costs_2014(rows: Rows | None) -> dict[str, int]:
    """Copper a day by lifestyle, from the *Lifestyle Expenses* table."""
    out: dict[str, int] = {}
    for row in rows or []:
        if len(row) >= 2 and strip_tags(row[0]).strip().lower() in LIFESTYLES:
            out[strip_tags(row[0]).strip().lower()] = copper(row[1])
    return out


def costs_2024(rows: Rows | None) -> dict[str, int]:
    """Copper a day by lifestyle: an inn stay and three meals of that quality, from the
    *Food, Drink, and Lodging* table's two sections."""
    inn: dict[str, int] = {}
    meal: dict[str, int] = {}
    section: dict[str, int] | None = None
    for row in rows or []:
        head = strip_tags(row[0]).strip().lower() if row else ""
        if len(row) == 1 or (len(row) >= 2 and not row[1].strip()):
            section = inn if "inn" in head else meal if "meal" in head else None
            continue
        if section is not None and head in LIFESTYLES:
            section[head] = copper(row[1])
    return {
        level: inn.get(level, 0) + MEALS * meal.get(level, 0)
        for level in LIFESTYLES
        if level in inn or level in meal or level == "wretched"
    }


class LifestyleService:
    """Charges the world's characters their lifestyle for the days that pass."""

    def __init__(
        self,
        protocol: ProtocolService | None,
        *,
        party: Callable[[str], Sequence[PlayerCharacter]],
        purse: Callable[[str], int],
        pay: Callable[[str, int], None],
    ) -> None:
        """``party(world)`` the world's characters; ``purse(character)`` what it holds
        in copper; ``pay(character, copper)`` takes coins from it."""
        self._protocol = protocol
        self._party = party
        self._purse = purse
        self._pay = pay

    def charge(
        self, world_id: str, days: int, costs: dict[str, int], by: str, by_name: str
    ) -> list[tuple[str, int, int]]:
        """``days`` of each character's lifestyle: ``(character, paid, owed)`` for each
        that keeps one, logged as ``lifestyle:<level>:<days>:<paid>:<owed>``."""
        if days <= 0:
            return []
        out: list[tuple[str, int, int]] = []
        entries: list[ProtocolEntry] = []
        for pc in self._party(world_id):
            if not pc.lifestyle or pc.in_wizard:
                continue
            due = costs.get(pc.lifestyle, 0) * days
            paid = min(due, self._purse(pc.id))
            if paid:
                self._pay(pc.id, paid)
            out.append((pc.id, paid, due - paid))
            if due:  # paid quietly, logged here in the lifestyle's words
                entries.append(
                    ProtocolEntry(
                        actor=by,
                        actor_name=by_name,
                        subject=f"character:{pc.id}",
                        subject_name=pc.name,
                        kind=EntryKind.CUSTOM,
                        what=f"lifestyle:{pc.lifestyle}:{days}:{paid}:{due - paid}",
                        visibility=Visibility.PUBLIC,
                        scope=Scope(world=world_id),
                    )
                )
        if entries and self._protocol is not None:
            self._protocol.append(entries)
        return out


__all__ = ["LIFESTYLES", "MEALS", "LifestyleService", "costs_2014", "costs_2024"]
