"""Context menus (documentation/CONTEXT-MENUS.md): what each offers to whom, and the
changes to creating and deleting they bring (back where asked, with Undo; a confirmation
as a dialog; a copy; moved up; New … here)."""

from __future__ import annotations

import html
import io
from datetime import date
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from app.models.gamestate import Role, Scope
from app.models.pc import PlayerCharacter
from app.services.copies import copy_name
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_sound import _wav
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


def _second_character(registry: StoreRegistry, lyra: str, name: str = "Brunhild") -> str:
    """Another character of Lyra's party, to hand things to."""
    data = registry.gamestate.characters.get(lyra).model_dump(mode="json", exclude={"id"})
    data |= {"name": name, "inventory": []}
    return registry.gamestate.characters.create(PlayerCharacter.model_validate(data)).id


def _index_of(registry: StoreRegistry, character: str, reference: str) -> int:
    pc = registry.gamestate.characters.get(character)
    return next(n for n, i in enumerate(pc.inventory) if i.item.reference == reference)


@requires_corpus
async def test_an_item_is_given_or_dropped(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    brunhild = _second_character(registry, lyra)
    daggers = _index_of(registry, lyra, "Dagger|PHB")
    menu = html.unescape((await ac.get(f"/menu/item/{lyra}/{daggers}")).text)
    assert f"/characters/{lyra}/hand?index={daggers}" in menu and "Give to" in menu
    asked = (await ac.get(f"/characters/{lyra}/hand?index={daggers}&dialog=1&next=/gm")).text
    assert asked.lstrip().startswith('<div class="panel"') and "Brunhild" in asked
    assert 'max="2"' in asked and 'name="next" value="/gm"' in asked
    given = await ac.post(
        f"/characters/{lyra}/hand",
        data={"index": daggers, "ref": "Dagger|PHB", "count": 1, "to": brunhild, "next": "/gm"},
    )
    assert given.headers["location"] == "/gm"
    mine = registry.gamestate.characters.get(lyra).inventory
    assert next(i for i in mine if i.item.reference == "Dagger|PHB").quantity == 1
    theirs = registry.gamestate.characters.get(brunhild).inventory
    assert [(i.item.reference, i.quantity) for i in theirs] == [("Dagger|PHB", 1)]
    # Given again, it joins the stack; an item drawn for another index is refused.
    daggers = _index_of(registry, lyra, "Dagger|PHB")
    wrong = await ac.post(
        f"/characters/{lyra}/hand",
        data={"index": daggers, "ref": "Quarterstaff|PHB", "to": brunhild},
    )
    assert "flash=" in wrong.headers["location"]
    await ac.post(
        f"/characters/{lyra}/hand", data={"index": daggers, "ref": "Dagger|PHB", "to": brunhild}
    )
    theirs = registry.gamestate.characters.get(brunhild).inventory
    assert [(i.item.reference, i.quantity) for i in theirs] == [("Dagger|PHB", 2)]
    # Dropped, it is gone; the log says both.
    staff = _index_of(registry, lyra, "Quarterstaff|PHB")
    await ac.post(f"/characters/{lyra}/hand", data={"index": staff, "ref": "Quarterstaff|PHB"})
    refs = [i.item.reference for i in registry.gamestate.characters.get(lyra).inventory]
    assert "Quarterstaff|PHB" not in refs and "Dagger|PHB" not in refs
    log = (await ac.get("/log")).text
    assert "Gives Dagger" in log and "Brunhild" in log and "Drops Quarterstaff" in log
    # Nobody else in the party: Drop only, and giving to a stranger is refused.
    registry.gamestate.characters.delete(brunhild)
    ring = _index_of(registry, lyra, "Ring of Protection|DMG")
    assert "Give to" not in html.unescape((await ac.get(f"/menu/item/{lyra}/{ring}")).text)
    stranger = await ac.post(
        f"/characters/{lyra}/hand",
        data={"index": ring, "ref": "Ring of Protection|DMG", "to": "nobody"},
    )
    assert "flash=" in stranger.headers["location"]


@requires_corpus
async def test_the_other_lists_ask_in_a_dialog(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    listing = (await ac.get("/gm/characters")).text
    assert f'data-menu="/menu/character/{lyra}"' in listing
    menu = html.unescape((await ac.get(f"/menu/character/{lyra}")).text)
    assert f'data-menu-dialog="/characters/{lyra}/delete"' in menu and "for good" in menu
    asked = (await ac.get(f"/characters/{lyra}/delete?dialog=1&next=/gm/characters")).text
    assert asked.lstrip().startswith('<div class="panel panel-danger"')
    note = registry.session_notes.create(world, date(2026, 11, 20))
    sessions = (await ac.get(f"/gm/worlds/{world}/sessions")).text
    assert "data-menu-open" in sessions and f"/gm/sessions/{note.id}/delete" in sessions
    menu = html.unescape((await ac.get(_menu_of(f"/gm/sessions/{note.id}"))).text)
    assert (
        "Send to the players" in menu
        and f'data-menu-dialog="/gm/sessions/{note.id}/delete"' in menu
    )
    for href in ("/gm/sessions/nope", "/gm/creatures/nope", "/gm/names/nope"):
        assert (await ac.get(_menu_of(href))).status_code == 404
    assert (await ac.get("/menu/sound/nope")).status_code == 404
    rain = registry.sounds.add("rain.wav", io.BytesIO(_wav()), "ambience", name="Rain")
    assert f'data-menu="/menu/sound/{rain.id}"' in (await ac.get("/gm/sounds")).text
    menu = html.unescape((await ac.get(f"/menu/sound/{rain.id}")).text)
    assert f"/gm/sound/press/{rain.id}" in menu and f"/gm/sounds/{rain.id}/delete" in menu


@requires_corpus
async def test_a_log_entry_narrows_the_log(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    await ac.post(f"/characters/{lyra}/act/roll", data={"kind": "save", "subject": "dex"})
    log = (await ac.get("/log")).text
    entry = log.split('data-menu="/menu/log/', 1)[1].split('"', 1)[0].split("?")[0]
    menu = html.unescape((await ac.get(f"/menu/log/{entry}")).text)
    assert f"filter=character&character={lyra}" in menu and "Only Lyra" in menu
    assert 'data-log-query="filter=all"' in menu
    assert (await ac.get("/menu/log/nope")).status_code == 404
