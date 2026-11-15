"""Creatures a spell summons, scaled to its level (app/services/summons.py), in a fight."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.gamestate import Role, SourceSet
from app.models.pc import PlayerCharacter
from app.services.monster_attacks import Damage, attack_of
from app.services.play import Actor
from app.services.summons import scaled, summoning
from app.store.compendium import CompendiumStore
from tests.conftest import requires_corpus
from tests.test_polymorph import GM, PASSWORD, Fight

pytestmark = requires_corpus


@pytest.fixture
def fight(tmp_path: Path, corpus: Path) -> Fight:
    return Fight(tmp_path / "lib", corpus)


@pytest.fixture
async def gm(fight: Fight) -> AsyncIterator[AsyncClient]:
    fight.registry.auth.create_user(GM, PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = fight.registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": GM, "password": PASSWORD})
        yield ac


def _creature(compendium: CompendiumStore, name: str, source: str) -> dict[str, Any]:
    monster = next(m for m in compendium.monsters() if m.name == name and m.source == source)
    data: dict[str, Any] = monster.entry.model_dump(by_alias=True, exclude_none=True)
    return data


class TestScaling:
    def test_a_bestial_spirit_at_4th_level(self, loaded_compendium: CompendiumStore) -> None:
        data = _creature(loaded_compendium, "Bestial Spirit", "XPHB")
        found = summoning(data)
        assert found is not None and found.spell == "Summon Beast|XPHB"
        assert (found.min_level, found.variants) == (2, ("Air", "Land", "Water"))
        air = scaled(data, 4, "Air", 7, 15)
        assert (air["name"], air["ac"], air["hp"]["average"]) == ("Bestial Spirit (Air)", [15], 30)
        assert air["action"][0]["entries"] == ["The spirit makes 2 Rend attacks."]
        low = scaled(data, 3, "Air", 7, 15)
        assert low["action"][0]["entries"] == ["The spirit makes 1 Rend attack."]
        rend = attack_of(air["action"][1])
        assert rend is not None and rend.hit == 7
        assert rend.damage == (Damage("1d8+4+4", "piercing"),)
        land = scaled(data, 4, "Land", 7, 15)
        assert land["hp"]["average"] == 40
        assert [t["name"] for t in land["trait"]] == ["Pack Tactics"]

    def test_every_summon_scales_to_plain_numbers(self, loaded_compendium: CompendiumStore) -> None:
        checked = 0
        for monster in loaded_compendium.monsters():
            data = monster.entry.model_dump(by_alias=True, exclude_none=True)
            found = summoning(data)
            if found is None:
                continue
            for variant in found.variants or ("",):
                out = scaled(data, found.min_level + 1, variant, 7, 15)
                text = json.dumps(out)
                assert isinstance(out["ac"][0], int), (found.name, variant)
                assert isinstance(out["hp"]["average"], int), (found.name, variant)
                for left in ("summonSpellLevel", "YourSpell", "your spell save DC"):
                    assert left not in text, (found.name, variant, left)
                checked += 1
        assert checked > 50

    def test_dice_that_grow_with_the_level(self) -> None:
        data = {
            "name": "X",
            "source": "T",
            "summonedBySpell": "S|T",
            "action": [
                {"name": "Bite", "entries": ["{@damage (summonSpellLevel - 3)d12 + 3|2d12 + 3}"]}
            ],
        }
        assert scaled(data, 5, "", None, None)["action"][0]["entries"] == ["{@damage 2d12 + 3}"]


class TestInAFight:
    def _druid_world(self, fight: Fight) -> None:
        world = fight.now.world_id
        assert world is not None
        fight.registry.gamestate.worlds.update(
            world, source_set=SourceSet(sources=["PHB", "MM", "XPHB", "XMM"])
        )

    def test_a_spirit_joins_beside_its_caster_and_goes_with_the_concentration(
        self, fight: Fight
    ) -> None:
        combats = fight.registry.combats
        fight.concentrate_lyra("Summon Beast|XPHB")
        lyra = fight.lyra_row()
        combats.summon(
            fight.combat.id,
            lyra,
            "Bestial Spirit|XPHB",
            "Summon Beast|XPHB",
            4,
            GM,
            variant="Land",
            attack=9,
            dc=17,
        )
        spirit = next(c for c in fight.now.combatants if c.summon is not None)
        caster = next(c for c in fight.now.combatants if c.id == lyra)
        assert (spirit.ac, spirit.hp, spirit.ally) == (15, 40, True)
        assert spirit.initiative == caster.initiative
        statblock = combats.summoned_data(spirit)
        assert statblock is not None and "{@hit 9}" in json.dumps(statblock)
        characters = fight.registry.characters
        characters.play.act(
            fight.lyra.id, "concentration", {}, characters.sheet(fight.pc()), Actor(GM)
        )
        assert not any(c.summon is not None for c in fight.now.combatants)

    def test_a_monsters_summon_goes_when_it_stops_concentrating(self, fight: Fight) -> None:
        combats = fight.registry.combats
        bugbear = fight.member("Bugbear")
        combats.concentrate(fight.combat.id, bugbear, "Summon Beast", GM)
        combats.summon(
            fight.combat.id,
            bugbear,
            "Bestial Spirit|XPHB",
            "Summon Beast|XPHB",
            2,
            GM,
            variant="Air",
        )
        spirit = next(c for c in fight.now.combatants if c.summon is not None)
        assert spirit.ally is False  # on its caster's side
        combats.concentrate(fight.combat.id, bugbear, None, GM)
        assert not any(c.summon is not None for c in fight.now.combatants)

    async def test_a_druid_summons_from_the_sheet(self, fight: Fight, gm: AsyncClient) -> None:
        self._druid_world(fight)
        store = fight.registry.gamestate
        druid = store.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Sanna",
                    "world_id": fight.now.world_id,
                    "classes": [{"class": "Druid|XPHB", "level": 5}],
                    "abilities": {"con": 12, "wis": 16},
                    "spells": {"prepared": ["Summon Beast|XPHB"]},
                }
            )
        )
        fight.registry.combats.add_character(fight.combat.id, druid.id, GM)
        sheet = (await gm.get(f"/characters/{druid.id}")).text
        assert f"/characters/{druid.id}/summon" in sheet and 'value="Air"' in sheet
        await gm.post(
            f"/characters/{druid.id}/summon",
            data={"spell": "Summon Beast|XPHB", "level": "3", "variant": "Water"},
        )
        spirit = next(c for c in fight.now.combatants if c.summon is not None)
        assert spirit.label == "Bestial Spirit (Water)" and spirit.hp == 35
        assert store.characters.get(druid.id).state.slots_used == {"3": 1}
