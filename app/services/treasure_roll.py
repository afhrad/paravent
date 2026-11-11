"""Rolling treasure on the *Dungeon Master's Guide* tables (TECHSTACK.md §6b, phase 2).

**Individual treasure** is one d100 on the challenge rating's table: coins. **A hoard**
is its coins plus a d100 row of gems, art objects and magic items. Gems and art come in
an amount of one value ("2d6 × 50 gp gems"), each a random line of that table. Magic
items are rolled on their table, and a row may name an item, or a choice:

* ``fromItems`` — one of these items;
* ``fromGeneric`` — one of the specific items a generic variant makes (+1 Ammunition);
* ``fromGroup`` — one of an item group's items (a potion of resistance);
* ``fromMatching`` — 2024's "a rare item": a theme is rolled (arcana, armaments,
  implements, relics — even odds), then that theme's table of that rarity.

2024 hoards roll "1d4 − 1 items by level": the hoard's band picks the by-level table.

**Which book's tables** follows the world: the 2024 *Dungeon Master's Guide* when it is
declared (or no book is), else the 2014 one; the gamemaster can choose either. Every
item resolves in the world's books; what does not (an item the world lacks, an art
object the corpus names but does not list) is returned as text, never dropped.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.gamestate import SourceSet
from app.services.dice import Dice
from app.store.loot import LootTables
from app.store.reference import parse_reference

Kind = Literal["individual", "hoard"]
BOOK_2014 = "DMG"
BOOK_2024 = "XDMG"
THEMES = ("arcana", "armaments", "implements", "relics")
# 2024's hoard bands → its by-level tables.
LEVEL_BANDS = {0: "1-4", 5: "5-10", 11: "11-16", 17: "17-20"}
_ITEM_TAG = re.compile(r"\{@item ([^}]+)\}")
_AMOUNT = re.compile(r"^\s*([0-9d+\- ]+?)\s*(?:\*\s*(\d+))?\s*$")
COINS = ("pp", "gp", "ep", "sp", "cp")

# (name, source) of an item in the world's books, from a name and an optional book.
Resolve = Callable[[str, str | None], tuple[str, str] | None]
# The specific items a generic variant makes, as (name, source), its book's first.
Specifics = Callable[[str, str], Sequence[tuple[str, str]]]


class TreasureRollError(LookupError):
    """No table for that book and challenge rating."""


@dataclass
class Rolled:
    """What a roll produced: coins, items in the world's books, and the rest as text."""

    coins: dict[str, int] = field(default_factory=dict)
    items: list[tuple[str, str]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    # What was rolled, for the gamemaster: "d100 = 42", "Magic Item Table A".
    lines: list[str] = field(default_factory=list)


def tables_for(source_set: SourceSet | None, books: set[str]) -> str:
    """Which *Dungeon Master's Guide* a world rolls on: 2024 unless only 2014 is declared."""
    if source_set is None or source_set.is_unrestricted:
        return BOOK_2024 if BOOK_2024 in books else BOOK_2014
    if source_set.allows(BOOK_2024) and BOOK_2024 in books:
        return BOOK_2024
    if source_set.allows(BOOK_2014):
        return BOOK_2014
    return BOOK_2024 if "one" in source_set.editions and BOOK_2024 in books else BOOK_2014


class TreasureRoller:
    """Rolls on the loot tables and resolves what comes up in a world's books."""

    def __init__(
        self, loot: LootTables, resolve: Resolve, specifics: Specifics, dice: Dice | None = None
    ) -> None:
        """Bind to the tables, an item resolver, the variant items and the dice."""
        self._loot = loot
        self._resolve = resolve
        self._specifics = specifics
        self._dice = dice or Dice()

    # -- dice ------------------------------------------------------------------------

    def _amount(self, expression: Any) -> int:
        """``"6d6*100"``, ``"1d4-1"`` or ``3`` → a number (never below 0)."""
        if isinstance(expression, int):
            return max(0, expression)
        found = _AMOUNT.match(str(expression))
        if found is None:
            return 0
        total = self._dice.roll(found[1].replace(" ", "")).total
        return max(0, total * int(found[2] or 1))

    def _pick(self, options: Sequence[Any]) -> Any:
        return options[self._dice.die(len(options)) - 1] if options else None

    def _row(self, table: dict[str, Any]) -> dict[str, Any] | None:
        """The row a roll lands on (rows without ranges are picked evenly)."""
        rows: list[dict[str, Any]] = [r for r in table.get("table") or [] if isinstance(r, dict)]
        if not rows:
            return None
        if any("max" not in r for r in rows):
            return rows[self._dice.die(len(rows)) - 1]
        # The die is the table's own: d100 for most, d12 for "Magic armor" on table I.
        roll = self._dice.die(max(int(r.get("max", 0)) for r in rows) or 100)
        return next((r for r in rows if r.get("min", 0) <= roll <= r.get("max", 0)), None)

    # -- items -----------------------------------------------------------------------

    def _item_text(self, text: str, out: Rolled, *, book: str = "", depth: int = 0) -> None:
        """An item named in a table line: resolved if it can be, else kept as text.

        A line names an item as a tag (``{@item Bag of Holding}``) or plainly, with or
        without its book (``Spell Scroll (Level 2)|XDMG``). A name that is an item
        group (Elemental Gem, Carpet of Flying) is one of the group's items; a generic
        variant's name (Vorpal Sword) is one of the items it makes.
        """
        tag = _ITEM_TAG.search(text)
        payload = tag[1] if tag is not None else text.strip()
        name, source, _display = parse_reference(payload, "item")
        found = self._resolve(name, source)
        if found is not None:
            out.items.append(found)
            return
        members = self._loot.group(name, source or book or None) if depth == 0 else []
        if members:
            self._item_text(str(self._pick(members)), out, book=book, depth=1)
            return
        made = self._specifics(name, source or book)  # a generic variant: Vorpal Sword
        if made:
            out.items.append(self._pick(made))
            return
        out.unresolved.append(re.sub(r"\{@\w+ ([^}|]+)[^}]*\}", r"\1", text).split("|")[0].strip())

    def _magic_row(self, row: dict[str, Any], book: str, out: Rolled) -> None:
        choose = row.get("choose")
        if isinstance(choose, dict):
            if choose.get("fromItems"):
                self._item_text(str(self._pick(choose["fromItems"])), out, book=book)
                return
            if choose.get("fromGeneric"):
                generic, _sep, source = str(self._pick(choose["fromGeneric"])).partition("|")
                made = self._specifics(generic, source or book)
                if made:
                    out.items.append(self._pick(made))
                    return
                # Not made from base items (dragon scale mail): the group of that name.
                members = self._loot.group(generic, source or book)
                self._item_text(str(self._pick(members) or generic), out, book=book)
                return
            if choose.get("fromGroup"):
                group, _sep, source = str(self._pick(choose["fromGroup"])).partition("|")
                members = self._loot.group(group, source or book)
                self._item_text(str(self._pick(members) or group), out, book=book)
                return
            matching = choose.get("fromMatching")
            if isinstance(matching, dict) and matching.get("rarity"):
                theme = str(self._pick(THEMES))
                self._magic(f"{theme}.{matching['rarity']}", book, out)
                return
        if isinstance(row.get("table"), list):
            sub = self._row(row)
            if sub is not None and sub.get("item"):
                self._item_text(str(sub["item"]), out, book=book)
                return
        if row.get("item"):
            self._item_text(str(row["item"]), out, book=book)

    def _magic(self, table_type: str, book: str, out: Rolled) -> None:
        table = self._loot.magic(book, table_type)
        if table is None:
            out.unresolved.append(table_type)
            return
        out.lines.append(str(table.get("name", table_type)))
        row = self._row(table)
        if row is not None:
            self._magic_row(row, book, out)

    def _valuables(
        self, kind: Literal["gems", "art"], spec: dict[str, Any], book: str, out: Rolled
    ) -> None:
        value = spec.get("type")
        if not isinstance(value, int):
            return
        table = self._loot.gems(book, value) if kind == "gems" else self._loot.art(book, value)
        count = self._amount(spec.get("amount", 1))
        out.lines.append(f"{count} × {table.get('name', value) if table else value}")
        for _ in range(count):
            line = self._pick((table or {}).get("table") or [])
            if isinstance(line, str):
                self._item_text(line, out, book=book)

    # -- rolling ---------------------------------------------------------------------

    def roll(self, kind: Kind, cr: float, book: str) -> Rolled:
        """Roll individual treasure or a hoard for a challenge rating, on ``book``'s tables."""
        table = (
            self._loot.individual(book, cr) if kind == "individual" else self._loot.hoard(book, cr)
        )
        if table is None:
            raise TreasureRollError(f"{kind} {book} {cr}")
        out = Rolled()
        out.lines.append(f"{book} · {table.get('name', '')}")
        coins = dict(table.get("coins") or {})
        row = self._row(table)
        if row is not None:
            coins.update(row.get("coins") or {})
        for coin, expression in coins.items():
            if coin in COINS:
                out.coins[coin] = out.coins.get(coin, 0) + self._amount(expression)
        if kind == "hoard" and row is not None:
            if isinstance(row.get("gems"), dict):
                self._valuables("gems", row["gems"], book, out)
            if isinstance(row.get("artObjects"), dict):
                self._valuables("art", row["artObjects"], book, out)
            for spec in row.get("magicItems") or []:
                if not isinstance(spec, dict):
                    continue
                table_type = str(spec.get("type", ""))
                if table_type == "randomByLevel":
                    low = int(table.get("crMin", 0))
                    band = LEVEL_BANDS.get(low, "17-20" if low >= 17 else "1-4")
                    table_type = f"byLevel.{band}"
                for _ in range(self._amount(spec.get("amount", 1))):
                    self._magic(table_type, book, out)
        return out


def roller_for(
    compendium: Any,
    loot: LootTables,
    source_set: SourceSet | None,
    book: str,
    dice: Dice | None = None,
) -> TreasureRoller:
    """A roller resolving items in a world's books, preferring ``book``'s variants."""
    resolver = compendium.resolver()

    def resolve(name: str, source: str | None) -> tuple[str, str] | None:
        for category in ("item", "baseitem"):
            found = resolver.resolve_parts(category, name, source, source_set)
            if found.entry is not None:
                entry = found.entry
                return str(getattr(entry, "name", name)), str(getattr(entry, "source", ""))
        return None

    def specifics(generic: str, generic_source: str) -> Sequence[tuple[str, str]]:
        made = [
            (str(getattr(i, "name", "")), str(getattr(i, "source", "")), variant)
            for i in compendium.all("item")
            if isinstance(variant := getattr(i, "genericVariant", None), dict)
            and str(variant.get("name", "")).lower() == generic.lower()
        ]
        allowed = [
            m
            for m in made
            if source_set is None or source_set.is_unrestricted or source_set.allows(m[1])
        ]
        own = [m for m in allowed if str(m[2].get("source", "")) == generic_source]
        return [(name, source) for name, source, _v in (own or allowed)]

    return TreasureRoller(loot, resolve, specifics, dice)


__all__ = [
    "BOOK_2014",
    "BOOK_2024",
    "Rolled",
    "TreasureRollError",
    "TreasureRoller",
    "roller_for",
    "tables_for",
]
