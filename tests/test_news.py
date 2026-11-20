"""What's new in Paravent, read from app/news.json (app/services/news.py)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.news import NEWS_FILE, NewsFeed
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"

LINES = [
    {"at": "2026-11-20T12:00:00+01:00", "en": "Newest thing.", "de": "Neueste Sache."},
    {"at": "2026-11-18T12:00:00+01:00", "en": "Two days older.", "de": "Zwei Tage älter."},
    {"at": "2026-11-01T12:00:00+01:00", "en": "Long ago.", "de": "Lange her."},
]


def test_unseen_lines_since_the_last_look(tmp_path: Path) -> None:
    path = tmp_path / "news.json"
    path.write_text(json.dumps(LINES))
    feed = NewsFeed(path)
    # Nothing seen yet: the last seven days.
    assert [x.en for x in feed.unseen("")] == ["Newest thing.", "Two days older."]
    assert [x.text("de") for x in feed.unseen("2026-11-19T00:00:00+01:00")] == ["Neueste Sache."]
    assert feed.unseen(LINES[0]["at"]) == []
    assert NewsFeed(tmp_path / "missing.json").unseen("") == []


def test_the_shipped_file_reads() -> None:
    lines = NewsFeed(NEWS_FILE).lines()
    assert lines and all(x.at and x.en and x.de for x in lines)


@pytest.fixture
async def gm(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    path = tmp_path / "news.json"
    path.write_text(json.dumps(LINES))
    registry.__dict__["news"] = NewsFeed(path)  # the cached property, given this file
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client, registry


async def test_the_home_page_shows_it_until_read(
    gm: tuple[AsyncClient, StoreRegistry],
) -> None:
    client, _registry = gm
    page = (await client.get("/gm")).text
    assert "What&#39;s new in Paravent" in page or "What's new in Paravent" in page
    assert "Newest thing." in page and "Long ago." not in page
    done = await client.post("/gm/news/seen", data={"at": LINES[0]["at"]})
    assert done.headers["location"] == "/gm"
    assert "Newest thing." not in (await client.get("/gm")).text
