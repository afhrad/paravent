"""Help pages that ship with the app (app/services/help.py, app/help/)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.help import HELP_DIR, TOPICS, help_text, shown
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


def _sections(text: str) -> list[str]:
    return re.findall(r"^## (\d+)\.", text, re.MULTILINE)


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_every_language_has_the_same_sections(topic: str) -> None:
    """A help page is kept in step across languages: same numbered sections."""
    english = _sections(help_text(topic, "en") or "")
    assert english
    for path in HELP_DIR.glob(f"{topic}.*.md"):
        assert _sections(path.read_text("utf-8")) == english, path.name


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_every_language_marks_the_same_passages(topic: str) -> None:
    """The passages a world's features keep or leave out are the same in each language."""
    marks = re.compile(r"<!-- feature: ([^>]+?) -->")
    english = marks.findall((HELP_DIR / f"{topic}.en.md").read_text("utf-8"))
    for path in HELP_DIR.glob(f"{topic}.*.md"):
        assert marks.findall(path.read_text("utf-8")) == english, path.name


def test_passages_of_what_a_world_plays_without_are_left_out() -> None:
    text = "\n".join(
        [
            "# Help",
            "## 1. Always",
            "<!-- feature: shops chases -->",
            "## 2. Money or running",
            "<!-- feature: shops -->",
            "- Shops.",
            "<!-- /feature -->",
            "<!-- feature: chases -->",
            "- Chases.",
            "<!-- /feature -->",
            "<!-- /feature -->",
            "## 3. Last",
        ]
    )
    everything = shown(text, None)
    assert "<!--" not in everything and "- Shops." in everything and "## 3. Last" in everything
    chases = shown(text, {"chases"})
    assert "- Chases." in chases and "Shops" not in chases and "## 2. Money" in chases
    none = shown(text, set())
    assert "Money" not in none and "## 2. Last" in none  # numbered again, without a gap


def test_falls_back_to_english() -> None:
    assert help_text("combat", "fr") == help_text("combat", "en")
    assert help_text("nothing", "en") is None


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "lib", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac


async def test_the_page_in_the_readers_language(client: AsyncClient) -> None:
    english = await client.get("/help/combat", params={"back": "/gm/combat/abc"})
    assert english.status_code == 200 and "Running a fight" in english.text
    assert 'href="/gm/combat/abc"' in english.text
    await client.post("/language", data={"lang": "de", "next": "/"})
    german = (await client.get("/help/combat")).text
    assert "Einen Kampf leiten" in german and "Offene Angriffe" in german
    assert (await client.get("/help/nothing")).status_code == 404
    elsewhere = await client.get("/help/combat", params={"back": "//evil.example"})
    assert 'href="//evil.example"' not in elsewhere.text  # only a local path goes back


async def test_a_new_gamemaster_finds_the_getting_started_guide(client: AsyncClient) -> None:
    # The table, and the world's pages while there is no world.
    for where in ("/gm", "/gm/adventures"):
        assert 'href="/help/start?back=' in (await client.get(where)).text, where
    guide = await client.get("/help/start", params={"back": "/gm"})
    assert guide.status_code == 200 and "Getting started" in guide.text
    await client.post("/language", data={"lang": "de", "next": "/"})
    assert "Erste Schritte" in (await client.get("/help/start")).text


async def test_a_fresh_install_says_there_is_no_data_yet(client: AsyncClient) -> None:
    """With no corpus configured, a reference page explains that instead of "not found"."""
    page = await client.get("/screen")
    assert page.status_code == 404
    assert "No 5eTools data yet" in page.text and 'href="/help/start?back=/"' in page.text
    assert "Entry not found" not in page.text


async def test_a_player_finds_how_playing_works(client: AsyncClient) -> None:
    app_registry = client._transport.app.state.stores  # type: ignore[attr-defined]
    app_registry.auth.create_user("mara", PASSWORD, Role.PLAYER)
    await client.post("/logout")
    await client.post("/login", data={"username": "mara", "password": PASSWORD})
    home = (await client.get("/play")).text
    assert 'href="/help/player?back=/play"' in home  # on the home page
    assert 'href="/help/player?back=' in home.split("usermenu-panel", 1)[1]  # and the menu
    guide = await client.get("/help/player", params={"back": "/play"})
    assert guide.status_code == 200 and "Playing with Paravent" in guide.text
    assert "From items" in guide.text and "X-card" in guide.text
    await client.post("/language", data={"lang": "de", "next": "/"})
    german = (await client.get("/help/player")).text
    assert "Spielen mit Paravent" in german and "Aus Gegenständen" in german


async def test_a_world_reads_of_what_it_plays_with(client: AsyncClient, tmp_path: Path) -> None:
    app_registry: StoreRegistry = client._transport.app.state.stores  # type: ignore[attr-defined]
    world = app_registry.worlds.create(WorldInput(name="Plain", features=("travel",)))
    app_registry.table.select(world.id)
    between = (await client.get("/help/between")).text.split('class="md-body"', 1)[1]
    assert "On the clock panel" in between or "Set out" in between
    assert "The bastion" not in between and "Hazard" not in between
    assert "Upkeep" not in between and "<!--" not in between
    player = (await client.get("/help/player")).text.split('class="md-body"', 1)[1]
    assert "X-card" not in player and "Hero points" not in player and "Story" in player
    # Hero points and the safety tools switched on.
    app_registry.gamestate.worlds.update(
        world.id, features=["travel", "safety"], character_rules=["hero_points"]
    )
    player = (await client.get("/help/player")).text.split('class="md-body"', 1)[1]
    assert "X-card" in player and "Hero points" in player and "Honor" not in player
