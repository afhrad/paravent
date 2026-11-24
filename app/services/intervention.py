"""A cleric's Divine Intervention, by the edition of the cleric's class.

**2014** (PHB): from level 10 the cleric calls on the deity; percentile dice of the
cleric's level or less, and it answers, the gamemaster saying how. Answered, it may not be
asked again for seven days; unanswered, after a long rest. From level 20 it always answers.

**2024** (XPHB): from level 10, once per long rest, the cleric casts any cleric spell of
level 5 or lower that is not cast as a reaction, without a slot and without material
components. From level 20 (*Greater Divine Intervention*) it may be *Wish*, after which
2d4 long rests pass before it can be called on again.

The waiting is the character's (``PlayState.intervention_rests``, counted down by each
long rest, and ``intervention_until``, a minute of the world's clock).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.pc import ChosenOption, PlayerCharacter
from app.services.chargen.catalogue import Catalogue, Offer

FROM_LEVEL = 10
ALWAYS_AT = 20
MOST_SPELL_LEVEL = 5
WEEK_DAYS = 7
WISH = "wish|xphb"


@dataclass(frozen=True)
class Intervention:
    """What the sheet shows of a cleric's Divine Intervention."""

    rules: str  # "classic" (2014) or "one" (2024)
    level: int  # the cleric's level
    rests: int = 0  # long rests still to take
    until: int | None = None  # the world's minute it may be called on again

    @property
    def greater(self) -> bool:
        """It always answers (2014), or may be Wish (2024)."""
        return self.level >= ALWAYS_AT

    def ready(self, now: int | None) -> bool:
        """Whether it may be called on at the world's minute ``now``."""
        if self.rests > 0:
            return False
        return self.until is None or now is None or now >= self.until


def intervention_of(pc: PlayerCharacter) -> Intervention | None:
    """The character's Divine Intervention, from its cleric levels (10 or more); None
    without them. The class's book says the edition: the 2024 handbook's cleric casts,
    every other prays for an answer."""
    cleric = next((c for c in pc.classes if c.name.lower() == "cleric"), None)
    if cleric is None or cleric.level < FROM_LEVEL:
        return None
    rules = "one" if (cleric.source or "").upper() == "XPHB" else "classic"
    state = pc.state
    return Intervention(rules, cleric.level, state.intervention_rests, state.intervention_until)


@dataclass(frozen=True)
class Castable:
    """A spell a 2024 cleric may cast by Divine Intervention."""

    reference: str
    name: str
    level: int
    concentration: bool
    wish: bool = False


def _concentration(data: Any) -> bool:
    return any(isinstance(d, dict) and d.get("concentration") for d in data.get("duration") or [])


def _reaction(data: Any) -> bool:
    return any(isinstance(t, dict) and t.get("unit") == "reaction" for t in data.get("time") or [])


def castable(pc: PlayerCharacter, catalogue: Catalogue, held: Intervention) -> list[Castable]:
    """The spells a 2024 cleric may choose, in the world's books: the cleric's list up to
    level 5, none cast as a reaction, and Wish from level 20; by level, then name."""
    cleric = next((c for c in pc.classes if c.name.lower() == "cleric"), None)
    if cleric is None or held.rules != "one":
        return []
    cls = catalogue.find("class", ChosenOption(name=cleric.name, source=cleric.source))
    if cls is None:
        return []
    found: list[Offer] = [
        o
        for o in catalogue.class_spells(cls, MOST_SPELL_LEVEL)
        if int(o.data.get("level", 0)) >= 1 and not _reaction(o.data)
    ]
    out = [
        Castable(o.reference, o.name, int(o.data.get("level", 0)), _concentration(o.data))
        for o in {o.name.lower(): o for o in found}.values()
    ]
    if held.greater:
        wish = catalogue.find("spell", WISH) or catalogue.find("spell", "Wish")
        if wish is not None:
            out.append(Castable(wish.reference, wish.name, 9, False, wish=True))
    return sorted(out, key=lambda c: (c.level, c.name.lower()))


def answered(roll: int, level: int) -> bool:
    """Whether a 2014 cleric's percentile roll calls the deity down."""
    return level >= ALWAYS_AT or roll <= level


__all__ = [
    "ALWAYS_AT",
    "Castable",
    "FROM_LEVEL",
    "MOST_SPELL_LEVEL",
    "WEEK_DAYS",
    "WISH",
    "Intervention",
    "answered",
    "castable",
    "intervention_of",
]
