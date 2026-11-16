"""Traps and hazards sprung at the table (app/services/traps.py)."""

from __future__ import annotations

from httpx import AsyncClient

from app.services.traps import Target, TrapService, read_trap
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, table

__all__ = ["table"]

ROOF = {
    "entries": [
        "Each creature beneath must succeed on a {@dc 13} Dexterity saving throw, taking 11 "
        "({@damage 2d10}) Bludgeoning damage on a failed save or half as much damage on a "
        "successful one.",
        {
            "type": "entries",
            "name": "Detect and Disarm",
            "entries": ["A {@dc 11} Wisdom ({@skill Perception|XPHB}) check finds it."],
        },
        {
            "type": "entries",
            "name": "At Higher Levels",
            "entries": [
                {
                    "type": "table",
                    "colLabels": ["Levels", "Bludgeoning Damage", "Save DC"],
                    "rows": [["5–10", "22 ({@damage 4d10})", "15"]],
                }
            ],
        },
    ]
}
DARTS = {
    "entries": [
        "Each dart makes a ranged attack with a +8 bonus against a random target (vision is "
        "irrelevant). (If there are none, nothing happens.) A target that is hit takes 2 "
        "({@damage 1d4}) piercing damage and must succeed on a {@dc 15} Constitution saving "
        "throw, taking 11 ({@damage 2d10}) poison damage on a failed save, or half as much "
        "damage on a successful one.",
        "A net falls: a {@dc 10} Strength saving throw or be {@condition restrained}.",
    ]
}


def test_reading_a_trap() -> None:
    roof = read_trap(ROOF)
    (save,) = roof.saves
    assert (save.dc, save.ability, save.damage, save.damage_type, save.half) == (
        13,
        "dex",
        "2d10",
        "bludgeoning",
        True,
    )
    assert roof.checks[0].skill == "perception" and roof.checks[0].dc == 11
    scaled = roof.at_level(7).saves[0]
    assert (scaled.damage, scaled.dc) == ("4d10", 15) and roof.at_level(2).saves[0].dc == 13
    darts = read_trap(DARTS)
    assert darts.attack is not None and (darts.attack.bonus, darts.attack.damage) == (8, "1d4")
    poison, net = darts.saves
    assert poison.on_hit and poison.damage == "2d10" and poison.damage_type == "poison"
    assert net.conditions == ("restrained",) and not net.on_hit


def test_springing_it() -> None:
    hurt: dict[str, int] = {}
    afflicted: list[tuple[str, str]] = []
    saves = {"a": 14, "b": 5}
    service = TrapService(
        None,
        save=lambda cid, ability: saves[cid],
        hurt=lambda cid, n: hurt.__setitem__(cid, hurt.get(cid, 0) + n),
        afflict=lambda cid, c: afflicted.append((cid, c)),
        dice=FixedDice([3, 4]),  # the 2d10: 7
    )
    caught = service.spring(
        "w",
        "Roof",
        read_trap(ROOF),
        [Target("a", "Ana"), Target("b", "Bo", resist=("bludgeoning",))],
        "dm",
        "DM",
    )
    # Ana saves (14 ≥ 13): half of 7 is 3. Bo fails: 7, halved by his resistance to 3.
    assert [(c.name, c.failed, c.damage) for c in caught] == [("Ana", False, 3), ("Bo", True, 3)]
    assert hurt == {"a": 3, "b": 3}
    # The darts miss Ana (10 + 8 against AC 19), hit Bo (AC 12): only Bo saves against
    # the poison; the net catches whoever fails.
    hurt.clear()
    service = TrapService(
        None,
        save=lambda cid, ability: saves[cid],
        hurt=lambda cid, n: hurt.__setitem__(cid, hurt.get(cid, 0) + n),
        afflict=lambda cid, c: afflicted.append((cid, c)),
        dice=FixedDice([10, 10, 2, 5, 5]),
    )
    caught = service.spring(
        "w",
        "Darts",
        read_trap(DARTS),
        [Target("a", "Ana", ac=19), Target("b", "Bo", ac=12)],
        "dm",
        "DM",
    )
    kinds = [(c.name, c.failed) for c in caught]
    assert kinds[:2] == [("Ana", False), ("Bo", True)]
    assert ("b", "restrained") in afflicted and ("a", "restrained") not in afflicted
    assert hurt["b"] == 2 + 10  # the dart's 1d4 (2) and the poison's 2d10 (5 + 5)


@requires_corpus
async def test_from_a_reference_page(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    page = await ac.get(f"/ref/trap/XDMG/Collapsing%20Roof?world={world}")
    assert "Spring it" in page.text
    form = await ac.get(
        "/gm/traps/form",
        params={"world": world, "category": "trap", "name": "Collapsing Roof", "source": "XDMG"},
    )
    assert "Dexterity save DC 15" in form.text  # Lyra is level 10: the trap scales
    assert "Call for Perception (DC 11)" in form.text
    before = registry.gamestate.characters.get(lyra).state.hp
    sprung = await ac.post(
        "/gm/traps/spring",
        data={
            "world": world,
            "category": "trap",
            "name": "Collapsing Roof",
            "source": "XDMG",
            "character": lyra,
            "op": "spring",
        },
    )
    assert "Collapsing Roof is sprung" in sprung.text
    after = registry.gamestate.characters.get(lyra).state.hp
    assert after is not None and (before is None or after < before)
    called = await ac.post(
        "/gm/traps/spring",
        data={
            "world": world,
            "category": "trap",
            "name": "Collapsing Roof",
            "source": "XDMG",
            "character": lyra,
            "op": "call:perception:11",
        },
    )
    assert "The check is called" in called.text and registry.checks.open_for(lyra)
