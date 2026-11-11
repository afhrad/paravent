"""Tests for derived statistics (documentation/CHARGEN.md G2).

These formulas exist in the corpus only as English sentences, so there is no data to
validate them against — they are checked against the printed rules instead, with the
arithmetic spelled out in each case so a reader can see what is being claimed.

One number worth recording: the design-reference character sheet shows a level-10 wizard
with 52 HP, and an earlier note in this project repeated that figure for a CON-14 build.
52 is right for **CON 13**; with CON 14 the answer is 62. The expectations below are
computed from the rules, not copied from that page.
"""

from __future__ import annotations

import pytest

from app.models.pc import Abilities, ClassLevel, PlayerCharacter
from app.services.character import (
    AbilityBreakdown,
    CharacterSheet,
    Derivation,
    EquippedArmor,
    ability_modifier,
    armor_class,
    check_bonus,
    derive,
    evaluate_formula,
    hit_points,
    proficiency_bonus,
    spell_attack_bonus,
    spell_save_dc,
)
from app.store.compendium import CompendiumStore
from tests.conftest import requires_corpus


class TestAbilityModifier:
    """``floor((score - 10) / 2)``, including the negative side."""

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(1, -5), (3, -4), (8, -1), (9, -1), (10, 0), (11, 0), (12, 1), (20, 5), (30, 10)],
    )
    def test_modifier(self, score: int, expected: int) -> None:
        assert ability_modifier(score) == expected

    def test_an_unset_score_is_treated_as_ten(self) -> None:
        """A half-built character must still render a sheet rather than raise."""
        assert ability_modifier(None) == 0


class TestProficiencyBonus:
    """``ceil(level / 4) + 1`` — +2 at 1st, +6 at 17th."""

    @pytest.mark.parametrize(
        ("level", "expected"),
        [(1, 2), (4, 2), (5, 3), (8, 3), (9, 4), (12, 4), (13, 5), (16, 5), (17, 6), (20, 6)],
    )
    def test_bonus_by_level(self, level: int, expected: int) -> None:
        assert proficiency_bonus(level) == expected

    def test_level_zero_has_no_bonus(self) -> None:
        assert proficiency_bonus(0) == 0


class TestHitPoints:
    """Full die at 1st level, the average thereafter, CON applied to every level."""

    @pytest.mark.parametrize(
        ("level", "faces", "con", "expected"),
        [
            (1, 10, 3, 13),  # fighter L1: 10 + 3
            (5, 10, 3, 49),  # + 4 × (6 avg + 3)
            (1, 6, 0, 6),  # wizard L1, CON 10
            (10, 6, 2, 62),  # 8 + 9 × (4 + 2) — not 52; that figure assumes CON 13
            (20, 6, 2, 122),
            (3, 12, 4, 38),  # barbarian: 16 + 2 × (7 + 4)
        ],
    )
    def test_against_the_printed_rules(
        self, level: int, faces: int, con: int, expected: int
    ) -> None:
        assert hit_points(level, faces, con).value == expected

    def test_a_con_increase_applies_to_every_level(self) -> None:
        """Raising CON raises HP retroactively, which is why the modifier is a parameter."""
        before = hit_points(10, 6, 2).value
        after = hit_points(10, 6, 3).value
        assert after - before == 10

    def test_hp_never_drops_below_one(self) -> None:
        """A punishing CON must not produce a character who is dead on creation."""
        assert hit_points(1, 6, -5).value == 1

    def test_the_derivation_is_explained(self) -> None:
        derivation = hit_points(5, 10, 3)
        assert "d10" in derivation.explanation
        assert "CON" in derivation.explanation


