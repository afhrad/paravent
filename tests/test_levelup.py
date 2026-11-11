"""Levelling up (documentation/CHARGEN-WIZARD.md §9) — against the real corpus."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import DraftStatus, HitPointMethod, PlayerCharacter
from app.services.chargen import Catalogue, Planner
from app.services.chargen.levelup import LevelUpError, LevelUpPlanner
from app.services.chargen.rules import meets, multiclass_requirement
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables
from app.store.registry import StoreRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "documentation" / "examples"
BOOKS_2014 = ["PHB", "MM", "DMG", "XGE", "TCE"]
BOOKS_2024 = ["XPHB", "XDMG", "XMM"]
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


def _levels(compendium: CompendiumStore, lookups: LookupTables, books: list[str]) -> LevelUpPlanner:
    return LevelUpPlanner(Planner(Catalogue(compendium, SourceSet(sources=books), lookups)))


def _character(data: dict[str, object]) -> PlayerCharacter:
    return PlayerCharacter.model_validate({"status": "ready", "pending_levels": 1, **data})


class TestPlan:
    def test_a_hand_written_wizard_adds_two_spells_to_the_book(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter.model_validate(
            json.loads((EXAMPLES / "lyra-mooncaller.json").read_text(encoding="utf-8"))
        )
        pc.pending_levels = 1
        plan = levels.plan(pc)
        assert (plan.from_level, plan.to_level) == (10, 11)
        known = next(p for p in plan.picks if p.id == "spells.known")
        assert known.count == 2
        assert "Magic Missile|PHB" not in known.options  # already in the book
        assert any(o.startswith("Disintegrate|") for o in known.options)  # level 6 now
        assert plan.hp_gain == 4 + 2  # d6 average 4, CON +2

    def test_nothing_without_a_granted_level(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character({"classes": [{"class": "Fighter|PHB", "level": 3}], "pending_levels": 0})
        with pytest.raises(LevelUpError):
            levels.plan(pc)

    def test_the_subclass_when_it_is_due(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2024)
        pc = _character({"classes": [{"class": "Fighter|XPHB", "level": 2}]})
        plan = levels.plan(pc)
        assert [p.id for p in plan.picks] == ["subclass"]
        levels.answer(pc, "subclass", ["Champion|XPHB"])
        assert ("Improved Critical", 3) in levels.plan(pc).gains

    def test_one_more_invocation_or_two(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2024)
        pc = _character(
            {
                "classes": [{"class": "Warlock|XPHB", "level": 4, "subclass": "Fiend Patron|XPHB"}],
                "optional_features": ["Armor of Shadows|XPHB"],
            }
        )
        invocations = next(p for p in levels.plan(pc).picks if p.id.startswith("opt."))
        assert invocations.count == 5 - 3
        assert "Armor of Shadows|XPHB" not in invocations.options

    def test_a_feat_brings_its_own_increase(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Fighter|PHB", "level": 3}],
                "abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 10, "cha": 10},
            }
        )
        levels.answer(pc, "asi", ["str", "", "feat:Resilient|PHB"])
        assert pc.choices["levelup.asi"] == ["feat:Resilient|PHB"]
        plan = levels.plan(pc)
        extra = next(p for p in plan.picks if p.id.startswith("asi.feat"))
        assert extra.kind == "ability" and extra.count == 1


class TestTake:
    def test_an_increase_past_20_is_refused(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Fighter|PHB", "level": 3, "subclass": "Champion|PHB"}],
                "abilities": {"str": 19, "dex": 14, "con": 14, "int": 8, "wis": 10, "cha": 10},
                "hit_points": {"method": "average"},
                "pending_levels": 1,
            }
        )
        levels.answer(pc, "asi", ["str", "con"])
        assert not levels.plan(pc).problems
        levels.answer(pc, "asi", ["str", "str"])
        assert [p.key for p in levels.plan(pc).problems] == ["chargen.problem.ability_cap"]

    def test_a_level_is_taken_and_recorded(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Fighter|PHB", "level": 3, "subclass": "Champion|PHB"}],
                "abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 10, "cha": 10},
                "hit_points": {"method": "rolled", "per_level": [10, 6]},
                "state": {"hp": 20},
            }
        )
        with pytest.raises(LevelUpError):
            levels.take(pc)  # the increase is not chosen yet
        levels.answer(pc, "asi", ["str", "con"])
        levels.answer(pc, "hp", ["7"])
        # what the level brought, for its entry in the log (LevelUps.take writes it)
        assert levels.take(pc) == ("Fighter", 4, 7 + 2)  # the die, and CON
        assert pc.classes[0].level == 4 and pc.pending_levels == 0
        # level 3 had no result: written out as the average, so 7 belongs to level 4
        assert pc.hit_points.per_level == [10, 6, 6, 7]
        assert pc.ability_improvements == [{"str": 1, "con": 1}]
        assert pc.state.hp == 20 + 7 + 2  # the new hit points arrive with the level
        assert pc.choices == {
            "levels.4.asi": ["str", "con"],
            "levels.4.hp": ["7"],
            "levels.4.class": ["Fighter|PHB"],
        }


class TestSpellsChangingOnLevelUp:
    def test_a_2024_sorcerer_adds_to_its_prepared_list(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2024)
        pc = _character(
            {
                "classes": [{"class": "Sorcerer|XPHB", "level": 4}],
                "abilities": {"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 16},
                "spells": {"prepared": ["Magic Missile|XPHB", "Shield|XPHB"]},
            }
        )
        plan = levels.plan(pc)
        grown = next(p for p in plan.picks if p.id == "spells.prepared")
        assert grown.count >= 1 and "Magic Missile|XPHB" not in grown.options
        levels.answer(pc, "spells.prepared", list(grown.options[: grown.count]))
        assert "spells.prepared" not in levels.plan(pc).missing

    def test_a_known_spell_may_be_replaced(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [
                    {"class": "Sorcerer|PHB", "level": 4, "subclass": "Draconic Bloodline|PHB"}
                ],
                "abilities": {"cha": 16, "con": 14},
                "spells": {
                    "cantrips": ["Fire Bolt|PHB"],
                    "known": ["Magic Missile|PHB", "Shield|PHB", "Burning Hands|PHB"],
                },
            }
        )
        plan = levels.plan(pc)
        assert "swap.spell.out" not in plan.missing  # a swap may be left alone
        levels.answer(pc, "swap.spell.out", ["Burning Hands|PHB"])
        assert "swap.spell.in" in levels.plan(pc).missing  # half of it owes the rest
        levels.answer(pc, "swap.spell.in", ["Scorching Ray|PHB"])
        known = next(p for p in levels.plan(pc).picks if p.id == "spells.known")
        levels.answer(pc, "spells.known", [known.options[0]])
        levels.take(pc)
        names = [s.name for s in pc.spells.known]
        assert "Burning Hands" not in names and "Scorching Ray" in names

    def test_a_2024_caster_may_replace_a_cantrip(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2024)
        pc = _character(
            {
                "classes": [{"class": "Wizard|XPHB", "level": 2, "subclass": "Evoker|XPHB"}],
                "abilities": {"int": 16},
                "spells": {
                    "cantrips": ["Fire Bolt|XPHB", "Light|XPHB", "Mage Hand|XPHB"],
                    "known": ["Magic Missile|XPHB", "Shield|XPHB"],
                },
            }
        )
        out = next(p for p in levels.plan(pc).picks if p.id == "swap.cantrip.out")
        assert "Light|XPHB" in out.options
        levels.answer(pc, "swap.cantrip.out", ["Light|XPHB"])
        levels.answer(pc, "swap.cantrip.in", ["Ray of Frost|XPHB"])
        plan = levels.plan(pc)
        for pick in plan.picks:
            if pick.id in plan.missing and pick.options:
                levels.answer(pc, pick.id, list(pick.options[: pick.count]))
        levels.take(pc)
        names = [c.name for c in pc.spells.cantrips]
        assert "Light" not in names and "Ray of Frost" in names

    def test_a_2014_caster_keeps_its_cantrips(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Wizard|PHB", "level": 2, "subclass": "Evocation|PHB"}],
                "abilities": {"int": 16},
                "spells": {"cantrips": ["Fire Bolt|PHB"], "known": ["Magic Missile|PHB"]},
            }
        )
        assert "swap.cantrip.out" not in [p.id for p in levels.plan(pc).picks]

    def test_a_warlock_may_replace_an_invocation(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Warlock|PHB", "level": 5, "subclass": "The Fiend|PHB"}],
                "abilities": {"cha": 16},
                "optional_features": [
                    "Agonizing Blast|PHB",
                    "Devil's Sight|PHB",
                    "Mask of Many Faces|PHB",
                ],
                "spells": {"cantrips": ["Eldritch Blast|PHB"], "known": ["Hex|PHB"]},
            }
        )
        out = next(p for p in levels.plan(pc).picks if p.id == "swap.opt.out")
        assert "Devil's Sight|PHB" in out.options
        levels.answer(pc, "swap.opt.out", ["Devil's Sight|PHB"])
        levels.answer(pc, "swap.opt.in", ["Armor of Shadows|PHB"])
        known = next(p for p in levels.plan(pc).picks if p.id == "spells.known")
        levels.answer(pc, "spells.known", [known.options[0]])
        levels.take(pc)
        names = [o.name for o in pc.optional_features]
        assert "Devil's Sight" not in names and "Armor of Shadows" in names


class TestMulticlass:
    def test_the_minimums_in_both_shapes(self) -> None:
        # 2014: plain keys all apply, an "or" group is any one of its keys
        fighter = {"multiclassing": {"requirements": {"or": [{"str": 13, "dex": 13}]}}}
        monk = {"multiclassing": {"requirements": {"dex": 13, "wis": 13}}}
        assert multiclass_requirement(fighter) == ({"str": 13}, {"dex": 13})
        assert multiclass_requirement(monk) == ({"dex": 13, "wis": 13},)
        # 2024: 13 in the primary ability, alternatives each needing all of theirs
        paladin = {"primaryAbility": [{"str": True, "cha": True}]}
        assert multiclass_requirement(paladin) == ({"str": 13, "cha": 13},)
        assert meets(multiclass_requirement(fighter), {"str": 8, "dex": 14})
        assert not meets(multiclass_requirement(monk), {"dex": 14, "wis": 12})
        assert meets((), {})

    def test_a_fighter_takes_a_rogue_level(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Fighter|PHB", "level": 3, "subclass": "Champion|PHB"}],
                "abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 10, "cha": 10},
                "skills": ["athletics", "perception"],
            }
        )
        plan = levels.plan(pc)
        offered = next(p for p in plan.picks if p.id == "class").options
        assert offered[0] == "Fighter|PHB" and "Rogue|PHB" in offered
        assert "Wizard|PHB" not in offered
        assert ("Wizard", ({"int": 13},)) in [(c.name, need) for c, need in plan.blocked]
        assert plan.cls.reference == "Fighter|PHB"  # unanswered: the first class

        levels.answer(pc, "class", ["Rogue|PHB"])
        plan = levels.plan(pc)
        assert plan.new_class and (plan.to_level, plan.character_level) == (1, 4)
        assert ("Sneak Attack", 1) in plan.gains
        skill = next(p for p in plan.picks if p.id.startswith("mc.skill"))
        assert skill.count == 1 and "stealth" in skill.options
        assert "athletics" not in skill.options  # already proficient
        assert plan.hp_gain == 5 + 2  # d8 average, CON +2
        levels.answer(pc, skill.id, ["stealth"])
        # A Rogue's first level brings Expertise, from the proficiencies just gained too
        expert = next(p for p in levels.plan(pc).picks if p.id == "expertise.rogue.1")
        assert {"stealth", "thieves' tools", "athletics"} <= set(expert.options)
        levels.answer(pc, expert.id, ["stealth", "thieves' tools"])
        assert levels.take(pc)[:2] == ("Rogue", 1)
        assert [e.name for e in pc.expertise] == ["Stealth", "Thieves' tools"]

        assert [(c.name, c.level) for c in pc.classes] == [("Fighter", 3), ("Rogue", 1)]
        assert "Stealth" in [s.name for s in pc.skills]
        assert [t.name.lower() for t in pc.tools] == ["thieves' tools"]
        assert pc.choices["levels.4.class"] == ["Rogue|PHB"]

    def test_a_new_casters_spells_count_as_if_it_were_alone(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Paladin|PHB", "level": 5}],
                "abilities": {"str": 15, "dex": 10, "con": 14, "int": 14, "wis": 10, "cha": 14},
            }
        )
        levels.answer(pc, "class", ["Wizard|PHB"])
        plan = levels.plan(pc)
        assert max(s.level for s in plan.after.slots) == 2  # the shared slots reach 2nd
        known = next(p for p in plan.picks if p.id == "spells.known")
        assert known.count == 6  # a first-level spellbook
        found = [levels.catalogue.find("spell", o) for o in known.options]
        assert {o.data.get("level") for o in found if o is not None} == {1}

    def test_a_2014_class_below_its_own_minimum_holds_the_rest(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2014)
        pc = _character(
            {
                "classes": [{"class": "Wizard|PHB", "level": 2}],
                "abilities": {"str": 15, "dex": 14, "con": 14, "int": 12, "wis": 10, "cha": 10},
            }
        )
        plan = levels.plan(pc)
        assert [c.name for c, _need in plan.held] == ["Wizard"]
        assert "class" not in [p.id for p in plan.picks]

    def test_2024_minimums_come_from_the_primary_ability(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        levels = _levels(loaded_compendium, lookups, BOOKS_2024)
        pc = _character(
            {
                "classes": [{"class": "Fighter|XPHB", "level": 3}],
                "abilities": {"str": 15, "dex": 10, "con": 14, "int": 8, "wis": 10, "cha": 13},
            }
        )
        offered = next(p for p in levels.plan(pc).picks if p.id == "class").options
        assert {"Paladin|XPHB", "Bard|XPHB", "Barbarian|XPHB"} <= set(offered)
        assert not any(o.endswith("|PHB") for o in offered)  # the class's own edition

    def test_a_second_class_adds_its_reduced_proficiencies(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        pc = _character(
            {
                "classes": [
                    {"class": "Wizard|PHB", "level": 3},
                    {"class": "Fighter|PHB", "level": 1},
                ]
            }
        )
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2014))
        assert "martial" in sheet.weapon_proficiencies
        assert "heavy" not in sheet.armor_proficiencies  # not gained by multiclassing
        assert "shield" in sheet.armor_proficiencies


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="Faerûn", source_set=SourceSet(sources=BOOKS_2024))
    )
    registry.gamestate.characters.create(
        PlayerCharacter.model_validate(
            {
                "id": "rhea",
                "name": "Rhea",
                "player": "alice",
                "world_id": world.id,
                "status": "ready",
                "classes": [{"class": "Fighter|XPHB", "level": 3, "subclass": "Champion|XPHB"}],
                "abilities": {"str": 15, "dex": 13, "con": 14, "int": 8, "wis": 12, "cha": 10},
            }
        )
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry


async def _login(ac: AsyncClient, user: str) -> None:
    await ac.post("/login", data={"username": user, "password": PASSWORD})


class TestPages:
    async def test_the_gamemaster_grants_and_the_player_takes(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "alice")
        assert (await ac.post("/characters/rhea/grant-level")).status_code == 403
        assert "Level up to" not in (await ac.get("/characters/rhea")).text

        await _login(ac, "dm")
        assert "Grant a level" in (await ac.get("/characters/rhea")).text
        await ac.post("/characters/rhea/grant-level")
        assert registry.gamestate.characters.get("rhea").pending_levels == 1
        assert "+1" in (await ac.get("/gm")).text  # the pending mark on the party card

        await _login(ac, "alice")
        sheet = (await ac.get("/characters/rhea")).text
        assert "Level up to 4" in sheet
        page = (await ac.get("/characters/rhea/level-up")).text
        assert "Rhea → level 4" in page and "Level 4: ability increase or feat" in page
        refused = await ac.post("/characters/rhea/level-up", data={"go": "take"})
        assert refused.status_code == 200 and "Answer every question" in refused.text
        done = await ac.post(
            "/characters/rhea/level-up", data={"asi": ["str", "str"], "go": "take"}
        )
        assert done.headers["location"] == "/characters/rhea"
        pc = registry.gamestate.characters.get("rhea")
        assert pc.classes[0].level == 4 and pc.pending_levels == 0
        assert pc.ability_improvements == [{"str": 2}]
        # nothing granted: the page goes back to the sheet
        back = await ac.get("/characters/rhea/level-up")
        assert back.headers["location"] == "/characters/rhea"

    async def test_the_player_chooses_another_class(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        await ac.post("/characters/rhea/grant-level")
        await _login(ac, "alice")
        page = (await ac.get("/characters/rhea/level-up")).text
        assert "Which class gains this level?" in page and "Level 3 → 4" in page
        assert "Not open to you yet" in page  # with what the others need
        await ac.post("/characters/rhea/level-up", data={"class": "Rogue|XPHB", "go": "stay"})
        pc = registry.gamestate.characters.get("rhea")
        assert pc.choices["levelup.class"] == ["Rogue|XPHB"]
        page = (await ac.get("/characters/rhea/level-up")).text
        assert "Rogue level 1" in page and "Rhea → level 4" in page
        assert "Proficiencies: light" in page

    async def test_switching_class_drops_the_other_classs_answers(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        await ac.post("/characters/rhea/grant-level")
        await _login(ac, "alice")
        url = "/characters/rhea/level-up"
        await ac.post(url, data={"class": "Rogue|XPHB", "go": "stay"})
        await ac.post(url, data={"class": "Rogue|XPHB", "mc.skill.0": "stealth", "go": "stay"})
        assert "levelup.mc.skill.0" in registry.gamestate.characters.get("rhea").choices
        await ac.post(url, data={"class": "Fighter|XPHB", "go": "stay"})
        choices = registry.gamestate.characters.get("rhea").choices
        assert "levelup.mc.skill.0" not in choices

    async def test_the_class_is_fixed_once_its_hit_die_is_rolled(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client

        def rolled(pc: PlayerCharacter) -> None:
            pc.hit_points = pc.hit_points.model_copy(update={"method": HitPointMethod.ROLLED})

        registry.gamestate.characters.mutate("rhea", rolled)
        await _login(ac, "dm")
        await ac.post("/characters/rhea/grant-level")
        await _login(ac, "alice")
        await ac.post("/characters/rhea/level-up/roll-hp")
        await ac.post("/characters/rhea/level-up", data={"class": "Rogue|XPHB", "go": "stay"})
        pc = registry.gamestate.characters.get("rhea")
        assert "levelup.class" not in pc.choices and "levelup.hp" in pc.choices

    async def test_a_level_for_everyone_in_the_world(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        draft = registry.gamestate.characters.create(PlayerCharacter(name="Draft"))
        await _login(ac, "dm")
        await ac.post("/gm/party/grant-level", data={"world": ""})
        assert registry.gamestate.characters.get("rhea").pending_levels == 1
        assert registry.gamestate.characters.get(draft.id).status is DraftStatus.DRAFT
        assert registry.gamestate.characters.get(draft.id).pending_levels == 0
