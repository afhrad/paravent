"""Tests for the player-character record and the ability-score constants.

The design claim under test is that the record stores **chosen options, not resolved
state** (documentation/CHARGEN.md §4). So these tests check that nothing derivable is
storable, that an invalid combination is rejected rather than merely discouraged, and
that a draft survives a round trip through disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.pc import (
    Abilities,
    AbilityMethod,
    ChosenOption,
    ClassLevel,
    DraftStatus,
    HitPoints,
    PlayerCharacter,
    SpellSelection,
)
from app.store.errors import InvalidEntry
from app.store.gamestate import GameStateStore

RULES = Path("data/rules/abilityscoregen.json")


class TestAbilityScoreConstants:
    """The hand-authored constants (documentation/CHARGEN.md G1).

    Checked against the printed rules because nothing in the corpus can verify them:
    the standard array and the budget are prose, and the cost table is a rendered table
    inside a prose entry.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def rules() -> dict:
        return json.loads(RULES.read_text())

    def test_the_file_exists(self) -> None:
        assert RULES.is_file(), "chargen step 1 cannot run without it"

    def test_all_four_methods_are_present(self, rules: dict) -> None:
        assert {m["id"] for m in rules["methods"]} == {
            "standard",
            "pointbuy",
            "roll",
            "manual",
        }

    def test_the_standard_array_matches_the_phb(self, rules: dict) -> None:
        standard = next(m for m in rules["methods"] if m["id"] == "standard")
        assert standard["values"] == [15, 14, 13, 12, 10, 8]

    def test_point_buy_matches_the_phb_cost_table(self, rules: dict) -> None:
        """Read from variantrules.json → "Customizing Ability Scores" → type:table."""
        pointbuy = next(m for m in rules["methods"] if m["id"] == "pointbuy")
        assert pointbuy["budget"] == 27
        assert pointbuy["cost"] == {
            "8": 0,
            "9": 1,
            "10": 2,
            "11": 3,
            "12": 4,
            "13": 5,
            "14": 7,
            "15": 9,
        }

    def test_a_full_point_buy_spend_is_affordable(self, rules: dict) -> None:
        """27 points must buy a legal spread, or the budget is wrong.

        15/15/15/8/8/8 costs 27 exactly — the canonical maximum-spend check.
        """
        pointbuy = next(m for m in rules["methods"] if m["id"] == "pointbuy")
        cost = pointbuy["cost"]
        spend = 3 * cost["15"] + 3 * cost["8"]
        assert spend == pointbuy["budget"]

    def test_point_buy_range_is_eight_to_fifteen(self, rules: dict) -> None:
        pointbuy = next(m for m in rules["methods"] if m["id"] == "pointbuy")
        assert (pointbuy["min"], pointbuy["max"]) == (8, 15)
        assert min(map(int, pointbuy["cost"])) == 8
        assert max(map(int, pointbuy["cost"])) == 15

    def test_the_roll_method_is_4d6_drop_lowest(self, rules: dict) -> None:
        roll = next(m for m in rules["methods"] if m["id"] == "roll")
        assert (roll["dice"], roll["drop_lowest"], roll["rolls"]) == ("4d6", 1, 6)

    def test_provenance_is_recorded(self, rules: dict) -> None:
        """Hand-authored data must say where it came from, or it cannot be rechecked."""
        assert rules["_meta"]["sources"]
        assert "PHB" in {s["source"] for s in rules["_meta"]["sources"]}


class TestAbilities:
    """Base scores only — increases are themselves stored choices."""

    def test_upstream_keys_are_accepted(self) -> None:
        """``str`` and ``int`` are Python builtins, so the fields are aliased."""
        abilities = Abilities.model_validate({"str": 8, "int": 15})
        assert abilities.get("str") == 8
        assert abilities.get("int") == 15

    def test_as_dict_uses_corpus_keys(self) -> None:
        abilities = Abilities.model_validate({"str": 8, "dex": 16})
        assert abilities.as_dict()["str"] == 8
        assert abilities.as_dict()["dex"] == 16
        assert abilities.as_dict()["wis"] is None

    def test_incomplete_until_all_six_are_set(self) -> None:
        assert not Abilities.model_validate({"str": 8}).is_complete
        full = Abilities.model_validate(
            {"str": 8, "dex": 16, "con": 14, "int": 15, "wis": 12, "cha": 10}
        )
        assert full.is_complete

    @pytest.mark.parametrize("score", [0, 31, -1])
    def test_out_of_range_scores_are_rejected(self, score: int) -> None:
        with pytest.raises(ValidationError):
            Abilities.model_validate({"dex": score})

    @pytest.mark.parametrize("score", [1, 8, 20, 30])
    def test_the_full_legal_range_is_accepted(self, score: int) -> None:
        """1–30 is the rule ceiling; narrower limits belong to the method."""
        assert Abilities.model_validate({"dex": score}).get("dex") == score


