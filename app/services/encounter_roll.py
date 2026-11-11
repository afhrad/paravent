"""Rolling on a random-encounter table (TECHSTACK.md §6b, phase 2).

The party's level picks the environment's table, its dice pick a row, and the row's
prose is read for creatures:

* ``" or "`` outside tags separates alternatives — one is chosen at random
  ("1 badger or 1d4 poisonous snakes");
* every ``{@creature …}`` is a line of the encounter; its count is the number or the
  ``{@dice …}`` written before it, rolled ("{@dice 2d4} woodcutters
  ({@creature commoner||commoners})" → 2d4 commoners), else 1;
* creatures resolve in the world's books. A row without creatures — a traveller, a
  wrecked cart — is an event: it becomes the encounter's note, as does anything that
  did not resolve.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.services.dice import Dice, DiceError
from app.store.encounter_tables import Environment
from app.store.reference import parse_reference

_CREATURE = re.compile(r"\{@creature ([^}]+)\}")
_COUNT = re.compile(r"\{@dice ([^}]+)\}|\b(\d+)\b")
_TAG = re.compile(r"\{@\w+ ([^}]*)\}")

# (name, source) of a creature in the world's books, from a name and an optional book.
Resolve = Callable[[str, str | None], tuple[str, str] | None]


@dataclass
class RolledEncounter:
    """What a table roll produced."""

    environment: str
    source: str
    roll: int
    text: str  # the row, as plain prose
    monsters: list[tuple[str, str, int]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def plain(text: str) -> str:
    """A row without its tags: each tag's display text, or its name."""

    def shown(match: re.Match[str]) -> str:
        parts = match[1].split("|")
        return parts[2] if len(parts) > 2 and parts[2] else parts[0]

    return _TAG.sub(shown, text)


def alternatives(text: str) -> list[str]:
    """The row split at " or " outside tags."""
    out, depth, start, i = [], 0, 0, 0
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(" or ", i):
            out.append(text[start:i])
            start = i + 4
            i += 3
        i += 1
    out.append(text[start:])
    return [part.strip() for part in out if part.strip()]


class EncounterRoller:
    """Rolls an environment's table and reads the row for creatures."""

    def __init__(self, resolve: Resolve, dice: Dice | None = None) -> None:
        """Bind to a creature resolver for the world's books, and the dice."""
        self._resolve = resolve
        self._dice = dice or Dice()

    def _roll(self, expression: str) -> int:
        normalized = re.sub(r"(?<!\d)d(\d+)", r"1d\1", expression.replace(" ", ""))
        try:
            return self._dice.roll(normalized).total
        except DiceError:
            return 1

    def roll(self, environment: Environment, level: int) -> RolledEncounter | None:
        """Roll the table for ``level``; None when the environment has none for it."""
        table = environment.table_for(level)
        if table is None:
            return None
        rolled = self._roll(table.dice)
        row = next((r for r in table.rows if r.get("min", 0) <= rolled <= r.get("max", 0)), None)
        text = str((row or {}).get("result", ""))
        out = RolledEncounter(environment.name, environment.source, rolled, plain(text))
        options = alternatives(text)
        if not options:
            return out
        chosen = options[self._dice.die(len(options)) - 1] if len(options) > 1 else options[0]
        previous = 0
        for tag in _CREATURE.finditer(chosen):
            before = chosen[previous : tag.start()]
            previous = tag.end()
            counts = list(_COUNT.finditer(before))
            count = 1
            if counts:
                last = counts[-1]
                count = self._roll(last[1]) if last[1] else int(last[2])
            name, source, _display = parse_reference(tag[1], "creature")
            found = self._resolve(name, source)
            if found is None:
                out.unresolved.append(name)
                continue
            out.monsters.append((found[0], found[1], max(1, min(99, count))))
        return out


__all__ = ["EncounterRoller", "RolledEncounter", "alternatives", "plain"]
