"""Features that change the sheet's numbers, recognised by name and book."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.pc import PlayerCharacter
from app.services.sheet import Sheet, build_sheet
from app.store.compendium import CompendiumStore
from tests.conftest import requires_corpus

EXAMPLES = Path(__file__).resolve().parent.parent / "documentation" / "examples"


def _sheet(compendium: CompendiumStore | None = None, **data: Any) -> Sheet:
    return build_sheet(PlayerCharacter.model_validate(data), compendium)


def _skill(sheet: Sheet, key: str) -> int | None:
    return next(s.value.value for s in sheet.skills if s.key == key)


ROLLED_3 = {"method": "rolled", "per_level": [10, 6, 6]}


class TestFeatsWithoutCorpus:
    """Feats are in the file, so they work even with no corpus configured."""

    def test_tough(self) -> None:
        sheet = _sheet(
            classes=[{"class": "X", "level": 3}], hit_points=ROLLED_3, feats=["Tough|PHB"]
        )
        assert sheet.hp_max.value == 22 + 6
        assert "Tough +6" in sheet.hp_max.explanation

    def test_alert_by_edition(self) -> None:
        old = _sheet(
            classes=[{"class": "X", "level": 5}], abilities={"dex": 14}, feats=["Alert|PHB"]
        )
        new = _sheet(
            classes=[{"class": "X", "level": 5}], abilities={"dex": 14}, feats=["Alert|XPHB"]
        )
        assert old.initiative.value == 2 + 5
        assert new.initiative.value == 2 + 3  # proficiency bonus at level 5

    def test_mobile_and_speedy(self) -> None:
        sheet = _sheet(feats=["Mobile|PHB"], overrides={"speed": 30})
        assert sheet.speed.value == 30  # an override always wins
        sheet = _sheet(feats=["Speedy|XPHB"])
        assert sheet.speed.value is None  # nothing to add to without a base speed

    def test_observant_only_in_2014(self) -> None:
        old = _sheet(abilities={"wis": 12}, feats=["Observant|PHB"])
        new = _sheet(abilities={"wis": 12}, feats=["Observant|XPHB"])
        assert old.passive_perception.value == 11 + 5
        assert new.passive_perception.value == 11

    def test_overrides_are_left_alone(self) -> None:
        sheet = _sheet(
            classes=[{"class": "X", "level": 3}],
            hit_points=ROLLED_3,
            feats=["Tough|PHB"],
            overrides={"hp_max": 25},
        )
        assert sheet.hp_max.value == 25 and sheet.hp_max.overridden


@requires_corpus
class TestClassFeatures:
    def test_aura_of_protection_from_level_6(self, loaded_compendium: CompendiumStore) -> None:
        five = _sheet(
            loaded_compendium, classes=[{"class": "Paladin|PHB", "level": 5}], abilities={"cha": 16}
        )
        six = _sheet(
            loaded_compendium, classes=[{"class": "Paladin|PHB", "level": 6}], abilities={"cha": 16}
        )
        assert six.ability("dex").save.value == five.ability("dex").save.value + 3  # type: ignore[operator]
        assert "Aura of Protection +3" in six.ability("str").save.explanation

    def test_aura_is_at_least_one(self, loaded_compendium: CompendiumStore) -> None:
        sheet = _sheet(
            loaded_compendium, classes=[{"class": "Paladin|XPHB", "level": 6}], abilities={"cha": 8}
        )
        assert "Aura of Protection +1" in sheet.ability("con").save.explanation

    def test_jack_of_all_trades(self, loaded_compendium: CompendiumStore) -> None:
        sheet = _sheet(
            loaded_compendium,
            classes=[{"class": "Bard|PHB", "level": 5}],
            abilities={"dex": 14, "int": 10},
            skills=["Performance"],
        )
        assert _skill(sheet, "arcana") == 0 + 1  # half of +3, rounded down
        assert _skill(sheet, "performance") == 0 + 3  # proficient: no extra
        assert sheet.initiative.value == 2 + 1

    def test_remarkable_athlete_is_2014_only(self, loaded_compendium: CompendiumStore) -> None:
        old = _sheet(
            loaded_compendium,
            abilities={"str": 10, "int": 10},
            classes=[{"class": "Fighter|PHB", "level": 7, "subclass": "Champion|PHB"}],
        )
        new = _sheet(
            loaded_compendium,
            abilities={"str": 10, "int": 10},
            classes=[{"class": "Fighter|XPHB", "level": 7, "subclass": "Champion|XPHB"}],
        )
        assert _skill(old, "athletics") == 2  # half of +3, rounded up
        assert _skill(old, "arcana") == 0  # INT is not physical
        assert _skill(new, "athletics") == 0  # the 2024 feature grants advantage instead

    def test_monk_movement_and_diamond_soul(self, loaded_compendium: CompendiumStore) -> None:
        sheet = _sheet(
            loaded_compendium,
            species="Human|PHB",
            classes=[{"class": "Monk|PHB", "level": 14}],
            abilities={"int": 10},
        )
        assert sheet.speed.value == 30 + 25
        assert sheet.ability("int").save_proficient
        assert sheet.ability("int").save.value == 0 + 5

    def test_unarmored_movement_needs_no_armour(self, loaded_compendium: CompendiumStore) -> None:
        sheet = _sheet(
            loaded_compendium,
            species="Human|PHB",
            classes=[{"class": "Monk|PHB", "level": 6}],
            inventory=[{"item": "Leather Armor|PHB", "equipped": True}],
        )
        assert sheet.speed.value == 30

    def test_fast_movement_not_in_heavy_armour(self, loaded_compendium: CompendiumStore) -> None:
        light = _sheet(
            loaded_compendium, species="Human|PHB", classes=[{"class": "Barbarian|PHB", "level": 5}]
        )
        heavy = _sheet(
            loaded_compendium,
            species="Human|PHB",
            classes=[{"class": "Barbarian|PHB", "level": 5}],
            inventory=[{"item": "Plate Armor|PHB", "equipped": True}],
        )
        assert (light.speed.value, heavy.speed.value) == (40, 30)

    def test_defense_fighting_style(self, loaded_compendium: CompendiumStore) -> None:
        armored = _sheet(
            loaded_compendium,
            classes=[{"class": "Fighter|PHB", "level": 1}],
            optional_features=["Defense|PHB"],
            inventory=[{"item": "Chain Mail|PHB", "equipped": True}],
        )
        assert armored.armor_class.value == 16 + 1
        bare = _sheet(
            loaded_compendium,
            classes=[{"class": "Fighter|PHB", "level": 1}],
            optional_features=["Defense|PHB"],
        )
        assert bare.armor_class.value == 10  # only while wearing armour
        feat = _sheet(
            loaded_compendium,
            classes=[{"class": "Fighter|XPHB", "level": 1}],
            feats=["Defense|XPHB"],
            inventory=[{"item": "Chain Mail|PHB", "equipped": True}],
        )
        assert feat.armor_class.value == 17

    def test_dwarven_toughness(self, loaded_compendium: CompendiumStore) -> None:
        hill = _sheet(
            loaded_compendium,
            species="Dwarf|PHB",
            subspecies="Hill|PHB",
            classes=[{"class": "Cleric|PHB", "level": 4}],
            abilities={"con": 10},
        )
        mountain = _sheet(
            loaded_compendium,
            species="Dwarf|PHB",
            subspecies="Mountain|PHB",
            classes=[{"class": "Cleric|PHB", "level": 4}],
            abilities={"con": 10},
        )
        assert hill.hp_max.value == mountain.hp_max.value + 4  # type: ignore[operator]

    def test_draconic_resilience_by_edition(self, loaded_compendium: CompendiumStore) -> None:
        old = _sheet(
            loaded_compendium,
            classes=[{"class": "Sorcerer|PHB", "level": 3, "subclass": "Draconic|PHB"}],
            abilities={"dex": 14, "cha": 16, "con": 10},
        )
        new = _sheet(
            loaded_compendium,
            classes=[{"class": "Sorcerer|XPHB", "level": 3, "subclass": "Draconic|XPHB"}],
            abilities={"dex": 14, "cha": 16, "con": 10},
        )
        assert old.armor_class.value == 13 + 2
        assert new.armor_class.value == 10 + 2 + 3
        assert old.hp_max.value == 6 + 4 + 4 + 3

    def test_brunhild_needs_no_overrides(self, loaded_compendium: CompendiumStore) -> None:
        pc = PlayerCharacter.model_validate(
            json.loads((EXAMPLES / "brunhild-ironvow.json").read_text())
        )
        assert pc.overrides == {}
        sheet = build_sheet(pc, loaded_compendium)
        assert sheet.armor_class.value == 21
        assert [a.save.value for a in sheet.abilities] == [8, 3, 7, 3, 7, 11]
        assert "Defense" in [f.name for f in sheet.features if f.kind == "option"]


@requires_corpus
def test_varis_needs_no_overrides(loaded_compendium: CompendiumStore) -> None:
    """The third example: a level 8 wood elf Champion with Defense and Alert."""
    pc = PlayerCharacter.model_validate(json.loads((EXAMPLES / "varis-thornwood.json").read_text()))
    assert pc.overrides == {}
    sheet = build_sheet(pc, loaded_compendium)
    assert (sheet.hp_max.value, sheet.armor_class.value, sheet.speed.value) == (68, 18, 35)
    assert sheet.initiative.value == 5 + 2 + 5  # DEX, Remarkable Athlete, Alert
    assert _skill(sheet, "stealth") == 5 + 2
    longbow = next(a for a in sheet.attacks if a.name == "Longbow")
    assert (longbow.bonus.value, longbow.damage) == (8, "1d8+5")
