"""Exhaustion from hunger and thirst that no rest lifts until the character is fed, tool
checks with their proficiency in the roll, the Workshop's chosen tools, and what a
lifestyle costs each day, and the contacts downtime brings (app/services/play.py,
travel.py, activities.py, bastion_orders.py, bastions.py, lifestyle.py, contacts.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind
from app.services.bastion_orders import tool_choice
from app.services.bastions import BastionError, BastionService, Facility
from app.services.clock import DAY, ClockService
from app.services.contacts import ContactError, ContactService, made
from app.services.lifestyle import LifestyleService, costs_2014, costs_2024
from app.services.play import PlayError
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore
from app.store.protocol import ProtocolStore, log_key
from tests.test_play import Table


@pytest.fixture
def rogue(tmp_path: Path) -> Table:
    return Table(
        tmp_path / "c",
        name="Nim",
        classes=[{"class": "Homebrew Rogue", "level": 5}],
        abilities={"dex": 12, "int": 14, "con": 10},
        tools=["Thieves' Tools|XPHB"],
        overrides={"hp_max": 30},
        inventory=[{"item": "Rations|XPHB", "quantity": 1}],
    )


class TestHungerAndThirst:
    def test_rests_leave_what_hunger_and_thirst_brought(self, rogue: Table) -> None:
        rogue.act("exhaustion", delta=1, cause="hunger")
        rogue.act("exhaustion", delta=1, cause="thirst")
        rogue.act("exhaustion", delta=1)
        assert (rogue.now.state.hunger_levels, rogue.now.state.thirst_levels) == (1, 1)
        rogue.act("rest", kind="long")  # the third level goes, the other two stay
        rogue.act("rest", kind="long")
        assert rogue.now.state.exhaustion == 2
        rogue.act("eat")  # a day's rations: hunger lets go
        assert rogue.now.state.hunger_levels == 0 and rogue.now.state.thirst_levels == 1
        rogue.act("rest", kind="long")
        rogue.act("rest", kind="long")
        assert rogue.now.state.exhaustion == 1
        rogue.act("fed", what="thirst")
        rogue.act("rest", kind="long")
        assert rogue.now.state.exhaustion == 0

    def test_levels_taken_by_hand_take_the_hold_too(self, rogue: Table) -> None:
        rogue.act("exhaustion", delta=2, cause="hunger")
        rogue.act("exhaustion", delta=-1)
        assert rogue.now.state.hunger_levels == 1


class TestToolChecks:
    def test_the_proficiency_is_in_the_roll(self, rogue: Table) -> None:
        rogue.roll(10).act("roll", kind="tool", subject="dex|Thieves' Tools")
        entry = rogue.latest
        assert entry.kind is EntryKind.CHECK and entry.what == "tool:dex:Thieves' Tools"
        assert entry.roll is not None and entry.roll.total == 10 + 1 + 3
        # A tool the character does not know: the ability alone.
        rogue.roll(10).act("roll", kind="tool", subject="int|Tinker's Tools")
        assert rogue.latest.roll is not None and rogue.latest.roll.total == 12


WORKSHOP: dict[str, Any] = {
    "entries": [
        {
            "type": "entries",
            "name": "Artisan's Tools",
            "entries": [
                "The Workshop comes equipped with six different kinds of "
                "{@item Artisan's Tools|XPHB}, chosen from the following list:",
                {
                    "type": "list",
                    "items": [
                        "{@item Carpenter's Tools|XPHB}",
                        "{@item Painter's Supplies|XPHB|Painter's Tools}",
                        "{@item Potter's Tools|XPHB}",
                    ],
                },
            ],
        }
    ]
}


class TestWorkshopTools:
    def test_the_choice_is_read_and_kept(self, tmp_path: Path) -> None:
        most, offered = tool_choice(WORKSHOP)
        assert most == 6 and offered[1] == "Painter's Supplies|XPHB"
        assert tool_choice({"entries": ["A plain room."]}) == (0, ())
        service = BastionService(GameStateStore(tmp_path), None)
        service.add("a", 5, Facility("Workshop", "XDMG", 5, ("craft",)))
        held = service.equip("a", 0, ["carpenter's tools|xphb", "Potter's Tools|XPHB"], offered, 6)
        assert held.facilities[0].tools == ["Carpenter's Tools|XPHB", "Potter's Tools|XPHB"]
        with pytest.raises(BastionError):
            service.equip("a", 0, ["Smith's Tools|XPHB"], offered, 6)
        with pytest.raises(BastionError):
            service.equip("a", 0, list(offered), offered, 2)


class TestLifestyle:
    def test_costs_from_either_handbook(self) -> None:
        old = [["Wretched", "—"], ["Modest", "1 gp"], ["Aristocratic", "10 gp minimum"]]
        assert costs_2014(old) == {"wretched": 0, "modest": 100, "aristocratic": 1000}
        new = [
            ["Ale (mug)", "4 CP"],
            ["{@i Inn Stay per Day}"],
            ["Modest", "5 SP"],
            ["{@i Meal}"],
            ["Modest", "1 SP"],
        ]
        assert costs_2024(new) == {"wretched": 0, "modest": 50 + 3 * 10}

    def test_days_off_the_road_are_paid(self, tmp_path: Path) -> None:
        log = ProtocolStore(tmp_path / "log")
        purses = {"a": 250, "b": 10_000}
        people = [
            PlayerCharacter(id="a", name="Ana", world_id="w", lifestyle="modest", status="ready"),
            PlayerCharacter(id="b", name="Bo", world_id="w", status="ready"),
        ]

        def pay(character: str, amount: int) -> None:
            purses[character] -= amount

        service = LifestyleService(
            ProtocolService(log),
            party=lambda _w: people,
            purse=lambda c: purses[c],
            pay=pay,
        )

        def days_passed(world: str, days: int, why: str, by: str, name: str) -> None:
            if why != "travel":
                service.charge(world, days, {"modest": 100}, by, name)

        clock = ClockService(GameStateStore(tmp_path / "lib"), None, days_passed=days_passed)
        clock.advance("w", 2 * DAY, "dm", "DM")  # two midnights: 2 gp, of which Ana has 2.5
        assert purses == {"a": 50, "b": 10_000}
        clock.advance("w", DAY, "dm", "DM", "travel")  # on the road: rations instead
        clock.advance("w", DAY, "dm", "DM")  # 1 gp due, 5 sp in the purse
        assert purses["a"] == 0
        assert log.newest(log_key("w"), limit=1)[0].what == "lifestyle:modest:1:50:50"

    def test_the_sheet_keeps_it(self, rogue: Table) -> None:
        assert rogue.act("lifestyle", level="wealthy").lifestyle == "wealthy"
        with pytest.raises(PlayError):
            rogue.act("lifestyle", level="palatial")


class TestContacts:
    def test_what_downtime_brings(self) -> None:
        assert made(
            "carouse", ["check:persuasion:17", "row:Character has made two allied contacts."]
        ) == ["ally", "ally"]
        assert made("carouse", ["row:Character has made a hostile contact."]) == ["enemy"]
        assert made(
            "service", ["row:You earn two favors.", "complication:Offended.", "rival:"]
        ) == [
            "favour",
            "favour",
            "rival",
        ]
        assert made("service", ["row:No effect."]) == []

    def test_kept_named_and_called_on(self, tmp_path: Path) -> None:
        log = ProtocolStore(tmp_path / "log")
        service = ContactService(GameStateStore(tmp_path / "lib"), ProtocolService(log))
        pc = PlayerCharacter(id="a", name="Ana", world_id="w", status="ready")
        allies = service.from_activity(pc, "carouse", ["row:three allied contacts"], "a", "Ana")
        assert [c.kind for c in allies] == ["ally"] * 3
        assert log.newest(log_key("w"), limit=1)[0].what == "contact:new:ally:3"
        named = service.edit("a", allies[0].id, "  Mara   the Fence ", "owes me")
        assert named.name == "Mara the Fence"
        assert service.call_on(pc, named.id, "a", "Ana").spent
        assert log.newest(log_key("w"), limit=1)[0].what == "contact:called:ally:Mara the Fence"
        service.remove("a", allies[1].id)
        assert len(service.of("a")) == 2
        with pytest.raises(ContactError):
            service.edit("b", allies[2].id, "x", "")
        with pytest.raises(ContactError):
            service.add(pc, "lover", "x", "")
