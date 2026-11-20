"""Deleted NPCs, places, treasure and encounters kept a while to restore
(app/services/trash.py, Check links)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, Scope
from app.services.trash import KEEP_DAYS, TrashError
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


def _world(registry: StoreRegistry) -> tuple[str, str, str]:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    registry.table.select(world.id)
    return world.id, bell.id, fog.id


def test_what_is_deleted_comes_back_where_it_was(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    hob = registry.npcs.create("Hob", f"c:{fog}", world)
    registry.gamestate.npcs.update(hob.id, notes="Rows the ferry.")
    ambush = registry.encounters.create(
        "Ambush", world, Scope(world_id=world, adventure_id=bell, chapter_id=fog)
    )
    registry.npcs.delete(hob.id)
    registry.encounters.delete(ambush.id)
    trash = registry.trash
    assert [t.name for t in trash.of_world(world)] == ["Ambush", "Hob"]  # newest first
    kept = next(t for t in trash.of_world(world) if t.name == "Hob")
    kind, back = trash.restore(kept.id)
    npc = registry.gamestate.npcs.get(hob.id)
    assert kind == "npc" and back.id == hob.id
    assert npc.scope.chapter_id == fog and npc.notes == "Rows the ferry."
    assert [t.name for t in trash.of_world(world)] == ["Ambush"]
    # Its chapter gone meanwhile: the encounter comes back into its adventure.
    registry.adventures.delete_chapter(fog)
    kind, again = trash.restore(trash.of_world(world)[0].id)
    encounter = registry.gamestate.encounters.get(ambush.id)
    assert encounter.adventure_id == bell and encounter.chapter_id is None
    with pytest.raises(TrashError):
        trash.restore("gone")


def test_old_deletions_are_forgotten_and_the_trash_empties(registry: StoreRegistry) -> None:
    world, _bell, _fog = _world(registry)
    for name in ("One", "Two"):
        registry.npcs.delete(registry.npcs.create(name, f"w:{world}", world).id)
    trash = registry.trash
    old = trash.of_world(world)[1]
    registry.gamestate.trash.update(
        old.id, deleted_at=datetime.now(UTC) - timedelta(days=KEEP_DAYS + 1)
    )
    trash.clear_old()
    assert [t.name for t in trash.of_world(world)] == ["Two"]
    assert trash.empty(world) == 1 and trash.of_world(world) == []


async def test_check_links_lists_and_restores(gm: AsyncClient, registry: StoreRegistry) -> None:
    world, _bell, _fog = _world(registry)
    mill = registry.locations.create("Old Mill", f"w:{world}", world)
    registry.locations.delete(mill.id)
    page = (await gm.get("/gm/world/links")).text
    assert "Recently deleted" in page and "Old Mill" in page
    kept = registry.trash.of_world(world)[0]
    back = await gm.post("/gm/world/links/restore", data={"id": kept.id})
    assert back.headers["location"] == f"/gm/places/{mill.id}"
    assert registry.gamestate.locations.get(mill.id).name == "Old Mill"
    registry.locations.delete(mill.id)
    emptied = await gm.post("/gm/world/links/empty-trash")
    assert emptied.headers["location"] == "/gm/world/links?fixed=emptied"
    assert registry.trash.of_world(world) == []
