"""The item builder (app/services/item_builder.py): magic items of the table's own,
published as homebrew, applied on the sheet like the books' own."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import ItemDesign, ItemSpell
from app.models.pc import ChosenOption, InventoryItem
from app.services.item_builder import ItemError, item_of, kind_of
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


def test_the_item_it_publishes_as() -> None:
    design = ItemDesign(
        name="Ember Ring",
        kind="ring",
        rarity="rare",
        attunement=True,
        attune_by="by a sorcerer",
        bonus_saving_throw=1,
        resist=["fire"],
        ability_set={"con": 19},
        ability_raise={"cha": 2},
        charges=3,
        recharge="dawn",
        recharge_amount="1d3",
        spells=[ItemSpell(spell="Fire Bolt|PHB"), ItemSpell(spell="Fireball|PHB", charges=3)],
        text="Warm to the touch.\n\nIt glows near fire.",
    )
    item = item_of(design, None, newer=True, value=400000)
    assert item["type"] == "RG|XDMG" and item["reqAttune"] == "by a sorcerer"
    assert item["bonusSavingThrow"] == "+1" and item["resist"] == ["fire"]
    assert item["ability"] == {"cha": 2, "static": {"con": 19}}
    assert (item["charges"], item["recharge"], item["rechargeAmount"]) == (3, "dawn", "{@dice 1d3}")
    assert item["attachedSpells"] == {"will": ["fire bolt|phb"], "charges": {"3": ["fireball|phb"]}}
    assert item["entries"] == ["Warm to the touch.", "It glows near fire."]
    assert item_of(design, None, newer=False, value=None)["type"] == "RG|DMG"
    assert kind_of({"type": "WD|DMG"}, None) == "wand" and kind_of({}, None) == "wondrous"


@requires_corpus
async def test_designs_from_the_books_published_and_worn(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    _ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    builder = registry.item_builder
    # From a printed item: its numbers and text come along.
    blade = builder.create("Dawn Blade", world, based_on="Sun Blade|DMG")
    assert (blade.kind, blade.base_item, blade.bonus_weapon) == ("weapon", "Longsword|PHB", 2)
    assert blade.attunement and "sunlight" in blade.text.lower()
    wand = builder.create("", world, based_on="Wand of Magic Missiles|DMG")
    assert wand.name == "Wand of Magic Missiles (variant)"
    assert (wand.kind, wand.charges, wand.recharge, wand.recharge_amount) == (
        "wand",
        7,
        "dawn",
        "1d6+1",
    )
    assert [(s.spell, s.charges) for s in wand.spells] == [("magic missile", 1)]
    # Blank: a weapon needs a base weapon.
    try:
        builder.create("Bad", world, kind="weapon", base_item="Plate Armor|PHB")
    except ItemError as exc:
        assert exc.key == "itembuilder.error.base_weapon"
    else:
        raise AssertionError("a weapon on plate armour")
    belt = builder.create("Belt of Oxen", world, kind="wondrous")
    belt.attunement, belt.ability_set, belt.rarity = True, {"str": 21}, "rare"
    builder.save(belt)
    assert builder.value(belt) and builder.value(belt) > 0
    # Published: in the compendium, its numbers on the sheet.
    published = builder.publish()
    assert published.count == 3
    found = registry.compendium.get("item", "Dawn Blade|Homebrew")
    assert found is not None
    pc = registry.gamestate.characters.get(lyra)
    inventory = [
        *pc.inventory,
        InventoryItem(item=ChosenOption(name="Dawn Blade", source="Homebrew"), equipped=True),
        InventoryItem(
            item=ChosenOption(name="Belt of Oxen", source="Homebrew"), equipped=True, attuned=True
        ),
    ]
    registry.gamestate.characters.update(lyra, inventory=inventory)
    sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
    strength = next(a for a in sheet.abilities if a.key == "str")
    assert strength.score.value == 21 and "Belt of Oxen 21" in strength.score.explanation
    attack = next(a for a in sheet.attacks if a.name == "Dawn Blade")
    assert "magic +2" in attack.bonus.explanation and attack.damage == "1d8+7"


@requires_corpus
async def test_the_builder_pages(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    registry.table.select(world)
    assert "Item builder" in (await ac.get("/gm/items")).text
    # A blank weapon begins on the first base weapon of the world's books.
    made = await ac.post("/gm/items", data={"name": "Thorn Spear", "kind": "weapon"})
    design_id = made.headers["location"].rsplit("/", 1)[1]
    design = registry.item_builder.get(design_id)
    assert design.kind == "weapon" and design.base_item
    page = (await ac.get(f"/gm/items/{design_id}")).text
    assert "Base item" in page and "How it reads" in page
    # The whole form, then published.
    done = await ac.post(
        f"/gm/items/{design_id}",
        data={
            "action": "publish",
            "name": "Thorn Spear",
            "world": world,
            "kind": "weapon",
            "base_item": "Spear|PHB",
            "rarity": "rare",
            "attunement": "1",
            "bonus_weapon": "2",
            "resist": "poison",
            "set_str": "",
            "raise_con": "2",
            "charges": "3",
            "recharge": "dawn",
            "recharge_amount": "1d3",
            "spell": ["Entangle|PHB", ""],
            "spell_charges": ["1", ""],
            "text": "Thorns curl along the haft.",
            "weight": "",
            "value": "",
        },
    )
    assert "published=1" in done.headers["location"]
    design = registry.item_builder.get(design_id)
    assert (design.base_item, design.bonus_weapon, design.resist) == ("Spear|PHB", 2, ["poison"])
    assert design.ability_raise == {"con": 2} and [s.spell for s in design.spells] == [
        "Entangle|PHB"
    ]
    item = registry.compendium.get("item", "Thorn Spear|Homebrew")
    assert item is not None
    shown = (await ac.get(f"/ref/item/Homebrew/Thorn Spear?world={world}")).text
    assert "Thorns curl along the haft" in shown
    # A printed magic item's page offers to begin one from it.
    printed = (await ac.get(f"/ref/item/DMG/Cloak of Protection?world={world}")).text
    assert "Make my own from this" in printed
    begun = await ac.post("/gm/items", data={"based_on": "Cloak of Protection|DMG"})
    assert begun.status_code == 303
    refused = await ac.post("/gm/items", data={"based_on": "No Such Thing"})
    assert refused.status_code == 422 and "No magic item" in refused.text
    assert (await ac.get("/gm/items/nope")).status_code == 404
    await ac.post(f"/gm/items/{design_id}/delete")
    assert registry.item_builder.named("Thorn Spear") is None
