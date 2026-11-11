"""Rolling a backstory: *Xanathar's Guide*'s "This Is Your Life" tables for the class and
background the draft has chosen (CHARGEN-WIZARD.md §8, Details).

``life.json`` holds, per 2014 class, why you became one plus tables of its own (a
barbarian's personal totem, a wizard's spellbook), and per 2014 background why you took it
up. The tables are matched by name, so a 2024 Fighter or Acolyte rolls on them too. They
are *Xanathar's*, so they are offered only where the world's books include it.

A 2014 background also prints its personality traits, ideals, bonds and flaws. Those come
from the background's own record, so they need no other book; a 2024 background has none.

A roll adds a line to the backstory, which stays the player's text to edit: the reasons
in their own first-person words, another table's line with its caption ("Tattoo: …").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import SourceSet
from app.models.pc import PlayerCharacter
from app.services.chargen.catalogue import Catalogue
from app.services.dice import Dice
from app.services.text import strip_tags
from app.store.npc_tables import NpcTables, Row, Table, table_rows

BOOK = "XGE"
ALL = "all"
# A background's tables, by their second column; the value is the caption kept here.
CHARACTERISTICS = {
    "personality trait": "personality",
    "trait": "personality",
    "ideal": "ideal",
    "bond": "bond",
    "flaw": "flaw",
}
# The rules give a character two personality traits, and one of the others.
TRAITS = 2
# A background's name, source and record, with the reader's language in its text.
Translate = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class LifeTable:
    """One table a draft can roll on."""

    # "class:", "background:" or "characteristic:", then the caption ("" for the reasons).
    key: str
    kind: str  # "class", "background" or "characteristic"
    owner: str  # "Barbarian", "Acolyte"
    caption: str
    table: Table


def _characteristics(node: Any, name: str, out: dict[str, Table]) -> None:
    """Every personality, ideal, bond and flaw table under ``node``, by caption."""
    if isinstance(node, Mapping):
        labels = node.get("colLabels") or []
        if node.get("type") == "table" and len(labels) == 2:
            caption = CHARACTERISTICS.get(strip_tags(str(labels[1])).strip().lower())
            if caption is not None and caption not in out:
                out[caption] = Table(caption, name, table_rows(node.get("rows")))
        for value in node.values():
            _characteristics(value, name, out)
    elif isinstance(node, list):
        for child in node:
            _characteristics(child, name, out)


@dataclass(frozen=True)
class Physique:
    """A rolled height and weight."""

    inches: int
    pounds: int


def roll_physique(table: Mapping[str, Any], dice: Dice) -> Physique:
    """A species' ``heightAndWeight``: its height modifier is added to the base height,
    and multiplied by the weight modifier (1 without one) for the weight."""
    height = dice.roll(str(table["heightMod"])).total if table.get("heightMod") else 0
    factor = dice.roll(str(table["weightMod"])).total if table.get("weightMod") else 1
    return Physique(
        int(table.get("baseHeight", 0)) + height,
        int(table.get("baseWeight", 0)) + height * factor,
    )


class Backstory:
    """The tables for a draft, and rolling on them."""

    def __init__(
        self,
        tables: NpcTables,
        source_set: SourceSet | None,
        dice: Dice,
        catalogue: Catalogue | None = None,
    ) -> None:
        """Bind to the corpus's tables, the draft's world's books, the dice and the
        catalogue that holds the draft's background."""
        self._tables = tables
        self._source_set = source_set or SourceSet()
        self._dice = dice
        self._catalogue = catalogue

    @property
    def available(self) -> bool:
        """True when the world's books include *Xanathar's Guide*."""
        return self._source_set.allows(BOOK)

    def tables(self, pc: PlayerCharacter, translate: Translate | None = None) -> list[LifeTable]:
        """The background's characteristics, then *Xanathar's* for the class and the
        background. ``translate`` puts the background's text in the reader's language."""
        out = self._background_tables(pc, translate)
        if not self.available:
            return out
        if pc.classes:
            name = pc.classes[0].name
            out += [
                LifeTable(f"class:{t.caption}", "class", name, t.caption, t)
                for t in self._tables.life_class(name)
            ]
        if pc.background is not None:
            name = pc.background.name
            out += [
                LifeTable(f"background:{t.caption}", "background", name, t.caption, t)
                for t in self._tables.life_background(name)
            ]
        return out

    def _background_tables(
        self, pc: PlayerCharacter, translate: Translate | None
    ) -> list[LifeTable]:
        if self._catalogue is None:
            return []
        offer = self._catalogue.find("background", pc.background)
        if offer is None:
            return []
        found: dict[str, Table] = {}
        data = translate(offer.name, offer.source, offer.data) if translate else offer.data
        _characteristics(data.get("entries"), offer.name, found)
        return [
            LifeTable(f"characteristic:{c}", "characteristic", offer.name, c, found[c])
            for c in dict.fromkeys(CHARACTERISTICS.values())
            if c in found
        ]

    def _row(self, table: Table, unlike: Row | None = None) -> Row:
        while True:
            rolled = self._dice.die(table.faces)
            row = next((r for r in table.rows if r.low <= rolled <= r.high), None)
            if row is not None and row != unlike:
                return row

    def roll(
        self,
        pc: PlayerCharacter,
        key: str,
        labels: Mapping[str, str] | None = None,
        translate: Translate | None = None,
    ) -> list[str]:
        """A line from the table ``key`` — or from each of them, for :data:`ALL` — as it
        goes into the backstory. ``labels`` names the characteristics in the reader's
        language; rolling all gives two different personality traits."""
        names = labels or {}
        lines: list[str] = []
        for life in self.tables(pc, translate):
            if key not in (ALL, life.key) or not life.table.rows:
                continue
            caption = names.get(life.caption, life.caption)
            rows = [self._row(life.table)]
            if key == ALL and life.caption == "personality" and len(life.table.rows) >= TRAITS:
                rows.append(self._row(life.table, unlike=rows[0]))
            for row in rows:
                text = strip_tags(row.cells[0])
                lines.append(f"{caption}: {text}" if caption else text)
        return lines


__all__ = [
    "ALL",
    "BOOK",
    "CHARACTERISTICS",
    "Backstory",
    "LifeTable",
    "Physique",
    "Translate",
    "roll_physique",
]
