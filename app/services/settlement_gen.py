"""Rolling a settlement on the guides' tables: its size, what defines it, what it is known
for, its trouble, who leads it, a tavern and some shops.

The tables stay in the user's corpus and are found by their caption in the world's books
(app/services/book_tables.py), as the weather and the downtime tables are. A world on the
2024 guide rolls on its tables (*Settlements by Size*, *Defining Traits*, *Claims to
Fame*, *Current Calamities*, *Local Leaders*, *Tavern Names*, *Random Shops*) and takes
the government and race relations, which only the 2014 guide prints, from that one where
it declares it; a world on the 2014 guide rolls on its own (*Forms of Government*,
*Ruler's Status*, *Race Relations*, *Notable Traits*, *Known For Its...*, *Current
Calamity*, *Tavern Name Generator*, *Shop*). A part whose table the world lacks is left
out. Each part rolls again on its own; nothing is kept until the gamemaster takes it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.services.book_tables import Rows, row_for, span
from app.services.dice import Dice

GUIDE_2014 = "DMG"
GUIDE_2024 = "XDMG"
# Where a tavern named by the world's naming style comes from.
NAMING = "naming"
# How many shops a settlement shows.
SHOPS = 3

# part → (book, caption), in the order they read.
TABLES_2024: dict[str, tuple[str, str]] = {
    "government": (GUIDE_2014, "Forms of Government"),
    "leader": (GUIDE_2024, "Local Leaders"),
    "relations": (GUIDE_2014, "Race Relations"),
    "trait": (GUIDE_2024, "Defining Traits"),
    "fame": (GUIDE_2024, "Claims to Fame"),
    "calamity": (GUIDE_2024, "Current Calamities"),
    "tavern": (GUIDE_2024, "Tavern Names"),
    "shops": (GUIDE_2024, "Random Shops"),
}
TABLES_2014: dict[str, tuple[str, str]] = {
    "government": (GUIDE_2014, "Forms of Government"),
    "leader": (GUIDE_2014, "Ruler's Status"),
    "relations": (GUIDE_2014, "Race Relations"),
    "trait": (GUIDE_2014, "Notable Traits"),
    "fame": (GUIDE_2014, "Known For Its..."),
    "calamity": (GUIDE_2014, "Current Calamity"),
    "tavern": (GUIDE_2014, "Tavern Name Generator"),
    "shops": (GUIDE_2014, "Shop"),
}
SIZES_CAPTION = (GUIDE_2024, "Settlements by Size")
SIZES = ("village", "town", "city")
PARTS = tuple(TABLES_2014)

# Finds a table by caption in a book the world declares: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]


@dataclass(frozen=True)
class Size:
    """A settlement's size as the 2024 guide's table gives it (population, the most a
    purchase there may cost), or by name alone where the world lacks it."""

    key: str
    name: str
    population: str = ""
    most: str = ""


class SettlementGenerator:
    """Rolls a settlement's parts on the tables of the world's guide."""

    def __init__(
        self,
        find: Finder,
        dice: Dice,
        *,
        new_rules: bool,
        tavern: Callable[[], str] | None = None,
    ) -> None:
        """``find`` reads the world's tables; ``new_rules`` when the world plays the 2024
        guide (it declares it); ``tavern`` names a tavern by the world's own naming style
        for taverns, in place of the guide's table, where it has one."""
        self._find = find
        self._dice = dice
        self._tables = TABLES_2024 if new_rules else TABLES_2014
        self._rows: dict[str, Rows | None] = {}
        self._tavern = tavern

    def _table(self, part: str) -> Rows | None:
        if part not in self._rows:
            book, caption = self._tables[part]
            self._rows[part] = self._find(caption, [book])
        return self._rows[part]

    def parts(self) -> list[str]:
        """The parts the world's books have a table for, in reading order."""
        return [p for p in PARTS if self._table(p) or (p == "tavern" and self._tavern)]

    def source_of(self, part: str) -> str:
        """The book a part is rolled in; ``naming`` for a tavern named by a style."""
        if part == "tavern" and self._tavern is not None:
            return NAMING
        return self._tables[part][0]

    def sizes(self) -> list[Size]:
        """Village, town and city, with the 2024 guide's numbers where the world has it."""
        rows = self._find(SIZES_CAPTION[1], [SIZES_CAPTION[0]]) or []
        by_name = {r[0].strip().casefold(): r for r in rows if r}
        out = []
        for key in SIZES:
            row = by_name.get(key)
            out.append(
                Size(key, row[0], row[1] if len(row) > 1 else "", row[2] if len(row) > 2 else "")
                if row
                else Size(key, key.capitalize())
            )
        return out

    def _roll_row(self, rows: Rows, skip: Sequence[str] = ()) -> list[str] | None:
        """A row by the die its first column reads (a d20, a d100); one whose text is in
        ``skip`` is rolled again, a few times."""
        ends = [end for r in rows if r and (found := span(r[0])) for end in found]
        die = max((e for e in ends if 0 < e < 10_000), default=len(rows))
        row = None
        for _ in range(12):
            total = self._dice.die(die)
            row = row_for(rows, total) or rows[(total - 1) % len(rows)]
            if row and row[-1] not in skip:
                return row
        return row

    def roll_part(self, part: str, current: str = "") -> str:
        """One part rolled (again): not what it is now, where the table offers another."""
        if part == "tavern" and self._tavern is not None:
            return self._tavern()
        rows = self._table(part)
        if not rows:
            return ""
        if part == "tavern":
            first = self._roll_row(rows) or []
            second = self._roll_row(rows) or []
            name = " ".join(x for x in (first[1:2] + second[2:3]) if x)
            return name if name != current else self.roll_part(part) if len(rows) > 1 else name
        if part == "shops":
            picked: list[str] = []
            for _ in range(min(SHOPS, len(rows))):
                row = self._roll_row(rows, picked)
                if row and row[-1] not in picked:
                    picked.append(row[-1])
            return ", ".join(picked)
        row = self._roll_row(rows, [current] if current and len(rows) > 1 else [])
        return row[-1] if row else ""

    def roll(self, keep: Mapping[str, str] | None = None) -> dict[str, str]:
        """Every part rolled, but those in ``keep``."""
        kept = dict(keep or {})
        return {p: kept[p] if kept.get(p) else self.roll_part(p) for p in self.parts()}


def plays_2024(sources: Sequence[str]) -> bool:
    """Whether a world rolls on the 2024 guide: it declares it (or declares nothing, and
    the corpus has it)."""
    declared = {s.upper() for s in sources}
    return not declared or GUIDE_2024 in declared


__all__ = ["PARTS", "SIZES", "SettlementGenerator", "Size", "plays_2024"]
