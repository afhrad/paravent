"""The features a world plays with (app/services/features.py): switched on and off in
World settings, gone from the gamemaster's and the players' screens when off, and nothing
of them run on the clock."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from app.models.gamestate import World
from app.services import features
from app.services.clock import DAY
from app.services.worlds import WorldInput, WorldService
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import player_of, table  # noqa: F401


class TestSwitches:
    def test_a_world_from_before_plays_with_everything(self) -> None:
        assert features.switched_on(None) == frozenset(features.FEATURES)
        assert features.switched_on(World(name="Old")) == frozenset(features.FEATURES)

    def test_survival_is_part_of_the_road(self) -> None:
        world = World(name="W", features=["survival", "shops"])
        assert not features.on(world, "travel") and not features.on(world, "survival")
        assert features.on(
            world.model_copy(update={"features": ["travel", "survival"]}), "survival"
        )
        assert features.kept(["shops", "nonsense", "travel", "shops"]) == ["travel", "shops"]

    def test_a_new_world_starts_with_the_basics_and_keeps_its_choice(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path)
        worlds = WorldService(store)
        made = worlds.create(WorldInput("New"))
        assert made.features == list(features.BASICS)
        # A form without the features leaves them; one with them sets them.
        worlds.update(made.id, WorldInput("New"))
        assert worlds.get(made.id).features == list(features.BASICS)
        worlds.update(made.id, WorldInput("New", features=("chases", "travel")))
        assert worlds.get(made.id).features == ["travel", "chases"]
        # Kept in the world's file, read back from it; an empty choice too.
        worlds.update(made.id, WorldInput("New", features=()))
        assert GameStateStore(tmp_path).worlds.get(made.id).features == []


@requires_corpus
class TestOnTheScreens:
    async def test_switched_off_features_are_gone(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.table.select(world)
        registry.gamestate.characters.update(lyra, lifestyle="modest")
        # Everything on (a world from before the switches).
        page = (await ac.get(f"/gm?world={world}")).text
        for shown in ('id="tool-travel"', 'id="tool-delve"', 'id="party-shop"', "/gm/quests"):
            assert shown in page, shown
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert f"/characters/{lyra}/followers" in sheet
        # The world form: only travel and quests; an open shop closes on saving.
        await ac.post(f"/gm/shop/{world}/open", data={"name": "The Anvil"})
        assert registry.shops.of(world).open
        saved = await ac.post(
            f"/gm/worlds/{world}",
            data={
                "name": "W",
                "sources": ["PHB", "MM", "XGE", "XDMG"],
                "features_set": "1",
                "features": ["travel", "quests"],
            },
        )
        assert saved.status_code == 303
        assert registry.gamestate.worlds.get(world).features == ["travel", "quests"]
        assert not registry.shops.of(world).open
        page = (await ac.get(f"/gm?world={world}")).text
        assert 'id="tool-travel"' in page and "/gm/quests" in page
        for gone in ('id="tool-delve"', 'id="party-shop"', "/gm/factions", 'id="tool-madness"'):
            assert gone not in page, gone
        # Survival off: the road asks for no rations or water.
        assert 'name="rations"' not in page and 'name="water"' not in page
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert f"/characters/{lyra}/followers" not in sheet
        assert (await ac.get(f"/characters/{lyra}/followers")).status_code == 404
        assert (await ac.get(f"/characters/{lyra}/downtime")).status_code == 404
        # A day passes: no lifestyle is charged.
        purse = registry.gamestate.characters.get(lyra).currency
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": str(DAY)})
        assert registry.gamestate.characters.get(lyra).currency == purse
        # Refused, however asked.
        await ac.post(f"/gm/clock/{world}/delve", data={"every": "10", "on": "18"})
        assert registry.delve.of(world) is None
        # The player: no X-card, no safety page in the menu.
        async with player_of(registry, lyra) as player:
            sheet = (await player.get(f"/characters/{lyra}")).text
            assert "/safety/xcard" not in sheet and 'href="/safety"' not in sheet
        # The settings dialog shows the choice.
        dialog = (await ac.get("/gm/world/settings")).text
        assert 'name="features" type="checkbox" value="travel" checked' in dialog
        assert 'value="shops" checked' not in dialog
