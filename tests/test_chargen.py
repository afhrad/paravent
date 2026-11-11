"""The character creation engine (documentation/CHARGEN-WIZARD.md §8 step 1).

The block reader, prerequisites and formula are tested on literal corpus shapes; the
catalogue, level plan and plans against the real corpus.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import Abilities, AbilityMethod, DraftStatus, PlayerCharacter
from app.services.auth import Identity
from app.services.chargen import Catalogue, Planner
from app.services.chargen.blocks import (
    ABILITY_LIMIT,
    Pick,
    ability_block,
    ability_increases,
    feat_block,
    feat_reference,
    over_limit,
    proficiencies,
)
from app.services.chargen.drafts import DraftError
from app.services.chargen.plan import evaluate_prepared, level_plan
from app.services.chargen.prereq import Context, check
from app.services.chargen.rules import ability_problem, point_cost
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables
from app.store.registry import StoreRegistry
from tests.conftest import log_about

BOOKS_2014 = ["PHB", "MM", "DMG"]
BOOKS_2024 = ["XPHB", "XDMG", "XMM"]


class TestBlocks:
    def test_fixed_and_chosen_skills(self) -> None:
        block = [{"insight": True, "choose": {"from": ["arcana", "history"], "count": 1}}]
        grants, picks = proficiencies(block, "skill", "Sage", "background.skills", {})
        assert [g.value for g in grants] == ["insight"]
        assert picks[0].options == ("arcana", "history") and picks[0].count == 1

    def test_any_of_a_pool(self) -> None:
        grants, picks = proficiencies(
            [{"common": True, "anyStandard": 1}], "language", "Human", "species.languages", {}
        )
        assert [g.value for g in grants] == ["common"]
        assert (picks[0].pool, picks[0].count, picks[0].options) == ("standard", 1, ())

    def test_a_pool_named_inside_a_choose(self) -> None:
        block = [{"choose": {"from": ["anyArtisansTool", "anyMusicalInstrument"]}}]
        _, picks = proficiencies(block, "tool", "Guild", "background.tools", {})
        assert picks[0].pool and picks[0].options == ()

    def test_alternatives_are_a_question_of_their_own(self) -> None:
        block = [{"dwarvish": True}, {"anyStandard": 1}]
        _, picks = proficiencies(block, "language", "Clan Crafter", "bg.languages", {})
        assert picks[0].id == "bg.languages.variant" and picks[0].options == ("0", "1")
        grants, picks = proficiencies(
            block, "language", "Clan Crafter", "bg.languages", {"bg.languages.variant": ["1"]}
        )
        assert grants == [] and picks[1].pool == "standard"

    def test_weighted_increases_are_a_distribution(self) -> None:
        block = [
            {"choose": {"weighted": {"from": ["con", "int", "wis"], "weights": [2, 1]}}},
            {"choose": {"weighted": {"from": ["con", "int", "wis"], "weights": [1, 1, 1]}}},
        ]
        _, picks = ability_block(block, "Sage", "background.ability", {})
        pick = picks[1]
        assert pick.weights == (2, 1) and pick.count == 2
        assert ability_increases(pick, ["int", "con"]) == {"int": 2, "con": 1}
        _, picks = ability_block(
            block, "Sage", "background.ability", {"background.ability.variant": ["1"]}
        )
        assert picks[1].weights == (1, 1, 1)

    def test_fixed_species_bonus_and_a_choice(self) -> None:
        fixed, picks = ability_block(
            [{"cha": 2, "choose": {"from": ["str", "dex"], "count": 2}}], "Half-Elf", "sp", {}
        )
        assert fixed == {"cha": 2} and picks[0].count == 2 and picks[0].amount == 1

    def test_feats_fixed_and_by_category(self) -> None:
        grants, _ = feat_block([{"magic initiate; wizard|xphb": True}], "Sage", "bg.feat", {})
        assert feat_reference(grants[0].value) == ("magic initiate", "XPHB", "wizard")
        _, picks = feat_block(
            [{"anyFromCategory": {"category": ["O"], "count": 1}}], "Human", "sp.feat", {}
        )
        assert picks[0].kind == "feat" and picks[0].pool == "O"


class TestPrerequisites:
    def test_any_alternative_is_enough(self) -> None:
        prereq = [{"ability": [{"str": 13}, {"dex": 13}]}]
        assert check(prereq, Context(abilities={"dex": 14})).allowed
        verdict = check(prereq, Context(abilities={"str": 10, "dex": 10}))
        assert not verdict.allowed and "STR 13" in str(verdict.unmet[0])

    def test_level_and_caster(self) -> None:
        assert not check([{"level": 4}], Context(level=3)).allowed
        assert not check([{"spellcasting2020": True}], Context(level=5)).allowed

    def test_prose_is_confirmed_not_decided(self) -> None:
        verdict = check([{"other": "Must have a patron"}], Context())
        assert verdict.allowed and verdict.confirm == ("Must have a patron",)


class TestNumbers:
    def test_prepared_formula(self) -> None:
        assert evaluate_prepared("<$level$> + <$int_mod$>", 3, {"int": 2}) == 5
        assert evaluate_prepared("<$level$> + <$int_mod$>", 1, {"int": -3}) == 1
        assert evaluate_prepared("<$level$> / 2 + <$cha_mod$>", 5, {"cha": 3}) == 5
        assert evaluate_prepared("<$level$> ^ 2", 1, {}) is None

    def test_ability_methods(self) -> None:
        pc = PlayerCharacter(ability_method=AbilityMethod.STANDARD)
        pc.abilities = Abilities.model_validate(
            {"str": 15, "dex": 14, "con": 13, "int": 12, "wis": 10, "cha": 8}
        )
        assert ability_problem(pc) is None
        pc.abilities = Abilities.model_validate(
            {"str": 15, "dex": 15, "con": 13, "int": 12, "wis": 10, "cha": 8}
        )
        assert ability_problem(pc) == "chargen.problem.standard"
        assert point_cost({"str": 15, "dex": 15, "con": 15, "int": 8, "wis": 8, "cha": 8}) == 27


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


def _planner(compendium: CompendiumStore, lookups: LookupTables, books: list[str]) -> Planner:
    return Planner(Catalogue(compendium, SourceSet(sources=books), lookups))


class TestCatalogue:
    def test_classes_and_subclasses_of_the_books(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups)
        names = [o.name for o in cat.classes()]
        assert "Wizard" in names and "Artificer" not in names and len(names) == 12
        wizard = cat.find("class", "Wizard|PHB")
        assert wizard is not None
        assert "School of Evocation" in [o.name for o in cat.subclasses(wizard)]

    def test_both_editions_offer_both_printings(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(sources=["PHB", "XPHB"]), lookups)
        fighters = [o.reference for o in cat.classes() if o.name == "Fighter"]
        assert fighters == ["Fighter|PHB", "Fighter|XPHB"]
        # species and backgrounds follow the class's edition
        assert {o.edition for o in cat.backgrounds("one")} == {"one"}
        assert {o.edition for o in cat.species("classic")} == {"classic"}

    def test_spell_lists_come_from_the_lookup(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups)
        wizard = cat.find("class", "Wizard|PHB")
        assert wizard is not None
        spells = {o.name for o in cat.class_spells(wizard, 1)}
        assert "Magic Missile" in spells and "Cure Wounds" not in spells
        assert "Fireball" not in spells  # level 3

    def test_deferred_equipment_pickers(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups)
        martial = {o.name for o in cat.equipment("weaponMartial")}
        assert "Longsword" in martial and "Dagger" not in martial
        assert "Crystal" in {o.name for o in cat.equipment("focusSpellcastingArcane")}


class TestLevelPlan:
    def test_fighter_2024_at_five(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2024), lookups)
        fighter = cat.find("class", "Fighter|XPHB")
        assert fighter is not None
        plan = level_plan(fighter, None, 5, None)
        assert plan.subclass_level == 3 and plan.asi_levels == (4,)
        assert plan.feat_progressions == (("Fighting Style", ("FS",), 1),)

    @pytest.mark.parametrize(
        ("ref", "level", "name", "count"),
        [("Sorcerer|PHB", 10, "Metamagic", 3), ("Warlock|XPHB", 5, "Eldritch Invocations", 5)],
    )
    def test_optional_features_by_level(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,
        ref: str,
        level: int,
        name: str,
        count: int,
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(), lookups)
        cls = cat.find("class", ref)
        assert cls is not None
        optional = {n: c for n, _types, c in level_plan(cls, None, level, None).optional}
        assert optional[name] == count


def _fighter(planner: Planner) -> PlayerCharacter:
    """A 2024 fighter at level 5, every question answered."""
    pc = PlayerCharacter(name="Rhea")
    for pick_id, values in {
        "class": ["Fighter|XPHB"],
        "level": ["5"],
        "subclass": ["Champion|XPHB"],
        "species": ["Elf|XPHB"],
        "lineage": ["Wood Elf"],
        "background": ["Sage|XPHB"],
        "background.ability.variant": ["0"],
        "background.ability.0": ["con", "int"],
        "abilities.method": ["standard"],
        "asi.4": ["str", "str"],
        "featprog.Fighting Style": ["Defense|XPHB"],
        "hp": ["average"],
        "class.skills.0": ["athletics", "perception"],
        "species.skills.0": ["survival"],
        "rules.languages": ["dwarvish", "elvish"],
        "equipment": ["packages"],
        "equipment.class.0": ["a"],
        "equipment.background.0": ["a"],
        # The wood elf's spells and the Sage's Magic Initiate (Wizard).
        "granted.species.ability": ["wis"],
        "granted.feat:Magic Initiate|XPHB.ability": ["int"],
        "granted.feat:Magic Initiate|XPHB.Wizard Spells.0": ["Fire Bolt|XPHB", "Light|XPHB"],
        "granted.feat:Magic Initiate|XPHB.Wizard Spells.1": ["Shield|XPHB"],
    }.items():
        planner.answer(pc, pick_id, values)
    pc.abilities = Abilities.model_validate(
        {"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
    )
    return pc


class TestPlan:
    def test_the_2024_order_and_a_finished_fighter(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _fighter(planner)
        plan = planner.plan(pc)
        assert plan.ids[:4] == ("class", "species", "background", "abilities")
        # Not a caster, but the wood elf and the Sage's Magic Initiate bring spells.
        assert "spells" in plan.ids
        assert plan.complete, [(s.id, s.missing, [str(p) for p in s.problems]) for s in plan.steps]

        planner.apply(pc)
        assert [c.reference for c in pc.classes] == ["Fighter|XPHB"]
        assert pc.classes[0].level == 5 and pc.classes[0].subclass is not None
        skills = {s.name for s in pc.skills}
        assert skills == {"Arcana", "History", "Athletics", "Perception", "Survival"}
        assert {f.name for f in pc.feats} == {"Magic Initiate", "Defense"}
        assert pc.ability_improvements == [{"con": 2, "int": 1}, {"str": 2}]
        assert any(i.item.name == "Greatsword" for i in pc.inventory)
        assert pc.currency.gp == 4 + 8  # 4 GP from the fighter's A, 8 from the sage's
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2024))
        assert sheet.ability("str").score.value == 17 and sheet.hp_max.value
        # Druidcraft and, at 3rd level, Longstrider from the wood elf (WIS); the Sage's
        # Magic Initiate picks (INT).
        spells = {s.name: (s.origin, s.ability) for s in sheet.spells}
        assert spells["Druidcraft"] == ("Elf", "wis") and spells["Longstrider"] == ("Elf", "wis")
        assert spells["Fire Bolt"] == ("Magic Initiate", "int") and "Shield" in spells

    def test_the_2014_order_and_a_wizards_spells(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        planner.answer(pc, "class", ["Wizard|PHB"])
        planner.answer(pc, "level", ["3"])
        plan = planner.plan(pc)
        assert plan.ids[:4] == ("species", "class", "abilities", "background")
        spells = plan.step("spells")
        assert spells is not None
        counts = {p.id: p.count for p in spells.picks}
        assert counts["spells.cantrips"] == 3 and counts["spells.known"] == 6 + 2 + 2

    def test_level_one_skips_the_features_step(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        planner.answer(pc, "class", ["Wizard|PHB"])
        assert "features" not in planner.plan(pc).ids
        planner.answer(pc, "class", ["Fighter|PHB"])  # a fighting style at 1
        assert "features" in planner.plan(pc).ids

    def test_a_skill_twice_needs_review(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _fighter(planner)
        planner.answer(pc, "class.skills.0", ["athletics", "history"])  # the sage gives history
        step = planner.plan(pc).step("skills")
        assert step is not None and step.state == "review"
        assert "Sage" in str(step.problems[0])

    def test_changing_the_class_flags_what_no_longer_fits(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _fighter(planner)
        planner.answer(pc, "class", ["Wizard|XPHB"])
        plan = planner.plan(pc)
        skills = plan.step("skills")
        assert skills is not None and skills.state == "review"
        assert any(p.pick == "class.skills.0" for p in skills.problems)
        # the old answer is kept, not dropped: the player sees what to change
        assert pc.choices["class.skills.0"] == ["athletics", "perception"]

    def test_a_feat_with_an_unmet_prerequisite_is_refused(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _fighter(planner)
        planner.answer(pc, "asi.4", ["feat:Ritual Caster|XPHB"])  # needs INT/WIS/CHA 13
        step = planner.plan(pc).step("features")
        assert step is not None and step.state == "review"


PASSWORD = "correct horse battery"
HTMX = {"HX-Request": "true"}


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    registry.auth.create_user("bob", PASSWORD, Role.PLAYER)
    registry.gamestate.worlds.create(World(name="Faerûn", source_set=SourceSet(sources=BOOKS_2024)))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        yield ac, registry


class TestWizardPages:
    async def test_a_player_starts_and_walks_the_first_steps(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        assert 'href="/chargen/new"' in (await ac.get("/play")).text
        world = registry.gamestate.worlds.list()[0]
        assert world.name in (await ac.get("/chargen/new")).text

        started = await ac.post("/chargen", data={"world": world.id})
        assert started.status_code == 303
        draft_id = started.headers["location"].rsplit("/", 1)[1]
        draft = registry.gamestate.characters.get(draft_id)
        assert draft.player == "alice" and draft.world_id == world.id
        assert f'href="/chargen/{draft_id}"' in (await ac.get("/play")).text
        # 2024 books: the class comes first
        first = await ac.get(started.headers["location"])
        assert first.headers["location"] == f"/chargen/{draft_id}/class"
        url = first.headers["location"]
        page = (await ac.get(url)).text
        assert "Fighter" in page and "Step 1 of" in page

        # a change comes back with its consequence: the subclass question at level 3
        changed = await ac.post(url, data={"class": "Fighter|XPHB", "level": "3"}, headers=HTMX)
        assert changed.text.startswith('<form id="wizard"')
        assert "Champion" in changed.text and "Fighter 3" in changed.text
        # Continue is refused while a question is open
        refused = await ac.post(url, data={"class": "Fighter|XPHB", "level": "3", "go": "next"})
        assert refused.status_code == 200 and "Answer every question" in refused.text
        onwards = await ac.post(
            url,
            data={"class": "Fighter|XPHB", "level": "3", "subclass": "Champion|XPHB", "go": "next"},
        )
        assert onwards.headers["location"] == f"/chargen/{draft_id}/species"

        species = f"/chargen/{draft_id}/species"
        elf = await ac.post(species, data={"species": "Elf|XPHB"}, headers=HTMX)
        assert "Wood Elf" in elf.text  # the lineage question
        await ac.post(species, data={"species": "Elf|XPHB", "lineage": "Wood Elf"}, headers=HTMX)
        await ac.post(species, data={"species": "Dwarf|XPHB"}, headers=HTMX)
        choices = registry.gamestate.characters.get(draft_id).choices
        assert choices["species"] == ["Dwarf|XPHB"] and "lineage" not in choices
        # resuming goes back to where the player was
        assert (await ac.get(f"/chargen/{draft_id}")).headers["location"] == species

        await ac.post("/login", data={"username": "bob", "password": PASSWORD})
        assert (await ac.get(species)).status_code == 404

    async def test_names_come_in_the_readers_language(
        self, client: tuple[AsyncClient, StoreRegistry], tmp_path: Path
    ) -> None:
        ac, registry = client
        glossary = tmp_path / "lib" / "i18n" / "de" / "_glossary.json"
        glossary.parent.mkdir(parents=True, exist_ok=True)
        glossary.write_text(
            json.dumps(
                {
                    "class": {"Fighter": "Kämpfer"},
                    "subclass": {"Champion": "Champion"},
                    "race": {"Dwarf": "Zwerg"},
                    "baseitem": {"Chain Mail": "Kettenhemd"},
                }
            ),
            encoding="utf-8",
        )
        await ac.post("/language", data={"lang": "de", "next": "/"})
        world = registry.gamestate.worlds.list()[0]
        started = await ac.post("/chargen", data={"world": world.id})
        draft_id = started.headers["location"].rsplit("/", 1)[1]
        classes = (await ac.get(f"/chargen/{draft_id}/class")).text
        assert '<b title="Fighter">Kämpfer</b>' in classes
        assert 'data-name="kämpfer fighter"' in classes  # either name filters
        chosen = await ac.post(
            f"/chargen/{draft_id}/class", data={"class": "Fighter|XPHB", "level": "1"}, headers=HTMX
        )
        assert "Kämpfer 1" in chosen.text  # the panel's summary line
        species = (await ac.get(f"/chargen/{draft_id}/species")).text
        assert '<b title="Dwarf">Zwerg</b>' in species
        assert "<b>Elf</b>" in species  # unnamed: the English stands
        skills = (await ac.get(f"/chargen/{draft_id}/skills")).text
        assert '<th scope="row">Athletik</th>' in skills  # skills from the UI catalogue
        assert '<td class="muted">ST</td>' in skills  # the German ability abbreviation
        assert "Kämpfer" in skills and ">Fighter<" not in skills
        equipment = (await ac.get(f"/chargen/{draft_id}/equipment")).text
        assert "Kettenhemd" in equipment and "Chain Mail" not in equipment
        assert " GM" in equipment and " GP" not in equipment  # coins in German

    async def test_the_gamemaster_starts_one_for_a_player(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await ac.get("/chargen/new")).text
        assert 'name="player"' in page and "alice" in page
        world = registry.gamestate.worlds.list()[0]
        started = await ac.post("/chargen", data={"world": world.id, "player": "alice"})
        draft = registry.gamestate.characters.get(started.headers["location"].rsplit("/", 1)[1])
        assert draft.player == "alice"
        assert 'href="/chargen/new"' in (await ac.get("/gm/characters")).text

    async def test_a_world_is_required_when_there_are_worlds(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        refused = await ac.post("/chargen", data={})
        assert refused.status_code == 400 and "Choose a world" in refused.text

    async def test_a_finished_character_is_no_draft(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        started = await ac.post("/chargen", data={"world": world.id})
        draft_id = started.headers["location"].rsplit("/", 1)[1]
        registry.gamestate.characters.update(draft_id, status=DraftStatus.READY)
        assert (await ac.get(f"/chargen/{draft_id}/class")).status_code == 404


def _identity(registry: StoreRegistry, username: str) -> Identity:
    user = next(u for u in registry.gamestate.users.list() if u.username == username)
    return Identity(user=user, session=None)  # type: ignore[arg-type]


class TestAbilitiesAndBackground:
    async def test_point_buy_cannot_overspend(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        _, registry = client
        drafts = registry.drafts
        alice = _identity(registry, "alice")
        world = registry.gamestate.worlds.list()[0]
        draft = drafts.start(alice, world.id)
        pc, _ = drafts.save(alice, draft.id, "abilities", {"abilities.method": ["pointbuy"]})
        assert pc.abilities.as_dict() == {a: 8 for a in ("str", "dex", "con", "int", "wis", "cha")}
        for _ in range(7):  # 8 → 15 costs 9 points
            pc, _ = drafts.save(
                alice,
                draft.id,
                "abilities",
                {"abilities.method": ["pointbuy"]},
                {"adjust": "str:+1"},
            )
        assert pc.abilities.get("str") == 15  # stops at 15
        for ability in ("dex", "con"):
            for _ in range(7):
                pc, _ = drafts.save(
                    alice,
                    draft.id,
                    "abilities",
                    {"abilities.method": ["pointbuy"]},
                    {f"score.{a}": str(pc.abilities.get(a)) for a in ("str", "dex", "con")}
                    | {"adjust": f"{ability}:+1"},
                )
        # 15/15/15 spends all 27: nothing left for INT
        pc, plan = drafts.save(
            alice, draft.id, "abilities", {"abilities.method": ["pointbuy"]}, {"adjust": "int:+1"}
        )
        assert [pc.abilities.get(a) for a in ("str", "dex", "con", "int")] == [15, 15, 15, 8]
        step = plan.step("abilities")
        assert step is not None and step.state == "done"

    async def test_a_roll_is_final_unless_the_world_allows_another(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        _, registry = client
        drafts = registry.drafts
        alice = _identity(registry, "alice")
        world = registry.gamestate.worlds.list()[0]
        draft = drafts.start(alice, world.id)
        rolled = drafts.roll_abilities(alice, draft.id)
        assert len(rolled.ability_rolls) == 6 and all(3 <= r <= 18 for r in rolled.ability_rolls)
        assert rolled.choices["abilities.method"] == ["roll"]
        entry = log_about(registry, rolled.id)[0]
        assert entry.kind.value == "ability_scores" and entry.what.startswith("4d6: ")
        with pytest.raises(DraftError):
            drafts.roll_abilities(alice, draft.id)
        registry.gamestate.worlds.update(world.id, allow_rerolls=True)
        assert len(drafts.roll_abilities(alice, draft.id).ability_rolls) == 6
        # the setting is a line in world.md and survives a reload
        registry.gamestate.prose.load()
        assert registry.gamestate.worlds.get(world.id).allow_rerolls

    async def test_a_half_filled_distribution_is_open_not_wrong(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        _, registry = client
        drafts = registry.drafts
        alice = _identity(registry, "alice")
        draft = drafts.start(alice, registry.gamestate.worlds.list()[0].id)
        drafts.save(alice, draft.id, "class", {"class": ["Fighter|XPHB"]})
        pc, plan = drafts.save(
            alice,
            draft.id,
            "background",
            {
                "background": ["Sage|XPHB"],
                "background.ability.variant": ["0"],
                "background.ability.0": ["", "int"],
            },
        )
        assert pc.choices["background.ability.0"] == ["", "int"]  # the +2 slot stays empty
        step = plan.step("background")
        assert step is not None and step.state == "open"
        assert "background.ability.0" in step.missing
        assert pc.ability_improvements == [{"int": 1}]

    async def test_the_screens(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        started = await ac.post("/chargen", data={"world": world.id})
        url = started.headers["location"]
        await ac.post(f"{url}/class", data={"class": "Fighter|XPHB"})
        background = await ac.post(
            f"{url}/background",
            data={"background": "Sage|XPHB", "background.ability.variant": "0"},
            headers=HTMX,
        )
        assert "Magic Initiate" in background.text and ">+2<" in background.text
        abilities = await ac.post(
            f"{url}/abilities", data={"abilities.method": "pointbuy"}, headers=HTMX
        )
        assert "Points left" in abilities.text and 'value="str:+1"' in abilities.text
        rolled = await ac.post(f"{url}/abilities/roll", headers=HTMX)
        assert "The roll is final." in rolled.text


class TestFeatures:
    async def test_a_feat_instead_of_the_increase(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        _, registry = client
        drafts = registry.drafts
        alice = _identity(registry, "alice")
        draft = drafts.start(alice, registry.gamestate.worlds.list()[0].id)
        drafts.save(alice, draft.id, "class", {"class": ["Fighter|XPHB"], "level": ["4"]})
        pc, plan = drafts.save(
            alice, draft.id, "features", {"asi.4": ["str", "", "feat:Alert|XPHB"]}
        )
        assert pc.choices["asi.4"] == ["feat:Alert|XPHB"]
        assert "Alert" in {f.name for f in pc.feats}
        pc, _ = drafts.save(alice, draft.id, "features", {"asi.4": ["str", "str", ""]})
        assert pc.choices["asi.4"] == ["str", "str"] and pc.ability_improvements == [{"str": 2}]

    async def test_rolled_hit_points(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        _, registry = client
        drafts = registry.drafts
        alice = _identity(registry, "alice")
        draft = drafts.start(alice, registry.gamestate.worlds.list()[0].id)
        drafts.save(alice, draft.id, "class", {"class": ["Fighter|XPHB"], "level": ["5"]})
        _, plan = drafts.save(alice, draft.id, "features", {"hp": ["rolled"]})
        step = plan.step("features")
        assert step is not None and "hp.rolls" in step.missing  # chosen, not yet rolled
        pc = drafts.roll_hit_points(alice, draft.id)
        assert pc.hit_points.method.value == "rolled"
        assert len(pc.hit_points.per_level) == 5 and pc.hit_points.per_level[0] == 10
        assert all(1 <= r <= 10 for r in pc.hit_points.per_level)
        with pytest.raises(DraftError):
            drafts.roll_hit_points(alice, draft.id)
        step = drafts.plan(pc).step("features")
        assert step is not None and "hp.rolls" not in step.missing

    async def test_the_screen(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        started = await ac.post("/chargen", data={"world": registry.gamestate.worlds.list()[0].id})
        url = started.headers["location"]
        await ac.post(f"{url}/class", data={"class": "Fighter|XPHB", "level": "4"})
        page = (await ac.get(f"{url}/features")).text
        assert "Level 4: ability increase or feat" in page and "or a feat" in page
        assert "Defense" in page and "Armor Class" in page  # a style, with its text
        assert "then 6 per level" in page


class TestAbilityLimit:
    def test_an_increase_may_reach_20_not_pass_it(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = _fighter(planner)
        pc.abilities = Abilities.model_validate(
            {"str": 18, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
        )

        def refused() -> list[str]:
            step = next(s for s in planner.plan(pc).steps if s.id == "features")
            return [p.pick for p in step.problems if p.key == "chargen.problem.ability_cap"]

        assert refused() == []
        pc.abilities = Abilities.model_validate(
            {"str": 19, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8}
        )
        assert refused() == ["asi.4"]

    def test_an_epic_boon_goes_past_20_and_costs_no_earlier_increase(self) -> None:
        increase = Pick("asi.4", "asi", "level 4", count=2, limit=ABILITY_LIMIT)
        boon = Pick("boon", "ability", "Boon of Fate", limit=30)
        answers = {"asi.4": ["str", "str"], "boon": ["str"]}
        assert over_limit([increase, boon], answers, {"str": 21}) == []
        assert over_limit([increase, boon], answers, {"str": 22}) == [increase]
        assert over_limit([boon], {"boon": ["str"]}, {"str": 31}) == [boon]

    def test_a_score_past_20_from_elsewhere_is_no_ones_fault(self) -> None:
        increase = Pick("asi.4", "asi", "level 4", count=2, limit=ABILITY_LIMIT)
        assert over_limit([increase], {"asi.4": ["dex", "con"]}, {"str": 22, "dex": 14}) == []


def test_a_spell_filter_is_confirmed_not_failed() -> None:
    prereq = [{"spell": [{"choose": "level=0|class=Warlock", "entry": "a damaging cantrip"}]}]
    verdict = check(prereq, Context())
    assert verdict.allowed and verdict.confirm == ("a damaging cantrip",)


class TestSkillsAndEquipment:
    def test_the_packages_dress_the_character(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        answers = {
            "class": ["Fighter|PHB"],
            "background": ["Soldier|PHB"],
            "equipment.class.0": ["a"],  # chain mail
            "equipment.class.1": ["b"],  # two martial weapons
            "equipment.class.1.b.0": ["Longsword|PHB", "Longsword|PHB"],
            "equipment.class.2": ["a"],
            "equipment.class.3": ["a"],
            "equipment.background.1": ["a"],
        }
        for pick_id, values in answers.items():
            planner.answer(pc, pick_id, values)
        options = planner.plan(pc).step("equipment")
        assert options is not None
        weapons = options.pick("equipment.class.1.b.0")
        assert weapons is not None and "Antimatter Rifle|DMG" not in weapons.options
        planner.apply(pc)
        assert pc.inventory == []  # nothing until the packages are taken
        planner.answer(pc, "equipment", ["packages"])
        planner.apply(pc)
        items = {i.item.name: i for i in pc.inventory}
        assert items["Chain Mail"].equipped and items["Longsword"].quantity == 2
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2014))
        assert sheet.armor_class.value == 16
        step = planner.plan(pc).step("equipment")
        assert step is not None and step.state == "done"

    async def test_the_screens(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        started = await ac.post("/chargen", data={"world": registry.gamestate.worlds.list()[0].id})
        url = started.headers["location"]
        await ac.post(f"{url}/class", data={"class": "Fighter|XPHB"})
        await ac.post(f"{url}/background", data={"background": "Sage|XPHB"})
        skills = (await ac.get(f"{url}/skills")).text
        assert "Arcana" in skills and ">Sage<" in skills and "Armor:" in skills
        equipment = (await ac.get(f"{url}/equipment")).text
        assert "Greatsword" in equipment and "Take the packages" in equipment


class TestSpells:
    def test_a_subclass_caster_draws_from_its_subclass_list(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        for pick_id, values in {
            "class": ["Fighter|PHB"],
            "level": ["7"],
            "subclass": ["Eldritch Knight|PHB"],
        }.items():
            planner.answer(pc, pick_id, values)
        step = planner.plan(pc).step("spells")
        assert step is not None
        known = step.pick("spells.known")
        assert known is not None and known.count == 5
        assert "Shield|PHB" in known.options and "Cure Wounds|PHB" not in known.options

    def test_a_cleric_prepares_level_plus_wisdom(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        planner.answer(pc, "class", ["Cleric|PHB"])
        planner.answer(pc, "level", ["3"])
        pc.abilities = Abilities.model_validate(
            {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 16, "cha": 10}
        )
        step = planner.plan(pc).step("spells")
        assert step is not None
        prepared = step.pick("spells.prepared")
        assert prepared is not None and prepared.count == 3 + 3
        assert "Cure Wounds|PHB" in prepared.options

    def test_a_wizard_prepares_from_the_book(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        planner.answer(pc, "class", ["Wizard|PHB"])
        planner.answer(pc, "spells.known", ["Magic Missile|PHB", "Shield|PHB"])
        step = planner.plan(pc).step("spells")
        assert step is not None
        prepared = step.pick("spells.prepared")
        assert prepared is not None
        assert set(prepared.options) == {"Magic Missile|PHB", "Shield|PHB"}

    async def test_the_screen(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        started = await ac.post("/chargen", data={"world": registry.gamestate.worlds.list()[0].id})
        url = started.headers["location"]
        await ac.post(f"{url}/class", data={"class": "Wizard|XPHB", "level": "3"})
        page = (await ac.get(f"{url}/spells")).text
        assert "0 / 3" in page and 'class="ref cg-option-name"' in page
        assert "/ref/spell/XPHB/Fire%20Bolt" in page and 'data-level-chip="1"' in page


class TestFinish:
    async def test_a_complete_draft_becomes_a_character(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        drafts = registry.drafts
        alice = _identity(registry, "alice")
        draft = drafts.start(alice, registry.gamestate.worlds.list()[0].id)
        with pytest.raises(DraftError):
            drafts.finish(alice, draft.id)
        refused = await ac.post(f"/chargen/{draft.id}/finish")
        assert refused.status_code == 400 and "not done yet" in refused.text

        answered = _fighter(drafts.planner(draft))
        registry.gamestate.characters.mutate(
            draft.id,
            lambda pc: (
                setattr(pc, "choices", answered.choices),
                setattr(pc, "abilities", answered.abilities),
                setattr(pc, "name", "Rhea"),
            ),
        )
        page = (await ac.get(f"/chargen/{draft.id}/details")).text
        assert "Your character" in page and "Still open" not in page
        assert "Finish" in page and "chain mail" in page.lower()  # AC's derivation
        done = await ac.post(f"/chargen/{draft.id}/finish")
        assert done.headers["location"] == f"/characters/{draft.id}"
        pc = registry.gamestate.characters.get(draft.id)
        assert pc.status.value == "ready" and pc.classes[0].level == 5
        assert (await ac.get(f"/characters/{draft.id}")).status_code == 200
        assert (await ac.get(f"/chargen/{draft.id}")).status_code == 404  # no draft now
        assert f'href="/characters/{draft.id}"' in (await ac.get("/play")).text


def test_every_starting_equipment_picker_has_options(
    loaded_compendium: CompendiumStore, lookups: LookupTables
) -> None:
    """A picker the catalogue cannot fill would block a character for good."""
    for books in (BOOKS_2014, BOOKS_2024):
        cat = Catalogue(loaded_compendium, SourceSet(sources=books), lookups)
        records = [*cat.classes(), *cat.backgrounds()]
        for record in records:
            raw = record.data.get("startingEquipment")
            groups = raw.get("defaultData") if isinstance(raw, dict) else raw
            for group in groups or []:
                for entries in group.values():
                    for entry in entries:
                        if isinstance(entry, dict) and entry.get("equipmentType"):
                            kind = entry["equipmentType"]
                            assert cat.equipment(kind), f"{record.reference}: {kind}"


def test_a_2024_character_knows_common_and_two_languages(
    loaded_compendium: CompendiumStore, lookups: LookupTables
) -> None:
    planner = _planner(loaded_compendium, lookups, BOOKS_2024)
    pc = _fighter(planner)
    planner.answer(pc, "rules.languages", [])
    step = planner.plan(pc).step("skills")
    assert step is not None and "rules.languages" in step.missing
    planner.answer(pc, "rules.languages", ["common", "elvish"])  # common is given
    step = planner.plan(pc).step("skills")
    assert step is not None and step.state == "review"
    planner.answer(pc, "rules.languages", ["dwarvish", "elvish"])
    planner.apply(pc)
    assert {lang.name for lang in pc.languages} == {"Common", "Dwarvish", "Elvish"}


class TestResilient:
    def test_the_raised_ability_becomes_a_save(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        from app.models.pc import ChosenOption
        from app.services.chargen.blocks import Pick, feat_saves

        catalogue = Catalogue(loaded_compendium, SourceSet(sources=["PHB"]), lookups)
        feats = [ChosenOption(name="Resilient", source="PHB")]
        pick = Pick("feat.Resilient|PHB.ability.0", "ability", "Resilient")
        choices = {pick.id: ["con"]}
        assert feat_saves(feats, catalogue, [pick], choices) == ["con"]
        alert = [ChosenOption(name="Alert", source="PHB")]
        assert feat_saves(alert, catalogue, [pick], choices) == []  # grants no save