class TestChosenOption:
    """A choice records what was picked, including its book."""

    def test_reference_includes_the_source(self) -> None:
        assert ChosenOption(name="Fireball", source="PHB").reference == "Fireball|PHB"

    def test_a_sourceless_choice_is_a_bare_name(self) -> None:
        assert ChosenOption(name="Fireball").reference == "Fireball"


class TestSpellSelection:
    """Known and prepared are different things, and conflating them is the classic bug."""

    def test_prepared_must_come_from_known(self) -> None:
        with pytest.raises(ValidationError, match="prepared but not known"):
            PlayerCharacter(
                spells=SpellSelection(
                    known=[ChosenOption(name="Shield")],
                    prepared=[ChosenOption(name="Fireball")],
                )
            )

    def test_a_class_list_preparer_records_its_spells(self) -> None:
        pc = PlayerCharacter(
            spells=SpellSelection(
                known=[ChosenOption(name="Shield")],
                prepared=[ChosenOption(name="Shield"), ChosenOption(name="Bless")],
            ),
            choices={"prepared.Wizard": ["Shield"], "prepared.Cleric": ["Bless"]},
        )
        assert [o.name for o in pc.spells.prepared] == ["Shield", "Bless"]

    def test_cantrips_are_exempt(self) -> None:
        """Cantrips need no preparation, so they are never in the prepared list."""
        selection = SpellSelection(
            cantrips=[ChosenOption(name="Fire Bolt")],
            known=[ChosenOption(name="Shield")],
            prepared=[ChosenOption(name="Shield")],
        )
        assert selection.cantrips[0].name == "Fire Bolt"

    def test_matching_is_case_insensitive(self) -> None:
        SpellSelection(
            known=[ChosenOption(name="Shield")], prepared=[ChosenOption(name="shield")]
        )

    def test_a_wizard_knows_more_than_it_prepares(self) -> None:
        """24 in the spellbook, 13 prepared — the shape the rules actually produce."""
        known = [ChosenOption(name=f"Spell {i}") for i in range(24)]
        selection = SpellSelection(known=known, prepared=known[:13])
        assert len(selection.known) == 24
        assert len(selection.prepared) == 13


