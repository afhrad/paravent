"""Reading the log: which entries a viewer may see, and what of them (PROTOCOL.md §4, §5).

Filtering is the server's job (§10): an entry a player may not read is never handed to a
page, a fragment or a toast. The gamemaster reads everything. A player reads

* the worlds their characters are in, and their own global rolls;
* public entries, and private ones they rolled or that are about their characters;
* a monster's doings as **results only** — its attack's hit or miss and damage, its
  saves' success or failure — never its dice, bonus or total, and not the roll entry
  that precedes the gamemaster's verdict;
* never the fight's bookkeeping (who was hidden, split, edited), nor a DC or AC.

Bookkeeping entries are applied here: a ``reveal`` makes its entry public, a ``correct``
strikes it through, a ``clear`` hides a subject's entries up to it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.models.protocol import EntryKind, ProtocolEntry, Visibility
from app.store.protocol import GLOBAL, ProtocolStore, log_key

PAGE_SIZE = 40
_BOOKKEEPING = frozenset({EntryKind.REVEAL, EntryKind.CORRECT, EntryKind.CLEAR})
# A monster's roll whose result reaches players through the entries after it.
_ROLL_BEHIND_THE_SCREEN = frozenset({EntryKind.ATTACK, EntryKind.EFFECT})


@dataclass(frozen=True)
class Viewer:
    """Who reads: a username, whether the gamemaster, and their characters' ids and
    worlds (a player's)."""

    username: str
    gamemaster: bool
    characters: frozenset[str] = frozenset()
    worlds: frozenset[str] = frozenset()

    def owns(self, ref: str | None) -> bool:
        """True when ``ref`` names one of the viewer's characters."""
        return ref is not None and ref.startswith("character:") and ref[10:] in self.characters


@dataclass(frozen=True)
class Filter:
    """What the panel narrows to: everything, the viewer's own, one fight, one
    character (the gamemaster's), or the monsters (the gamemaster's)."""

    kind: str = "all"  # all | me | fight | character | monsters
    combat: str | None = None
    character: str | None = None

    def accepts(self, entry: ProtocolEntry, viewer: Viewer) -> bool:
        if self.kind == "me":
            return (
                viewer.owns(entry.subject)
                or viewer.owns(entry.target)
                or (bool(viewer.username) and entry.actor == viewer.username)
            )
        if self.kind == "fight":
            return self.combat is not None and entry.scope.combat == self.combat
        if self.kind == "character" and self.character:
            ref = f"character:{self.character}"
            return ref in (entry.subject, entry.target)
        if self.kind == "monsters":
            return entry.subject.startswith("combatant:")
        return True


@dataclass(frozen=True)
class Shown:
    """An entry as this viewer reads it, and what the bookkeeping says about it."""

    entry: ProtocolEntry
    corrected: bool = False
    revealed: bool = False
    # For the gamemaster: the players do not see all of it yet — it can be revealed.
    revealable: bool = False


@dataclass
class LogPage:
    """One page of the panel, newest first, and whether older ones exist."""

    shown: list[Shown] = field(default_factory=list)
    more: bool = False
    # Fight id → its name, from the fights' start entries, for the dividers.
    fights: dict[str, str] = field(default_factory=dict)


class ProtocolReader:
    """The log as one viewer may read it."""

    def __init__(self, store: ProtocolStore) -> None:
        """Read through ``store``."""
        self._store = store

    def keys_for(self, viewer: Viewer) -> list[str]:
        """The files a viewer reads: every one for the gamemaster; a player's worlds
        and the global log."""
        if viewer.gamemaster:
            return self._store.logs()
        return sorted({*viewer.worlds, GLOBAL})

    def page(
        self,
        viewer: Viewer,
        chosen: Filter | None = None,
        *,
        before: str | None = None,
        limit: int = PAGE_SIZE,
    ) -> LogPage:
        """Up to ``limit`` entries the viewer may read and ``chosen`` accepts, newest
        first, older than the entry ``before`` names."""
        chosen = chosen or Filter()
        entries = self._merged(self.keys_for(viewer))
        revealed, corrected, cleared = _bookkeeping(entries)
        result = LogPage(fights=_fight_names(entries))
        started = before is None
        for entry in entries:
            if not started:
                started = entry.id == before
                continue
            if entry.kind in _BOOKKEEPING:
                continue
            if entry.subject in cleared and entry.at <= cleared[entry.subject]:
                continue
            face = self.face(entry, viewer, revealed=entry.id in revealed)
            if face is None or not chosen.accepts(face, viewer):
                continue
            if len(result.shown) >= limit:
                result.more = True
                break
            result.shown.append(
                Shown(
                    face,
                    corrected=entry.id in corrected,
                    revealed=entry.id in revealed,
                    revealable=viewer.gamemaster
                    and entry.id not in revealed
                    and hidden_from_players(entry),
                )
            )
        return result

    def toasts(
        self, viewer: Viewer, since: datetime, mode: str, *, limit: int = 3
    ) -> list[Shown]:
        """What pops up for the viewer since ``since``, newest first (PROTOCOL.md §6).

        ``mine``: a player's own rolls and what hits, heals or befalls their characters;
        for the gamemaster, every roll made for a character. ``all``: everything the
        viewer may read but the turn order. ``off``: nothing.
        """
        if mode == "off":
            return []
        entries = self._merged(self.keys_for(viewer))
        revealed, _, _ = _bookkeeping(entries)
        found: list[Shown] = []
        for entry in entries:
            if entry.at <= since:
                break
            if entry.kind in _BOOKKEEPING or entry.kind in _NOT_TOASTED:
                continue
            face = self.face(entry, viewer, revealed=entry.id in revealed)
            if face is None or (mode == "mine" and not _concerns(face, viewer)):
                continue
            found.append(Shown(face, revealed=entry.id in revealed))
            if len(found) >= limit:
                break
        return found

    def fight(self, combat_id: str, world_id: str | None) -> list[ProtocolEntry]:
        """A fight's entries, oldest first, as the gamemaster reads them (the recap)."""
        found = [
            e
            for e in self._store.entries(log_key(world_id))
            if e.scope.combat == combat_id and e.kind not in _BOOKKEEPING
        ]
        found.sort(key=lambda e: e.at)
        return found

    def find(self, entry_id: str) -> ProtocolEntry | None:
        """The entry with this id, in any log."""
        for key in self._store.logs():
            for entry in self._store.entries(key):
                if entry.id == entry_id:
                    return entry
        return None

    def since(self, viewer: Viewer, moment: datetime | None) -> int:
        """How many entries the viewer may read arrived after ``moment`` (the badge)."""
        entries = self._merged(self.keys_for(viewer))
        revealed, _, _ = _bookkeeping(entries)
        count = 0
        for entry in entries:
            if moment is not None and entry.at <= moment:
                break
            if entry.kind in _BOOKKEEPING:
                continue
            if self.face(entry, viewer, revealed=entry.id in revealed) is not None:
                count += 1
        return count

    @staticmethod
    def face(
        entry: ProtocolEntry, viewer: Viewer, *, revealed: bool = False
    ) -> ProtocolEntry | None:
        """``entry`` as ``viewer`` reads it, or None when they may not read it."""
        if viewer.gamemaster:
            return entry
        visibility = Visibility.PUBLIC if revealed else entry.visibility
        if visibility is Visibility.GM:
            return None
        if visibility is Visibility.PRIVATE and not (
            entry.actor == viewer.username or viewer.owns(entry.subject)
        ):
            return None
        if entry.kind is EntryKind.COMBAT:
            return None  # the fight's bookkeeping: hidden, split, edited …
        if revealed:
            return entry  # the gamemaster showed it in full
        hidden: dict[str, object] = {"against": None}
        if entry.subject.startswith("combatant:"):
            if entry.kind in _ROLL_BEHIND_THE_SCREEN and entry.refers_to is None:
                return None  # its result follows in the verdict and the saves
            hidden |= {"roll": None, "damage_rolls": []}
        return entry.model_copy(update=hidden)

    def _merged(self, keys: Iterable[str]) -> list[ProtocolEntry]:
        """Every entry of ``keys``, newest first."""
        entries = [e for key in keys for e in self._store.entries(key)]
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries


# The fight's rhythm and bookkeeping: shown in the panel, never popped up.
_NOT_TOASTED = frozenset(
    {
        EntryKind.TURN,
        EntryKind.ROUND,
        EntryKind.COMBAT,
        EntryKind.COMBAT_START,
        EntryKind.COMBAT_END,
    }
)


def _concerns(entry: ProtocolEntry, viewer: Viewer) -> bool:
    """A toast "mine": a player's own doings and their characters'; the gamemaster's,
    every roll made for a character."""
    if viewer.gamemaster:
        return entry.subject.startswith("character:") and entry.roll is not None
    return (
        viewer.owns(entry.subject)
        or viewer.owns(entry.target)
        or (bool(viewer.username) and entry.actor == viewer.username)
    )


def hidden_from_players(entry: ProtocolEntry) -> bool:
    """True when a player reads less of ``entry`` than the gamemaster: not public, a
    monster's numbers, or a DC or AC. The fight's bookkeeping is never revealed."""
    if entry.kind is EntryKind.COMBAT or entry.kind in _BOOKKEEPING:
        return False
    if entry.visibility is not Visibility.PUBLIC or entry.against is not None:
        return True
    monster = entry.subject.startswith("combatant:")
    return monster and (entry.roll is not None or bool(entry.damage_rolls))


def _bookkeeping(
    entries: Iterable[ProtocolEntry],
) -> tuple[set[str], set[str], dict[str, datetime]]:
    """Which entries were revealed, which corrected, and up to when each subject's
    entries were cleared."""
    revealed: set[str] = set()
    corrected: set[str] = set()
    cleared: dict[str, datetime] = {}
    for entry in entries:
        if entry.kind is EntryKind.REVEAL and entry.refers_to:
            revealed.add(entry.refers_to)
        elif entry.kind is EntryKind.CORRECT and entry.refers_to:
            corrected.add(entry.refers_to)
        elif entry.kind is EntryKind.CLEAR and entry.subject:
            cleared[entry.subject] = max(entry.at, cleared.get(entry.subject, entry.at))
    return revealed, corrected, cleared


def _fight_names(entries: Iterable[ProtocolEntry]) -> dict[str, str]:
    return {
        e.scope.combat: e.subject_name
        for e in entries
        if e.kind is EntryKind.COMBAT_START and e.scope.combat
    }


__all__ = [
    "PAGE_SIZE",
    "Filter",
    "LogPage",
    "ProtocolReader",
    "Shown",
    "Viewer",
    "hidden_from_players",
]
