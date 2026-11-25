"""The planes (app/services/planes.py): the plane the party is on, and its rules carried
out after long rests, by the hour, on leaving and at the gamemaster's call."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import SourceSet
from app.services.dice import Dice
from app.services.planes import PLANES, RULES, befell_of, roll_on, rules_of
from app.store.protocol import log_key
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table  # noqa: F401


def test_a_planes_rules_by_the_worlds_books() -> None:
    assert all(key in RULES for keys in PLANES.values() for key in keys)
    only_2024 = rules_of("feywild", lambda book: book == "XDMG")
    assert [r.key for r in only_2024] == ["memory", "time_warp"]
    assert rules_of("abyss", lambda book: book == "XDMG") == []
    assert [r.key for r in rules_of("abyss", lambda book: True)] == ["dissonance", "corruption"]
    found = roll_on(
        (("XDMG", "Nothing"), ("DMG", "T")),
        lambda caption, books: [["1-20", "{@b Lost} {@dice 1d1} day"]] if caption == "T" else None,
        Dice(),
    )
    assert found is not None and found[1] == "Lost 1 day"
    read = befell_of("plane_rule:despair:7:10:1:4:Dread: a flaw", "character:x", "Lyra")
    assert read is not None and (read.character_id, read.roll, read.struck) == ("x", 7, True)
    assert read.text == "Dread: a flaw" and befell_of("upkeep:x", "", "") is None


def _log(registry: StoreRegistry, world: str) -> list[str]:
    return [e.what for e in registry.protocol.store.entries(log_key(world))]


@requires_corpus
class TestAtTheTable:
    async def test_the_abyss_pandemonium_and_the_feywild(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.table.select(world)
        registry.gamestate.worlds.update(
            world, source_set=SourceSet(sources=["PHB", "MM", "XGE", "XDMG", "DMG"])
        )
        page = (await ac.get(f"/gm?world={world}")).text
        assert 'id="tool-plane"' in page and "Material Plane" in page
        registry.planes._save = lambda character, ability: 1  # every save fails
        # The Abyss: its rules linked to the guide.
        await ac.post(f"/gm/plane/{world}/enter", data={"plane": "abyss"})
        assert registry.planes.of(world) == "abyss"
        page = (await ac.get(f"/gm?world={world}")).text
        assert "Plane: The Abyss" in page and "Abyssal Corruption" in page
        assert "/books/DMG/" in page and "#h-optional-rule-abyssal-corruption" in page
        # Psychic Dissonance spares Lyra (unticked); a long rest corrupts her.
        await ac.post(f"/gm/plane/{world}/spare", data={"rule": "dissonance"})
        assert registry.planes.spared(world, "dissonance") == [lyra]
        await ac.post(f"/characters/{lyra}/act/rest", data={"kind": "long"})
        log = _log(registry, world)
        assert any(w.startswith("plane_rule:corruption:1:10:1:") for w in log)
        assert not any(w.startswith("plane_rule:dissonance") for w in log)
        [held] = [a for a in registry.gamestate.characters.get(lyra).state.afflictions]
        assert (held.kind, held.name, held.ability, held.dc, held.rule) == (
            "plane",
            "corruption",
            "cha",
            15,
            "one",
        )
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert "Abyssal Corruption" in sheet
        panel = (await ac.get(f"/log/panel?world={world}")).text
        assert "Abyssal Corruption: Lyra" in panel and "now on The Abyss" in panel
        # Pandemonium: three hours, three failed saves, three levels of exhaustion.
        before = registry.gamestate.characters.get(lyra).state.exhaustion
        await ac.post(f"/gm/plane/{world}/enter", data={"plane": "pandemonium"})
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": "190"})
        assert registry.gamestate.characters.get(lyra).state.exhaustion == min(6, before + 3)
        # The Feywild: leaving after two days forgets it and warps time.
        await ac.post(f"/gm/plane/{world}/enter", data={"plane": "feywild"})
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": str(2 * 24 * 60)})
        done = await ac.post(f"/gm/plane/{world}/enter", data={"plane": ""})
        assert "planed=2" in done.headers["location"]
        log = _log(registry, world)
        assert any(w.startswith("plane_rule:memory:1:10:1:") for w in log)
        assert any(w.startswith("plane_rule:time_warp::::") for w in log)
        page = (await ac.get(done.headers["location"])).text
        assert "Memory Loss: Lyra" in page and "Time Warp:" in page
        # The Shadowfell, at the gamemaster's call: despair, carried.
        await ac.post(f"/gm/plane/{world}/enter", data={"plane": "shadowfell"})
        await ac.post(f"/gm/plane/{world}/call", data={"rule": "despair"})
        names = [a.name for a in registry.gamestate.characters.get(lyra).state.afflictions]
        assert "despair" in names
        # Switched off: back on the Material Plane, the tool gone.
        registry.gamestate.worlds.update(world, features=["travel"])
        registry.features_changed(world, "dm", "DM")
        assert registry.planes.of(world) == ""
        assert 'id="tool-plane"' not in (await ac.get(f"/gm?world={world}")).text
        refused = await ac.post(f"/gm/plane/{world}/enter", data={"plane": "abyss"})
        assert refused.status_code == 404
