"""What references this? (PROJECT.md "Reverse lookup") — against the real corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    Encounter,
    EncounterMonster,
    Npc,
    Role,
    Scope,
    SourceSet,
    Treasure,
    TreasureItem,
    World,
)
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"
BOOKS_2014 = ["PHB", "MM", "DMG"]
BOOKS_2024 = ["XPHB", "XMM", "XDMG"]


@pytest.fixture(scope="module")
def registry(corpus: Path, tmp_path_factory: pytest.TempPathFactory) -> StoreRegistry:
    lib = tmp_path_factory.mktemp("lib")
    registry = StoreRegistry(Settings(reference_dir=corpus, library_dir=lib, watch_files=False))
    registry.load()
    return registry


def _owners(
    registry: StoreRegistry, category: str, name: str, source: str, books: list[str] | None
) -> dict[str, set[str]]:
    found = registry.backlinks.links_to(
        category, name, source, source_set=SourceSet(sources=books) if books else None
    )
    return {cat: {o.name for o in owners} for cat, owners in found.groups}


class TestCorpus:
    def test_who_casts_the_2014_fireball(self, registry: StoreRegistry) -> None:
        owners = _owners(registry, "spell", "Fireball", "PHB", BOOKS_2014)
        assert {"Mage", "Lich"} <= owners["monster"]
        assert all(o for o in owners.get("item", ()))

    def test_a_2024_world_has_its_own(self, registry: StoreRegistry) -> None:
        old = _owners(registry, "spell", "Fireball", "PHB", BOOKS_2014)
        new = _owners(registry, "spell", "Fireball", "XPHB", BOOKS_2024)
        assert new.get("monster")  # the 2024 Monster Manual's own casters
        # the 2014 world never sees a 2024 entry, nor the reverse
        assert _owners(registry, "spell", "Fireball", "XPHB", BOOKS_2014) == {}
        assert old != new

    def test_without_a_world_a_bare_name_counts_for_every_printing(
        self, registry: StoreRegistry
    ) -> None:
        unrestricted = _owners(registry, "spell", "Fireball", "PHB", None)
        assert {"Mage", "Lich"} <= unrestricted["monster"]

    def test_rulebook_chapters_refer_too(self, registry: StoreRegistry) -> None:
        found = registry.backlinks.links_to(
            "condition", "Grappled", "PHB", source_set=SourceSet(sources=BOOKS_2014)
        )
        chapters = dict(found.groups).get("book", ())
        assert chapters and all(o.url.startswith("/books/") for o in chapters)
        assert any(o.context == "Player's Handbook (2014)" for o in chapters)

    def test_an_entry_never_links_to_itself(self, registry: StoreRegistry) -> None:
        owners = _owners(registry, "spell", "Fireball", "PHB", None)
        assert ("spell", "Fireball") not in {(c, n) for c, ns in owners.items() for n in ns}

    def test_the_index_is_quick(self, registry: StoreRegistry) -> None:
        import time

        registry.backlinks._ensure()
        start = time.perf_counter()
        for _ in range(20):
            registry.backlinks.links_to(
                "spell", "Fireball", "PHB", source_set=SourceSet(sources=BOOKS_2014)
            )
        assert (time.perf_counter() - start) / 20 < 0.05


@pytest.fixture
async def client(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="Phandalin", source_set=SourceSet(sources=BOOKS_2014))
    )
    adventure = registry.adventures.create(world.id, "Lost Mine")
    chapter = registry.adventures.create_chapter(adventure.id, "Cragmaw Hideout")
    registry.adventures.update_chapter(
        chapter.id,
        title="Cragmaw Hideout",
        status="planned",
        body="A shaman casts {@spell fireball} at the party; {@creature goblin|MM}s flee.",
    )
    where = Scope(world_id=world.id)
    registry.gamestate.npcs.create(Npc(scope=where, name="Klarg", statblock="Goblin|MM"))
    registry.gamestate.treasures.create(
        Treasure(
            scope=where, name="Klarg's chest", items=[TreasureItem(name="Longsword", source="PHB")]
        )
    )
    registry.gamestate.encounters.create(
        Encounter(
            name="Cave mouth",
            world_id=world.id,
            monsters=[EncounterMonster(name="Goblin", source="MM", count=3)],
        )
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry, world.id


async def _login(ac: AsyncClient, user: str) -> None:
    await ac.post("/login", data={"username": user, "password": PASSWORD})


class TestCampaign:
    async def test_the_gamemaster_sees_where_the_campaign_uses_it(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world_id = client
        goblin = registry.backlinks.links_to(
            "monster",
            "Goblin",
            "MM",
            source_set=SourceSet(sources=BOOKS_2014),
            world_id=world_id,
        )
        kinds = {(link.kind, link.name) for link in goblin.library}
        assert {
            ("chapter", "Cragmaw Hideout"),
            ("npc", "Klarg"),
            ("encounter", "Cave mouth"),
        } <= kinds
        sword = registry.backlinks.links_to("baseitem", "Longsword", "PHB", world_id=world_id)
        assert ("treasure", "Klarg's chest") in {(x.kind, x.name) for x in sword.library}
        assert not registry.backlinks.links_to(
            "monster", "Goblin", "MM", world_id="another-world"
        ).library

        await _login(ac, "dm")
        page = (await ac.get("/ref/spell/PHB/Fireball", params={"world": world_id})).text
        assert "Referenced by" in page and "In your campaign" in page and "Cragmaw Hideout" in page
        assert ">Mage<" in page

    async def test_players_see_the_books_not_the_campaign(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, _registry, world_id = client
        await _login(ac, "alice")
        page = (await ac.get("/ref/spell/PHB/Fireball", params={"world": world_id})).text
        assert "Referenced by" in page and ">Mage<" not in page  # a creature: not theirs
        assert "In your campaign" not in page and "Cragmaw Hideout" not in page
