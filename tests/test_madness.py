"""Madness and mental stress on characters (app/services/madness.py), carried as an
affliction. The tables are invented; a world without the 2014 guide takes the 2024
guide's effects, which need no table."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.models.pc import PlayerCharacter
from app.services.book_tables import Rows
from app.services.dice import Dice
from app.services.madness import SHORT_2024, roll_madness, rolls_2014
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from tests.conftest import log_about

PASSWORD = "correct horse"

INVENTED: dict[tuple[str, str], Rows] = {
    ("Short-Term Madness", "DMG"): [
        ["01-50", "The character stares at nothing and is {@condition stunned|XPHB}."],
        ["51-00", "The character hums an endless tune."],
    ],
}


def _find(caption: str, books: Sequence[str]) -> Rows | None:
    return next((INVENTED[(caption, b)] for b in books if (caption, b) in INVENTED), None)


def test_rolled_on_the_2014_table_where_the_world_has_it() -> None:
    assert rolls_2014(_find)
    seen = {roll_madness("short", _find, Dice(random.Random(n))) for n in range(20)}
    texts = {e.text for e in seen}
    assert texts == {
        "The character stares at nothing and is stunned.",
        "The character hums an endless tune.",
    }
    stunned = next(e for e in seen if e.conditions)
    assert stunned.conditions == ("stunned",) and stunned.source == "DMG"
    assert (stunned.lasts, stunned.unit) == ("1d10", 1)


def test_the_2024_effects_without_the_table() -> None:
    def nothing(caption: str, books: Sequence[str]) -> None:
        return None

    assert not rolls_2014(nothing)
    short = roll_madness("short", nothing, Dice(random.Random(1)))
    assert short.text == "" and len(short.conditions) == 1 and short.conditions[0] in SHORT_2024
    long = roll_madness("long", nothing, Dice(random.Random(1)))
    assert long.conditions == () and (long.lasts, long.unit) == ("1d10", 600)
    assert roll_madness("indefinite", nothing, Dice()).lasts == ""


@pytest.fixture
async def table(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.worlds.create(WorldInput(name="Grey Reed", features=("madness",)))
    registry.table.select(world.id)
    for name in ("Wren", "Tamsin"):
        registry.gamestate.characters.create(PlayerCharacter(name=name, world_id=world.id))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client, registry, world.id


async def test_madness_from_the_party_view(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    client, registry, world = table
    page = (await client.get("/gm")).text
    assert 'data-dialog="tool-madness"' in page and "Frightened, Incapacitated or Stunned" in page
    ids = {pc.name: pc.id for pc in registry.gamestate.characters.of_world(world)}
    done = await client.post(
        "/gm/madness",
        data={"world": world, "level": "short", "ability": "", "to": [ids["Wren"]]},
    )
    assert done.headers["location"] == "/gm?mad=1%3A1"
    wren = registry.gamestate.characters.get(ids["Wren"])
    (madness,) = wren.state.afflictions
    assert madness.kind == "madness" and madness.name == "Short-term madness"
    assert madness.conditions[0] in SHORT_2024 and madness.conditions == wren.state.conditions
    assert madness.ends is not None and madness.ends <= registry.clock.of(world).minute + 10
    assert registry.gamestate.characters.get(ids["Tamsin"]).state.afflictions == []
    notice = (await client.get(done.headers["location"])).text
    assert "1 of 1 character failed" in notice
    sheet = (await client.get(f"/characters/{ids['Wren']}")).text
    assert "Short-term madness" in sheet and "Diseases, poisons, curses and madness" in sheet
    # It wears off with the clock, its condition with it.
    registry.clock.advance(world, 20, "dm", "DM")
    wren = registry.gamestate.characters.get(ids["Wren"])
    assert wren.state.afflictions == [] and wren.state.conditions == []


async def test_a_longer_madness_reads_as_disadvantage(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    client, registry, world = table
    ids = [pc.id for pc in registry.gamestate.characters.of_world(world)]
    await client.post(
        "/gm/madness",
        data={
            "world": world,
            "level": "indefinite",
            "ability": "wis",
            "dc": "30",  # no one makes it
            "damage": "2d6",
            "to": ids,
        },
    )
    for cid in ids:
        (madness,) = registry.gamestate.characters.get(cid).state.afflictions
        assert madness.note == "Disadvantage on some or all ability checks."
        assert madness.ends is None and madness.conditions == []
        caught = next(e for e in log_about(registry, cid) if "madness" in e.what)
        _step, _kind, _name, roll, dc, failed, damage = caught.what.split(":")[1:8]
        assert dc == "30" and failed == "1" and int(roll) < 30 and 2 <= int(damage) <= 12


async def test_nothing_chosen_opens_the_dialog_again(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    client, _registry, world = table
    refused = await client.post("/gm/madness", data={"world": world, "level": "", "to": []})
    assert refused.headers["location"] == "/gm?tool=madness"
