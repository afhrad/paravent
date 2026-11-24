"""A day of play, begun and ended by the gamemaster (the session page).

Starting a session gathers what the table should know before play: the last session's
note, the world's date, timers still running, bastion turns that are due, lifestyle the
characters could not pay since the last session, contacts nobody has named yet, downtime
days still to spend, and whatever is still open (a chase, a shop, calls for checks).
Ending it writes the day's session note: the recap of its fights, as the session notes
always began, and what else happened in the world since it started, read from the log:
the time that passed, the miles travelled, quests finished or failed, chases, traps,
contacts made and lifestyle paid or owed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.gamestate import ClockTimer, PlaySession, SessionNote
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services import features
from app.services.clock import bastions_due
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore
from app.store.protocol import log_key


@dataclass
class Happened:
    """What the log says happened in a world since a moment, beyond its fights."""

    since: datetime
    minute_from: int | None = None  # the clock when the session began
    minute_to: int = 0
    miles: float = 0
    travel_encounters: int = 0
    chases: list[str] = field(default_factory=list)  # how each ended
    traps: list[str] = field(default_factory=list)  # the traps sprung, by name
    quests: list[tuple[str, str]] = field(default_factory=list)  # (done|failed, name)
    contacts: dict[str, int] = field(default_factory=dict)  # made, by kind
    paid: int = 0  # lifestyle paid, in copper
    owed: int = 0  # lifestyle the purses lacked, in copper
    xp: dict[str, int] = field(default_factory=dict)  # experience awarded, by character
    levels: list[str] = field(default_factory=list)  # a level taken, one name each
    # What characters were handed (a treasure shared out, a quest's reward): character →
    # item references with their counts, and the copper the coins were worth.
    received: dict[str, tuple[list[tuple[str, int]], int]] = field(default_factory=dict)
    bastion_turns: list[str] = field(default_factory=list)  # whose bastion took a turn
    downtime: int = 0  # downtime days granted

    @property
    def empty(self) -> bool:
        return not (
            self.miles
            or self.travel_encounters
            or self.chases
            or self.traps
            or self.quests
            or self.contacts
            or self.paid
            or self.owed
            or self.xp
            or self.levels
            or self.received
            or self.bastion_turns
            or self.downtime
            or (self.minute_from is not None and self.minute_to != self.minute_from)
        )


@dataclass
class Brief:
    """What the gamemaster should know as a session begins."""

    session: PlaySession | None  # the session running, if one is
    minute: int
    last_note: SessionNote | None
    timers: list[ClockTimer]
    bastions: list[str]  # characters whose bastion turn is due
    owed: list[tuple[str, int]]  # (character name, copper) not paid since the last session
    unnamed: list[tuple[str, str, int]]  # (character id, name, contacts without a name)
    downtime: list[tuple[str, str, int]]  # (character id, name, days still to spend)
    chase: bool = False
    shop: str | None = None
    calls: int = 0
    quests: int = 0  # quests still open
    broken_links: int = 0  # links of the table's world that lead nowhere (Check links)


class PlaySessionService:
    """Each world's day of play (``library/play_sessions.json``), logged when it starts
    and ends."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        minute_of: Callable[[str], int],
        timers_of: Callable[[str], list[ClockTimer]],
    ) -> None:
        """``minute_of(world)`` is the world's clock; ``timers_of(world)`` its timers
        still running."""
        self._store = store
        self._protocol = protocol
        self._minute_of = minute_of
        self._timers_of = timers_of

    def of(self, world_id: str) -> PlaySession | None:
        """The world's session while it runs."""
        held = self._store.play_sessions.find(world_id)
        return held if held is not None and held.ended_at is None else None

    def last(self, world_id: str) -> PlaySession | None:
        """The world's latest session, running or ended."""
        return self._store.play_sessions.find(world_id)

    def start(self, world_id: str, by: str, by_name: str) -> PlaySession:
        """Begin a day of play (a running one is kept)."""
        running = self.of(world_id)
        if running is not None:
            return running
        session = PlaySession(id=world_id, minute=self._minute_of(world_id))
        self._log(world_id, by, by_name, "session:start")
        return self._store.play_sessions.upsert(session)

    def end(self, world_id: str, note_id: str | None, by: str, by_name: str) -> PlaySession | None:
        """End the day of play, keeping the note written for it."""
        running = self.of(world_id)
        if running is None:
            return None
        ended = running.model_copy(update={"ended_at": datetime.now(UTC), "note_id": note_id})
        ended.touch()
        self._log(world_id, by, by_name, "session:end")
        return self._store.play_sessions.upsert(ended)

    def happened(self, world_id: str, since: datetime, minute_from: int | None) -> Happened:
        """What the world's log records since ``since`` (its fights aside)."""
        out = Happened(since=since, minute_from=minute_from, minute_to=self._minute_of(world_id))
        for entry in self._entries(world_id, since):
            head, _, rest = entry.what.partition(":")
            parts = rest.split(":")
            if head == "travel" and len(parts) > 2:
                out.miles += _number(parts[2])
            elif head in ("travel_encounter", "delve_encounter"):
                out.travel_encounters += 1
            elif head == "chase" and parts[0] == "end" and len(parts) > 1:
                out.chases.append(parts[1])
            elif head == "trap" and parts[0] == "sprung" and len(parts) > 1:
                out.traps.append(parts[1])
            elif head == "quest" and parts[0] in ("done", "failed") and len(parts) > 1:
                out.quests.append((parts[0], parts[1]))
            elif head == "contact" and parts[0] == "new" and len(parts) > 2:
                out.contacts[parts[1]] = out.contacts.get(parts[1], 0) + int(_number(parts[2]))
            elif head == "lifestyle" and len(parts) > 3:
                out.paid += int(_number(parts[2]))
                out.owed += int(_number(parts[3]))
            elif head == "receive" and len(parts) > 1:
                items, copper = out.received.setdefault(entry.subject_name or "?", ([], 0))
                for part in filter(None, parts[0].split(";")):
                    ref, _, count = part.rpartition("*")
                    items.append((ref, int(_number(count)) or 1))
                out.received[entry.subject_name or "?"] = (items, copper + _copper(parts[1]))
            elif head == "bastion" and parts[0].isdigit():
                out.bastion_turns.append(entry.subject_name or "?")
            elif head == "downtime" and parts[0].isdigit():
                out.downtime += int(parts[0])
            elif entry.kind == EntryKind.EXPERIENCE and entry.amount:
                name = entry.subject_name or "?"
                out.xp[name] = out.xp.get(name, 0) + entry.amount
            elif entry.kind == EntryKind.LEVEL_UP:
                out.levels.append(entry.subject_name or "?")
        return out

    def brief(self, world_id: str) -> Brief:
        """What to know before play (see the module's note)."""
        store = self._store
        members = store.characters.of_world(world_id, drafts=False)
        minute = self._minute_of(world_id)
        notes = sorted(
            store.session_notes.list(lambda n: n.world_id == world_id),
            key=lambda n: (n.day, n.created_at),
            reverse=True,
        )
        previous = self.last(world_id)
        # Lifestyle owed since the last session ended (or ever, before the first).
        since = previous.ended_at if previous is not None and previous.ended_at else None
        owed: dict[str, int] = {}
        for entry in self._entries(world_id, since):
            head, _, rest = entry.what.partition(":")
            parts = rest.split(":")
            if head == "lifestyle" and len(parts) > 3 and _number(parts[3]):
                name = entry.subject_name or "?"
                owed[name] = owed.get(name, 0) + int(_number(parts[3]))
        contacts = store.contacts.list(lambda c: c.world_id == world_id and not c.name)
        unnamed = [
            (pc.id, pc.name or "?", n)
            for pc in members
            if (n := sum(1 for c in contacts if c.character_id == pc.id))
        ]
        downtime = []
        for pc in members:
            spare = store.downtime.find(pc.id)
            if spare is not None and spare.days:
                downtime.append((pc.id, pc.name or "?", spare.days))
        built = [
            (pc.name or "?", bastion.last_turn)
            for pc in members
            if pc.total_level >= 5
            and (bastion := store.bastions.find(pc.id)) is not None
            and bastion.facilities
        ]
        shop = store.shops.find(world_id)
        # What the world plays without stays off the list (app/services/features.py).
        on = features.switched_on(store.worlds.find(world_id))
        return Brief(
            session=self.of(world_id),
            minute=minute,
            last_note=notes[0] if notes else None,
            timers=self._timers_of(world_id),
            bastions=bastions_due(minute, built) if "bastions" in on else [],
            owed=sorted(owed.items()) if "lifestyle" in on else [],
            unnamed=unnamed if "downtime" in on else [],
            downtime=downtime if "downtime" in on else [],
            chase=store.chases.find(world_id) is not None and "chases" in on,
            shop=shop.name if shop is not None and shop.open and "shops" in on else None,
            calls=len(store.checks.list(lambda c: c.open and c.world_id == world_id)),
            quests=len(store.quests.list(lambda q: q.world_id == world_id and q.status == "open"))
            if "quests" in on
            else 0,
        )

    def _entries(self, world_id: str, since: datetime | None) -> list[ProtocolEntry]:
        if self._protocol is None:
            return []
        found = []
        for entry in reversed(self._protocol.store.entries(log_key(world_id))):
            if since is not None and entry.at <= since:
                break
            found.append(entry)
        found.reverse()
        return found

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
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            ]
        )


_COPPER = {"cp": 1, "sp": 10, "ep": 50, "gp": 100, "pp": 1000}


def _copper(purse: str) -> int:
    """A logged purse ("20gp,5sp") in copper."""
    total = 0
    for part in filter(None, purse.split(",")):
        amount, unit = part[:-2], part[-2:]
        if amount.isdigit() and unit in _COPPER:
            total += int(amount) * _COPPER[unit]
    return total


def _number(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return 0


__all__ = ["Brief", "Happened", "PlaySessionService"]
