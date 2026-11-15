"""Reading the books' small tables for the rules that roll on them.

Downtime, travel, the shop and bastions roll on tables printed in the books — the
weather, a carousing result, a facility's harvest — and read prices and times out of
others. The tables stay in the user's corpus; this module finds one by its caption in
the world's books and reads its cells: a roll range ("1—5", "21+", "9 or lower",
"96–00"), an amount of coins ("1,000 gp", "100 GP"), a time ("3 days", "2 workweeks"),
and dice written into a cell (``{@dice 1d4 × 10}``), which are rolled.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from app.services.dice import Dice, DiceError
from app.services.text import strip_tags

Rows = list[list[str]]
# Finds a table by caption in the first of ``sources`` the world has: its rows.
TableFinder = Callable[[str, Sequence[str]], Rows | None]

_RANGE = re.compile(r"^\s*(\d+)\s*(?:[–—-]\s*(\d+)|(\+)|\s+or\s+(lower|higher|more|less))?\s*$")
_COINS = re.compile(r"([\d,]+)\s*(pp|gp|ep|sp|cp)\b", re.IGNORECASE)
_TIME = re.compile(r"(\d+)\s*(day|workweek|week|month)s?\b", re.IGNORECASE)
_DICE_TAG = re.compile(r"\{@dice ([^}|]+)(?:\|[^}]*)?\}")
_TIMES = {"day": 1, "workweek": 5, "week": 7, "month": 30}
_COPPER = {"pp": 1000, "gp": 100, "ep": 50, "sp": 10, "cp": 1}
_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "twenty": 20,
    "twenty-five": 25,
    "fifty": 50,
}


def rows_of(table: Any) -> Rows:
    """A corpus table's rows as text cells."""
    rows = getattr(table, "rows", None) or []
    return [[_cell(c) for c in row] for row in rows if isinstance(row, list)]


def _cell(cell: Any) -> str:
    if isinstance(cell, dict):
        roll = cell.get("roll")
        if isinstance(roll, dict):
            if "exact" in roll:
                return str(roll["exact"])
            return f"{roll.get('min', '')}–{roll.get('max', '')}"
        return str(cell.get("entry") or cell.get("text") or "")
    return str(cell)


def span(text: str) -> tuple[int, int] | None:
    """A roll range: ``"1—5"`` (1, 5), ``"21+"`` (21, ∞), ``"9 or lower"`` (−∞, 9),
    ``"96–00"`` (96, 100), ``"7"`` (7, 7); None for anything else."""
    found = _RANGE.match(strip_tags(text).replace("−", "-"))
    if found is None:
        return None
    low = int(found[1])
    if found[2] is not None:
        high = int(found[2])
        if high == 0 and found[2].startswith("0"):
            high = 100
        return (low, high)
    if found[3]:
        return (low, 10**9)
    if found[4] in ("lower", "less"):
        return (-(10**9), low)
    if found[4] in ("higher", "more"):
        return (low, 10**9)
    return (low, low)


def row_for(rows: Rows, total: int) -> list[str] | None:
    """The row whose range in the first column holds ``total``."""
    for row in rows:
        bounds = span(row[0]) if row else None
        if bounds is not None and bounds[0] <= total <= bounds[1]:
            return row
    return None


def copper(text: str) -> int:
    """The first amount of coins in ``text`` in copper ("1,000 gp" → 100000); 0 if none."""
    found = _COINS.search(strip_tags(text))
    return int(found[1].replace(",", "")) * _COPPER[found[2].lower()] if found else 0


def days(text: str) -> int:
    """A time in days ("1 day" 1, "2 workweeks" 10, "1 week" 7); 0 if none."""
    found = _TIME.search(strip_tags(text))
    return int(found[1]) * _TIMES[found[2].lower()] if found else 0


def count_word(word: str) -> int | None:
    """``"ten"`` 10, ``"4"`` 4, ``"twenty-five"`` 25; None for anything else."""
    word = word.strip().lower()
    return int(word) if word.isdigit() else _WORDS.get(word)


def rolled(text: str, dice: Dice) -> str:
    """``text`` with the dice written in it rolled ("{@dice 1d4 × 10} degrees" → "30
    degrees"), and its other tags as their display text."""

    def roll(match: re.Match[str]) -> str:
        return str(roll_amount(match[1], dice))

    return strip_tags(_DICE_TAG.sub(roll, text))


def roll_amount(expression: str, dice: Dice) -> int:
    """A dice amount as the books write it: ``1d6 × 10``, ``(1d6 + 1) × 10``, ``2d10``."""
    text = expression.replace("×", "*").replace("x", "*").replace(",", "")
    base, _, factor = text.partition("*")
    base = base.strip().strip("()").replace(" ", "")
    try:
        total = dice.roll(base).total
    except DiceError:
        return 0
    multiplier = factor.strip().replace(" ", "")
    return total * int(multiplier) if multiplier.isdigit() else total


def find_in(tables: Iterable[Any], caption: str, sources: Sequence[str]) -> Rows | None:
    """The rows of the table called ``caption`` (or "<section>; caption") from the first
    of ``sources`` that prints it."""
    wanted = caption.lower()
    found: dict[str, Rows] = {}
    for table in tables:
        name = str(getattr(table, "name", "")).lower()
        source = str(getattr(table, "source", "")).lower()
        if name == wanted or name.endswith(f"; {wanted}"):
            found.setdefault(source, rows_of(table))
    return next((found[s.lower()] for s in sources if s.lower() in found), None)


__all__ = [
    "Rows",
    "TableFinder",
    "copper",
    "count_word",
    "days",
    "find_in",
    "roll_amount",
    "rolled",
    "row_for",
    "rows_of",
    "span",
]
