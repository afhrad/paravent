"""A chase, run by the gamemaster from the party view (the guides' chase rules).

A chase has a quarry and pursuers: characters of the world and creatures of its books,
each rolling initiative as it begins. The gamemaster keeps the distance between the
quarry and the lead pursuer. Each participant may Dash freely 3 + its Constitution
modifier times (at least once under the 2024 rules); each Dash beyond that calls for a
DC 10 Constitution check (2014) or saving throw (2024), and failing it costs a level of
exhaustion. A participant with five levels drops out.

At the end of each participant's turn a die is rolled on the environment's
complication table (the 2014 guide's d20, where 11–20 is no complication; the 2024
guide's d12, where 7–12 is none), and a complication affects the next participant in
initiative order. At the end of the round each quarry still running
makes a Dexterity (Stealth) check, which fails if it never left the lead pursuer's
sight; a total above the highest passive Perception of the pursuers still running is
an escape. A character's checks are rolled on its sheet, into its log; a creature's
with its statblock's numbers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.models.gamestate import Chase, ChaseRunner
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import Rows, row_for
from app.services.dice import Advantage, Dice
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.services.text import strip_tags
from app.store.gamestate import GameStateStore

# The DC of the Constitution roll for a Dash past the free ones.
DASH_DC = 10
# Exhaustion levels at which a participant drops out.
OUT_AT = 5
ENVIRONMENTS = ("urban", "wilderness")


class ChaseError(Translatable):
    """A chase the rules refuse."""


@dataclass(frozen=True)
class Runner:
    """A participant as the start form names it."""

    name: str
    side: str
    character_id: str | None = None
    monster: str = ""
    initiative_bonus: int = 0
    con_mod: int = 0
    stealth: int = 0
    passive: int = 10


# ``(character id, kind, subject, advantage)`` → the total rolled on its sheet.
SheetRoll = Callable[[str, str, str, Advantage], int]


def free_dashes(con_mod: int, rules: str) -> int:
    """How many Dash actions a participant takes before they tire it."""
    return max(1 if rules == "one" else 0, 3 + con_mod)


def complication(rows: Rows | None, roll: int) -> str:
    """The complication a roll gives on a table, as plain text; "" for none."""
    row = row_for(rows or [], roll)
    if row is None or len(row) < 2:
        return ""
    text = " ".join(strip_tags(row[1]).split())
    return "" if text.lower().startswith(("no complication", "there is no complication")) else text


class ChaseService:
    """Starts, runs and ends each world's chase; every step goes to the log."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        sheet_roll: SheetRoll,
        tire: Callable[[str], int | None],
        dice: Dice | None = None,
        changed: Callable[[str], None] | None = None,
    ) -> None:
        """``sheet_roll`` rolls a character's check or save on its sheet; ``tire(id)``
        gives a character a level of exhaustion; ``changed(world)`` tells open
        screens."""
        self._store = store
        self._protocol = protocol
        self._sheet_roll = sheet_roll
        self._tire = tire
        self._dice = dice or Dice()
        self._changed = changed

    def of(self, world_id: str) -> Chase | None:
        return self._store.chases.find(world_id)

    def start(
        self,
        world_id: str,
        runners: Sequence[Runner],
        *,
        environment: str,
        distance: int,
        rules: str,
        by: str,
        by_name: str,
    ) -> Chase:
        """Begin a chase: everyone rolls initiative with its bonus."""
        if environment not in ENVIRONMENTS or not 0 <= distance <= 10_000:
            raise ChaseError("chase.error.bad")
        sides = {r.side for r in runners}
        if not {"quarry", "pursuer"} <= sides or sides - {"quarry", "pursuer"}:
            raise ChaseError("chase.error.sides")
        rolled = []
        for n, runner in enumerate(runners):
            # Rolled here, never on the sheet: a character in a running fight keeps the
            # fight's initiative.
            total = self._dice.d20(runner.initiative_bonus).total
            rolled.append((total, runner.initiative_bonus, -n, runner))
        rolled.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
        chase = Chase(
            id=world_id,
            environment=environment,
            rules="one" if rules == "one" else "classic",
            distance=distance,
            runners=[
                ChaseRunner(
                    name=r.name,
                    side=r.side,
                    character_id=r.character_id,
                    monster=r.monster,
                    initiative=total,
                    con_mod=r.con_mod,
                    stealth=r.stealth,
                    passive=r.passive,
                    free_dashes=free_dashes(r.con_mod, rules),
                )
                for total, _bonus, _n, r in rolled
            ],
        )
        self._log(world_id, by, by_name, [f"chase:start:{environment}:{distance}"])
        return self._save(chase)

    def dash(self, world_id: str, index: int, by: str, by_name: str) -> Chase:
        """A participant takes the Dash action: free while it has some, else a DC 10
        Constitution roll, a failure costing a level of exhaustion."""
        chase, runner = self._runner(world_id, index)
        runner.dashes += 1
        what = f"chase:dash:{_safe(runner.name)}:{runner.dashes}"
        if runner.dashes > runner.free_dashes:
            kind = "save" if chase.rules == "one" else "check"
            if runner.character_id:
                total = self._sheet_roll(runner.character_id, kind, "con", Advantage.NORMAL)
            else:
                total = self._dice.d20(runner.con_mod).total
            failed = total < DASH_DC
            what += f":{total}:{int(not failed)}"
            if failed:
                runner.exhaustion += 1
                # A character counts the levels it came with too.
                level = self._tire(runner.character_id) if runner.character_id else None
                if (level if level is not None else runner.exhaustion) >= OUT_AT:
                    runner.out = True
                    what += ":out"
                    if chase.turn == index:
                        # Out on its own turn: the turn passes on, with no complication.
                        chase.turn = _onward(chase, index)
        self._log(world_id, by, by_name, [what])
        return self._save(chase)

    def end_turn(
        self,
        world_id: str,
        rows: Rows | None,
        die: int,
        by: str,
        by_name: str,
        translated: Mapping[str, Rows] | None = None,
    ) -> Chase:
        """The participant whose turn it is ends it: the complication die (``die``
        faces, as the table is printed) is rolled on ``rows``, and what it brings falls
        on the next participant still running; ``translated`` holds the table in other
        languages, by language, so each reader reads the complication in theirs."""
        chase = self._running(world_id)
        active = [n for n, r in enumerate(chase.runners) if _running(r)]
        if not active or chase.turn >= len(chase.runners):
            raise ChaseError("chase.error.round")
        current = _onward(chase, chase.turn)
        if current >= len(chase.runners):
            chase.turn = current
            return self._save(chase)
        roll = self._dice.die(die)
        text = complication(rows, roll)
        later = [n for n in active if n > current]
        following = later[0] if later else active[0]
        lines = []
        if text and following != current:
            chase.runners[following].complication = text
            chase.runners[following].complication_texts = {
                lang: said
                for lang, table in (translated or {}).items()
                if (said := complication(table, roll))
            }
            lines.append(
                f"chase:complication:{_safe(chase.runners[following].name)}:{roll}:{_safe(text)}"
            )
        chase.runners[current].complication = ""
        chase.runners[current].complication_texts = {}
        # The last turn of the round leaves the escape checks to come.
        chase.turn = later[0] if later else len(chase.runners)
        self._log(world_id, by, by_name, lines)
        return self._save(chase)

    def round_end(
        self,
        world_id: str,
        in_sight: Iterable[int],
        advantage: dict[int, Advantage],
        by: str,
        by_name: str,
    ) -> Chase:
        """Each quarry still running tries to slip away: a Dexterity (Stealth) check
        against the highest passive Perception of the pursuers still running, failed
        when it never left the lead pursuer's sight. Then the next round."""
        chase = self._running(world_id)
        seen = set(in_sight)
        hunters = [r.passive for r in chase.runners if r.side == "pursuer" and _running(r)]
        dc = max(hunters, default=0)
        lines = []
        for n, runner in enumerate(chase.runners):
            if runner.side != "quarry" or not _running(runner):
                continue
            if n in seen:
                lines.append(f"chase:escape:{_safe(runner.name)}:-:{dc}:0")
                continue
            edge = advantage.get(n, Advantage.NORMAL)
            if runner.character_id:
                total = self._sheet_roll(runner.character_id, "skill", "stealth", edge)
            else:
                total = self._dice.d20(runner.stealth, edge).total
            runner.escaped = total > dc
            lines.append(f"chase:escape:{_safe(runner.name)}:{total}:{dc}:{int(runner.escaped)}")
        chase.round += 1
        chase.turn = _onward(chase, 0)
        quarry = [r for r in chase.runners if r.side == "quarry"]
        if quarry and not any(_running(r) for r in quarry):
            chase.over = "escaped" if any(r.escaped for r in quarry) else "stopped"
        elif not hunters:
            chase.over = "escaped"
        if chase.over:
            lines.append(f"chase:end:{chase.over}")
        self._log(world_id, by, by_name, lines)
        return self._save(chase)

    def distance(self, world_id: str, feet: int) -> Chase:
        """The gamemaster's distance between the quarry and the lead pursuer."""
        if not 0 <= feet <= 10_000:
            raise ChaseError("chase.error.bad")
        chase = self._running(world_id)
        return self._save(chase.model_copy(update={"distance": feet}))

    def drop(self, world_id: str, index: int) -> Chase:
        """A participant leaves the chase (stops, or is caught), or comes back in."""
        chase, runner = self._runner(world_id, index, running=False)
        runner.out = not runner.out
        return self._save(chase)

    def end(self, world_id: str, outcome: str, by: str, by_name: str) -> None:
        """End the chase (``caught`` or ``stopped``) and forget it."""
        chase = self.of(world_id)
        if chase is None:
            return
        if not chase.over:
            self._log(world_id, by, by_name, [f"chase:end:{_safe(outcome) or 'stopped'}"])
        self._store.chases.delete(world_id)
        if self._changed is not None:
            self._changed(world_id)

    def _running(self, world_id: str) -> Chase:
        chase = self.of(world_id)
        if chase is None or chase.over:
            raise ChaseError("chase.error.none")
        return chase

    def _runner(
        self, world_id: str, index: int, *, running: bool = True
    ) -> tuple[Chase, ChaseRunner]:
        chase = self._running(world_id)
        if not 0 <= index < len(chase.runners):
            raise ChaseError("chase.error.bad")
        runner = chase.runners[index]
        if running and not _running(runner):
            raise ChaseError("chase.error.out")
        return chase, runner

    def _log(self, world_id: str, by: str, by_name: str, lines: Sequence[str]) -> None:
        if self._protocol is None or not lines:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    kind=EntryKind.CUSTOM,
                    what=line,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
                for line in lines
            ]
        )

    def _save(self, chase: Chase) -> Chase:
        chase.touch()
        saved = self._store.chases.upsert(chase)
        if self._changed is not None:
            self._changed(chase.id)
        return saved


def _running(runner: ChaseRunner) -> bool:
    return not runner.out and not runner.escaped


def _onward(chase: Chase, start: int) -> int:
    """The first runner still running from ``start`` on; past the last, the round's end."""
    return next(
        (n for n in range(start, len(chase.runners)) if _running(chase.runners[n])),
        len(chase.runners),
    )


def _safe(text: str) -> str:
    return text.replace(":", " ")


__all__ = [
    "DASH_DC",
    "ENVIRONMENTS",
    "OUT_AT",
    "ChaseError",
    "ChaseService",
    "Runner",
    "complication",
    "free_dashes",
]
