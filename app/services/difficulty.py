"""How hard is this fight? Encounter difficulty under 2014 and 2024 rules.

The two editions do different arithmetic, and both are implemented rather than one
picked (documentation/CHARGEN.md trap #1 makes the same point for characters):

* **2014** (DMG p. 82) — sum the monsters' XP, multiply by a factor for how many there
  are (more monsters act more often), shift that factor one step for a party smaller
  than three or larger than five, and compare against the party's summed
  Easy / Medium / Hard / Deadly thresholds.
* **2024** (XDMG p. 114) — sum the monsters' XP, no multiplier, and compare against the
  party's summed Low / Moderate / High budgets.

Which applies follows from the world's books, as edition always does here
(documentation/TECHSTACK.md §6c): only 2014 books → 2014; 2024 books in play → 2024, the
same choice supersession makes for a table that owns both. An encounter may override it.

The numbers are read out of the books' own tables (see :mod:`app.store.rulestables`).
Those that are also in the System Reference Documents ship with Paravent in
``data/rules/encounterdifficulty.json``: experience by challenge rating and the 2024
budgets. The 2014 guide's thresholds, multipliers and adventuring day are in no SRD and
are read from the user's corpus; without that book there is no 2014 rating, and the
encounter says so. Only the party-size shift is prose in the DMG, and so is coded here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from app.models.gamestate import EDITION_CLASSIC, EDITION_ONE, SourceSet
from app.store import rulestables

# The DMG's multiplier ladder, including the ×0.5 and ×5 steps that exist only for the
# party-size shift ("use the next lowest/highest multiplier"), which the table omits.
MULTIPLIER_STEPS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)

CLASSIC_BANDS = ("easy", "medium", "hard", "deadly")
ONE_BANDS = ("low", "moderate", "high")
MAX_LEVEL = 20


@dataclass(frozen=True)
class Band:
    """One threshold (2014) or budget (2024), summed over the party."""

    key: str
    xp: int


@dataclass(frozen=True)
class Rating:
    """The result: what the fight is worth, against what the party can take.

    ``level`` under 2014 is the highest threshold the fight reaches, ``"trivial"`` below
    the first. Under 2024 it is the lowest budget the fight fits within — the XDMG spends
    "as much of your XP budget as you can without going over" — and ``"beyond"`` above
    High, where its budgets stop. ``bands`` is empty when there is no party to rate
    against.
    """

    rules: str
    base_xp: int
    multiplier: float
    adjusted_xp: int
    bands: tuple[Band, ...]
    level: str | None
    monster_count: int
    party_size: int

    @property
    def rated(self) -> bool:
        """True when there is both a party and at least one monster."""
        return bool(self.bands) and self.monster_count > 0

    def share(self, band: Band) -> float:
        """Where a band sits on a 0–1 scale whose end is 125% of the top band."""
        top = self.bands[-1].xp * 1.25 if self.bands else 1
        return min(1.0, band.xp / top) if top else 0.0

    @property
    def fill(self) -> float:
        """How far along that scale the fight's XP reaches."""
        top = self.bands[-1].xp * 1.25 if self.bands else 0
        return min(1.0, self.adjusted_xp / top) if top else 0.0


def rules_for(source_set: SourceSet | None, override: str | None = None) -> str:
    """The edition whose arithmetic applies: the override, else what the books imply."""
    if override in (EDITION_CLASSIC, EDITION_ONE):
        return override
    editions = (source_set or SourceSet()).editions
    return EDITION_CLASSIC if editions == {EDITION_CLASSIC} else EDITION_ONE


