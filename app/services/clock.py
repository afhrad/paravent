"""A world's time in the game, and what runs out against it.

Each world keeps one clock: minutes since midnight of its first day ("Day 3, 14:20").
The gamemaster moves it on by hand — ten minutes, an hour, a day — or by a party rest,
and a fight's rounds add their six seconds each when it ends. Against it run timers: a
torch or lantern lit on the sheet, a spell cast with a duration of a minute or more,
and the gamemaster's own reminders. When the clock passes a timer's end, the timer is
logged and gone; a spell held by concentration ends the caster's concentration with it,
and one whose concentration ended earlier quietly disappears.

A bastion turn falls due every seven days (2024 rules); the clock says when.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from app.models.gamestate import ClockTimer, Journey, WorldClock
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

DAY = 24 * 60
BASTION_TURN = 7 * DAY
# The most a single move may add: a year.
MOST = 365 * DAY
# How long a light source burns, in minutes, by its name (the rules' prose: a torch
# and a candle an hour, a lamp or lantern six hours on a flask of oil).
LIGHTS = {
    "torch": 60,
    "candle": 60,
    "lamp": 360,
    "hooded lantern": 360,
    "bullseye lantern": 360,
}
# Light sources that burn oil rather than themselves, and the oil's names.
OIL_LIGHTS = frozenset({"lamp", "hooded lantern", "bullseye lantern"})
OIL = frozenset({"oil (flask)", "oil"})
# A spell duration's units in minutes; rounds are six seconds.
_UNITS = {"minute": 1, "hour": 60, "day": DAY, "week": 7 * DAY, "year": 365 * DAY}


class ClockError(Translatable):
    """A change the clock refuses."""


def light_minutes(name: str) -> int:
    """How long a light source of this name burns; 0 for anything else."""
    return LIGHTS.get(name.partition("|")[0].strip().lower(), 0)


def spell_minutes(duration: Iterable[Any]) -> tuple[int, bool] | None:
    """A spell's timed duration in minutes and whether it is held by concentration;
    None for an instant, permanent or shorter-than-a-minute spell."""
    for d in duration:
        if not isinstance(d, dict) or d.get("type") != "timed":
            continue
        amount = d.get("duration") or {}
        unit, count = str(amount.get("type", "")), amount.get("amount")
        if not isinstance(count, int):
            return None
        minutes = count // 10 if unit == "round" else count * _UNITS.get(unit, 0)
        return (minutes, bool(d.get("concentration"))) if minutes >= 1 else None
    return None


def day_of(minute: int) -> int:
    """The day a minute falls on, the first day being 1."""
    return minute // DAY + 1


def time_of(minute: int) -> str:
    """The time of day, "14:20"."""
    return f"{minute % DAY // 60:02d}:{minute % 60:02d}"


def part_of_day(minute: int) -> str:
    """night, dawn, morning, afternoon or evening — a key under ``clock.part``."""
    hour = minute % DAY // 60
    if hour < 5 or hour >= 21:
        return "night"
    if hour < 7:
        return "dawn"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


class ClockService:
    """Reads and writes ``library/clocks.json``; moves and timers go to the log."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        concentrating: Callable[[str], str | None] = lambda _c: None,
        end_concentration: Callable[[str, str, str], None] | None = None,
        changed: Callable[[WorldClock], None] | None = None,
        days_passed: Callable[[str, int, str, str, str], None] | None = None,
    ) -> None:
        """``concentrating(character)`` names the spell a character holds now (its
        reference, or None); ``end_concentration(character, spell, by)`` ends it when its
        timer runs out; ``changed(clock)`` tells open screens; ``days_passed(world,
        days, why, by, by_name)`` hears of each move past midnight (lifestyle costs)."""
        self._store = store
        self._protocol = protocol
        self._concentrating = concentrating
        self._end_concentration = end_concentration
        self._changed = changed
        self._days_passed = days_passed

    def of(self, world_id: str) -> WorldClock:
        """A world's clock: Day 1, 08:00 until it is first moved."""
        return self._store.clocks.find(world_id) or WorldClock(id=world_id)

    def active(self, world_id: str) -> list[ClockTimer]:
        """The timers still running, soonest first; a spell whose caster stopped
        concentrating on it is left out."""
        return sorted(
            (t for t in self.of(world_id).timers if not self._stale(t)), key=lambda t: t.ends
        )

    def of_character(self, world_id: str | None, character_id: str) -> list[ClockTimer]:
        """A character's running lights and spells."""
        if world_id is None:
            return []
        return [t for t in self.active(world_id) if t.character_id == character_id]

    def set(self, world_id: str, minute: int, by: str, by_name: str) -> WorldClock:
        """Put the clock at ``minute``; a timer it passes runs out, one it goes back
        before keeps its end."""
        clock = self.of(world_id)
        if minute < 0:
            raise ClockError("clock.error.bad_time")
        if minute > clock.minute:
            return self.advance(world_id, minute - clock.minute, by, by_name)
        self._log(world_id, by, by_name, f"clock:set:{minute}")
        return self._save(clock.model_copy(update={"minute": minute}))

    def advance(
        self, world_id: str, minutes: int, by: str, by_name: str, why: str = ""
    ) -> WorldClock:
        """Move the clock on; every timer it passes runs out, in order, logged."""
        if not 0 < minutes <= MOST:
            raise ClockError("clock.error.bad_time")
        clock = self.of(world_id)
        now = clock.minute + minutes
        done = sorted((t for t in clock.timers if t.ends <= now), key=lambda t: t.ends)
        kept = [t for t in clock.timers if t.ends > now]
        self._log(world_id, by, by_name, f"clock:{minutes}:{now}:{why}")
        for timer in done:
            self._run_out(world_id, timer, by, by_name)
        saved = self._save(clock.model_copy(update={"minute": now, "timers": kept}))
        days = now // DAY - clock.minute // DAY
        if days > 0 and self._days_passed is not None:
            self._days_passed(world_id, days, why, by, by_name)
        return saved

    def start(
        self,
        world_id: str,
        kind: str,
        label: str,
        minutes: int,
        *,
        character_id: str | None = None,
        concentration: bool = False,
    ) -> ClockTimer:
        """A timer from now. A character's new concentration spell replaces the one it
        held; a second light of the same kind burns beside the first."""
        label = " ".join(label.split())[:200]
        if kind not in ("light", "spell", "custom") or not label or not 0 < minutes <= MOST:
            raise ClockError("clock.error.bad_timer")
        clock = self.of(world_id)
        timers = [
            t
            for t in clock.timers
            if not (
                character_id is not None
                and t.character_id == character_id
                and t.kind == "spell"
                and (t.label.lower() == label.lower() or (concentration and t.concentration))
            )
        ]
        timer = ClockTimer(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            label=label,
            character_id=character_id,
            ends=clock.minute + minutes,
            concentration=concentration,
        )
        self._save(clock.model_copy(update={"timers": [*timers, timer]}))
        return timer

    def stop(self, world_id: str, timer_id: str, character_id: str | None = None) -> WorldClock:
        """Put a timer out early (a torch doused, a reminder dismissed); with
        ``character_id``, only one of that character's."""
        clock = self.of(world_id)
        kept = [
            t
            for t in clock.timers
            if t.id != timer_id or (character_id is not None and t.character_id != character_id)
        ]
        return self._save(clock.model_copy(update={"timers": kept}))

    def keep(
        self,
        world_id: str,
        journey: Journey | None,
        hungry: dict[str, int] | None = None,
        road: tuple[int, int | None] | None = None,
    ) -> WorldClock:
        """Keep the party's last journey (None forgets it) and, with ``hungry``, how
        many days each character has gone without food; ``road`` is the minutes
        travelled on a day left partway and that day (app/services/travel.py)."""
        update: dict[str, Any] = {"journey": journey}
        if hungry is not None:
            update["hungry"] = hungry
        if road is not None:
            update["road"], update["road_day"] = road
        return self._save(self.of(world_id).model_copy(update=update))

    def dated(self, world_id: str, calendar: str, epoch: int, era: str) -> WorldClock:
        """Date the days by a calendar (its key; "" for plain day numbers): ``epoch`` is
        the calendar's day of the clock's first day (app/services/calendar.py)."""
        update = {"calendar": calendar, "calendar_epoch": epoch, "calendar_era": era}
        return self._save(self.of(world_id).model_copy(update=update))

    def _stale(self, timer: ClockTimer) -> bool:
        if not (timer.concentration and timer.character_id):
            return False
        held = self._concentrating(timer.character_id)
        return held is None or held.lower() != timer.label.lower()

    def _run_out(self, world_id: str, timer: ClockTimer, by: str, by_name: str) -> None:
        if self._stale(timer):
            return
        if timer.concentration and timer.character_id and self._end_concentration is not None:
            self._end_concentration(timer.character_id, timer.label, by)
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{timer.character_id}" if timer.character_id else "",
                    subject_name=self._name(timer.character_id),
                    kind=EntryKind.CUSTOM,
                    what=f"timer:{timer.kind}:{timer.label}",
                    visibility=Visibility.GM if timer.kind == "custom" else Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            ]
        )

    def _name(self, character_id: str | None) -> str:
        pc = self._store.characters.find(character_id) if character_id else None
        return (pc.name or "?") if pc is not None else ""

    def _log(self, world_id: str, by: str, by_name: str, what: str) -> None:
        """A move of the clock: ``clock:<minutes>:<now>:<why>`` or ``clock:set:<now>``."""
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    scope=Scope(world=world_id),
                )
            ]
        )

    def _save(self, clock: WorldClock) -> WorldClock:
        clock.touch()
        saved = self._store.clocks.upsert(clock)
        if self._changed is not None:
            self._changed(saved)
        return saved


def bastions_due(minute: int, bastions: Sequence[tuple[str, int | None]]) -> list[str]:
    """Of ``(name, last turn)`` pairs, the bastions whose turn is due: seven days after
    the last, or never had one."""
    return [name for name, last in bastions if last is None or minute - last >= BASTION_TURN]


__all__ = [
    "BASTION_TURN",
    "DAY",
    "LIGHTS",
    "OIL",
    "OIL_LIGHTS",
    "ClockError",
    "ClockService",
    "bastions_due",
    "day_of",
    "light_minutes",
    "part_of_day",
    "spell_minutes",
    "time_of",
]
