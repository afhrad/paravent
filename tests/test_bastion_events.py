"""What bastion events bring (app/services/bastion_events.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.models.gamestate import Bastion, BastionFacility
from app.services.bastion_events import EventTools, item_ref
from app.services.bastions import BastionError, BastionService, event_rows
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, table

__all__ = ["table"]

ROWS = [
    ["01–10", "{@area Attack|78e|x}"],
    ["11–20", "{@area Friendly Visitors|791|x}"],
    ["21–30", "{@area Guest|792|x}"],
    ["31–40", "{@area Criminal Hireling|78f|x}"],
    ["41–50", "{@area Extraordinary Opportunity|790|x}"],
    ["51–60", "{@area Request for Aid|796|x}"],
    ["61–70", "{@area Magical Discovery|794|x}"],
    ["71–80", "{@area Treasure|797|x}"],
    ["81–90", "{@area Lost Hirelings|793|x}"],
]
TABLES = {
    "Guest": [
        ["1", "The guest offers you a gift of {@dice 1d6 × 100} GP."],
        ["2", "The guest is a mercenary, giving you one additional Bastion Defender."],
        ["3", "If your Bastion is attacked, it defends your Bastion."],
    ],
    "Treasure": [
        ["01–50", "Roll on the 25 GP {@book Art Objects|XDMG|6|25 GP Art Objects} table."],
        ["51–00", "Roll on an Uncommon Magic Items table of your choice."],
    ],
}


class Ledger:
    """Tools that write down what they were asked to do."""

    def __init__(self, purse: int = 10**6) -> None:
        self.purse = purse
        self.items: list[str] = []

    def tools(self) -> EventTools:
        def pay(_cid: str, copper: int, _why: str) -> bool:
            if copper > self.purse:
                return False
            self.purse -= copper
            return True

        return EventTools(
            earn=lambda _cid, copper: setattr(self, "purse", self.purse + copper),
            pay=pay,
            give=lambda _cid, ref, _n: self.items.append(ref),
            table=lambda caption: TABLES.get(caption, []),
            art=lambda value: f"Art {value}|XDMG",
            magic=lambda _cid, rarity: f"Some {rarity} item|XDMG",
        )


def _service(tmp_path: Path, rolls: list[int], ledger: Ledger) -> BastionService:
    store = GameStateStore(tmp_path)
    service = BastionService(
        store, None, events=lambda: event_rows(ROWS), tools=ledger.tools(), dice=FixedDice(rolls)
    )
    store.bastions.upsert(
        service.of("a").model_copy(
            update={
                "facilities": [
                    BastionFacility(ref="Smithy|XDMG"),
                    BastionFacility(ref="Garden|XDMG"),
                ],
                "defenders": 3,
            }
        )
    )
    return service


def _turn(service: BastionService):  # type: ignore[no-untyped-def]
    service.maintain("a", True)
    return service.turn("a", "Ana", None, "dm", "DM")


def test_visitors_guests_treasure_and_lost_hirelings(tmp_path: Path) -> None:
    ledger = Ledger(0)
    # Friendly Visitors (15): 1d6 4 → 400 gp.
    after = _turn(_service(tmp_path / "1", [15, 4], ledger))
    assert after.event_yields == ["gold|40000"] and ledger.purse == 40000
    # Guest (25), row 2: a mercenary joins.
    after = _turn(_service(tmp_path / "2", [25, 2], ledger))
    assert after.defenders == 4 and "defender|1" in after.event_yields
    # Guest, row 3: a friendly monster guards — the next attack costs nothing.
    service = _service(tmp_path / "3", [25, 3, 5], ledger)
    assert _turn(service).guardian
    attacked = _turn(service)
    assert attacked.defenders == 3 and not attacked.guardian
    assert attacked.event_yields == ["guarded|1"]
    # Treasure (75): d100 20 → a 25 gp art object; 80 → an uncommon magic item.
    _turn(_service(tmp_path / "4", [75, 20], ledger))
    _turn(_service(tmp_path / "5", [75, 80], ledger))
    assert ledger.items == ["Art 25|XDMG", "Some uncommon item|XDMG"]
    # Lost Hirelings (85): the second facility shuts.
    after = _turn(_service(tmp_path / "6", [85, 2], ledger))
    assert [f.shut for f in after.facilities] == [False, True]


def test_decisions(tmp_path: Path) -> None:
    ledger = Ledger(30000)
    # Criminal Hireling (35): a bribe of 1d6 (5) × 100 gp; the purse is short.
    service = _service(tmp_path / "1", [35, 5], ledger)
    assert _turn(service).offer == "bribe|50000"
    with pytest.raises(BastionError):
        service.answer("a", "Ana", None, True, "", "dm", "DM")
    declined = service.answer("a", "Ana", None, False, "", "dm", "DM")
    assert declined.offer == "" and any(f.shut for f in declined.facilities)
    # Extraordinary Opportunity (45) paid: the table rolled again, skipping itself (45),
    # landing on Friendly Visitors (15, 1d6 = 2).
    ledger.purse = 60000
    service = _service(tmp_path / "2", [45, 45, 15, 2], ledger)
    assert _turn(service).offer == "opportunity|50000"
    again = service.answer("a", "Ana", None, True, "", "dm", "DM")
    assert "again|Friendly Visitors" in again.event_yields and ledger.purse == 10000 + 20000
    # Request for Aid (55): two defenders roll 3 + 4 = 7: half of 1d6 (6) × 100, one dies.
    service = _service(tmp_path / "3", [55, 3, 4, 6], ledger)
    assert _turn(service).offer == "aid"
    helped = service.answer("a", "Ana", None, True, "2", "dm", "DM")
    assert helped.defenders == 2 and helped.event_yields[-1] == "aid|7|30000|1"
    # Magical Discovery (65): the item chosen goes into the pack.
    service = _service(tmp_path / "4", [65], ledger)
    assert _turn(service).offer == "discovery"
    service.answer("a", "Ana", None, True, "Potion of Climbing|XDMG", "dm", "DM")
    assert ledger.items[-1] == "Potion of Climbing|XDMG"


def test_item_refs() -> None:
    assert item_ref("{@item Silver ewer|XDMG}") == "Silver ewer|XDMG"
    assert item_ref("{@item Gold bracelet}") == "Gold bracelet|XDMG"
    assert item_ref("plain") == ""


@requires_corpus
async def test_a_discovery_takes_only_a_potion_or_scroll(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    registry.gamestate.bastions.upsert(
        Bastion(id=lyra, facilities=[BastionFacility(ref="Smithy|XDMG")], offer="discovery")
    )
    url = f"/characters/{lyra}/bastion"
    before = len(registry.gamestate.characters.get(lyra).inventory)
    await ac.post(url, data={"op": "offer", "accept": "1", "value": "Vorpal Sword|DMG"})
    assert len(registry.gamestate.characters.get(lyra).inventory) == before
    assert registry.bastions.of(lyra).offer == "discovery"  # still waiting
    ref, _name = registry.discoveries(registry.gamestate.characters.get(lyra).world_id)[0]
    await ac.post(url, data={"op": "offer", "accept": "1", "value": ref})
    pc = registry.gamestate.characters.get(lyra)
    assert any(i.item.reference == ref for i in pc.inventory)
