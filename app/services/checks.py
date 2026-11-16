"""Checks the gamemaster calls for outside a fight: "everyone roll Perception".

A call names what to roll — a skill, an ability check or a saving throw — the characters
asked, and optionally a DC the players do not see. Asked openly, each player finds the
call on their sheet; any roll of that check made there (the call's button or the skill's
own row) is taken as their answer, and the gamemaster's party view shows the totals as
they come in. Rolled secretly, the gamemaster rolls for every character at once with its
sheet's numbers, in the log for the gamemaster alone. A group check passes when at least
half of those asked succeed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.models.gamestate import CheckCall
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
SKILLS = (
    "acrobatics",
    "animal handling",
    "arcana",
    "athletics",
    "deception",
    "history",
    "insight",
    "intimidation",
    "investigation",
    "medicine",
    "nature",
    "perception",
    "performance",
    "persuasion",
    "religion",
    "sleight of hand",
    "stealth",
    "survival",
)
# How many closed calls the party view keeps showing, newest first.
RECENT = 3


class CheckError(Translatable):
    """A call the rules or the input refuse."""


class CheckService:
    """Calls for checks and their answers, kept in ``library/checks.json``."""

    def __init__(
        self,
        store: GameStateStore,
        roll: Callable[[str, str, str, str, bool], int | None],
        changed: Callable[[CheckCall], None] | None = None,
    ) -> None:
        """``roll(character, kind, subject, by, secret)`` rolls a check on the
        character's sheet and returns its total; ``changed(call)`` tells open screens."""
        self._store = store
        self._roll = roll
        self._changed = changed

    def call(
        self,
        world_id: str | None,
        kind: str,
        subject: str,
        character_ids: Sequence[str],
        by: str,
        *,
        dc: int | None = None,
        group: bool = False,
        secret: bool = False,
        purpose: str = "",
    ) -> CheckCall:
        """Ask ``character_ids`` for a check, or (``secret``) roll it for them now."""
        subject = subject.strip().lower()
        if kind not in ("skill", "check", "save"):
            raise CheckError("checks.error.bad_input")
        if (kind == "skill" and subject not in SKILLS) or (
            kind != "skill" and subject not in ABILITIES
        ):
            raise CheckError("checks.error.bad_input")
        chosen = list(dict.fromkeys(c for c in character_ids if c))
        if not chosen:
            raise CheckError("checks.error.no_one")
        call = CheckCall(
            world_id=world_id,
            kind=kind,
            subject=subject,
            dc=dc,
            group=group,
            secret=secret,
            characters=chosen,
            by=by,
            purpose=" ".join(purpose.split())[:200],
        )
        if secret:
            for character_id in chosen:
                total = self._roll(character_id, kind, subject, by, True)
                if total is not None:
                    call.results[character_id] = total
            call.open = False
        created = self._store.checks.create(call)
        self._tell(created)
        return created

    def answer(self, character_id: str, kind: str, subject: str, total: int) -> None:
        """A roll made on a character's sheet: the open calls asking it for that check
        take it as the character's answer (its first; a later roll does not replace it)."""
        subject = subject.strip().lower()
        for call in self._store.checks.list(
            lambda c: (
                c.open
                and c.kind == kind
                and c.subject == subject
                and character_id in c.characters
                and character_id not in c.results
            )
        ):

            def take(record: CheckCall) -> None:
                record.results = {**record.results, character_id: total}

            self._tell(self._store.checks.mutate(call.id, take))

    def close(self, call_id: str) -> CheckCall:
        """The gamemaster is done with a call; it stays listed a while, closed."""

        def done(record: CheckCall) -> None:
            record.open = False

        closed = self._store.checks.mutate(call_id, done)
        self._tell(closed)
        return closed

    def open_for(self, character_id: str) -> list[CheckCall]:
        """The open calls asking a character, oldest first."""
        calls = self._store.checks.list(lambda c: c.open and character_id in c.characters)
        return sorted(calls, key=lambda c: c.created_at)

    def recent(self, world_id: str | None = None) -> list[CheckCall]:
        """The open calls (of a world, if given) and the last few closed, newest first."""
        calls = sorted(
            self._store.checks.list(lambda c: world_id is None or c.world_id == world_id),
            key=lambda c: c.created_at,
            reverse=True,
        )
        return [c for c in calls if c.open] + [c for c in calls if not c.open][:RECENT]

    def _tell(self, call: CheckCall) -> None:
        if self._changed is not None:
            self._changed(call)


__all__ = ["ABILITIES", "SKILLS", "CheckError", "CheckService"]
