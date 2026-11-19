"""Which world a page belongs to, read from its address (app/services/current_world.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.current_world import world_of_page
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    return registry


@pytest.fixture
async def gm(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client


def test_a_page_names_its_world(registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    hob = registry.npcs.create("Hob", f"c:{fog.id}", world.id)
    landing = registry.locations.create("Landing", f"w:{world.id}", world.id)
    store = registry.gamestate
    for url in (
        f"/gm/worlds/{world.id}/npcs",
        f"/gm/adventures/{bell.id}",
        f"/gm/chapters/{fog.id}?x=1",
        f"/gm/npcs/{hob.id}",
        f"/gm/places/{landing.id}",
    ):
        assert world_of_page(store, url) == world.id, url
    assert world_of_page(store, "/gm/npcs/gone") is None
    assert world_of_page(store, "/books") is None
