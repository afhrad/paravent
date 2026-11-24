"""Exploring a dungeon (app/services/delve.py): encounter checks run by the world's clock,
the one that comes up rolled on the table, and the clock panel that makes it an
encounter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.models.pc import PlayerCharacter
from app.services.clock import ClockService
from app.services.delve import DelveError, DelveService
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, _table, table  # noqa: F401

FOREST = _table(["{@creature wolf}", "2 {@creature goblin|MM|goblins}", "a quiet glade", "x"])


@pytest.fixture
def dungeon(tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {}
    moved: list[tuple[int, int, str]] = []
    pc = PlayerCharacter(name="Ana", world_id="w")

    def on_move(world: str, before: int, now: int, by: str, by_name: str, why: str) -> None:
        moved.append((before, now, why))
        if why != "travel":
            state["delve"].passed(world, before, now, by, by_name)

    clock = ClockService(GameStateStore(tmp_path), None, moved=on_move)
    resolved = {"wolf": ("Wolf", "MM"), "goblin": ("Goblin", "MM")}

    def make(rolls: list[int]) -> DelveService:
        state["delve"] = DelveService(
            clock,
            None,
            party=lambda _w: [pc],
            table=lambda key: FOREST if key == "Forest|XGE" else None,
            resolver=lambda _w: lambda n, s: resolved.get(n.lower()),
            dice=FixedDice(rolls),
        )
        return state["delve"]

    state.update(clock=clock, make=make, moved=moved)
    return state


class TestChecks:
    def test_the_clock_rolls_a_check_for_each_interval_passed(
        self, dungeon: dict[str, Any]
    ) -> None:
        clock: ClockService = dungeon["clock"]
        delve = dungeon["make"]([])  # every d20 a 10: none comes up
        delve.start("w", "Forest|XGE", 10, 18, "dm", "DM")
        clock.advance("w", 25, "dm", "DM")
        held = delve.of("w")
        assert held is not None and (held.rolled, held.checked) == (2, 8 * 60 + 20)
        # The five minutes left over count toward the next check.
        clock.advance("w", 5, "dm", "DM")
        assert delve.of("w").rolled == 3  # type: ignore[union-attr]

    def test_one_that_comes_up_rolls_the_table_and_holds_the_rest(
        self, dungeon: dict[str, Any]
    ) -> None:
        clock: ClockService = dungeon["clock"]
        # Miss, then 19 meets; the table rolls 2: two goblins.
        delve = dungeon["make"]([10, 19, 2])
        delve.start("w", "Forest|XGE", 10, 18, "dm", "DM")
        clock.advance("w", 60, "dm", "DM", "short_rest")
        held = delve.of("w")
        assert held is not None and held.found is not None
        assert (held.rolled, held.found.at, held.found.check) == (2, 8 * 60 + 20, 19)
        assert [(m.name, m.count) for m in held.found.monsters] == [("Goblin", 2)]
        # Waiting: the clock moves on without a check until it is dealt with.
        clock.advance("w", 60, "dm", "DM")
        assert delve.of("w").rolled == 2  # type: ignore[union-attr]
        delve.dismiss("w")
        held = delve.of("w")
        assert held is not None and held.found is None and held.checked == 10 * 60
        clock.advance("w", 10, "dm", "DM")
        assert delve.of("w").rolled == 3  # type: ignore[union-attr]

    def test_the_road_rolls_its_own(self, dungeon: dict[str, Any]) -> None:
        clock: ClockService = dungeon["clock"]
        delve = dungeon["make"]([])
        delve.start("w", "", 10, 18, "dm", "DM")
        clock.advance("w", 60, "dm", "DM", "travel")
        assert delve.of("w").rolled == 0  # type: ignore[union-attr]

    def test_a_check_now_and_no_table(self, dungeon: dict[str, Any]) -> None:
        delve = dungeon["make"]([20])
        delve.start("w", "", 60, 18, "dm", "DM")
        found = delve.check("w", "dm", "DM")
        assert found is not None and found.check == 20 and not found.monsters
        with pytest.raises(DelveError):  # one waits already
            delve.check("w", "dm", "DM")
        assert delve.of("w").checked == 8 * 60  # type: ignore[union-attr]

    def test_a_clock_set_back_counts_from_there(self, dungeon: dict[str, Any]) -> None:
        clock: ClockService = dungeon["clock"]
        delve = dungeon["make"]([])
        delve.start("w", "", 10, 18, "dm", "DM")
        clock.advance("w", 60, "dm", "DM")
        clock.set("w", 8 * 60, "dm", "DM")
        clock.advance("w", 20, "dm", "DM")
        assert delve.of("w").rolled == 8  # type: ignore[union-attr]

    def test_bad_settings_and_leaving(self, dungeon: dict[str, Any]) -> None:
        delve = dungeon["make"]([])
        with pytest.raises(DelveError):
            delve.start("w", "Nowhere|X", 10, 18, "dm", "DM")
        with pytest.raises(DelveError):
            delve.start("w", "", 10, 7, "dm", "DM")
        delve.start("w", "", 10, 18, "dm", "DM")
        delve.leave("w", "dm", "DM")
        assert delve.of("w") is None


@requires_corpus
class TestOnTheClockPanel:
    async def test_exploring_from_the_party_view(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.table.select(world)
        page = (await ac.get(f"/gm?world={world}")).text
        assert 'id="tool-delve"' in page and 'name="every"' in page
        await ac.post(
            f"/gm/clock/{world}/delve",
            data={"environment": "Forest|XGE", "every": "10", "on": "18"},
        )
        held = registry.delve.of(world)
        assert held is not None and held.environment == "Forest|XGE"
        page = (await ac.get(f"/gm?world={world}")).text
        assert "Exploring" in page and "Next check at" in page
        # The next check meets, and the forest's first row is a wolf or so.
        registry.delve._dice = FixedDice([19, 1, 1, 1])
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": "10"})
        found = registry.delve.of(world).found  # type: ignore[union-attr]
        assert found is not None and found.check == 19
        page = (await ac.get(f"/gm?world={world}")).text
        assert "No further checks until this is dealt with." in page
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "An encounter while exploring" in log
        if found.monsters:
            made = await ac.post(f"/gm/clock/{world}/delve_encounter")
            assert made.status_code == 303
            encounter_id = registry.delve.of(world).found.encounter_id  # type: ignore[union-attr]
            assert encounter_id is not None
            assert registry.encounters.get(encounter_id).monsters == found.monsters
        await ac.post(f"/gm/clock/{world}/delve_clear")
        assert registry.delve.of(world).found is None  # type: ignore[union-attr]
        await ac.post(f"/gm/clock/{world}/delve_leave")
        assert registry.delve.of(world) is None
        # In German too.
        await ac.post(
            f"/gm/clock/{world}/delve", data={"environment": "", "every": "60", "on": "20"}
        )
        await ac.post("/language", data={"lang": "de"})
        page = (await ac.get(f"/gm?world={world}")).text
        assert "Erkundung" in page and "Nächster Wurf um" in page
