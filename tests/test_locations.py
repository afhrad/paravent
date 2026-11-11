"""Places, ``{@place Name}`` (documentation/ADVENTURES.md §4): the record, its pages, the
card in a chapter, the editor's create-in-place, and Claude writing them; and an
encounter card's folded notes."""

from __future__ import annotations

import html
from typing import Any

import httpx

from app.models.gamestate import Scope
from app.services.adventures import text_version
from app.services.locations import named_in
from tests.test_mcp import PASSWORD, call, session, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


async def _login(raw: httpx.AsyncClient) -> None:
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})


def test_what_a_text_names() -> None:
    text = "{@npc Hob}, {@npc Hob|the knight}, {@encounter Ambush} and {@treasure Chest}"
    assert named_in(text) == [("npc", "Hob"), ("encounter", "Ambush"), ("treasure", "Chest")]


class TestService:
    def test_nearest_first_and_a_rename_follows(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure = registry.adventures.create(world_id, "Fog on the River")
        chapter = registry.adventures.create_chapter(adventure.id, "Arrival")
        registry.adventures.update_chapter(
            chapter.id, title="Arrival", status="planned", body="We reach {@place Grey Reed}."
        )
        world_town = registry.locations.create("Grey Reed", f"w:{world_id}", world_id)
        own_town = registry.locations.create("Grey Reed", f"a:{adventure.id}", world_id)
        here = Scope(world_id=world_id, adventure_id=adventure.id, chapter_id=chapter.id)
        assert registry.locations.find("grey reed", here).id == own_town.id
        assert registry.locations.find("Grey Reed", Scope(world_id=world_id)).id == world_town.id
        registry.locations.update(
            own_town.id,
            name="Grey Reed Ruins",
            scope_value=f"a:{adventure.id}",
            tags=["ruin, Village"],
            notes="",
        )
        saved = registry.locations.get(own_town.id)
        assert saved.tags == ["ruin", "village"]
        assert "{@place Grey Reed Ruins}" in registry.adventures.get_chapter(chapter.id).body


class TestPages:
    async def test_create_edit_show_delete(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        await _login(raw)
        made = await raw.post(f"/gm/worlds/{world_id}/places", data={"name": "Grey Reed"})
        assert made.status_code == 303
        (town,) = registry.locations.list(world_id)
        notes = "A frontier town. {@npc Hob Wade} waits here; {@encounter Greycloaks}."
        saved = await raw.post(
            f"/gm/places/{town.id}/edit",
            data={
                "name": "Grey Reed",
                "place": f"w:{world_id}",
                "tags": "town",
                "notes": notes,
                "base": text_version(""),
            },
        )
        assert saved.status_code == 303
        page = html.unescape((await raw.get(f"/gm/places/{town.id}")).text)
        assert "A frontier town." in page and "{@place Grey Reed}" in page
        # Here: the NPC its notes name links; the encounter nobody made is grey.
        assert 'class="ref lib npc"' in page and 'class="ref missing">Greycloaks' in page
        stale = await raw.post(
            f"/gm/places/{town.id}/edit",
            data={"name": "Grey Reed", "notes": "x", "base": text_version("")},
        )
        assert stale.status_code == 409
        world_page = (await raw.get(f"/gm/worlds/{world_id}")).text
        assert "1 place" in world_page
        assert (await raw.get(f"/gm/worlds/{world_id}/places")).status_code == 200
        gone = await raw.post(f"/gm/places/{town.id}/delete")
        assert gone.status_code == 303 and registry.locations.list(world_id) == []

    async def test_created_from_the_editor(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        await _login(raw)
        made = await raw.post(
            "/gm/editor/create",
            data={"kind": "place", "name": "Marlow", "scope": f"w:{world_id}"},
        )
        assert made.status_code == 201 and made.headers["location"].startswith("/gm/places/")
        assert [x.name for x in registry.locations.list(world_id)] == ["Marlow"]


class TestInAChapter:
    async def test_link_card_and_folded_notes(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure = registry.adventures.create(world_id, "Fog on the River")
        town = registry.locations.create("Grey Reed", f"a:{adventure.id}", world_id)
        # Its notes name the place itself alone on a line: a link there, never a card again.
        registry.locations.update(
            town.id,
            name="Grey Reed",
            scope_value=f"a:{adventure.id}",
            tags=["town"],
            notes="**Muddy** streets.\n\n{@place Grey Reed}\n",
        )
        here = Scope(world_id=world_id, adventure_id=adventure.id)
        fight = registry.encounters.create("Greycloaks", world_id, here)
        registry.encounters.add_monster(fight.id, "Bandit|MM", 3)
        registry.encounters.update(
            fight.id,
            name="Greycloaks",
            scope=here,
            notes="They **flee** at two.",
            rules=None,
            party=None,
        )
        chapter = registry.adventures.create_chapter(adventure.id, "Arrival")
        body = "We reach {@place Grey Reed}.\n\n{@place Grey Reed}\n\n{@encounter Greycloaks}\n"
        registry.adventures.update_chapter(chapter.id, title="Arrival", status="planned", body=body)
        await _login(raw)
        page = (await raw.get(f"/gm/chapters/{chapter.id}")).text
        assert 'class="ref lib place"' in page  # inline: a link
        assert 'class="npc-embed place-embed"' in page and "<strong>Muddy</strong>" in page
        assert page.count("place-embed") == 1  # the notes did not unfold a second card
        assert 'class="md-statblock enc-embed-notes"' in page and "<strong>flee</strong>" in page
        # The adventure's panel lists the place it holds, with the line to paste.
        overview = (await raw.get(f"/gm/adventures/{adventure.id}")).text
        holdings = overview[overview.index("holdings-grid") :]
        assert "{@place Grey Reed}" in html.unescape(holdings)


class TestClaude:
    async def test_writes_places_into_a_draft(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        world_id = facts["world"].id
        _lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Fen",
                summary="",
                character_ids=[brunhild],
            )
            made = await call(
                mcp,
                "create_place",
                adventure_id=draft["id"],
                name="Kranwitt",
                notes="A village at the fen's edge.",
                tags=["village"],
            )
            assert made["tags"] == ["village"]
            changed = await call(
                mcp, "update_place", place_id=made["id"], base=made["version"], notes="Sunk."
            )
            assert changed["notes"] == "Sunk."
            whole = await call(mcp, "read_adventure", adventure_id=draft["id"])
            assert [p["name"] for p in whole["places"]] == ["Kranwitt"]
            overview = await call(mcp, "world_overview", world_id=world_id)
            assert any(p["name"] == "Kranwitt" for p in overview["places"])
            assert "Deleted the place" in await call(mcp, "delete", kind="place", id=made["id"])
