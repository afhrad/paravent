"""Writing the log (documentation/PROTOCOL.md): rolls as data, events as entries.

The sheet's play rules and the combat tracker *collect* entries while a change is
applied and :meth:`ProtocolService.append` them once it is stored — a change the rules
refuse leaves nothing in the log. This module only builds entries; who may read them,
and how they read, is the panel's business (§4, §5).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.models.gamestate import Combat, Combatant, CombatLogEntry
from app.models.protocol import (
    Bonus,
    Die,
    EntryKind,
    Mode,
    Outcome,
    ProtocolEntry,
    RollRecord,
    Scope,
    Visibility,
)
from app.services.dice import Advantage, Dice, Roll
from app.store.protocol import ProtocolStore, log_key

if TYPE_CHECKING:
    from app.models.pc import PlayerCharacter

_BOOKKEEPING = frozenset({EntryKind.REVEAL, EntryKind.CORRECT, EntryKind.CLEAR})

_MODE: dict[Advantage, Mode] = {
    Advantage.ADVANTAGE: "advantage",
    Advantage.DISADVANTAGE: "disadvantage",
    Advantage.NORMAL: "normal",
}


class ProtocolService:
    """The log's one way in."""

    def __init__(self, store: ProtocolStore) -> None:
        """Write through ``store``."""
        self.store = store

    def append(self, entries: Iterable[ProtocolEntry]) -> None:
        """Append entries collected during one change, in the order they happened."""
        self.store.append_all(entries)

    def free_roll(
        self,
        expression: str,
        *,
        username: str,
        name: str,
        label: str,
        world_id: str | None,
        gamemaster: bool = False,
        dice: Dice | None = None,
    ) -> ProtocolEntry:
        """Roll a dice chip outside a character sheet (a statblock's attack on the
        reference page) and log it: private to the roller and the gamemaster, or, rolled
        by the gamemaster, behind the screen until revealed.

        Raises :class:`~app.services.dice.DiceError` for an expression that is not one.
        """
        roll = (dice or Dice()).roll(expression)
        d20s = [g for g in roll.groups if g[0] == 20]
        natural = d20s[0][1][0] if len(d20s) == 1 and len(d20s[0][1]) == 1 else None
        if natural is not None:
            roll = replace(roll, natural=abs(natural))
        entry = ProtocolEntry(
            actor=username,
            actor_name=name,
            subject_name=label,
            kind=EntryKind.CUSTOM,
            what=f"custom:{roll.expression}",
            roll=roll_record(roll, rest="modifier"),
            outcome=outcome_of(roll),
            visibility=Visibility.GM if gamemaster else Visibility.PRIVATE,
            scope=Scope(world=world_id),
            origin="table",
        )
        self.store.append(entry)
        return entry

    def newest(
        self,
        subject: str,
        world_id: str | None,
        *,
        kinds: Iterable[EntryKind] | None = None,
        origin: str | None = None,
        since: datetime | None = None,
        rolled: bool = False,
    ) -> ProtocolEntry | None:
        """The newest entry about ``subject``: of these kinds, from this origin, after
        ``since``, with a roll — whatever is asked. Its world's log first, then every
        other: what a character does in a fight is kept in the fight's world."""
        wanted = frozenset(kinds) if kinds is not None else None
        best: ProtocolEntry | None = None
        for key in self._keys(world_id):
            for entry in reversed(self.store.entries(key)):
                if (since is not None and entry.at <= since) or (
                    best is not None and entry.at <= best.at
                ):
                    break
                if entry.subject != subject or entry.kind in _BOOKKEEPING:
                    continue
                if wanted is not None and entry.kind not in wanted:
                    continue
                if origin is not None and entry.origin != origin:
                    continue
                if rolled and entry.roll is None:
                    continue
                best = entry
                break
        return best

    def since(self, subject: str, world_id: str | None, moment: datetime) -> list[ProtocolEntry]:
        """What was appended about ``subject`` after ``moment``, newest first, in any log."""
        found = []
        for key in self._keys(world_id):
            for entry in reversed(self.store.entries(key)):
                if entry.at <= moment:
                    break
                if entry.subject == subject:
                    found.append(entry)
        found.sort(key=lambda e: e.at, reverse=True)
        return found

    def _keys(self, world_id: str | None) -> list[str]:
        """The subject's own world's log, then every other."""
        own = log_key(world_id)
        return [own, *(k for k in self.store.logs() if k != own)]

    def reveal(self, entry: ProtocolEntry, *, username: str, name: str) -> ProtocolEntry:
        """Show ``entry`` to the table in full: a new entry that refers to it (§3)."""
        revealed = ProtocolEntry(
            actor=username,
            actor_name=name,
            kind=EntryKind.REVEAL,
            refers_to=entry.id,
            scope=entry.scope,
        )
        self.store.append(revealed)
        return revealed


