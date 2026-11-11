"""Dice: parsing ``2d6+3``-style expressions and rolling them, with advantage.

Rolls happen on the server so every screen showing a character sees the same result
(the character's log is shared). The random source is injectable, so tests can fix it.

Advantage is three states, never a boolean (documentation/DESIGN.md §4): disadvantage,
normal, advantage. Both d20s are reported, so the log shows which one was taken.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from enum import IntEnum

_TERM = re.compile(r"([+-]?)\s*(?:(\d*)d(\d+)|(\d+))", re.IGNORECASE)
_EXPRESSION = re.compile(r"^\s*[+-]?\s*(\d*d\d+|\d+)(\s*[+-]\s*(\d*d\d+|\d+))*\s*$", re.I)

MAX_DICE = 100
MAX_FACES = 1000


class Advantage(IntEnum):
    """The three states of a d20 roll."""

    DISADVANTAGE = -1
    NORMAL = 0
    ADVANTAGE = 1

    @classmethod
    def parse(cls, value: str | int | None) -> Advantage:
        """Lenient parsing from a form value; anything unknown is normal."""
        try:
            number = int(value) if value not in (None, "") else 0
        except (TypeError, ValueError):
            return cls.NORMAL
        return cls.ADVANTAGE if number > 0 else cls.DISADVANTAGE if number < 0 else cls.NORMAL


@dataclass(frozen=True)
class Roll:
    """The outcome of one expression."""

    expression: str
    total: int
    rolls: tuple[int, ...] = ()
    detail: str = ""
    natural: int | None = None  # the kept d20, for crit/fumble marking
    kept_from: tuple[int, ...] = field(default=())
    # The dice as data, for the log: (faces, signed results) per term, and the flat
    # number added — ``2d6+1d4-1`` is ((6, (3, 5)), (4, (2,))) and -1.
    groups: tuple[tuple[int, tuple[int, ...]], ...] = ()
    modifier: int = 0

    @property
    def kind(self) -> str:
        """``crit`` on a natural 20, ``fumble`` on a natural 1."""
        if self.natural == 20:
            return "crit"
        if self.natural == 1:
            return "fumble"
        return ""


class DiceError(ValueError):
    """An expression that is not a dice expression, or an absurd one."""


def is_expression(text: str) -> bool:
    """True when ``text`` is a dice expression such as ``2d6+3``."""
    return bool(_EXPRESSION.match(text or ""))


class Dice:
    """Rolls dice from an injectable random source."""

    def __init__(self, rng: random.Random | None = None) -> None:
        """``rng`` defaults to a system-seeded generator."""
        self._rng = rng or random.SystemRandom()

    def die(self, faces: int) -> int:
        """One die."""
        return self._rng.randint(1, faces)

    def roll(self, expression: str) -> Roll:
        """Roll an expression like ``8d6``, ``1d8+3`` or ``2d6 + 1d4 - 1``."""
        if not is_expression(expression):
            raise DiceError(f"not a dice expression: {expression!r}")
        total, rolls, parts = 0, [], []
        groups: list[tuple[int, tuple[int, ...]]] = []
        modifier = 0
        for sign, count, faces, flat in _TERM.findall(expression):
            factor = -1 if sign == "-" else 1
            if faces:
                n, f = int(count or 1), int(faces)
                if not (1 <= n <= MAX_DICE and 1 <= f <= MAX_FACES):
                    raise DiceError(f"too many dice: {expression!r}")
                values = [self.die(f) for _ in range(n)]
                rolls.extend(values)
                groups.append((f, tuple(factor * v for v in values)))
                total += factor * sum(values)
                parts.append(f"{'-' if factor < 0 else ''}{n}d{f}[{','.join(map(str, values))}]")
            else:
                total += factor * int(flat)
                modifier += factor * int(flat)
                parts.append(f"{'-' if factor < 0 else '+'}{flat}")
        detail = " ".join(parts).replace(" +", " + ").replace(" -", " - ")
        return Roll(
            expression=expression.replace(" ", ""),
            total=total,
            rolls=tuple(rolls),
            detail=detail,
            groups=tuple(groups),
            modifier=modifier,
        )

    def d20(self, bonus: int, advantage: Advantage = Advantage.NORMAL) -> Roll:
        """A d20 test with a bonus, honouring advantage."""
        first = self.die(20)
        pair: tuple[int, ...] = (first,)
        kept = first
        if advantage is not Advantage.NORMAL:
            second = self.die(20)
            pair = (first, second)
            kept = max(pair) if advantage is Advantage.ADVANTAGE else min(pair)
        shown = f"d20[{','.join(map(str, pair))}]" if len(pair) > 1 else f"d20 {kept}"
        detail = f"{shown} {bonus:+d}"
        return Roll(
            expression=f"1d20{bonus:+d}",
            total=kept + bonus,
            rolls=pair,
            detail=detail,
            natural=kept,
            kept_from=pair,
            groups=((20, pair),),
            modifier=bonus,
        )


__all__ = ["Advantage", "Dice", "DiceError", "Roll", "is_expression"]
