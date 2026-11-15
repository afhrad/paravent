"""Travel, downtime, the shop during play and the rest of the bastion rules
(app/services/travel.py, downtime.py, shop.py, coins.py, bastions.py)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import BastionFacility, Role, SourceSet, World
from app.models.pc import ChosenOption, Currency, InventoryItem, PlayerCharacter
from app.services import coins
from app.services.bastions import BastionEvent, BastionService, event_rows
from app.services.clock import DAY, ClockService
from app.services.dice import Dice
from app.services.downtime import DowntimeError, DowntimeService, crafting_days, materials
from app.services.shop import bought_back, priced
from app.services.travel import Plan, TravelService, hunger_limit
from app.store.encounter_tables import EncounterTable, Environment
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_checks import LYRA, PASSWORD


class FixedDice(Dice):
    """Dice that roll what they are told, then 1s (or 10s for a d20, which never meets)."""

    def __init__(self, rolls: Sequence[int] = ()) -> None:
        super().__init__()
        self._rolls = list(rolls)

    def die(self, faces: int) -> int:
        if self._rolls:
            return self._rolls.pop(0)
        return 10 if faces == 20 else 1


class TestCoins:
    def test_paying_takes_small_coins_first_and_gives_change(self) -> None:
        purse = Currency(cp=3, sp=1, gp=2)
        assert coins.pay(purse, 25) == Currency(cp=8, sp=8, gp=1)
        assert coins.pay(Currency(pp=1), 1) == Currency(gp=9, sp=9, cp=9)
        assert coins.pay(purse, 1000) is None

    def test_earning_and_worth(self) -> None:
        assert coins.earn(Currency(), 1234) == Currency(gp=12, sp=3, cp=4)
        assert coins.worth(Currency(pp=1, ep=1, cp=5)) == 1055
        assert coins.text(1205) == "12gp,5cp"


class TestPrices:
    def test_shop_and_crafting(self) -> None:
        assert priced(1500, 120) == 1800 and priced(1, 150) == 2
        assert bought_back(1500, 50) == 750 and bought_back(1, 50) == 0
        # Plate armour: 300 days alone (2014), 150 (2024); materials half the price.
        assert crafting_days(150_000, "classic") == 300
        assert crafting_days(150_000, "one") == 150
        assert crafting_days(5000, "one") == 5 and crafting_days(1, "classic") == 1
        assert materials(150_000) == 75_000

    def test_hunger(self) -> None:
        assert hunger_limit(2, "classic") == 5 and hunger_limit(-3, "classic") == 1
        assert hunger_limit(5, "one") == 4


def _table(rows: list[str]) -> Environment:
    table = EncounterTable(
        "Forest",
        "XGE",
        None,
        None,
        "d4",
        tuple({"min": n + 1, "max": n + 1, "result": r} for n, r in enumerate(rows)),
    )
    return Environment("Forest", "XGE", (table,))


def _pc(cid: str, name: str, rations: int) -> PlayerCharacter:
    pc = PlayerCharacter(name=name, world_id="w")
    pc.id = cid
    if rations:
        pc.inventory = [
            InventoryItem(item=ChosenOption.model_validate("Rations (1 day)|PHB"), quantity=rations)
        ]
    return pc


class TestTravel:
    @pytest.fixture
    def road(self, tmp_path: Path) -> dict[str, Any]:
        party = [_pc("a", "Ana", 5), _pc("b", "Bo", 0)]
        state: dict[str, Any] = {"tired": [], "rests": 0}

        def eat(pc: PlayerCharacter) -> bool:
            for item in pc.inventory:
                if item.quantity:
                    item.quantity -= 1
                    return True
            return False

        def rest(_world: str) -> None:
            state["rests"] += 1

        state["clock"] = ClockService(GameStateStore(tmp_path), None)
        state["make"] = lambda dice: TravelService(
            state["clock"],
            None,
            party=lambda _w: party,
            eat=eat,
            tire=lambda pc: state["tired"].append(pc.name),
            con_mod=lambda _pc: -3,
            rest=rest,
            dice=dice,
        )
        return state

    def test_days_on_the_road(self, road: dict[str, Any]) -> None:
        journey = road["make"](FixedDice()).travel(
            "w", Plan(hours=24), rules="classic", resolve=lambda n, s: None, by="dm", by_name="DM"
        )
        # Three travelling days: two nights between them, ending on the third evening.
        assert (journey.hours, journey.miles) == (24, 72.0)
        assert road["clock"].of("w").minute == 8 * 60 + 2 * DAY + 8 * 60
        assert road["rests"] == 2 and journey.rations == 3
        # Bo has no rations and a Constitution of 3: hungry from the second day on, the
        # night's rest does not take it away.
        assert journey.hungry == ["Bo"] and road["tired"] == ["Bo", "Bo"]
        assert road["clock"].of("w").hungry == {"b": 3}

    def test_an_encounter_stops_the_party(self, road: dict[str, Any]) -> None:
        resolved = {"wolf": ("Wolf", "MM")}
        # Check on day 1 misses (10), day 2 meets (19); the table rolls 2.
        journey = road["make"](FixedDice([10, 19, 2])).travel(
            "w",
            Plan(
                hours=24,
                pace="fast",
                difficult=True,
                environment=_table(["a fox", "{@dice 2d4} {@creature wolf||wolves}", "x", "y"]),
            ),
            rules="one",
            resolve=lambda n, s: resolved.get(n.lower()),
            by="dm",
            by_name="DM",
        )
        assert journey.hours == 16 and journey.miles == 32.0
        assert journey.check == 19 and journey.encounter_roll == 2
        assert [(m.name, m.count) for m in journey.monsters] == [("Wolf", 2)]
        assert road["clock"].of("w").journey == journey


class TestDowntime:
    def test_projects(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path)
        paid: list[int] = []
        given: list[str] = []
        learned: list[str] = []
        service = DowntimeService(
            store,
            None,
            pay=lambda _c, cp, _w: paid.append(cp),
            give=lambda _c, ref: given.append(ref),
            learn=lambda _c, kind, ref: learned.append(f"{kind}:{ref}"),
            recuperate=lambda _c: None,
        )
        pc = _pc("a", "Ana", 0)
        service.grant([pc], 260, "w", "dm", "DM")
        sword = service.start(pc, "craft", "Longsword|PHB", price=1500, by="a", by_name="Ana")
        assert sword.needed == 3 and paid == [750]
        service.spend(pc, sword.id, 10, "a", "Ana")  # only the three days it needs
        assert given == ["Longsword|PHB"] and service.of("a").days == 257
        elvish = service.start(pc, "train", "language:Elvish|PHB", by="a", by_name="Ana")
        service.spend(pc, elvish.id, 250, "a", "Ana")
        assert learned == ["language:Elvish|PHB"] and paid[-1] == 25_000
        with pytest.raises(DowntimeError):
            service.spend(pc, elvish.id, 1, "a", "Ana")  # finished
        work = service.start(pc, "work", "", by="a", by_name="Ana")
        with pytest.raises(DowntimeError):
            service.spend(pc, work.id, 8, "a", "Ana")  # 7 days left


class TestBastionEvents:
    ROWS = [
        ["01–50", "{@area All Is Well|78c|x}"],
        ["51–55", "{@area Attack|78e|x}"],
        ["99–00", "{@area Treasure|40a|x}"],
    ]

    def test_rows(self) -> None:
        assert event_rows(self.ROWS) == [
            BastionEvent(1, 50, "78c", "All Is Well"),
            BastionEvent(51, 55, "78e", "Attack"),
            BastionEvent(99, 100, "40a", "Treasure"),
        ]

    def test_an_attack_costs_defenders_and_shuts_a_facility(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path)
        # d100 53 → Attack; 6d6 with two 1s; then the facility picked (2nd).
        service = BastionService(
            store,
            None,
            events=lambda: event_rows(self.ROWS),
            dice=FixedDice([53, 1, 4, 1, 5, 6, 3, 2]),
        )
        bastion = service.of("a").model_copy(
            update={
                "facilities": [
                    BastionFacility(ref="Smithy|XDMG", order="craft"),
                    BastionFacility(ref="Garden|XDMG"),
                ],
                "defenders": 2,
            }
        )
        store.bastions.upsert(bastion)
        service.maintain("a", True)
        after = service.turn("a", "Ana", None, "dm", "DM")
        assert after.defenders == 0 and not after.maintain
        assert after.event == "53|78e|Attack|2|Garden|XDMG"
        assert [f.shut for f in after.facilities] == [False, True]
        assert after.facilities[0].order == ""  # Maintain carries out no orders
        # The next turn opens it again.
        assert not any(f.shut for f in service.turn("a", "Ana", None, "dm", "DM").facilities)


@pytest.fixture
async def table(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(
        World(name="W", source_set=SourceSet(sources=["PHB", "MM", "XGE", "XDMG"]))
    )
    data = json.loads(LYRA.read_text(encoding="utf-8"))
    data.pop("$schema", None)
    data["world_id"] = world.id
    data["currency"] = {"gp": 20}
    lyra = registry.gamestate.characters.create(PlayerCharacter.model_validate(data))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry, lyra.id


@requires_corpus
class TestAtTheTable:
    async def test_travel_from_the_party_view(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        pc = registry.gamestate.characters.get(lyra)
        world = pc.world_id
        assert world is not None
        pc.inventory = [
            *pc.inventory,
            InventoryItem(item=ChosenOption.model_validate("Rations (1 day)|PHB"), quantity=1),
        ]
        registry.gamestate.characters.replace(pc)
        assert "Set out" in (await ac.get("/gm")).text
        await ac.post(
            f"/gm/clock/{world}/travel",
            data={
                "amount": "2",
                "unit": "day",
                "pace": "slow",
                "checks": "0",
                "rations": "1",
                "rest": "1",
            },
        )
        journey = registry.clock.of(world).journey
        assert journey is not None and (journey.hours, journey.miles) == (16, 32.0)
        assert journey.rations == 1 and journey.hungry == ["Lyra Mooncaller"]
        assert registry.clock.of(world).minute == 16 * 60 + DAY
        page = (await ac.get("/gm")).text
        assert "Went hungry: Lyra Mooncaller" in page
        assert "The party travels 16 h" in (await ac.get("/log")).text
        await ac.post(f"/gm/clock/{world}/travel_clear")
        assert registry.clock.of(world).journey is None

    async def test_an_encounter_on_the_road_becomes_one(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        forest = registry.encounter_tables.get("Forest|XGE")
        assert forest is not None
        journey = None
        for _ in range(40):  # a check meets on 18+; the forest's rows mostly have creatures
            await ac.post(
                f"/gm/clock/{world}/travel",
                data={"amount": "1", "unit": "day", "environment": "Forest|XGE", "checks": "3"},
            )
            journey = registry.clock.of(world).journey
            if journey is not None and journey.monsters:
                break
        assert journey is not None and journey.monsters
        made = await ac.post(f"/gm/clock/{world}/travel_encounter")
        encounter_id = registry.clock.of(world).journey.encounter_id  # type: ignore[union-attr]
        assert made.status_code == 303 and encounter_id
        assert registry.encounters.get(encounter_id).monsters == journey.monsters

    async def test_the_shop(self, table: tuple[AsyncClient, StoreRegistry, str]) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert (await ac.get(f"/characters/{lyra}/shop")).status_code == 404
        await ac.post(
            f"/gm/shop/{world}/open", data={"name": "The Anvil", "markup": "200", "buys": "50"}
        )
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert f"/characters/{lyra}/shop" in sheet and "The Anvil" in sheet
        assert "Torch" in (await ac.get(f"/characters/{lyra}/shop?q=torch")).text
        shop = f"/characters/{lyra}/shop"
        await ac.post(shop, data={"op": "buy", "item": "Longsword|PHB", "count": "1"})
        pc = registry.gamestate.characters.get(lyra)
        # 15 gp at 200 % is more than the purse holds.
        assert coins.worth(pc.currency) == 2000
        assert not any(i.item.name == "Longsword" for i in pc.inventory)
        await ac.post(shop, data={"op": "buy", "item": "Torch|PHB", "count": "3"})
        await ac.post(shop, data={"op": "buy", "item": "Dagger|PHB", "count": "1"})
        pc = registry.gamestate.characters.get(lyra)
        assert coins.worth(pc.currency) == 2000 - 6 - 400
        dagger = next(
            n for n, i in enumerate(pc.inventory) if i.item.name == "Dagger" and not i.equipped
        )
        await ac.post(shop, data={"op": "sell", "index": str(dagger), "count": "1"})
        assert coins.worth(registry.gamestate.characters.get(lyra).currency) == 2000 - 6 - 400 + 100
        log = (await ac.get("/log")).text
        assert "Buys Torch ×3" in log and "Sells Dagger" in log
        # The sheet's own action route may not hand out goods or gold.
        refused = await ac.post(
            f"/characters/{lyra}/act/receive", data={"gp": "1000"}, headers={"HX-Request": "true"}
        )
        assert refused.status_code >= 400 or "Unknown action" in refused.text
        assert coins.worth(registry.gamestate.characters.get(lyra).currency) == 1694
        await ac.post(f"/gm/shop/{world}/close")
        assert (await ac.get(f"/characters/{lyra}/shop")).status_code == 404

    async def test_downtime(self, table: tuple[AsyncClient, StoreRegistry, str]) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        before = registry.clock.of(world).minute  # type: ignore[arg-type]
        await ac.post(f"/gm/clock/{world}/downtime", data={"days": "10"})
        assert registry.clock.of(world).minute == before + 10 * DAY  # type: ignore[arg-type]
        assert "Downtime: 10 days" in (await ac.get(f"/characters/{lyra}")).text
        page = (await ac.get(f"/characters/{lyra}/downtime")).text
        assert "Crafting" in page and "Training" in page
        await ac.post(
            f"/characters/{lyra}/downtime",
            data={"op": "start", "kind": "craft", "item": "Dagger|PHB"},
        )
        project = registry.downtime.of(lyra).projects[0]
        assert project.needed == 1 and project.paid == 100
        await ac.post(
            f"/characters/{lyra}/downtime", data={"op": "spend", "project": project.id, "days": "1"}
        )
        pc = registry.gamestate.characters.get(lyra)
        assert any(i.item.name == "Dagger" for i in pc.inventory)
        assert registry.downtime.of(lyra).days == 9
        assert "Finishes Crafting: Dagger" in (await ac.get("/log")).text

    async def test_the_bastion_maintains_and_rolls_an_event(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        bastion = registry.bastions.of(lyra).model_copy(
            update={"facilities": [BastionFacility(ref="Arcane Study|XDMG")]}
        )
        registry.gamestate.bastions.upsert(bastion)
        url = f"/characters/{lyra}/bastion"
        await ac.post(url, data={"op": "add_basic", "facility": "Kitchen|XDMG", "space": "cramped"})
        await ac.post(url, data={"op": "defenders", "change": "1"})
        await ac.post(url, data={"op": "maintain", "on": "1"})
        held = registry.bastions.of(lyra)
        assert [(f.ref, f.space) for f in held.basic] == [("Kitchen|XDMG", "cramped")]
        assert held.defenders == 1 and held.maintain
        page = (await ac.post(url, data={"op": "turn"})).text
        held = registry.bastions.of(lyra)
        assert held.event and not held.maintain
        assert "Bastion event:" in page
