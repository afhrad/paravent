"""Wild magic: a zone where spells surge, and surges rolled on a character.

The 2024 guide's *Wild Magic Zone* hazard: whenever a creature expends a spell slot to
cast a spell there, a d20 is rolled, and on a 20 the *Wild Magic Surge* table makes a
magical effect. The table is the handbooks' (the 2014 one's d100 of fifty rows, the 2024
one's of twenty-five), in the user's corpus, found by caption in the handbook of the
world's rules first (app/services/book_tables.py). The gamemaster puts the party in a zone
or takes it out (``WorldClock.wild_magic``); while it is in one, every slot a character of
the world spends from the sheet rolls the d20. A surge can also be rolled on chosen
characters at once. The effect's dice are rolled into its text; what it does is the
table's, said in the log for everyone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import Rows, roll_row, rolled
from app.services.clock import ClockService
from app.services.dice import Dice
from app.services.protocol import ProtocolService

TABLE = "Wild Magic Surge"
# A slot spent in a zone surges on this d20 (the 2024 guide).
SURGES_ON = 20


@dataclass(frozen=True)
class Surge:
    """One surge as rolled: the d100, and the effect, its dice rolled."""

    character_id: str
    name: str
    roll: int
    text: str


class WildMagicService:
    """The world's wild magic zone and the surges rolled in it."""

    def __init__(
        self,
        clock: ClockService,
        protocol: ProtocolService | None,
        *,
        table: Callable[[str], Rows | None],
        dice: Dice | None = None,
    ) -> None:
        """``table(world)`` is the world's *Wild Magic Surge* table, None without one."""
        self._clock = clock
        self._protocol = protocol
        self._table = table
        self._dice = dice or Dice()

    def zone(self, world_id: str | None) -> bool:
        """Whether the world's party is in a wild magic zone."""
        return world_id is not None and self._clock.of(world_id).wild_magic

    def has_table(self, world_id: str) -> bool:
        """Whether the world's books print the table."""
        return bool(self._table(world_id))

    def enter(self, world_id: str, on: bool, by: str, by_name: str) -> None:
        """The party enters (``on``) or leaves a wild magic zone, told to the table."""
        if self.zone(world_id) == on:
            return
        self._clock.wild(world_id, on)
        self._log(world_id, by, by_name, "", "", f"wild_zone:{int(on)}")

    def surge(
        self, world_id: str, character_id: str, name: str, by: str, by_name: str, cause: str = ""
    ) -> Surge | None:
        """A surge on a character, logged for everyone; None without the table.
        ``cause`` is ``zone`` for one a spell slot set off."""
        rows = self._table(world_id)
        if not rows:
            return None
        total, row = roll_row(rows, self._dice)
        text = " ".join(rolled(row[-1], self._dice).split()) if row else ""
        self._log(
            world_id,
            by,
            by_name,
            character_id,
            name,
            f"wild_surge:{total}:{cause}:{text}",
        )
        return Surge(character_id, name, total, text)

    def slot_spent(self, world_id: str | None, character_id: str, name: str) -> Surge | None:
        """A character spent a spell slot: in a zone, the d20; a surge on a 20."""
        if world_id is None or not self.zone(world_id):
            return None
        if self._dice.die(20) < SURGES_ON:
            return None
        return self.surge(world_id, character_id, name, "", "Paravent", cause="zone")

    def _log(
        self, world_id: str, by: str, by_name: str, character_id: str, name: str, what: str
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{character_id}" if character_id else "",
                    subject_name=name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            ]
        )


def surge_of(what: str) -> tuple[int, str, str] | None:
    """A logged surge read back: (d100, cause, effect); None for anything else."""
    head, _, rest = what.partition(":")
    if head != "wild_surge":
        return None
    roll, _, rest = rest.partition(":")
    cause, _, text = rest.partition(":")
    return (int(roll) if roll.isdigit() else 0, cause, text)


__all__ = ["SURGES_ON", "TABLE", "Surge", "WildMagicService", "surge_of"]
