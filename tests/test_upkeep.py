"""Running costs (app/services/upkeep.py): the guide's maintenance costs, a property given
to a character, paid from its purse each day the clock passes, owed when it cannot."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import SourceSet
from app.models.pc import Currency
from app.services.clock import DAY
from app.services.upkeep import costs, priced
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table  # noqa: F401


def test_the_guides_table() -> None:
    rows = [["Abbey", "20 gp", "5", "25"], ["Farm", "5 sp", "1", "2"], ["Header", "-"]]
    found = costs(rows)
    assert found == [("Abbey", 2000), ("Farm", 50)]
    assert priced(found, "farm") == 50 and priced(found, "Palace") is None


@requires_corpus
class TestAtTheTable:
    async def test_a_keep_costs_its_owner_every_day(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.table.select(world)
        registry.gamestate.characters.update(lyra, currency=Currency(gp=150))
        page = (await ac.get(f"/gm?world={world}")).text
        assert 'id="tool-upkeep"' in page and "Keep or small castle" in page
        # With the 2014 guide declared too: the 2024 fortifications first, then its farm.
        assert "Farm" not in [n for n, _ in registry.upkeep_costs(world)]
        registry.gamestate.worlds.update(
            world, source_set=SourceSet(sources=["PHB", "MM", "XGE", "XDMG", "DMG"])
        )
        names = [n for n, _ in registry.upkeep_costs(world)]
        assert names[0] == "Fortified outpost or watchtower" and "Farm" in names
        # The world declares the 2024 guide too: its table prices the keep at 100 GP.
        await ac.post(
            f"/gm/upkeep/{world}/give",
            data={"character": lyra, "kind": "Keep or small castle", "name": "Ravenhold"},
        )
        [keep] = registry.upkeep.of_character(lyra)
        assert (keep.name, keep.per_day) == ("Ravenhold", 10_000)
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert "Ravenhold" in sheet and "100 gp a day" in sheet
        # Two days: 150 gp pays one and a half; the rest is owed.
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": str(2 * DAY)})
        purse = registry.gamestate.characters.get(lyra).currency
        assert (purse.gp, purse.sp, purse.cp, purse.pp) == (0, 0, 0, 0)
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "pays 150 gp upkeep for Ravenhold (2 days)" in log and "50 gp owed" in log
        # A price by hand; one without a name or price is refused.
        await ac.post(
            f"/gm/upkeep/{world}/give", data={"character": lyra, "name": "Stall", "gp": "1.5"}
        )
        assert [p.per_day for p in registry.upkeep.of_character(lyra)] == [10_000, 150]
        refused = await ac.post(f"/gm/upkeep/{world}/give", data={"character": lyra})
        assert "upkeep=bad" in refused.headers["location"]
        # Taken back: no more costs.
        await ac.post(f"/gm/upkeep/{world}/remove", data={"property": keep.id})
        assert [p.name for p in registry.upkeep.of_character(lyra)] == ["Stall"]
        # Switched off: no tool, no charge.
        registry.gamestate.worlds.update(world, features=["travel"])
        registry.gamestate.characters.update(lyra, currency=Currency(gp=10))
        await ac.post(f"/gm/clock/{world}/advance", data={"minutes": str(DAY)})
        assert registry.gamestate.characters.get(lyra).currency.gp == 10
        assert 'id="tool-upkeep"' not in (await ac.get(f"/gm?world={world}")).text
        assert "Stall" not in (await ac.get(f"/characters/{lyra}")).text
