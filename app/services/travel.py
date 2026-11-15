"""The party on the road: a stretch of travel against the world's clock.

The gamemaster says how long the party travels (hours, or days of eight travelling
hours), at what pace, through what, and how often to check for an encounter. The pace
gives the distance: 2, 3 or 4 miles an hour (18, 24 or 30 miles a day), half that in
difficult terrain. Each check is a d20 that comes up on 18 or more; when it does and
an environment is chosen, its random-encounter table is rolled for the party's level
and the journey stops there, the clock at that moment, the rest of the way still ahead.

At the end of each travelling day every character eats a day's rations from their
pack (when supplies are kept); one with none goes hungry. Hunger follows the world's
rules: under the 2014 rules a character manages 3 + its Constitution modifier days
(at least one) and gains a level of exhaustion each further day; under the 2024 rules
the fifth day without food brings one, and each day after another. A day of eating
ends the count. A night between two travelling days is a long rest, when chosen, and
takes no exhaustion from hunger away. Water is left to the table.
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
# Miles an hour by pace.
PACES: dict[str, int] = {"slow": 2, "normal": 3, "fast": 4}
# Travelling hours in a day.
TRAVEL_DAY = 8
# An encounter check comes up on a d20 roll of this or more.
ENCOUNTER_ON = 18
# The most one stretch may cover: thirty days on the road.
MOST_HOURS = 30 * TRAVEL_DAY


class TravelError(Translatable):
    """A journey the rules refuse."""


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


def hunger_limit(con_mod: int, rules: str) -> int:
    """How many days without food pass before the first level of exhaustion."""
    return 4 if rules == "one" else max(1, 3 + con_mod)


class TravelService:
    """Moves the party along: the clock, rations, hunger, rests and encounter checks."""

    def __init__(
        self,
        clock: ClockService,
        protocol: ProtocolService | None,
        *,
        party: Callable[[str], Sequence[PlayerCharacter]],
        eat: Callable[[PlayerCharacter], bool],
        tire: Callable[[PlayerCharacter], None],
        con_mod: Callable[[PlayerCharacter], int],
        rest: Callable[[str], None],
        dice: Dice | None = None,
    ) -> None:
        """``party(world)`` the characters on the road; ``eat(pc)`` takes a day's
        rations from the pack (False: there were none); ``tire(pc)`` adds a level of
        exhaustion; ``con_mod(pc)`` its Constitution modifier; ``rest(world)`` a long
        rest for the party."""
        self._clock = clock
        self._protocol = protocol
        self._party = party
        self._eat = eat
        self._tire = tire
        self._con_mod = con_mod
        self._rest = rest
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
    ) -> Journey:
        """Travel until the time is up or an encounter stops the party; the journey is
        kept on the world's clock for the party view and logged."""
        if not 0 < plan.hours <= MOST_HOURS or plan.pace not in PACES:
            raise TravelError("travel.error.bad")
        party = [pc for pc in self._party(world_id) if not pc.in_wizard]
        levels = [max(1, pc.total_level) for pc in party] or [1]
        level = round(sum(levels) / len(levels))
        mph = PACES[plan.pace] / (2 if plan.difficult else 1)
        start = self._clock.of(world_id).minute
        hungry = dict(self._clock.of(world_id).hungry)
        journey = Journey(
            started=start,
            ended=start,
            pace=plan.pace,
            planned=plan.hours,
            difficult=plan.difficult,
            environment=plan.environment.key if plan.environment else "",
        )
        every = TRAVEL_DAY * 60 // plan.checks if plan.checks > 0 else 0
        travelled = 0  # minutes on the road
        elapsed = 0  # minutes on the clock, nights included
        left = plan.hours * 60
        stopped = False
        while left > 0 and not stopped:
            today = min(left, TRAVEL_DAY * 60 - travelled % (TRAVEL_DAY * 60))
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
            travelled += moved
            elapsed += moved
            left -= moved
            if stopped or travelled % (TRAVEL_DAY * 60):
                break
            # The end of a travelling day: supper, then the night if the road goes on.
            tired = self._supper(journey, party, hungry, plan.rations, rules)
            if left > 0:
                if plan.rest:
                    self._rest(world_id)
                elapsed += DAY - TRAVEL_DAY * 60
            for pc in tired:
                self._tire(pc)
                journey.exhausted.append(pc.name or "?")
        journey.hours = travelled // 60
        journey.miles = round(travelled / 60 * mph, 1)
        if elapsed:
            self._clock.advance(world_id, elapsed, by, by_name, "travel")
        clock = self._clock.of(world_id)
        journey.ended = clock.minute
        self._clock.keep(world_id, journey, hungry)
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
    ) -> list[PlayerCharacter]:
        """Everyone eats or goes hungry; those whose hunger now costs them a level of
        exhaustion (applied after the night's rest, which cannot take it away)."""
        tired: list[PlayerCharacter] = []
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
                tired.append(pc)
        return tired

    def _log(self, world_id: str, journey: Journey, by: str, by_name: str) -> None:
        """``travel:<pace>:<hours>:<miles>:<environment>:<hungry names>`` for everyone,
        and the encounter's row for the gamemaster."""
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
                    + ",".join(n.replace(":", " ").replace(",", " ") for n in journey.hungry)
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


__all__ = [
    "ENCOUNTER_ON",
    "PACES",
    "TRAVEL_DAY",
    "Plan",
    "TravelError",
    "TravelService",
    "hunger_limit",
]