class TestArmorClass:
    """Unarmoured, armoured and the two Unarmoured Defense variants."""

    def test_unarmoured_is_ten_plus_dex(self) -> None:
        assert armor_class(EquippedArmor(), 3).value == 13

    def test_a_shield_adds_without_armour(self) -> None:
        assert armor_class(EquippedArmor(shield_ac=2), 3).value == 15

    def test_light_armour_adds_full_dex(self) -> None:
        armor = EquippedArmor(name="Leather", armor_type="LA", base_ac=11)
        assert armor_class(armor, 4).value == 15

    def test_medium_armour_caps_dex_at_two(self) -> None:
        armor = EquippedArmor(name="Half Plate", armor_type="MA", base_ac=15)
        assert armor_class(armor, 4).value == 17
        assert "capped" in armor_class(armor, 4).explanation

    def test_medium_armour_below_the_cap_uses_the_real_modifier(self) -> None:
        armor = EquippedArmor(name="Hide", armor_type="MA", base_ac=12)
        assert armor_class(armor, 1).value == 13

    def test_heavy_armour_ignores_dex_entirely(self) -> None:
        """Including a *negative* DEX, which heavy armour also ignores."""
        armor = EquippedArmor(name="Chain Mail", armor_type="HA", base_ac=16)
        assert armor_class(armor, 4).value == 16
        assert armor_class(armor, -1).value == 16

    def test_heavy_armour_with_a_shield(self) -> None:
        armor = EquippedArmor(name="Chain Mail", armor_type="HA", base_ac=16, shield_ac=2)
        assert armor_class(armor, 3).value == 18

    def test_barbarian_unarmoured_defense_adds_con_and_allows_a_shield(self) -> None:
        result = armor_class(
            EquippedArmor(shield_ac=2), 2, unarmored_class="barbarian", second_modifier=4
        )
        assert result.value == 18
        assert "Unarmored Defense" in result.explanation

    def test_monk_unarmoured_defense_adds_wis_and_refuses_a_shield(self) -> None:
        """The Monk's version, unlike the Barbarian's, does not permit a shield."""
        result = armor_class(
            EquippedArmor(shield_ac=2), 3, unarmored_class="monk", second_modifier=3
        )
        assert result.value == 16
        assert "shield" not in result.explanation

    def test_armour_overrides_unarmoured_defense(self) -> None:
        """The feature applies only while no body armour is worn."""
        armor = EquippedArmor(name="Chain Mail", armor_type="HA", base_ac=16)
        result = armor_class(armor, 2, unarmored_class="barbarian", second_modifier=4)
        assert result.value == 16

    def test_a_shield_alone_is_not_armour(self) -> None:
        assert not EquippedArmor(shield_ac=2).is_armored


class TestSpellcastingNumbers:
    """DC and attack bonus."""

    def test_save_dc(self) -> None:
        assert spell_save_dc(4, 5).value == 17

    def test_attack_bonus(self) -> None:
        assert spell_attack_bonus(4, 5).value == 9

    def test_the_dc_derivation_names_its_parts(self) -> None:
        assert spell_save_dc(4, 5).explanation == "8 + prof +4 + ability +5"


class TestCheckBonus:
    """Saves, skills and ability checks, with expertise."""

    def test_without_proficiency_it_is_the_modifier_alone(self) -> None:
        assert check_bonus(3, 4).value == 3

    def test_proficiency_is_added_once(self) -> None:
        assert check_bonus(3, 4, proficient=True).value == 7

    def test_expertise_doubles_the_bonus(self) -> None:
        assert check_bonus(3, 4, expertise=True).value == 11

    def test_expertise_implies_proficiency(self) -> None:
        """A character cannot have expertise in something they are not proficient in."""
        assert check_bonus(3, 4, proficient=False, expertise=True).value == 11


