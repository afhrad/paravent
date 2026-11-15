"""Water, weather, mounts and vessels on the road; tools for crafting, training by the
world's books, scrolls and the book's downtime activities; magic items in the shop;
and what bastion orders and basic facilities bring and cost (app/services/travel.py,
crafting.py, downtime.py, activities.py, shop.py, bastion_orders.py, bastions.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.models.gamestate import BastionFacility, SourceSet
from app.models.pc import PlayerCharacter
from app.services import coins
from app.services.activities import Activities, Offered
from app.services.bastion_orders import OrderYields, options_of
from app.services.bastions import BastionService, Facility
from app.services.book_tables import copper, days, rolled, row_for, span
from app.services.chargen.catalogue import Offer
from app.services.clock import DAY, ClockService
from app.services.crafting import craft_rules, may_craft, tools_for
from app.services.downtime import DowntimeError, DowntimeService, training_plan
from app.services.shop import ShopError, ShopService
from app.services.travel import Plan, TravelService, Vessel
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, _pc, table

__all__ = ["table"]


class TestBookTables:
    def test_cells(self) -> None:
        assert span("1—5") == (1, 5) and span("96–00") == (96, 100)
        assert span("21+") == (21, 10**9) and span("9 or lower") == (-(10**9), 9)
        assert span("{@filter 3|spells|level=3}") == (3, 3) and span("Cantrip") is None
        rows = [["1—10", "a"], ["11—20", "b"], ["21+", "c"]]
        assert row_for(rows, 15) == ["11—20", "b"] and row_for(rows, 40) == ["21+", "c"]
        assert copper("1,000 GP") == 100_000 and copper("Win 50 gp.") == 5000
        assert days("3 days") == 3 and days("2 workweeks") == 10 and days("1 week") == 7
        assert rolled("{@dice 1d4 × 10} degrees colder", FixedDice([3])) == "30 degrees colder"


class TestTheRoad:
    @pytest.fixture
    def road(self, tmp_path: Path) -> dict[str, Any]:
        party = [_pc("a", "Ana", 9), _pc("b", "Bo", 9)]
        state: dict[str, Any] = {"tired": [], "rests": 0, "party": party}

        def eat(pc: PlayerCharacter) -> bool:
            item = pc.inventory[0]
            item.quantity -= 1
            return True

        def rest(_world: str) -> None:
            state["rests"] += 1

        state["clock"] = ClockService(GameStateStore(tmp_path), None)
        state["make"] = lambda dice: TravelService(
            state["clock"],
            None,
            party=lambda _w: party,
            eat=eat,
            tire=lambda pc: state["tired"].append(pc.name),
            con_mod=lambda _pc: 0,
            con_save=lambda _pc: 2,
            rest=rest,
            dice=dice,
        )
        return state

    def go(self, road: dict[str, Any], plan: Plan, rules: str = "classic", dice: Any = None) -> Any:
        return road["make"](dice or FixedDice()).travel(
            "w",
            plan,
            rules=rules,
            resolve=lambda n, s: None,
            by="dm",
            by_name="DM",
            weather=lambda: "Normal|None|Light rain",
        )

    def test_water_by_the_rules(self, road: dict[str, Any]) -> None:
        # 2014, half water: a DC 15 save each (+2): Ana rolls 13 (15, saved), Bo 12 (fails).
        journey = self.go(road, Plan(hours=8, water="half", checks=0), dice=FixedDice([13, 12]))
        assert journey.thirsty == ["Bo"] and road["tired"] == ["Bo"]
        # 2024, half water is not less than half; none costs a level each.
        road["tired"].clear()
        assert self.go(road, Plan(hours=8, water="half"), "one").thirsty == []
        journey = self.go(road, Plan(hours=8, water="none"), "one")
        assert journey.thirsty == ["Ana", "Bo"] and road["tired"] == ["Ana", "Bo"]

    def test_mounts_vessels_and_weather(self, road: dict[str, Any]) -> None:
        # Mounted: twice the distance in each day's first hour.
        journey = self.go(road, Plan(hours=16, mounted=True, weather=True))
        assert journey.miles == 48.0 + 6.0 and journey.mounted
        assert journey.weather == ["Normal|None|Light rain"] * 2
        # A sailing ship around the clock: two days of 24 hours, no nights in between.
        start = road["clock"].of("w").minute
        ship = Vessel("Sailing Ship", 2.0, around_the_clock=True)
        journey = self.go(road, Plan(hours=48, vessel=ship))
        assert journey.miles == 96.0 and journey.vessel == "Sailing Ship"
        assert road["clock"].of("w").minute == start + 2 * DAY

    def test_a_day_left_partway_carries_on(self, road: dict[str, Any]) -> None:
        first = self.go(road, Plan(hours=4))
        assert first.rations == 0  # no supper yet: the day is half done
        assert road["clock"].of("w").road == 4 * 60
        second = self.go(road, Plan(hours=4))
        assert second.rations == 2 and road["clock"].of("w").road == 0

    def test_a_night_check_wakes_the_party(self, road: dict[str, Any]) -> None:
        # The day's check misses (10); the night's comes up (19); woken 3 hours in.
        journey = self.go(road, Plan(hours=16, night_check=True), dice=FixedDice([10, 19, 3]))
        assert journey.night and journey.check == 19 and journey.hours == 8
        assert road["rests"] == 0


def _offer(name: str, data: dict[str, Any]) -> Offer:
    return Offer(name=name, source="XPHB", data={"name": name, "source": "XPHB", **data})


class TestCrafting:
    SMITH = _offer(
        "Smith's Tools",
        {
            "type": "AT|XPHB",
            "entries": [
                {
                    "type": "list",
                    "items": [
                        {
                            "type": "item",
                            "name": "Craft:",
                            "entries": [
                                "Any Melee weapon (except {@item Club|XPHB}), "
                                "Medium armor (except {@item Hide Armor|XPHB|Hide}), "
                                "Heavy armor, {@item Chain|XPHB}"
                            ],
                        }
                    ],
                }
            ],
        },
    )

    def test_which_tool_makes_what(self) -> None:
        rules = craft_rules([self.SMITH, _offer("Rope", {"type": "G|XPHB"})])
        sword = _offer("Longsword", {"type": "M|XPHB"})
        club = _offer("Club", {"type": "M|XPHB"})
        hide = _offer("Hide Armor", {"type": "MA|XPHB"})
        chain = _offer("Chain", {"type": "G|XPHB"})
        assert tools_for(sword, rules) == ["Smith's Tools"] and tools_for(chain, rules)
        assert tools_for(club, rules) == [] and tools_for(hide, rules) == []
        assert not may_craft(sword, rules, ["Weaver's Tools"])
        assert may_craft(sword, rules, ["smith's tools"]) and may_craft(club, rules, [])

    def test_training_follows_the_books(self) -> None:
        assert training_plan("xge", 3) == (35, 500)  # 7 workweeks at 25 gp a week
        assert training_plan("xge", -1) == (50, 500)
        assert training_plan("phb", 3) == (250, 100)
        assert training_plan("table", 3, 40) == (40, 0)


TABLES: dict[str, list[list[str]]] = {
    "Carousing": [["1—5", "hostile"], ["6—10", "none"], ["11—15", "one ally"], ["16+", "two"]],
    "Lower-Class Carousing Complications": [["1", "A pickpocket.*"], ["2", "A scar.*"]],
    "Crime; Loot Value": [["10", "50 gp, a merchant"], ["15", "100 gp, a richer one"]],
    "Crime Complications": [["1", "A bounty."]],
    "Buying a Magic Item; Buying Magic Items": [
        ["1—10", "Roll {@dice 1d4} times on {@table Magic Item Table B}."]
    ],
    "Buying a Magic Item; Magic Item Price": [
        ["Common", "{@dice (1d6 + 1) × 10} gp"],
        ["Uncommon", "{@dice 1d6 × 100} gp"],
    ],
}


class TestActivities:
    @pytest.fixture
    def week(self, tmp_path: Path) -> dict[str, Any]:
        state: dict[str, Any] = {"paid": [], "earned": [], "given": [], "sold": [], "purse": 1000}
        downtime = DowntimeService(
            GameStateStore(tmp_path),
            None,
            pay=lambda c, n, w: None,
            give=lambda c, r: None,
            learn=lambda c, k, r: None,
            recuperate=lambda c: None,
        )
        state["downtime"] = downtime
        state["totals"] = []

        def pay(_c: str, amount: int, _w: str) -> None:
            state["paid"].append(amount)
            state["purse"] -= amount

        state["make"] = lambda dice: Activities(
            downtime,
            check=lambda pc, kind, subject: state["totals"].pop(0),
            proficiency=lambda pc: 2,
            hit_die=lambda pc: 8,
            pay=pay,
            earn=lambda c, n, w: state["earned"].append(n),
            purse=lambda c: max(0, state["purse"]),
            magic_items=lambda table, times: [
                Offered("Potion of Climbing", "DMG", "common", True),
                Offered("Bag of Holding", "DMG", "uncommon"),
            ][:times],
            give=lambda c, r: state["given"].append(r),
            sell=lambda c, r, p: state["sold"].append((r, p)),
            dice=dice,
        )
        pc = _pc("a", "Ana", 0)
        downtime.grant([pc], 20, "w", "dm", "DM")
        state["pc"] = pc
        return state

    def run(self, week: dict[str, Any], kind: str, options: dict[str, str], dice: Any) -> Any:
        return week["make"](dice).run(
            week["pc"], kind, options, lambda caption: TABLES.get(caption), by="a", by_name="Ana"
        )

    def test_carousing(self, week: dict[str, Any]) -> None:
        week["totals"] = [12]
        # d100 for a complication: 5 comes up; the d2 picks the pickpocket.
        done = self.run(week, "carouse", {"class": "lower"}, FixedDice([5, 1]))
        assert done.result == ["check:persuasion:12", "row:one ally", "complication:A pickpocket."]
        assert week["paid"] == [1000] and done.finished and done.needed == 5
        assert week["downtime"].of("a").days == 15

    def test_crime(self, week: dict[str, Any]) -> None:
        # Two of three checks at DC 15: half of 100 gp.
        week["totals"] = [16, 10, 17]
        done = self.run(week, "crime", {"dc": "15", "third": "perception"}, FixedDice())
        assert done.result[1] == "check:thieves_tools:10:15:0" and week["earned"] == [5000]
        # None at DC 10: caught, fined 50 gp, which the purse cannot cover in full.
        # The 25 gp to plan it come first: 30 gp in the purse leave 5 for the fine.
        week["totals"], week["purse"] = [1, 1, 1], 3000
        caught = self.run(week, "crime", {"dc": "10", "third": "deception"}, FixedDice())
        assert "caught:5000:2" in caught.result and "debt:4500" in caught.result
        with pytest.raises(DowntimeError):
            self.run(week, "crime", {"dc": "12"}, FixedDice())

    def test_buying_magic_items(self, week: dict[str, Any]) -> None:
        week["totals"] = [7]
        # 1d4 = 2 items; the common's price (1d6 + 1) × 10 → 3+1 = 40 gp, halved; the
        # uncommon's 1d6 × 100 → 2 → 200 gp.
        done = self.run(week, "buy_magic", {}, FixedDice([2, 3, 2, 50]))
        assert done.offers == ["Potion of Climbing|DMG|2000", "Bag of Holding|DMG|20000"]
        activities = week["make"](FixedDice())
        assert activities.take(week["pc"], done.id, 0) == "Potion of Climbing|DMG"
        assert week["given"] == ["Potion of Climbing|DMG"] and week["paid"][-1] == 2000
        activities.refuse(week["pc"], done.id, 0)
        assert week["downtime"].of("a").projects[-1].offers == []


class TestDowntimeScroll:
    def test_scribing(self, tmp_path: Path) -> None:
        given: list[str] = []
        service = DowntimeService(
            GameStateStore(tmp_path),
            None,
            pay=lambda c, n, w: None,
            give=lambda c, r: given.append(r),
            learn=lambda c, k, r: None,
            recuperate=lambda c: None,
        )
        pc = _pc("a", "Ana", 0)
        service.grant([pc], 5, "w", "dm", "DM")
        scroll = service.start(
            pc,
            "scribe",
            "Spell Scroll (Level 1)|XDMG;Magic Missile|XPHB",
            needed=1,
            price=2500,
            by="a",
            by_name="Ana",
        )
        assert scroll.paid == 2500
        service.spend(pc, scroll.id, 1, "a", "Ana")
        assert given == ["Spell Scroll (Level 1)|XDMG"]


class TestShopStock:
    def test_stock_and_sales(self, tmp_path: Path) -> None:
        shops = ShopService(GameStateStore(tmp_path), None)
        shops.stock(
            "w", [("Bag of Holding|XDMG", 40000, 2), ("Potion of Healing|XDMG", 5000, None)]
        )
        shops.stock("w", [("Bag of Holding|XDMG", 1, 1)])  # more of one, price kept
        held = shops.of("w").stock
        assert [(s.ref, s.price, s.count) for s in held] == [
            ("Bag of Holding|XDMG", 40000, 3),
            ("Potion of Healing|XDMG", 5000, None),
        ]
        shops.sold("w", "Bag of Holding|XDMG", 3)
        shops.sold("w", "Potion of Healing|XDMG", 10)  # no end of them
        assert [s.ref for s in shops.of("w").stock] == ["Potion of Healing|XDMG"]
        with pytest.raises(ShopError):
            shops.sold("w", "Bag of Holding|XDMG", 1)


BARRACK = {
    "name": "Barrack",
    "source": "XDMG",
    "entries": [
        "A Barrack is furnished to serve as sleeping quarters for up to twelve Bastion Defenders.",
        {
            "type": "entries",
            "name": "Recruit: Bastion Defenders",
            "entries": ["Each time, up to four Bastion Defenders are recruited to your Bastion."],
        },
    ],
}
HALL = {
    "name": "Gaming Hall",
    "source": "XDMG",
    "entries": [
        {
            "type": "entries",
            "name": "Trade: Gambling Hall",
            "entries": [
                "Roll {@dice 1d100} and consult the following table.",
                {
                    "type": "table",
                    "rows": [["01–50", "{@dice 1d6 × 10} GP"], ["51–00", "{@dice 2d6 × 10} GP"]],
                },
            ],
        }
    ],
}
SMITHY = {
    "name": "Smithy",
    "source": "XDMG",
    "entries": [
        {
            "type": "list",
            "items": [
                {
                    "type": "item",
                    "name": "Craft:",
                    "entry": "Smith's Tools. The facility's hirelings craft anything that can "
                    "be made with {@item Smith's Tools|XPHB}.",
                },
                {
                    "type": "item",
                    "name": "Craft:",
                    "entry": "Book. You commission a blank book. The work takes 7 days and "
                    "costs you 10 GP.",
                },
            ],
        }
    ],
}


class TestBastionOrders:
    def test_reading_the_texts(self) -> None:
        barrack = options_of(BARRACK)["recruit"][0]
        assert (barrack.defenders, barrack.capacity) == (4, 12)
        hall = options_of(HALL)["trade"][0]
        assert hall.winnings == ((1, 50, "1d6 × 10"), (51, 100, "2d6 × 10"))
        smithy = options_of(SMITHY)["craft"]
        assert smithy[0].tool == "Smith's Tools" and smithy[1].cost == 1000

    def test_a_turn_carries_them_out(self, tmp_path: Path) -> None:
        texts = {"Barrack|XDMG": BARRACK, "Gaming Hall|XDMG": HALL, "Smithy|XDMG": SMITHY}
        state: dict[str, Any] = {"paid": [], "earned": [], "given": []}
        yields = OrderYields(
            options=lambda ref: options_of(texts[ref]),
            pay=lambda c, n, w: state["paid"].append(n) or True,
            earn=lambda c, n: state["earned"].append(n),
            give=lambda c, r, n: state["given"].append((r, n)),
            price=lambda ref: 150_000 if ref.startswith("Plate") else 1000,
            dice=FixedDice([30, 4]),
        )
        store = GameStateStore(tmp_path)
        service = BastionService(store, None, yields=yields)
        bastion = service.of("a").model_copy(
            update={
                "defenders": 10,
                "facilities": [
                    BastionFacility(ref="Barrack|XDMG", order="recruit", option="0"),
                    BastionFacility(ref="Gaming Hall|XDMG", order="trade", option="0"),
                    BastionFacility(ref="Smithy|XDMG", order="craft", option="0|Plate Armor|XPHB"),
                ],
            }
        )
        store.bastions.upsert(bastion)
        after = service.turn("a", "Ana", None, "dm", "DM", at=0)
        # Room for two more in the one Barrack; d100 30 → 1d6 (4) × 10 GP; plate armour
        # takes 150 days, so it waits, its materials paid.
        assert after.defenders == 12
        assert state["earned"] == [4000] and state["paid"] == [75_000]
        assert after.pending == [f"{150 * DAY}|Plate Armor|XPHB"]
        assert "Barrack|XDMG|defenders|2" in after.yields
        later = service.turn("a", "Ana", None, "dm", "DM", at=150 * DAY)
        assert later.pending == [] and ("Plate Armor|XPHB", 1) in state["given"]

    def test_basic_facilities_cost_after_the_first_two(self, tmp_path: Path) -> None:
        paid: list[int] = []
        service = BastionService(
            GameStateStore(tmp_path), None, pay=lambda c, n, w: paid.append(n) or True
        )
        kitchen = Facility("Kitchen", "XDMG", 0, ())
        costs = {"cramped": (50_000, 20), "roomy": (100_000, 45)}
        service.add_basic("a", 5, kitchen, "cramped", costs=costs, at=0)
        service.add_basic("a", 5, kitchen, "roomy", costs=costs, at=0)
        built = service.add_basic("a", 5, kitchen, "roomy", costs=costs, at=0)
        assert paid == [100_000] and built.basic[2].ready == 45 * DAY
        grown = service.enlarge_basic("a", 0, {"cramped": (50_000, 25)}, at=0)
        assert grown.basic[0].space == "roomy" and paid[-1] == 50_000


@requires_corpus
class TestAtTheTable:
    async def test_downtime_activities_and_tools(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        await ac.post(f"/gm/clock/{world}/downtime", data={"days": "10"})
        page = (await ac.get(f"/characters/{lyra}/downtime")).text
        # The world has Xanathar's: its activities, and its training rule.
        assert "Carousing" in page and "a workweek" in page
        url = f"/characters/{lyra}/downtime"
        await ac.post(url, data={"op": "activity", "kind": "carouse", "class": "lower"})
        done = registry.downtime.of(lyra).projects[-1]
        assert done.kind == "carouse" and done.result[0].startswith("check:persuasion:")
        assert registry.downtime.of(lyra).days == 5
        assert coins.worth(registry.gamestate.characters.get(lyra).currency) == 1000
        page = (await ac.get(url)).text
        assert "Persuasion" in page

    async def test_magic_items_in_the_shop(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        await ac.post(f"/gm/shop/{world}/open", data={"name": "Curios", "markup": "100"})
        await ac.post(f"/gm/shop/{world}/add", data={"item": "Potion of Climbing", "price": "5"})
        stock = registry.shops.of(world).stock  # type: ignore[arg-type]
        assert [(s.ref, s.price) for s in stock] == [("Potion of Climbing|XDMG", 500)]
        page = (await ac.get(f"/characters/{lyra}/shop")).text
        assert "Magic items" in page and "Potion of Climbing" in page
        await ac.post(
            f"/characters/{lyra}/shop", data={"op": "buy_magic", "item": "Potion of Climbing|XDMG"}
        )
        pc = registry.gamestate.characters.get(lyra)
        assert any(i.item.name == "Potion of Climbing" for i in pc.inventory)
        assert coins.worth(pc.currency) == 1500
        await ac.post(f"/gm/shop/{world}/roll", data={"rarity": "uncommon", "count": "2"})
        assert len(registry.shops.of(world).stock) == 3  # type: ignore[arg-type]

    async def test_bastion_orders_with_options(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        registry.gamestate.worlds.update(
            world, source_set=SourceSet(sources=["XPHB", "XMM", "XDMG"])
        )
        bastion = registry.bastions.of(lyra).model_copy(
            update={"facilities": [BastionFacility(ref="Garden|XDMG")]}
        )
        registry.gamestate.bastions.upsert(bastion)
        url = f"/characters/{lyra}/bastion"
        page = (await ac.get(url)).text
        assert "Food: Rations ×100" in page
        options = registry.facility_options("Garden|XDMG")["harvest"]
        food = next(n for n, o in enumerate(options) if o.title.startswith("Food"))
        await ac.post(url, data={"op": "order", "index": "0", "order": f"harvest|{food}"})
        assert registry.bastions.of(lyra).facilities[0].option == str(food)
        await ac.post(url, data={"op": "turn"})
        pc = registry.gamestate.characters.get(lyra)
        rations = [i for i in pc.inventory if i.item.name == "Rations"]
        assert rations and rations[0].quantity >= 100
        assert "Rations ×100 into the pack" in (await ac.get(url)).text
