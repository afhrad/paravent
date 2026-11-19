"""Magic items' charges and attunement (app/services/item_charges.py)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.pc import InventoryItem
from app.services.afflictions import DAY
from app.services.item_charges import Charges, attunement_slots, charges_of, crossings
from app.services.play import Actor, PlayError
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]

WAND = "Wand of Magic Missiles|XDMG"


def test_reading_an_items_charges() -> None:
    wand = {"charges": 7, "recharge": "dawn", "rechargeAmount": "{@dice 1d6 + 1}"}
    assert charges_of(wand) == Charges(7, "dawn", "1d6+1")
    assert charges_of({"charges": 3, "recharge": "dawn", "rechargeAmount": 3}) == Charges(
        3, "dawn", "3"
    )
    # No amount: all of them come back.
    assert charges_of({"charges": 10, "recharge": "dusk"}) == Charges(10, "dusk", "")
    # A count left to dice, or none, is nothing to track.
    assert charges_of({"charges": "{@dice 1d3}"}) is None
    assert charges_of({"name": "Rope"}) is None and charges_of(None) is None


def test_attunement_slots_and_the_hours_that_bring_charges_back() -> None:
    assert attunement_slots({"wizard": 10}) == 3
    assert attunement_slots({"artificer": 9}) == 3
    assert attunement_slots({"artificer": 10, "wizard": 2}) == 4
    assert attunement_slots({"artificer": 18}) == 6
    eight = 8 * 60  # Day 1, 08:00
    assert crossings("dawn", eight, DAY + 5 * 60) == 0  # to 05:00 the next day
    assert crossings("dawn", eight, DAY + 7 * 60) == 1
    assert crossings("dawn", eight, 3 * DAY + eight) == 3
    assert crossings("dusk", eight, 20 * 60) == 1
    assert crossings("midnight", eight, DAY) == 1
    assert crossings("special", eight, 5 * DAY) == 0


def _dm() -> Actor:
    return Actor("DM", username="dm")


def _give(registry: StoreRegistry, character_id: str, *items: InventoryItem) -> None:
    def change(pc):  # noqa: ANN001, ANN202 - the store's mutation callback
        pc.inventory.extend(items)

    registry.gamestate.characters.mutate(character_id, change)


@requires_corpus
async def test_charges_spent_on_the_sheet_come_back_at_dawn(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    _give(registry, lyra, InventoryItem.model_validate({"item": WAND}))
    pc = registry.gamestate.characters.get(lyra)
    index = len(pc.inventory) - 1
    wand = registry.characters.sheet(pc).inventory[index]
    assert wand.charges == Charges(7, "dawn", "1d6+1") and wand.charges_left == 7
    page = (await ac.get(f"/characters/{lyra}?view=gear")).text
    assert "7/7" in page and "1W6+1 zurück bei Sonnenaufgang" not in page  # English reader
    assert "1d6+1 back at dawn" in page
    for _ in range(3):
        await ac.post(f"/characters/{lyra}/act/charge", data={"index": index, "op": "spend"})
    assert registry.gamestate.characters.get(lyra).inventory[index].charges_used == 3
    # The gamemaster's table shows the wand and what is left of it.
    card = (await ac.get("/gm")).text
    assert "pc-magic-item" in card and "4/7" in card
    with pytest.raises(PlayError):
        registry.characters.act_for(lyra, "charge", {"index": str(index), "n": "5"}, _dm())
    # Day 1, 08:00 → Day 2, 08:00: one dawn, 1d6 + 1 (at least 2) back.
    world = registry.gamestate.characters.get(lyra).world_id or ""
    registry.clock.advance(world, DAY, "dm", "DM")
    assert registry.gamestate.characters.get(lyra).inventory[index].charges_used <= 1
    log = (await ac.get(f"/log/panel?world={world}")).text
    assert "Spends 1 of Wand of Magic Missiles" in log and "Wand of Magic Missiles regains" in log


@requires_corpus
async def test_attuning_up_to_the_slots(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    # Lyra wears an attuned Ring of Protection; three more items ask for attunement.
    _give(
        registry,
        lyra,
        *(
            InventoryItem.model_validate({"item": ref, "equipped": True})
            for ref in (
                "Cloak of Protection|XDMG",
                "Ruby of the War Mage|XDMG",
                "Periapt of Wound Closure|XDMG",
            )
        ),
    )
    pc = registry.gamestate.characters.get(lyra)
    sheet = registry.characters.sheet(pc)
    assert sheet.attunement_slots == 3
    cloak, wand, periapt = (len(pc.inventory) - 3 + n for n in range(3))
    assert sheet.inventory[cloak].attunable and not sheet.inventory[cloak].attuned
    page = (await ac.get(f"/characters/{lyra}?view=gear")).text
    assert "Attuned 1 of 3" in page
    for index in (cloak, wand):
        registry.characters.act_for(lyra, "attune", {"index": str(index)}, _dm())
    with pytest.raises(PlayError):  # a fourth is one too many
        registry.characters.act_for(lyra, "attune", {"index": str(periapt)}, _dm())
    # Ending one makes room.
    registry.characters.act_for(lyra, "attune", {"index": str(cloak)}, _dm())
    registry.characters.act_for(lyra, "attune", {"index": str(periapt)}, _dm())
    held = [i.item.name for i in registry.gamestate.characters.get(lyra).inventory if i.attuned]
    assert held == ["Ring of Protection", "Ruby of the War Mage", "Periapt of Wound Closure"]
    log = (await ac.get(f"/log/panel?world={pc.world_id}")).text
    assert "Attunes to Cloak of Protection" in log
    assert "Ends the attunement to Cloak of Protection" in log
