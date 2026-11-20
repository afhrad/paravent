"""A world's factions and the characters' renown (app/services/factions.py)."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.models.gamestate import Faction, FactionRank
from app.services.factions import granted, ranks_from, rewards_of, title_of
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


def test_reading_the_tables() -> None:
    rewards = rewards_of([["3+", "Friendly {@b citizens}"], ["10", "A house"], ["—", "x"]])
    assert [(r.renown, r.text) for r in rewards] == [(3, "Friendly citizens"), (10, "A house")]
    assert [r.renown for r in granted(rewards, 5)] == [3]
    ranks = ranks_from(["Renown", "Reed Wardens", "Lantern Court"], [["1", "Ferryhand", "Wick"]])
    assert ranks["Lantern Court"][0].title == "Wick"
    faction = Faction(
        world_id="w",
        name="Reed Wardens",
        ranks=[FactionRank(renown=1, title="Ferryhand"), FactionRank(renown=3, title="Bellkeeper")],
    )
    assert title_of(faction, 0) == "" and title_of(faction, 4) == "Bellkeeper"


@requires_corpus
async def test_renown_from_the_books(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    pc = registry.gamestate.characters.get(lyra)
    world = registry.gamestate.worlds.get(pc.world_id or "")
    registry.gamestate.worlds.update(
        world.id,
        source_set=world.source_set.model_copy(
            update={"sources": [*world.source_set.sources, "DMG", "FRAiF"]}
        ),
    )
    page = html.unescape((await ac.get(f"/gm/factions?world={world.id}")).text)
    assert "No factions yet." in page and "Dale (FRAiF)" in page and "Harpers" in page
    await ac.post(f"/gm/factions/{world.id}/from_table", data={"table": "Dale Renown|FRAiF"})
    await ac.post(f"/gm/factions/{world.id}/from_example", data={"example": "Harpers"})
    dale, harpers = registry.factions.of(world.id)
    assert dale.table == "Dale Renown|FRAiF" and harpers.ranks[0].title == "Watcher"
    for _ in range(3):
        await ac.post(
            f"/gm/factions/{world.id}/renown",
            data={"faction": dale.id, "character": lyra, "delta": "1"},
        )
    await ac.post(
        f"/gm/factions/{world.id}/renown",
        data={"faction": harpers.id, "character": lyra, "delta": "1"},
    )
    assert registry.factions.get(dale.id).renown[lyra] == 3
    sheet = html.unescape((await ac.get(f"/characters/{lyra}/downtime")).text)
    assert "Renown 3" in sheet and "All citizens of the dale are Friendly" in sheet
    assert "Watcher" in sheet
    await ac.post(
        f"/characters/{lyra}/downtime",
        data={"op": "contact_add", "kind": "ally", "name": "Mara", "faction": harpers.id},
    )
    (contact,) = registry.contacts.of(lyra)
    assert contact.faction_id == harpers.id
    assert "Renown with Dale: +1 (now 3)" in html.unescape((await ac.get("/log")).text)
    bad = await ac.post(f"/gm/factions/{world.id}/create", data={"name": " "})
    assert bad.status_code == 422
    await ac.post(f"/gm/factions/{world.id}/delete", data={"faction": harpers.id})
    assert registry.contacts.of(lyra)[0].faction_id is None
