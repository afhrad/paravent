"""The derived sheet — without a corpus (overrides, fallbacks) and against the real one."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.pc import PlayerCharacter
from app.services.sheet import CASTER_SLOTS, build_sheet
from app.store.compendium import CompendiumStore
from tests.conftest import requires_corpus

EXAMPLE = (
    Path(__file__).resolve().parent.parent / "documentation" / "examples" / "lyra-mooncaller.json"
)


def _lyra(**changes: Any) -> PlayerCharacter:
    data = json.loads(EXAMPLE.read_text())
    data.update(changes)
    return PlayerCharacter.model_validate(data)


def _pc(**data: Any) -> PlayerCharacter:
    return PlayerCharacter.model_validate(data)


class TestWithoutCorpus:
    """A hand-made character still renders when nobody configured a corpus."""

    def test_abilities_saves_and_skills_from_the_file_alone(self) -> None:
        sheet = build_sheet(
            _pc(
                abilities={"str": 16, "dex": 12},
                skills=["Athletics"],
                classes=[{"class": "Fighter|PHB", "level": 5}],
            )
        )
        assert sheet.level == 5
        assert sheet.proficiency.value == 3
        assert sheet.ability("str").modifier == 3
        athletics = next(s for s in sheet.skills if s.key == "athletics")
        assert athletics.proficient and athletics.value.value == 6
        assert len(sheet.skills) == 18  # the fallback table

    def test_unknown_values_stay_blank_rather_than_wrong(self) -> None:
        sheet = build_sheet(_pc(classes=[{"class": "Homebrew Knight", "level": 3}]))
        assert sheet.hp_max.value is None
        assert sheet.speed.value is None
        assert sheet.spell_save_dc is None

    def test_overrides_win_and_say_so(self) -> None:
        sheet = build_sheet(
            _pc(
                abilities={"con": 14},
                classes=[{"class": "Homebrew Knight", "level": 3}],
                overrides={
                    "hp_max": {"value": 31, "note": "rolled"},
                    "ac": 18,
                    "speed": 35,
                    "save.con": 9,
                    "ability.str": 21,
                    "slots.1": 3,
                },
            )
        )
        assert (sheet.hp_max.value, sheet.hp_max.overridden, sheet.hp_max.note) == (
            31,
            True,
            "rolled",
        )
        assert sheet.armor_class.value == 18 and sheet.armor_class.overridden
        assert sheet.speed.value == 35
        assert sheet.ability("con").save.value == 9
        assert sheet.ability("str").score.value == 21 and sheet.ability("str").modifier == 5
        assert [(s.level, s.total) for s in sheet.slots] == [(1, 3)]

    def test_current_hp_defaults_to_the_maximum(self) -> None:
        sheet = build_sheet(_pc(overrides={"hp_max": 20}))
        assert sheet.hp_current == 20
        sheet = build_sheet(_pc(overrides={"hp_max": 20}, state={"hp": 7, "temp_hp": 3}))
        assert (sheet.hp_current, sheet.temp_hp) == (7, 3)

    def test_unknown_spells_are_listed_not_dropped(self) -> None:
        sheet = build_sheet(_pc(spells={"prepared": ["Fireball|PHB"]}))
        assert [(s.name, s.found) for s in sheet.spells] == [("Fireball", False)]

    def test_all_fourteen_conditions_with_active_marked(self) -> None:
        sheet = build_sheet(_pc(state={"conditions": ["prone"]}))
        assert len(sheet.conditions) == 14
        assert [c.key for c in sheet.conditions if c.active] == ["prone"]

    def test_resources_track_use(self) -> None:
        sheet = build_sheet(
            _pc(
                resources=[{"name": "Rage", "max": 3, "recharge": "long"}],
                state={"resources_used": {"Rage": 2}},
            )
        )
        assert (sheet.resources[0].available, sheet.resources[0].max) == (1, 3)

    def test_multiclass_slot_table_is_complete(self) -> None:
        assert set(CASTER_SLOTS) == set(range(1, 21))
        assert all(sum(row) > 0 for row in CASTER_SLOTS.values())


class TestHitPoints:
    """Maximum HP from the ``hit_points`` choice: averaged, or rolled per level."""

    def test_rolled_needs_no_corpus(self) -> None:
        pc = _pc(
            classes=[{"class": "Homebrew Knight", "level": 3}],
            abilities={"con": 14},
            hit_points={"method": "rolled", "per_level": [10, 4, 7]},
        )
        assert build_sheet(pc).hp_max.value == 21 + 2 * 3

    def test_con_applies_to_every_level(self) -> None:
        rolls = {"method": "rolled", "per_level": [10, 4, 7]}
        low = _pc(classes=[{"class": "X", "level": 3}], abilities={"con": 10}, hit_points=rolls)
        high = _pc(classes=[{"class": "X", "level": 3}], abilities={"con": 16}, hit_points=rolls)
        assert build_sheet(high).hp_max.value - build_sheet(low).hp_max.value == 3 * 3

    def test_unknown_class_without_rolls_uses_the_last_saved_maximum(self) -> None:
        pc = _pc(classes=[{"class": "Homebrew Knight", "level": 3}], state={"hp_max": 31})
        sheet = build_sheet(pc)
        assert sheet.hp_max.value == 31
        assert sheet.hp_max.explanation == "last saved maximum"

    def test_override_beats_the_rolls(self) -> None:
        pc = _pc(
            classes=[{"class": "X", "level": 2}],
            hit_points={"method": "rolled", "per_level": [10, 8]},
            overrides={"hp_max": {"value": 30, "note": "Aid"}},
        )
        assert build_sheet(pc).hp_max.value == 30

    def test_die_results_are_checked(self) -> None:
        with pytest.raises(ValidationError, match="1–12"):
            _pc(hit_points={"method": "rolled", "per_level": [10, 13]})


@requires_corpus
class TestHitPointsAgainstTheCorpus:
    def test_average_is_the_default(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(classes=[{"class": "Fighter|PHB", "level": 3}], abilities={"con": 14})
        assert build_sheet(pc, loaded_compendium).hp_max.value == 10 + 6 + 6 + 2 * 3

    def test_missing_rolls_count_as_average(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(
            classes=[{"class": "Fighter|PHB", "level": 3}],
            abilities={"con": 10},
            hit_points={"method": "rolled", "per_level": [10, 2]},
        )
        sheet = build_sheet(pc, loaded_compendium)
        assert sheet.hp_max.value == 10 + 2 + 6
        assert "1 levels at average" in sheet.hp_max.explanation


@requires_corpus
class TestLyraAgainstTheCorpus:
    """The example character, every number read from the real 5eTools data."""

    def test_headline_numbers(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(), loaded_compendium)
        assert sheet.summary == "High Elf · Wizard 10 · Evocation · Sage"
        assert sheet.hp_max.value == 62  # 6 + 9 × 4 + 2 × 10
        assert sheet.hp_current == 41
        assert sheet.armor_class.value == 14  # 10 + DEX 3 + Ring of Protection 1
        assert "items +1" in sheet.armor_class.explanation
        assert sheet.speed.value == 30
        assert sheet.initiative.value == 3
        assert sheet.passive_perception.value == 11

    def test_species_bonuses_come_from_the_corpus(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(), loaded_compendium)
        # base 14 DEX + elf 2 = 16; base 15 INT + high elf 1 + two improvements = 20
        assert sheet.ability("dex").score.value == 16
        assert sheet.ability("int").score.value == 20
        assert "species +1" in sheet.ability("int").score.explanation

    def test_spellcasting(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(), loaded_compendium)
        assert sheet.spell_save_dc is not None and sheet.spell_save_dc.value == 17
        assert sheet.spell_attack is not None and sheet.spell_attack.value == 9
        assert [(s.level, s.total, s.used) for s in sheet.slots] == [
            (1, 4, 1),
            (2, 3, 0),
            (3, 3, 1),
            (4, 3, 0),
            (5, 2, 0),
        ]
        assert sheet.pact is None

    def test_spell_details_are_real_data(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(), loaded_compendium)
        fire_bolt = sheet.spell("Fire Bolt|PHB")
        assert fire_bolt is not None and fire_bolt.damage == "2d10"  # scales at level 5
        fireball = sheet.spell("Fireball|PHB")
        assert fireball is not None
        assert (fireball.level, fireball.save, fireball.damage, fireball.range) == (
            3,
            "DEX",
            "8d6",
            "150 ft",
        )
        haste = sheet.spell("Haste|PHB")
        assert haste is not None and haste.concentration
        assert all(s.found for s in sheet.spells)
        assert sheet.concentration is not None and sheet.concentration.name == "Haste"

    def test_attacks_from_equipped_weapons(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(), loaded_compendium)
        staff = next(a for a in sheet.attacks if a.name == "Quarterstaff")
        assert (staff.bonus.value, staff.damage, staff.versatile) == (3, "1d6-1", "1d8-1")
        assert "Dagger" not in [a.name for a in sheet.attacks]  # carried, not equipped

    def test_features(self, loaded_compendium: CompendiumStore) -> None:
        names = [f.name for f in build_sheet(_lyra(), loaded_compendium).features]
        assert {
            "Arcane Recovery",
            "Sculpt Spells",
            "Empowered Evocation",
            "Fey Ancestry",
            "Feature: Researcher",
        } <= set(names)
        assert "Cantrip Formulas" not in names  # Tasha's option, not taken
        assert names.count("School of Evocation") == 1  # not also the 2024 copy
        assert "Ability Score Improvement" not in names

    def test_optional_feature_appears_when_taken(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(optional_features=["Cantrip Formulas|TCE"]), loaded_compendium)
        assert "Cantrip Formulas" in [f.name for f in sheet.features]

    def test_equipping_armour_changes_ac(self, loaded_compendium: CompendiumStore) -> None:
        lyra = _lyra(
            inventory=[
                {"item": "Studded Leather Armor|PHB", "equipped": True},
                {"item": "Shield|PHB", "equipped": True},
            ]
        )
        ac = build_sheet(lyra, loaded_compendium).armor_class
        assert ac.value == 12 + 3 + 2
        assert "shield +2" in ac.explanation


@requires_corpus
class TestOtherCasters:
    def test_multiclass_uses_the_combined_table(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(
            classes=[{"class": "Wizard|PHB", "level": 3}, {"class": "Cleric|PHB", "level": 2}],
            abilities={"con": 10},
        )
        sheet = build_sheet(pc, loaded_compendium)
        assert [s.total for s in sheet.slots] == list(CASTER_SLOTS[5])
        assert sheet.hp_max.value == 6 + 2 * 4 + 2 * 5  # wizard first, then averages

    def test_warlock_pact_magic(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(
            _pc(classes=[{"class": "Warlock|PHB", "level": 11}], abilities={"cha": 18}),
            loaded_compendium,
        )
        assert sheet.slots == []
        assert sheet.pact is not None and (sheet.pact.total, sheet.pact.level) == (3, 5)
        assert sheet.spell_save_dc is not None and sheet.spell_save_dc.value == 8 + 4 + 4

    def test_half_caster_reads_its_own_table(self, loaded_compendium: CompendiumStore) -> None:
        old = build_sheet(_pc(classes=[{"class": "Paladin|PHB", "level": 1}]), loaded_compendium)
        new = build_sheet(_pc(classes=[{"class": "Paladin|XPHB", "level": 1}]), loaded_compendium)
        assert old.slots == []  # 2014 paladins cast from level 2
        assert [s.total for s in new.slots] == [2]  # 2024 paladins from level 1

    def test_subclass_caster(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(
            classes=[{"class": "Fighter|PHB", "level": 7, "subclass": "Eldritch Knight|PHB"}],
            abilities={"int": 14},
        )
        sheet = build_sheet(pc, loaded_compendium)
        assert [s.total for s in sheet.slots] == [4, 2]
        assert sheet.spellcasting_ability == "int"

    def test_monk_unarmoured_defence(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(classes=[{"class": "Monk|PHB", "level": 1}], abilities={"dex": 16, "wis": 14})
        assert build_sheet(pc, loaded_compendium).armor_class.value == 10 + 3 + 2


@requires_corpus
class TestBrunhild:
    """The second example: a level 8 mountain dwarf paladin owned by a named player."""

    def test_brunhild(self, loaded_compendium: CompendiumStore) -> None:
        path = EXAMPLE.parent / "brunhild-ironvow.json"
        raw = json.loads(path.read_text())
        pc = PlayerCharacter.model_validate(raw)
        assert pc.model_dump(mode="json") == raw
        assert pc.belongs_to("tenewaja")
        sheet = build_sheet(pc, loaded_compendium)
        assert sheet.hp_max.value == 59 + 3 * 8  # rolled 10+7+4+9+6+8+5+10, CON +3 × 8
        assert sheet.hp_max.explanation.startswith("rolled 59 (8 levels)")
        assert sheet.speed.value == 25
        assert sheet.armor_class.value == 21  # derived: plate, shield, Defense
        assert sheet.ability("cha").save.value == 11
        assert [(s.level, s.total) for s in sheet.slots] == [(1, 4), (2, 3)]
        assert sheet.spell_save_dc is not None and sheet.spell_save_dc.value == 15
        assert all(s.found for s in sheet.spells)
        longsword = next(a for a in sheet.attacks if a.name == "Longsword")
        assert (longsword.bonus.value, longsword.damage) == (7, "1d8+4")
        assert "Aura of Protection" in [f.name for f in sheet.features]


@requires_corpus
class TestWornItems:
    """Magic items add their typed bonuses when worn — and attuned, if they ask for it."""

    @staticmethod
    def _inventory(*items: dict[str, Any]) -> list[dict[str, Any]]:
        lyra = json.loads(EXAMPLE.read_text())
        keep = [i for i in lyra["inventory"] if "Ring of Protection" not in i["item"]]
        return [*keep, *items]

    def test_an_attuned_ring_adds_to_saves_and_ac(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_lyra(), loaded_compendium)
        save = next(a for a in sheet.abilities if a.key == "int").save
        assert "Ring of Protection +1" in save.explanation
        bare = build_sheet(_lyra(inventory=self._inventory()), loaded_compendium)
        assert save.value == next(a for a in bare.abilities if a.key == "int").save.value + 1

    def test_unattuned_it_does_nothing(self, loaded_compendium: CompendiumStore) -> None:
        cloak = {"item": "Cloak of Protection|DMG", "equipped": True, "attuned": False}
        sheet = build_sheet(_lyra(inventory=self._inventory(cloak)), loaded_compendium)
        assert sheet.armor_class.value == 13
        assert all("Cloak" not in a.save.explanation for a in sheet.abilities)

    def test_a_wand_and_a_rod_raise_spell_attack_and_dc(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        plain = build_sheet(_lyra(inventory=self._inventory()), loaded_compendium)
        assert plain.spell_attack is not None and plain.spell_save_dc is not None
        wand = {"item": "+1 Wand of the War Mage|DMG", "equipped": True, "attuned": True}
        rod = {"item": "+2 Rod of the Pact Keeper|DMG", "equipped": True, "attuned": True}
        sheet = build_sheet(_lyra(inventory=self._inventory(wand, rod)), loaded_compendium)
        assert sheet.spell_attack is not None and sheet.spell_save_dc is not None
        assert sheet.spell_attack.value == (plain.spell_attack.value or 0) + 3
        assert sheet.spell_save_dc.value == (plain.spell_save_dc.value or 0) + 2
        assert "+1 Wand of the War Mage +1" in sheet.spell_attack.explanation


class TestSavingThrows:
    def test_a_listed_save_is_proficient(self) -> None:
        pc = _pc(
            abilities={"con": 14},
            classes=[{"class": "Wizard|PHB", "level": 4}],
            saving_throws=["con"],
        )
        con = next(a for a in build_sheet(pc).abilities if a.key == "con")
        assert con.save_proficient and con.save.value == 2 + 2


@requires_corpus
class TestDefenses:
    def test_species_and_attuned_items(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(
            species="Tiefling|PHB",
            classes=[{"class": "Warlock|PHB", "level": 3}],
            inventory=[
                {"item": "Ring of Cold Resistance|DMG", "equipped": True, "attuned": True},
                {"item": "Ring of Acid Resistance|DMG", "equipped": True, "attuned": False},
            ],
        )
        sheet = build_sheet(pc, loaded_compendium)
        assert sheet.defended("resist") == ["fire", "cold"]  # unattuned acid ring: nothing
        origins = {d.damage: d.origin for d in sheet.defenses}
        assert origins == {"fire": "Tiefling", "cold": "Ring of Cold Resistance"}

    def test_a_dwarf_resists_poison(self, loaded_compendium: CompendiumStore) -> None:
        sheet = build_sheet(_pc(species="Dwarf|PHB"), loaded_compendium)
        assert sheet.defended("resist") == ["poison"] and not sheet.defended("immune")


@requires_corpus
class TestChosenAndRaging:
    def test_a_dragonborn_is_asked_then_resists(self, loaded_compendium: CompendiumStore) -> None:
        open_ = build_sheet(_pc(species="Dragonborn|PHB"), loaded_compendium)
        assert [o for o, _ in open_.open_resistances] == ["Dragonborn"]
        assert "fire" in open_.open_resistances[0][1] and not open_.defended("resist")
        chosen = build_sheet(_pc(species="Dragonborn|PHB", resistances=["fire"]), loaded_compendium)
        assert chosen.defended("resist") == ["fire"] and not chosen.open_resistances

    def test_rage_resists_while_on(self, loaded_compendium: CompendiumStore) -> None:
        calm = _pc(classes=[{"class": "Barbarian|PHB", "level": 2}])
        assert not build_sheet(calm, loaded_compendium).defended("resist")
        angry = _pc(classes=[{"class": "Barbarian|PHB", "level": 2}], state={"raging": True})
        assert build_sheet(angry, loaded_compendium).defended("resist") == [
            "bludgeoning",
            "piercing",
            "slashing",
        ]
        wizard = _pc(classes=[{"class": "Wizard|PHB", "level": 2}], state={"raging": True})
        assert not build_sheet(wizard, loaded_compendium).defended("resist")  # no Rage feature


class TestUpcasting:
    def test_damage_grows_with_the_slot(self) -> None:
        from app.services.sheet import SpellView, damage_at

        fireball = SpellView("Fireball|PHB", "Fireball", True, level=3, damage="8d6", upcast="1d6")
        assert damage_at(fireball, 3) == "8d6" and damage_at(fireball, 5) == "10d6"
        odd = SpellView("X|Y", "X", True, level=1, damage="2d8", upcast="1d6")
        assert damage_at(odd, 3) == "2d8+1d6+1d6"  # different dice are added, not merged
        flat = SpellView("Z|Y", "Z", True, level=1, damage="3d4+3")
        assert damage_at(flat, 4) == "3d4+3"  # no higher-level rule: as written

    @requires_corpus
    def test_the_slot_levels_left_and_the_scaling_read(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        wizard = _pc(
            abilities={"int": 16},
            classes=[{"class": "Wizard|PHB", "level": 9}],
            spells={"known": ["Fireball|PHB"], "prepared": ["Fireball|PHB"]},
            state={"slots_used": {"4": 3}},
        )
        sheet = build_sheet(wizard, loaded_compendium)
        fireball = sheet.spell("Fireball|PHB")
        assert fireball is not None and fireball.upcast == "1d6"
        assert sheet.castable(fireball) == [3, 5]  # every 4th-level slot is spent


@requires_corpus
class TestHealing:
    def test_healing_spells_read_from_the_corpus(self, loaded_compendium: CompendiumStore) -> None:
        from app.services.sheet import heal_at, spell_view

        cure = spell_view(
            loaded_compendium.get("spell", "cure wounds|phb").model_dump(
                by_alias=True, exclude_none=True
            )
        )
        assert (cure.heal, cure.heal_upcast, cure.heal_mod) == ("1d8", "1d8", True)
        assert heal_at(cure, 2, 3) == "2d8+3" and heal_at(cure, 1, 0) == "1d8"
        mass = spell_view(
            loaded_compendium.get("spell", "mass healing word|phb").model_dump(
                by_alias=True, exclude_none=True
            )
        )
        assert (mass.heal, mass.heal_targets) == ("1d4", 6)  # up to six creatures

    def test_group_and_fixed_healing(self, loaded_compendium: CompendiumStore) -> None:
        from app.services.sheet import heal_at, spell_view

        def view(key: str) -> Any:
            data = loaded_compendium.get("spell", key).model_dump(by_alias=True, exclude_none=True)
            return spell_view(data)

        mass = view("mass cure wounds|phb")
        assert (mass.heal, mass.heal_targets) == ("3d8", 6)
        assert view("prayer of healing|phb").heal_targets == 6  # "up to six creatures"
        heal = view("heal|xphb")
        assert heal.heal == "70" and heal_at(heal, 8, 4) == "90"  # +10 a slot above 6th
        assert view("mass heal|phb").heal == ""  # 700 divided: left to the gamemaster


@requires_corpus
class TestExhaustion:
    def test_2014_halves_speed_then_hit_points(self, loaded_compendium: CompendiumStore) -> None:
        def sheet(level: int) -> Any:
            pc = _pc(
                species="Human|PHB",
                classes=[{"class": "Fighter|PHB", "level": 4}],
                abilities={"con": 14},
                state={"exhaustion": level},
            )
            return build_sheet(pc, loaded_compendium)

        fresh, tired, worn = sheet(0), sheet(2), sheet(4)
        assert fresh.edition == "2014" and tired.speed.value == (fresh.speed.value or 0) // 2
        assert "exhaustion 2" in tired.speed.explanation
        assert worn.hp_max.value == (fresh.hp_max.value or 0) // 2
        assert sheet(5).speed.value == 0

    def test_2024_takes_five_feet_a_level(self, loaded_compendium: CompendiumStore) -> None:
        pc = _pc(
            species="Human|XPHB",
            classes=[{"class": "Fighter|XPHB", "level": 4}],
            state={"exhaustion": 2},
        )
        sheet = build_sheet(pc, loaded_compendium)
        assert sheet.edition == "2024" and sheet.speed.value == 30 - 10
