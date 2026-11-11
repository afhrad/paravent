"""Bringing the old logs into the log, once (documentation/PROTOCOL.md §8).

Before the log, a character kept its last rolls in its file and a fight its own lines in
its record. Since the log began, both write to it as well — so only what is **older than
the log's first entry** for that character or that fight is copied, marked ``migrated``.
That makes the copy safe to run on every start: once done, nothing older is left.

The old lines carry less than a new entry: no dice as data (only the total), no bonuses
by source. A resolved monster attack becomes its roll and its verdict, as the tracker
writes them now, so the recap reads old fights like new ones.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from app.models.gamestate import Combat, CombatLogEntry
from app.models.pc import LogEntry, PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, RollRecord, Scope
from app.services.protocol import attack_entry_id, from_combat
from app.store.protocol import ProtocolStore

logger = logging.getLogger(__name__)


def copy_old_logs(
    store: ProtocolStore, characters: Iterable[PlayerCharacter], combats: Iterable[Combat]
) -> int:
    """Append what the old logs hold and the log does not; returns how many entries."""
    first_by_subject: dict[str, datetime] = {}
    first_by_fight: dict[str, datetime] = {}
    for key in store.logs():
        for entry in store.entries(key):
            for ref in (entry.subject, entry.target):
                if ref and (ref not in first_by_subject or entry.at < first_by_subject[ref]):
                    first_by_subject[ref] = entry.at
            fight = entry.scope.combat
            if fight and (fight not in first_by_fight or entry.at < first_by_fight[fight]):
                first_by_fight[fight] = entry.at
    copied: list[ProtocolEntry] = []
    for pc in characters:
        cutoff = first_by_subject.get(f"character:{pc.id}")
        for old in reversed(pc.log):  # newest first in the file
            if cutoff is None or old.at < cutoff:
                copied.append(_from_sheet(pc, old))
    for combat in combats:
        cutoff = first_by_fight.get(combat.id)
        effect_id: str | None = None
        for line in reversed(combat.log):
            if cutoff is not None and line.at >= cutoff:
                continue
            entry = from_combat(combat, line).model_copy(
                update={
                    "migrated": True,
                    "scope": Scope(
                        world=combat.world_id, combat=combat.id, round=line.round or None
                    ),
                }
            )
            if line.kind == "save_effect":
                effect_id = entry.id
            elif line.kind == "save_damage" and effect_id is not None:
                entry = entry.model_copy(update={"refers_to": effect_id})
            copied.append(entry)
            if line.kind == "attack" and line.applied:
                copied.append(_verdict(combat, line, entry))
    copied.sort(key=lambda e: e.at)
    store.append_all(copied)
    if copied:
        logger.info("Copied %d entries of the old character and combat logs", len(copied))
    return len(copied)


def _from_sheet(pc: PlayerCharacter, old: LogEntry) -> ProtocolEntry:
    kind = EntryKind(old.kind.value)
    subject = old.subject
    if kind is EntryKind.SKILL:
        what = f"skill:{subject.lower()}"
    elif kind is EntryKind.CAST:
        level = old.detail[1:] if old.detail.startswith("L") else ""
        what = f"cast:{subject}:{level}" if level else f"cast:{subject}"
    elif subject:
        what = f"{kind.value}:{subject}"
    else:
        what = kind.value
    outcome = old.outcome
    return ProtocolEntry(
        at=old.at,
        actor_name=old.by,
        subject=f"character:{pc.id}",
        subject_name=pc.name,
        kind=kind,
        what=what,
        roll=RollRecord(expression="", total=old.total) if old.total is not None else None,
        outcome=outcome,
        scope=Scope(world=pc.world_id),
        migrated=True,
    )


def _verdict(combat: Combat, line: CombatLogEntry, rolled: ProtocolEntry) -> ProtocolEntry:
    """A resolved attack's Hit or Miss, as the tracker writes it now."""
    hit = line.applied not in ("", "-")
    return ProtocolEntry(
        at=line.at + timedelta(microseconds=1),
        actor_name=line.by,
        subject=rolled.subject,
        subject_name=rolled.subject_name,
        target_name=line.applied if hit else "",
        kind=EntryKind.ATTACK,
        what=line.detail,
        outcome="hit" if hit else "miss",
        amount=line.damage if hit else None,
        types=dict(line.damage_types) if hit else {},
        visibility=rolled.visibility,
        scope=rolled.scope,
        refers_to=attack_entry_id(combat.id, line.at),
        migrated=True,
    )


__all__ = ["copy_old_logs"]
