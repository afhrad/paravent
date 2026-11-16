"""A day of play: the briefing, its start and the note it ends with
(app/services/play_session.py)."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.models.gamestate import Contact, Downtime
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.clock import DAY
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


def _log(registry: StoreRegistry, world: str, what: str, subject: str = "", name: str = "") -> None:
    registry.protocol.append(
        [
            ProtocolEntry(
                actor="dm",
                actor_name="DM",
                subject=subject,
                subject_name=name,
                kind=EntryKind.CUSTOM,
                what=what,
                scope=Scope(world=world),
            )
        ]
    )


@requires_corpus
async def test_a_session_from_brief_to_note(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    store = registry.gamestate
    store.contacts.create(Contact(character_id=lyra, world_id=world, kind="ally"))
    store.downtime.upsert(Downtime(id=lyra, days=5))
    _log(registry, world, "lifestyle:modest:1:0:100", f"character:{lyra}", "Lyra Mooncaller")
    assert "Session" in (await ac.get(f"/gm?world={world}")).text
    brief = html.unescape((await ac.get(f"/gm/session?world={world}")).text)
    assert "Start the session" in brief
    assert "Lyra Mooncaller owes 1 gp for their lifestyle" in brief
    assert "Lyra Mooncaller: 1 contact without a name" in brief
    assert "Lyra Mooncaller: 5 days of downtime to spend" in brief
    assert "No session note yet." in brief
    await ac.post(f"/gm/session/{world}/start")
    running = registry.play_sessions.of(world)
    assert running is not None and running.minute == registry.clock.of(world).minute
    registry.clock.advance(world, DAY, "dm", "DM")
    _log(registry, world, "trap:sprung:Collapsing Roof")
    _log(registry, world, "chase:end:escaped")
    _log(registry, world, "contact:new:ally:2")
    _log(registry, world, "travel:normal:8:24:forest:::")
    page = html.unescape((await ac.get(f"/gm/session?world={world}")).text)
    assert "Since the session began" in page and "Sprung: Collapsing Roof" in page
    assert "Travelled 24 mi." in page and "New contacts: 2 × Ally" in page
    ended = await ac.post(f"/gm/session/{world}/end")
    assert ended.status_code == 303 and "/edit" in ended.headers["location"]
    assert registry.play_sessions.of(world) is None
    (note,) = registry.session_notes.list(world)
    assert "## In the world" in note.text and "A chase: Escaped" in note.text
    assert "From Day 1 · 08:00 to Day 2 · 08:00" in note.text
    again = html.unescape((await ac.get(f"/gm/session?world={world}")).text)
    assert "Last time" in again and "Sprung: Collapsing Roof" in again
    # The debt was before this session ended: no longer listed.
    assert "owes" not in again
    assert "The session begins" in (await ac.get("/log")).text
