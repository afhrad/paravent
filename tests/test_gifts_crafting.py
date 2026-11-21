"""Supernatural gifts (app/services/gifts.py) and crafting magic items
(app/services/magic_crafting.py). The unit tests use invented texts and tables; the
route tests read the user's guides."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.pc import ChosenOption, Currency
from app.services.gifts import GiftReading, read_gift
from app.services.magic_crafting import Crafter, plan, rule_from
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]

DAY = 1440


def test_reading_a_gift() -> None:
    charm = {"type": "Charm", "entries": ["This charm has 4 charges. Once all are spent, it goes."]}
    assert read_gift(charm) == GiftReading(uses=4)
    assert read_gift({"type": "Charm", "entries": ["Once used three times, it fades."]}).uses == 3
    once = {"type": "Charm", "entries": ["You hum. Once you do so, the charm vanishes from you."]}
    assert read_gift(once).uses == 1
    lasting = {"type": "Charm", "entries": ["You float. These benefits last for 10 days."]}
    assert read_gift(lasting) == GiftReading(lasts=10 * DAY)
    spells = {
        "type": "Charm",
        "additionalSpells": [{"innate": {"_": {"limited": {"2e": ["a", "b"], "1": ["c"]}}}}],
        "entries": ["You may cast these spells."],
    }
    assert read_gift(spells).uses == 5  # two each of two spells, and one more
    blessing = {"type": "Blessing", "entries": ["Your skin hardens."]}
    assert read_gift(blessing) == GiftReading()


COSTS_2024 = [["Common", "5 days", "50 GP"], ["Uncommon", "10 days", "200 GP"]]
TOOLS_2024 = [["Wand", "{@item Woodcarver's Tools|XPHB}"], ["Potion", "{@item Herbalism Kit}"]]
COSTS_2014 = [["Common", "100 gp", "3rd"], ["Uncommon", "500 gp", "3rd"]]
WAND = {"type": "WD|XDMG", "rarity": "uncommon", "attachedSpells": {"charges": {"1": ["zap"]}}}


def test_crafting_by_the_2024_guide() -> None:
    rule = rule_from("XDMG", COSTS_2024, TOOLS_2024)
    assert rule is not None and rule.daily == 0
    novice = Crafter(level=3)
    made = plan(rule, WAND, novice)
    assert made is not None and made.days == 10 and made.paid == 20000
    assert [key for key, _ in made.missing] == ["arcana", "tool", "spells"]
    adept = Crafter(level=3, arcana=True, tools=["woodcarver's tools"], spells=["Zap"])
    assert plan(rule, WAND, adept) == made.__class__(
        "uncommon", 10, 20000, 0, (), "", ("Woodcarver's Tools",)
    )
    potion = plan(rule, {"type": "P", "rarity": "common"}, adept)
    assert potion is not None and (potion.days, potion.paid) == (2, 2500)  # halved
    blade = plan(rule, {"type": "M", "rarity": "uncommon"}, adept, base_price=1500)
    assert blade is not None and blade.paid == 21500  # the sword's price on top
    assert plan(rule, {"type": "SC", "rarity": "common"}, adept) is None  # scribed instead


def test_crafting_by_the_2014_guide() -> None:
    rule = rule_from("DMG", COSTS_2014)
    assert rule is not None
    made = plan(rule, WAND, Crafter(level=2, spells=["zap"]))
    assert made is not None and (made.days, made.paid, made.daily) == (20, 0, 2500)
    assert [key for key, _ in made.missing] == ["level", "slots"]
    assert plan(rule, WAND, Crafter(level=3, spells=["zap"], slots=True)).possible  # type: ignore[union-attr]


@requires_corpus
async def test_a_charm_given_and_used(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    page = await ac.get(f"/ref/reward/XDMG/Charm%20of%20Darkvision?world={world}")
    assert "Grant" in page.text
    charm = {"world": world, "name": "Charm of Darkvision", "source": "XDMG"}
    form = await ac.get("/gm/gifts/form", params=charm)
    assert 'name="uses"' in form.text and 'value="3"' in form.text
    done = await ac.post("/gm/gifts/grant", data={**charm, "character": lyra, "uses": "2"})
    assert "Charm of Darkvision given to Lyra" in done.text
    (gift,) = registry.gamestate.characters.get(lyra).gifts
    assert (gift.kind, gift.uses, gift.left) == ("charm", 2, 2)
    sheet = (await ac.get(f"/characters/{lyra}")).text
    assert "Supernatural gifts" in sheet and "2 of 2 uses left" in sheet
    assert "Charm of Darkvision" in (await ac.get("/gm")).text
    for _ in range(2):
        await ac.post(f"/characters/{lyra}/gifts", data={"op": "use", "gift": gift.id})
    assert registry.gamestate.characters.get(lyra).gifts == []
    log = (await ac.get(f"/log/panel?world={world}")).text
    assert "received Charm of Darkvision" in log and "is used up and vanishes" in log
    # A charm that lasts fades by the world's clock.
    study = {"world": world, "name": "Arcane Study Charm", "source": "XDMG"}
    assert 'value="7"' in (await ac.get("/gm/gifts/form", params=study)).text
    await ac.post("/gm/gifts/grant", data={**study, "character": lyra, "uses": "1", "days": "7"})
    registry.clock.advance(world, 8 * DAY, "dm", "DM")
    assert registry.gamestate.characters.get(lyra).gifts == []


@requires_corpus
async def test_a_magic_item_crafted(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id or ""
    wand = {"world": world, "name": "Wand of Magic Missiles", "source": "XDMG"}
    page = await ac.get(f"/ref/item/XDMG/Wand%20of%20Magic%20Missiles?world={world}")
    assert "Give the formula" in page.text
    given = await ac.post("/gm/formulas/give", data={**wand, "character": lyra})
    assert "Formula given to Lyra" in given.text and "holds it" in given.text
    url = f"/characters/{lyra}/downtime"
    downtime = (await ac.get(url)).text
    assert "Magic item formulas" in downtime and "Woodcarver" in downtime
    refused = await ac.post(
        url, data={"op": "start", "kind": "enchant", "item": "Wand of Magic Missiles|XDMG"}
    )
    assert registry.downtime.of(lyra).projects == [] and refused.status_code == 200
    pc = registry.gamestate.characters.get(lyra)
    pc.tools = [ChosenOption.model_validate("Woodcarver's Tools|XPHB")]
    pc.currency = Currency(gp=250)
    registry.gamestate.characters.replace(pc)
    registry.downtime.grant([pc], 10, world, "dm", "DM")
    await ac.post(
        url, data={"op": "start", "kind": "enchant", "item": "Wand of Magic Missiles|XDMG"}
    )
    (project,) = registry.downtime.of(lyra).projects
    assert (project.kind, project.needed, project.paid) == ("enchant", 10, 20000)
    assert registry.gamestate.characters.get(lyra).currency.gp == 50
    await ac.post(url, data={"op": "spend", "project": project.id, "days": "10"})
    items = [i.item.name for i in registry.gamestate.characters.get(lyra).inventory]
    assert "Wand of Magic Missiles" in items
    await ac.post(url, data={"op": "formula_drop", "item": "Wand of Magic Missiles|XDMG"})
    assert registry.gamestate.characters.get(lyra).formulas == []
