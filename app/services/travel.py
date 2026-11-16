"""The party on the road: a stretch of travel against the world's clock.

The gamemaster says how long the party travels (hours, or days of eight travelling
hours), at what pace, through what, and how often to check for an encounter. The pace
gives the distance: 2, 3 or 4 miles an hour (18, 24 or 30 miles a day), half that in
difficult terrain. Mounted, the party covers twice the distance in the first hour of
each day, after which the mounts need a rest. On board a vessel there is no pace: the
vessel's speed is the party's, and with a crew working in shifts it sails around the
clock. Each check is a d20 that comes up on 18 or more; when it does and an
environment is chosen, its random-encounter table is rolled for the party's level and
the journey stops there, the clock at that moment, the rest of the way still ahead.
One more check may fall in the night; one that comes up wakes the party.

A stretch that ends before the day's travelling hours are done leaves the rest of that
day for the next stretch, so supper comes at the end of the day either way.

At the end of each travelling day every character eats a day's rations from their
pack (when supplies are kept); one with none goes hungry. Hunger follows the world's
rules: under the 2014 rules a character manages 3 + its Constitution modifier days
(at least one) and gains a level of exhaustion each further day; under the 2024 rules
the fifth day without food brings one, and each day after another. A day of eating
ends the count. Water is enough, half or none for the day: under the 2014 rules half
calls for a DC 15 Constitution save, failed or with none a level of exhaustion (two
for a character already exhausted); under the 2024 rules only less than half costs a
level. A night between two travelling days is a long rest, when chosen. Exhaustion
from hunger or thirst stays through any rest until the character has eaten (or drunk
its fill: a day with enough water). The weather may be rolled for each day on the
books' table.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.models.gamestate import EncounterMonster, Journey
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.clock import DAY, ClockService
from app.services.dice import Dice
from app.services.encounter_roll import EncounterRoller, Resolve
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.encounter_tables import Environment

Pace = Literal["slow", "normal", "fast"]
Water = Literal["enough", "half", "none"]
# Miles an hour by pace.
PACES: dict[str, int] = {"slow": 2, "normal": 3, "fast": 4}
WATER = ("enough", "half", "none")
# Travelling hours in a day.
TRAVEL_DAY = 8
# An encounter check comes up on a d20 roll of this or more.
ENCOUNTER_ON = 18
# The most one stretch may cover: thirty days on the road.
MOST_HOURS = 30 * TRAVEL_DAY
# The save against thirst on half water (2014 rules).
THIRST_DC = 15


class TravelError(Translatable):
    """A journey the rules refuse."""


@dataclass(frozen=True)
class Vessel:
    """A ship or airship the party travels on: its name and speed in miles an hour."""

    name: str
    mph: float
    around_the_clock: bool = False


@dataclass(frozen=True)
class Plan:
    """What the gamemaster set out."""

    hours: int
    pace: Pace = "normal"
    difficult: bool = False
    environment: Environment | None = None
    checks: int = 1  # encounter checks per travelling day
    rations: bool = True
    rest: bool = True
    water: Water = "enough"
    mounted: bool = False
    vessel: Vessel | None = None
    night_check: bool = False
    weather: bool = False


def hunger_limit(con_mod: int, rules: str) -> int:
    """How many days without food pass before the first level of exhaustion."""
    return 4 if rules == "one" else max(1, 3 + con_mod)


class TravelService:
    """Moves the party along: the clock, rations, water, rests and encounter checks."""

    def __init__(
        self,
        clock: ClockService,
        protocol: ProtocolService | None,
        *,
        party: Callable[[str], Sequence[PlayerCharacter]],
        eat: Callable[[PlayerCharacter], bool],
        tire: Callable[[PlayerCharacter, str], None],
        con_mod: Callable[[PlayerCharacter], int],
        rest: Callable[[str], None],
        con_save: Callable[[PlayerCharacter], int] | None = None,
        quench: Callable[[PlayerCharacter], None] | None = None,
        dice: Dice | None = None,
    ) -> None:
        """``party(world)`` the characters on the road; ``eat(pc)`` takes a day's
        rations from the pack (False: there were none); ``tire(pc, cause)`` adds a
        level of exhaustion from ``hunger`` or ``thirst``; ``quench(pc)`` marks a day
        with enough water, which ends thirst's hold; ``con_mod(pc)`` its Constitution
        modifier and ``con_save(pc)`` its Constitution saving throw bonus (the modifier
        when not given); ``rest(world)`` a long rest for the party."""
        self._clock = clock
        self._protocol = protocol
        self._party = party
        self._eat = eat
        self._tire = tire
        self._con_mod = con_mod
        self._con_save = con_save or con_mod
        self._rest = rest
        self._quench = quench
        self._dice = dice or Dice()

    def travel(
        self,
        world_id: str,
        plan: Plan,
        *,
        rules: str,
        resolve: Resolve,
        by: str,
        by_name: str,
        weather: Callable[[], str] | None = None,
    ) -> Journey:
        """Travel until the time is up or an encounter stops the party; the journey is
        kept on the world's clock for the party view and logged. ``weather()`` rolls
        a day's weather on the books' table ("temperature|wind|precipitation")."""
        if not 0 < plan.hours <= MOST_HOURS or plan.pace not in PACES:
            raise TravelError("travel.error.bad")
        if plan.water not in WATER or (plan.vessel is not None and plan.vessel.mph <= 0):
            raise TravelError("travel.error.bad")
        party = [pc for pc in self._party(world_id) if not pc.in_wizard]
        levels = [max(1, pc.total_level) for pc in party] or [1]
        level = round(sum(levels) / len(levels))
        if plan.vessel is not None:
            mph = plan.vessel.mph
        else:
            mph = PACES[plan.pace] / (2 if plan.difficult else 1)
        day_length = (
            DAY if plan.vessel is not None and plan.vessel.around_the_clock else TRAVEL_DAY * 60
        )
        held = self._clock.of(world_id)
        start = held.minute
        hungry = dict(held.hungry)
        journey = Journey(
            started=start,
            ended=start,
            pace=plan.pace,
            planned=plan.hours,
            difficult=plan.difficult and plan.vessel is None,
            environment=plan.environment.key if plan.environment else "",
            water=plan.water,
            mounted=plan.mounted and plan.vessel is None,
            vessel=plan.vessel.name if plan.vessel else "",
        )
        # The road already covered today, when the last stretch stopped partway.
        travelled = held.road if held.road_day == start // DAY and held.road < day_length else 0
        offset = travelled
        every = TRAVEL_DAY * 60 // plan.checks if plan.checks > 0 else 0
        elapsed = 0  # minutes on the clock, nights included
        miles = 0.0
        left = plan.hours * 60
        stopped = False
        if plan.weather and weather is not None and offset == 0:
            journey.weather.append(weather())
        while left > 0 and not stopped:
            into = travelled % day_length
            today = min(left, day_length - into)
            moved = today
            if every:
                check_at = (travelled // every + 1) * every
                while check_at <= travelled + today:
                    roll = self._dice.die(20)
                    if roll >= ENCOUNTER_ON:
                        moved = check_at - travelled
                        stopped = True
                        self._meet(journey, roll, plan.environment, level, resolve)
                        break
                    check_at += every
            miles += moved / 60 * mph
            if journey.mounted:
                miles += max(0, min(60, into + moved) - into) / 60 * mph
            travelled += moved
            elapsed += moved
            left -= moved
            if stopped or travelled % day_length:
                break
            # The end of a travelling day: supper, then the night if the road goes on.
            tired = self._supper(journey, party, hungry, plan.rations, rules)
            tired += self._drink(journey, party, plan.water, rules)
            if left > 0:
                night = DAY - day_length
                if plan.night_check and night > 0:
                    roll = self._dice.die(20)
                    if roll >= ENCOUNTER_ON:
                        # Woken partway through the night: no rest, and the road stops.
                        stopped = True
                        journey.night = True
                        self._meet(journey, roll, plan.environment, level, resolve)
                        elapsed += self._dice.die(max(1, night // 60)) * 60
                if not stopped:
                    if plan.rest:
                        self._rest(world_id)
                    elapsed += night
                    if plan.weather and weather is not None:
                        journey.weather.append(weather())
            for pc, cause in tired:
                self._tire(pc, cause)
                journey.exhausted.append(pc.name or "?")
        journey.hours = (travelled - offset) // 60
        journey.miles = round(miles, 1)
        if elapsed:
            self._clock.advance(world_id, elapsed, by, by_name, "travel")
        clock = self._clock.of(world_id)
        journey.ended = clock.minute
        part = travelled % day_length
        self._clock.keep(
            world_id,
            journey,
            hungry,
            road=(part, clock.minute // DAY) if part and not journey.night else (0, None),
        )
        self._log(world_id, journey, by, by_name)
        return journey

    def _meet(
        self,
        journey: Journey,
        check: int,
        environment: Environment | None,
        level: int,
        resolve: Resolve,
    ) -> None:
        journey.check = check
        if environment is None:
            return
        rolled = EncounterRoller(resolve, self._dice).roll(environment, level)
        if rolled is None:
            return
        journey.encounter = rolled.text
        journey.encounter_roll = rolled.roll
        journey.monsters = [
            EncounterMonster(name=n, source=s, count=c) for n, s, c in rolled.monsters
        ]
        journey.unresolved = rolled.unresolved

    def _supper(
        self,
        journey: Journey,
        party: Sequence[PlayerCharacter],
        hungry: dict[str, int],
        rations: bool,
        rules: str,
    ) -> list[tuple[PlayerCharacter, str]]:
        """Everyone eats or goes hungry; those whose hunger now costs them a level of
        exhaustion (applied after the night's rest, which cannot take it away)."""
        tired: list[tuple[PlayerCharacter, str]] = []
        if not rations:
            return tired
        for pc in party:
            if self._eat(pc):
                journey.rations += 1
                hungry.pop(pc.id, None)
                continue
            days = hungry.get(pc.id, 0) + 1
            hungry[pc.id] = days
            if (pc.name or "?") not in journey.hungry:
                journey.hungry.append(pc.name or "?")
            if days > hunger_limit(self._con_mod(pc), rules):
                tired.append((pc, "hunger"))
        return tired

    def _drink(
        self,
        journey: Journey,
        party: Sequence[PlayerCharacter],
        water: str,
        rules: str,
    ) -> list[tuple[PlayerCharacter, str]]:
        """Each character short of water at the day's end: those it costs a level of
        exhaustion, once for each level (applied after the night's rest)."""
        tired: list[tuple[PlayerCharacter, str]] = []
        if water == "enough":
            for pc in party:
                if self._quench is not None:
                    self._quench(pc)
            return tired
        for pc in party:
            if rules == "one":
                levels = 1 if water == "none" else 0
            else:
                failed = water == "none" or (self._dice.die(20) + self._con_save(pc) < THIRST_DC)
                levels = (2 if pc.state.exhaustion > 0 else 1) if failed else 0
            if levels and (pc.name or "?") not in journey.thirsty:
                journey.thirsty.append(pc.name or "?")
            tired.extend([(pc, "thirst")] * levels)
        return tired

    def _log(self, world_id: str, journey: Journey, by: str, by_name: str) -> None:
        """``travel:<pace>:<hours>:<miles>:<environment>:<hungry names>:<thirsty
        names>:<vessel>`` for everyone, and the encounter's row for the gamemaster."""
        if self._protocol is None:
            return
        entries = [
            ProtocolEntry(
                actor=by,
                actor_name=by_name,
                kind=EntryKind.CUSTOM,
                what=(
                    f"travel:{journey.pace}:{journey.hours}:{journey.miles}:"
                    f"{journey.environment.partition('|')[0]}:"
                    f"{_names(journey.hungry)}:{_names(journey.thirsty)}:"
                    f"{journey.vessel.replace(':', ' ')}"
                ),
                scope=Scope(world=world_id),
            )
        ]
        if journey.check is not None:
            entries.append(
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    kind=EntryKind.CUSTOM,
                    what=f"travel_encounter:{journey.encounter}",
                    visibility=Visibility.GM,
                    scope=Scope(world=world_id),
                )
            )
        self._protocol.append(entries)


def _names(names: Sequence[str]) -> str:
    return ",".join(n.replace(":", " ").replace(",", " ") for n in names)


__all__ = [
    "ENCOUNTER_ON",
    "PACES",
    "THIRST_DC",
    "TRAVEL_DAY",
    "WATER",
    "Plan",
    "TravelError",
    "TravelService",
    "Vessel",
    "hunger_limit",
]
