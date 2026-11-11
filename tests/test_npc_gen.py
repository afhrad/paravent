"""The NPC generator (app/services/npc_gen.py) — against the real corpus."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.services.dice import Dice
from app.services.npc_gen import ANY_STYLE, NpcGenerator, ability_text, edition_for
from app.store.npc_tables import NpcTables
from app.store.registry import StoreRegistry
from tests.test_dice_text_events import Scripted

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def tables(corpus: Path) -> NpcTables:
    return NpcTables(corpus)


class TestTables:
    def test_both_guides_and_the_names(self, tables: NpcTables) -> None:
        assert tables.books() == {"DMG", "XDMG"}
        talents = tables.table("DMG", "NPC Talents")
        assert talents is not None and talents.faces == 20
        assert [t.caption for t in tables.name_styles()][0] == "1: Common Names"
        human = tables.ancestry("Human|XGE")
        assert human is not None and "Celtic" in human.cultures
        assert len(tables.trinkets()) == 100

    def test_no_corpus_is_no_tables(self) -> None:
        empty = NpcTables(None)
        assert empty.books() == set() and empty.ancestries() == [] and empty.trinkets() == ()
        assert NpcGenerator(empty, "2014").elements() == []

    def test_ability_text(self) -> None:
        assert ability_text("Strength-powerful, brawny") == "Strength: powerful, brawny"


class TestEditions:
    def test_the_world_decides(self, tables: NpcTables) -> None:
        assert edition_for(SourceSet(sources=["PHB", "DMG"]), tables) == "2014"
        assert edition_for(SourceSet(sources=["XPHB", "XDMG"]), tables) == "2024"

    def test_2014_rolls_everything_on_the_2014_guide(self, tables: NpcTables) -> None:
        generator = NpcGenerator(tables, "2014")
        assert "flaw" in generator.elements() and "secret" not in generator.elements()
        assert generator.source_of("appearance") == "DMG"

    def test_2024_takes_its_own_tables_and_the_rest_from_2014(self, tables: NpcTables) -> None:
        generator = NpcGenerator(tables, "2024")
        assert "secret" in generator.elements() and "flaw" not in generator.elements()
        assert generator.source_of("appearance") == "XDMG"
        assert generator.source_of("talent") == "DMG"
        assert generator.source_of("trinket") == "XGE"
        assert generator.default_name_set() == ANY_STYLE


class TestRolling:
    def test_a_row_by_its_number(self, tables: NpcTables) -> None:
        generator = NpcGenerator(tables, "2014", Dice(Scripted(3)))
        assert generator.roll_element("talent") == "Unbelievably lucky"

    def test_bond_ten_rolls_twice(self, tables: NpcTables) -> None:
        # d10 = 10: "Roll twice, ignoring result of 10." → 2, then 2 again is skipped → 9
        generator = NpcGenerator(tables, "2014", Dice(Scripted(10, 2, 2, 9)))
        assert generator.roll_element("bond") == (
            "Protective of close family members; Out for revenge"
        )

    def test_the_low_ability_is_not_the_high_one(self, tables: NpcTables) -> None:
        # the low roll lands on Strength (1), which is the high one: rolled again → 6
        generator = NpcGenerator(tables, "2014", Dice(Scripted(1, 6)))
        low = generator.roll_element("low", {"high": "Strength: powerful, brawny"})
        assert low == "Charisma: dull, boring"

    def test_an_ideal_names_its_alignment(self, tables: NpcTables) -> None:
        generator = NpcGenerator(
            tables, "2014", Dice(Scripted(3, 3)), axis_label=lambda a: f"<{a}>"
        )
        assert generator.roll_element("ideal") == "Honor (<lawful>)"

    def test_names(self, tables: NpcTables) -> None:
        # Dwarf, female: the one female table, its first row; the one clan table, its first
        generator = NpcGenerator(tables, "2014", Dice(Scripted(1, 1, 1, 1)))
        assert generator.roll_name("Dwarf|XGE", "female") == "Anbera Aranore"
        # one style chosen: a given name (1) and a surname (3)
        style = NpcGenerator(tables, "2024", Dice(Scripted(1, 3)))
        assert style.roll_name("style:3: Lyrical Names") == "Arannis Daardendrian"
        celtic = NpcGenerator(tables, "2014")
        names = {celtic.roll_name("Human|XGE|Celtic", "male") for _ in range(10)}
        assert all(" " not in n for n in names)  # humans have no surname tables
        assert celtic.roll_name("Nowhere|XX") == ""

    def test_a_whole_npc(self, tables: NpcTables) -> None:
        generator = NpcGenerator(tables, "2024")
        npc = generator.roll(generator.default_name_set())
        assert npc.name and set(npc.values) == set(generator.elements())
        assert all(npc.values.values())


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


def _carried(page: str) -> dict[str, str]:
    """The hidden ``v_*`` fields of the generator form."""
    return dict(re.findall(r'name="(v_\w+)" value="([^"]*)"', page))


class TestPages:
    async def test_generate_a_new_npc_and_keep_what_is_ticked(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM", "DMG", "XGE"]))
        )
        made = await ac.post(f"/gm/worlds/{world.id}/npcs/generate", data={"name": "Toblen"})
        assert made.status_code == 303
        npc = registry.npcs.list(world.id)[0]
        assert npc.name == "Toblen" and made.headers["location"].endswith("keep_name=1")
        page = (await ac.get(made.headers["location"])).text
        assert "Toblen" in page and "Talent" in page and "Bandit Captain (MM)" in page
        carried = _carried(page)
        assert carried["v_name"] == "Toblen" and carried["v_talent"]
        rerolled = await ac.post(
            f"/gm/npcs/{npc.id}/generate",
            data={**carried, "action": "reroll:name", "names": "Dwarf|XGE"},
        )
        assert _carried(rerolled.text)["v_talent"] == carried["v_talent"]  # the rest is kept
        applied = await ac.post(
            f"/gm/npcs/{npc.id}/generate",
            data={
                **carried,
                "action": "apply",
                "take": ["talent", "bond"],
                "statblock": "Bandit Captain|MM",
            },
        )
        assert applied.status_code == 303
        after = registry.npcs.get(npc.id)
        assert after.name == "Toblen"  # the name was not ticked
        assert after.statblock == "Bandit Captain|MM"
        assert f"- **Talent:** {carried['v_talent']}" in after.notes
        assert "**Mannerism:**" not in after.notes

    async def test_an_npcs_own_name_is_not_ticked(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        npc = registry.npcs.create("Sildar", f"w:{world.id}", world.id)
        page = (await ac.get(f"/gm/npcs/{npc.id}/generate")).text
        assert 'value="starter:old-norse"' in page and "Old Norse (saga age)" in page
        rolled = (
            await ac.get(f"/gm/npcs/{npc.id}/generate", params={"names": "starter:nahua-calendar"})
        ).text
        assert 'value="starter:nahua-calendar" selected' in rolled
        assert 'name="take" value="name">' in page.replace(" checked", "") and (
            'name="take" value="name" checked' not in page
        )
        assert 'name="take" value="talent" checked' in page
