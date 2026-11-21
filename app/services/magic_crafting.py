"""Crafting a magic item in downtime, by the world's guide.

A character needs a **formula** for the item, which the gamemaster gives from the item's
page (``PlayerCharacter.formulas``): magic items are the gamemaster's to hand out, and
the books say so. With one, the character's downtime page offers to craft it, by the
first of these the world's books include:

* **The 2024 guide** (a world playing the 2024 rules): the *Magic Item Crafting Time and
  Cost* table by rarity, halved for a consumable other than a spell scroll; proficiency
  in Arcana, the tool the *Magic Item Tools* table names for the item's category, every
  spell the item casts prepared; an item made from a weapon or armour adds its price.
  The cost is paid when the work starts.
* ***Xanathar's Guide***: its *Magic Item Crafting Time and Cost* table (workweeks and
  cost, halved for a consumable), the appropriate tool or Arcana; the exotic material is
  an adventure of its own, its *Magic Item Ingredients* table naming the challenge it
  calls for. The cost is paid when the work starts.
* **The 2014 guide**: its *Crafting Magic Items* table (creation cost and the minimum
  level by rarity), progress in 25 gp a day, paid day by day; the character must be a
  spellcaster with spell slots who can cast every spell the item produces.

Spell scrolls are scribed (app/services/downtime.py); the tables, read by caption, stay
in the user's corpus. Whatever a text leaves to the table — the material's availability,
a spell slot spent each day, assistants — stays the table's.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.pc import ChosenOption, PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.activities import RARITIES, rarity_of
from app.services.book_tables import Rows, copper, days
from app.services.text import strip_tags
from app.store.gamestate import GameStateStore

# What a day of work under the 2014 guide pays for, in copper.
DMG_DAILY = 2500
# Item type codes by the 2024 guide's categories; anything else is a wondrous item.
CATEGORIES = {
    "LA": "armor",
    "MA": "armor",
    "HA": "armor",
    "S": "armor",
    "P": "potion",
    "RG": "ring",
    "RD": "rod",
    "SC": "scroll",
    "ST": "staff",
    "WD": "wand",
    "M": "weapon",
    "R": "weapon",
    "A": "weapon",
    "AF": "weapon",
}
# Consumables, whose time and cost are halved: potions and ammunition (a spell scroll is
# scribed instead).
CONSUMABLE = frozenset({"P", "A", "AF"})
_TOOL = re.compile(r"\{@item ([^}|]+)")


@dataclass(frozen=True)
class Costs:
    """One rarity's row: days of work, copper, and the 2014 guide's minimum level."""

    days: int
    copper: int
    level: int = 0


@dataclass(frozen=True)
class CraftRule:
    """How the world's guide makes magic items: ``book`` (``XDMG``, ``XGE``, ``DMG``),
    its costs by rarity, the tools by category (2024) and the ingredients' challenge
    by rarity (*Xanathar's*)."""

    book: str
    costs: Mapping[str, Costs]
    tools: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    ingredients: Mapping[str, str] = field(default_factory=dict)

    @property
    def daily(self) -> int:
        """What each day of work costs (the 2014 guide), else 0: paid at the start."""
        return DMG_DAILY if self.book == "DMG" else 0


def _rarity_rows(rows: Rows | None) -> dict[str, list[str]]:
    out = {}
    for row in rows or []:
        rarity = rarity_of(strip_tags(row[0]).strip().lower()) if row else ""
        if rarity:
            out[rarity] = row
    return out


def rule_from(
    book: str,
    costs: Rows | None,
    tools: Rows | None = None,
    ingredients: Rows | None = None,
) -> CraftRule | None:
    """The rule a guide's tables make, None when its costs table is missing."""
    by_rarity: dict[str, Costs] = {}
    for rarity, row in _rarity_rows(costs).items():
        if len(row) < 3:
            continue
        if book == "XDMG":
            by_rarity[rarity] = Costs(days(row[1]), copper(row[2]))
        elif book == "XGE":
            weeks = int(re.sub(r"\D", "", row[1]) or 0)
            by_rarity[rarity] = Costs(weeks * 5, copper(row[2]))
        else:
            cost = copper(row[1])
            level = int(re.sub(r"\D", "", row[2]) or 0)
            by_rarity[rarity] = Costs(-(-cost // DMG_DAILY), cost, level)
    if not by_rarity:
        return None
    by_category = {
        strip_tags(row[0]).strip().lower(): tuple(t.strip() for t in _TOOL.findall(row[1]))
        for row in tools or []
        if len(row) >= 2
    }
    needs = {rarity: strip_tags(row[1]) for rarity, row in _rarity_rows(ingredients).items()}
    return CraftRule(book, by_rarity, by_category, needs)


def category(data: Mapping[str, Any]) -> str:
    """The 2024 guide's category of an item: armor, potion, ring, … or wondrous item."""
    code = str(data.get("type") or "").split("|")[0].upper()
    return CATEGORIES.get(code, "wondrous item")


def item_spells(data: Mapping[str, Any]) -> list[str]:
    """The spells an item casts (``attachedSpells``), by name, lower case."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            found.append(node.split("|")[0].split("#")[0].strip().lower())
        elif isinstance(node, Mapping):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data.get("attachedSpells"))
    return list(dict.fromkeys(s for s in found if s))


@dataclass(frozen=True)
class Crafter:
    """What crafting asks of the character, read from its sheet."""

    level: int
    arcana: bool = False
    tools: Collection[str] = ()
    spells: Collection[str] = ()
    slots: bool = False


@dataclass(frozen=True)
class Plan:
    """An item as the character would craft it: its rarity, days, what is paid at the
    start and each day (copper), what the character still lacks (keys of
    ``crafting.missing.*`` with their values) and the ingredient's challenge."""

    rarity: str
    days: int
    paid: int
    daily: int
    missing: tuple[tuple[str, str], ...] = ()
    ingredient: str = ""
    tools: tuple[str, ...] = ()

    @property
    def possible(self) -> bool:
        return not self.missing


def plan(
    rule: CraftRule, data: Mapping[str, Any], crafter: Crafter, base_price: int = 0
) -> Plan | None:
    """How the character would craft an item by the rule; None for an item the rule
    has no row for (an artifact, a rarity left open, a spell scroll)."""
    rarity = rarity_of(data.get("rarity"))
    costs = rule.costs.get(rarity) if rarity in RARITIES else None
    code = str(data.get("type") or "").split("|")[0].upper()
    if costs is None or code == "SC":
        return None
    work, cost = costs.days, costs.copper
    if rule.book != "DMG" and code in CONSUMABLE:
        work, cost = max(1, work // 2), cost // 2
    if rule.book == "XDMG":
        cost += base_price
    missing: list[tuple[str, str]] = []
    spells = {s.lower() for s in crafter.spells}
    lacking = [s for s in item_spells(data) if s not in spells]
    held = {t.lower() for t in crafter.tools}
    tools = rule.tools.get(category(data), ())
    has_tool = any(t.lower() in held for t in tools)
    if rule.book == "XDMG":
        if not crafter.arcana:
            missing.append(("arcana", ""))
        if tools and not has_tool:
            missing.append(("tool", ", ".join(tools)))
        if lacking:
            missing.append(("spells", ", ".join(lacking)))
    elif rule.book == "XGE":
        if not (crafter.arcana or has_tool):
            missing.append(("arcana_or_tool", ", ".join(tools)))
    else:
        if crafter.level < costs.level:
            missing.append(("level", str(costs.level)))
        if not crafter.slots:
            missing.append(("slots", ""))
        if lacking:
            missing.append(("spells", ", ".join(lacking)))
    daily = rule.daily
    return Plan(
        rarity,
        work,
        0 if daily else cost,
        daily,
        tuple(missing),
        rule.ingredients.get(rarity, ""),
        tools,
    )


class Formulas:
    """The magic item formulas characters hold: given and taken back by the gamemaster,
    each logged."""

    def __init__(self, store: GameStateStore, protocol: Any | None) -> None:
        self._store = store
        self._protocol = protocol

    def give(
        self, world_id: str, ref: str, character_ids: Sequence[str], by: str, by_name: str
    ) -> list[str]:
        """Give each character the formula for ``ref``: the names of those who did not
        hold it yet."""
        option = ChosenOption.model_validate(ref)
        names = []
        for character_id in character_ids:
            pc = self._store.characters.find(character_id)
            if pc is None or any(f.reference.lower() == ref.lower() for f in pc.formulas):
                continue
            self._mutate(character_id, lambda pc: pc.formulas.append(option))
            names.append(pc.name or "?")
            self._log(world_id, by, by_name, pc, f"formula:given:{ref}")
        return names

    def drop(self, character_id: str, ref: str, by: str, by_name: str) -> bool:
        """The character no longer holds the formula: False when it did not."""
        pc = self._store.characters.find(character_id)
        if pc is None or not any(f.reference.lower() == ref.lower() for f in pc.formulas):
            return False

        def change(pc: PlayerCharacter) -> None:
            pc.formulas[:] = [f for f in pc.formulas if f.reference.lower() != ref.lower()]

        self._mutate(character_id, change)
        self._log(pc.world_id or "", by, by_name, pc, f"formula:taken:{ref}")
        return True

    def _mutate(self, character_id: str, change: Callable[[PlayerCharacter], None]) -> None:
        self._store.characters.mutate(character_id, change)

    def _log(self, world_id: str, by: str, by_name: str, pc: PlayerCharacter, what: str) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}",
                    subject_name=pc.name or "?",
                    kind=EntryKind.CUSTOM,
                    what=what,
                    scope=Scope(world=world_id or None),
                )
            ]
        )


__all__ = [
    "Formulas",
    "CONSUMABLE",
    "DMG_DAILY",
    "Costs",
    "CraftRule",
    "Crafter",
    "Plan",
    "category",
    "item_spells",
    "plan",
    "rule_from",
]
