"""A world's time in the game and what runs out against it (app/services/clock.py)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import BastionFacility, Role, SourceSet, World
from app.models.pc import ChosenOption, InventoryItem, PlayerCharacter
from app.services.clock import (
    DAY,
    ClockError,
    ClockService,
    bastions_due,
    day_of,
    light_minutes,
    part_of_day,
    spell_minutes,
    time_of,
)
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_checks import LYRA, PASSWORD


def _timed(unit: str, amount: int, concentration: bool = False) -> list[dict[str, object]]:
    return [
        {
            "type": "timed",
            "duration": {"type": unit, "amount": amount},
            "concentration": concentration,
        }
    ]


class TestReading:
    def test_durations(self) -> None:
        assert spell_minutes(_timed("hour", 8)) == (480, False)
        assert spell_minutes(_timed("minute", 1, True)) == (1, True)
        assert spell_minutes(_timed("round", 10)) == (1, False)
        assert spell_minutes(_timed("round", 1)) is None  # under a minute
        assert spell_minutes([{"type": "instant"}]) is None
        assert spell_minutes([{"type": "permanent", "ends": ["dispel"]}]) is None

    def test_lights_and_times(self) -> None:
        assert light_minutes("Torch|PHB") == 60 and light_minutes("Hooded Lantern") == 360
        assert light_minutes("Dagger") == 0
        assert (day_of(0), time_of(0)) == (1, "00:00")
        assert (day_of(DAY + 14 * 60 + 20), time_of(DAY + 14 * 60 + 20)) == (2, "14:20")
        assert part_of_day(8 * 60) == "morning" and part_of_day(22 * 60) == "night"

    def test_bastions_due(self) -> None:
        assert bastions_due(10 * DAY, [("A", None), ("B", 5 * DAY), ("C", 2 * DAY)]) == ["A", "C"]


class TestTheService:
    @pytest.fixture
    def held(self) -> dict[str, str | None]:
        return {"a": "Haste|PHB"}

    @pytest.fixture
    def ended(self) -> list[tuple[str, str]]:
        return []

    @pytest.fixture
    def clock(
        self, tmp_path: Path, held: dict[str, str | None], ended: list[tuple[str, str]]
    ) -> ClockService:
        def end(character: str, spell: str, _by: str) -> None:
            ended.append((character, spell))
            held[character] = None

        return ClockService(
            GameStateStore(tmp_path),
            None,
            concentrating=lambda c: held.get(c),
            end_concentration=end,
        )

    def test_a_new_world_starts_on_the_first_morning(self, clock: ClockService) -> None:
        assert clock.of("w").minute == 8 * 60

    def test_timers_run_out_as_the_clock_passes_them(
        self, clock: ClockService, ended: list[tuple[str, str]]
    ) -> None:
        clock.start("w", "light", "Torch|PHB", 60, character_id="a")
        clock.start("w", "spell", "Haste|PHB", 1, character_id="a", concentration=True)
        clock.start("w", "custom", "The ritual completes", 240)
        assert [t.label for t in clock.active("w")] == [
            "Haste|PHB",
            "Torch|PHB",
            "The ritual completes",
        ]
        clock.advance("w", 10, "dm", "DM")
        assert ended == [("a", "Haste|PHB")]
        clock.advance("w", 60, "dm", "DM")
        assert [t.label for t in clock.active("w")] == ["The ritual completes"]

    def test_a_spell_whose_concentration_ended_is_gone(
        self, clock: ClockService, held: dict[str, str | None], ended: list[tuple[str, str]]
    ) -> None:
        clock.start("w", "spell", "Haste|PHB", 1, character_id="a", concentration=True)
        held["a"] = None
        assert clock.active("w") == []
        clock.advance("w", 5, "dm", "DM")
        assert ended == []

    def test_a_new_concentration_spell_replaces_the_old(self, clock: ClockService) -> None:
        clock.start("w", "spell", "Haste|PHB", 1, character_id="a", concentration=True)
        clock.start("w", "spell", "Mage Armor|PHB", 480, character_id="a")
        clock.start("w", "spell", "Fly|PHB", 10, character_id="a", concentration=True)
        assert sorted(t.label for t in clock.of("w").timers) == ["Fly|PHB", "Mage Armor|PHB"]

    def test_setting_and_stopping(self, clock: ClockService) -> None:
        timer = clock.start("w", "custom", "Bell", 30)
        clock.set("w", 2 * DAY, "dm", "DM")  # forward: the bell runs out
        assert clock.of("w").timers == [] and clock.of("w").minute == 2 * DAY
        timer = clock.start("w", "custom", "Bell", 30)
        clock.set("w", DAY, "dm", "DM")  # back: the bell keeps its end
        assert [t.id for t in clock.active("w")] == [timer.id]
        clock.stop("w", timer.id, character_id="someone")  # not theirs
        assert clock.active("w") and not clock.stop("w", timer.id).timers

    def test_refused(self, clock: ClockService) -> None:
        with pytest.raises(ClockError):
            clock.advance("w", 0, "dm", "DM")
        with pytest.raises(ClockError):
            clock.start("w", "custom", "  ", 10)


@pytest.fixture
async def table(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(
        World(name="W", source_set=SourceSet(sources=["PHB", "MM", "XDMG"]))
    )
    data = json.loads(LYRA.read_text(encoding="utf-8"))
    data.pop("$schema", None)
    data["world_id"] = world.id
    lyra = registry.gamestate.characters.create(PlayerCharacter.model_validate(data))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry, lyra.id


@requires_corpus
class TestAtTheTable:
    async def test_the_party_view_moves_the_clock_and_rests(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        assert "Day 1 · 08:00" in (await ac.get("/gm")).text
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": "60"})
        await ac.post(
            f"/gm/clock/{world}/advance", data={"amount": "2", "unit": "day", "why": "travel"}
        )
        assert registry.clock.of(world).minute == 9 * 60 + 2 * DAY
        await ac.post(f"/gm/clock/{world}/set", data={"day": "4", "time": "21:30"})
        assert registry.clock.of(world).minute == 3 * DAY + 21 * 60 + 30
        pc = registry.gamestate.characters.get(lyra)
        pc.state.slots_used = {"1": 2}
        registry.gamestate.characters.replace(pc)
        await ac.post(f"/gm/clock/{world}/rest", data={"kind": "long"})
        assert registry.gamestate.characters.get(lyra).state.slots_used == {}
        assert registry.clock.of(world).minute == 4 * DAY + 5 * 60 + 30
        log = (await ac.get("/log")).text
        assert "Time passes (long rest): 8 h" in log and "Time passes (travel): 2 d" in log

    async def test_a_lit_torch_burns_out_and_a_lantern_needs_oil(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        pc = registry.gamestate.characters.get(lyra)
        world = pc.world_id
        assert world is not None
        pc.inventory = [
            *pc.inventory,
            InventoryItem(item=ChosenOption.model_validate("Torch|PHB"), quantity=2),
            InventoryItem(item=ChosenOption.model_validate("Hooded Lantern|PHB")),
        ]
        registry.gamestate.characters.replace(pc)
        torch, lantern = len(pc.inventory) - 2, len(pc.inventory) - 1
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert "Light it: burns 1 h" in sheet
        await ac.post(f"/characters/{lyra}/act/light", data={"index": str(lantern)})
        assert registry.clock.active(world) == []  # no oil
        await ac.post(f"/characters/{lyra}/act/light", data={"index": str(torch)})
        assert registry.gamestate.characters.get(lyra).inventory[torch].quantity == 1
        assert [t.label for t in registry.clock.of_character(world, lyra)] == ["Torch|PHB"]
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": "60"})
        assert registry.clock.active(world) == []
        assert "Torch burns out" in (await ac.get("/log")).text

    async def test_spells_with_a_duration_run_on_the_clock(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        await ac.post(f"/characters/{lyra}/act/cast", data={"spell": "Mage Armor|PHB"})
        await ac.post(f"/characters/{lyra}/act/cast", data={"spell": "Haste|PHB"})
        timers = registry.clock.of_character(world, lyra)
        assert {(t.label, t.ends - 8 * 60, t.concentration) for t in timers} == {
            ("Mage Armor|PHB", 480, False),
            ("Haste|PHB", 1, True),
        }
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert "cs-timers" in sheet and "8 h" in sheet
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": "10"})
        assert registry.gamestate.characters.get(lyra).state.concentration is None
        assert [t.label for t in registry.clock.of_character(world, lyra)] == ["Mage Armor|PHB"]
        # A player puts out their own timer from the sheet.
        timer = registry.clock.of_character(world, lyra)[0]
        await ac.post(f"/characters/{lyra}/timer/{timer.id}/stop")
        assert registry.clock.of_character(world, lyra) == []

    async def test_a_bastion_turn_is_due_seven_days_on(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        bastion = registry.bastions.of(lyra).model_copy(
            update={"facilities": [BastionFacility(ref="Arcane Study|XDMG")]}
        )
        registry.gamestate.bastions.upsert(bastion)
        assert "Bastion turn due: Lyra Mooncaller" in (await ac.get("/gm")).text
        await ac.post(f"/characters/{lyra}/bastion", data={"op": "turn"})
        assert registry.bastions.of(lyra).last_turn == 8 * 60
        assert "Bastion turn due" not in (await ac.get("/gm")).text
        assert "due on day 8" in (await ac.get(f"/characters/{lyra}/bastion")).text
        await ac.post(f"/gm/clock/{world}/advance", data={"amount": "7", "unit": "day"})
        assert "Bastion turn due: Lyra Mooncaller" in (await ac.get("/gm")).text
