"""Diseases, poisons and curses on characters (app/services/afflictions.py).

The texts below are the System Reference Documents' (SRD 5.1 and 5.2.1, CC BY 4.0)."""

from __future__ import annotations

from collections.abc import Iterator

from httpx import AsyncClient

from app.services.afflictions import DAY, AfflictionService, Victim, read_affliction
from app.services.sheet import CONDITIONS
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, table

__all__ = ["table"]

SEWER_PLAGUE_2024 = {
    "entries": [
        "Any Humanoid that is wounded by a creature that carries the contagion or that comes "
        "into contact with contaminated filth or offal must succeed on a {@dc 11} Constitution "
        "saving throw or become infected with Sewer Plague. A creature suffers the following "
        "effects {@dice 1d4} days after infection:",
        {
            "type": "list",
            "items": [
                {
                    "type": "item",
                    "name": "Fatigue",
                    "entry": "The creature gains 1 {@condition Exhaustion|XPHB} level.",
                },
            ],
        },
        {
            "type": "entries",
            "name": "Fighting the Contagion",
            "entries": [
                "Daily at dawn, an infected creature makes a {@dc 11} Constitution saving "
                "throw. On a failed save, the creature gains 1 {@condition Exhaustion|XPHB} "
                "level as its fatigue worsens. On a successful save, the creature's Exhaustion "
                "level decreases by 1. If the creature's Exhaustion level is reduced to 0, the "
                "contagion ends on the creature."
            ],
        },
    ]
}
CACKLE_FEVER_2014 = {
    "entries": [
        "Symptoms manifest {@dice 1d4} hours after infection and include fever and "
        "disorientation. The infected creature gains one level of {@condition exhaustion} that "
        "can't be removed until the disease is cured.",
        "Any event that causes the infected creature great stress—including entering "
        "combat, taking damage, experiencing fear, or having a nightmare—forces the "
        "creature to make a {@dc 13} Constitution saving throw.",
        "At the end of each long rest, an infected creature can make a {@dc 13} Constitution "
        "saving throw. On a successful save, the DC for this save and for the save to avoid an "
        "attack of mad laughter drops by {@dice 1d6}. When the saving throw DC drops to 0, the "
        "creature recovers from the disease.",
    ]
}
SIGHT_ROT_2024 = {
    "entries": [
        "Any Beast or Humanoid that drinks water tainted by Sight Rot must succeed on a {@dc 15} "
        "Constitution saving throw or have the {@condition Blinded|XPHB} condition until the "
        "contagion ends.",
        {
            "type": "entries",
            "name": "Spreading the Contagion",
            "entries": [
                "Any Humanoid that makes skin contact with a creature infected with Sight Rot "
                "must succeed on a {@dc 15} Constitution saving throw or also become infected."
            ],
        },
    ]
}
ASSASSINS_BLOOD = {
    "poison": True,
    "entries": [
        "A creature subjected to this poison must make a {@dc 10} Constitution saving throw. "
        "On a failed save, it takes 6 ({@damage 1d12}) poison damage and is {@condition "
        "poisoned} for 24 hours. On a successful save, the creature takes half damage and "
        "isn't {@condition poisoned}."
    ],
}


def test_reading_diseases() -> None:
    sewer = read_affliction(SEWER_PLAGUE_2024, "disease")
    assert sewer.catch is not None and (sewer.catch.dc, sewer.catch.ability) == (11, "con")
    assert (sewer.onset, sewer.onset_unit, sewer.exhaustion) == ("1d4", DAY, 1)
    assert (sewer.course, sewer.dc, sewer.ability, sewer.rule) == ("dawn", 11, "con", "exhaustion")
    cackle = read_affliction(CACKLE_FEVER_2014, "disease")
    # The stress save is not how it is caught: the 2014 text gives no such save.
    assert cackle.catch is None
    assert (cackle.onset, cackle.onset_unit, cackle.exhaustion) == ("1d4", 60, 1)
    assert (cackle.course, cackle.rule, cackle.drop) == ("long_rest", "dc_drop", "1d6")
    sight = read_affliction(SIGHT_ROT_2024, "disease")
    assert sight.catch is not None and sight.catch.conditions == ("blinded",)
    assert sight.lasts == "" and not sight.course  # until it is cured


