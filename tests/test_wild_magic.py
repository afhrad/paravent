"""Wild magic (app/services/wild_magic.py): a zone where a spent spell slot may surge,
and surges rolled on the characters ticked, both on the world's Wild Magic Surge table."""

from __future__ import annotations

from httpx import AsyncClient

from app.services.book_tables import roll_row
from app.services.dice import Dice
from app.services.wild_magic import surge_of
from app.store.protocol import log_key
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table  # noqa: F401


class Highest(Dice):
    """Every die comes up at its highest: a zone's d20 always surges."""

    def die(self, faces: int) -> int:
        return faces


def test_a_row_by_the_tables_own_die() -> None:
    rows = [["01-50", "Low"], ["51-00", "High"]]
    assert roll_row(rows, Highest()) == (100, ["51-00", "High"])
    assert surge_of("wild_surge:42:zone:You turn blue: for a while.") == (
        42,
        "zone",
        "You turn blue: for a while.",
    )
    assert surge_of("upkeep:x") is None


@requires_corpus
class TestAtTheTable:
    async def test_surges_and_the_zone(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.table.select(world)
        page = (await ac.get(f"/gm?world={world}")).text
        assert 'id="tool-wild"' in page and "Roll a surge" in page
        # A surge on a ticked character, shown in the dialog and told in the log.
        done = await ac.post(f"/gm/wild/{world}/surge", data={"to": lyra})
        assert "surged=1" in done.headers["location"]
        page = (await ac.get(done.headers["location"])).text
        assert "d100 " in page
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "Wild magic surges around Lyra" in log
        # The zone: a slot spent from the sheet rolls the d20, and surges on a 20.
        await ac.post(f"/gm/wild/{world}/zone", data={"on": "1"})
        assert registry.wild_magic.zone(world)
        assert "Wild magic: zone" in (await ac.get(f"/gm?world={world}")).text
        registry.wild_magic._dice = Highest()
        await ac.post(f"/characters/{lyra}/act/cast", data={"spell": "Mage Armor|PHB"})
        surges = [e for e in registry.protocol.store.entries(log_key(world)) if surge_of(e.what)]
        assert len(surges) == 2 and surge_of(surges[-1].what)[1] == "zone"  # type: ignore[index]
        assert surge_of(surges[-1].what)[0] == 100  # type: ignore[index]
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "spell surges in the wild magic zone" in log
        assert "enters a wild magic zone" in log
        # A cantrip spends no slot: no roll.
        await ac.post(f"/characters/{lyra}/act/cast", data={"spell": "Fire Bolt|PHB"})
        assert (
            sum(1 for e in registry.protocol.store.entries(log_key(world)) if surge_of(e.what)) == 2
        )
        # Out of the zone: no more surges from casting.
        await ac.post(f"/gm/wild/{world}/zone", data={})
        assert not registry.wild_magic.zone(world)
        await ac.post(f"/characters/{lyra}/act/cast", data={"spell": "Mage Armor|PHB"})
        assert (
            sum(1 for e in registry.protocol.store.entries(log_key(world)) if surge_of(e.what)) == 2
        )
        # Switching hazards off takes the party out of a zone and the tool away.
        await ac.post(f"/gm/wild/{world}/zone", data={"on": "1"})
        registry.gamestate.worlds.update(world, features=["travel"])
        registry.features_changed(world, "dm", "DM")
        assert not registry.wild_magic.zone(world)
        assert 'id="tool-wild"' not in (await ac.get(f"/gm?world={world}")).text
        assert (await ac.post(f"/gm/wild/{world}/surge", data={"to": lyra})).status_code == 404
