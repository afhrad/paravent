"""The ability-score methods' rules (documentation/CHARGEN.md G1), and multiclassing's.

The standard array, the point-buy costs and budget, and the 4d6 roll exist nowhere in
the corpus as data; ``data/rules/abilityscoregen.json`` holds them with their provenance.
The multiclassing minimums do exist, in two shapes: see :func:`multiclass_requirement`.
Which class features grant Expertise is prose upstream too; ``expertise.json`` holds it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.base import ABILITIES
from app.models.pc import AbilityMethod, PlayerCharacter
from app.store.jsonfile import read_json

RULES_FILE = Path(__file__).resolve().parents[3] / "data" / "rules" / "abilityscoregen.json"
EXPERTISE_FILE = RULES_FILE.parent / "expertise.json"
FEAT_CHOICES_FILE = RULES_FILE.parent / "feat_choices.json"


@dataclass(frozen=True)
class GoldRoll:
    """A 2014 class's starting wealth: ``5d4 × 10`` gold pieces."""

    count: int
    faces: int
    times: int


def starting_gold(cls: Mapping[str, Any]) -> GoldRoll | None:
    """The class's ``goldAlternative``, or None: the 2024 classes print gold as a package."""
    raw = cls.get("startingEquipment")
    text = raw.get("goldAlternative") if isinstance(raw, Mapping) else None
    found = re.search(r"(\d+)d(\d+)(?:\s*×\s*(\d+))?", str(text or ""))
    if found is None:
        return None
    return GoldRoll(int(found[1]), int(found[2]), int(found[3] or 1))


@lru_cache(maxsize=1)
def methods() -> dict[str, dict[str, Any]]:
    """The four methods by id, as the rules file states them."""
    data = read_json(RULES_FILE, {}) or {}
    return {str(m["id"]): m for m in data.get("methods", [])}


def standard_array() -> list[int]:
    """15, 14, 13, 12, 10, 8."""
    return [int(v) for v in methods()["standard"]["values"]]


def point_cost(scores: dict[str, int]) -> int | None:
    """The points ``scores`` cost, or None when a score is outside 8–15."""
    rule = methods()["pointbuy"]
    cost = rule["cost"]
    total = 0
    for value in scores.values():
        if str(value) not in cost:
            return None
        total += int(cost[str(value)])
    return total


def point_budget() -> int:
    """27."""
    return int(methods()["pointbuy"]["budget"])


def ability_problem(pc: PlayerCharacter, method: AbilityMethod | None = None) -> str | None:
    """Why the base scores do not fit the method, as a translation key; or None.

    ``method`` is the wizard's answer, before it is written to the character; without
    it the character's own ``ability_method`` is judged.
    """
    scores = {a: pc.abilities.get(a) for a in ABILITIES}
    if any(v is None for v in scores.values()):
        return "chargen.problem.scores_missing"
    values = {a: int(v) for a, v in scores.items() if v is not None}
    method = method or pc.ability_method
    if method is AbilityMethod.STANDARD:
        if sorted(values.values()) != sorted(standard_array()):
            return "chargen.problem.standard"
    elif method is AbilityMethod.POINTBUY:
        cost = point_cost(values)
        if cost is None or cost > point_budget():
            return "chargen.problem.pointbuy"
    elif method is AbilityMethod.ROLL:
        if sorted(values.values()) != sorted(pc.ability_rolls) or len(pc.ability_rolls) != 6:
            return "chargen.problem.rolled"
    elif method is None:
        return "chargen.problem.method"
    return None


# -- multiclassing -----------------------------------------------------------------------

# The 2024 rule: 13 in the primary ability of every class involved.
MULTICLASS_MINIMUM = 13

# Alternatives, any of which is enough; each needs all of its abilities at its score.
Requirement = tuple[dict[str, int], ...]


def multiclass_requirement(cls: Mapping[str, Any]) -> Requirement:
    """The minimum scores to take or leave ``cls`` by multiclassing; ``()`` is none.

    A 2014 class states them in ``multiclassing.requirements``: plain keys all apply,
    and each ``or`` entry is satisfied by any one of its keys (the Fighter's STR 13 or
    DEX 13). A 2024 class states none there; its rule is 13 in the primary ability,
    read from ``primaryAbility`` — alternatives, each needing all its abilities (the
    Monk's DEX and WIS, the Fighter's STR or DEX).
    """
    stated = (cls.get("multiclassing") or {}).get("requirements")
    if isinstance(stated, Mapping):
        every = {k: int(v) for k, v in stated.items() if k in ABILITIES and isinstance(v, int)}
        options: list[dict[str, int]] = [every]
        for group in stated.get("or") or []:
            if not isinstance(group, Mapping):
                continue
            either = [(k, int(v)) for k, v in group.items() if k in ABILITIES]
            options = [{**base, k: v} for base in options for k, v in either] or options
        return tuple(o for o in options if o)
    primary = cls.get("primaryAbility")
    if isinstance(primary, list):
        return tuple(
            {k: MULTICLASS_MINIMUM for k, v in entry.items() if k in ABILITIES and v}
            for entry in primary
            if isinstance(entry, Mapping)
        )
    return ()


def meets(requirement: Requirement, scores: Mapping[str, int]) -> bool:
    """True when the scores satisfy one of the alternatives (or there are none)."""
    if not requirement:
        return True
    return any(all(scores.get(k, 0) >= v for k, v in option.items()) for option in requirement)


# -- expertise ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpertiseGrant:
    """A class feature granting Expertise: in how many proficiencies, and which may count."""

    cls: str
    source: str
    feature: str
    level: int
    count: int
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _expertise_rows() -> tuple[ExpertiseGrant, ...]:
    data = read_json(EXPERTISE_FILE, {}) or {}
    return tuple(
        ExpertiseGrant(
            str(row["class"]),
            str(row["source"]),
            str(row["feature"]),
            int(row["level"]),
            int(row["count"]),
            tuple(str(t) for t in row.get("tools") or ()),
            tuple(str(s) for s in row.get("skills") or ()),
        )
        for row in data.get("features", [])
    )


def expertise_grants(class_name: str, source: str) -> list[ExpertiseGrant]:
    """The Expertise a class's features grant, by level (Rogue 1 and 6: two each)."""
    return [
        row
        for row in _expertise_rows()
        if row.cls.lower() == class_name.lower() and row.source.lower() == source.lower()
    ]


@dataclass(frozen=True)
class FeatChoice:
    """A choice a feat asks for in prose only (``feat_choices.json``)."""

    feat: str
    id: str
    label: str
    options: tuple[str, ...]
    repeatable: bool = False


@lru_cache(maxsize=1)
def _feat_choice_rows() -> tuple[FeatChoice, ...]:
    data = read_json(FEAT_CHOICES_FILE, {}) or {}
    return tuple(
        FeatChoice(
            str(row["feat"]),
            str(row["id"]),
            str(row.get("label", "")),
            tuple(row["options"]),
            bool(row.get("repeatable")),
        )
        for row in data.get("choices") or []
    )


def feat_choices(reference: str) -> list[FeatChoice]:
    """The prose-only choices of a feat (``"Elemental Adept|XPHB"``)."""
    return [row for row in _feat_choice_rows() if row.feat.lower() == reference.lower()]


__all__ = [
    "MULTICLASS_MINIMUM",
    "FeatChoice",
    "feat_choices",
    "ExpertiseGrant",
    "expertise_grants",
    "Requirement",
    "ability_problem",
    "meets",
    "methods",
    "multiclass_requirement",
    "point_budget",
    "point_cost",
    "standard_array",
]
