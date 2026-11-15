"""Polymorph, True Polymorph and Shapechange: who may become what, and what the shape
gives.

The rules are prose in the spells, so their few facts are kept here. Polymorph turns a
creature into a beast, True Polymorph into any creature, Shapechange its caster into any
creature but a construct or undead; each of a challenge rating no higher than the
target's (a character's level stands for it). Under the 2014 rules the shape has hit
points of its own that take damage first; under the 2024 rules it gives temporary hit
points and ends when they are gone. Either way the shape lasts while the caster
concentrates on the spell. Polymorph and True Polymorph forbid casting in the shape;
Shapechange does not. (An object, True Polymorph made permanent after a full hour, and
Shapechange's own limits on what the shape may do are left to the table.)
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from app.models.gamestate import SourceSet, SpellForm


@dataclass(frozen=True)
class SpellRules:
    """What one shape spell allows: the creature types (``None``: any), those never
    allowed, whether it reaches only its caster, and whether the shape may cast."""

    types: frozenset[str] | None
    excluded: frozenset[str] = frozenset()
    self_only: bool = False
    casts: bool = False


# Lowercased spell name → its rules.
SPELLS: dict[str, SpellRules] = {
    "polymorph": SpellRules(frozenset({"beast"})),
    "true polymorph": SpellRules(None),
    "shapechange": SpellRules(None, frozenset({"construct", "undead"}), self_only=True, casts=True),
}
# The books whose spells follow the 2024 rules.
EDITION_2024 = frozenset({"XPHB"})


def spell_name(reference: str) -> str:
    """``"Polymorph|PHB"`` → ``"polymorph"``."""
    return reference.partition("|")[0].strip().lower()


def is_polymorph(reference: str) -> bool:
    """Whether a spell reference (or a bare name) holds a shape: Polymorph, True
    Polymorph or Shapechange."""
    return spell_name(reference) in SPELLS


def rules_of(spell: str) -> SpellRules:
    """The rules of a shape spell (Polymorph's for any other name)."""
    return SPELLS.get(spell_name(spell), SPELLS["polymorph"])


def casts_in_shape(spell: str) -> bool:
    """Whether a creature held in ``spell``'s shape may still cast (Shapechange)."""
    return rules_of(spell).casts


def self_only(spell: str) -> bool:
    """Whether ``spell`` changes only its caster (Shapechange)."""
    return rules_of(spell).self_only


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
    projection): of a type the spell allows, no stronger."""
    if monster is None or monster.cr is None or monster.cr.value is None:
        return False
    if monster.cr.value > target_cr:
        return False
    return _type_allowed(rules_of(spell), monster.kind.types)


def _type_allowed(rules: SpellRules, types: Any) -> bool:
    kinds = set(types)
    if kinds & rules.excluded:
        return False
    return rules.types is None or bool(kinds & rules.types)


def shapes(
    compendium: Any, source_set: SourceSet | None, spell: str, target_cr: Fraction
) -> list[Shape]:
    """Every creature in the world's books ``spell`` may turn a target of ``target_cr``
    into, by name."""
    rules = rules_of(spell)
    query = compendium.monsters().in_books(source_set)
    if rules.types is not None:
        query = query.of_type(*rules.types)
    found = [
        Shape(m.name, m.source, m.cr.text, m.hp or 1)
        for m in query
        if m.cr is not None
        and m.cr.value is not None
        and m.cr.value <= target_cr
        and _type_allowed(rules, m.kind.types)
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
    "SpellRules",
    "allows",
    "casts_in_shape",
    "edition",
    "form_for",
    "held_by",
    "is_polymorph",
    "rules_of",
    "same_spell",
    "self_only",
    "shapes",
    "spell_name",
]
