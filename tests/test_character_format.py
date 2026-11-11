"""The character file format: compact references, overrides, play state, the schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.pc import (
    LOG_LIMIT,
    ChosenOption,
    ClassLevel,
    LogEntry,
    PlayerCharacter,
    SpellSelection,
    character_json_schema,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "documentation" / "examples" / "lyra-mooncaller.json"
SCHEMA = ROOT / "documentation" / "character.schema.json"


class TestExample:
    def test_the_documented_example_is_valid(self) -> None:
        pc = PlayerCharacter.model_validate(json.loads(EXAMPLE.read_text()))
        assert pc.name == "Lyra Mooncaller"
        assert pc.total_level == 10
        assert pc.classes[0].subclass is not None
        assert pc.state.concentration is not None
        assert pc.state.concentration.reference == "Haste|PHB"

    def test_the_example_round_trips_unchanged(self) -> None:
        raw = json.loads(EXAMPLE.read_text())
        dumped = PlayerCharacter.model_validate(raw).model_dump(mode="json")
        assert dumped == raw

    @pytest.mark.parametrize("path", sorted(EXAMPLE.parent.glob("*.json")), ids=lambda p: p.stem)
    def test_every_example_is_valid_and_canonical(self, path: Path) -> None:
        raw = json.loads(path.read_text())
        assert PlayerCharacter.model_validate(raw).model_dump(mode="json") == raw

    def test_the_committed_schema_is_current(self) -> None:
        """Regenerate with ``python -m app.cli schema`` after changing the model."""
        assert json.loads(SCHEMA.read_text()) == character_json_schema()


class TestReferences:
    def test_string_form(self) -> None:
        option = ChosenOption.model_validate("Fireball|PHB")
        assert (option.name, option.source) == ("Fireball", "PHB")
        assert option.model_dump() == "Fireball|PHB"

    def test_bare_name(self) -> None:
        option = ChosenOption.model_validate("Arcana")
        assert option.source is None
        assert option.model_dump() == "Arcana"

    def test_object_form_still_accepted(self) -> None:
        assert (
            ChosenOption.model_validate({"name": "Shield", "source": "PHB"}).reference
            == "Shield|PHB"
        )

    def test_class_compact_form(self) -> None:
        cls = ClassLevel.model_validate(
            {"class": "Wizard|PHB", "level": 3, "subclass": "Evocation|PHB"}
        )
        assert (cls.name, cls.source, cls.level) == ("Wizard", "PHB", 3)
        assert cls.model_dump() == {"class": "Wizard|PHB", "level": 3, "subclass": "Evocation|PHB"}

    def test_class_without_subclass_omits_it(self) -> None:
        assert "subclass" not in ClassLevel.model_validate({"class": "Fighter|PHB"}).model_dump()


class TestSpells:
    def test_prepared_must_be_in_the_spellbook(self) -> None:
        with pytest.raises(ValidationError, match="prepared but not known"):
            PlayerCharacter.model_validate(
                {"spells": {"known": ["Shield"], "prepared": ["Fireball"]}}
            )

    def test_a_cleric_prepares_without_a_spellbook(self) -> None:
        selection = SpellSelection.model_validate({"prepared": ["Bless|PHB", "Cure Wounds|PHB"]})
        assert [o.name for o in selection.usable] == ["Bless", "Cure Wounds"]

    def test_usable_prefers_prepared_over_known(self) -> None:
        selection = SpellSelection.model_validate(
            {"cantrips": ["Light"], "known": ["Shield", "Sleep"], "prepared": ["Shield"]}
        )
        assert [o.name for o in selection.usable] == ["Light", "Shield"]

    def test_a_sorcerer_casts_what_they_know(self) -> None:
        selection = SpellSelection.model_validate({"known": ["Shield", "Sleep"]})
        assert [o.name for o in selection.usable] == ["Shield", "Sleep"]


class TestOverrides:
    def test_number_shorthand(self) -> None:
        pc = PlayerCharacter.model_validate({"overrides": {"ac": 17}})
        assert pc.override("ac") is not None
        assert pc.override("ac").value == 17  # type: ignore[union-attr]

    def test_keys_are_normalized(self) -> None:
        pc = PlayerCharacter.model_validate({"overrides": {" AC ": {"value": 15, "note": "Ring"}}})
        assert pc.override("ac").note == "Ring"  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        "key",
        [
            "ac",
            "hp_max",
            "save.dex",
            "ability.str",
            "skill.sleight of hand",
            "slots.3",
            "pact_slots",
        ],
    )
    def test_allowed(self, key: str) -> None:
        PlayerCharacter.model_validate({"overrides": {key: 1}})

    @pytest.mark.parametrize("key", ["armour", "save.luck", "slots.10", "hp"])
    def test_rejected(self, key: str) -> None:
        with pytest.raises(ValidationError, match="cannot override"):
            PlayerCharacter.model_validate({"overrides": {key: 1}})

    def test_no_derived_value_sits_at_the_top_level(self) -> None:
        """Derived numbers are only ever overrides, visibly, never plain fields."""
        assert {"ac", "hp_max", "spell_save_dc", "hp"} & set(PlayerCharacter.model_fields) == set()


class TestPlayState:
    def test_full_health_by_default(self) -> None:
        assert PlayerCharacter().state.hp is None

    def test_conditions_are_normalized(self) -> None:
        pc = PlayerCharacter.model_validate(
            {"state": {"conditions": ["Prone", " prone", "Poisoned"]}}
        )
        assert pc.state.conditions == ["prone", "poisoned"]

    @pytest.mark.parametrize(
        "bad", [{"hit_dice_used": {"6": 1}}, {"slots_used": {"10": 1}}, {"exhaustion": 7}]
    )
    def test_bad_state_is_refused(self, bad: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            PlayerCharacter.model_validate({"state": bad})

    def test_log_is_capped(self) -> None:
        entries = [{"kind": "custom", "subject": str(i)} for i in range(LOG_LIMIT + 10)]
        assert len(PlayerCharacter.model_validate({"log": entries}).log) == LOG_LIMIT

    def test_log_entries_are_language_neutral(self) -> None:
        entry = LogEntry.model_validate({"kind": "save", "subject": "dex", "total": 14})
        assert entry.model_dump(mode="json")["kind"] == "save"

    def test_schema_pointer_survives(self) -> None:
        pc = PlayerCharacter.model_validate({"$schema": "../character.schema.json"})
        assert pc.model_dump(mode="json")["$schema"] == "../character.schema.json"

    def test_unknown_fields_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            PlayerCharacter.model_validate({"hit_points": 42})
