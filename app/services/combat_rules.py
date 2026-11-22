"""The 2014 guide's optional combat rules, switched on per world.

A world may tick these, and the tracker applies them (documentation/COMBAT.md §3b). A
fight keeps the rules it started with (``Combat.rules``), so changing the world mid-fight
leaves a running fight alone.

* **Massive damage**: damage from one source of half the target's hit point maximum or
  more, which it survives, calls a DC 15 Constitution save. A failure rolls the guide's
  *System Shock* table, and what the row says in numbers is carried out: a drop to 0 hit
  points (stable or not), a condition until the end of the creature's next turn.
* **Lingering injuries**: when a character takes a critical hit or drops to 0 hit points,
  the tracker offers a roll on the guide's *Lingering Injuries* table. The injury is kept
  on the character as an affliction until the gamemaster ends it.
* **Morale**: a group that has lost half its number, or a lone creature at half its hit
  points or fewer, is reminded once, at its turn, to make a DC 10 Wisdom save or flee.
  The flight is the gamemaster's.
* **Mobs**: many creatures of one kind attacking one target don't roll each attack. The
  guide's *Mob Attacks* table says how many attackers it takes for one hit, from the d20
  roll they need against the target's AC.
* **Side initiative**: each side rolls one d20, and every member of the higher side acts
  before the other. Ties are rolled again.

The tables stay in the user's corpus and are read by caption (app/services/book_tables.py).
A rule that needs a table the world's books lack is not offered.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.services.book_tables import Rows, row_for
from app.services.dice import Dice
from app.services.text import strip_tags

GUIDE_2014 = "DMG"
RULES = ("massive_damage", "injuries", "morale", "mobs", "side_initiative")
# The table each rule rolls on; the others need none.
TABLES = {
    "massive_damage": "System Shock",
    "injuries": "Lingering Injuries",
    "mobs": "Mob Attacks",
}
SHOCK_DC = 15
MORALE_DC = 10
FRIEND, FOE = "friend", "foe"

# Finds a table by caption in a book the world declares: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]

_CONDITION = re.compile(r"\{@condition ([^}|]+)")
_BOLD = re.compile(r"^\s*\{@b(?:old)? ([^}]+)\}\s*")


def available(find: Finder) -> frozenset[str]:
    """The rules this world's books allow: those without a table, and those whose table
    is there."""
    return frozenset(r for r in RULES if r not in TABLES or find(TABLES[r], [GUIDE_2014]))


def kept(chosen: Sequence[str]) -> list[str]:
    """Known rules from a form or a file, in their own order."""
    wanted = set(chosen)
    return [r for r in RULES if r in wanted]


def massive(amount: int, hp_max: int | None) -> bool:
    """Whether ``amount`` from one source is massive damage to a creature of ``hp_max``."""
    return bool(hp_max) and amount * 2 >= (hp_max or 0)


@dataclass(frozen=True)
class Shock:
    """A *System Shock* row as rolled: its text, and what it does in numbers."""

    roll: int
    text: str
    drop: bool = False
    stable: bool = False
    conditions: tuple[str, ...] = ()


def system_shock(rows: Rows, dice: Dice) -> Shock:
    """Roll the table: a d10 by its rows' ranges."""
    total = dice.die(10)
    row = row_for(rows, total)
    raw = row[-1] if row else ""
    text = strip_tags(raw)
    low = text.lower()
    return Shock(
        total,
        text,
        drop="0 hit points" in low,
        stable="stable" in low,
        conditions=tuple(dict.fromkeys(c.strip().lower() for c in _CONDITION.findall(raw))),
    )


@dataclass(frozen=True)
class Injury:
    """A *Lingering Injuries* row as rolled: its name (the row's bold lead) and the rest."""

    roll: int
    name: str
    note: str


def lingering_injury(rows: Rows, dice: Dice) -> Injury:
    """Roll the table: a d20 by its rows' ranges."""
    total = dice.die(20)
    row = row_for(rows, total)
    raw = row[-1] if row else ""
    lead = _BOLD.match(raw)
    if lead is not None:
        name = strip_tags(lead[1]).strip().rstrip(".")
        note = strip_tags(raw[lead.end() :]).strip()
    else:
        text = strip_tags(raw).strip()
        name, _, note = text.partition(". ")
    return Injury(total, name[:200] or str(total), note[:2000])


def mob_needs(rows: Rows, ac: int, bonus: int) -> int | None:
    """How many attackers make one hit against ``ac`` with ``bonus`` to hit: the row of
    the d20 roll they need. None when not even a 20 would do."""
    needed = max(1, ac - bonus)
    if needed > 20:
        return None
    row = row_for(rows, needed)
    if row is None or len(row) < 2:
        return None
    found = re.search(r"\d+", strip_tags(row[1]))
    return int(found[0]) if found else None


def roll_sides(dice: Dice) -> dict[str, int]:
    """One d20 for each side, rolled again until they differ."""
    while True:
        friend, foe = dice.die(20), dice.die(20)
        if friend != foe:
            return {FRIEND: friend, FOE: foe}


__all__ = [
    "FOE",
    "FRIEND",
    "GUIDE_2014",
    "MORALE_DC",
    "RULES",
    "SHOCK_DC",
    "TABLES",
    "Finder",
    "Injury",
    "Shock",
    "available",
    "kept",
    "lingering_injury",
    "massive",
    "mob_needs",
    "roll_sides",
    "system_shock",
]
