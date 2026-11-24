"""Hazards by level (app/services/hazards.py): the guides' damage and DCs at the party's
level, a fall by its height, and the party view's hazard sprung like a trap."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from app.services.hazards import Hazard, HazardError, effect, party_level, scale
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table  # noqa: F401

# The rows as the books print them (numbers only, laid out as the guides lay them out).
CLASSIC = {
    "Damage Severity by Level": [
        ["1st-4th", "{@damage 1d10}", "{@damage 2d10}", "{@damage 4d10}"],
        ["5th-10th", "{@damage 2d10}", "{@damage 4d10}", "{@damage 10d10}"],
    ],
    "Trap Save DCs and Attack Bonuses": [
        ["Setback", "10-11", "+3 to +5"],
        ["Dangerous", "12-15", "+6 to +8"],
        ["Deadly", "16-20", "+9 to +12"],
    ],
}
NEW = {
    "Damage Severity and Level": [
        ["1–4", "5 ({@damage 1d10})", "11 ({@damage 2d10})"],
        ["5–10", "11 ({@damage 2d10})", "22 ({@damage 4d10})"],
    ],
    "Building a Trap": [
        ["1–4", "{@hit 4}", "10–12", "5", "{@hit 8}", "13–15", "11"],
        ["5–10", "{@hit 4}", "12–14", "11", "{@hit 8}", "15–17", "22"],
    ],
}


def _from(tables: dict[str, list[list[str]]]):  # type: ignore[no-untyped-def]
    return lambda caption, _book: tables.get(caption)


class TestScale:
    def test_the_2014_guide(self) -> None:
        found = scale("classic", 7, _from(CLASSIC))
        assert found is not None and found.severities == ("setback", "dangerous", "deadly")
        assert found.damage == {"setback": "2d10", "dangerous": "4d10", "deadly": "10d10"}
        assert found.dc == {"setback": 10, "dangerous": 13, "deadly": 18}

    def test_the_2024_guide(self) -> None:
        found = scale("one", 3, _from(NEW))
        assert found is not None and found.severities == ("nuisance", "deadly")
        assert found.damage == {"nuisance": "1d10", "deadly": "2d10"}
        assert found.dc == {"nuisance": 11, "deadly": 14}
        assert scale("one", 3, _from({})) is None
        assert party_level([3, 4, 6]) == 4 and party_level([]) == 1


class TestEffect:
    def test_a_hazard_behind_its_save(self) -> None:
        found = scale("classic", 2, _from(CLASSIC))
        made = effect(Hazard("fire", "dangerous", "dex", "fire"), found)
        save = made.saves[0]
        assert (save.dc, save.ability, save.damage, save.damage_type, save.half) == (
            13,
            "dex",
            "2d10",
            "fire",
            True,
        )
        # The gamemaster's own DC and dice win.
        own = effect(Hazard("fire", "dangerous", "con", "cold", False, 16, "3d6"), found)
        assert (own.saves[0].dc, own.saves[0].damage, own.saves[0].half) == (16, "3d6", False)
        # No save: every creature caught takes it.
        assert effect(Hazard("acid", "setback", "", "acid"), found).damage == (("1d10", "acid"),)

    def test_a_fall(self) -> None:
        assert effect(Hazard("falling", feet=30), None).damage == (("3d6", "bludgeoning"),)
        assert effect(Hazard("falling", feet=500), None).damage == (("20d6", "bludgeoning"),)
        with pytest.raises(HazardError):
            effect(Hazard("falling", feet=5), None)

    def test_what_does_not_work_out(self) -> None:
        with pytest.raises(HazardError):  # no table and no dice
            effect(Hazard("fire", "deadly"), None)
        with pytest.raises(HazardError):
            effect(Hazard("fire", dice="lots", dc=12), None)
        with pytest.raises(HazardError):  # dice but no DC for the save
            effect(Hazard("fire", dice="2d10"), None)
        hazard = Hazard("cold", "deadly", "con", "cold", True, 15, "4d10")
        assert Hazard.from_spec(hazard.spec()) == hazard
        with pytest.raises(HazardError):
            Hazard.from_spec("earthquake:deadly")


@requires_corpus
class TestOnThePartyView:
    async def test_a_hazard_sprung_on_a_character(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.table.select(world)
        page = (await ac.get(f"/gm?world={world}")).text
        assert 'id="tool-hazard"' in page and 'name="severity"' in page
        # The world declares the 2024 guide: a deadly fire at level 10.
        form = (
            await ac.get(
                "/gm/hazards/form",
                params={"world": world, "kind": "fire", "severity": "deadly", "half": "1"},
            )
        ).text
        assert "Fire (Deadly)" in form and "DC 16" in form and "4d10 fire" in form
        spec = re.search(r'type="hidden" name="source" value="([^"]+)"', form)
        assert spec is not None
        before = registry.gamestate.characters.get(lyra).state.hp
        result = (
            await ac.post(
                "/gm/traps/spring",
                data={
                    "world": world,
                    "category": "improvised",
                    "name": "Fire (Deadly)",
                    "source": spec[1],
                    "character": lyra,
                    "op": "spring",
                },
            )
        ).text
        assert "Fire (Deadly)" in result and "Lyra" in result
        assert registry.gamestate.characters.get(lyra).state.hp < before
        # A fall, in feet; a height too low comes back with the reason.
        fall = (
            await ac.get(
                "/gm/hazards/form", params={"world": world, "kind": "falling", "feet": "40"}
            )
        ).text
        assert "A fall of 40 ft." in fall and "4d6" in fall
        short = (
            await ac.get(
                "/gm/hazards/form", params={"world": world, "kind": "falling", "feet": "5"}
            )
        ).text
        assert "at least 10 feet" in short
        # Switched off: no tool, no form.
        registry.gamestate.worlds.update(world, features=["travel"])
        assert 'id="tool-hazard"' not in (await ac.get(f"/gm?world={world}")).text
        off = await ac.get("/gm/hazards/form", params={"world": world, "fresh": "1"})
        assert 'name="kind"' not in off.text
