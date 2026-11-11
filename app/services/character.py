"""Derived statistics: turning a :class:`~app.models.pc.PlayerCharacter` into a sheet.

The record stores **chosen options, not resolved state** (documentation/CHARGEN.md §4), so
every number a player reads — HP, AC, saving throws, spell save DC, passive scores — is
computed here, on demand, from those choices plus the compendium.

**The formulas are rules logic, not data.** AC calculation, HP per level, the proficiency
bonus progression and the save DC exist in the corpus only as English sentences
(documentation/CHARGEN.md G2). No 5e data source encodes them; D&D Beyond hardcodes them
too. What *is* data, and is read rather than retyped:

* hit dice, from ``class.hd``
* which ability each skill uses, from ``skills.json``
* armour AC, type and stealth penalty, from the item records
* the ``preparedSpells`` formula, which really is an evaluable string

Every result carries its **derivation** so the UI can explain itself: a player seeing
"AC 16" can be shown "chain mail 16, no DEX (heavy)" rather than a bare number
(documentation/CHARGEN-WIZARD.md §4, step "Details & review").
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.models.base import ABILITIES
from app.models.pc import PlayerCharacter

# Armour item types, as the corpus abbreviates them.
LIGHT_ARMOR = "LA"
MEDIUM_ARMOR = "MA"
HEAVY_ARMOR = "HA"
SHIELD = "S"
ARMOR_TYPES = frozenset({LIGHT_ARMOR, MEDIUM_ARMOR, HEAVY_ARMOR})

# How much DEX each armour category adds. Heavy armour adds none; medium caps at +2.
MEDIUM_ARMOR_DEX_CAP = 2

# Unarmoured Defense is a class feature whose text is prose, so the second ability is
# hardcoded per class — the one place where a rule cannot be read from the data.
# Barbarian adds CON, Monk adds WIS; both require no armour, and only the Barbarian may
# still use a shield.
UNARMORED_DEFENSE: dict[str, tuple[str, bool]] = {
    "barbarian": ("con", True),
    "monk": ("wis", False),
}


def ability_modifier(score: int | None) -> int:
    """The 5e modifier for a score: ``floor((score - 10) / 2)``.

    An unset score is treated as 10, giving +0, so a half-built character still renders
    a sheet instead of raising. The wizard shows those fields as blank, not as zero.
    """
    if score is None:
        return 0
    return math.floor((score - 10) / 2)


def proficiency_bonus(level: int) -> int:
    """The proficiency bonus at a character level: ``ceil(level / 4) + 1``.

    +2 at levels 1–4, rising to +6 at 17. Prose in the corpus, so computed here.
    """
    if level < 1:
        return 0
    return math.ceil(level / 4) + 1


@dataclass(frozen=True)
class Derivation:
    """A computed value together with how it was reached.

    The parts are what the UI shows on tap: ``["chain mail 16", "no DEX (heavy)"]``.
    Carrying them beside the total is what lets the wizard teach the system rather than
    merely report it.

    ``sources`` are the same parts as ``(source key, value)`` for a bonus a roll adds
    (``("ability:dex", 3)``, ``("proficiency", 2)``): what the log records, in no
    language (documentation/PROTOCOL.md §2). Zero parts are left out.
    """

    value: int
    parts: tuple[str, ...] = ()
    sources: tuple[tuple[str, int], ...] = ()

    def __int__(self) -> int:
        """The value, so a `Derivation` can stand in for a number."""
        return self.value

    @property
    def explanation(self) -> str:
        """The derivation as one readable line."""
        return " + ".join(self.parts) if self.parts else str(self.value)


@dataclass
class AbilityBreakdown:
    """One ability, split into base, bonuses and total.

    Three numbers rather than one, because a player must always be able to see where a
    score came from (documentation/CHARGEN-WIZARD.md, step "Abilities").
    """

    ability: str
    base: int | None = None
    species_bonus: int = 0
    background_bonus: int = 0
    improvement_bonus: int = 0

    @property
    def total(self) -> int | None:
        """The final score, or None while no base is set."""
        if self.base is None:
            return None
        return self.base + self.bonus

    @property
    def bonus(self) -> int:
        """Every increase applied to the base score."""
        return self.species_bonus + self.background_bonus + self.improvement_bonus

    @property
    def modifier(self) -> int:
        """The modifier derived from the total."""
        return ability_modifier(self.total)

    @property
    def sources(self) -> tuple[str, ...]:
        """Where each bonus came from, for display."""
        out: list[str] = []
        if self.species_bonus:
            out.append(f"species +{self.species_bonus}")
        if self.background_bonus:
            out.append(f"background +{self.background_bonus}")
        if self.improvement_bonus:
            out.append(f"improvements +{self.improvement_bonus}")
        return tuple(out)


@dataclass
class EquippedArmor:
    """What the character is wearing, as the AC rules need to see it."""

    name: str | None = None
    armor_type: str | None = None
    base_ac: int | None = None
    shield_ac: int = 0
    stealth_disadvantage: bool = False
    strength_requirement: int | None = None

    @property
    def is_armored(self) -> bool:
        """True when body armour is worn — a shield alone does not count."""
        return self.armor_type in ARMOR_TYPES and self.base_ac is not None


@dataclass
class ProficiencySource:
    """A skill or save proficiency, with the choice that granted it.

    Overlap between class, species and background is the most common confusion in 5e
    character creation, so the grant is named at the point of display
    (documentation/CHARGEN-WIZARD.md, step "Skills & proficiencies").
    """

    name: str
    ability: str
    proficient: bool = False
    expertise: bool = False
    granted_by: str | None = None


@dataclass
class CharacterSheet:
    """Every derived value for one character, with its derivation.

    Built by :func:`derive`. Nothing here is persisted — it is recomputed whenever the
    character, the corpus or the world's declared books change.
    """

    level: int
    proficiency_bonus: int
    abilities: dict[str, AbilityBreakdown] = field(default_factory=dict)
    hit_points: Derivation | None = None
    armor_class: Derivation | None = None
    initiative: Derivation | None = None
    saving_throws: dict[str, Derivation] = field(default_factory=dict)
    skills: dict[str, Derivation] = field(default_factory=dict)
    passive_perception: int | None = None
    spell_save_dc: Derivation | None = None
    spell_attack_bonus: Derivation | None = None
    prepared_spell_limit: int | None = None
    hit_dice: str | None = None
    speed: int | None = None
    # Prose prerequisites and rules the engine could not check, surfaced rather than
    # silently ignored (documentation/CHARGEN.md G4).
    notes: list[str] = field(default_factory=list)

    def modifier(self, ability: str) -> int:
        """The modifier for one ability, 0 when unset."""
        breakdown = self.abilities.get(ability)
        return breakdown.modifier if breakdown else 0

    def score(self, ability: str) -> int | None:
        """The total score for one ability, None when unset."""
        breakdown = self.abilities.get(ability)
        return breakdown.total if breakdown else None


# -- the formula evaluator ---------------------------------------------------------


_FORMULA_TOKEN = re.compile(r"<\$([a-z_0-9]+)\$>")


def evaluate_formula(formula: str, sheet: CharacterSheet) -> int | None:
    """Evaluate a 5eTools formula string such as ``"<$level$> + <$int_mod$>"``.

    This is the one derived value the corpus really does express as a formula rather
    than prose — ``class.preparedSpells`` — so it is read and evaluated instead of
    hardcoded per class.

    Supported tokens are ``level``, ``<ability>_mod`` and ``<ability>_score``. Anything
    else, or any arithmetic beyond ``+``/``-`` on integers, returns None rather than
    guessing: a formula we cannot read must not silently produce a wrong spell count.
    """

    def substitute(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "level":
            return str(sheet.level)
        if token.endswith("_mod"):
            ability = token[: -len("_mod")]
            if ability in ABILITIES:
                return str(sheet.modifier(ability))
        if token.endswith("_score"):
            ability = token[: -len("_score")]
            if ability in ABILITIES:
                return str(sheet.score(ability) or 0)
        raise KeyError(token)

    try:
        expanded = _FORMULA_TOKEN.sub(substitute, formula)
    except KeyError:
        return None

    # Deliberately not eval(): a restricted grammar is enough for every formula in the
    # corpus, and evaluating arbitrary expressions from a data file is not a trade worth
    # making.
    if not re.fullmatch(r"\s*-?\d+(\s*[+-]\s*-?\d+)*\s*", expanded):
        return None
    total = 0
    for term in re.findall(r"[+-]?\s*\d+", expanded.replace(" ", "")):
        total += int(term)
    return total


# -- the pieces ---------------------------------------------------------------------


def hit_points(
    level: int,
    hit_die_faces: int,
    con_modifier: int,
    *,
    average_after_first: bool = True,
) -> Derivation:
    """Maximum HP.

    Level 1 is the full hit die plus CON. Each level after adds either the average
    (``die/2 + 1``, the standard "take the average" option) or is rolled — the wizard
    offers the average by default because it is what most tables use.

    A CON increase applies retroactively to every level, which is why this takes the
    modifier rather than accumulating per level.
    """
    if level < 1:
        return Derivation(0, ())
    average = hit_die_faces // 2 + 1
    first = hit_die_faces + con_modifier
    later = (level - 1) * (average + con_modifier) if average_after_first else 0
    parts = [f"d{hit_die_faces} ({hit_die_faces}) + CON {con_modifier:+d}"]
    if level > 1:
        parts.append(f"{level - 1} × (avg {average} + CON {con_modifier:+d})")
    return Derivation(max(1, first + later), tuple(parts))


def armor_class(
    armor: EquippedArmor,
    dex_modifier: int,
    *,
    unarmored_class: str | None = None,
    second_modifier: int = 0,
) -> Derivation:
    """Armour class, covering the unarmoured, armoured and Unarmoured Defense cases.

    ``unarmored_class`` names the class granting Unarmoured Defense (``"barbarian"`` or
    ``"monk"``); ``second_modifier`` is that feature's second ability modifier. The
    feature applies only while no body armour is worn, and only the Barbarian may add a
    shield on top.
    """
    shield = armor.shield_ac

    if armor.is_armored:
        base = armor.base_ac or 10
        if armor.armor_type == HEAVY_ARMOR:
            dex = 0
            dex_note = "no DEX (heavy)"
        elif armor.armor_type == MEDIUM_ARMOR:
            dex = min(dex_modifier, MEDIUM_ARMOR_DEX_CAP)
            dex_note = f"DEX {dex:+d} (capped at +{MEDIUM_ARMOR_DEX_CAP})"
        else:
            dex = dex_modifier
            dex_note = f"DEX {dex:+d}"
        parts = [f"{armor.name or 'armor'} {base}", dex_note]
        if shield:
            parts.append(f"shield +{shield}")
        return Derivation(base + dex + shield, tuple(parts))

    if unarmored_class in UNARMORED_DEFENSE:
        second_ability, shield_allowed = UNARMORED_DEFENSE[unarmored_class]
        applied_shield = shield if shield_allowed else 0
        parts = [
            "10",
            f"DEX {dex_modifier:+d}",
            f"{second_ability.upper()} {second_modifier:+d} (Unarmored Defense)",
        ]
        if applied_shield:
            parts.append(f"shield +{applied_shield}")
        total = 10 + dex_modifier + second_modifier + applied_shield
        return Derivation(total, tuple(parts))

    parts = ["10", f"DEX {dex_modifier:+d}"]
    if shield:
        parts.append(f"shield +{shield}")
    return Derivation(10 + dex_modifier + shield, tuple(parts))


def spell_save_dc(proficiency: int, ability_modifier_value: int) -> Derivation:
    """Spell save DC: ``8 + proficiency + spellcasting ability modifier``."""
    return Derivation(
        8 + proficiency + ability_modifier_value,
        ("8", f"prof {proficiency:+d}", f"ability {ability_modifier_value:+d}"),
    )


def spell_attack_bonus(
    proficiency: int, ability_modifier_value: int, ability: str = ""
) -> Derivation:
    """Spell attack bonus: ``proficiency + spellcasting ability modifier``."""
    return Derivation(
        proficiency + ability_modifier_value,
        (f"prof {proficiency:+d}", f"ability {ability_modifier_value:+d}"),
        _sources(("proficiency", proficiency), (f"ability:{ability}", ability_modifier_value)),
    )


def _sources(*parts: tuple[str, int]) -> tuple[tuple[str, int], ...]:
    """The non-zero parts of a bonus, by source."""
    return tuple(part for part in parts if part[1])


def check_bonus(
    ability_modifier_value: int,
    proficiency: int,
    *,
    proficient: bool = False,
    expertise: bool = False,
    label: str = "ability",
    ability: str = "",
) -> Derivation:
    """A save, skill or ability check bonus; ``ability`` names its key for the log.

    Expertise doubles the proficiency bonus, and implies proficiency — a character
    cannot have expertise in a skill they are not proficient in.
    """
    multiplier = 2 if expertise else (1 if proficient else 0)
    applied = proficiency * multiplier
    parts = [f"{label} {ability_modifier_value:+d}"]
    if expertise:
        parts.append(f"expertise {applied:+d}")
    elif proficient:
        parts.append(f"prof {applied:+d}")
    sources = _sources(
        (f"ability:{ability or label.lower()}", ability_modifier_value),
        ("expertise" if expertise else "proficiency", applied),
    )
    return Derivation(ability_modifier_value + applied, tuple(parts), sources)


def prepared_spells(formula: str, level: int, modifiers: Mapping[str, int]) -> int | None:
    """A ``preparedSpells`` formula at a class level: how many spells, at least 1.

    The 2014 formulas are ``"<$level$> + <$wis_mod$>"`` and, for half casters,
    ``"<$level$> / 2 + <$cha_mod$>"`` — division rounds down, as the rules' "half your
    paladin level, rounded down" does. Tokens are ``level`` and ``<ability>_mod``;
    integers are joined by ``+ - * /`` with the usual precedence. Anything else returns
    None rather than a wrong count — never ``eval``.
    """
    values: list[int | str] = []
    for part in re.findall(r"<\$\w+\$>|\d+|[-+*/]|\S", formula):
        token = _FORMULA_TOKEN.fullmatch(part)
        if token:
            name = token.group(1)
            if name == "level":
                values.append(level)
            elif name.endswith("_mod") and name[:-4] in ABILITIES:
                values.append(modifiers.get(name[:-4], 0))
            else:
                return None
        elif part.isdigit():
            values.append(int(part))
        elif part in "+-*/":
            values.append(part)
        else:
            return None
    # Operands and operators must alternate: value (op value)*.
    if not values or len(values) % 2 == 0:
        return None
    if any(isinstance(v, str) for v in values[::2]) or any(
        not isinstance(v, str) for v in values[1::2]
    ):
        return None
    terms: list[int] = []
    sign = 1
    current = int(values[0])
    for op, operand in zip(values[1::2], values[2::2], strict=True):
        number = int(operand)
        if op == "*":
            current *= number
        elif op == "/":
            if number == 0:
                return None
            current = math.floor(current / number)
        else:
            terms.append(sign * current)
            sign = 1 if op == "+" else -1
            current = number
    terms.append(sign * current)
    return max(1, sum(terms))


__all__ = [
    "ARMOR_TYPES",
    "UNARMORED_DEFENSE",
    "AbilityBreakdown",
    "CharacterSheet",
    "Derivation",
    "EquippedArmor",
    "ProficiencySource",
    "ability_modifier",
    "armor_class",
    "check_bonus",
    "evaluate_formula",
    "hit_points",
    "prepared_spells",
    "proficiency_bonus",
    "spell_attack_bonus",
    "spell_save_dc",
    "derive",
]


# -- assembling a whole sheet -------------------------------------------------------


def _skill_abilities(compendium: Any) -> dict[str, str]:
    """Map each skill name to its ability, from ``skills.json``.

    Read rather than hardcoded: the corpus states it, and a homebrew skill should work
    without a code change.
    """
    out: dict[str, str] = {}
    for skill in compendium.all("skill"):
        ability = getattr(skill, "ability", None)
        if isinstance(ability, str) and ability in ABILITIES:
            out[str(skill.name).lower()] = ability
    return out


def _resolve_class(compendium: Any, entry: Any) -> Any | None:
    """Find the loaded class record for one of the character's class entries."""
    return compendium.find("class", entry.name, entry.source)


