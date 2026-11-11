"""Dice expressions, plain-text entries, and the live-update hub."""

from __future__ import annotations

import asyncio
import random
import threading

import pytest

from app.services.dice import Advantage, Dice, DiceError, is_expression
from app.services.events import ChangeHub, revision_stream
from app.services.text import first_dice, paragraphs, strip_tags


class Scripted(random.Random):
    """A random source that returns the given die faces, in order."""

    def __init__(self, *values: int) -> None:
        super().__init__(0)
        self.values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self.values.pop(0)


class TestDice:
    def test_expression(self) -> None:
        roll = Dice(Scripted(3, 5)).roll("2d6+3")
        assert roll.total == 11
        assert roll.rolls == (3, 5)
        assert "2d6[3,5]" in roll.detail

    def test_subtraction_and_mixed_dice(self) -> None:
        assert Dice(Scripted(4, 2)).roll("1d8 + 1d4 - 1").total == 5

    @pytest.mark.parametrize("text", ["", "fireball", "2d", "d", "3+", "1d6*2"])
    def test_not_an_expression(self, text: str) -> None:
        assert not is_expression(text)
        with pytest.raises(DiceError):
            Dice().roll(text)

    def test_absurd_dice_refused(self) -> None:
        with pytest.raises(DiceError):
            Dice().roll("1000d6")

    @pytest.mark.parametrize(
        ("adv", "kept"),
        [(Advantage.ADVANTAGE, 17), (Advantage.DISADVANTAGE, 4), (Advantage.NORMAL, 4)],
    )
    def test_advantage(self, adv: Advantage, kept: int) -> None:
        roll = Dice(Scripted(4, 17)).d20(2, adv)
        assert roll.natural == kept
        assert roll.total == kept + 2

    def test_both_d20s_are_shown(self) -> None:
        assert "d20[4,17]" in Dice(Scripted(4, 17)).d20(0, Advantage.ADVANTAGE).detail

    @pytest.mark.parametrize(("face", "kind"), [(20, "crit"), (1, "fumble"), (12, "")])
    def test_crit_and_fumble(self, face: int, kind: str) -> None:
        assert Dice(Scripted(face)).d20(5).kind == kind

    @pytest.mark.parametrize(
        ("raw", "adv"), [("1", 1), ("-1", -1), ("0", 0), ("", 0), ("x", 0), (None, 0)]
    )
    def test_parse_advantage(self, raw: str | None, adv: int) -> None:
        assert Advantage.parse(raw) == adv


class TestText:
    @pytest.mark.parametrize(
        ("raw", "plain"),
        [
            ("Deals {@damage 8d6} fire damage.", "Deals 8d6 fire damage."),
            ("You are {@condition prone||knocked down}.", "You are knocked down."),
            ("{@creature goblin|MM}", "goblin"),
            (
                "{@atk mw} {@hit 5} to hit. {@h}7 damage.",
                "Melee Weapon Attack: +5 to hit. Hit: 7 damage.",
            ),
            ("Make a {@dc 15} save.", "Make a DC 15 save."),
            ("{@note see {@spell fireball}}", "see fireball"),
            ("{@deity Bahamut|Nonhuman|PHB}", "Bahamut"),
        ],
    )
    def test_strip_tags(self, raw: str, plain: str) -> None:
        assert strip_tags(raw) == plain

    def test_paragraphs(self) -> None:
        entries = [
            "First {@b bold}.",
            {"type": "entries", "name": "Sculpt Spells", "entries": ["You can protect allies."]},
            {
                "type": "list",
                "items": ["One", {"type": "item", "name": "Two.", "entries": ["Second"]}],
            },
            {"type": "table", "caption": "Wild Magic"},
        ]
        assert paragraphs(entries) == [
            "First bold.",
            "Sculpt Spells. You can protect allies.",
            "• One",
            "• Two. Second",
            "[Wild Magic]",
        ]

    def test_first_dice(self) -> None:
        assert first_dice(["A {@damage 2d6} then {@damage 1d4}"]) == "2d6"
        assert first_dice(["No dice here."]) is None


class TestHub:
    async def test_publish_reaches_subscribers_of_that_key(self) -> None:
        hub = ChangeHub()
        mine, other = hub.subscribe("a"), hub.subscribe("b")
        hub.publish("a", "rev1")
        assert await asyncio.wait_for(mine.get(), 1) == "rev1"
        assert other.empty()

    async def test_unsubscribe(self) -> None:
        hub = ChangeHub()
        queue = hub.subscribe("a")
        hub.unsubscribe("a", queue)
        assert hub.subscriber_count("a") == 0
        hub.publish("a", "x")  # nobody listening: no error

    async def test_publish_from_another_thread(self) -> None:
        hub = ChangeHub()
        queue = hub.subscribe("a")
        thread = threading.Thread(target=hub.publish, args=("a", "from-thread"))
        thread.start()
        thread.join()
        assert await asyncio.wait_for(queue.get(), 1) == "from-thread"

    async def test_stream_sends_current_then_changes_then_ends(self) -> None:
        hub = ChangeHub()

        async def connected() -> bool:
            return False

        stream = revision_stream(hub, "a", "rev0", connected, heartbeat=0.05)
        assert await anext(stream) == "retry: 2000\n\n"
        assert await anext(stream) == "event: revision\ndata: rev0\n\n"
        assert await anext(stream) == ": ping\n\n"  # heartbeat while idle
        hub.publish("a", "rev1")
        assert await anext(stream) == "event: revision\ndata: rev1\n\n"
        hub.publish("a", None)
        assert await anext(stream) == "event: revision\ndata: \n\n"  # deleted
        hub.close()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert hub.subscriber_count("a") == 0

    async def test_stream_stops_when_the_browser_leaves(self) -> None:
        hub = ChangeHub()

        async def gone() -> bool:
            return True

        stream = revision_stream(hub, "a", None, gone, heartbeat=0.01)
        await anext(stream)
        await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert hub.subscriber_count("a") == 0


class TestShutdownSignals:
    """Open streams must end when the server is told to stop, or shutdown waits forever."""

    async def test_a_stop_signal_closes_streams_and_is_passed_on(self) -> None:
        import signal

        from app.services.events import close_on_shutdown_signals

        seen: list[int] = []
        original = signal.signal(signal.SIGTERM, lambda signum, _frame: seen.append(signum))
        try:
            hub = ChangeHub()
            queue = hub.subscribe("a")
            restore = close_on_shutdown_signals(hub)
            signal.raise_signal(signal.SIGTERM)
            await asyncio.sleep(0.01)  # the close is scheduled onto the loop
            assert seen == [signal.SIGTERM]  # uvicorn's handler still runs
            assert not queue.empty()  # and the stream got its end marker
            restore()
            signal.raise_signal(signal.SIGTERM)
            assert seen == [signal.SIGTERM, signal.SIGTERM]
            assert queue.qsize() == 1  # restored: no second close
        finally:
            signal.signal(signal.SIGTERM, original)
