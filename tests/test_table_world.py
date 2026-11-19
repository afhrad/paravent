"""The world the whole table plays in, and the gamemaster's world menu
(app/services/table.py, app/ui/world_menu.py; documentation/CURRENT-WORLD.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.table import TableError
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("pia", PASSWORD, Role.PLAYER)
    return registry


async def _client(registry: StoreRegistry, user: str) -> AsyncClient:
    app = create_app()
    app.state.stores = registry
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client.post("/login", data={"username": user, "password": PASSWORD})
    return client


@pytest.fixture
async def gm(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    client = await _client(registry, "dm")
    yield client
    await client.aclose()


def test_the_table_plays_in_one_world(registry: StoreRegistry) -> None:
    table = registry.table
    assert table.world() is None  # no world yet
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    assert table.world_id() == grey.id  # none chosen: the first by name
    table.select(salt.id)
    assert table.world_id() == salt.id
    # The table's theme is kept beside it, neither undoing the other.
    registry.themes.set_table("")
    assert table.world_id() == salt.id
    with pytest.raises(TableError):
        table.select("gone")
    # A deleted world: the table plays in the next one.
    registry.worlds.delete(salt.id)
    assert table.world_id() == grey.id


async def test_the_world_menu_chooses_makes_and_sets_up(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    page = (await gm.get("/gm")).text
    assert 'class="world-menu"' in page and "Create a world" in page
    assert 'id="world-settings"' not in page  # nothing to set up yet
    # A new world from the menu's dialog is the table's at once, its settings opened.
    made = await gm.post("/gm/worlds", data={"name": "Grey Reed", "next": "/gm"})
    grey = registry.table.world()
    assert grey is not None and grey.name == "Grey Reed"
    assert made.headers["location"] == f"/gm/worlds/{grey.id}?created=1&settings=1"
    page = (await gm.get(made.headers["location"])).text
    assert 'id="world-settings"' in page and "data-open-now" in page
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    page = (await gm.get("/gm")).text
    assert f'name="world" value="{salt.id}"' in page and "Salt Coast" in page
    assert f'value="{grey.id}" class="usermenu-link world-menu-world" aria-current="true"' in page
    # Choosing one: the table's, back to where it was chosen.
    chosen = await gm.post("/gm/world/select", data={"world": salt.id, "next": "/gm/encounters"})
    assert chosen.headers["location"] == "/gm/encounters"
    assert registry.table.world_id() == salt.id
    assert (await gm.post("/gm/world/select", data={"world": "gone"})).status_code == 404
    # The settings dialog's content: the world form, saving back to the page it was opened on.
    form = (await gm.get("/gm/world/settings", params={"next": "/gm/encounters"})).text
    assert f'action="/gm/worlds/{salt.id}"' in form and 'name="next" value="/gm/encounters"' in form
    assert "<html" not in form and "data-close" in form
    saved = await gm.post(
        f"/gm/worlds/{salt.id}", data={"name": "Salt Coast", "next": "/gm/encounters"}
    )
    assert saved.headers["location"] == "/gm/encounters"
    # Never off the site.
    away = await gm.post(f"/gm/worlds/{salt.id}", data={"name": "Salt Coast", "next": "//evil"})
    assert away.headers["location"].startswith(f"/gm/worlds/{salt.id}")


async def test_a_player_has_no_world_menu(registry: StoreRegistry) -> None:
    registry.worlds.create(WorldInput(name="Grey Reed"))
    client = await _client(registry, "pia")
    try:
        page = (await client.get("/play")).text
        assert 'class="world-menu"' not in page and 'id="new-world"' not in page
        assert (await client.post("/gm/world/select", data={"world": "x"})).status_code == 403
    finally:
        await client.aclose()


async def test_the_bar_links_the_current_world(gm: AsyncClient, registry: StoreRegistry) -> None:
    # Before any world: its pages ask for one.
    empty = (await gm.get("/gm/npcs")).text
    assert "No world yet" in empty and 'data-dialog="new-world"' in empty
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    registry.table.select(grey.id)
    registry.npcs.create("Maren", f"w:{grey.id}", grey.id)
    registry.npcs.create("Odo", f"w:{salt.id}", salt.id)
    page = (await gm.get("/gm/npcs")).text
    assert "Maren" in page and "Odo" not in page
    bar = page.split('class="shell-nav"', 1)[1].split("</nav>", 1)[0]
    # Every link of the world, ranked; no Worlds; More holds the same links.
    for href in (
        "/gm/adventures",
        "/gm/npcs",
        "/gm/places",
        "/gm/treasure",
        "/gm/encounters",
        "/gm/combat",
        "/gm/quests",
        "/gm/factions",
        "/journal",
        "/gm/sessions",
    ):
        assert f'href="{href}"' in bar, href
    assert 'href="/gm/worlds"' not in bar and 'class="nav-more"' in bar
    assert 'href="/gm/npcs" class="nav-r2" aria-current="page"' in bar
    assert 'class="nav-more-button is-current"' in bar  # the NPCs may sit in More
    for address in ("/gm/adventures", "/gm/places", "/gm/treasure", "/gm/sessions"):
        assert (await gm.get(address)).status_code == 200, address
    # Choosing another world from a world's page: the same section of the new one, and
    # the sections' remembered pages of the old world forgotten.
    await gm.get(f"/gm/worlds/{grey.id}/npcs")
    user = registry.auth.find_user("dm")
    assert user is not None and "/gm/npcs" in registry.places.nav(user.id)
    moved = await gm.post(
        "/gm/world/select", data={"world": salt.id, "next": f"/gm/worlds/{grey.id}/npcs"}
    )
    assert moved.headers["location"] == "/gm/npcs"
    assert "/gm/npcs" not in registry.places.nav(user.id)
    assert "Odo" in (await gm.get("/gm/npcs")).text
    # A page of no world section stays where it was.
    stay = await gm.post("/gm/world/select", data={"world": grey.id, "next": "/books"})
    assert stay.headers["location"] == "/books"


async def test_another_worlds_page_offers_to_switch(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    registry.table.select(grey.id)
    odo = registry.npcs.create("Odo", f"w:{salt.id}", salt.id)
    maren = registry.npcs.create("Maren", f"w:{grey.id}", grey.id)
    # The table's own world: no banner.
    assert 'class="world-banner"' not in (await gm.get(f"/gm/npcs/{maren.id}")).text
    # Another world's NPC opens, saying whose it is; nothing switches by itself.
    page = (await gm.get(f"/gm/npcs/{odo.id}")).text
    assert 'class="world-banner"' in page and "Switch to Salt Coast" in page
    assert registry.table.world_id() == grey.id
    # Switching from there stays on the NPC, now of the table's world.
    switched = await gm.post(
        "/gm/world/select", data={"world": salt.id, "next": f"/gm/npcs/{odo.id}"}
    )
    assert switched.headers["location"] == f"/gm/npcs/{odo.id}"
    assert 'class="world-banner"' not in (await gm.get(f"/gm/npcs/{odo.id}")).text
    # A world's old address shows its page, with the banner while it is not the table's.
    assert 'class="world-banner"' in (await gm.get(f"/gm/worlds/{grey.id}/npcs")).text


async def test_the_worlds_page_is_its_adventures(gm: AsyncClient, registry: StoreRegistry) -> None:
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    registry.adventures.create(grey.id, "The Drowned Bell")
    page = (await gm.get("/gm/adventures")).text
    main = page.split('id="main"', 1)[1]
    assert "The Drowned Bell" in main and "<h1>Adventures</h1>" in main
    # Its settings, deleting it and its other lists are the world menu's and the bar's.
    assert f"/gm/worlds/{grey.id}/edit" not in main and f"/gm/worlds/{grey.id}/delete" not in main
    assert 'class="world-links"' not in main
    menu = page.split('class="world-menu"', 1)[1].split("</details>", 1)[0]
    assert f"/gm/worlds/{grey.id}/names" in menu and f"/gm/worlds/{grey.id}/claude" in menu
    # A trail no longer starts at the worlds' list.
    npcs = (await gm.get(f"/gm/worlds/{grey.id}/npcs")).text
    trail = npcs.split('class="crumbs"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/gm/worlds"' not in trail and "Grey Reed" in trail


async def test_the_pages_show_the_tables_world_without_a_picker(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    registry.table.select(salt.id)
    registry.encounters.create("Ambush at the ford", grey.id)
    registry.encounters.create("Salt raiders", salt.id)
    registry.encounters.create("Planned ahead", None)
    page = (await gm.get("/gm/encounters")).text
    assert "Salt raiders" in page and "Planned ahead" in page
    assert "Ambush at the ford" not in page
    assert 'id="enc-world"' not in page and "in Salt Coast" in page
    # Two worlds, none named: the table's, where before they asked or showed all.
    for address in ("/gm/quests", "/gm/factions", "/gm/session"):
        shown = await gm.get(address)
        assert shown.status_code == 200 and "Salt Coast" in shown.text, address
    party = (await gm.get("/gm")).text
    assert 'id="party-world"' not in party and "Salt Coast" in party
    assert (await gm.get("/journal")).status_code == 200


def test_a_character_plays_in_the_tables_world(registry: StoreRegistry) -> None:
    import json

    from app.models.pc import PlayerCharacter

    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    salt = registry.worlds.create(WorldInput(name="Salt Coast"))
    registry.table.select(grey.id)
    store = registry.gamestate.characters
    # A file naming a world of its own (written before) is served in the table's.
    lyra = store.create(PlayerCharacter(name="Lyra", world_id=salt.id))
    assert store.get(lyra.id).world_id == grey.id
    path = store.path(lyra.id)
    assert path is not None and "world_id" not in json.loads(path.read_text(encoding="utf-8"))
    # Switching the table's world moves every character with it, the party too.
    registry.table.select(salt.id)
    assert store.get(lyra.id).world_id == salt.id
    assert [m.sheet.character.name for m in registry.characters.party(salt.id)] == ["Lyra"]
    # A change written keeps the file free of a world.
    store.update(lyra.id, name="Lyra Mooncaller")
    assert "world_id" not in json.loads(path.read_text(encoding="utf-8"))
    assert store.get(lyra.id).world_id == salt.id


async def test_the_settings_hold_the_pictures_and_the_list_is_the_menus(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    grey = registry.worlds.create(WorldInput(name="Grey Reed"))
    form = (await gm.get("/gm/world/settings", params={"next": "/gm/npcs"})).text
    # Its cover and pictures, coming back to the page with the dialog open again.
    assert 'class="panel images-panel"' in form and "data-image-editor" in form
    assert 'name="next" value="/gm/npcs?settings=1"' in form
    # The old list of worlds leads to the table's world; deleting one too.
    assert (await gm.get("/gm/worlds")).headers["location"] == "/gm/adventures"
    gone = await gm.post(f"/gm/worlds/{grey.id}/delete")
    assert gone.headers["location"] == "/gm/adventures"


async def test_a_player_sees_the_world_the_table_plays_in(registry: StoreRegistry) -> None:
    registry.worlds.create(WorldInput(name="Grey Reed"))
    client = await _client(registry, "pia")
    try:
        assert "You are playing in Grey Reed" in (await client.get("/play")).text
    finally:
        await client.aclose()


async def test_a_world_made_from_the_menu_is_in_the_makers_language(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    await gm.post("/language", data={"lang": "de"})
    await gm.post("/gm/worlds", data={"name": "Nebelgipfel", "next": "/gm"})
    world = registry.table.world()
    assert world is not None and world.language == "de"
