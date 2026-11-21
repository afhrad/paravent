"""A place rolled as a settlement on the guides' tables (app/services/settlement_gen.py).
The unit tests use invented tables; the corpus tests read the user's guides."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Location, Role, Scope
from app.services.book_tables import Rows
from app.services.dice import Dice
from app.services.settlement_gen import SettlementGenerator, plays_2024
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"

INVENTED: dict[tuple[str, str], Rows] = {
    ("Notable Traits", "DMG"): [["1", "Reed roofs"], ["2", "Stilts over the marsh"]],
    ("Shop", "DMG"): [["1-2", "Eel seller"], ["3", "Ropewalk"], ["4", "Net mender"]],
    ("Tavern Name Generator", "DMG"): [["1", "The Drowned", "Bell"], ["2", "The Grey", "Heron"]],
    ("Settlements by Size", "XDMG"): [["Village", "Up to 50", "2 GP"]],
}


def _find(caption: str, books: Sequence[str]) -> Rows | None:
    return next((INVENTED[(caption, b)] for b in books if (caption, b) in INVENTED), None)


def test_only_the_parts_the_books_have_are_rolled() -> None:
    generator = SettlementGenerator(_find, Dice(random.Random(3)), new_rules=False)
    assert generator.parts() == ["trait", "tavern", "shops"]
    rolled = generator.roll()
    assert rolled["trait"] in ("Reed roofs", "Stilts over the marsh")
    first, second = rolled["tavern"].rsplit(" ", 1)
    assert first in ("The Drowned", "The Grey") and second in ("Bell", "Heron")
    shops = rolled["shops"].split(", ")
    assert len(shops) == 3 and len(set(shops)) == 3  # three, none twice
    # One part again: another, where the table has one.
    again = generator.roll_part("trait", rolled["trait"])
    assert again != rolled["trait"]
    # What is kept stays.
    assert generator.roll({"trait": "Kept"})["trait"] == "Kept"
    sizes = generator.sizes()
    assert [s.key for s in sizes] == ["village", "town", "city"]
    assert (sizes[0].population, sizes[1].population) == ("Up to 50", "")


def test_which_guide_a_world_rolls_on() -> None:
    assert plays_2024(["XPHB", "XMM", "XDMG"]) and plays_2024([])
    assert not plays_2024(["PHB", "MM", "DMG"])


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


def _place(registry: StoreRegistry, books: list[str]) -> Location:
    world = registry.worlds.create(WorldInput(name="Grey Reed", sources=tuple(books)))
    registry.table.select(world.id)
    return registry.gamestate.locations.create(
        Location(scope=Scope(world_id=world.id), name="Hob's Ford", notes="A ferry crossing.")
    )


async def test_a_2024_settlement_kept_in_the_notes(
    gm: tuple[AsyncClient, StoreRegistry],
) -> None:
    client, registry = gm
    ford = _place(registry, ["XPHB", "XMM", "XDMG"])
    assert f"/gm/places/{ford.id}/settlement" in (await client.get(f"/gm/places/{ford.id}")).text
    url = f"/gm/places/{ford.id}/settlement"
    page = (await client.get(url)).text
    for label in ("Defining trait", "Known for", "Current trouble", "Tavern", "Shops"):
        assert label in page
    assert "Government" not in page  # only the 2014 guide prints it, and it is not declared
    assert "Town (501–5,000)" in page
    form = {
        f"v_{p}": f"{p} as rolled"
        for p in ("leader", "trait", "fame", "calamity", "tavern", "shops")
    }
    rerolled = (await client.post(url, data={**form, "action": "reroll:trait", "size": "town",
                                             "keep": ["trait", "tavern"]})).text  # fmt: skip
    assert "trait as rolled" not in rerolled and "tavern as rolled" in rerolled
    done = await client.post(
        url, data={**form, "action": "apply", "size": "town", "keep": ["tavern", "fame"]}
    )
    assert done.headers["location"] == f"/gm/places/{ford.id}"
    kept = registry.locations.get(ford.id)
    assert kept.notes.startswith("A ferry crossing.\n\n**Size:** Town (501–5,000)")
    assert "- **Tavern:** tavern as rolled" in kept.notes and "trait as rolled" not in kept.notes
    assert "town" in kept.tags


async def test_a_2014_settlement(gm: tuple[AsyncClient, StoreRegistry]) -> None:
    client, registry = gm
    ford = _place(registry, ["PHB", "MM", "DMG"])
    page = (await client.get(f"/gm/places/{ford.id}/settlement")).text
    for label in ("Government", "Leader", "Relations between its peoples", "Defining trait"):
        assert label in page
    assert ">DMG<" in page and ">XDMG<" not in page


async def test_a_rolled_name_and_a_tavern_by_the_worlds_style(
    gm: tuple[AsyncClient, StoreRegistry],
) -> None:
    client, registry = gm
    ford = _place(registry, ["PHB", "MM", "DMG"])
    url = f"/gm/places/{ford.id}/settlement"
    page = (await client.get(url)).text
    assert 'id="f-name"' in page and "name-roll-dice" in page  # the place's name dice
    assert "naming style" not in page  # starters alone leave the tavern to the guide
    registry.worlds.set_naming(ford.scope.world_id, ["starter:english-taverns"])
    page = (await client.get(url)).text
    assert "naming style" in page
    await client.post(url, data={"action": "apply", "size": "village", "name": "Hob's Landing"})
    renamed = registry.locations.get(ford.id)
    assert renamed.name == "Hob's Landing" and "**Size:** Village" in renamed.notes
