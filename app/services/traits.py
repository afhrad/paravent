"""Features that change the sheet's numbers.

The sheet already knows which features a character has — read from the corpus by class
level, subclass, species, feats and chosen options (:mod:`app.services.sheet`). This
module gives the common ones their effect on the numbers, so a level 6 paladin's saves
include Aura of Protection without anyone writing an override.

Recognised by **name and source book**, never by class: a feature is in effect because
the character has it, and the same name can mean different things in the 2014 and 2024
books (Remarkable Athlete is half proficiency in one and advantage in the other).

Every effect is added to the value's explanation ("… + Aura of Protection +4"), and a
value overridden in the file is left exactly as stated — an override always wins.

The rules here are prose in the corpus (documentation/CHARGEN.md G2), so they are
written out; which character has them is data.
"""

from __future__ import annotations

import math
from dataclasses import replace

from app.services.character import HEAVY_ARMOR
from app.services.sheet import FeatureView, Sheet, SkillView, Value

# Unarmored Movement: monk level → extra speed (PHB and XPHB agree).
UNARMORED_MOVEMENT = ((18, 30), (14, 25), (10, 20), (6, 15), (2, 10))

PHYSICAL = frozenset({"str", "dex", "con"})


def _plus(value: Value, amount: int, label: str, source: str = "") -> Value:
    """``value`` raised by ``amount``, with the reason added to its explanation and its
    source (``feature:<label>`` unless named) to the parts the log records."""
    if value.overridden or value.value is None or amount == 0:
        return value
    part = f"{label} {amount:+d}"
    explanation = f"{value.explanation} + {part}" if value.explanation else part
    sources = (*value.sources, (source or f"feature:{label}", amount))
    return Value(value.value + amount, explanation, sources=sources)


def _book(feature: FeatureView | None) -> str:
    return (feature.source if feature else "").upper()


def _class_level(sheet: Sheet, name: str) -> int:
    return sheet.character.class_level(name)


def apply_traits(sheet: Sheet) -> None:
    """Apply every recognised feature's effect to ``sheet``, in place."""
    prof = sheet.proficiency.value or 0
    _items(sheet)
    _saves(sheet, prof)
    _checks(sheet, prof)
    _speed(sheet)
    _hit_points(sheet)
    _armor_class(sheet)


def _items(sheet: Sheet) -> None:
    """Worn magic items: a Cloak of Protection's saves, a wand's spell attack and DC,
    a Stone of Good Luck's checks. Before the checks' rules, so passive Perception
    follows a raised Perception."""

    def raised(value: Value, key: str) -> Value:
        for name, amount in sheet.item_bonuses.get(key, ()):
            value = _plus(value, amount, name, f"item:{name}")
        return value

    sheet.abilities = [replace(a, save=raised(a.save, "bonusSavingThrow")) for a in sheet.abilities]
    if sheet.spell_attack is not None:
        sheet.spell_attack = raised(sheet.spell_attack, "bonusSpellAttack")
    if sheet.spell_save_dc is not None:
        sheet.spell_save_dc = raised(sheet.spell_save_dc, "bonusSpellSaveDc")
    # Spells a feat or species grants are cast with their own ability, and the item's
    # bonus counts for them too (Magic Initiate's Fire Bolt with a Wand of the War Mage).
    sheet.granted_casting = {
        ability: (raised(dc, "bonusSpellSaveDc"), raised(attack, "bonusSpellAttack"))
        for ability, (dc, attack) in sheet.granted_casting.items()
    }
    if sheet.item_bonuses.get("bonusAbilityCheck"):
        sheet.skills = [
            replace(s, value=raised(s.value, "bonusAbilityCheck")) for s in sheet.skills
        ]
        sheet.initiative = raised(sheet.initiative, "bonusAbilityCheck")


def _saves(sheet: Sheet, prof: int) -> None:
    # Diamond Soul (2014) / Disciplined Survivor (2024): proficiency in every save.
    every = sheet.has_feature("Diamond Soul") or sheet.has_feature("Disciplined Survivor")
    aura = sheet.has_feature("Aura of Protection")
    cha = next((a.modifier for a in sheet.abilities if a.key == "cha"), 0)
    for index, ability in enumerate(sheet.abilities):
        save, proficient = ability.save, ability.save_proficient
        if every and not proficient:
            save, proficient = _plus(save, prof, every.name), True
        if aura:
            # The paladin's CHA modifier, minimum +1, to every saving throw.
            save = _plus(save, max(1, cha), "Aura of Protection")
        sheet.abilities[index] = replace(ability, save=save, save_proficient=proficient)


