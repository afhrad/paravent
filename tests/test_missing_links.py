"""A link to an NPC, place, treasure or encounter the world lacks: the gamemaster is offered
to make it where the text is (app/ui/markdown.py, app/routes/editor.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.routes.adventures import close_names
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


def _chapter(registry: StoreRegistry) -> str:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    registry.gamestate.chapters.update(
        fog.id, body="The captain's purse: {@treasure Captain's Purse}.\n\n{@npc Hob}\n"
    )
    return fog.id


async def test_a_missing_link_offers_to_make_it(gm: AsyncClient, registry: StoreRegistry) -> None:
    chapter_id = _chapter(registry)
    page = (await gm.get(f"/gm/chapters/{chapter_id}")).text
    assert page.count('class="ref missing creatable"') == 2  # in the text, and alone on a line
    href = next(
        part.split('"')[0]
        for part in page.split('href="')[1:]
        if part.startswith("/gm/editor/new") and "treasure" in part.split('"')[0]
    ).replace("&amp;", "&")
    query = parse_qs(urlsplit(href).query)
    assert query["scope"] == [f"c:{chapter_id}"] and query["back"] == [f"/gm/chapters/{chapter_id}"]
    asked = (await gm.get(href)).text
    assert "Create “Captain&#39;s Purse”?" in asked or "Create “Captain's Purse”?" in asked
    assert "in the chapter “Fog”" in asked
    made = await gm.post(
        "/gm/editor/new",
        data={"kind": "treasure", "name": "Captain's Purse", "scope": f"c:{chapter_id}"},
    )
    assert made.status_code == 303 and made.headers["location"].startswith("/gm/treasure/")
    (treasure,) = registry.gamestate.treasures.list()
    assert treasure.name == "Captain's Purse" and treasure.scope.chapter_id == chapter_id
    page = (await gm.get(f"/gm/chapters/{chapter_id}")).text
    assert page.count('class="ref missing creatable"') == 1  # only Hob is left
    # Asked again once it exists: straight to it, nothing made twice.
    again = await gm.get(href)
    assert again.status_code == 303 and again.headers["location"] == made.headers["location"]
    assert len(registry.gamestate.treasures.list()) == 1


async def test_a_player_view_offers_nothing(gm: AsyncClient, registry: StoreRegistry) -> None:
    chapter_id = _chapter(registry)
    page = (await gm.get(f"/gm/chapters/{chapter_id}?as=player")).text
    assert "creatable" not in page
    bad = await gm.get("/gm/editor/new", params={"kind": "chapter", "name": "X", "scope": "w:x"})
    assert bad.status_code == 400


def test_close_names_find_typos_and_parts() -> None:
    pool = ["Modiras Dank", "Beutel des Hauptmanns", "Ferry Toll", "Hob Wade"]
    assert close_names("Modira's Dank", pool) == ["Modiras Dank"]
    assert close_names("Beutel", pool) == ["Beutel des Hauptmanns"]
    assert close_names("Hob Wade", pool) == []  # itself is no suggestion
    assert close_names("Dragon Hoard", pool) == []
    assert close_names("Hob", pool) == ["Hob Wade"] and close_names("ob", pool) == []


async def test_the_check_suggests_what_the_text_meant(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    chapter_id = _chapter(registry)
    chapter = registry.gamestate.chapters.get(chapter_id)
    registry.treasure.create(
        "Captain's Purse (silver)", f"a:{chapter.adventure_id}", chapter.world_id
    )
    registry.npcs.create("Hob Wade", f"w:{chapter.world_id}", chapter.world_id)
    body = (
        "{@treasure Captain's Purse|the purse}\n\n{@npc Hob}\n\n{@place Landing}\n\nSee [[Fgo]].\n"
    )
    panel = (await gm.post(f"/gm/chapters/{chapter_id}/check", data={"body": body})).text
    # The label is kept; the name is the one that exists.
    assert 'data-to="{@treasure Captain&#39;s Purse (silver)|the purse}"' in panel
    assert 'data-to="{@npc Hob Wade}"' in panel
    assert 'data-to="[[Fog]]"' in panel
    assert 'data-kind="place" data-name="Landing"' in panel  # nothing near: create it
