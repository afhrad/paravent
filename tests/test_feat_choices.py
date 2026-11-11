"""The choices a feat brings beyond abilities, proficiencies and spells
(documentation/CHARGEN-WIZARD.md §8, §9) — against the real corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models.gamestate import SourceSet
from app.models.pc import Abilities, InventoryItem, PlayerCharacter
from app.services.chargen import Catalogue, Planner
from app.services.chargen.levelup import SWAP, LevelUpPlanner
from app.services.chargen.rules import _feat_choice_rows
from app.services.play import PlayError
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables
from tests.test_play import Table

BOOKS_2014 = ["PHB", "MM", "DMG", "XGE", "TCE", "BGG"]
BOOKS_2024 = ["XPHB", "XDMG", "XMM"]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


def _planner(compendium: CompendiumStore, lookups: LookupTables, books: list[str]) -> Planner:
    return Planner(Catalogue(compendium, SourceSet(sources=books), lookups))


def _drafted(planner: Planner, answers: dict[str, list[str]], **scores: int) -> PlayerCharacter:
    pc = PlayerCharacter(name="Test")
    for pick_id, values in answers.items():
        planner.answer(pc, pick_id, values)
    pc.abilities = Abilities.model_validate(
        {"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8, **scores}
    )
    return pc


def _fighter(feat: str) -> dict[str, list[str]]:
    return {
        "class": ["Fighter|PHB"],
        "level": ["4"],
        "subclass": ["Champion|PHB"],
        "species": ["Human|PHB"],
        "background": ["Soldier|PHB"],
        "abilities.method": ["standard"],
        "asi.4": [f"feat:{feat}"],
        "hp": ["average"],
    }


def _ready(data: dict[str, Any]) -> PlayerCharacter:
    return PlayerCharacter.model_validate({"status": "ready", "pending_levels": 1, **data})


class TestRulesFile:
    def test_every_row_names_a_feat_of_the_corpus(self, loaded_compendium: CompendiumStore) -> None:
        feats = {f"{f.name}|{f.source}".lower() for f in loaded_compendium.all("feat")}
        for row in _feat_choice_rows():
            assert row.feat.lower() in feats, row


class TestWizard:
    def test_martial_adept_asks_two_maneuvers(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _drafted(planner, _fighter("Martial Adept|PHB"))
        pick = next(
            p for p in planner.plan(pc).step("features").picks if p.id.startswith("opt.feat.")
        )
        assert pick.kind == "optional" and pick.count == 2
        assert "Riposte|PHB" in pick.options and "Martial Adept" in pick.label
        planner.answer(pc, pick.id, ["Riposte|PHB", "Parry|PHB"])
        planner.apply(pc)
        assert {o.name for o in pc.optional_features} >= {"Riposte", "Parry"}

    def test_weapon_master_trains_four_weapons_the_sheet_counts(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        answers = {**_fighter("Weapon Master|PHB"), "class": ["Wizard|PHB"]}
        answers.pop("subclass")
        pc = _drafted(planner, answers, int=15, str=13)
        pick = next(p for p in planner.plan(pc).step("features").picks if p.kind == "weapon")
        assert pick.count == 4 and "Longsword|PHB" in pick.options
        assert "Shortbow|PHB" in pick.options  # simple weapons too
        planner.answer(pc, pick.id, ["Longsword|PHB", "Rapier|PHB", "Shortbow|PHB", "Whip|PHB"])
        planner.apply(pc)
        assert [w.name for w in pc.weapons] == ["Longsword", "Rapier", "Shortbow", "Whip"]
        pc.inventory = [InventoryItem(item="Longsword|PHB", equipped=True)]
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2014))
        longsword = next(a for a in sheet.attacks if a.name.startswith("Longsword"))
        assert "prof" in longsword.bonus.explanation
        assert "longsword" in [w.lower() for w in sheet.weapon_proficiencies]

    def test_elemental_adept_asks_its_damage_type_and_the_sheet_names_it(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _drafted(
            planner,
            {
                "class": ["Wizard|XPHB"],
                "level": ["4"],
                "species": ["Human|XPHB"],
                "background": ["Sage|XPHB"],
                "abilities.method": ["standard"],
                "asi.4": ["feat:Elemental Adept|XPHB"],
                "hp": ["average"],
            },
            int=15,
        )
        pick = next(
            p for p in planner.plan(pc).step("features").picks if p.id.endswith(".choice.damage")
        )
        assert pick.kind == "featureoption" and "fire" in pick.options
        planner.answer(pc, pick.id, ["fire"])
        planner.apply(pc)
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2024))
        adept = next(f for f in sheet.features if f.name == "Elemental Adept")
        assert adept.chosen == ("fire",)


class TestLevelUp:
    def test_a_feat_taken_later_asks_its_maneuvers(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2014))
        pc = _ready(
            {
                "classes": [{"class": "Fighter|PHB", "level": 3, "subclass": "Champion|PHB"}],
                "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
            }
        )
        levels.answer(pc, "asi", ["feat:Martial Adept|PHB"])
        pick = next(p for p in levels.plan(pc).picks if p.id.startswith("opt.feat."))
        levels.answer(pc, pick.id, ["Trip Attack|PHB", "Menacing Attack|PHB"])
        assert levels.plan(pc).complete
        levels.take(pc)
        assert {o.name for o in pc.optional_features} == {"Trip Attack", "Menacing Attack"}

    def test_a_battle_master_may_trade_a_maneuver_when_learning_more(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2014))
        pc = _ready(
            {
                "classes": [{"class": "Fighter|PHB", "level": 6, "subclass": "Battle Master|PHB"}],
                "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
                "optional_features": ["Riposte|PHB", "Parry|PHB", "Trip Attack|PHB"],
            }
        )
        plan = levels.plan(pc)
        out = next(p for p in plan.picks if p.id == f"{SWAP}opt.out")
        assert set(out.options) == {"Riposte|PHB", "Parry|PHB", "Trip Attack|PHB"}
        into = next(p for p in plan.picks if p.id == f"{SWAP}opt.in")
        assert "Riposte|PHB" not in into.options and "Feinting Attack|PHB" in into.options

    def test_a_2024_fighter_may_replace_the_fighting_style(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2024))
        pc = _ready(
            {
                "classes": [{"class": "Fighter|XPHB", "level": 1}],
                "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
                "feats": ["Defense|XPHB"],
            }
        )
        plan = levels.plan(pc)
        out = next(p for p in plan.picks if p.id == f"{SWAP}style.out")
        assert out.options == ("Defense|XPHB",)
        levels.answer(pc, f"{SWAP}style.out", ["Defense|XPHB"])
        levels.answer(pc, f"{SWAP}style.in", ["Archery|XPHB"])
        levels.take(pc)
        assert [f.name for f in pc.feats] == ["Archery"]


class TestSheetTraining:
    def test_a_high_elf_is_trained_with_the_longsword_and_a_feat_with_heavy_armor(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        data = json.loads((ROOT / "documentation/examples/lyra-mooncaller.json").read_text())
        data.pop("$schema")
        pc = PlayerCharacter.model_validate(data)
        pc.inventory = [InventoryItem(item="Longsword|PHB", equipped=True)]
        pc.feats = [
            *pc.feats,
            *PlayerCharacter.model_validate({"feats": ["Heavily Armored|PHB"]}).feats,
        ]
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2014))
        longsword = next(a for a in sheet.attacks if a.name.startswith("Longsword"))
        assert "prof" in longsword.bonus.explanation
        assert "heavy" in sheet.armor_proficiencies


class TestWeaponMaster2024:
    def test_one_kind_of_weapon_is_mastered_until_a_long_rest(
        self, loaded_compendium: CompendiumStore, tmp_path: Path
    ) -> None:
        table = Table(
            tmp_path / "c",
            loaded_compendium,
            name="Iri",
            classes=[{"class": "Wizard|XPHB", "level": 4}],
            abilities={"str": 10, "dex": 14, "con": 14, "int": 16, "wis": 12, "cha": 8},
            feats=["Weapon Master|XPHB"],
            inventory=[{"item": "Dagger|XPHB", "equipped": True}],
        )
        sheet = build_sheet(table.now, loaded_compendium)
        assert sheet.weapon_master and sheet.mastery_open
        assert "Dagger" in sheet.mastery_choices and "Longsword" not in sheet.mastery_choices
        dagger = next(a for a in sheet.attacks if a.name == "Dagger")
        assert dagger.mastery and not sheet.masters(dagger)
        table.act("mastery", kind="Dagger")
        sheet = build_sheet(table.now, loaded_compendium)
        assert sheet.masters(next(a for a in sheet.attacks if a.name == "Dagger"))
        with pytest.raises(PlayError):  # chosen until a long rest
            table.act("mastery", kind="Quarterstaff")
        table.act("rest", kind="long")
        assert build_sheet(table.now, loaded_compendium).mastery_open
        table.act("mastery", kind="Quarterstaff")
        assert table.now.state.mastery_kind == "Quarterstaff"


class TestTakenTwice:
    def test_elemental_adept_twice_asks_two_damage_types_and_the_sheet_shows_each(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _drafted(
            planner,
            {
                "class": ["Wizard|XPHB"],
                "level": ["8"],
                "species": ["Human|XPHB"],
                "background": ["Sage|XPHB"],
                "abilities.method": ["standard"],
                "asi.4": ["feat:Elemental Adept|XPHB"],
                "asi.8": ["feat:Elemental Adept|XPHB"],
                "hp": ["average"],
            },
            int=15,
        )
        pick = next(
            p for p in planner.plan(pc).step("features").picks if p.id.endswith(".choice.damage")
        )
        assert pick.count == 2
        planner.answer(pc, pick.id, ["fire", "cold"])
        planner.apply(pc)
        assert [f.name for f in pc.feats].count("Elemental Adept") == 2
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2024))
        shown = [f.chosen for f in sheet.features if f.name == "Elemental Adept"]
        assert shown == [("fire",), ("cold",)]

    def test_level_up_offers_a_feat_again_only_when_it_may_be_taken_again(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2024))
        pc = _ready(
            {
                "classes": [{"class": "Wizard|XPHB", "level": 7}],
                "abilities": {"str": 8, "dex": 14, "con": 14, "int": 16, "wis": 12, "cha": 10},
                "feats": ["Elemental Adept|XPHB", "Alert|XPHB"],
                "choices": {"feat.elemental adept|xphb.choice.damage": ["fire"]},
            }
        )
        asi = next(p for p in levels.plan(pc).picks if p.id == "asi")
        assert "feat:Elemental Adept|XPHB" in asi.options
        assert "feat:Alert|XPHB" not in asi.options
        levels.answer(pc, "asi", ["feat:Elemental Adept|XPHB"])
        damage = next(p for p in levels.plan(pc).picks if p.id.endswith(".choice.damage"))
        assert "fire" not in damage.options and damage.count == 1
        levels.answer(pc, damage.id, ["lightning"])
        plan = levels.plan(pc)
        for pick in plan.picks:  # the rest of the level: anything allowed
            if pick.id in plan.missing:
                options = levels.planner.options(pick)
                allowed = [
                    o for o in options if plan.verdicts.get(o) is None or plan.verdicts[o].allowed
                ]
                levels.answer(pc, pick.id, allowed[: pick.count])
        levels.take(pc)
        assert pc.choices["feat.elemental adept|xphb.choice.damage"] == ["fire", "lightning"]

    def test_elemental_adept_twice_raises_a_score_each_time(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _drafted(
            planner,
            {
                "class": ["Wizard|XPHB"],
                "level": ["8"],
                "species": ["Human|XPHB"],
                "background": ["Sage|XPHB"],
                "abilities.method": ["standard"],
                "asi.4": ["feat:Elemental Adept|XPHB"],
                "asi.8": ["feat:Elemental Adept|XPHB"],
                "hp": ["average"],
            },
            int=15,
        )
        asked = [
            p
            for p in planner.plan(pc).step("features").picks
            if p.id.startswith("feat.Elemental Adept|XPHB.ability")
        ]
        assert [p.id for p in asked] == [
            "feat.Elemental Adept|XPHB.ability.0",
            "feat.Elemental Adept|XPHB.ability2.0",
        ]
        assert asked[1].origin == "Elemental Adept (2)"
        planner.answer(pc, asked[0].id, ["int"])
        planner.answer(pc, asked[1].id, ["wis"])
        planner.apply(pc)
        raised = [i for i in pc.ability_improvements if set(i) <= {"int", "wis"}]
        assert {"int": 1} in raised and {"wis": 1} in raised

    def test_a_feat_taken_once_keeps_its_one_question(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _drafted(
            planner,
            {
                "class": ["Wizard|XPHB"],
                "level": ["4"],
                "species": ["Human|XPHB"],
                "background": ["Sage|XPHB"],
                "abilities.method": ["standard"],
                "asi.4": ["feat:Elemental Adept|XPHB"],
                "hp": ["average"],
            },
            int=15,
        )
        ids = [p.id for p in planner.plan(pc).step("features").picks]
        assert "feat.Elemental Adept|XPHB.ability.0" in ids
        assert not any(".ability2" in i for i in ids)
