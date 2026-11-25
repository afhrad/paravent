"""A character's story page (app/services/story.py)."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.models.gamestate import Contact
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import player_of, table

__all__ = ["table"]


@requires_corpus
async def test_the_story_of_a_character(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    pc = registry.gamestate.characters.get(lyra)
    world = pc.world_id or ""
    guild = registry.factions.create(world, "Lantern Guild")
    registry.factions.adjust(guild.id, world, pc, 2, "dm", "DM")
    registry.gamestate.contacts.create(
        Contact(character_id=lyra, world_id=world, kind="ally", name="Mara")
    )
    registry.quests.save(world, {"name": "Find the bell", "shown": True})
    registry.quests.save(world, {"name": "Secret errand", "shown": False})
    registry.characters.award_xp(lyra, 300, "The bell", "DM")
    page = html.unescape((await ac.get(f"/characters/{lyra}/story")).text)
    assert "Lyra Mooncaller's story" in page
    assert "Find the bell" in page and "Secret errand" in page  # the gamemaster sees all
    assert "Lantern Guild" in page and "Renown 2" in page and "Mara" in page
    assert "Renown with Lantern Guild: +2 (now 2)" in page
    assert "300" in page and "The bell" in page
    assert f"/characters/{lyra}/story" in (await ac.get(f"/characters/{lyra}")).text
    story = registry.story.of(registry.gamestate.characters.get(lyra), gamemaster=False)
    assert [q.name for q in story.quests] == ["Find the bell"]  # a player: shown ones only


@requires_corpus
async def test_marks_of_prestige(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    url = f"/characters/{lyra}/story"
    page = html.unescape((await ac.get(url)).text)
    assert "Marks of prestige" in page and "Grant a mark of prestige" in page
    # The gamemaster grants a medal and a favor.
    await ac.post(
        url,
        data={
            "op": "grant",
            "kind": "medal",
            "name": "Golden Bear",
            "granted_by": "the Queen",
            "note": "Heroes within the kingdom",
        },
    )
    await ac.post(url, data={"op": "grant", "kind": "favor", "name": "A debt of the guild"})
    medal, favor = registry.prestige.of(lyra)
    assert (medal.kind, medal.name, medal.granted_by) == ("medal", "Golden Bear", "the Queen")
    bad = await ac.post(url, data={"op": "grant", "kind": "crown", "name": "x"})
    assert bad.status_code == 422
    page = html.unescape((await ac.get(url)).text)
    assert "Golden Bear" in page and "from the Queen" in page and "Call in" in page
    assert "Granted a mark of prestige: Golden Bear (Medal)" in page
    # The player sees its marks, calls in the favor, and may not grant or take back.
    async with player_of(registry, lyra) as player:
        mine = html.unescape((await player.get(url)).text)
        assert "Golden Bear" in mine and "Grant a mark of prestige" not in mine
        assert "Take back" not in mine
        await player.post(url, data={"op": "call_in", "mark": favor.id})
        refused = await player.post(url, data={"op": "take_back", "mark": medal.id})
        assert refused.status_code == 403
    assert registry.prestige.get(lyra, favor.id).spent
    again = await ac.post(url, data={"op": "call_in", "mark": favor.id})
    assert again.status_code == 422
    # Land is taken back.
    await ac.post(url, data={"op": "take_back", "mark": medal.id})
    assert [m.name for m in registry.prestige.of(lyra)] == ["A debt of the guild"]
    log = html.unescape((await ac.get(f"/log/panel?world={world}")).text)
    assert "Called in a favor: A debt of the guild" in log
    assert "Lost a mark of prestige: Golden Bear (Medal)" in log


@requires_corpus
async def test_a_quest_rewards_a_mark(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    registry.table.select(world)
    session = registry.play_sessions.start(world, "dm", "DM")
    await ac.post(
        f"/gm/quests/{world}/save",
        data={
            "name": "Save the town",
            "giver": "Lord Neverember",
            "shown": "1",
            "mark_kind": "title",
            "mark_name": "Warden of the March",
        },
    )
    (quest,) = registry.quests.of(world)
    assert (quest.mark_kind, quest.mark_name) == ("title", "Warden of the March")
    page = html.unescape((await ac.get(f"/gm/quests?world={world}")).text)
    assert "Title: Warden of the March" in page
    await ac.post(f"/gm/quests/{world}/finish", data={"quest": quest.id})
    marks = registry.prestige.of(lyra)
    assert [(m.kind, m.name, m.granted_by) for m in marks] == [
        ("title", "Warden of the March", "Lord Neverember")
    ]
    happened = registry.play_sessions.happened(world, session.started_at, None)
    assert happened.marks == [("Lyra Mooncaller", "title", "Warden of the March")]
