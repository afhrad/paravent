"""Context menus (documentation/CONTEXT-MENUS.md): what each offers to whom, and the
changes to creating and deleting they bring (back where asked, with Undo; a confirmation
as a dialog; a copy; moved up; New … here)."""

from __future__ import annotations

import html
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from app.models.gamestate import Role, Scope
from app.services.copies import copy_name
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import PASSWORD, table

__all__ = ["table"]


def test_copies_are_numbered() -> None:
    assert copy_name("Ilse", set(), " (copy)") == "Ilse (copy)"
    assert copy_name("Ilse", {"ilse (copy)"}, " (copy)") == "Ilse (copy 2)"
    assert copy_name("Ilse", {"ilse (kopie)", "ilse (kopie 2)"}, " (Kopie)") == "Ilse (Kopie 3)"


def _menu_of(href: str, next_page: str = "") -> str:
    url = f"/menu/link?href={quote(href, safe='')}"
    return url + (f"&next={quote(next_page, safe='')}" if next_page else "")


@requires_corpus
async def test_an_npc_row_menu_deletes_in_place_with_undo(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    ilse = registry.npcs.create("Mother Ilse", f"w:{world}", world)
    listing = (await ac.get(f"/gm/npcs?world={world}")).text
    assert "data-menu-open" in listing and f"/gm/npcs/{ilse.id}/delete" not in listing
    here = f"/gm/npcs?world={world}"
    menu = html.unescape((await ac.get(_menu_of(f"/gm/npcs/{ilse.id}", here))).text)
    assert "Mother Ilse" in menu and "Duplicate" in menu and "can be undone" in menu
    deleted = await ac.post(f"/gm/npcs/{ilse.id}/delete", data={"next": here})
    location = deleted.headers["location"]
    assert (
        location.startswith("/gm/npcs?world=") and "deleted=1" in location and "undo=" in location
    )
    assert "undo-notice" in (await ac.get(location)).text
    # A next pointing at the gone NPC's own page, or at another site, returns to the list.
    again = registry.npcs.create("Old Marla", f"w:{world}", world)
    gone = await ac.post(f"/gm/npcs/{again.id}/delete", data={"next": f"/gm/npcs/{again.id}"})
    assert gone.headers["location"].startswith("/gm/worlds/")
    other = registry.npcs.create("Hob Wade", f"w:{world}", world)
    away = await ac.post(f"/gm/npcs/{other.id}/delete", data={"next": "//evil.example/"})
    assert away.headers["location"].startswith("/gm/worlds/")


@requires_corpus
async def test_duplicate_and_move_up(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    adventure = registry.adventures.create(world, "The Drowned Bell")
    chapter = registry.adventures.create_chapter(adventure.id, "The Ferry")
    ilse = registry.npcs.create("Mother Ilse", f"c:{chapter.id}", world)
    menu = html.unescape((await ac.get(_menu_of(f"/gm/npcs/{ilse.id}"))).text)
    assert "Move up to the adventure" in menu
    made = await ac.post("/menu/duplicate", data={"kind": "npc", "id": ilse.id})
    copy_id = made.headers["location"].rsplit("/", 1)[1]
    copy = registry.gamestate.npcs.get(copy_id)
    assert copy.name == "Mother Ilse (copy)" and copy.scope.chapter_id == chapter.id
    await ac.post("/menu/move", data={"kind": "npc", "id": ilse.id, "next": "/gm"})
    moved = registry.gamestate.npcs.get(ilse.id).scope
    assert moved.chapter_id is None and moved.adventure_id == adventure.id
    await ac.post("/menu/move", data={"kind": "npc", "id": ilse.id})
    assert registry.gamestate.npcs.get(ilse.id).scope == Scope(world_id=world)


@requires_corpus
async def test_chapters_ask_in_a_dialog_and_make_new_things_here(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    adventure = registry.adventures.create(world, "The Drowned Bell")
    chapter = registry.adventures.create_chapter(adventure.id, "The Ferry")
    menu = html.unescape((await ac.get(_menu_of(f"/gm/chapters/{chapter.id}"))).text)
    assert f'data-menu-dialog="/gm/chapters/{chapter.id}/delete"' in menu
    assert f"tool=npc&amp;place=c:{chapter.id}" in menu or f"tool=npc&place=c:{chapter.id}" in menu
    here = f"/gm/adventures/{adventure.id}"
    asked = (await ac.get(f"/gm/chapters/{chapter.id}/delete?dialog=1&next={quote(here)}")).text
    assert asked.lstrip().startswith('<div class="panel panel-danger"')
    assert f'name="next" value="{here}"' in asked and "data-close" in asked
    dialog = (await ac.get(f"/gm/npcs?tool=npc&place=c:{chapter.id}")).text
    assert f'name="place" value="c:{chapter.id}"' in dialog
    forged = (await ac.get("/gm/npcs?tool=npc&place=../etc")).text
    assert f'name="place" value="w:{world}"' in forged
    done = await ac.post(f"/gm/chapters/{chapter.id}/delete", data={"next": here})
    assert done.headers["location"].startswith(here) and "undo=" in done.headers["location"]


@requires_corpus
async def test_a_missing_link_offers_where_to_make_it(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    adventure = registry.adventures.create(world, "The Drowned Bell")
    chapter = registry.adventures.create_chapter(adventure.id, "The Ferry")
    registry.npcs.create("Captain Rhosk", f"w:{world}", world)
    href = f"/gm/editor/new?kind=npc&name=Captain+Rhos&scope=c:{chapter.id}&back=/gm"
    menu = html.unescape((await ac.get(_menu_of(href))).text)
    for words in ("In this chapter", "In the adventure", "In the world", "Captain Rhosk"):
        assert words in menu
    assert f'value="a:{adventure.id}"' in menu


@requires_corpus
async def test_party_and_sheet_menus_follow_the_role(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    party = html.unescape((await ac.get(f"/menu/party/{lyra}")).text)
    assert "Grant a level" in party and "Downtime" in party
    assert 'data-menu="/menu/party/' in (await ac.get("/gm")).text
    sheet = (await ac.get(f"/characters/{lyra}")).text
    assert f'data-menu="/menu/item/{lyra}/' in sheet and f'data-menu="/menu/spell/{lyra}' in sheet
    item = html.unescape((await ac.get(f"/menu/item/{lyra}/0")).text)
    assert 'hx-target="#sheet"' in item
    spell = html.unescape((await ac.get(f"/menu/spell/{lyra}?ref=Magic Missile|PHB")).text)
    assert "Cast at level 1" in spell and "Open the full entry" in spell
    # A player reads the menus of their own character's sheet only, never the table's.
    registry.auth.create_user("mara", PASSWORD, Role.PLAYER)
    await ac.post("/logout")
    await ac.post("/login", data={"username": "mara", "password": PASSWORD})
    assert (await ac.get(f"/menu/item/{lyra}/0")).status_code in (403, 404)
    assert (await ac.get(f"/menu/party/{lyra}")).status_code == 403
    assert (await ac.get(_menu_of("/gm/npcs/x"))).status_code == 403


@pytest.mark.parametrize("href", ["/elsewhere", "/gm/npcs/nope", "/gm/editor/new?kind=x"])
@requires_corpus
async def test_unknown_menus_are_not_found(
    table: tuple[AsyncClient, StoreRegistry, str], href: str
) -> None:
    ac, _registry, _lyra = table
    assert (await ac.get(_menu_of(href))).status_code == 404