class DifficultyRules:
    """The tables, and the arithmetic over them."""

    def __init__(self, path: Path = rulestables.RULES_FILE, reference: Path | None = None) -> None:
        """Read the rules file lazily from ``path``, and the 2014 guide's tables from the
        corpus at ``reference`` (without one there is no 2014 rating)."""
        self._path = path
        self._reference = reference
        self._classic_at: tuple[int, int] | None = None
        self._classic_tables: dict[str, Any] | None = None

    @cached_property
    def _data(self) -> dict[str, Any]:
        return rulestables.load(self._path)

    @property
    def _classic(self) -> dict[str, Any]:
        """The 2014 tables of the user's corpus, read again when its book changes."""
        stamp = rulestables.classic_stamp(self._reference)
        if self._classic_tables is None or stamp != self._classic_at:
            self._classic_tables = rulestables.classic(self._reference)
            self._classic_at = stamp
        return self._classic_tables

    def has_tables(self, rules: str) -> bool:
        """Whether the tables of ``rules`` are there: the 2024 budgets always, the 2014
        thresholds when the corpus holds the 2014 *Dungeon Master's Guide*."""
        return rules != EDITION_CLASSIC or bool(self._classic["thresholds"])

    def xp_for_cr(self, cr: str) -> int | None:
        """Experience for one creature of challenge rating ``cr`` (``"1/4"``, ``"5"``)."""
        value = self._data["xp_by_cr"].get(cr)
        return int(value) if value is not None else None

    def day_budget(self, rules: str, levels: Sequence[int]) -> int | None:
        """The adjusted XP a party can face between long rests (2014's "Adventuring Day
        XP", summed over its members); None under 2024 rules, which set no such budget,
        and when the corpus lacks the 2014 guide's table."""
        table = self._classic["day"]
        if rules != EDITION_CLASSIC or not levels or not table:
            return None
        return sum(int(table.get(str(max(1, min(MAX_LEVEL, level))), 0)) for level in levels)

    def multiplier(self, monsters: int, party_size: int) -> float:
        """The 2014 encounter multiplier for ``monsters`` creatures against this party."""
        rows = self._classic["multipliers"]
        if monsters <= 0 or not rows:
            return 1.0
        base = 1.0
        for row in rows:
            high = row["max"]
            if monsters >= row["min"] and (high is None or monsters <= high):
                base = float(row["multiplier"])
                break
        step = MULTIPLIER_STEPS.index(base)
        if party_size < 3:
            step += 1
        elif party_size >= 6:
            step -= 1
        return MULTIPLIER_STEPS[max(0, min(step, len(MULTIPLIER_STEPS) - 1))]

    def bands(self, rules: str, levels: Sequence[int]) -> tuple[Band, ...]:
        """The party's thresholds (2014) or budgets (2024), each summed over its members;
        none when the corpus lacks the 2014 guide's table (:meth:`has_tables`)."""
        keys: tuple[str, ...]
        if rules == EDITION_CLASSIC:
            table, keys = self._classic["thresholds"], CLASSIC_BANDS
        else:
            table, keys = self._data["one"]["budgets"], ONE_BANDS
        if not levels or not table:
            return ()
        totals = dict.fromkeys(keys, 0)
        for level in levels:
            row = table[str(max(1, min(MAX_LEVEL, level)))]
            for key in keys:
                totals[key] += int(row[key])
        return tuple(Band(key, totals[key]) for key in keys)

    def rate(
        self, rules: str, levels: Sequence[int], monster_xp: Sequence[tuple[int, int]]
    ) -> Rating:
        """Rate a fight: ``monster_xp`` is ``(xp each, how many)`` per kind of monster."""
        count = sum(n for _xp, n in monster_xp)
        base = sum(xp * n for xp, n in monster_xp)
        factor = self.multiplier(count, len(levels)) if rules == EDITION_CLASSIC else 1.0
        adjusted = round(base * factor)
        bands = self.bands(rules, levels)
        level: str | None = None
        if bands and count and rules == EDITION_ONE:
            level = next((b.key for b in bands if adjusted <= b.xp), "beyond")
        elif bands and count:
            level = "trivial"
            for band in bands:
                if adjusted >= band.xp:
                    level = band.key
        return Rating(
            rules=rules,
            base_xp=base,
            multiplier=factor,
            adjusted_xp=adjusted,
            bands=bands,
            level=level,
            monster_count=count,
            party_size=len(levels),
        )


__all__ = [
    "CLASSIC_BANDS",
    "MULTIPLIER_STEPS",
    "ONE_BANDS",
    "Band",
    "DifficultyRules",
    "Rating",
    "rules_for",
]
