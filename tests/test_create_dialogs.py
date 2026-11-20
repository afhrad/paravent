"""Every list makes its new things the same way: a **New …** button that opens a dialog
(app/ui/tool_dialog.py, app/ui/library_links.py)."""

from __future__ import annotations

import html
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from app.ui.markdown import CALLOUTS

PASSWORD = "correct horse"


@pytest.fixture
async def gm(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client, registry


async def test_every_list_has_a_new_button_and_its_dialog(
    gm: tuple[AsyncClient, StoreRegistry],
) -> None:
    client, registry = gm
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    registry.table.select(world.id)
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    lists = {
        "/gm/adventures": "tool-adventure",
        f"/gm/adventures/{bell.id}": "tool-chapter",
        "/gm/characters": "tool-character",
        "/gm/encounters": "tool-encounter",
        "/gm/factions": "tool-faction",
        "/gm/quests": "tool-quest",
        "/gm/creatures": "tool-creature",
        "/gm/players": "tool-player",
        "/gm/sessions": "tool-session-note",
        "/gm/sounds": "tool-sound",
        "/gm/mcp": "tool-key",
        "/gm/places": "new-place",
        "/gm/npcs": "new-npc",
        "/gm/treasure": "new-treasure",
    }
    for url, dialog_id in lists.items():
        page = (await client.get(url, follow_redirects=True)).text
        assert f'data-dialog="{dialog_id}"' in page, url
        assert f'<dialog id="{dialog_id}"' in page, url
    # The adventure's holdings make theirs the same way.
    adventure = (await client.get(f"/gm/adventures/{bell.id}")).text
    for kind in ("npc", "place", "encounter", "treasure"):
        assert f'data-dialog="tool-hold-{kind}"' in adventure
        assert f'<dialog id="tool-hold-{kind}"' in adventure
    # Without the script the button reloads the page with its dialog open.
    opened = (await client.get("/gm/encounters?tool=encounter")).text
    assert " open" in opened.split('<dialog id="tool-encounter"', 1)[1].split(">", 1)[0]


async def test_every_editor_offers_every_callout(gm: tuple[AsyncClient, StoreRegistry]) -> None:
    client, registry = gm
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    registry.table.select(world.id)
    page = (await client.get("/gm/npcs")).text
    # The toolbar's data, on every gamemaster page: every callout the Markdown knows.
    raw = page.split('id="insert-palette"', 1)[1].split('data-tools="', 1)[1].split('"', 1)[0]
    tools = json.loads(html.unescape(raw))
    kinds = [k for k, _name, _icon in tools["callouts"]]
    assert set(kinds) == set(CALLOUTS) and kinds[:3] == ["read", "secret", "sidebar"]
    assert dict((k, n) for k, n, _ in tools["callouts"])["warning"] == "Warning"
    # A new NPC's first note is an editor with the toolbar, like the world's description.
    assert 'id="new-npc-notes" name="notes" rows="4" class="prose-editor"' in page
    settings = (await client.get(f"/gm/worlds/{world.id}/edit")).text
    assert 'id="f-description" name="description" rows="3" class="prose-editor"' in settings
