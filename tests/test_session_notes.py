"""Session notes (documentation/ADVENTURES.md §4b): written per day of play, started
from that day's recap, read by Claude through the campaign memory."""

from __future__ import annotations

import html
from datetime import date
from typing import Any

import httpx

from app.models.gamestate import Npc, Scope
from app.services.adventures import text_version
from tests.test_mcp import PASSWORD, call, session, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


async def _login(raw: httpx.AsyncClient) -> None:
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})


class TestNotes:
    async def test_a_day_starts_from_its_recap(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        _lyra, brunhild = facts["ids"]
        encounter = registry.encounters.create("Goblin ambush", world_id)
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 4)
        fight = registry.combats.start(encounter, by="dm", characters=[brunhild])
        registry.combats.end(fight.id, by="dm")
        await _login(raw)
        made = await raw.post(
            f"/gm/worlds/{world_id}/sessions", data={"day": date.today().isoformat()}
        )
        assert made.status_code == 303
        (note,) = registry.session_notes.list(world_id)
        assert "Goblin ambush" in note.text  # the recap of the day's fight
        form = (await raw.get(made.headers["location"])).text
        assert 'class="prose-editor"' in form and "Goblin ambush" in html.unescape(form)

    async def test_write_read_conflict_delete(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure = registry.adventures.create(world_id, "Fog on the River")
        registry.gamestate.npcs.create(
            Npc(scope=Scope(world_id=world_id, adventure_id=adventure.id), name="Gnasher")
        )
        await _login(raw)
        await raw.post(
            f"/gm/worlds/{world_id}/sessions",
            data={"day": "2026-10-25", "adventure": adventure.id},
        )
        (note,) = registry.session_notes.list(world_id)
        assert note.text == "" and note.adventure_id == adventure.id
        text = "## What happened\n\nThe party spared {@npc Gnasher}.\n"
        saved = await raw.post(
            f"/gm/sessions/{note.id}",
            data={
                "day": "2026-10-25",
                "adventure": adventure.id,
                "text": text,
                "base": text_version(""),
            },
        )
        assert saved.status_code == 303
        listing = (await raw.get(f"/gm/worlds/{world_id}/sessions")).text
        # Rendered in the adventure's context: its own NPC resolves.
        assert "Gnasher</a>" in listing and "Fog on the River" in listing
        assert (
            "25.10.2026"
            in (
                await raw.get(f"/gm/worlds/{world_id}/sessions", headers={"Accept-Language": "de"})
            ).text
            or "2026-10-25" in listing
        )
        stale = await raw.post(
            f"/gm/sessions/{note.id}",
            data={"day": "2026-10-25", "text": "other", "base": text_version("")},
        )
        assert stale.status_code == 409 and "spared" in html.unescape(stale.text)
        assert (
            await raw.post(f"/gm/sessions/{note.id}", data={"day": "soon", "text": "x"})
        ).status_code == 422
        world_page = (await raw.get(f"/gm/worlds/{world_id}")).text
        assert "1 session note" in world_page
        assert (await raw.get(f"/gm/sessions/{note.id}/delete")).status_code == 200
        gone = await raw.post(f"/gm/sessions/{note.id}/delete")
        assert gone.status_code == 303 and registry.session_notes.list(world_id) == []

    async def test_claude_reads_them(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        registry.session_notes.create(
            world_id, date(2026, 10, 25), text="The hag escaped into the fen."
        )
        async with session(table) as mcp:
            history = await call(mcp, "campaign_history", world_id=world_id)
        (day,) = history["sessions"]
        assert day["date"] == "2026-10-25"
        assert day["notes"] == [{"adventure": None, "text": "The hag escaped into the fen."}]
