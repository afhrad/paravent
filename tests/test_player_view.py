"""What a player may read (documentation/PLAYER-VIEW.md): player books, hidden kinds, and
what the character has."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from htpy import span
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import ClassLevel, InventoryItem, PlayerCharacter
from app.services.player_view import (
    Reader,
    player_books,
    player_choice,
    player_ticks,
    reader_for,
)
from app.store.registry import StoreRegistry
from app.ui.tags import RenderContext, render

PASSWORD = "correct horse battery"
GROUPS = {"PHB": "core", "MM": "core", "DMG": "core", "XGE": "supplement", "SCAG": "setting"}


def _world(sources: list[str], players: list[str] | None = None) -> World:
    return World(name="W", source_set=SourceSet(sources=sources), player_sources=players)


class TestPlayerBooks:
    def test_the_handbooks_and_supplements_by_default(self) -> None:
        world = _world(["PHB", "MM", "DMG", "XGE", "SCAG"])
        assert player_books(world, GROUPS) == {"PHB", "XGE"}
        # a world declaring nothing draws on every loaded book
        assert player_books(_world([]), GROUPS) == {"PHB", "XGE"}

    def test_the_gamemasters_ticks(self) -> None:
        world = _world(["PHB", "MM", "SCAG"], players=["PHB", "SCAG"])
        assert player_books(world, GROUPS) == {"PHB", "SCAG"}
        assert player_books(_world(["PHB"], players=[]), GROUPS) == frozenset()

    def test_the_form_stores_only_what_differs(self) -> None:
        declared = ["PHB", "MM", "XGE"]
        assert player_choice(["PHB", "XGE"], declared, GROUPS) is None
        assert player_choice(["PHB"], declared, GROUPS) == ["PHB"]
        assert player_choice([], declared, GROUPS) == []
        # the form starts from the world's choice, and the defaults for the rest
        ticks = player_ticks(_world(["PHB", "XGE"], players=["PHB"]), GROUPS)
        assert "PHB" in ticks and "XGE" not in ticks and "SCAG" not in ticks


class TestReader:
    def test_kinds_books_and_what_a_character_has(self) -> None:
        pc = PlayerCharacter(
            name="Rhea",
            classes=[ClassLevel(name="Artificer", source="TCE", level=3)],
            inventory=[InventoryItem.model_validate({"item": "Cloak of Protection|DMG"})],
        )
        reader = reader_for([pc], [_world(["PHB", "MM", "DMG", "TCE"])], GROUPS)
        assert reader.readable("spell", {"name": "Fireball", "source": "PHB"})
        assert not reader.readable("spell", {"name": "Fireball", "source": "DMG"})
        assert not reader.readable("monster", {"name": "Goblin", "source": "PHB"})
        assert reader.readable("item", {"name": "Rope", "source": "PHB", "rarity": "none"})
        assert not reader.readable("item", {"name": "Wand", "source": "PHB", "rarity": "rare"})
        # what the character has reads whatever its book
        assert reader.readable("item", {"name": "Cloak of Protection", "source": "DMG"})
        assert reader.readable("class", {"name": "Artificer", "source": "TCE"})
        feature = {"name": "Infuse Item", "source": "TCE", "className": "Artificer"}
        assert reader.readable("classFeature", feature)
        assert reader.reads_book("PHB") and not reader.reads_book("DMG")

    def test_a_hidden_link_is_plain_text(self) -> None:
        class Found:
            def __init__(self, entry: object) -> None:
                self.entry, self.outcome = entry, None

        class Resolver:
            def resolve_parts(self, category: str, name: str, *_: object) -> Found:
                from types import SimpleNamespace

                return Found(SimpleNamespace(name=name.title(), source="MM"))

        reader = Reader(frozenset({"PHB"}))
        ctx = RenderContext(resolver=Resolver(), readable=reader.readable)
        shown = str(span[render("{@creature goblin}", ctx)])
        assert "Goblin" in shown and "<a " not in shown and "missing" not in shown
        assert "<a " in str(span[render("{@creature goblin}", RenderContext(resolver=Resolver()))])


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
        World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
    )
    registry.gamestate.characters.create(
        PlayerCharacter(
            name="Rhea",
            player="alice",
            world_id=world.id,
            inventory=[InventoryItem.model_validate({"item": "Cloak of Protection|DMG"})],
        )
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        yield ac, registry, world.id


class TestRoutes:
    async def test_a_player_reads_their_books_and_their_things(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, _registry, world = client
        ok = {"world": world}
        assert (await ac.get("/ref/spell/PHB/Fireball", params=ok)).status_code == 200
        assert (await ac.get("/ref/monster/MM/Goblin", params=ok)).status_code == 404
        assert (await ac.get("/ref/monster/MM/Goblin/preview", params=ok)).status_code == 404
        cloak = "/ref/item/DMG/Cloak%20of%20Protection"
        assert (await ac.get(cloak, params=ok)).status_code == 200  # it is in the inventory
        assert (await ac.get("/ref/item/DMG/Bag%20of%20Holding", params=ok)).status_code == 404

    async def test_rulebooks_screen_and_search(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, _registry, world = client
        books = (await ac.get("/books", params={"world": world})).text
        assert "/books/PHB" in books and "/books/DMG" not in books
        assert (await ac.get("/books/DMG")).status_code == 404
        assert (await ac.get("/books/DMG/0")).status_code == 404
        assert (await ac.get("/books/PHB/0")).status_code == 200
        assert (await ac.get("/screen", params={"ref": "dm"})).status_code == 404
        found = (await ac.get("/search", params={"q": "goblin", "world": world})).text
        assert "/ref/monster/" not in found

    async def test_pages_without_a_world_read_in_the_players_one(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, _registry, _world = client
        found = (await ac.get("/search", params={"q": "fireball", "partial": "1"})).text
        assert "/ref/spell/PHB/Fireball" in found  # the 2014 spell, not the 2024 one
        chapter = (await ac.get("/books/PHB/9")).text
        assert 'href="/ref/condition/PHB/' in chapter

    async def test_the_gamemaster_reads_all_and_can_look_as_a_player(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, _registry, world = client
        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        page = await ac.get("/ref/monster/MM/Goblin", params={"world": world})
        assert page.status_code == 200 and "View as player" in page.text
        looked = await ac.get("/ref/monster/MM/Goblin", params={"world": world, "as": "player"})
        assert looked.status_code == 404
        spell = await ac.get("/ref/spell/PHB/Fireball", params={"as": "player"})
        assert spell.status_code == 200 and "Back to your view" in spell.text

    async def test_the_world_form_keeps_the_players_ticks(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world = client
        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        form = (await ac.get(f"/gm/worlds/{world}/edit")).text
        assert 'name="players" value="PHB" checked' in form
        assert 'name="players" value="DMG" checked' not in form
        await ac.post(
            f"/gm/worlds/{world}",
            data={"name": "Phandalin", "sources": ["PHB", "MM", "DMG"], "players": ["PHB", "DMG"]},
        )
        saved = registry.gamestate.worlds.find(world)
        assert saved is not None and saved.player_sources == ["PHB", "DMG"]
        # back to the defaults: nothing stored
        await ac.post(
            f"/gm/worlds/{world}",
            data={"name": "Phandalin", "sources": ["PHB", "MM", "DMG"], "players": ["PHB"]},
        )
        saved = registry.gamestate.worlds.find(world)
        assert saved is not None and saved.player_sources is None
