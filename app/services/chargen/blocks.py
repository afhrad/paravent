"""One reader for the corpus's choice blocks (documentation/CHARGEN-WIZARD.md §1 rule 4).

Species, backgrounds, classes and feats all say what they grant in the same few shapes::

    {"insight": true, "religion": true}                      fixed
    {"choose": {"from": ["arcana", "history"], "count": 2}}  two of these
    {"any": 2} / {"anyStandard": 1} / {"anyArtisansTool": 1} two of anything of the kind
    {"cha": 2, "choose": {"from": [...], "count": 2}}        ability increases
    {"choose": {"weighted": {"from": [...], "weights": [2, 1]}}}   a +2/+1 to assign

and a list of several such blocks offers **alternatives** — one of them, the player's
choice. This module turns a block into :class:`Grant` s (fixed) and :class:`Pick` s
(questions), so a new book needs no new code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.base import ABILITIES

PickKind = Literal[
    "species",
    "subspecies",
    "lineage",
    "class",
    "level",
    "subclass",
    "background",
    "variant",
    "casting",
    "featureoption",
    "skill",
    "tool",
    "language",
    "ability",
    "feat",
    "asi",
    "optional",
    "hp",
    "equipment",
    "item",
    "cantrip",
    "spell",
    "prepared",
    "expertise",
    "weapon",
]


@dataclass(frozen=True)
class Grant:
    """Something given outright: a skill, a language, a feat — with who gave it."""

    kind: str
    value: str
    origin: str


# Ability score improvements and feats raise a score this far (an Epic Boon says 30).
ABILITY_LIMIT = 20


@dataclass(frozen=True)
class Pick:
    """One question the wizard asks, answered by a list of values in ``choices[id]``.

    ``options`` lists what may be chosen; empty means "anything of the kind" and
    ``pool`` narrows that (``standard`` languages, ``artisan`` tools, a feat category).
    ``count`` values are wanted. For abilities, ``amount`` is the increase each chosen
    ability gets, and ``weights`` makes the pick a distribution — the first value
    chosen gets ``weights[0]``, the second ``weights[1]``.
    """

    id: str
    kind: PickKind
    origin: str
    count: int = 1
    options: tuple[str, ...] = ()
    pool: str = ""
    amount: int = 1
    weights: tuple[int, ...] = ()
    label: str = ""
    # The highest score this pick may raise an ability to; 0: not its concern.
    limit: int = 0
    # Labels for an alternatives pick: what each variant gives.
    details: tuple[str, ...] = field(default=())

    @property
    def optional(self) -> bool:
        """A question that may be left unanswered (none, so far: every pick is owed)."""
        return False


# Keys that stand for "any N of a kind", with the pool they draw from.
_ANY_KEYS: dict[str, tuple[str, str]] = {
    "any": ("", ""),
    "anySkill": ("skill", ""),
    "anyTool": ("tool", "any"),
    "anyLanguage": ("language", "any"),
    "anyStandard": ("language", "standard"),
    "anyExotic": ("language", "exotic"),
    "anyArtisansTool": ("tool", "artisan"),
    "anyMusicalInstrument": ("tool", "instrument"),
    "anyGamingSet": ("tool", "gaming"),
}
# Keys inside a block that are never a grant.
_NOT_GRANTS = {"choose", "other", "hidden", *list(_ANY_KEYS)}


def _clean(value: str) -> str:
    """A grant key as the wizard compares it: ``"thieves' tools|phb"`` → ``thieves' tools``."""
    return value.split("|")[0].strip().lower()


def alternatives(block: Any) -> list[Mapping[str, Any]]:
    """The alternatives a block offers (usually one)."""
    if isinstance(block, Mapping):
        return [block]
    if isinstance(block, Sequence) and not isinstance(block, str):
        return [b for b in block if isinstance(b, Mapping)]
    return []


def variant_pick(pick_id: str, block: Any, origin: str) -> Pick | None:
    """When a block offers several alternatives, the question which one."""
    options = alternatives(block)
    if len(options) < 2:
        return None
    return Pick(
        f"{pick_id}.variant",
        "variant",
        origin,
        options=tuple(str(i) for i in range(len(options))),
        details=tuple(_describe(o) for o in options),
    )


def chosen_alternative(block: Any, choices: Mapping[str, Sequence[str]], pick_id: str) -> Any:
    """The alternative the player chose (the first until they choose)."""
    options = alternatives(block)
    if not options:
        return None
    answer = choices.get(f"{pick_id}.variant") or ["0"]
    index = int(answer[0]) if answer[0].isdigit() else 0
    return options[index] if 0 <= index < len(options) else options[0]


