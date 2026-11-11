"""Starting gold and the shop on the wizard's Equipment step (CHARGEN-WIZARD.md §4)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.gamestate import SourceSet, World
from app.models.pc import PlayerCharacter
from app.services.chargen.plan import GOLD, GOLD_ROLL, SHOP, Planner, price
from app.services.chargen.rules import GoldRoll, starting_gold
from app.services.dice import Dice
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables
from app.store.registry import StoreRegistry
from tests.conftest import log_about
from tests.test_chargen import (  # noqa: F401 - the fixtures
    BOOKS_2014,
    BOOKS_2024,
    HTMX,
    _planner,
    client,
    lookups,
)


def _gold_fighter(planner: Planner, gold: int = 100) -> PlayerCharacter:
    pc = PlayerCharacter()
    planner.answer(pc, "class", ["Fighter|PHB"])
    planner.answer(pc, "background", ["Soldier|PHB"])
    planner.answer(pc, "equipment", [GOLD])
    planner.answer(pc, GOLD_ROLL, [str(gold * 100), "Fighter|PHB"])
    return pc


class TestStartingGold:
    def test_read_from_the_class(self) -> None:
        def cls(text: str) -> dict[str, object]:
            return {"startingEquipment": {"goldAlternative": text}}

        fighter = cls("{@dice 5d4 × 10|5d4 × 10|Starting Gold}")
        assert starting_gold(fighter) == GoldRoll(5, 4, 10)
        assert starting_gold(cls("{@dice 5d4|5d4|Starting Gold}")) == GoldRoll(5, 4, 1)
        assert starting_gold({"startingEquipment": {"defaultData": []}}) is None

    def test_only_where_the_class_prints_it(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        old = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = PlayerCharacter()
        old.answer(pc, "class", ["Fighter|PHB"])
        assert old.plan(pc).step("equipment").pick("equipment").options == ("packages", GOLD)
        new = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = PlayerCharacter()
        new.answer(pc, "class", ["Fighter|XPHB"])
        assert new.plan(pc).step("equipment").pick("equipment").options == ("packages",)

    def test_gold_replaces_the_packages_and_needs_its_roll(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _gold_fighter(planner)
        step = planner.plan(pc).step("equipment")
        assert [p.id for p in step.picks] == ["equipment"] and step.state == "done"
        planner.apply(pc)
        assert pc.inventory == [] and pc.currency.gp == 100
        # A roll for another class does not count.
        planner.answer(pc, "class", ["Wizard|PHB"])
        assert planner.plan(pc).step("equipment").missing == (GOLD_ROLL,)


class TestShop:
    def test_what_it_sells(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        offers = _planner(loaded_compendium, lookups, BOOKS_2014).catalogue.shop("classic")
        names = {o.name for o in offers}
        assert {"Longsword", "Chain Mail", "Backpack"} <= names
        assert all(price(o) > 0 and o.data.get("rarity", "none") == "none" for o in offers)
        assert not any(o.name in ("Galley", "Ruby", "Wheat") for o in offers)

    def test_both_editions_sell_one_printing_of_the_characters(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, [*BOOKS_2014, *BOOKS_2024])
        chain = [o for o in planner.catalogue.shop("one") if o.name == "Chain Mail"]
        assert [o.source for o in chain] == ["XPHB"]
        chain = [o for o in planner.catalogue.shop("classic") if o.name == "Chain Mail"]
        assert [o.source for o in chain] == ["PHB"]

    def test_buying_spends_the_purse_and_putting_back_refunds(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _gold_fighter(planner)
        for _ in range(2):
            planner.trade(pc, "+Longsword|PHB")  # 15 gp each
        planner.trade(pc, "+Chain Mail|PHB")  # 75 gp: more than the 70 left
        assert pc.choices[SHOP] == ["Longsword|PHB", "Longsword|PHB"]  # refused, not flagged
        planner.trade(pc, "-Longsword|PHB")
        planner.trade(pc, "+Chain Mail|PHB")
        planner.apply(pc)
        items = {i.item.name: i for i in pc.inventory}
        assert items["Longsword"].quantity == 1 and items["Chain Mail"].equipped
        assert pc.currency.gp == 10
        assert planner.purse(pc) == (10000, 9000)

    def test_something_not_for_sale_is_refused(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _gold_fighter(planner)
        planner.trade(pc, "+Vorpal Sword|DMG")
        assert SHOP not in pc.choices

    def test_a_second_suit_of_armor_stays_in_the_pack(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _gold_fighter(planner)
        planner.trade(pc, "+Chain Mail|PHB")
        planner.trade(pc, "+Leather Armor|PHB")
        planner.apply(pc)
        worn = {i.item.name: i.equipped for i in pc.inventory}
        assert worn == {"Chain Mail": True, "Leather Armor": False}

    def test_packages_coins_can_be_spent_too(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = PlayerCharacter()
        planner.answer(pc, "class", ["Fighter|XPHB"])
        planner.answer(pc, "equipment", ["packages"])
        planner.answer(pc, "equipment.class.0", ["c"])  # 155 gp, no gear
        planner.trade(pc, "+Longsword|XPHB")
        planner.apply(pc)
        assert [i.item.name for i in pc.inventory] == ["Longsword"]
        assert pc.currency.gp == 140

    def test_a_smaller_purse_later_asks_for_a_review(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2014)
        pc = _gold_fighter(planner)
        planner.trade(pc, "+Chain Mail|PHB")
        planner.answer(pc, GOLD_ROLL, ["5000", "Fighter|PHB"])
        step = planner.plan(pc).step("equipment")
        assert step.state == "review"
        planner.apply(pc)
        assert pc.currency.gp == 0  # never below nothing


async def _fighter_at_equipment(
    ac: AsyncClient,
    registry: StoreRegistry,
) -> str:
    world = World(name="Sword Coast", source_set=SourceSet(sources=BOOKS_2014))
    created = registry.gamestate.worlds.create(world)
    started = await ac.post("/chargen", data={"world": created.id})
    url = started.headers["location"]
    await ac.post(f"{url}/class", data={"class": "Fighter|PHB"})
    await ac.post(f"{url}/background", data={"background": "Soldier|PHB"})
    return url


class TestEitherKindOfItem:
    def test_the_2024_monk_chooses_a_tool_or_an_instrument(
        self,
        loaded_compendium: CompendiumStore,
        lookups: LookupTables,  # noqa: F811
    ) -> None:
        planner = _planner(loaded_compendium, lookups, BOOKS_2024)
        pc = PlayerCharacter()
        planner.answer(pc, "class", ["Monk|XPHB"])
        planner.answer(pc, "equipment", ["packages"])
        planner.answer(pc, "equipment.class.0", ["a"])
        step = next(s for s in planner.plan(pc).steps if s.id == "equipment")
        pick = next(p for p in step.picks if p.kind == "item")
        assert pick.label == "instrumentMusical toolArtisan"
        names = {o.split("|")[0].lower() for o in pick.options}
        assert {"flute", "smith's tools"} <= names

        planner.answer(pc, pick.id, [next(o for o in pick.options if "flute" in o.lower())])
        planner.apply(pc)
        assert "Flute" in [i.item.name for i in pc.inventory]


class TestScreens:
    async def test_roll_buy_and_finish_the_step(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ac, registry = client
        monkeypatch.setattr(Dice, "die", lambda self, faces: 4)
        url = await _fighter_at_equipment(ac, registry)
        draft_id = url.rsplit("/", 1)[1]
        page = (await ac.get(f"{url}/equipment")).text
        assert "Gold instead" in page and "5d4 × 10" in page

        chose = await ac.post(f"{url}/equipment", data={"equipment": GOLD}, headers=HTMX)
        assert "Roll 5d4 × 10 gold" in chose.text and "cg-shop" not in chose.text
        rolled = await ac.post(f"{url}/equipment/roll-gold", headers=HTMX)
        assert "200 gp" in rolled.text and "The roll is final" in rolled.text
        assert "Longsword" in rolled.text and "Shop" in rolled.text

        # Not twice in a world without rerolls.
        again = await ac.post(f"{url}/equipment/roll-gold", headers=HTMX)
        assert "does not allow rolling again" in again.text
        rolls = [e.what for e in log_about(registry, draft_id)]
        assert "5d4 × 10: 200" in rolls

        bought = await ac.post(
            f"{url}/equipment",
            data={"equipment": GOLD, "trade": "+Longsword|PHB"},
            headers=HTMX,
        )
        assert "185 gp left" in bought.text
        pc = registry.gamestate.characters.get(draft_id)
        assert pc.currency.gp == 185 and pc.inventory[0].item.name == "Longsword"

    async def test_leaving_for_gold_and_back_keeps_the_packages(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        url = await _fighter_at_equipment(ac, registry)
        draft_id = url.rsplit("/", 1)[1]
        await ac.post(
            f"{url}/equipment",
            data={"equipment": "packages", "equipment.class.0": "a"},
            headers=HTMX,
        )
        # The form still carries the package answer as it switches to gold.
        await ac.post(
            f"{url}/equipment",
            data={"equipment": GOLD, "equipment.class.0": "a"},
            headers=HTMX,
        )
        await ac.post(f"{url}/equipment", data={"equipment": "packages"}, headers=HTMX)
        assert registry.gamestate.characters.get(draft_id).choices["equipment.class.0"] == ["a"]

    async def test_the_purse_reads_in_german(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        await ac.post("/language", data={"lang": "de"})
        url = await _fighter_at_equipment(ac, registry)
        await ac.post(f"{url}/equipment", data={"equipment": GOLD}, headers=HTMX)
        rolled = await ac.post(f"{url}/equipment/roll-gold", headers=HTMX)
        assert "Laden" in rolled.text and " GM übrig" in rolled.text
