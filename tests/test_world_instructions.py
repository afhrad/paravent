"""Instructions for Claude per world, and reskins (documentation/MCP.md §5): the page,
what Claude receives, and a book creature shown under the setting's name."""

from __future__ import annotations

import html
from typing import Any

import httpx

from app.models.gamestate import Scope
from tests.test_mcp import PASSWORD, call, session, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]
SETTING = "This world is the Sunken Marches. Mire Lurkers are Kuo-toa."


async def _login(raw: httpx.AsyncClient) -> None:
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})


class TestInstructions:
    async def test_written_on_the_page_read_by_claude(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        await _login(raw)
        assert (
            f'href="/gm/worlds/{world_id}/claude"' in (await raw.get(f"/gm/worlds/{world_id}")).text
        )
        form = (await raw.get(f"/gm/worlds/{world_id}/claude")).text
        assert 'name="setting"' in form and 'name="encounter"' in form
        saved = await raw.post(
            f"/gm/worlds/{world_id}/claude",
            data={"setting": SETTING, "npc": "  Every NPC has a faction.  ", "chapter": ""},
        )
        assert saved.status_code == 303
        kept = registry.instructions.get(world_id)
        assert kept.setting == SETTING and kept.npc == "Every NPC has a faction."
        assert SETTING in html.unescape((await raw.get(f"/gm/worlds/{world_id}/claude")).text)
        async with session(table) as mcp:
            overview = await call(mcp, "world_overview", world_id=world_id)
        # Only what says something reaches Claude.
        assert overview["instructions"] == {
            "setting": SETTING,
            "npc": "Every NPC has a faction.",
        }


class TestReskins:
    async def test_shown_as_rated_as_the_book(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        here = Scope(world_id=world_id)
        encounter = registry.encounters.create("Mire Lurker camp", world_id, here)
        registry.encounters.add_monster(encounter.id, "Kuo-toa|MM", 3)
        before = registry.encounters.view(registry.encounters.get(encounter.id)).rating
        await _login(raw)
        await raw.post(
            f"/gm/encounters/{encounter.id}/monsters/0/label", data={"label": "  Mire Lurker "}
        )
        planned = registry.encounters.get(encounter.id).monsters[0]
        assert planned.label == "Mire Lurker" and planned.name == "Kuo-toa"
        after = registry.encounters.view(registry.encounters.get(encounter.id)).rating
        assert after.adjusted_xp == before.adjusted_xp  # the numbers stay the book's
        builder = (await raw.get(f"/gm/encounters/{encounter.id}")).text
        assert 'value="Mire Lurker"' in builder and "Kuo-toa" in builder
        fight = registry.combats.start(registry.encounters.get(encounter.id), by="dm")
        labels = sorted(c.label for c in fight.combatants if c.ref)
        assert labels == ["Mire Lurker 1", "Mire Lurker 2", "Mire Lurker 3"]
        assert all(c.ref == "Kuo-toa|MM" for c in fight.combatants if c.ref)
        # Emptying it shows the book's name again.
        await raw.post(f"/gm/encounters/{encounter.id}/monsters/0/label", data={"label": ""})
        assert registry.encounters.get(encounter.id).monsters[0].label is None

    async def test_claude_reskins_in_a_draft(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        world_id = facts["world"].id
        _lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Coast",
                summary="",
                character_ids=[brunhild],
            )
            fight = await call(
                mcp,
                "create_encounter",
                adventure_id=draft["id"],
                name="Mire Lurkers",
                monsters=[{"name": "Kuo-toa", "count": 4, "label": "Mire Lurker"}],
            )
            chapter = await call(
                mcp,
                "write_chapter",
                adventure_id=draft["id"],
                title="Shore",
                body="{@encounter Mire Lurkers}\n",
            )
        (line,) = fight["rating"]["monsters"]
        assert line["label"] == "Mire Lurker" and line["name"] == "Kuo-toa"
        await _login(raw)
        page = (await raw.get(f"/gm/chapters/{chapter['id']}")).text
        assert "4× Mire Lurker" in page
