"""Random-encounter tables (TECHSTACK.md §6b, phase 2) — against the real corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Encounter, EncounterMonster, PartySpec, Role, SourceSet, World
from app.services.dice import Dice
from app.services.encounter_roll import EncounterRoller, alternatives, plain
from app.store.compendium import CompendiumStore
from app.store.encounter_tables import EncounterTables
from app.store.registry import StoreRegistry
from tests.test_dice_text_events import Scripted

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def tables(corpus: Path) -> EncounterTables:
    return EncounterTables(corpus)


class TestReading:
    def test_alternatives_split_outside_tags(self) -> None:
        row = "1 {@creature badger} or {@dice 1d4} {@creature poisonous snake||snakes}"
        assert alternatives(row) == [
            "1 {@creature badger}",
            "{@dice 1d4} {@creature poisonous snake||snakes}",
        ]
        assert alternatives("{@creature lord or lady}") == ["{@creature lord or lady}"]

    def test_plain_text_keeps_the_display(self) -> None:
        assert plain("{@dice 2d4} woodcutters ({@creature commoner||commoners})") == (
            "2d4 woodcutters (commoners)"
        )

    def test_the_tables(self, tables: EncounterTables) -> None:
        forest = tables.get("Forest|XGE")
        assert forest is not None and len(forest.tables) == 4
        table = forest.table_for(7)
        assert table is not None and (table.min_level, table.max_level) == (5, 10)
        urban = tables.get("Urban|DMG")
        assert urban is not None and urban.table_for(17) is not None  # one table, any level


def _roller(compendium: CompendiumStore, books: list[str], *faces: int) -> EncounterRoller:
    resolver = compendium.resolver()

    def resolve(name: str, source: str | None) -> tuple[str, str] | None:
        found = resolver.resolve_parts("monster", name, source, SourceSet(sources=books))
        entry = found.entry
        return (str(entry.name), str(entry.source)) if entry is not None else None

    return EncounterRoller(resolve, Dice(Scripted(*faces)))


class TestRolling:
    def test_counts_are_rolled(
        self, tables: EncounterTables, loaded_compendium: CompendiumStore
    ) -> None:
        forest = tables.get("Forest|XGE")
        assert forest is not None
        # d100 = 3: "{@dice 2d4} woodcutters ({@creature commoner||commoners})"; 2d4 = 3 + 2
        rolled = _roller(loaded_compendium, ["PHB", "MM", "XGE"], 3, 3, 2).roll(forest, 2)
        assert rolled is not None and rolled.roll == 3
        assert rolled.monsters == [("Commoner", "MM", 5)]
        assert rolled.text == "2d4 woodcutters (commoners)"

    def test_one_alternative_is_chosen(
        self, tables: EncounterTables, loaded_compendium: CompendiumStore
    ) -> None:
        forest = tables.get("Forest|XGE")
        assert forest is not None
        # d100 = 4: "1 badger or 1d4 poisonous snakes"; the second, with 1d4 = 3
        rolled = _roller(loaded_compendium, ["PHB", "MM", "XGE"], 4, 2, 3).roll(forest, 2)
        assert rolled is not None and rolled.monsters == [("Poisonous Snake", "MM", 3)]

    def test_a_creature_outside_the_books_is_kept_as_a_name(
        self, tables: EncounterTables, loaded_compendium: CompendiumStore
    ) -> None:
        forest = tables.get("Forest|XGE")
        assert forest is not None
        rolled = _roller(loaded_compendium, ["PHB"], 3, 3, 2).roll(forest, 2)
        assert rolled is not None and rolled.monsters == [] and rolled.unresolved == ["commoner"]


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


class TestBuilder:
    async def test_a_roll_replaces_the_plan(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Woods", source_set=SourceSet(sources=["PHB", "MM", "XGE"]))
        )
        encounter = registry.gamestate.encounters.create(
            Encounter(
                name="On the road",
                world_id=world.id,
                party=PartySpec(size=4, level=3),
                # Nothing a forest table rolls: the forest can roll goblins.
                monsters=[EncounterMonster(name="Tarrasque", source="MM", count=1)],
            )
        )
        url = f"/gm/encounters/{encounter.id}"
        page = (await ac.get(url)).text
        assert "Roll on a table" in page and "party level 3" in page
        assert '<optgroup label="In this world&#39;s books">' in page
        answer = await ac.post(f"{url}/roll", data={"environment": "Forest|XGE"})
        assert answer.status_code == 200 and "Rolled" in answer.text
        after = registry.gamestate.encounters.get(encounter.id)
        assert after.notes.startswith("Forest (XGE), ")
        assert all(m.name != "Tarrasque" for m in after.monsters)  # the plan was replaced
        # The table rolled on is chosen again for the next roll.
        assert after.roll_table == "Forest|XGE"
        assert '<option value="Forest|XGE" selected>' in (await ac.get(url)).text

    async def test_an_unknown_environment(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        encounter = registry.gamestate.encounters.create(Encounter(name="X"))
        answer = await ac.post(
            f"/gm/encounters/{encounter.id}/roll", data={"environment": "Nowhere|XX"}
        )
        assert "no table for this level" in answer.text
