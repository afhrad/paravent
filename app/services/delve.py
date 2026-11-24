"""Exploring a dungeon: random encounter checks run by the world's clock.

The guides check for a wandering encounter at an interval while the party explores
(every 10 minutes, every hour), a d20 coming up on 18 or more. The gamemaster sets the
table, the interval and the chance once; every move of the clock (a step, a rest, a
fight's rounds, downtime, but not the road, which rolls its own) then rolls the checks it
passes. A check that comes up rolls the table for the party's level, waits on the clock
panel to be made an encounter in one press, and holds further checks until the
gamemaster has dealt with it. Everything goes to the log for the gamemaster only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.models.gamestate import Delve, DelveFinding, EncounterMonster
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.clock import ClockService
from app.services.dice import Dice
from app.services.encounter_roll import EncounterRoller, Resolve
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.encounter_tables import Environment

# How often a check is rolled, in minutes, as the dialog offers them.
INTERVALS = (10, 30, 60, 240, 480)
# The d20 a check comes up on, at least: 18 as the guides suggest.
CHANCES = (20, 19, 18, 17, 16, 15)
DEFAULT_ON = 18


class DelveError(Translatable):
    """Exploring could not start as asked."""


class DelveService:
    """Starts and ends a party's exploring and rolls its encounter checks."""

    def __init__(
        self,
        clock: ClockService,
        protocol: ProtocolService | None,
        *,
        party: Callable[[str], Sequence[PlayerCharacter]],
        table: Callable[[str], Environment | None],
        resolver: Callable[[str], Resolve],
        dice: Dice | None = None,
    ) -> None:
        """``party(world)`` the characters exploring, ``table(key)`` an encounter table,
        ``resolver(world)`` resolves a row's creatures in the world's books."""
        self._clock = clock
        self._protocol = protocol
        self._party = party
        self._table = table
        self._resolver = resolver
        self._dice = dice or Dice()

    def of(self, world_id: str) -> Delve | None:
        """The party's exploring in the world, if it is exploring."""
        return self._clock.of(world_id).delve

    def start(
        self, world_id: str, environment: str, every: int, on: int, by: str, by_name: str
    ) -> Delve:
        """Start exploring now (or change how): the first check comes ``every`` minutes
        on; a check that came up and still waits is kept."""
        if environment and self._table(environment) is None:
            raise DelveError("delve.error.bad")
        if not 1 <= every <= 7 * 24 * 60 or on not in CHANCES:
            raise DelveError("delve.error.bad")
        now = self._clock.of(world_id).minute
        held = self.of(world_id)
        delve = Delve(
            environment=environment,
            every=every,
            on=on,
            started=held.started if held is not None else now,
            checked=now,
            rolled=held.rolled if held is not None else 0,
            found=held.found if held is not None else None,
        )
        self._clock.explore(world_id, delve)
        table = self._table(environment) if environment else None
        name = table.name.replace(":", " ") if table is not None else ""
        self._log(world_id, by, by_name, f"delve:start:{every}:{name}")
        return delve

    def leave(self, world_id: str, by: str, by_name: str) -> None:
        """The party leaves the dungeon: no more checks."""
        held = self.of(world_id)
        if held is None:
            return
        self._clock.explore(world_id, None)
        self._log(world_id, by, by_name, f"delve:stop:{held.rolled}")

    def check(self, world_id: str, by: str, by_name: str) -> DelveFinding | None:
        """One check now, whatever the clock says (the party made a noise); the next
        regular check still comes when it is due."""
        held = self.of(world_id)
        if held is None or held.found is not None:
            raise DelveError("delve.error.bad")
        now = self._clock.of(world_id).minute
        delve = self._roll(world_id, held, [now], by, by_name)
        self._clock.explore(world_id, delve)
        return delve.found

    def dismiss(self, world_id: str) -> None:
        """The gamemaster has dealt with the check that came up: checks go on from now,
        the time that passed meanwhile not rolled again."""
        held = self.of(world_id)
        if held is None:
            return
        now = self._clock.of(world_id).minute
        self._clock.explore(world_id, held.model_copy(update={"found": None, "checked": now}))

    def made(self, world_id: str, encounter_id: str) -> None:
        """The check that came up was made into an encounter."""
        held = self.of(world_id)
        if held is None or held.found is None:
            return
        found = held.found.model_copy(update={"encounter_id": encounter_id})
        self._clock.explore(world_id, held.model_copy(update={"found": found}))

    def passed(self, world_id: str, before: int, now: int, by: str, by_name: str) -> None:
        """The clock moved from ``before`` to ``now``: roll every check it passed, up to
        the first that comes up. One waiting holds them."""
        held = self.of(world_id)
        if held is None or held.found is not None or now <= before:
            return
        # A clock set back since the last check counts from where it was set to.
        start = min(held.checked, before)
        due = list(range(start + held.every, now + 1, held.every))
        if not due:
            return
        delve = self._roll(world_id, held, due, by, by_name)
        self._clock.explore(world_id, delve)

    def _roll(self, world_id: str, held: Delve, due: Sequence[int], by: str, by_name: str) -> Delve:
        """Roll the checks at the minutes ``due``, stopping at the first that comes up."""
        rolled = held.rolled
        checked = held.checked
        for at in due:
            rolled += 1
            checked = max(checked, at)
            roll = self._dice.die(20)
            if roll >= held.on:
                found = self._meet(world_id, held.environment, at, roll)
                self._log(world_id, by, by_name, f"delve_encounter:{found.encounter}")
                return held.model_copy(update={"rolled": rolled, "checked": at, "found": found})
        return held.model_copy(update={"rolled": rolled, "checked": checked})

    def _meet(self, world_id: str, environment: str, at: int, check: int) -> DelveFinding:
        found = DelveFinding(at=at, check=check)
        table = self._table(environment) if environment else None
        if table is None:
            return found
        party = [pc for pc in self._party(world_id) if not pc.in_wizard]
        levels = [max(1, pc.total_level) for pc in party] or [1]
        level = round(sum(levels) / len(levels))
        rolled = EncounterRoller(self._resolver(world_id), self._dice).roll(table, level)
        if rolled is None:
            return found
        return found.model_copy(
            update={
                "encounter": rolled.text,
                "encounter_roll": rolled.roll,
                "monsters": [
                    EncounterMonster(name=n, source=s, count=c) for n, s, c in rolled.monsters
                ],
                "unresolved": rolled.unresolved,
            }
        )

    def _log(self, world_id: str, by: str, by_name: str, what: str) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.GM,
                    scope=Scope(world=world_id),
                )
            ]
        )


__all__ = ["CHANCES", "DEFAULT_ON", "INTERVALS", "DelveError", "DelveService"]