class TestFormulaEvaluator:
    """``preparedSpells`` is a real formula string, not prose."""

    def _sheet(self, level: int, int_score: int) -> CharacterSheet:
        return CharacterSheet(
            level=level,
            proficiency_bonus=proficiency_bonus(level),
            abilities={"int": AbilityBreakdown(ability="int", base=int_score)},
        )

    def test_the_wizard_formula(self) -> None:
        """``"<$level$> + <$int_mod$>"`` at level 10 with INT 20 is 15."""
        assert evaluate_formula("<$level$> + <$int_mod$>", self._sheet(10, 20)) == 15

    def test_a_negative_modifier_subtracts(self) -> None:
        assert evaluate_formula("<$level$> + <$int_mod$>", self._sheet(5, 8)) == 4

    def test_score_tokens_resolve(self) -> None:
        assert evaluate_formula("<$int_score$>", self._sheet(1, 20)) == 20

    def test_a_bare_number_evaluates(self) -> None:
        assert evaluate_formula("4", self._sheet(1, 10)) == 4

    def test_subtraction_is_supported(self) -> None:
        assert evaluate_formula("<$level$> - 2", self._sheet(10, 10)) == 8

    def test_an_unknown_token_returns_none(self) -> None:
        """Better no answer than a wrong spell count."""
        assert evaluate_formula("<$luck_mod$>", self._sheet(10, 20)) is None

    def test_unsupported_arithmetic_returns_none(self) -> None:
        """Multiplication does not occur in the corpus and is not guessed at."""
        assert evaluate_formula("<$level$> * 2", self._sheet(10, 20)) is None

    def test_no_arbitrary_expression_is_evaluated(self) -> None:
        """The evaluator is a restricted grammar, not eval()."""
        assert evaluate_formula("__import__('os').getcwd()", self._sheet(1, 10)) is None


class TestAbilityBreakdown:
    """Base, bonus and total stay separable for display."""

    def test_total_sums_every_increase(self) -> None:
        breakdown = AbilityBreakdown(
            ability="int", base=15, species_bonus=1, improvement_bonus=4
        )
        assert breakdown.total == 20
        assert breakdown.bonus == 5
        assert breakdown.modifier == 5

    def test_an_unset_base_has_no_total(self) -> None:
        assert AbilityBreakdown(ability="int").total is None

    def test_sources_are_named_for_display(self) -> None:
        breakdown = AbilityBreakdown(ability="int", base=15, species_bonus=1)
        assert breakdown.sources == ("species +1",)


class TestDerivation:
    """A value that carries its reasoning."""

    def test_it_stands_in_for_a_number(self) -> None:
        assert int(Derivation(17, ("8", "prof +4"))) == 17

    def test_a_bare_value_explains_as_itself(self) -> None:
        assert Derivation(5).explanation == "5"


class TestDeriveWithoutACompendium:
    """The first wizard steps run before a class is chosen."""

    def test_an_empty_character_still_produces_a_sheet(self) -> None:
        sheet = derive(PlayerCharacter())
        assert sheet.level == 0
        assert sheet.proficiency_bonus == 0
        assert sheet.hit_points is None

    def test_ac_and_initiative_need_only_dex(self) -> None:
        character = PlayerCharacter(abilities=Abilities.model_validate({"dex": 16}))
        sheet = derive(character)
        assert sheet.armor_class is not None and sheet.armor_class.value == 13
        assert sheet.initiative is not None and sheet.initiative.value == 3

    def test_improvements_reach_the_breakdown(self) -> None:
        character = PlayerCharacter(
            abilities=Abilities.model_validate({"int": 16}),
            ability_improvements=[{"int": 2}, {"int": 2}],
        )
        sheet = derive(character)
        assert sheet.score("int") == 20
        assert sheet.modifier("int") == 5

    def test_confirmed_prerequisites_become_notes(self) -> None:
        """Prose prerequisites the engine cannot check are surfaced, not dropped."""
        character = PlayerCharacter(confirmed_prerequisites=["Member of a knightly order"])
        assert derive(character).notes


