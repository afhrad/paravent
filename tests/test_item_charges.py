"""Magic items' charges and attunement (app/services/item_charges.py)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.pc import InventoryItem
from app.services.afflictions import DAY
from app.services.item_charges import (
    Charges,
    SpellTerms,
    attunement_allows,
    attunement_slots,
    charges_of,
    crossings,
    spell_terms,
)
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


@requires_corpus
async def test_spells_cast_from_an_items_charges(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    _give(
        registry,
        lyra,
        InventoryItem.model_validate({"item": "Staff of Fire|XDMG", "equipped": True}),
        InventoryItem.model_validate({"item": "Hat of Disguise|XDMG"}),
    )
    pc = registry.gamestate.characters.get(lyra)
    staff, healing = len(pc.inventory) - 2, len(pc.inventory) - 1
    # Not attuned yet: the staff casts nothing.
    assert not [x for x in registry.characters.sheet(pc).item_spells if x.index == staff]
    registry.characters.act_for(lyra, "attune", {"index": str(staff)}, _dm())
    sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
    costs = {x.spell.name: x.cost for x in sheet.item_spells if x.index == staff}
    assert costs == {"Burning Hands": 1, "Fireball": 3, "Wall of Fire": 4}
    page = (await ac.get(f"/characters/{lyra}?tab=spells")).text
    assert "From items" in page and "3 charges" in page
    slots_before = dict(registry.gamestate.characters.get(lyra).state.slots_used)
    fireball = next(x for x in sheet.item_spells if x.spell.name == "Fireball")
    registry.characters.act_for(
        lyra, "cast", {"spell": fireball.spell.reference, "level": fireball.key}, _dm()
    )
    after = registry.gamestate.characters.get(lyra)
    assert after.inventory[staff].charges_used == 3
    assert dict(after.state.slots_used) == slots_before  # no slot spent
    # A Hat of Disguise casts Disguise Self at will: nothing is spent.
    registry.characters.act_for(lyra, "attune", {"index": str(healing)}, _dm())
    sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
    (will,) = [x for x in sheet.item_spells if x.index == healing and x.cost == 0]
    assert will.spell.name == "Disguise Self"
    registry.characters.act_for(
        lyra, "cast", {"spell": will.spell.reference, "level": will.key}, _dm()
    )
    assert registry.gamestate.characters.get(lyra).inventory[healing].charges_used == 0
    # More than the charges left is refused.
    wall = next(x for x in sheet.item_spells if x.spell.name == "Wall of Fire")
    registry.characters.act_for(lyra, "charge", {"index": str(staff), "n": "5"}, _dm())
    with pytest.raises(PlayError):  # 2 left, Wall of Fire costs 4
        registry.characters.act_for(
            lyra, "cast", {"spell": wall.spell.reference, "level": wall.key}, _dm()
        )


FIREBALLS_2024 = {
    "entries": [
        "This wand has 7 charges. While holding it, you can expend no more than 3 charges to "
        "cast {@spell Fireball|XPHB} (save {@dc 15}) from it. For 1 charge, you cast the "
        "level 3 version of the spell. You can increase the spell's level by 1 for each "
        "additional charge you expend."
    ]
}


def test_how_an_item_casts_and_who_may_attune() -> None:
    assert spell_terms(FIREBALLS_2024) == SpellTerms(dc=15, upcast=True, most=3)
    assert spell_terms({"entries": ["using your spell save DC"]}) == SpellTerms()
    healers = [{"class": "bard"}, {"class": "cleric|xphb"}, {"class": "druid"}]
    assert attunement_allows(healers, classes=["Cleric"], species="", spellcaster=True)
    assert not attunement_allows(healers, classes=["Wizard"], species="", spellcaster=True)
    casters = [{"spellcasting": True}]
    assert not attunement_allows(casters, classes=["Fighter"], species="", spellcaster=False)
    dwarves = [{"race": "dwarf"}]
    assert attunement_allows(dwarves, classes=[], species="Mountain Dwarf", spellcaster=False)
    # Alignment is the table's to judge: it passes.
    assert attunement_allows([{"alignment": ["L", "G"]}], classes=[], species="", spellcaster=False)
    assert attunement_allows([], classes=[], species="", spellcaster=False)


@requires_corpus
async def test_a_wand_with_its_own_dc_cast_higher(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    _give(
        registry,
        lyra,
        InventoryItem.model_validate({"item": "Wand of Fireballs|XDMG"}),
        InventoryItem.model_validate({"item": "Staff of Healing|XDMG"}),
    )
    pc = registry.gamestate.characters.get(lyra)
    wand, healing = len(pc.inventory) - 2, len(pc.inventory) - 1
    # The wand asks for a spellcaster: Lyra, a wizard, may attune to it.
    registry.characters.act_for(lyra, "attune", {"index": str(wand)}, _dm())
    sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
    (fireball,) = [x for x in sheet.item_spells if x.index == wand]
    assert fireball.levels(7) == [3, 4, 5]  # no more than 3 charges
    assert fireball.levels(2) == [3, 4] and fireball.levels(0) == []
    dc = sheet.save_dc_for(fireball.spell)
    assert dc is not None and dc.value == 15  # the wand's, not Lyra's 17
    page = (await ac.get(f"/characters/{lyra}?tab=spells")).text
    assert f'value="{fireball.key}:5"' in page and "3 charges" in page
    registry.characters.act_for(
        lyra, "cast", {"spell": fireball.spell.reference, "level": f"{fireball.key}:5"}, _dm()
    )
    assert registry.gamestate.characters.get(lyra).inventory[wand].charges_used == 3
    # The Staff of Healing is for a bard, cleric or druid: Lyra, a wizard, cannot attune.
    with pytest.raises(PlayError):
        registry.characters.act_for(lyra, "attune", {"index": str(healing)}, _dm())
