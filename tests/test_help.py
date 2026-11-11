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
from app.services.help import HELP_DIR, TOPICS, help_text
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
    for where in ("/gm", "/gm/worlds"):  # the table, and worlds while there are none
        assert 'href="/help/start?back=' in (await client.get(where)).text, where
    guide = await client.get("/help/start", params={"back": "/gm"})
    assert guide.status_code == 200 and "Getting started" in guide.text
    await client.post("/language", data={"lang": "de", "next": "/"})
    assert "Erste Schritte" in (await client.get("/help/start")).text