@requires_corpus
class TestDeriveAgainstTheRealCorpus:
    """Hit dice, saves and the casting progression are read from the data."""

    def _wizard(self) -> PlayerCharacter:
        return PlayerCharacter(
            name="Lyra Mooncaller",
            classes=[ClassLevel(name="Wizard", source="PHB", level=10)],
            abilities=Abilities.model_validate(
                {"str": 8, "dex": 16, "con": 14, "int": 20, "wis": 12, "cha": 10}
            ),
        )

    def test_the_level_ten_wizard_sheet(self, loaded_compendium: CompendiumStore) -> None:
        sheet = derive(self._wizard(), loaded_compendium)
        assert sheet.proficiency_bonus == 4
        assert sheet.hit_dice == "10d6"
        assert sheet.hit_points is not None and sheet.hit_points.value == 62
        assert sheet.armor_class is not None and sheet.armor_class.value == 13
        assert sheet.spell_save_dc is not None and sheet.spell_save_dc.value == 17
        assert sheet.spell_attack_bonus is not None
        assert sheet.spell_attack_bonus.value == 9

    def test_the_prepared_limit_comes_from_the_class_formula(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """Evaluated from ``class.preparedSpells``, never hardcoded per class."""
        sheet = derive(self._wizard(), loaded_compendium)
        assert sheet.prepared_spell_limit == 15

    def test_save_proficiencies_come_from_the_class_record(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """A PHB wizard is proficient in INT and WIS saves, and nothing else."""
        sheet = derive(self._wizard(), loaded_compendium)
        assert sheet.saving_throws["int"].value == 9  # INT +5, prof +4
        assert sheet.saving_throws["wis"].value == 5  # WIS +1, prof +4
        assert sheet.saving_throws["dex"].value == 3  # DEX +3, no proficiency

    def test_skills_read_their_ability_from_the_corpus(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        sheet = derive(
            self._wizard(), loaded_compendium, proficient_skills={"Arcana": "Sage"}
        )
        assert sheet.skills["arcana"].value == 9  # INT +5, prof +4
        assert sheet.skills["athletics"].value == -1  # STR -1, no proficiency

    def test_passive_perception_follows_the_skill(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        sheet = derive(self._wizard(), loaded_compendium)
        assert sheet.passive_perception == 11  # 10 + WIS +1
        proficient = derive(
            self._wizard(), loaded_compendium, proficient_skills={"Perception": "Elf"}
        )
        assert proficient.passive_perception == 15  # 10 + WIS +1 + prof +4

    @pytest.mark.parametrize(
        ("class_name", "level", "con", "expected"),
        [
            ("Fighter", 1, 16, 13),
            ("Fighter", 5, 16, 49),
            ("Wizard", 20, 14, 122),
            ("Barbarian", 3, 18, 38),
        ],
    )
    def test_hit_dice_are_read_per_class(
        self,
        loaded_compendium: CompendiumStore,
        class_name: str,
        level: int,
        con: int,
        expected: int,
    ) -> None:
        character = PlayerCharacter(
            classes=[ClassLevel(name=class_name, source="PHB", level=level)],
            abilities=Abilities.model_validate({"con": con}),
        )
        sheet = derive(character, loaded_compendium)
        assert sheet.hit_points is not None
        assert sheet.hit_points.value == expected

    def test_barbarian_unarmoured_defense_applies_end_to_end(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        character = PlayerCharacter(
            classes=[ClassLevel(name="Barbarian", source="PHB", level=3)],
            abilities=Abilities.model_validate({"dex": 14, "con": 18}),
        )
        sheet = derive(
            character, loaded_compendium, armor=EquippedArmor(shield_ac=2)
        )
        assert sheet.armor_class is not None and sheet.armor_class.value == 18

    def test_monk_unarmoured_defense_applies_end_to_end(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        character = PlayerCharacter(
            classes=[ClassLevel(name="Monk", source="PHB", level=3)],
            abilities=Abilities.model_validate({"dex": 16, "wis": 16}),
        )
        sheet = derive(
            character, loaded_compendium, armor=EquippedArmor(shield_ac=2)
        )
        assert sheet.armor_class is not None and sheet.armor_class.value == 16

    def test_a_non_caster_gets_no_spell_numbers(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        character = PlayerCharacter(
            classes=[ClassLevel(name="Fighter", source="PHB", level=5)],
            abilities=Abilities.model_validate({"con": 14}),
        )
        sheet = derive(character, loaded_compendium)
        assert sheet.spell_save_dc is None
        assert sheet.prepared_spell_limit is None

    def test_a_spells_known_caster_has_a_dc_but_no_prepared_limit(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """A sorcerer knows spells rather than preparing them, so the formula is absent."""
        character = PlayerCharacter(
            classes=[ClassLevel(name="Sorcerer", source="PHB", level=5)],
            abilities=Abilities.model_validate({"cha": 16, "con": 14}),
        )
        sheet = derive(character, loaded_compendium)
        assert sheet.spell_save_dc is not None
        assert sheet.prepared_spell_limit is None