def _ability_breakdowns(character: PlayerCharacter) -> dict[str, AbilityBreakdown]:
    """Base scores plus every increase, kept separable for display.

    Species and background increases would come from the chosen records; until the
    choice engine resolves those (``services/chargen.py``), only improvements taken at
    an Ability Score Improvement are applied. The fields exist so the wizard can fill
    them without this shape changing.
    """
    out: dict[str, AbilityBreakdown] = {}
    for ability in ABILITIES:
        out[ability] = AbilityBreakdown(
            ability=ability,
            base=character.abilities.get(ability),
            improvement_bonus=character.improvement_total(ability),
        )
    return out


def derive(
    character: PlayerCharacter,
    compendium: Any | None = None,
    *,
    armor: EquippedArmor | None = None,
    proficient_skills: dict[str, str] | None = None,
) -> CharacterSheet:
    """Compute every derived value for ``character``.

    ``compendium`` supplies the data-driven parts — hit dice, the skill/ability map and
    the spellcasting progression. Without it the sheet still computes everything that
    depends only on the character's own choices, which is what the first wizard steps
    need before a class is picked.

    ``proficient_skills`` maps a skill name to what granted it, so the sheet can name
    the source rather than only the bonus.
    """
    level = character.total_level
    proficiency = proficiency_bonus(level) if level else 0
    abilities = _ability_breakdowns(character)

    sheet = CharacterSheet(
        level=level,
        proficiency_bonus=proficiency,
        abilities=abilities,
    )

    dex_modifier = sheet.modifier("dex")
    sheet.initiative = Derivation(dex_modifier, (f"DEX {dex_modifier:+d}",))
    sheet.armor_class = armor_class(armor or EquippedArmor(), dex_modifier)

    primary = character.primary_class
    class_record = _resolve_class(compendium, primary) if compendium and primary else None

    if class_record is not None:
        hd = getattr(class_record, "hd", None) or {}
        faces = hd.get("faces")
        if isinstance(faces, int):
            sheet.hit_dice = f"{level}d{faces}"
            sheet.hit_points = hit_points(level, faces, sheet.modifier("con"))

        # Unarmoured Defense, where the class grants it and no armour is worn.
        unarmored = str(primary.name).lower() if primary else None
        if unarmored in UNARMORED_DEFENSE and not (armor and armor.is_armored):
            second_ability, _ = UNARMORED_DEFENSE[unarmored]
            sheet.armor_class = armor_class(
                armor or EquippedArmor(),
                dex_modifier,
                unarmored_class=unarmored,
                second_modifier=sheet.modifier(second_ability),
            )

        saves = getattr(class_record, "proficiency", []) or []
        for ability in ABILITIES:
            sheet.saving_throws[ability] = check_bonus(
                sheet.modifier(ability),
                proficiency,
                proficient=ability in saves,
                label=ability.upper(),
            )

        casting_ability = getattr(class_record, "spellcastingAbility", None)
        if isinstance(casting_ability, str) and casting_ability in ABILITIES:
            modifier = sheet.modifier(casting_ability)
            sheet.spell_save_dc = spell_save_dc(proficiency, modifier)
            sheet.spell_attack_bonus = spell_attack_bonus(proficiency, modifier)

        formula = getattr(class_record, "preparedSpells", None)
        if isinstance(formula, str) and formula:
            sheet.prepared_spell_limit = evaluate_formula(formula, sheet)
    else:
        for ability in ABILITIES:
            sheet.saving_throws[ability] = check_bonus(
                sheet.modifier(ability), proficiency, label=ability.upper()
            )

    if compendium is not None:
        granted = {k.lower(): v for k, v in (proficient_skills or {}).items()}
        for name, ability in sorted(_skill_abilities(compendium).items()):
            source = granted.get(name)
            sheet.skills[name] = check_bonus(
                sheet.modifier(ability),
                proficiency,
                proficient=source is not None,
                label=ability.upper(),
            )
        perception = sheet.skills.get("perception")
        sheet.passive_perception = 10 + (perception.value if perception else 0)

    for prerequisite in character.confirmed_prerequisites:
        sheet.notes.append(f"Confirmed by the player: {prerequisite}")

    return sheet
