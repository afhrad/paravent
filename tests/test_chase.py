"""A chase run from the party view (app/services/chase.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.chase import ChaseError, ChaseService, Runner, complication, free_dashes
from app.services.dice import Advantage
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, table

__all__ = ["table"]

URBAN = [
    ["1", "A crowd blocks your way. Make a {@dc 10} Strength check."],
    ["2–6", "A cart. Make a {@dc 10} Dexterity saving throw."],
    ["7–12", "There is no complication."],
]


class Table:
    """A chase service with scripted dice, a sheet that answers with set totals, and
    the characters it tired."""

    def __init__(self, root: Path, rolls: list[int], totals: list[int]) -> None:
        self.tired: list[str] = []
        self.asked: list[tuple[str, str, str, Advantage]] = []
        self.totals = totals

        def sheet_roll(cid: str, kind: str, subject: str, edge: Advantage) -> int:
            self.asked.append((cid, kind, subject, edge))
            return self.totals.pop(0)

        self.service = ChaseService(
            GameStateStore(root),
            None,
            sheet_roll=sheet_roll,
            tire=self.tired.append,
            dice=FixedDice(rolls),
        )


def test_rules_of_thumb() -> None:
    assert free_dashes(-4, "one") == 1 and free_dashes(-4, "classic") == 0
    assert free_dashes(2, "classic") == 5
    assert complication(URBAN, 1).startswith("A crowd blocks your way")
    assert complication(URBAN, 9) == "" and complication([["11–20", "No complication."]], 15) == ""


def test_a_chase_from_start_to_escape(tmp_path: Path) -> None:
    # Initiative: the thug rolls 5 (+1), Ana 14.
    table = Table(tmp_path, [5, 14], [])
    service = table.service
    with pytest.raises(ChaseError):
        service.start(
            "w",
            [Runner("Ana", "quarry", "a")],
            environment="urban",
            distance=30,
            rules="one",
            by="dm",
            by_name="DM",
        )
    chase = service.start(
        "w",
        [
            Runner(
                "Thug", "pursuer", monster="Thug|XMM", initiative_bonus=1, con_mod=-3, passive=11
            ),
            Runner("Ana", "quarry", "a", con_mod=1, stealth=5),
        ],
        environment="urban",
        distance=30,
        rules="one",
        by="dm",
        by_name="DM",
    )
    assert [r.name for r in chase.runners] == ["Ana", "Thug"]
    assert [r.free_dashes for r in chase.runners] == [4, 1]
    # The thug's free Dash, then one more: a DC 10 save at −3 on a 4 fails.
    service.dash("w", 1, "dm", "DM")
    table.service._dice = FixedDice([4, 4])  # noqa: SLF001
    chase = service.dash("w", 1, "dm", "DM")
    assert chase.runners[1].exhaustion == 1 and table.tired == []
    # Ana's turn ends: the d12 comes up 2, a cart in the thug's way.
    chase = service.end_turn("w", URBAN, 12, "dm", "DM")
    assert chase.runners[1].complication.startswith("A cart") and chase.turn == 1
    # The thug's turn ends (7: nothing); the round is done.
    table.service._dice = FixedDice([7])  # noqa: SLF001
    chase = service.end_turn("w", URBAN, 12, "dm", "DM")
    assert chase.turn == 2 and chase.runners[1].complication == ""
    with pytest.raises(ChaseError):
        service.end_turn("w", URBAN, 12, "dm", "DM")
    # Ana's Stealth on her sheet, with advantage: 12 beats the thug's 11.
    table.totals.append(12)
    chase = service.round_end("w", [], {0: Advantage.ADVANTAGE}, "dm", "DM")
    assert table.asked[-1] == ("a", "skill", "stealth", Advantage.ADVANTAGE)
    assert chase.runners[0].escaped and chase.over == "escaped" and chase.round == 2
    service.end("w", "stopped", "dm", "DM")
    assert service.of("w") is None


def test_in_sight_fails_and_characters_tire(tmp_path: Path) -> None:
    table = Table(tmp_path, [], [3])
    service = table.service
    service.start(
        "w",
        [Runner("Bo", "pursuer", "b", con_mod=-3, passive=12), Runner("Rat", "quarry")],
        environment="wilderness",
        distance=20,
        rules="classic",
        by="dm",
        by_name="DM",
    )
    # Under the 2014 rules −3 leaves no free Dash: Bo's check on the sheet, 3, fails.
    chase = service.dash("w", 0, "dm", "DM")
    assert table.asked[-1][1:3] == ("check", "con") and table.tired == ["b"]
    chase = service.round_end("w", [1], {}, "dm", "DM")
    assert not chase.runners[1].escaped and chase.round == 2 and not chase.over


@requires_corpus
async def test_the_party_view_runs_it(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    assert "Start a chase" in (await ac.get("/gm")).text
    started = await ac.post(
        f"/gm/chase/{world}/start",
        data={
            f"side_{lyra}": "quarry",
            "pursuer": "2 Goblin",
            "environment": "urban",
            "distance": "30",
        },
        headers={"HX-Request": "true"},
    )
    chase = registry.chases.of(world)
    assert started.status_code == 200 and chase is not None
    assert {r.name for r in chase.runners} == {"Lyra Mooncaller", "Goblin 1", "Goblin 2"}
    goblin = next(r for r in chase.runners if r.monster)
    assert goblin.monster == "Goblin|MM" and goblin.stealth == 6 and goblin.passive == 9
    for _ in chase.runners:
        await ac.post(f"/gm/chase/{world}/turn", headers={"HX-Request": "true"})
    await ac.post(f"/gm/chase/{world}/round", data={}, headers={"HX-Request": "true"})
    assert "A chase begins, through the streets" in (await ac.get("/log")).text
    ended = await ac.post(f"/gm/chase/{world}/end", headers={"HX-Request": "true"})
    assert "chase-form" in ended.text and registry.chases.of(world) is None