def test_reading_a_poison() -> None:
    blood = read_affliction(ASSASSINS_BLOOD, "poison")
    catch = blood.catch
    assert catch is not None
    assert (catch.dc, catch.damage, catch.damage_type, catch.half) == (10, "1d12", "poison", True)
    assert catch.conditions == ("poisoned",) and (blood.lasts, blood.lasts_unit) == ("24", 60)
    assert blood.lasting


def _service(
    registry: StoreRegistry, saves: Iterator[int], clock: list[int], dice: FixedDice
) -> tuple[AfflictionService, dict[str, int]]:
    hurt: dict[str, int] = {}
    service = AfflictionService(
        registry.gamestate,
        registry.protocol,
        now=lambda _w: clock[0],
        save=lambda _c, _a: next(saves),
        hurt=lambda cid, n: hurt.__setitem__(cid, hurt.get(cid, 0) + n),
        conditions=frozenset(CONDITIONS),
        dice=dice,
    )
    return service, hurt


@requires_corpus
async def test_a_disease_runs_its_course(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    _ac, registry, lyra = table
    pc = registry.gamestate.characters.get(lyra)
    world = pc.world_id or ""
    clock = [8 * 60]  # Day 1, 08:00
    # Caught on a 5; symptoms after 2 days; then the dawn saves: 4, 15, 15.
    service, _ = _service(registry, iter([5, 4, 15, 15]), clock, FixedDice([2]))
    (outcome,) = service.afflict(
        world,
        "disease",
        "Sewer Plague",
        "Sewer Plague|XDMG",
        read_affliction(SEWER_PLAGUE_2024, "disease"),
        [Victim(lyra, "Lyra")],
        with_save=True,
        by="dm",
        by_name="DM",
    )
    assert outcome.caught and outcome.lasting
    (held,) = registry.gamestate.characters.get(lyra).state.afflictions
    assert held.onset == 8 * 60 + 2 * DAY and not held.showing
    assert registry.gamestate.characters.get(lyra).state.exhaustion == 0
    # Day 3, 09:00: the symptoms show; the dawn before them rolls nothing.
    service.passed(world, clock[0], 2 * DAY + 9 * 60, "dm", "DM")
    state = registry.gamestate.characters.get(lyra).state
    assert state.exhaustion == 1 and state.afflictions[0].showing
    assert state.held_by_afflictions == 1
    # A long rest keeps the level the disease holds.
    registry.characters.act_for(lyra, "rest", {"kind": "long"}, _dm())
    assert registry.gamestate.characters.get(lyra).state.exhaustion == 1
    # Three dawns: failed (4), saved (15), saved (15) — the last one cures it.
    service.passed(world, 2 * DAY + 9 * 60, 5 * DAY + 9 * 60, "dm", "DM")
    state = registry.gamestate.characters.get(lyra).state
    assert state.afflictions == [] and state.exhaustion == 0


@requires_corpus
async def test_a_poison_wears_off(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    _ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    clock = [0]
    service, hurt = _service(registry, iter([3]), clock, FixedDice([8]))
    blood = read_affliction(ASSASSINS_BLOOD, "poison")
    (outcome,) = service.afflict(
        world,
        "poison",
        "Assassin's Blood",
        "",
        blood,
        [Victim(lyra, "Lyra", resist=("poison",))],
        with_save=True,
        by="dm",
        by_name="DM",
    )
    assert outcome.caught and hurt == {lyra: 4}  # 8, halved by the resistance
    state = registry.gamestate.characters.get(lyra).state
    assert "poisoned" in state.conditions and state.afflictions[0].ends == 24 * 60
    service.passed(world, 0, 25 * 60, "dm", "DM")
    state = registry.gamestate.characters.get(lyra).state
    assert "poisoned" not in state.conditions and state.afflictions == []


@requires_corpus
async def test_at_the_table(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    page = await ac.get(f"/ref/disease/XDMG/Sewer%20Plague?world={world}")
    assert "Afflict" in page.text
    disease = {"world": world, "category": "disease", "name": "Sewer Plague", "source": "XDMG"}
    form = await ac.get("/gm/afflictions/form", params=disease)
    assert "Constitution save DC 11" in form.text and "at each dawn" in form.text
    done = await ac.post(
        "/gm/afflictions/afflict", data={**disease, "character": lyra, "op": "caught"}
    )
    assert "carries it" in done.text
    (held,) = registry.gamestate.characters.get(lyra).state.afflictions
    sheet = (await ac.get(f"/characters/{lyra}")).text
    assert "Diseases, poisons and curses" in sheet and "Not showing yet" in sheet
    # The world's clock carries it on: after five days its symptoms have shown (and the
    # dawn saves since may have cured it already).
    registry.clock.advance(world or "", 5 * DAY, "dm", "DM")
    left = registry.gamestate.characters.get(lyra).state.afflictions
    assert all(x.showing for x in left)
    assert "Symptoms show: Disease Sewer Plague" in (await ac.get(f"/log/panel?world={world}")).text
    if left:
        await ac.post(f"/gm/afflictions/{lyra}/end", data={"affliction": left[0].id})
    # A curse of the gamemaster's own, then both ended.
    await ac.post(f"/gm/afflictions/{lyra}/curse", data={"name": "Hag's Mark", "note": "Ugh"})
    marks = registry.gamestate.characters.get(lyra).state.afflictions
    assert [m.name for m in marks] == ["Hag's Mark"]
    for mark in marks:
        await ac.post(f"/gm/afflictions/{lyra}/end", data={"affliction": mark.id})
    assert registry.gamestate.characters.get(lyra).state.afflictions == []
    log = (await ac.get(f"/log/panel?world={world}")).text
    assert "Cursed: Hag" in log and "affliction:" not in log
    poison = {"world": world, "category": "item", "name": "Assassin's Blood", "source": "XDMG"}
    form = await ac.get("/gm/afflictions/form", params=poison)
    assert "Roll the Constitution save DC 10" in form.text and held.kind == "disease"


@requires_corpus
async def test_the_players_roll_the_catching_save(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    disease = {"world": world, "category": "disease", "name": "Sewer Plague", "source": "XDMG"}
    form = (await ac.get("/gm/afflictions/form", params=disease)).text
    assert "Players roll the saves" in form
    asked = await ac.post(
        "/gm/afflictions/afflict", data={**disease, "character": lyra, "op": "ask"}
    )
    assert "The players are rolling their saves" in asked.text
    (call,) = [c for c in registry.gamestate.checks.list() if c.open]
    assert (call.kind, call.subject, call.dc, call.characters) == ("save", "con", 11, [lyra])
    assert call.purpose == "Sewer Plague"
    # Lyra rolls a 4 on her sheet; the form shows it, and applying uses it.
    registry.checks.answer(lyra, "save", "con", 4)
    waiting = await ac.post(
        "/gm/afflictions/afflict", data={**disease, "call": call.id, "op": "wait"}
    )
    assert '<b class="mono">4</b>' in waiting.text
    done = await ac.post(
        "/gm/afflictions/afflict", data={**disease, "call": call.id, "op": "apply"}
    )
    assert "carries it" in done.text
    assert [a.name for a in registry.gamestate.characters.get(lyra).state.afflictions] == [
        "Sewer Plague"
    ]
    assert not registry.gamestate.checks.get(call.id).open
    # A closed call afflicts no one again.
    again = await ac.post(
        "/gm/afflictions/afflict", data={**disease, "call": call.id, "op": "apply"}
    )
    assert "Players roll the saves" in again.text
    assert len(registry.gamestate.characters.get(lyra).state.afflictions) == 1


def _dm():  # noqa: ANN202 - the play rules' actor
    from app.services.play import Actor

    return Actor("DM", username="dm")
