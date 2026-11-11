"""Rolling treasure on the DMG tables (TECHSTACK.md §6b, phase 2) — against the corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    Coins,
    Encounter,
    EncounterMonster,
    Role,
    Scope,
    SourceSet,
    Treasure,
    World,
)
from app.services.dice import Dice
from app.services.treasure_roll import BOOK_2014, BOOK_2024, Rolled, roller_for, tables_for
from app.store.compendium import CompendiumStore
from app.store.loot import LootTables
from app.store.registry import StoreRegistry
from tests.test_dice_text_events import Scripted

PASSWORD = "correct horse battery"
BOOKS_2014 = ["PHB", "MM", "DMG", "XGE", "TCE"]
BOOKS_2024 = ["XPHB", "XMM", "XDMG"]


@pytest.fixture(scope="module")
def loot(corpus: Path) -> LootTables:
    return LootTables(corpus)


class TestTables:
    def test_the_world_decides_the_book(self, loot: LootTables) -> None:
        books = loot.books()
        assert books == {BOOK_2014, BOOK_2024}
        assert tables_for(SourceSet(sources=BOOKS_2014), books) == BOOK_2014
        assert tables_for(SourceSet(sources=BOOKS_2024), books) == BOOK_2024
        assert tables_for(None, books) == BOOK_2024

    def test_groups_per_edition(self, loot: LootTables) -> None:
        assert "Potion of Fire Resistance" in loot.group("Potion of Resistance", "DMG")
        assert all("|XDMG" in i for i in loot.group("Dragon Scale Mail", "XDMG"))


class TestRolling:
    def test_individual_treasure_is_coins(
        self, loot: LootTables, loaded_compendium: CompendiumStore
    ) -> None:
        # d100 = 45 → "4d6 sp" on the 2014 CR 0-4 table; the four dice show 3s
        dice = Dice(Scripted(45, 3, 3, 3, 3))
        roller = roller_for(loaded_compendium, loot, SourceSet(sources=BOOKS_2014), "DMG", dice)
        rolled = roller.roll("individual", 2, "DMG")
        assert rolled.coins == {"sp": 12} and not rolled.items

    @pytest.mark.parametrize(("books", "book"), [(BOOKS_2014, BOOK_2014), (BOOKS_2024, BOOK_2024)])
    def test_every_hoard_item_resolves(
        self,
        loot: LootTables,
        loaded_compendium: CompendiumStore,
        books: list[str],
        book: str,
    ) -> None:
        roller = roller_for(loaded_compendium, loot, SourceSet(sources=books), book)
        unresolved: list[str] = []
        items = 0
        for _ in range(100):
            for cr in (2, 7, 13, 19):
                rolled = roller.roll("hoard", cr, book)
                unresolved.extend(rolled.unresolved)
                items += len(rolled.items)
        assert items > 100 and unresolved == []

    def test_items_come_from_the_worlds_books(
        self, loot: LootTables, loaded_compendium: CompendiumStore
    ) -> None:
        roller = roller_for(loaded_compendium, loot, SourceSet(sources=BOOKS_2024), BOOK_2024)
        sources = {
            source for _ in range(60) for _name, source in roller.roll("hoard", 13, BOOK_2024).items
        }
        assert sources and sources <= set(BOOKS_2024)


@pytest.fixture
async def client(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(
        World(name="Phandalin", source_set=SourceSet(sources=BOOKS_2014))
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry, world.id


class TestTreasure:
    def test_a_roll_is_added_to_a_treasure(self, tmp_path: Path, corpus: Path) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        world = registry.gamestate.worlds.create(World(name="W"))
        treasure = registry.gamestate.treasures.create(
            Treasure(scope=Scope(world_id=world.id), name="Chest", coins=Coins(gp=10), notes="Old.")
        )
        rolled = Rolled(
            coins={"gp": 40, "sp": 5},
            items=[("Potion of Healing", "DMG"), ("Potion of Healing", "DMG")],
            unresolved=["A painting by an artist long forgotten"],
        )
        after = registry.treasure.add_rolled(treasure.id, rolled)
        assert (after.coins.gp, after.coins.sp) == (50, 5)
        assert [(i.name, i.count) for i in after.items] == [("Potion of Healing", 2)]
        assert after.notes == "Old.\n\n- A painting by an artist long forgotten"

    async def test_the_page_rolls_into_the_treasure(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world_id = client
        treasure = registry.treasure.create("Hoard", f"w:{world_id}", world_id)
        page = (await ac.get(f"/gm/treasure/{treasure.id}", params={"cr": "7"})).text
        assert "Roll treasure" in page and 'name="cr"' in page and 'value="7"' in page
        assert '<option value="DMG" selected>' in page  # the world's tables
        answer = await ac.post(
            f"/gm/treasure/{treasure.id}/roll", data={"kind": "hoard", "cr": "7", "book": "DMG"}
        )
        assert "Rolled:" in answer.text and "DMG · Challenge 5-10" in answer.text
        assert not registry.treasure.get(treasure.id).coins.empty

    async def test_a_fight_opens_a_treasure_to_roll(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world_id = client
        encounter = registry.gamestate.encounters.create(
            Encounter(
                name="Cave mouth",
                world_id=world_id,
                monsters=[
                    EncounterMonster(name="Goblin", source="MM", count=2),
                    EncounterMonster(name="Bugbear", source="MM"),
                ],
            )
        )
        combat = registry.combats.start(encounter, "dm")
        registry.combats.end(combat.id, "dm")
        assert "Roll treasure" in (await ac.get(f"/gm/combat/{combat.id}")).text
        opened = await ac.post(f"/gm/combat/{combat.id}/treasure")
        location = opened.headers["location"]
        assert "kind=hoard" in location and "cr=1" in location  # the bugbear's CR
        treasure = registry.treasure.list(world_id)[0]
        assert treasure.name == "Treasure: Cave mouth"
