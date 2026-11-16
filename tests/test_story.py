"""A character's story page (app/services/story.py)."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.models.gamestate import Contact
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

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
