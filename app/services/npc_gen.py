"""Rolling an NPC on the corpus's tables (PROJECT.md "Generate NPCs").

A name, then the *Dungeon Master's Guide*'s "Detailed NPCs" elements — appearance, a
high and a low ability, a talent, a mannerism, how they treat others, an ideal, a bond,
a flaw or secret — and a trinket from *Xanathar's Guide*. Each element can be rolled
again on its own; nothing is kept until the gamemaster takes it.

**Which tables.** The 2024 guide prints only names, appearance and secrets; the rest
exist only in the 2014 guide. So a world on the 2024 guide (TECHSTACK.md §6c, the same
rule as treasure) rolls those three on the 2024 tables and the others on the 2014 ones;
every element says which book it came from. An element whose book is not imported is
left out.

**Names** come from a naming style (documentation/NAMES.md): the books' tables, the
starters or the table's own.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.models.gamestate import SourceSet
from app.services.dice import Dice
from app.services.naming import ANY_STYLE, GENDERS, MadeName, Naming, NamingError, StyleEntry
from app.services.treasure_roll import BOOK_2024, tables_for
from app.store.npc_tables import NpcTables, Row, Table

ELEMENTS = (
    "appearance",
    "high",
    "low",
    "talent",
    "mannerism",
    "interaction",
    "ideal",
    "bond",
    "flaw",
    "secret",
    "trinket",
)
IDEAL_AXES = ("good", "evil", "lawful", "chaotic", "neutral", "other")
TRINKET_SOURCE = "XGE"

# element → (book, caption) per edition; "2024" falls back to "2014" for the rest.
_TABLES_2014 = {
    "appearance": "NPC Appearance",
    "high": "NPC Abilities",
    "low": "NPC Low Abilities",
    "talent": "NPC Talents",
    "mannerism": "NPC Mannerisms",
    "interaction": "NPC Interaction Traits",
    "bond": "NPC Bonds",
    "flaw": "NPC Flaws and Secrets",
}
_TABLES_2024 = {"appearance": "NPC Appearance", "secret": "NPC Secrets"}
_ABILITY = re.compile(r"^([A-Z][a-z]+)\s*[-–—]\s*")
_WORDS = re.compile(r"[a-z]+")


@dataclass
class GeneratedNpc:
    """What was rolled: a name and the elements, each with the book it came from."""

    name: str = ""
    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    # The name's parts, which become the NPC's when the name is taken (NAMES.md §3).
    parts: dict[str, str] = field(default_factory=dict)


def ability_text(text: str) -> str:
    """``"Strength-powerful, brawny"`` → ``"Strength: powerful, brawny"``."""
    return _ABILITY.sub(r"\1: ", text)


def _words(text: str) -> set[str]:
    return set(_WORDS.findall(text.lower()))


class NpcGenerator:
    """Rolls NPCs on the tables, in one edition's books."""

    def __init__(
        self,
        tables: NpcTables,
        edition: str,
        dice: Dice | None = None,
        axis_label: Callable[[str], str] | None = None,
        naming: Naming | None = None,
        default_style: str = "",
        language: str = "",
    ) -> None:
        """``edition`` is "2014" or "2024": which guide's tables come first.

        ``axis_label`` names an ideal's alignment ("lawful") in the reader's language;
        ``naming`` holds the name styles (the books' alone when not given),
        ``default_style`` the one a new NPC starts from (the books' default when empty),
        ``language`` the reader's, whose styles a race's key prefers (NAMES.md §5).
        """
        self._naming = naming or Naming(tables)
        self._default_style = default_style
        self._language = language
        self._tables = tables
        self._edition = edition
        self._dice = dice or Dice()
        self._axis = axis_label or (lambda axis: axis)

    @property
    def edition(self) -> str:
        """ "2014" or "2024"."""
        return self._edition

    # -- tables ----------------------------------------------------------------------

    def _source(self, element: str) -> Table | None:
        if element == "ideal":
            return None
        if self._edition == "2024" and element in _TABLES_2024:
            found = self._tables.table("XDMG", _TABLES_2024[element])
            if found is not None:
                return found
        if element == "secret":
            return None  # 2014 has its flaws-and-secrets table instead
        if (
            element == "flaw"
            and self._edition == "2024"
            and self._tables.table("XDMG", _TABLES_2024["secret"])
        ):
            return None  # the 2024 secret replaces it
        caption = _TABLES_2014.get(element)
        return self._tables.table("DMG", caption) if caption else None

    def _ideals(self) -> list[tuple[str, Table]]:
        found = []
        for axis in IDEAL_AXES:
            table = self._tables.table("DMG", f"{axis.title()} Ideals")
            if table is not None and table.rows:
                found.append((axis, table))
        return found

    def elements(self) -> list[str]:
        """The elements these tables can roll, in order."""
        out = []
        for element in ELEMENTS:
            if element == "ideal":
                present = bool(self._ideals())
            elif element == "trinket":
                present = bool(self._tables.trinkets())
            else:
                table = self._source(element)
                present = table is not None and bool(table.rows)
            if present:
                out.append(element)
        return out

    # -- dice ------------------------------------------------------------------------

    def _pick(self, options: Sequence[str]) -> str:
        return options[self._dice.die(len(options)) - 1] if options else ""

    def _row(self, table: Table, skip: Sequence[Row] = ()) -> Row | None:
        rows = [r for r in table.rows if r not in skip]
        if not rows:
            return None
        faces = max(r.high for r in rows)
        for _ in range(20):
            rolled = self._dice.die(faces)
            row = next((r for r in rows if r.low <= rolled <= r.high), None)
            if row is not None:
                return row
        return rows[self._dice.die(len(rows)) - 1]

    def _text(self, table: Table, skip: Sequence[Row] = ()) -> str:
        row = self._row(table, skip)
        return row.cells[0] if row else ""

    # -- elements --------------------------------------------------------------------

    def roll_element(self, element: str, current: dict[str, str] | None = None) -> str:
        """One element's text, or "" when there is no table for it."""
        current = current or {}
        if element == "ideal":
            ideals = self._ideals()
            if not ideals:
                return ""
            axis, ideal = ideals[self._dice.die(len(ideals)) - 1]
            return f"{self._text(ideal)} ({self._axis(axis)})"
        if element == "trinket":
            return self._pick(self._tables.trinkets())
        table = self._source(element)
        if table is None:
            return ""
        if element == "bond":
            # "Roll twice, ignoring result of 10."
            again = [r for r in table.rows if "roll twice" in r.cells[0].lower()]
            row = self._row(table)
            if row is not None and row in again:
                first = self._text(table, again)
                taken = [r for r in table.rows if r.cells[0] == first]
                second = self._text(table, [*again, *taken])
                return "; ".join(x for x in (first, second) if x)
            return row.cells[0] if row else ""
        if element in ("high", "low"):
            other = current.get("low" if element == "high" else "high", "")
            ability = other.split(":", 1)[0] if other else ""
            skip = [r for r in table.rows if ability and r.cells[0].startswith(ability)]
            return ability_text(self._text(table, skip))
        return self._text(table)

    def source_of(self, element: str) -> str:
        """The book an element is rolled from."""
        if element == "ideal":
            return "DMG"
        if element == "trinket":
            return TRINKET_SOURCE
        table = self._source(element)
        return table.source if table else ""

    # -- names -----------------------------------------------------------------------

    def name_sets(self) -> list[StyleEntry]:
        """The styles for the name choice (:meth:`Naming.entries`)."""
        return self._naming.entries()

    def default_name_set(self) -> str:
        """The style a new NPC starts from."""
        return self._default_style or self._naming.default_key(self._edition)

    def roll_name(self, name_set: str, gender: str = "any") -> str:
        """A name from ``name_set`` (see :meth:`name_sets`), or "" when it has none."""
        return self.roll_made(name_set, gender).text

    def roll_made(
        self, name_set: str, gender: str = "any", kin: Mapping[str, str] | None = None
    ) -> MadeName:
        """As :meth:`roll_name`, with the name's parts; ``kin`` holds a parent's."""
        try:
            found = self._naming.made(
                name_set, gender, dice=self._dice, kin=kin, language=self._language
            )
        except NamingError:
            found = []
        return found[0] if found else MadeName("")

    # -- the whole NPC ---------------------------------------------------------------

    def roll(
        self, name_set: str, gender: str = "any", kin: Mapping[str, str] | None = None
    ) -> GeneratedNpc:
        """A name and every element."""
        made = self.roll_made(name_set, gender, kin)
        out = GeneratedNpc(name=made.text, parts=made.parts)
        for element in self.elements():
            out.values[element] = self.roll_element(element, out.values)
            out.sources[element] = self.source_of(element)
        return out


def edition_for(source_set: SourceSet | None, tables: NpcTables) -> str:
    """ "2024" when the world rolls on the 2024 guide (treasure's rule), else "2014"."""
    return "2024" if tables_for(source_set, tables.books()) == BOOK_2024 else "2014"


__all__ = [
    "ANY_STYLE",
    "ELEMENTS",
    "GENDERS",
    "GeneratedNpc",
    "NpcGenerator",
    "ability_text",
    "edition_for",
]