def _describe(option: Mapping[str, Any]) -> str:
    """A short, untranslated description of one alternative, for its label."""
    parts = []
    for key, value in option.items():
        if key == "choose" and isinstance(value, Mapping):
            weighted = value.get("weighted")
            if isinstance(weighted, Mapping):
                parts.append("/".join(f"+{w}" for w in weighted.get("weights") or []))
            else:
                parts.append(
                    f"{value.get('count', 1)}× ({', '.join(map(str, value.get('from') or []))})"
                )
        elif key in _ANY_KEYS:
            parts.append(f"{key} {value}")
        elif value is True:
            parts.append(_clean(key))
        elif isinstance(value, int) and key in ABILITIES:
            parts.append(f"{key.upper()} +{value}")
    return ", ".join(parts)


def proficiencies(
    block: Any,
    kind: Literal["skill", "tool", "language"],
    origin: str,
    pick_id: str,
    choices: Mapping[str, Sequence[str]],
) -> tuple[list[Grant], list[Pick]]:
    """Grants and picks of a skill, tool or language block.

    ``{"choose": {"from": [...]}}`` lists may name pools themselves (``"anyArtisansTool"``
    among a background's tools), which widen the pick to that pool.
    """
    grants: list[Grant] = []
    picks: list[Pick] = []
    variant = variant_pick(pick_id, block, origin)
    if variant is not None:
        picks.append(variant)
    option = chosen_alternative(block, choices, pick_id)
    if option is None:
        return grants, picks
    for key, value in option.items():
        if key in _NOT_GRANTS or value is not True:
            continue
        grants.append(Grant(kind, _clean(key), origin))
    n = 0
    for key, value in option.items():
        if key in _ANY_KEYS and isinstance(value, int) and value > 0:
            any_kind, pool = _ANY_KEYS[key]
            if any_kind and any_kind != kind:
                continue
            picks.append(Pick(f"{pick_id}.{n}", kind, origin, count=value, pool=pool))
            n += 1
    choose = option.get("choose")
    for block_choice in choose if isinstance(choose, list) else [choose]:
        if not isinstance(block_choice, Mapping):
            continue
        listed = [str(x) for x in block_choice.get("from") or []]
        pool = ""
        named = []
        for entry in listed:
            if entry in _ANY_KEYS:
                pool = _ANY_KEYS[entry][1] or pool
            else:
                named.append(_clean(entry))
        picks.append(
            Pick(
                f"{pick_id}.{n}",
                kind,
                origin,
                count=int(block_choice.get("count", 1)),
                options=() if pool else tuple(named),
                pool=pool,
            )
        )
        n += 1
    return grants, picks


def feat_ability_id(reference: str, taking: int = 1) -> str:
    """The id of a feat's ability question; a repeatable feat's later takings each have
    their own (``feat.elemental adept|xphb.ability2``), so each raises a score."""
    return f"feat.{reference}.ability" + (str(taking) if taking > 1 else "")


def ability_block(
    block: Any,
    origin: str,
    pick_id: str,
    choices: Mapping[str, Sequence[str]],
    limit: int = 0,
) -> tuple[dict[str, int], list[Pick]]:
    """Fixed increases and the increases to choose, from an ``ability`` block.

    Fixed ones (a 2014 dwarf's +2 CON) are returned as a dict; the sheet reads them from
    the corpus itself, so the wizard only shows them. ``hidden`` blocks (the 2024 Ability
    Score Improvement feat) are still walked: hidden only means "not printed". ``limit``
    is what the picks may raise a score to, unless the block says (an Epic Boon's 30).
    """
    fixed: dict[str, int] = {}
    picks: list[Pick] = []
    variant = variant_pick(pick_id, block, origin)
    if variant is not None:
        picks.append(variant)
    option = chosen_alternative(block, choices, pick_id)
    if option is None:
        return fixed, picks
    for key, value in option.items():
        if key in ABILITIES and isinstance(value, int):
            fixed[key] = value
    limit = int(option.get("max") or limit)
    choose = option.get("choose")
    if isinstance(choose, Mapping):
        weighted = choose.get("weighted")
        if isinstance(weighted, Mapping):
            weights = tuple(int(w) for w in weighted.get("weights") or [])
            picks.append(
                Pick(
                    f"{pick_id}.0",
                    "ability",
                    origin,
                    count=len(weights),
                    options=tuple(str(a) for a in weighted.get("from") or ABILITIES),
                    weights=weights,
                    limit=limit,
                )
            )
        else:
            picks.append(
                Pick(
                    f"{pick_id}.0",
                    "ability",
                    origin,
                    count=int(choose.get("count", 1)),
                    options=tuple(str(a) for a in choose.get("from") or ABILITIES),
                    amount=int(choose.get("amount", 1)),
                    limit=limit,
                )
            )
    return fixed, picks


