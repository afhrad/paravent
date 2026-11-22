"""The guides' variant rules for characters, switched on per world.

A world may tick these (World settings), and the sheet and the play rules apply them
(documentation/CHARACTER-FORMAT.md §5b):

* **Rest variants** (2014 guide): *epic heroism*, a short rest of 5 minutes and a long rest
  of an hour; *gritty realism*, a short rest of 8 hours and a long rest of 7 days. What a
  rest restores stays the same; the world's clock moves on by the variant's time when the
  party rests. At most one of the two.
* **Hero points** (2014 guide): 5 at 1st level, a new total of 5 + half the level at each
  level (unspent ones lost). One may be spent on an attack roll, an ability check or a
  saving throw to add a d6, or to turn a failed death save into a success.
* **Honor and Sanity** (2014 guide): two more ability scores, with checks and saves of
  their own. The gamemaster raises or lowers Honor by 1 at the end of an adventure.
* **Slow natural healing** (2014 guide): a long rest restores no hit points; hit dice may
  be spent at its end, as after a short rest.
* **Healer's kit dependency** (2014 guide): after a rest, no hit dice are spent until a use
  of a healer's kit has treated the character's wounds.
* **Defeated, not dead** (2024 guide): a character who would die is instead *defeated*: at
  1 hit point and unconscious until a Greater Restoration or a sudden awakening, a DC 20
  Constitution save after each long rest.
"""

from __future__ import annotations

from collections.abc import Sequence

REST_VARIANTS = ("rest_epic", "rest_gritty")
SWITCHES = ("hero_points", "honor", "sanity", "slow_healing", "healers_kit", "defeated")
RULES = (*REST_VARIANTS, *SWITCHES)
# The extra ability scores, by the key their rolls and words use.
EXTRA_ABILITIES = {"honor": "hon", "sanity": "san"}
# A rest's length in minutes, by variant ("" the handbook's).
REST_MINUTES = {
    "": {"short": 60, "long": 8 * 60},
    "rest_epic": {"short": 5, "long": 60},
    "rest_gritty": {"short": 8 * 60, "long": 7 * 24 * 60},
}
HERO_DIE = 6
AWAKENING_DC = 20
HEALERS_KIT = "healer's kit"
KIT_USES = 10


def kept(chosen: Sequence[str]) -> list[str]:
    """Known rules from a form or a file, in their own order; one rest variant at most."""
    wanted = set(chosen)
    rests = [r for r in REST_VARIANTS if r in wanted][:1]
    return [r for r in RULES if r in wanted and (r not in REST_VARIANTS or r in rests)]


def rest_variant(rules: Sequence[str] | frozenset[str]) -> str:
    """The rest variant in play, "" for the handbook's."""
    return next((r for r in REST_VARIANTS if r in rules), "")


def rest_minutes(rules: Sequence[str] | frozenset[str], kind: str) -> int:
    """How long a short or long rest takes under these rules."""
    return REST_MINUTES[rest_variant(rules)][kind]


def hero_points_max(level: int) -> int:
    """The hero points a character has at ``level``: 5, then 5 + half the level."""
    return 5 if level <= 1 else 5 + level // 2


def extra_abilities(rules: Sequence[str] | frozenset[str]) -> list[str]:
    """The extra ability keys these rules add (``hon``, ``san``)."""
    return [key for rule, key in EXTRA_ABILITIES.items() if rule in rules]


__all__ = [
    "AWAKENING_DC",
    "EXTRA_ABILITIES",
    "HEALERS_KIT",
    "HERO_DIE",
    "KIT_USES",
    "REST_MINUTES",
    "REST_VARIANTS",
    "RULES",
    "SWITCHES",
    "extra_abilities",
    "hero_points_max",
    "kept",
    "rest_minutes",
    "rest_variant",
]
