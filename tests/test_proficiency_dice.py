"""Proficiency dice, the 2014 guide's variant (app/services/character_rules.py): a die
instead of the proficiency bonus on checks, attacks and saves, twice for expertise."""

from __future__ import annotations

from httpx import AsyncClient

from app.services.character_rules import proficiency_die, with_proficiency_dice
from app.store.protocol import log_key
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table  # noqa: F401


def test_the_die_by_the_bonus() -> None:
    assert [proficiency_die(b) for b in (2, 3, 4, 5, 6)] == [4, 6, 8, 10, 12]


def test_the_proficiency_rolled_instead() -> None:
    asked: list[str] = []

    def roll(dice: str) -> int:
        asked.append(dice)
        return 5

    skill = (("ability:int", 4), ("proficiency", 3))
    assert with_proficiency_dice(7, skill, 3, roll) == (
        9,
        (("ability:int", 4), ("proficiency_die:1d6", 5)),
    )
    expert = (("ability:dex", 3), ("expertise", 6))
    assert with_proficiency_dice(9, expert, 3, roll)[0] == 8 and asked[-1] == "2d6"
    tool = (("ability:dex", 3), ("tool:Thieves' Tools", 3))
    assert with_proficiency_dice(6, tool, 3, roll)[0] == 8
    # Nothing to roll for: unchanged, nothing rolled.
    asked.clear()
    plain = (("ability:str", 2),)
    assert with_proficiency_dice(2, plain, 3, roll) == (2, plain) and not asked


@requires_corpus
class TestOnTheSheet:
    async def test_a_proficient_roll_adds_the_die(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
        skill = next(s for s in sheet.skills if any(k == "proficiency" for k, _ in s.value.sources))
        flat = (skill.value.value or 0) - (sheet.proficiency.value or 0)
        die = f"d{2 * (sheet.proficiency.value or 0)}"
        page = (await ac.get(f"/characters/{lyra}")).text
        assert f"+{flat} + {die}" not in page
        registry.gamestate.worlds.update(world, character_rules=["proficiency_dice"])
        page = (await ac.get(f"/characters/{lyra}")).text
        assert f"{flat:+d} + {die}" in page
        await ac.post(f"/characters/{lyra}/act/roll", data={"kind": "skill", "subject": skill.key})
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert f"Prof 1{die}" in log
        entry = registry.protocol.store.entries(log_key(world))[-1]
        assert entry.roll is not None
        assert any(b.source == f"proficiency_die:1{die}" for b in entry.roll.bonuses)
        assert not any(b.source == "proficiency" for b in entry.roll.bonuses)