def _checks(sheet: Sheet, prof: int) -> None:
    """Half proficiency on unproficient checks, initiative, passive Perception."""
    jack = sheet.has_feature("Jack of All Trades")
    athlete = sheet.has_feature("Remarkable Athlete")
    # 2014 Champion: half proficiency, rounded up, on STR/DEX/CON checks. The 2024
    # feature of that name grants advantage instead, which the sheet does not model.
    athlete = athlete if _book(athlete) == "PHB" else None

    def half(ability: str) -> tuple[int, str] | None:
        options = []
        if jack:
            options.append((prof // 2, jack.name))
        if athlete and ability in PHYSICAL:
            options.append((math.ceil(prof / 2), athlete.name))
        return max(options) if options else None

    for index, skill in enumerate(sheet.skills):
        if skill.proficient:
            continue
        bonus = half(skill.ability)
        if bonus:
            sheet.skills[index] = SkillView(
                skill.key,
                skill.name,
                skill.ability,
                _plus(skill.value, bonus[0], bonus[1]),
                skill.proficient,
                skill.expertise,
            )

    # Initiative is a DEX check.
    initiative = sheet.initiative
    bonus = half("dex")
    if bonus:
        initiative = _plus(initiative, bonus[0], bonus[1])
    alert = sheet.has_feature("Alert", kind="feat")
    if alert:
        # 2014: +5. 2024: add your proficiency bonus.
        initiative = _plus(initiative, prof if _book(alert) == "XPHB" else 5, "Alert", "feat:Alert")
    sheet.initiative = initiative

    # Passive Perception follows the (possibly raised) Perception skill.
    perception = next((s for s in sheet.skills if s.key == "perception"), None)
    passive = sheet.passive_perception
    if not passive.overridden and perception is not None and perception.value.value is not None:
        passive = Value(10 + perception.value.value, f"10 + Perception {perception.value.value:+d}")
    observant = sheet.has_feature("Observant", kind="feat")
    if observant and _book(observant) == "PHB":
        passive = _plus(passive, 5, "Observant", "feat:Observant")
    sheet.passive_perception = passive


def _speed(sheet: Sheet) -> None:
    speed = sheet.speed
    unarmored = sheet.armor_type is None and not sheet.shield_bonus
    movement = sheet.has_feature("Unarmored Movement")
    monk = _class_level(sheet, "Monk")
    if movement and unarmored:
        extra = next((ft for level, ft in UNARMORED_MOVEMENT if monk >= level), 0)
        speed = _plus(speed, extra, "Unarmored Movement")
    fast = sheet.has_feature("Fast Movement")
    if fast and sheet.armor_type != HEAVY_ARMOR:
        speed = _plus(speed, 10, "Fast Movement")
    for feat in ("Mobile", "Speedy"):
        if sheet.has_feature(feat, kind="feat"):
            speed = _plus(speed, 10, feat, f"feat:{feat}")
    sheet.speed = speed


def _hit_points(sheet: Sheet) -> None:
    top = sheet.hp_max
    if top.overridden or top.value is None or top.explanation == "last saved maximum":
        return
    level = sheet.level
    if sheet.has_feature("Dwarven Toughness"):
        top = _plus(top, level, "Dwarven Toughness")
    if sheet.has_feature("Tough", kind="feat"):
        top = _plus(top, 2 * level, "Tough", "feat:Tough")
    if sheet.has_feature("Draconic Resilience"):
        # +1 per sorcerer level (2024: +3 at level 3, then +1 per level — the same sum).
        top = _plus(top, _class_level(sheet, "Sorcerer"), "Draconic Resilience")
    if top is sheet.hp_max:
        return
    sheet.hp_max = top
    maximum = top.value or 0
    current = sheet.character.state.hp
    sheet.hp_current = maximum if current is None else min(current, maximum)


def _armor_class(sheet: Sheet) -> None:
    ac = sheet.armor_class
    if ac.overridden or ac.value is None:
        return
    mods = {a.key: a.modifier for a in sheet.abilities}

    # Defense fighting style (a 2014 option, a 2024 feat): +1 while wearing armour.
    defense = sheet.has_feature("Defense", kind="option") or sheet.has_feature(
        "Defense", kind="feat"
    )
    if defense and sheet.armor_type is not None:
        ac = _plus(ac, 1, "Defense")

    # Unarmoured AC formulas granted by a subclass, used when they beat what is worn.
    # Each candidate carries its full explanation, shield included where allowed.
    if sheet.armor_type is None:
        shield = sheet.shield_bonus
        shield_part = f" + shield +{shield}" if shield else ""
        candidates: list[tuple[int, str]] = []
        draconic = sheet.has_feature("Draconic Resilience")
        if draconic and _book(draconic) == "XPHB":
            candidates.append(
                (
                    10 + mods["dex"] + mods["cha"] + shield,
                    f"10 + DEX + CHA (Draconic Resilience){shield_part}",
                )
            )
        elif draconic:
            candidates.append(
                (13 + mods["dex"] + shield, f"13 + DEX (Draconic Resilience){shield_part}")
            )
        dance = next(
            (
                f
                for f in sheet.features
                if f.name == "Unarmored Defense" and f.kind == "subclass" and f.owner == "Bard"
            ),
            None,
        )
        if dance and not shield:  # the Dance bard's defense needs no armour *and* no shield
            candidates.append(
                (10 + mods["dex"] + mods["cha"], "10 + DEX + CHA (Unarmored Defense)")
            )
        best = max(candidates, default=None)
        current = ac.value if ac.value is not None else 0
        if best is not None and best[0] > current:
            ac = Value(best[0], best[1])
    sheet.armor_class = ac


__all__ = ["apply_traits"]
