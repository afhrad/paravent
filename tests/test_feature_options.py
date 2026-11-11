"""Choices between features (app/services/feature_options.py): asked in the wizard and on
level-up, kept in a class's ``options``, and only the option taken on the sheet."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.gamestate import SourceSet
from app.models.pc import ClassLevel, PlayerCharacter
from app.services.chargen import Catalogue, Planner
from app.services.chargen.levelup import LevelUpPlanner
from app.services.feature_options import option_blocks, parse_ref
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


def _planner(compendium: CompendiumStore, lookups: LookupTables, books: list[str]) -> Planner:
    return Planner(Catalogue(compendium, SourceSet(sources=books), lookups))


class TestReading:
    def test_references(self) -> None:
        ref = parse_ref("refSubclassFeature", "Elk|Barbarian|PHB|Totem Warrior||3|SCAG")
        assert ref is not None and (ref.name, ref.subclass, ref.level, ref.source) == (
            "Elk",
            "Totem Warrior",
            3,
            "SCAG",
        )
        ref = parse_ref("refClassFeature", "Protector|Cleric|XPHB|1|XPHB")
        assert ref is not None and ref.kind == "classFeature" and ref.level == 1

    def test_only_counted_blocks_are_choices(self) -> None:
        entries = [
            {
                "type": "options",
                "count": 1,
                "entries": [
                    {"type": "refSubclassFeature", "subclassFeature": "A|X||Y||3"},
                    {"type": "refSubclassFeature", "subclassFeature": "B|X||Y||3"},
                ],
            },
            {
                "type": "options",  # a grouping: every one is had
                "entries": [{"type": "refSubclassFeature", "subclassFeature": "C|X||Y||3"}],
            },
        ]
        blocks = list(option_blocks(entries))
        assert len(blocks) == 1 and [r.name for r in blocks[0].options] == ["A", "B"]


def _hunter(level: int, **options: str) -> PlayerCharacter:
    return PlayerCharacter(
        name="Kael",
        classes=[
            ClassLevel.model_validate(
                {
                    "class": "Ranger|PHB",
                    "level": level,
                    "subclass": "Hunter|PHB",
                    "options": options,
                }
            )
        ],
    )


class TestSheet:
    def test_only_the_option_taken(self, loaded_compendium: CompendiumStore) -> None:
        books = SourceSet(sources=["PHB"])
        unchosen = {
            f.name for f in build_sheet(_hunter(3), loaded_compendium, source_set=books).features
        }
        assert "Hunter's Prey" in unchosen
        assert not {"Colossus Slayer", "Giant Killer", "Horde Breaker"} & unchosen
        chosen = build_sheet(
            _hunter(3, **{"Hunter's Prey": "Giant Killer"}), loaded_compendium, source_set=books
        )
        names = {f.name for f in chosen.features}
        assert "Giant Killer" in names and not {"Colossus Slayer", "Horde Breaker"} & names

    def test_a_2024_class_option(self, loaded_compendium: CompendiumStore) -> None:
        pc = PlayerCharacter(
            name="Iri",
            classes=[
                ClassLevel.model_validate(
                    {"class": "Cleric|XPHB", "level": 1, "options": {"Divine Order": "Protector"}}
                )
            ],
        )
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=["XPHB"]))
        protector = next(f for f in sheet.features if f.name == "Protector")
        assert "Divine Order" in protector.origin and protector.text

    def test_the_land_chosen_gives_its_circle_spells(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        def druid(**options: str) -> PlayerCharacter:
            return PlayerCharacter(
                name="Fen",
                classes=[
                    ClassLevel.model_validate(
                        {
                            "class": "Druid|PHB",
                            "level": 3,
                            "subclass": "Land|PHB",
                            "options": options,
                        }
                    )
                ],
            )

        books = SourceSet(sources=["PHB"])
        assert not [
            s for s in build_sheet(druid(), loaded_compendium, source_set=books).spells if s.always
        ]
        coast = build_sheet(druid(spells="Coast"), loaded_compendium, source_set=books)
        assert {s.name for s in coast.spells if s.always} >= {"Mirror Image", "Misty Step"}


class TestCantripChoice:
    @pytest.mark.parametrize(
        ("subclass", "cls", "offered", "books"),
        [
            (
                "Path of the Giant|BGG",
                "Barbarian|PHB",
                ("Druidcraft", "Thaumaturgy"),
                ["PHB", "BGG"],
            ),
            (
                "Arcane Archer|XGE",
                "Fighter|PHB",
                ("Prestidigitation", "Druidcraft"),
                ["PHB", "XGE"],
            ),
        ],
    )
    def test_asked_at_its_level_and_shown_on_the_sheet(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,
        subclass: str,
        cls: str,
        offered: tuple[str, ...],
        books: list[str],
    ) -> None:
        planner = _planner(loaded_compendium, lookups, books)
        pc = PlayerCharacter(name="Kael")
        for pick_id, values in {"class": [cls], "level": ["2"]}.items():
            planner.answer(pc, pick_id, values)
        pick = "featopt.cantrip"
        assert not [p for s in planner.plan(pc).steps for p in s.picks if p.id == pick]

        planner.answer(pc, "level", ["3"])
        planner.answer(pc, "subclass", [subclass])
        step = next(s for s in planner.plan(pc).steps if s.id == "features")
        asked = next(p for p in step.picks if p.id == pick)
        assert asked.options == offered and pick in step.missing

        planner.answer(pc, pick, [offered[1]])
        planner.apply(pc)
        assert pc.classes[0].options["cantrip"] == offered[1]
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=books))
        names = {s.name for s in sheet.spells}
        assert offered[1] in names and offered[0] not in names


class TestWizardAndLevelUp:
    def test_the_wizard_asks_and_keeps_the_choice(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, ["PHB"])
        pc = PlayerCharacter(name="Kael")
        for pick_id, values in {
            "class": ["Ranger|PHB"],
            "level": ["3"],
            "subclass": ["Hunter|PHB"],
        }.items():
            planner.answer(pc, pick_id, values)
        features = planner.plan(pc).step("features")
        assert features is not None
        prey = next(p for p in features.picks if p.id == "featopt.Hunter's Prey")
        assert prey.kind == "featureoption" and "Colossus Slayer" in prey.options
        assert prey.details[0]  # its text, to choose by
        planner.answer(pc, prey.id, ["Colossus Slayer"])
        planner.apply(pc)
        assert pc.classes[0].options == {"Hunter's Prey": "Colossus Slayer"}

    def test_level_up_asks_the_new_levels_choice(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = LevelUpPlanner(_planner(loaded_compendium, lookups, ["PHB"]))
        pc = _hunter(6, **{"Hunter's Prey": "Colossus Slayer"})
        pc.status = pc.status.READY  # type: ignore[assignment]
        pc.pending_levels = 1
        ids = [p.id for p in levels.plan(pc).picks]
        assert "featopt.Defensive Tactics" in ids and "featopt.Hunter's Prey" not in ids
        levels.answer(pc, "featopt.Defensive Tactics", ["Steel Will"])
        levels.answer(pc, "hp", ["average"])
        known = next(p for p in levels.plan(pc).picks if p.id == "spells.known")
        levels.answer(pc, known.id, list(known.options[: known.count]))  # a ranger's spell
        levels.take(pc)
        assert pc.classes[0].options == {
            "Hunter's Prey": "Colossus Slayer",
            "Defensive Tactics": "Steel Will",
        }
