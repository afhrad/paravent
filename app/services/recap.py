"""What happened in a fight, or in a session of fights: the gamemaster's recap.

Read from the log (documentation/PROTOCOL.md §8), each fight's entries oldest first — the
log records what was rolled and applied, uncapped, so the recap needs nothing of its own.
Damage *taken* comes from the damage the tracker applied (resistances counted); damage
*dealt* from what landed — an attack's verdict, a save effect's damage on each target.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from app.models.gamestate import Combat
from app.models.protocol import EntryKind, ProtocolEntry
from app.services.combat import CombatService
from app.services.tokens import Token


@dataclass
class Fighter:
    """One combatant across the fights recapped."""

    name: str
    party: bool
    dealt: int = 0
    taken: int = 0
    healed: int = 0
    crits: int = 0
    fumbles: int = 0
    down: bool = False
    # Its round token, from the fight it was first met in (documentation/TOKENS.md).
    token: Token | None = None


@dataclass(frozen=True)
class Hit:
    """The biggest single hit."""

    attacker: str
    target: str
    what: str
    amount: int


@dataclass(frozen=True)
class FightLine:
    """One fight of the session: its name, rounds, and who was defeated."""

    combat_id: str
    name: str
    rounds: int
    defeated: tuple[str, ...]
    xp: int


@dataclass
class Recap:
    """The recap of one or more fights."""

    fights: list[FightLine] = field(default_factory=list)
    fighters: list[Fighter] = field(default_factory=list)
    biggest: Hit | None = None
    # (who, condition, source) in the order they happened.
    conditions: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def xp(self) -> int:
        """The XP the defeated were worth, all fights together."""
        return sum(f.xp for f in self.fights)


def recap(
    combats: Sequence[Combat],
    service: CombatService,
    entries: Callable[[Combat], Sequence[ProtocolEntry]],
) -> Recap:
    """Recap ``combats`` (oldest first) from the log; ``entries(combat)`` is a fight's,
    oldest first."""
    out = Recap()
    fighters: dict[str, Fighter] = {}

    def fighter(name: str, party: bool = False) -> Fighter:
        if name not in fighters:
            fighters[name] = Fighter(name, party)
        return fighters[name]

    for combat in sorted(combats, key=lambda c: c.created_at):
        summary = service.summary(combat)
        for member in combat.combatants:
            entry = fighter(service.label_of(member), member.is_friend)
            entry.party = member.is_friend
            entry.down = entry.down or service.is_down(member)
            if entry.token is None:
                entry.token = service.token_of(member, combat.world_id)
        out.fights.append(
            FightLine(
                combat.id,
                combat.name,
                summary.rounds,
                tuple(label for label, _ in summary.defeated),
                summary.xp,
            )
        )
        _read_log(entries(combat), fighter, out)
    out.fighters = sorted(fighters.values(), key=lambda f: (not f.party, -f.dealt, f.name.lower()))
    return out


# What follows a save effect in the log: its targets' lines. Anything else ends it, so a
# condition the gamemaster toggles later is not credited to the effect.
_EFFECT_LINES = frozenset(
    {EntryKind.SAVE, EntryKind.DAMAGE, EntryKind.CONDITION, EntryKind.CONCENTRATION}
)


def _read_log(
    entries: Iterable[ProtocolEntry], fighter: Callable[[str], Fighter], out: Recap
) -> None:
    """Tally one fight's entries, oldest first.

    An attack is two entries: its roll (a natural 20 or 1 counts for the attacker) and
    the gamemaster's verdict, which ``refers_to`` it and carries the damage that landed.
    A save effect's damage is credited to its source through each target's save.
    """
    effect: ProtocolEntry | None = None  # the save effect the next lines belong to
    for entry in entries:
        kind = entry.kind
        if kind is EntryKind.EFFECT:
            effect = entry
            continue
        if kind not in _EFFECT_LINES:
            effect = None
        if kind is EntryKind.DAMAGE and entry.amount:
            fighter(entry.subject_name).taken += entry.amount
        elif kind is EntryKind.HEAL and entry.amount:
            fighter(entry.subject_name).healed += entry.amount
        elif kind is EntryKind.ATTACK and entry.refers_to is None:
            attacker = fighter(entry.subject_name)
            attacker.crits += entry.outcome == "crit"
            attacker.fumbles += entry.outcome == "fumble"
        elif kind is EntryKind.ATTACK and entry.outcome == "hit" and entry.amount:
            fighter(entry.subject_name).dealt += entry.amount
            if out.biggest is None or entry.amount > out.biggest.amount:
                out.biggest = Hit(entry.subject_name, entry.target_name, entry.what, entry.amount)
        elif (
            kind is EntryKind.SAVE
            and effect is not None
            and entry.refers_to == effect.id
            and entry.amount
        ):
            fighter(effect.subject_name).dealt += entry.amount
            if out.biggest is None or entry.amount > out.biggest.amount:
                out.biggest = Hit(
                    effect.subject_name, entry.subject_name, effect.what, entry.amount
                )
        elif kind is EntryKind.CONDITION and entry.what.startswith("on:"):
            source = effect.what if effect is not None else ""
            out.conditions.append((entry.subject_name, entry.what[3:], source))


def session_combats(combats: Iterable[Combat], world_id: str | None, day: str) -> list[Combat]:
    """The fights of one world that began on ``day`` (YYYY-MM-DD, local time)."""
    return [
        c
        for c in combats
        if c.world_id == world_id and c.created_at.astimezone().date().isoformat() == day
    ]


__all__ = ["Fighter", "FightLine", "Hit", "Recap", "recap", "session_combats"]