def roll_record(
    roll: Roll,
    *,
    sources: Sequence[tuple[str, int]] = (),
    advantage: Advantage = Advantage.NORMAL,
    chosen: Advantage = Advantage.NORMAL,
    reasons: Sequence[str] = (),
    rest: str = "other",
) -> RollRecord:
    """``roll`` as the log keeps it.

    ``advantage`` is what applied, ``chosen`` the sheet's switch, ``reasons`` the
    conditions (and exhaustion) that bent it. ``sources`` split the flat bonus; whatever
    they do not account for is put down as ``rest`` — ``modifier`` for the number in a
    damage expression, ``other`` for an honest unknown.
    """
    dice = []
    for faces, results in roll.groups:
        kept = list(results)
        if faces == 20 and len(results) > 1 and roll.natural is not None:
            kept = [roll.natural]
        dice.append(Die(faces=faces, results=list(results), kept=kept))
    bonuses = [Bonus(source=s, value=v) for s, v in sources if v]
    remainder = roll.modifier - sum(b.value for b in bonuses)
    if remainder:
        bonuses.append(Bonus(source=rest, value=remainder))
    why = (["sheet"] if chosen is not Advantage.NORMAL else []) + [
        r if r == "exhaustion" else f"condition:{r}" for r in reasons
    ]
    return RollRecord(
        expression=roll.expression,
        dice=dice,
        natural=roll.natural,
        total=roll.total,
        mode=_MODE[advantage],
        reasons=why,
        bonuses=bonuses,
    )


def for_character(
    pc: PlayerCharacter,
    kind: EntryKind,
    *,
    actor: str = "",
    actor_name: str = "",
    origin: str = "table",
    **facts: Any,
) -> ProtocolEntry:
    """An entry about a character, in its world: XP, a level, the wizard's rolls."""
    return ProtocolEntry(
        actor=actor,
        actor_name=actor_name,
        subject=f"character:{pc.id}",
        subject_name=pc.name,
        kind=kind,
        scope=Scope(world=pc.world_id),
        origin=origin,
        **facts,
    )


def plain_dice(record: RollRecord | None) -> str:
    """A roll's dice in one plain line ("d20 15 (7) + 3"), for records that keep text."""
    if record is None:
        return ""
    words = []
    for die in record.dice:
        if die.faces == 20 and len(die.results) > 1:
            words.append(f"d20[{','.join(map(str, die.results))}]")
        else:
            words.append(f"{len(die.results)}d{die.faces}[{','.join(map(str, die.results))}]")
    modifier = sum(b.value for b in record.bonuses)
    if modifier:
        words.append(f"{modifier:+d}")
    return " ".join(words)


def outcome_of(roll: Roll | None) -> Outcome:
    """A natural 20 or 1, from the roll."""
    kind = roll.kind if roll is not None else ""
    return "crit" if kind == "crit" else "fumble" if kind == "fumble" else ""


# -- the tracker's events ------------------------------------------------------------

_COMBAT_KINDS: dict[str, EntryKind] = {
    "start": EntryKind.COMBAT_START,
    "begin": EntryKind.ROUND,
    "turn": EntryKind.TURN,
    "lair": EntryKind.TURN,
    "initiative": EntryKind.INITIATIVE,
    "damage": EntryKind.DAMAGE,
    "heal": EntryKind.HEAL,
    "temp": EntryKind.TEMP_HP,
    "condition_on": EntryKind.CONDITION,
    "condition_off": EntryKind.CONDITION,
    "condition_expired": EntryKind.CONDITION,
    "condition_immune": EntryKind.CONDITION,
    "cast": EntryKind.CAST,
    "concentration": EntryKind.CONCENTRATION,
    "concentration_save": EntryKind.CONCENTRATION,
    "attack": EntryKind.ATTACK,
    "save_effect": EntryKind.EFFECT,
    "save_damage": EntryKind.SAVE,
    "repeat_save": EntryKind.SAVE,
    "recharge": EntryKind.CUSTOM,
    # What a weapon mastery does after the attack: for the whole table, not bookkeeping.
    "mastery": EntryKind.CUSTOM,
    "end": EntryKind.COMBAT_END,
}


