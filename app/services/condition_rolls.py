"""How a creature's own conditions bend its d20 rolls (both editions agree on these).

Advantage and disadvantage from every source cancel out, as the rules have it: one of each
is a normal roll, however many there are. Only what the roller's own conditions and
exhaustion do is here — a target's (an attack against a prone creature) is the
gamemaster's call at Hit or Miss. Exhaustion follows the character's edition.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.services.dice import Advantage

# What each condition does to its creature's rolls: (kind of roll, +1 advantage / -1 dis).
# Kinds: "attack", "check" (skills and initiative too) and "save:<ability>".
_EFFECTS: dict[str, tuple[tuple[str, int], ...]] = {
    "blinded": (("attack", -1),),
    "frightened": (("attack", -1), ("check", -1)),
    "poisoned": (("attack", -1), ("check", -1)),
    "prone": (("attack", -1),),
    "restrained": (("attack", -1), ("save:dex", -1)),
    "invisible": (("attack", 1),),
    # Variant encumbrance's heavy load: passed only for rolls of STR, DEX or CON.
    "encumbered": (
        ("attack", -1),
        ("check", -1),
        ("save:str", -1),
        ("save:dex", -1),
        ("save:con", -1),
    ),
}


@dataclass(frozen=True)
class Bent:
    """A roll's advantage after conditions, and the conditions that bent it; ``penalty``
    is what 2024's exhaustion takes off the roll (−2 a level)."""

    advantage: Advantage
    reasons: tuple[str, ...] = ()
    penalty: int = 0


def bend(
    chosen: Advantage,
    conditions: Iterable[str],
    kind: str,
    exhaustion: int = 0,
    edition: str = "2014",
) -> Bent:
    """Combine the advantage chosen with what ``conditions`` (and the exhaustion level, by
    the character's edition) do to a roll of ``kind``.

    2014: level 1 gives disadvantage on ability checks, level 3 on attacks and saves too.
    2024: every d20 test takes −2 per level instead.
    """
    ups = 1 if chosen is Advantage.ADVANTAGE else 0
    downs = 1 if chosen is Advantage.DISADVANTAGE else 0
    reasons: list[str] = []
    penalty = 0
    if exhaustion and kind:
        if edition == "2024":
            penalty = -2 * exhaustion
            reasons.append("exhaustion")
        elif (kind == "check" and exhaustion >= 1) or (
            exhaustion >= 3 and (kind == "attack" or kind.startswith("save:"))
        ):
            downs += 1
            reasons.append("exhaustion")
    for condition in dict.fromkeys(c.lower() for c in conditions):
        for affected, direction in _EFFECTS.get(condition, ()):
            if affected == kind:
                ups += direction > 0
                downs += direction < 0
                reasons.append(condition)
    if ups and downs:
        return Bent(Advantage.NORMAL, tuple(reasons), penalty)
    if ups:
        return Bent(Advantage.ADVANTAGE, tuple(reasons), penalty)
    if downs:
        return Bent(Advantage.DISADVANTAGE, tuple(reasons), penalty)
    return Bent(Advantage.NORMAL, tuple(reasons), penalty)


def affected(conditions: Iterable[str]) -> dict[str, tuple[tuple[str, int], ...]]:
    """The conditions among ``conditions`` that change rolls, with what they do."""
    return {c: _EFFECTS[c] for c in dict.fromkeys(x.lower() for x in conditions) if c in _EFFECTS}


__all__ = ["Bent", "affected", "bend"]
