"""Madness and mental stress, rolled for a character and carried as an affliction.

Both guides know three grades of it. Short-term lasts 1d10 minutes, long-term 1d10 × 10
hours, and indefinite lasts until cured. These durations are rules of the System
Reference Documents (SRD 5.1 "Madness", SRD 5.2.1 "Mental Stress Effects").

**What it does.** A world whose books include the 2014 guide rolls the effect on that
guide's d100 tables (*Short-Term Madness*, *Long-Term Madness*, *Indefinite Madness*).
They stay in the user's corpus and are found by caption (app/services/book_tables.py). The
row's text is kept as the affliction's note, and the conditions it names are put on. A
world without that guide takes the 2024 guide's prolonged effects instead:

* short-term: Frightened, Incapacitated or Stunned (one of them, rolled);
* long-term: disadvantage on some or all ability checks;
* indefinite: the same, until cured.

Disadvantage is the table's to apply, so it is named in the note only.

Resisting it is a saving throw the gamemaster sets (usually Wisdom). Mental stress may
also deal psychic damage, half on a success where the gamemaster says so. Both are
carried out by ``AfflictionService.madden``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.services.book_tables import Rows, row_for, span
from app.services.dice import Dice
from app.services.text import strip_tags
from app.services.traps import _CONDITION

GUIDE_2014 = "DMG"
LEVELS = ("short", "long", "indefinite")
TABLES = {
    "short": "Short-Term Madness",
    "long": "Long-Term Madness",
    "indefinite": "Indefinite Madness",
}
# How long each grade lasts: dice of units, and the unit in minutes (SRD 5.1, 5.2.1).
LASTS: dict[str, tuple[str, int]] = {"short": ("1d10", 1), "long": ("1d10", 600)}
# The 2024 guide's short-term effects: one of these conditions.
SHORT_2024 = ("frightened", "incapacitated", "stunned")

# Finds a table by caption in a book the world declares: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]


@dataclass(frozen=True)
class MadEffect:
    """One character's madness as rolled: the table's text ("" for the 2024 effects,
    whose words are the reader's), the conditions it puts on, how long it lasts, and the
    book it was rolled in ("" for the 2024 effects)."""

    level: str
    text: str = ""
    conditions: tuple[str, ...] = ()
    lasts: str = ""
    unit: int = 1
    source: str = ""


def rolls_2014(find: Finder) -> bool:
    """Whether the world rolls on the 2014 guide's madness tables (it has them)."""
    return bool(find(TABLES["short"], [GUIDE_2014]))


def roll_madness(level: str, find: Finder, dice: Dice) -> MadEffect:
    """One grade of madness rolled, on the 2014 guide's table where the world has it,
    else as the 2024 guide's prolonged effect."""
    lasts, unit = LASTS.get(level, ("", 1))
    rows = find(TABLES[level], [GUIDE_2014]) if level in TABLES else None
    if rows:
        row = _row(rows, dice)
        text = row[-1] if row else ""
        conditions = tuple(dict.fromkeys(c.strip().lower() for c in _CONDITION.findall(text)))
        return MadEffect(level, strip_tags(text), conditions, lasts, unit, GUIDE_2014)
    if level == "short":
        condition = SHORT_2024[dice.die(len(SHORT_2024)) - 1]
        return MadEffect(level, "", (condition,), lasts, unit)
    return MadEffect(level, "", (), lasts, unit)


def _row(rows: Rows, dice: Dice) -> list[str] | None:
    ends = [end for r in rows if r and (found := span(r[0])) for end in found]
    die = max((e for e in ends if 0 < e < 10_000), default=len(rows))
    total = dice.die(die)
    return row_for(rows, total) or rows[(total - 1) % len(rows)]


__all__ = ["LEVELS", "SHORT_2024", "MadEffect", "roll_madness", "rolls_2014"]