class TestPlayerCharacter:
    """The record itself."""

    def test_a_new_character_is_a_draft(self) -> None:
        """A partly built character is a real, saved thing."""
        assert PlayerCharacter().status is DraftStatus.DRAFT

    def test_level_sums_across_classes(self) -> None:
        pc = PlayerCharacter(
            classes=[
                ClassLevel(name="Wizard", source="PHB", level=8),
                ClassLevel(name="Rogue", source="PHB", level=2),
            ]
        )
        assert pc.total_level == 10
        assert pc.is_multiclassed
        assert pc.class_level("wizard") == 8
        assert pc.class_level("cleric") == 0

    def test_primary_class_is_the_first_taken(self) -> None:
        pc = PlayerCharacter(
            classes=[
                ClassLevel(name="Wizard", source="PHB"),
                ClassLevel(name="Rogue", source="PHB"),
            ]
        )
        assert pc.primary_class is not None
        assert pc.primary_class.reference == "Wizard|PHB"

    def test_an_empty_character_has_no_level(self) -> None:
        pc = PlayerCharacter()
        assert pc.total_level == 0
        assert pc.primary_class is None

    def test_subclass_stays_unset_until_chosen(self) -> None:
        """The level at which it is granted varies by class and is read from the data."""
        assert ClassLevel(name="Wizard", source="PHB", level=1).subclass is None

    def test_improvements_accumulate_per_ability(self) -> None:
        pc = PlayerCharacter(ability_improvements=[{"int": 2}, {"int": 1, "dex": 1}])
        assert pc.improvement_total("int") == 3
        assert pc.improvement_total("dex") == 1
        assert pc.improvement_total("cha") == 0

    def test_an_improvement_must_name_a_real_ability(self) -> None:
        with pytest.raises(ValidationError, match="not an ability"):
            PlayerCharacter(ability_improvements=[{"luck": 2}])

    @pytest.mark.parametrize("level", [0, 21])
    def test_class_level_bounds(self, level: int) -> None:
        with pytest.raises(ValidationError):
            ClassLevel(name="Wizard", source="PHB", level=level)

    def test_spellcaster_follows_the_chosen_spells(self) -> None:
        assert not PlayerCharacter().is_spellcaster
        pc = PlayerCharacter(
            spells=SpellSelection(cantrips=[ChosenOption(name="Fire Bolt")])
        )
        assert pc.is_spellcaster

    def test_unknown_fields_are_rejected(self) -> None:
        """Library models are strict: an unknown field is a bug, not data to preserve."""
        with pytest.raises(ValidationError):
            PlayerCharacter(hit_points=42)

    def test_no_derived_field_exists_to_corrupt(self) -> None:
        """The design claim, enforced: nothing derivable is storable.

        HP, AC, proficiency bonus and spell save DC all come from the choices via
        services/character.py, so there is no field in which a stale number could sit.
        """
        fields = set(PlayerCharacter.model_fields)
        # ``hit_points`` exists, but holds the *choice* (average or the rolls), never
        # a maximum or a current value.
        assert set(HitPoints.model_fields) == {"method", "per_level"}
        derived = {
            "hp",
            "hp_max",
            "max_hp",
            "ac",
            "armor_class",
            "proficiency_bonus",
            "spell_save_dc",
            "initiative",
            "passive_perception",
        }
        assert fields & derived == set()


class TestPersistence:
    """A character is written to its own file in library/characters/ and resumes."""

    def test_characters_are_a_registered_collection(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path)
        assert "characters" in store.collections()

    def test_a_draft_survives_a_round_trip(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path)
        created = store.characters.create(
            PlayerCharacter(
                name="Lyra Mooncaller",
                ability_method=AbilityMethod.STANDARD,
                classes=[ClassLevel(name="Wizard", source="PHB", level=1)],
                abilities=Abilities.model_validate(
                    {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10}
                ),
            )
        )

        reopened = GameStateStore(tmp_path).characters.get(created.id)
        assert reopened.name == "Lyra Mooncaller"
        assert reopened.total_level == 1
        assert reopened.abilities.get("int") == 15
        assert reopened.ability_method is AbilityMethod.STANDARD
        assert reopened.status is DraftStatus.DRAFT

    def test_the_file_lands_in_the_library(self, tmp_path: Path) -> None:
        """One file per character, named so a person can find it."""
        store = GameStateStore(tmp_path)
        created = store.characters.create(PlayerCharacter(name="Lyra Mooncaller"))
        assert (tmp_path / "characters" / f"lyra-mooncaller-{created.id}.json").is_file()
        assert not (tmp_path / "characters.json").exists()

    def test_an_invalid_update_is_refused_before_it_reaches_disk(
        self, tmp_path: Path
    ) -> None:
        store = GameStateStore(tmp_path)
        created = store.characters.create(PlayerCharacter(name="X"))
        with pytest.raises(InvalidEntry):
            store.characters.update(created.id, ability_improvements=[{"luck": 2}])
        assert GameStateStore(tmp_path).characters.get(created.id).ability_improvements == []

    def test_abilities_serialize_under_the_upstream_keys(self, tmp_path: Path) -> None:
        """So a hand-edited character file reads the way the corpus does."""
        store = GameStateStore(tmp_path)
        created = store.characters.create(
            PlayerCharacter(
                name="X", abilities=Abilities.model_validate({"str": 8, "int": 15})
            )
        )
        path = store.characters.path(created.id)
        assert path is not None
        stored = json.loads(path.read_text())["abilities"]
        assert "str" in stored and "int" in stored
        assert "str_" not in stored and "int_" not in stored
