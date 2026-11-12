"""A fight built to a difficulty (the encounter builder's "Build a fight").

The corpus's encounter shapes (``encounterbuilder.json``: "Boss", "Boss with Minions",
"Duo", "Trio", "Horde") say how a fight is put together: groups of creatures, how many
in each (``players`` is the party's size) and what share of the XP budget each takes.
The generator fills a shape from a pool of creatures — the world's books, a habitat if
one is chosen — so that the fight rates at the difficulty asked for:

1. a goal the builder rates at the chosen band: under 2014 between its threshold and the
   next (with the multiplier for the number of creatures), under 2024 above the budget
   below it and within its own;
2. each group's share of it, divided by its count, is the XP one of its creatures should
   be worth; the creature nearest below it is taken (one of the few nearest, at random,
   and a kind not used yet in the fight);
3. the result is rated by the builder's own arithmetic, and of several tries the one at
   the chosen band and nearest the goal is kept.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from app.models.gamestate import EDITION_CLASSIC
from app.services.difficulty import CLASSIC_BANDS, ONE_BANDS, DifficultyRules, Rating
from app.store.encounter_tables import EncounterShape, ShapeGroup

TRIES = 60
NEAREST = 3  # a creature is chosen from this many nearest to its XP
MAX_GROUP = 30
_FORMULA = re.compile(r"^[\d\s+*players]+$")


@dataclass(frozen=True)
class Candidate:
    """A creature the generator may use: its reference and what it is worth."""

    name: str
    source: str
    xp: int


@dataclass(frozen=True)
class Generated:
    """A fight the generator built: its creatures as ``(name, source, count)``, the shape
    it followed, and how it rates."""

    shape: str
    monsters: tuple[tuple[str, str, int], ...]
    rating: Rating


def bands_of(rules: str) -> tuple[str, ...]:
    """The difficulties one can ask for under ``rules``."""
    return CLASSIC_BANDS if rules == EDITION_CLASSIC else ONE_BANDS


def _formula(text: str, players: int) -> int:
    """``"players * 2"``, ``"players + 3"``, ``"2"``: sums of products, nothing else."""
    if not _FORMULA.match(text):
        return 1
    total = 0
    for term in text.split("+"):
        product = 1
        for factor in term.split("*"):
            factor = factor.strip()
            product *= players if factor == "players" else int(factor) if factor.isdigit() else 1
        total += product
    return total


def _count(spec: dict[str, Any], players: int, rng: random.Random) -> int:
    if "exact" in spec:
        low = high = int(spec["exact"])
    elif "formulaMin" in spec or "formulaMax" in spec:
        low = _formula(str(spec.get("formulaMin", "1")), players)
        high = _formula(str(spec.get("formulaMax", spec.get("formulaMin", "1"))), players)
    else:
        low, high = int(spec.get("min", 1)), int(spec.get("max", spec.get("min", 1)))
    low, high = max(1, low), max(1, high)
    return min(MAX_GROUP, rng.randint(min(low, high), max(low, high)))


def _number(value: Any) -> float:
    if isinstance(value, dict) and value.get("denom"):
        return float(Fraction(int(value.get("num", 1)), int(value["denom"])))
    return float(value)


def _ratio(spec: dict[str, Any] | None, rng: random.Random) -> float | None:
    if spec is None:
        return None
    if "exact" in spec:
        return _number(spec["exact"])
    return rng.uniform(_number(spec.get("min", 0)), _number(spec.get("max", spec.get("min", 0))))


class EncounterGenerator:
    """Builds fights to a difficulty from the shapes and a pool of creatures."""

    def __init__(self, rules: DifficultyRules, rng: random.Random | None = None) -> None:
        """Bind to the difficulty arithmetic, and a random source (for tests)."""
        self._rules = rules
        self._rng = rng or random.Random()

    def generate(
        self,
        pool: Sequence[Candidate],
        levels: Sequence[int],
        rules: str,
        band: str,
        shapes: Sequence[EncounterShape],
    ) -> Generated | None:
        """The best of several tries at ``band`` for a party of ``levels``, following one
        of ``shapes`` (chosen at random each try); None when there is no party, no pool,
        no shape or no such band."""
        pool = sorted((c for c in pool if c.xp > 0), key=lambda c: c.xp)
        bands = self._rules.bands(rules, levels)
        keys = [b.key for b in bands]
        if not pool or not levels or not shapes or band not in keys:
            return None
        index = keys.index(band)
        if rules == EDITION_CLASSIC:
            low = bands[index].xp
            high = bands[index + 1].xp if index + 1 < len(bands) else round(low * 1.25)
        else:  # within its budget, above the one below (half the lowest for Low)
            high = bands[index].xp + 1
            low = bands[index - 1].xp + 1 if index else bands[0].xp // 2
        best: tuple[float, Generated] | None = None
        for _ in range(TRIES):
            shape = self._rng.choice(list(shapes))
            goal = self._rng.uniform(low, max(low, high - 1))
            monsters = self._fill(self._rng.choice(shape.templates), pool, len(levels), rules, goal)
            if not monsters:
                continue
            rating = self._rules.rate(
                rules, levels, [(xp, count) for _n, _s, xp, count in monsters]
            )
            miss = abs(rating.adjusted_xp - goal) / max(1.0, goal)
            score = miss + (0 if rating.level == band else 10)
            made = Generated(shape.name, tuple((n, s, c) for n, s, _xp, c in monsters), rating)
            if best is None or score < best[0]:
                best = (score, made)
            if rating.level == band and miss < 0.1:
                break
        return best[1] if best else None

    def _fill(
        self,
        groups: Sequence[ShapeGroup],
        pool: Sequence[Candidate],
        players: int,
        rules: str,
        goal: float,
    ) -> list[tuple[str, str, int, int]]:
        counts = [_count(g.count, players, self._rng) for g in groups]
        factor = self._rules.multiplier(sum(counts), players) if rules == EDITION_CLASSIC else 1.0
        budget = goal / factor
        ratios = [_ratio(g.ratio, self._rng) for g in groups]
        given = sum(r for r in ratios if r is not None)
        rest = [i for i, r in enumerate(ratios) if r is None]
        left = max(0.0, 1.0 - given) / len(rest) if rest else 0.0
        used: set[tuple[str, str]] = set()
        out: list[tuple[str, str, int, int]] = []
        for count, ratio in zip(counts, ratios, strict=True):
            each = budget * (ratio if ratio is not None else left) / count
            chosen = self._pick(pool, each, used)
            if chosen is None:
                continue
            used.add((chosen.name, chosen.source))
            out.append((chosen.name, chosen.source, chosen.xp, count))
        return out

    def _pick(
        self, pool: Sequence[Candidate], xp: float, used: set[tuple[str, str]]
    ) -> Candidate | None:
        """One of the creatures nearest below ``xp`` (the weakest if none is), a kind not
        yet in the fight where there is one."""
        fresh = [c for c in pool if (c.name, c.source) not in used] or list(pool)
        below = [c for c in fresh if c.xp <= xp]
        if not below:
            weakest = fresh[0].xp
            return self._rng.choice([c for c in fresh if c.xp == weakest])
        top = below[-1].xp
        # The few nearest values below xp, each kind at those values a candidate.
        values = sorted({c.xp for c in below}, reverse=True)[:NEAREST]
        near = [c for c in below if c.xp in values] if values else [below[-1]]
        near.sort(key=lambda c: top - c.xp)
        return self._rng.choice(near)


def pool_of(monsters: Any) -> tuple[list[Candidate], list[str]]:
    """The creatures a generated fight may use, from a monster query already narrowed
    to the world's books (and a habitat, if chosen): those with a challenge rating worth
    XP, named people (``isNpc``) left out. And the habitats found among them."""
    pool: list[Candidate] = []
    habitats: set[str] = set()
    for m in monsters:
        if m.npc or m.cr is None or not m.cr.xp:
            continue
        pool.append(Candidate(m.name, m.source, int(m.cr.xp)))
        habitats.update(m.environments)
    return pool, sorted(habitats)


__all__ = ["Candidate", "EncounterGenerator", "Generated", "bands_of", "pool_of"]
