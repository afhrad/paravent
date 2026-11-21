"""Experience for a challenge that was no fight (app/ui/xp_pages.py), and how the campaign's
people stand toward the party (app/ui/attitude.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Npc, Role, Scope
from app.models.pc import PlayerCharacter
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from tests.conftest import log_about

PASSWORD = "correct horse"


@pytest.fixture
async def table(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    registry.table.select(world.id)
    for name in ("Wren", "Tamsin", "Oddo"):
        registry.gamestate.characters.create(
            PlayerCharacter(name=name, world_id=world.id, experience=100)
        )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client, registry, world.id


def _ids(registry: StoreRegistry, world_id: str) -> dict[str, str]:
    return {pc.name: pc.id for pc in registry.gamestate.characters.of_world(world_id)}


async def test_xp_for_a_challenge_each_or_shared(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    client, registry, world = table
    page = (await client.get("/gm")).text
    assert 'data-dialog="tool-xp"' in page and '<dialog id="tool-xp"' in page
    ids = _ids(registry, world)
    done = await client.post(
        "/gm/xp",
        data={
            "world": world,
            "xp": "150",
            "how": "each",
            "reason": "The riddle of the bell",
            "to": [ids["Wren"], ids["Tamsin"]],  # Oddo was not there
        },
    )
    assert done.headers["location"] == "/gm?xp=150%3A2"
    xp = {pc.name: pc.experience for pc in registry.gamestate.characters.of_world(world)}
    assert xp == {"Wren": 250, "Tamsin": 250, "Oddo": 100}
    entry = next(e for e in log_about(registry, ids["Wren"]) if e.amount == 150)
    assert entry.note == "The riddle of the bell"
    assert (
        "150 XP each awarded to 2 characters." in (await client.get(done.headers["location"])).text
    )
    # Shared out: the total split evenly, rounded down.
    await client.post(
        "/gm/xp", data={"world": world, "xp": "100", "how": "split", "to": list(ids.values())}
    )
    xp = {pc.name: pc.experience for pc in registry.gamestate.characters.of_world(world)}
    assert xp == {"Wren": 283, "Tamsin": 283, "Oddo": 133}
    # Nothing to give, or no one: the dialog opens again.
    refused = await client.post("/gm/xp", data={"world": world, "xp": "0", "to": ids["Oddo"]})
    assert refused.headers["location"] == "/gm?tool=xp"


async def test_no_xp_in_a_milestone_world(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    client, registry, world = table
    registry.worlds.update(world, WorldInput(name="Grey Reed", milestone=True))
    assert 'data-dialog="tool-xp"' not in (await client.get("/gm")).text
    ids = _ids(registry, world)
    await client.post("/gm/xp", data={"world": world, "xp": "50", "to": ids["Wren"]})
    assert registry.gamestate.characters.get(ids["Wren"]).experience == 100


async def test_an_npcs_attitude_and_when_last_seen(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    client, registry, world = table
    marla = registry.gamestate.npcs.create(Npc(scope=Scope(world_id=world), name="Old Marla"))
    url = f"/gm/npcs/{marla.id}"
    await client.post(f"{url}/attitude", data={"op": "set", "attitude": "hostile"})
    assert registry.npcs.get(marla.id).attitude == "hostile"
    registry.clock.advance(world, 2 * 24 * 60 + 6 * 60, "dm", "DM")  # Day 3, 14:00
    await client.post(f"{url}/attitude", data={"op": "seen"})
    assert registry.npcs.get(marla.id).last_seen == registry.clock.of(world).minute
    registry.clock.advance(world, 24 * 60, "dm", "DM")
    page = (await client.get(url)).text
    assert 'aria-pressed="true">Hostile' in page
    assert "Last seen Day 3 · 14:00" in page and "(1 d ago)" in page
    assert "Hostile" in (await client.get("/gm/npcs")).text  # in the list too
    # Pressing it again takes it back.
    await client.post(f"{url}/attitude", data={"op": "set", "attitude": ""})
    assert registry.npcs.get(marla.id).attitude is None


async def test_a_contacts_attitude(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    client, registry, world = table
    wren = registry.gamestate.characters.get(_ids(registry, world)["Wren"])
    contact = registry.contacts.add(wren, "ally", "Hob Wade", "ferryman", "")
    saved = registry.contacts.edit(wren.id, contact.id, "Hob Wade", "ferryman", "", "friendly")
    assert saved.attitude == "friendly"
    kept = registry.contacts.edit(wren.id, contact.id, "Hob Wade", "the ferryman")
    assert kept.attitude == "friendly" and kept.note == "the ferryman"
    registry.clock.advance(world, 90, "dm", "DM")
    seen = registry.contacts.seen(wren.id, contact.id, registry.clock.of(world).minute)
    assert seen.last_seen == registry.clock.of(world).minute
