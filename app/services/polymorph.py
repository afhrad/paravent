"""Polymorph and True Polymorph: who may become what, and what the shape gives.

The rules are prose in the spells, so their few numbers are kept here. Polymorph turns a
creature into a beast, True Polymorph into any creature, either of a challenge rating no
higher than the target's (a character's level stands for it). Under the 2014 rules the
shape has hit points of its own that take damage first; under the 2024 rules it gives
temporary hit points and ends when they are gone. Either way the shape lasts while the
caster concentrates on the spell (an object, and True Polymorph made permanent after a
full hour, are left to the table).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from app.models.gamestate import SourceSet, SpellForm

# Lowercased spell name → whether any creature type is allowed (True Polymorph).
SPELLS: dict[str, bool] = {"polymorph": False, "true polymorph": True}
# The books whose spells follow the 2024 rules.
EDITION_2024 = frozenset({"XPHB"})


def spell_name(reference: str) -> str:
    """``"Polymorph|PHB"`` → ``"polymorph"``."""
    return reference.partition("|")[0].strip().lower()


def is_polymorph(reference: str) -> bool:
    """Whether a spell reference (or a bare name) is Polymorph or True Polymorph."""
    return spell_name(reference) in SPELLS


def edition(spell: str) -> str:
    """``"2024"`` for the 2024 printing of the spell, ``"2014"`` for any other."""
    return "2024" if spell.partition("|")[2].strip().upper() in EDITION_2024 else "2014"


@dataclass(frozen=True)
class Shape:
    """A creature a target may be turned into, as a picker lists it."""

    name: str
    source: str
    cr: str
    hp: int

    @property
    def reference(self) -> str:
        return f"{self.name}|{self.source}"


def allows(spell: str, target_cr: Fraction, monster: Any) -> bool:
    """Whether ``spell`` may turn a creature of ``target_cr`` into ``monster`` (a
    projection): a beast for Polymorph, anything for True Polymorph, no stronger."""
    if monster is None or monster.cr is None or monster.cr.value is None:
        return False
    if monster.cr.value > target_cr:
        return False
    return SPELLS.get(spell_name(spell), False) or "beast" in monster.kind.types


def shapes(
    compendium: Any, source_set: SourceSet | None, spell: str, target_cr: Fraction
) -> list[Shape]:
    """Every creature in the world's books ``spell`` may turn a target of ``target_cr``
    into, by name."""
    query = compendium.monsters().in_books(source_set)
    if not SPELLS.get(spell_name(spell), False):
        query = query.of_type("beast")
    found = [
        Shape(m.name, m.source, m.cr.text, m.hp or 1)
        for m in query
        if m.cr is not None and m.cr.value is not None and m.cr.value <= target_cr
    ]
    return sorted(found, key=lambda s: s.name.lower())


def form_for(spell: str, shape: Any, caster: str) -> SpellForm:
    """The record of a target held in ``shape`` (a projection) by ``caster``: the shape's
    own hit points under the 2014 rules, none under the 2024 rules."""
    hp = shape.hp or 1
    return SpellForm(
        form=f"{shape.name}|{shape.source}",
        spell=spell,
        caster=caster,
        hp=hp if edition(spell) == "2014" else None,
    )


def same_spell(one: str, other: str) -> bool:
    """Whether two spell references or names name the same spell, printing aside."""
    return spell_name(one) == spell_name(other)


def held_by(form: SpellForm | None, caster: str, spell: str) -> bool:
    """Whether ``form`` is held up by ``caster``'s concentration on ``spell``."""
    return form is not None and form.caster == caster and same_spell(form.spell, spell)


__all__ = [
    "EDITION_2024",
    "SPELLS",
    "Shape",
    "allows",
    "edition",
    "form_for",
    "held_by",
    "is_polymorph",
    "same_spell",
    "shapes",
    "spell_name",
]
