"""Expertise (documentation/CHARGEN-WIZARD.md §11) — against the real corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.gamestate import SourceSet
from app.models.pc import Abilities, PlayerCharacter
from app.services.chargen import Catalogue, Planner
from app.services.chargen.levelup import LevelUpPlanner
from app.services.chargen.rules import _expertise_rows
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables

BOOKS_2014 = ["PHB", "MM", "DMG", "XGE", "TCE"]
BOOKS_2024 = ["XPHB", "XDMG", "XMM"]


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


def _planner(compendium: CompendiumStore, lookups: LookupTables, books: list[str]) -> Planner:
    return Planner(Catalogue(compendium, SourceSet(sources=books), lookups))


def _rogue(planner: Planner, level: int) -> PlayerCharacter:
    pc = PlayerCharacter(name="Nim")
    for pick_id, values in {
        "class": ["Rogue|PHB"],
        "level": [str(level)],
        "subclass": ["Thief|PHB"],
        "species": ["Halfling|PHB"],
        "subspecies": ["Lightfoot|PHB"],
        "background": ["Criminal|PHB"],
        "abilities.method": ["standard"],
        "asi.4": ["dex", "dex"],
        "hp": ["average"],
        "class.skills.0": ["acrobatics", "perception", "sleight of hand", "insight"],
    }.items():
        planner.answer(pc, pick_id, values)
    pc.abilities = Abilities.model_validate(
        {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 10, "cha": 13}
    )
    return pc


class TestRulesFile:
    def test_every_row_names_a_class_feature_at_its_level(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        features = {
            (
                str(d.get("name")),
                str(d.get("className")),
                str(d.get("classSource")),
                int(d.get("level", 0)),
            )
            for d in (
                e.model_dump(by_alias=True, exclude_none=True)
                for e in loaded_compendium.all("classFeature")
            )
        }
        for row in _expertise_rows():
            assert (row.feature, row.cls, row.source, row.level) in features, row


class TestWizard:
    def test_a_rogue_chooses_twice_and_never_the_same(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _rogue(planner, 6)
        step = planner.plan(pc).step("skills")
        assert step is not None
        first = next(p for p in step.picks if p.id == "expertise.rogue.1")
        assert first.count == 2
        assert "thieves' tools" in first.options  # the 2014 rogue may choose the tools
        assert "stealth" in first.options  # from the background: proficient
        assert "athletics" not in first.options  # not proficient
        planner.answer(pc, first.id, ["stealth", "thieves' tools"])
        step = planner.plan(pc).step("skills")
        assert step is not None
        second = next(p for p in step.picks if p.id == "expertise.rogue.6")
        assert "stealth" not in second.options  # already chosen at level 1
        planner.answer(pc, second.id, ["perception", "acrobatics"])
        planner.apply(pc)
        assert [e.name for e in pc.expertise] == [
            "Stealth",
            "Thieves' tools",
            "Perception",
            "Acrobatics",
        ]
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2014))
        stealth = next(s for s in sheet.skills if s.key == "stealth")
        insight = next(s for s in sheet.skills if s.key == "insight")
        assert stealth.expertise and not insight.expertise
        prof = sheet.proficiency.value or 0
        dex = next(a for a in sheet.abilities if a.key == "dex").modifier
        assert stealth.value.value == dex + 2 * prof

    def test_below_the_level_nothing_is_asked(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter(name="Bard")
        planner.answer(pc, "class", ["Bard|PHB"])
        planner.answer(pc, "level", ["2"])
        step = planner.plan(pc).step("skills")
        assert step is not None
        assert not [p for p in step.picks if p.kind == "expertise"]  # a bard's comes at 3


class TestLevelUp:
    def test_the_sixth_rogue_level(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2014))
        pc = PlayerCharacter.model_validate(
            {
                "status": "ready",
                "pending_levels": 1,
                "classes": [{"class": "Rogue|PHB", "level": 5, "subclass": "Thief|PHB"}],
                "abilities": {"dex": 16},
                "skills": ["Stealth", "Perception", "Acrobatics", "Deception"],
                "tools": ["Thieves' tools"],
                "expertise": ["Stealth", "Thieves' tools"],
            }
        )
        pick = next(p for p in levels.plan(pc).picks if p.id == "expertise.rogue.6")
        assert set(pick.options) == {"acrobatics", "deception", "perception"}
        levels.answer(pc, pick.id, ["perception", "deception"])
        levels.take(pc)
        assert [e.name for e in pc.expertise] == [
            "Stealth",
            "Thieves' tools",
            "Perception",
            "Deception",
        ]
        assert pc.choices["levels.6.expertise.rogue.6"] == ["perception", "deception"]

    def test_a_feat_brings_its_skill_and_its_expertise(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2014))
        pc = PlayerCharacter.model_validate(
            {
                "status": "ready",
                "pending_levels": 1,
                "classes": [{"class": "Fighter|PHB", "level": 3, "subclass": "Champion|PHB"}],
                "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 12, "cha": 8},
                "skills": ["Athletics", "Perception"],
            }
        )
        levels.answer(pc, "asi", ["feat:Skill Expert|TCE"])
        plan = levels.plan(pc)
        skill = next(p for p in plan.picks if p.kind == "skill")
        assert "athletics" not in skill.options  # already proficient
        levels.answer(pc, skill.id, ["stealth"])
        increase = next(p for p in plan.picks if p.kind == "ability")
        levels.answer(pc, increase.id, ["str"])
        expert = next(p for p in levels.plan(pc).picks if p.kind == "expertise")
        assert "stealth" in expert.options  # the skill the feat just gave
        levels.answer(pc, expert.id, ["stealth"])
        levels.take(pc)
        assert "Stealth" in [s.name for s in pc.skills]
        assert [e.name for e in pc.expertise] == ["Stealth"]

    def test_a_2024_wizards_scholar_is_limited_to_its_list(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, BOOKS_2024))
        pc = PlayerCharacter.model_validate(
            {
                "status": "ready",
                "pending_levels": 1,
                "classes": [{"class": "Wizard|XPHB", "level": 1}],
                "abilities": {"int": 16},
                "skills": ["Arcana", "History", "Stealth"],
            }
        )
        scholar = next(p for p in levels.plan(pc).picks if p.kind == "expertise")
        assert set(scholar.options) == {"arcana", "history"} and scholar.count == 1
