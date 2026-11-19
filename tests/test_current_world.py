"""The world the gamemaster works in, where a new encounter takes place
(app/services/current_world.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.current_world import current_world, world_of_page
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


async def test_a_new_encounter_takes_place_in_the_world_worked_in(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    user = registry.auth.find_user("dm")
    assert user is not None
    # Two worlds and nowhere been: the form asks.
    page = (await gm.get("/gm/encounters")).text
    assert '<select id="f-world" name="world"' in page
    # Working in one of them: the form names it and asks nothing.
    await gm.get(f"/gm/worlds/{salt.id}")
    worlds = registry.worlds.list()
    assert current_world(registry.gamestate, registry.places, user.id, worlds) == salt.id
    page = (await gm.get("/gm/encounters")).text
    assert 'id="f-world"' not in page and "in Salt Coast" in page
    assert f'name="world" value="{salt.id}"' in page
    made = await gm.post("/gm/encounters", data={"name": "Ambush", "world": salt.id})
    assert made.status_code == 303
    assert [e.world_id for e in registry.gamestate.encounters.list()] == [salt.id]
    # The list's filter is the world in view, and so the one a new encounter goes to.
    page = (await gm.get(f"/gm/encounters?world={grey.id}")).text
    assert f'name="world" value="{grey.id}"' in page and "in Grey Reed" in page
