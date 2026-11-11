"""Tests for the 5eTools data models.

The value of these tests is that they run against the *real* corpus. Synthetic fixtures
would pass trivially — every field shape here was discovered by profiling the actual
data, and it is the corpus that keeps regressing them honest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.compendium import Item, Monster, Race, Spell
from app.models.values import ArmorClass, format_alignment, format_size
from app.store.compendium import CATEGORIES, CompendiumStore
from tests.conftest import requires_corpus


class TestPolymorphicFields:
    """Fields that arrive in more than one shape in the real corpus."""

    def test_ac_accepts_bare_int(self) -> None:
        monster = Monster.model_validate({"name": "X", "source": "S", "ac": [12]})
        assert monster.ac == [12]

    def test_ac_accepts_object_with_source(self) -> None:
        monster = Monster.model_validate(
            {"name": "X", "source": "S", "ac": [{"ac": 13, "from": ["natural armor"]}]}
        )
        entry = monster.ac[0]
        assert isinstance(entry, ArmorClass)
        assert entry.display == "13 (natural armor)"

    def test_ac_accepts_prose_special_form(self) -> None:
        """Summoned creatures scale with the spell slot and carry prose, not a number."""
        monster = Monster.model_validate(
            {"name": "X", "source": "S", "ac": [{"special": "11 + the level of the spell"}]}
        )
        entry = monster.ac[0]
        assert isinstance(entry, ArmorClass)
        assert entry.ac is None
        assert entry.display == "11 + the level of the spell"

    def test_cr_accepts_string_and_object(self) -> None:
        assert Monster.model_validate({"name": "X", "source": "S", "cr": "1/4"}).cr_value == "1/4"
        lair = Monster.model_validate(
            {"name": "X", "source": "S", "cr": {"cr": "15", "lair": "16", "xp": 13000}}
        )
        assert lair.cr_value == "15"

    def test_type_accepts_string_and_tagged_object(self) -> None:
        assert (
            Monster.model_validate({"name": "X", "source": "S", "type": "elemental"}).type_name
            == "elemental"
        )
        tagged = Monster.model_validate(
            {"name": "X", "source": "S", "type": {"type": "humanoid", "tags": ["goblinoid"]}}
        )
        assert tagged.type_name == "humanoid"

    def test_damage_lists_may_mix_strings_and_objects(self) -> None:
        """``resist`` mixes bare types with conditional objects inside one list."""
        monster = Monster.model_validate(
            {
                "name": "X",
                "source": "S",
                "resist": [
                    "psychic",
                    {"resist": ["bludgeoning"], "note": "from nonmagical attacks", "cond": True},
                ],
            }
        )
        assert monster.resist[0] == "psychic"
        assert isinstance(monster.resist[1], dict)


class TestBuiltinShadowing:
    """D&D names fields exactly like Python builtins; the aliases must hold."""

    def test_ability_scores_use_json_keys(self) -> None:
        monster = Monster.model_validate(
            {"name": "X", "source": "S", "str": 8, "dex": 14, "int": 10}
        )
        assert monster.str_ == 8
        assert monster.int_ == 10
        assert monster.abilities["str"] == 8
        assert monster.abilities["cha"] is None

    def test_item_weapon_properties_use_json_key(self) -> None:
        item = Item.model_validate({"name": "X", "source": "S", "property": ["F", "L"]})
        assert item.properties == ["F", "L"]

    def test_copy_directive_uses_json_key(self) -> None:
        monster = Monster.model_validate(
            {"name": "X", "source": "S", "_copy": {"name": "Y", "source": "S"}}
        )
        assert monster.is_unresolved_copy
        assert monster.copy_from is not None
        assert monster.copy_from.name == "Y"


class TestNullHandling:
    """5eTools writes explicit nulls to delete inherited fields."""

    @pytest.mark.parametrize("field", ["action", "trait", "languages", "senses", "spellcasting"])
    def test_explicit_null_becomes_empty(self, field: str) -> None:
        monster = Monster.model_validate({"name": "X", "source": "S", field: None})
        assert getattr(monster, field) == []


class TestDerivedProperties:
    def test_key_is_case_insensitive(self) -> None:
        assert Monster.model_validate({"name": "Goblin", "source": "MM"}).key == "goblin|mm"

    def test_size_and_alignment_expand(self) -> None:
        monster = Monster.model_validate(
            {"name": "X", "source": "S", "size": ["L"], "alignment": ["C", "E"]}
        )
        assert monster.size_label == "Large"
        assert monster.alignment_label == "chaotic evil"

    def test_alignment_handles_weighted_choice(self) -> None:
        assert "(75%)" in format_alignment([{"alignment": ["N", "E"], "chance": 75}])

    def test_format_helpers_tolerate_empty(self) -> None:
        assert format_size(None) == ""
        assert format_alignment([]) == ""

    def test_spell_flags(self) -> None:
        spell = Spell.model_validate(
            {
                "name": "Bless",
                "source": "PHB",
                "level": 1,
                "school": "E",
                "meta": {"ritual": True},
                "duration": [{"type": "timed", "concentration": True}],
            }
        )
        assert spell.is_ritual
        assert spell.is_concentration
        assert not spell.is_cantrip

    def test_cantrip_is_level_zero(self) -> None:
        spell = Spell.model_validate({"name": "Light", "source": "PHB", "level": 0, "school": "E"})
        assert spell.is_cantrip

    def test_item_attunement(self) -> None:
        assert Item.model_validate(
            {"name": "X", "source": "S", "reqAttune": True}
        ).requires_attunement
        assert Item.model_validate(
            {"name": "X", "source": "S", "reqAttune": "by a spellcaster"}
        ).requires_attunement
        assert not Item.model_validate({"name": "X", "source": "S"}).requires_attunement

    def test_unnamed_subrace_takes_parent_name(self) -> None:
        """Five subraces are the unnamed base variant of their parent race."""
        race = Race.model_validate({"source": "PHB", "raceName": "Dragonborn", "raceSource": "PHB"})
        assert race.name == "Dragonborn"
        assert race.is_base_variant


class TestLossless:
    """Unknown upstream fields must survive, not be dropped."""

    def test_unknown_fields_are_preserved(self) -> None:
        monster = Monster.model_validate(
            {"name": "X", "source": "S", "somethingNewInThe2027PHB": {"a": 1}}
        )
        assert monster.extra_fields()["somethingNewInThe2027PHB"] == {"a": 1}

    def test_required_identity_is_still_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Monster.model_validate({"source": "MM"})


@requires_corpus
class TestAgainstRealCorpus:
    """The models must survive the entire imported corpus without a single failure."""

    def test_every_category_loads_without_error(self, loaded_compendium: CompendiumStore) -> None:
        report = loaded_compendium.report
        assert report.errors == [], f"validation failures: {report.errors[:5]}"

    def test_every_copy_directive_resolves(self, loaded_compendium: CompendiumStore) -> None:
        report = loaded_compendium.report
        assert report.unresolved_copies == []

    def test_no_unknown_copy_modes(self, loaded_compendium: CompendiumStore) -> None:
        """An unhandled mode silently produces a wrong statblock, so fail loudly."""
        assert loaded_compendium.report.unknown_copy_modes == set()

    def test_all_declared_categories_have_entries(self, loaded_compendium: CompendiumStore) -> None:
        empty = [c.name for c in CATEGORIES if loaded_compendium.count(c.name) == 0]
        assert empty == [], f"categories loaded nothing: {empty}"

    def test_strict_mode_also_passes(self, corpus: Path) -> None:
        """Strict mode raises on the first bad entry; reaching the end proves cleanliness."""
        CompendiumStore(strict=True).load(corpus)


class TestSerialisation:
    """A dump must use the upstream JSON key names, not our Python-safe aliases.

    Aliased fields (``str``/``int`` ability scores, ``property`` on items) would
    otherwise serialise as ``str_``/``int_``/``properties``, which no 5eTools consumer
    understands — including our own renderer and any homebrew round trip.
    """

    def test_ability_scores_serialise_under_json_names(self) -> None:
        monster = Monster.model_validate({"name": "X", "source": "S", "str": 8, "int": 10})
        dumped = monster.model_dump(mode="json", exclude_none=True)
        assert dumped["str"] == 8
        assert dumped["int"] == 10
        assert "str_" not in dumped
        assert "int_" not in dumped

    def test_item_weapon_properties_serialise_under_json_name(self) -> None:
        item = Item.model_validate({"name": "X", "source": "S", "property": ["F", "L"]})
        dumped = item.model_dump(mode="json", exclude_none=True)
        assert dumped["property"] == ["F", "L"]
        assert "properties" not in dumped

    def test_round_trip_preserves_aliased_and_unknown_fields(self) -> None:
        """Validate -> dump -> validate must be lossless."""
        original = {
            "name": "X",
            "source": "S",
            "str": 8,
            "int": 10,
            "cr": "1/4",
            "somethingUnmodelled": {"a": 1},
        }
        once = Monster.model_validate(original)
        twice = Monster.model_validate(once.model_dump(mode="json", exclude_none=True))
        assert twice.str_ == 8
        assert twice.int_ == 10
        assert twice.cr_value == "1/4"
        assert twice.extra_fields()["somethingUnmodelled"] == {"a": 1}
