"""Hirelings, companions, mounts and sidekicks (app/services/followers.py)."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.services.clock import DAY
from app.services.followers import hit_die, level_hp
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import player_of, table

__all__ = ["table"]


def test_growth() -> None:
    assert hit_die("5d10 + 10") == 10 and hit_die("") == 8
    assert level_hp(10, 1) == 7 and level_hp(4, -3) == 1


@requires_corpus
async def test_followers_at_the_table(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    url = f"/characters/{lyra}/followers"
    page = html.unescape((await ac.get(url)).text)
    assert "Skilled hireling (2 gp / day)" in page
    await ac.post(url, data={"op": "hire", "kind": "skilled", "name": "Tomas the guide"})
    await ac.post(url, data={"op": "creature", "kind": "mount", "creature": "Riding Horse"})
    await ac.post(
        url, data={"op": "creature", "kind": "sidekick", "creature": "Scout", "name": "Pip"}
    )
    hired, horse, pip = registry.followers.of(lyra)
    assert hired.wage == 200 and horse.hp == horse.hp_max > 0 and pip.name == "Pip"
    bad = await ac.post(url, data={"op": "creature", "kind": "mount", "creature": "Nothing"})
    assert bad.status_code == 422
    # A day passes: the hireling is paid.
    gold = registry.gamestate.characters.get(lyra).currency.gp
    registry.clock.advance(world, DAY, "dm", "DM")
    assert registry.gamestate.characters.get(lyra).currency.gp == gold - 2
    assert "Wages for 1 day: 2 gp" in html.unescape((await ac.get("/log")).text)
    # The sidekick levels; its hit points rise.
    await ac.post(url, data={"op": "level", "follower": pip.id})
    grown = registry.followers.get(lyra, pip.id)
    assert grown.levels == 1 and grown.hp_max > pip.hp_max
    await ac.post(url, data={"op": "hp", "follower": horse.id, "delta": "-5"})
    assert registry.followers.get(lyra, horse.id).hp == horse.hp - 5
    # Every character of the world riding: the travel form chooses mounted.
    others = [
        pc.id
        for pc in registry.gamestate.characters.list()
        if pc.world_id == world and pc.id != lyra
    ]
    assert registry.followers.mounted([lyra, *others]) == (not others)
    assert "Followers" in (await ac.get(f"/characters/{lyra}")).text
    refused = await ac.post(url, data={"op": "fight", "follower": horse.id})
    assert "no running fight" in html.unescape(refused.text)
    await ac.post(url, data={"op": "dismiss", "follower": hired.id})
    assert len(registry.followers.of(lyra)) == 2


@requires_corpus
async def test_a_mount_joins_the_fight(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    from app.models.gamestate import Encounter, EncounterMonster

    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    encounter = registry.gamestate.encounters.create(
        Encounter(
            name="Ambush",
            world_id=world,
            monsters=[EncounterMonster(name="Goblin", source="MM", count=1)],
        )
    )
    combat = registry.combats.start(encounter, "dm")
    url = f"/characters/{lyra}/followers"
    await ac.post(
        url, data={"op": "creature", "kind": "mount", "creature": "Riding Horse", "name": "Ash"}
    )
    (horse,) = registry.followers.of(lyra)
    registry.followers.hit_points(lyra, horse.id, -3)
    joined = await ac.post(url, data={"op": "fight", "follower": horse.id})
    assert "Ash joins the fight" in html.unescape(joined.text)
    ash = next(c for c in registry.combats.get(combat.id).combatants if c.label == "Ash")
    assert ash.hp == horse.hp - 3 and ash.hp_max == horse.hp_max
    again = await ac.post(url, data={"op": "fight", "follower": horse.id})
    assert "already in this fight" in html.unescape(again.text)
    # Hurt in the fight, then the fight ends: the horse keeps its wounds.
    registry.combats.damage(combat.id, ash.id, 4, "dm")
    await ac.post(f"/gm/combat/{combat.id}/end")
    assert registry.followers.get(lyra, horse.id).hp == horse.hp - 7
    # Rested since, then "End" pressed again: the wounds do not come back.
    registry.followers.rest(lyra)
    await ac.post(f"/gm/combat/{combat.id}/end")
    assert registry.followers.get(lyra, horse.id).hp == horse.hp


@requires_corpus
async def test_a_sidekick_with_its_class(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.worlds.get(registry.gamestate.characters.get(lyra).world_id or "")
    registry.gamestate.worlds.update(
        world.id,
        source_set=world.source_set.model_copy(
            update={"sources": [*world.source_set.sources, "TCE"]}
        ),
    )
    url = f"/characters/{lyra}/followers"
    assert "Warrior Sidekick" in (await ac.get(url)).text
    await ac.post(
        url,
        data={
            "op": "creature",
            "kind": "sidekick",
            "creature": "Scout",
            "name": "Pip",
            "sidekick_class": "Warrior Sidekick|TCE",
        },
    )
    (pip,) = registry.followers.of(lyra)
    await ac.post(url, data={"op": "level", "follower": pip.id})
    await ac.post(url, data={"op": "level", "follower": pip.id})
    page = html.unescape((await ac.get(url)).text)
    assert "level 3, proficiency +2" in page and "Second Wind" in page
    assert "Improved Critical" in page
    bad = await ac.post(
        url,
        data={
            "op": "creature",
            "kind": "mount",
            "creature": "Riding Horse",
            "sidekick_class": "Warrior Sidekick|TCE",
        },
    )
    assert bad.status_code == 422


@requires_corpus
async def test_a_player_adds_no_creature_above_a_sidekicks_rating(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    url = f"/characters/{lyra}/followers"
    async with player_of(registry, lyra) as player:
        refused = await player.post(
            url, data={"op": "creature", "kind": "mount", "creature": "Adult Red Dragon"}
        )
        assert "challenge rating 1/2" in html.unescape(refused.text)
        await player.post(url, data={"op": "creature", "kind": "mount", "creature": "Riding Horse"})
    assert [f.name for f in registry.followers.of(lyra)] == ["Riding Horse"]
    # The gamemaster's word is enough.
    await ac.post(url, data={"op": "creature", "kind": "mount", "creature": "Adult Red Dragon"})
    assert len(registry.followers.of(lyra)) == 2
