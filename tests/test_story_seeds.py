"""A villain for an NPC and ideas for an adventure, rolled on the guides' tables
(app/services/story_seeds.py). The unit tests use invented tables; the route tests read
the user's guides."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Npc, Role, Scope
from app.models.pc import ClassLevel, PlayerCharacter
from app.services.book_tables import Rows
from app.services.dice import Dice
from app.services.story_seeds import SeedGenerator, band_caption
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"

INVENTED: dict[tuple[str, str], Rows] = {
    ("Villain's Scheme", "DMG"): [
        ["1", "Tides ({@dice d2|d2|Tides})", ""],
        ["", "1", "Drown the lower town"],
        ["", "2", "Hold back the spring flood"],
        ["2", "Bells", ""],
    ],
    ("Villain's Weakness", "DMG"): [["1", "Cannot cross running water."], ["2", "Fears owls."]],
    ("Dungeon Goals", "DMG"): [["1", "Ring the drowned bell."]],
    ("Twists", "DMG"): [["1", "The ferryman lied."], ["2", "The bell was never lost."]],
    ("Levels 5–10 Adventure Situations", "XDMG"): [["1", "A heron cult floods the mill."]],
    ("Supernatural Hooks", "XDMG"): [["1", "A reed whispers a name."]],
}


def _find(caption: str, books: Sequence[str]) -> Rows | None:
    return next((INVENTED[(caption, b)] for b in books if (caption, b) in INVENTED), None)


def test_a_villain_rolls_the_two_step_tables() -> None:
    gen = SeedGenerator(_find, Dice(random.Random(2)), kind="villain", new_rules=False)
    assert gen.parts() == ["scheme", "weakness"]  # what the books hold
    schemes = {gen.roll_part("scheme") for _ in range(40)}
    assert schemes == {"Tides: Drown the lower town", "Tides: Hold back the spring flood", "Bells"}
    assert gen.roll_part("weakness", "Fears owls.") == "Cannot cross running water."
    assert gen.roll({"weakness": "Kept"})["weakness"] == "Kept"


def test_an_adventure_by_the_worlds_guide() -> None:
    old = SeedGenerator(
        _find, Dice(random.Random(1)), kind="adventure", new_rules=False, goal_label=str.upper
    )
    assert old.parts() == ["goal", "twist"]
    assert old.roll()["goal"] == "DUNGEON: Ring the drowned bell."
    new = SeedGenerator(_find, Dice(random.Random(1)), kind="adventure", new_rules=True, level=7)
    assert new.parts() == ["situation", "hook", "twist"]  # 2014's twist joins the 2024 parts
    assert new.roll()["situation"] == "A heron cult floods the mill."
    assert band_caption(17) == "Levels 17–20 Adventure Situations"


@pytest.fixture
async def gm(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client, registry


def _world(registry: StoreRegistry, books: list[str]) -> str:
    world = registry.worlds.create(WorldInput(name="Grey Reed", sources=tuple(books)))
    registry.table.select(world.id)
    return world.id


async def test_an_npc_kept_as_a_villain(gm: tuple[AsyncClient, StoreRegistry]) -> None:
    client, registry = gm
    world = _world(registry, ["PHB", "MM", "DMG"])
    marla = registry.gamestate.npcs.create(
        Npc(scope=Scope(world_id=world), name="Old Marla", notes="Keeps the ferry.")
    )
    url = f"/gm/npcs/{marla.id}/villain"
    assert url in (await client.get(f"/gm/npcs/{marla.id}")).text
    page = (await client.get(url)).text
    for label in ("Villain", "Scheme", "Methods", "Weakness"):
        assert f'settle-label">{label}<' in page
    form = {f"v_{p}": f"{p} as rolled" for p in ("villain", "scheme", "methods", "weakness")}
    again = (await client.post(url, data={**form, "action": "reroll:weakness"})).text
    assert "weakness as rolled" not in again and "scheme as rolled" in again
    done = await client.post(url, data={**form, "action": "apply", "keep": ["scheme", "weakness"]})
    assert done.headers["location"] == f"/gm/npcs/{marla.id}"
    notes = registry.npcs.get(marla.id).notes
    assert notes.startswith(
        "Keeps the ferry.\n\n**As a villain**\n\n- **Scheme:** scheme as rolled"
    )
    assert "methods as rolled" not in notes


async def test_a_2024_world_has_no_villain_tables(gm: tuple[AsyncClient, StoreRegistry]) -> None:
    client, registry = gm
    world = _world(registry, ["XPHB", "XMM", "XDMG"])
    marla = registry.gamestate.npcs.create(Npc(scope=Scope(world_id=world), name="Old Marla"))
    page = (await client.get(f"/gm/npcs/{marla.id}/villain")).text
    assert "no villain tables" in page


async def test_ideas_for_a_2024_adventure(gm: tuple[AsyncClient, StoreRegistry]) -> None:
    client, registry = gm
    world = _world(registry, ["XPHB", "XMM", "XDMG"])
    registry.gamestate.characters.create(
        PlayerCharacter(
            name="Wren",
            world_id=world,
            classes=[ClassLevel.model_validate({"class": "Fighter|XPHB", "level": 6})],
        )
    )
    adventure = registry.adventures.create(world, "The Drowned Bell")
    url = f"/gm/adventures/{adventure.id}/ideas"
    assert url in (await client.get(f"/gm/adventures/{adventure.id}")).text
    page = (await client.get(url)).text
    assert "The situation fits a party of level 6." in page
    for label in ("Situation", "Hook", "Climax", "Leads on to"):
        assert f'settle-label">{label}<' in page
    assert 'settle-label">Twist<' not in page  # the 2014 guide is not declared
    done = await client.post(
        url,
        data={"v_situation": "Floods", "v_hook": "A dream", "action": "apply", "keep": "hook"},
    )
    assert done.headers["location"] == f"/gm/adventures/{adventure.id}"
    body = registry.adventures.get(adventure.id).body
    assert body == "> [!secret] Ideas\n> - **Hook:** A dream"
