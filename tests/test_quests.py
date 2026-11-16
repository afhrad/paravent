"""A world's quests and their rewards (app/services/quests.py), and milestone levelling."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.models.gamestate import World
from app.services.quests import as_coins, split_coins
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


def test_sharing_coins() -> None:
    assert split_coins(1001, 3) == [335, 333, 333] and split_coins(50, 0) == []
    assert as_coins(1234) == {"gp": 12, "sp": 3, "cp": 4}


@requires_corpus
async def test_a_quest_from_start_to_reward(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    registry.gamestate.characters.update(lyra, experience=64000)
    pc = registry.gamestate.characters.get(lyra)
    world = pc.world_id or ""
    xp_before, gold_before = 64000, pc.currency.gp
    guild = registry.factions.create(world, "Lantern Guild")
    saved = await ac.post(
        f"/gm/quests/{world}/save",
        data={
            "name": "The Drowned Bell",
            "giver": "Mother Ilse",
            "faction_id": guild.id,
            "text": "Raise the bell from the mere.",
            "steps": "Find the bell\n[x] Hire a boat",
            "xp": "300",
            "gold": "50",
            "renown": "2",
            "items": "2 Potion of Healing",
            "shown": "1",
        },
    )
    assert saved.status_code == 303
    (quest,) = registry.quests.of(world)
    assert [s.done for s in quest.steps] == [False, True]
    assert quest.items[0].ref.startswith("Potion of Healing|") and quest.items[0].count == 2
    page = html.unescape((await ac.get(f"/gm/quests?world={world}")).text)
    assert "300 XP each · 50 gp · Potion of Healing ×2 · renown +2" in page
    journal = html.unescape((await ac.get("/journal")).text)
    assert "The Drowned Bell" in journal and "Given by Mother Ilse" in journal
    await ac.post(f"/gm/quests/{world}/step", data={"quest": quest.id, "index": "0"})
    assert all(s.done for s in registry.quests.get(quest.id).steps)
    await ac.post(f"/gm/quests/{world}/finish", data={"quest": quest.id})
    pc = registry.gamestate.characters.get(lyra)
    assert registry.quests.get(quest.id).status == "done"
    assert (pc.experience or 0) == xp_before + 300 and pc.currency.gp == gold_before + 50
    assert any(i.item.reference.startswith("Potion of Healing") for i in pc.inventory)
    assert registry.factions.get(guild.id).renown[lyra] == 2
    log = html.unescape((await ac.get("/log")).text)
    assert "Quest done: The Drowned Bell" in log
    again = await ac.post(f"/gm/quests/{world}/finish", data={"quest": quest.id})
    assert again.status_code == 422  # handed out once
    bad = await ac.post(
        f"/gm/quests/{world}/save", data={"name": "Odd", "items": "Bell of Nowhere"}
    )
    assert bad.status_code == 422 and "Bell of Nowhere" in html.unescape(bad.text)


@requires_corpus
async def test_milestone_worlds_award_no_xp(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    pc = registry.gamestate.characters.get(lyra)
    world: World = registry.gamestate.worlds.get(pc.world_id or "")
    registry.gamestate.worlds.update(world.id, milestone=True)
    before = pc.experience or 0
    await ac.post(f"/gm/quests/{world.id}/save", data={"name": "Quiet", "xp": "500", "shown": "1"})
    (quest,) = registry.quests.of(world.id)
    page = html.unescape((await ac.get(f"/gm/quests?world={world.id}")).text)
    assert "XP for each" not in page
    await ac.post(f"/gm/quests/{world.id}/finish", data={"quest": quest.id})
    assert (registry.gamestate.characters.get(lyra).experience or 0) == before
    form = (await ac.get(f"/gm/worlds/{world.id}/edit")).text
    assert "Level by milestones" in form