def attack_entry_id(combat_id: str, at: datetime) -> str:
    """The id an attack's or effect's roll is logged under, so its verdict (Hit or
    Miss, each target's save) can refer to it: the fight and the moment it was rolled."""
    return f"{combat_id}-{at:%Y%m%d%H%M%S%f}"


def combatant_ref(combat: Combat, member: Combatant) -> str:
    """How an entry names a combatant: its character, or its place in the fight."""
    if member.character_id is not None:
        return f"character:{member.character_id}"
    return f"combatant:{combat.id}/{member.id}"


def from_combat(
    combat: Combat,
    entry: CombatLogEntry,
    *,
    roll: RollRecord | None = None,
    damage_rolls: Sequence[RollRecord] = (),
) -> ProtocolEntry:
    """A line of the tracker's log as a log entry: who, what, the dice, where."""
    kind = _COMBAT_KINDS.get(entry.kind, EntryKind.COMBAT)
    by_label = {m.label: m for m in combat.combatants}
    by_id = {m.id: m for m in combat.combatants}
    member = by_id.get(entry.actor) or by_label.get(entry.subject)
    hidden = member is not None and member.hidden
    what = {
        "condition_on": f"on:{entry.detail}",
        "condition_off": f"off:{entry.detail}",
        "condition_expired": f"expired:{entry.detail}",
        "condition_immune": f"immune:{entry.detail}",
        "cast": f"cast:{entry.detail}",
        "lair": "lair",
        "begin": "1",
        "recharge": f"recharge:{entry.detail}",
        "repeat_save": f"repeat:{entry.save_ability}:{entry.detail}",
        "mastery": f"mastery:{entry.detail}",
        "save_damage": f"save:{entry.detail}",
    }.get(entry.kind)
    if what is None:
        if kind in (EntryKind.COMBAT, EntryKind.COMBAT_START):
            what = f"{entry.kind}:{entry.detail}" if entry.detail else entry.kind
        else:
            what = entry.detail
    amount: int | None = entry.amount
    against: int | None = None
    if entry.kind == "attack":
        amount = entry.damage
    elif entry.kind == "save_effect":
        amount, against = entry.damage, entry.amount
    elif entry.kind == "save_damage":
        amount = entry.damage
    outcome: Outcome = entry.outcome
    # An attack's or effect's roll gets an id its verdicts can refer to.
    fixed = (
        {"id": attack_entry_id(combat.id, entry.at)}
        if entry.kind in ("attack", "save_effect")
        else {}
    )
    return ProtocolEntry(
        **fixed,
        at=entry.at,
        actor_name=entry.by,
        subject=combatant_ref(combat, member) if member is not None else "",
        subject_name=entry.subject,
        kind=kind,
        what=what,
        roll=roll,
        damage_rolls=list(damage_rolls),
        outcome=outcome,
        against=against,
        amount=amount,
        types=dict(entry.damage_types),
        visibility=Visibility.GM if hidden or entry.kind == "hide" else Visibility.PUBLIC,
        scope=Scope(world=combat.world_id, combat=combat.id, round=combat.round or None),
        origin="tracker",
    )


def verdict(
    combat: Combat,
    attack: CombatLogEntry,
    target: Combatant | None,
    *,
    hit: bool,
    amount: int | None,
    types: dict[str, int],
    by: str,
) -> ProtocolEntry:
    """The gamemaster's Hit or Miss on a rolled attack, referring to its roll; a
    critical made at the table is in ``amount``, its extra dice counted."""
    member = next((m for m in combat.combatants if m.id == attack.actor), None)
    return ProtocolEntry(
        actor_name=by,
        subject=combatant_ref(combat, member) if member is not None else "",
        subject_name=attack.subject,
        target=combatant_ref(combat, target) if target is not None else None,
        target_name=target.label if target is not None else "",
        kind=EntryKind.ATTACK,
        what=attack.detail,
        outcome="hit" if hit and target is not None else "miss",
        amount=amount if hit else None,
        types=types if hit else {},
        visibility=Visibility.GM if member is not None and member.hidden else Visibility.PUBLIC,
        scope=Scope(world=combat.world_id, combat=combat.id, round=combat.round or None),
        origin="tracker",
        # A player's attack is the sheet's entry; one the tracker rolled has its id.
        refers_to=attack.entry_id or attack_entry_id(combat.id, attack.at),
    )


__all__ = [
    "ProtocolService",
    "attack_entry_id",
    "for_character",
    "plain_dice",
    "combatant_ref",
    "from_combat",
    "outcome_of",
    "roll_record",
    "verdict",
]