def ability_increases(pick: Pick, values: Sequence[str]) -> dict[str, int]:
    """What an answered ability pick adds: ``{"int": 2, "wis": 1}``."""
    out: dict[str, int] = {}
    for index, ability in enumerate(values):
        if ability not in ABILITIES:
            continue
        gain = pick.weights[index] if pick.weights and index < len(pick.weights) else pick.amount
        out[ability] = out.get(ability, 0) + gain
    return out


def over_limit(
    picks: Sequence[Pick],
    answers: Mapping[str, Sequence[str]],
    scores: Mapping[str, int],
) -> list[Pick]:
    """The picks that raise a score past their limit; ``scores`` have the answers in them.

    An Epic Boon goes past 20 by right, so what a boon adds does not count against an
    ordinary increase taken before it.
    """
    gains = [
        (pick, ability_increases(pick, answers.get(pick.id) or [])) for pick in picks if pick.limit
    ]
    beyond: dict[str, int] = {}
    for pick, raised in gains:
        if pick.limit > ABILITY_LIMIT:
            for ability, gain in raised.items():
                beyond[ability] = beyond.get(ability, 0) + gain
    return [
        pick
        for pick, raised in gains
        if any(
            scores.get(ability, 0) - (beyond.get(ability, 0) if pick.limit == ABILITY_LIMIT else 0)
            > pick.limit
            for ability in raised
        )
    ]


def feat_saves(
    feats: Sequence[Any],
    catalogue: Any,
    picks: Sequence[Pick],
    choices: Mapping[str, Sequence[str]],
) -> list[str]:
    """Saving throws feats made proficient: Resilient's is the ability it raised."""
    out: list[str] = []
    for feat in feats:
        record = catalogue.find("feat", feat)
        if record is None or not record.data.get("savingThrowProficiencies"):
            continue
        prefix = f"feat.{record.reference}.ability"
        for pick in picks:
            if pick.id.startswith(prefix):
                out.extend(a for a in choices.get(pick.id) or [] if a in ABILITIES)
    return list(dict.fromkeys(out))


def feat_block(
    block: Any,
    origin: str,
    pick_id: str,
    choices: Mapping[str, Sequence[str]],
) -> tuple[list[Grant], list[Pick]]:
    """Feats a species or background grants: fixed (``{"alert|xphb": true}``) or chosen.

    A fixed key may carry a variant after a semicolon (``"magic initiate; wizard|xphb"``):
    the feat is Magic Initiate, the variant its spell list, kept as the grant's value.
    """
    grants: list[Grant] = []
    picks: list[Pick] = []
    variant = variant_pick(pick_id, block, origin)
    if variant is not None:
        picks.append(variant)
    option = chosen_alternative(block, choices, pick_id)
    if option is None:
        return grants, picks
    for key, value in option.items():
        if value is True and key not in _NOT_GRANTS:
            grants.append(Grant("feat", key, origin))
    category = option.get("anyFromCategory")
    if isinstance(category, Mapping):
        picks.append(
            Pick(
                f"{pick_id}.0",
                "feat",
                origin,
                count=int(category.get("count", 1)),
                pool=",".join(str(c) for c in category.get("category") or []),
            )
        )
    elif isinstance(option.get("any"), int):
        picks.append(Pick(f"{pick_id}.0", "feat", origin, count=int(option["any"])))
    return grants, picks


def feat_reference(key: str) -> tuple[str, str | None, str | None]:
    """``"magic initiate; wizard|xphb"`` → (name, source, variant)."""
    name, _, source = key.partition("|")
    base, _, variant = name.partition(";")
    return base.strip(), (source.strip().upper() or None), (variant.strip() or None)


__all__ = [
    "Grant",
    "Pick",
    "PickKind",
    "ability_block",
    "ability_increases",
    "alternatives",
    "chosen_alternative",
    "feat_block",
    "feat_reference",
    "feat_saves",
    "proficiencies",
    "variant_pick",
]
