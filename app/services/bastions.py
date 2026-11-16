"""A character's bastion (the 2024 rules): its facilities, defenders and turns.

From level 5 a character has a bastion. Its special facilities are chosen from the world's
books, each no higher in level than the character; how many follows the level (two at 5,
four at 9, five at 13, six at 17). Basic facilities (a bedroom, a kitchen…) are listed
with their space, the first two free. Each special facility may be given one of its
orders for the next bastion turn, or the whole bastion the Maintain order instead; the
gamemaster runs the turn, which logs the orders and starts the next.

A turn on Maintain rolls the books' Bastion Events table: the event is logged and shown
on the page with its text. An attack rolls 6d6 and loses a Bastion Defender for each 1;
with none left, a special facility picked at random is shut and takes no order on the
next turn, unless a friendly monster stays as a guest and defends it. The other events
are carried out as app/services/bastion_events.py says, some waiting for the player's
decision.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import BasicFacility, Bastion, BastionFacility, SourceSet
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.bastion_events import EventTools, aid, carry_out
from app.services.bastion_orders import OrderYields
from app.services.dice import Dice
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

FROM_LEVEL = 5
DAY = 24 * 60
SPACES = ("cramped", "roomy", "vast")
# A row of the events table: its range and the section it points at.
_AREA = re.compile(r"\{@area ([^|}]+)\|([^|}]+)")
# (character level, special facilities), highest first.
_SPECIAL = ((17, 6), (13, 5), (9, 4), (5, 2))


class BastionError(Translatable):
    """A change the bastion rules refuse."""


def special_slots(level: int) -> int:
    """How many special facilities a character of ``level`` may have."""
    return next((n for start, n in _SPECIAL if level >= start), 0)


@dataclass(frozen=True)
class Facility:
    """A special facility as the bastion page offers it."""

    name: str
    source: str
    level: int
    orders: tuple[str, ...]

    @property
    def reference(self) -> str:
        return f"{self.name}|{self.source}"


@dataclass(frozen=True)
class BastionEvent:
    """A row of the Bastion Events table: its d100 range, the book entry that tells
    it (its id) and its name."""

    low: int
    high: int
    entry: str
    name: str


def event_rows(rows: Iterable[Any]) -> list[BastionEvent]:
    """The table's rows as events (``"01–50"``, ``"99–00"``: 00 is 100)."""
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        span = re.split(r"[–—-]", str(row[0]))
        bounds = [100 if b.strip() == "00" else int(b) for b in span if b.strip().isdigit()]
        found = _AREA.search(str(row[1]))
        if not bounds or found is None:
            continue
        out.append(BastionEvent(bounds[0], bounds[-1], found[2], found[1]))
    return out


def basic_facilities(entries: Iterable[Any], source_set: SourceSet | None) -> list[Facility]:
    """The basic facilities of the world's books."""
    wanted = (
        None if source_set is None or source_set.is_unrestricted else set(source_set.normalized)
    )
    found = []
    for entry in entries:
        data = entry.model_dump(by_alias=True, exclude_none=True)
        if data.get("facilityType") != "basic":
            continue
        if wanted is not None and str(data.get("source", "")).lower() not in wanted:
            continue
        found.append(Facility(str(data["name"]), str(data["source"]), 0, ()))
    return sorted(found, key=lambda f: f.name.lower())


def facilities(entries: Iterable[Any], source_set: SourceSet | None, level: int) -> list[Facility]:
    """The special facilities of the world's books a character of ``level`` may build."""
    wanted = (
        None if source_set is None or source_set.is_unrestricted else set(source_set.normalized)
    )
    found = []
    for entry in entries:
        data = entry.model_dump(by_alias=True, exclude_none=True)
        if data.get("facilityType") != "special":
            continue
        if wanted is not None and str(data.get("source", "")).lower() not in wanted:
            continue
        need = int(data.get("level") or FROM_LEVEL)
        if need > level:
            continue
        orders = tuple(str(o) for o in data.get("orders") or [])
        found.append(Facility(str(data["name"]), str(data["source"]), need, orders))
    return sorted(found, key=lambda f: (f.level, f.name.lower()))


class BastionService:
    """Reads and writes ``library/bastions.json``; a bastion turn goes to the log."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        events: Callable[[], list[BastionEvent]] = list,
        yields: OrderYields | None = None,
        pay: Callable[[str, int, str], bool] | None = None,
        tools: EventTools | None = None,
        dice: Dice | None = None,
    ) -> None:
        """``events()`` reads the books' Bastion Events table (empty without it);
        ``yields`` carries orders out on the turn; ``pay(character, copper, why)`` pays
        for building (False: the purse is short); ``tools`` carry events out
        (app/services/bastion_events.py)."""
        self._store = store
        self._protocol = protocol
        self._events = events
        self._yields = yields
        self._pay = pay
        self._tools = tools
        self._dice = dice or Dice()

    def of(self, character_id: str) -> Bastion:
        """A character's bastion (empty until something is chosen)."""
        return self._store.bastions.find(character_id) or Bastion(id=character_id)

    def rename(self, character_id: str, name: str) -> Bastion:
        return self._save(
            self.of(character_id).model_copy(update={"name": " ".join(name.split())[:80]})
        )

    def add(self, character_id: str, level: int, facility: Facility) -> Bastion:
        """Build a special facility, within the level's count and not twice."""
        bastion = self.of(character_id)
        if level < FROM_LEVEL or facility.level > level:
            raise BastionError("bastion.error.level")
        if len(bastion.facilities) >= special_slots(level):
            raise BastionError("bastion.error.full")
        if any(f.ref.lower() == facility.reference.lower() for f in bastion.facilities):
            raise BastionError("bastion.error.twice")
        built = [*bastion.facilities, BastionFacility(ref=facility.reference)]
        return self._save(bastion.model_copy(update={"facilities": built}))

    def remove(self, character_id: str, index: int) -> Bastion:
        """Pull a facility down; not one shut after an attack, which building it anew
        would open again."""
        bastion = self.of(character_id)
        if 0 <= index < len(bastion.facilities) and bastion.facilities[index].shut:
            raise BastionError("bastion.error.shut")
        kept = [f for n, f in enumerate(bastion.facilities) if n != index]
        return self._save(bastion.model_copy(update={"facilities": kept}))

    def add_basic(
        self,
        character_id: str,
        level: int,
        facility: Facility,
        space: str,
        *,
        costs: Mapping[str, tuple[int, int]] | None = None,
        at: int | None = None,
    ) -> Bastion:
        """Add a basic facility of a space; a bastion may have several of one. The first
        two are free (one Cramped, one Roomy); any other is paid now and stands once
        its days (``costs[space]``: copper, days) have passed on the clock ``at``."""
        if level < FROM_LEVEL:
            raise BastionError("bastion.error.level")
        if space not in SPACES:
            raise BastionError("bastion.error.order")
        bastion = self.of(character_id)
        free = {"cramped", "roomy"} - {f.space for f in bastion.basic[:2]}
        ready = None
        if not (len(bastion.basic) < 2 and space in free) and costs and space in costs:
            cost, days = costs[space]
            self._charge(character_id, cost, f"bastion {facility.name}")
            ready = at + days * DAY if at is not None else None
        built = [*bastion.basic, BasicFacility(ref=facility.reference, space=space, ready=ready)]
        return self._save(bastion.model_copy(update={"basic": built}))

    def enlarge_basic(
        self,
        character_id: str,
        index: int,
        costs: Mapping[str, tuple[int, int]],
        at: int | None = None,
    ) -> Bastion:
        """Enlarge a basic facility a step (``costs`` by the space it grows from)."""
        bastion = self.of(character_id)
        if not 0 <= index < len(bastion.basic):
            raise BastionError("bastion.error.order")
        facility = bastion.basic[index]
        step = SPACES.index(facility.space)
        if step + 1 >= len(SPACES) or facility.space not in costs or facility.ready:
            raise BastionError("bastion.error.order")
        cost, days = costs[facility.space]
        self._charge(character_id, cost, f"bastion {facility.ref.partition('|')[0]}")
        grown = facility.model_copy(
            update={
                "space": SPACES[step + 1],
                "ready": at + days * DAY if at is not None else None,
            }
        )
        basic = [grown if n == index else f for n, f in enumerate(bastion.basic)]
        return self._save(bastion.model_copy(update={"basic": basic}))

    def _charge(self, character_id: str, cost: int, why: str) -> None:
        if cost and self._pay is not None and not self._pay(character_id, cost, why):
            raise BastionError("bastion.error.money")

    def remove_basic(self, character_id: str, index: int) -> Bastion:
        bastion = self.of(character_id)
        kept = [f for n, f in enumerate(bastion.basic) if n != index]
        return self._save(bastion.model_copy(update={"basic": kept}))

    def defenders(
        self,
        character_id: str,
        change: int,
        character_name: str,
        by: str,
        by_name: str,
        world_id: str | None = None,
    ) -> Bastion:
        """Recruit (or lose) Bastion Defenders, logged."""
        bastion = self.of(character_id)
        count = max(0, min(999, bastion.defenders + change))
        if count != bastion.defenders:
            self._log(
                character_id,
                character_name,
                world_id,
                by,
                by_name,
                f"bastion_defenders:{count - bastion.defenders}:{count}",
            )
        return self._save(bastion.model_copy(update={"defenders": count}))

    def maintain(self, character_id: str, on: bool) -> Bastion:
        """Issue (or take back) the Maintain order for the next turn."""
        return self._save(self.of(character_id).model_copy(update={"maintain": on}))

    def order(
        self,
        character_id: str,
        index: int,
        order: str,
        allowed: Iterable[str],
        option: str = "",
    ) -> Bastion:
        """Give a facility one of its orders for the next turn ("" takes it back), with
        the option it carries out; a facility shut after an attack takes none."""
        bastion = self.of(character_id)
        if not 0 <= index < len(bastion.facilities) or (order and order not in set(allowed)):
            raise BastionError("bastion.error.order")
        if order and bastion.facilities[index].shut:
            raise BastionError("bastion.error.shut")
        changed = [
            f.model_copy(update={"order": order, "option": option if order else ""})
            if n == index
            else f
            for n, f in enumerate(bastion.facilities)
        ]
        return self._save(bastion.model_copy(update={"facilities": changed}))

    def equip(
        self, character_id: str, index: int, tools: Sequence[str], allowed: Sequence[str], most: int
    ) -> Bastion:
        """The tools a facility is equipped with (a Workshop's six kinds of Artisan's
        Tools), each from ``allowed``, different, and at most ``most``."""
        bastion = self.of(character_id)
        known = {a.lower(): a for a in allowed}
        chosen = list(dict.fromkeys(known.get(t.lower(), "") for t in tools))
        if not 0 <= index < len(bastion.facilities) or "" in chosen or len(chosen) > most:
            raise BastionError("bastion.error.tools")
        changed = [
            f.model_copy(update={"tools": chosen}) if n == index else f
            for n, f in enumerate(bastion.facilities)
        ]
        return self._save(bastion.model_copy(update={"facilities": changed}))

    def turn(
        self,
        character_id: str,
        character_name: str,
        world_id: str | None,
        by: str,
        by_name: str,
        at: int | None = None,
    ) -> Bastion:
        """A bastion turn: the orders are logged for the table, then cleared, or on
        Maintain an event is rolled; ``at`` is the world's clock, from which the next
        turn is due seven days on. A facility shut by the last attack opens again."""
        bastion = self.of(character_id)
        orders = (
            []
            if bastion.maintain
            else [f"{f.ref}={f.order}" for f in bastion.facilities if f.order]
        )
        number = bastion.turns + 1
        what = f"bastion:{number}:{';'.join(orders)}"
        self._log(character_id, character_name, world_id, by, by_name, what)
        lines: list[str] = []
        recruited = 0
        pending = list(bastion.pending)
        if self._yields is not None:
            done, pending = self._yields.deliver(character_id, pending, at)
            lines += [y.line for y in done]
            if not bastion.maintain:
                ordered = [
                    (f.ref, f.order, f.option) for f in bastion.facilities if f.order and not f.shut
                ]
                made, recruited, more = self._yields.run(
                    character_id,
                    ordered,
                    defenders=bastion.defenders,
                    facilities=[f.ref for f in bastion.facilities],
                    at=at,
                )
                lines += [y.line for y in made]
                pending += more
        cleared = [
            f.model_copy(update={"order": "", "option": "", "shut": False})
            for f in bastion.facilities
        ]
        basic = [
            f.model_copy(update={"ready": None})
            if f.ready is not None and at is not None and f.ready <= at
            else f
            for f in bastion.basic
        ]
        update: dict[str, Any] = {
            "facilities": cleared,
            "basic": basic,
            "turns": number,
            "last_turn": at,
            "maintain": False,
            "event": "",
            "event_yields": [],
            "offer": "",
            "yields": lines,
            "pending": pending,
            "defenders": min(999, bastion.defenders + recruited),
        }
        if bastion.maintain:
            update |= self._event(character_id, update["defenders"], cleared, bastion.guardian)
            name = update["event"].split("|")[2] if update["event"] else ""
            if name:
                lost = bastion.defenders - update.get("defenders", bastion.defenders)
                self._log(
                    character_id,
                    character_name,
                    world_id,
                    by,
                    by_name,
                    f"bastion_event:{name}:{lost}",
                )
        return self._save(bastion.model_copy(update=update))

    def _event(
        self,
        character_id: str,
        defenders: int,
        facilities: list[BastionFacility],
        guardian: bool,
        exclude: str = "",
    ) -> dict[str, Any]:
        """Roll the Bastion Events table and carry the event out: an attack costs
        defenders (none while a friendly monster guards), or shuts a facility; the
        others as app/services/bastion_events.py says. ``exclude`` rerolls that event."""
        rows = self._events()
        if not rows:
            return {}
        event = None
        for _ in range(20):
            roll = self._dice.die(100)
            event = next((e for e in rows if e.low <= roll <= e.high), None)
            if event is None or event.name.lower() != exclude.lower():
                break
        if event is None:
            return {}
        update: dict[str, Any] = {"event": f"{roll}|{event.entry}|{event.name}"}
        if event.name.lower() == "attack":
            if guardian:
                update["guardian"] = False
                update["event"] += "|0"
                update["event_yields"] = ["guarded|1"]
                return update
            ones = sum(1 for _ in range(6) if self._dice.die(6) == 1)
            left = max(0, defenders - ones)
            update["defenders"] = left
            update["event"] += f"|{ones}"
            if left == 0 and facilities:
                hit = self._dice.die(len(facilities)) - 1
                update["facilities"] = [
                    f.model_copy(update={"shut": True}) if n == hit else f
                    for n, f in enumerate(facilities)
                ]
                update["event"] += f"|{facilities[hit].ref}"
            return update
        if self._tools is None:
            return update
        done = carry_out(
            event.name,
            character_id,
            defenders=defenders,
            facilities=[f.ref for f in facilities],
            guardian=guardian,
            tools=self._tools,
            dice=self._dice,
        )
        update["event_yields"] = done.lines
        update["offer"] = done.offer
        if done.defenders is not None:
            update["defenders"] = done.defenders
        if done.guardian is not None:
            update["guardian"] = done.guardian
        if done.shut:
            update["facilities"] = [
                f.model_copy(update={"shut": True}) if f.ref == done.shut else f for f in facilities
            ]
        return update

    def answer(
        self,
        character_id: str,
        character_name: str,
        world_id: str | None,
        accept: bool,
        value: str,
        by: str,
        by_name: str,
    ) -> Bastion:
        """The player's decision on the last event's offer: pay the bribe or the
        opportunity, send defenders (``value`` how many), take a discovery (``value`` the
        item); or decline it."""
        bastion = self.of(character_id)
        kind, _, amount_text = bastion.offer.partition("|")
        if not kind or self._tools is None:
            raise BastionError("bastion.error.order")
        amount = int(amount_text) if amount_text.isdigit() else 0
        tools = self._tools
        update: dict[str, Any] = {"offer": ""}
        lines = list(bastion.event_yields)
        if kind == "bribe":
            if accept and tools.pay(character_id, amount, "bribe"):
                lines.append(f"paid|{amount}")
            elif accept:
                raise BastionError("bastion.error.short")
            elif bastion.facilities:
                hit = self._dice.die(len(bastion.facilities)) - 1
                update["facilities"] = [
                    f.model_copy(update={"shut": True}) if n == hit else f
                    for n, f in enumerate(bastion.facilities)
                ]
                lines.append(f"shut|{bastion.facilities[hit].ref}")
        elif kind == "opportunity" and accept:
            if not tools.pay(character_id, amount, "opportunity"):
                raise BastionError("bastion.error.short")
            lines.append(f"paid|{amount}")
            again = self._event(
                character_id,
                bastion.defenders,
                bastion.facilities,
                bastion.guardian,
                exclude="Extraordinary Opportunity",
            )
            name = again.get("event", "").split("|")[2] if again.get("event") else ""
            if name:
                lines.append(f"again|{name}")
                self._log(
                    character_id, character_name, world_id, by, by_name, f"bastion_event:{name}:0"
                )
            lines += again.pop("event_yields", [])
            again.pop("event", None)
            update |= again
        elif kind == "aid" and accept:
            sent = int(value) if value.isdigit() else 0
            if not 1 <= sent <= bastion.defenders:
                raise BastionError("bastion.error.order")
            total, reward, lost = aid(sent, self._dice)
            tools.earn(character_id, reward)
            update["defenders"] = bastion.defenders - lost
            lines.append(f"aid|{total}|{reward}|{lost}")
        elif kind == "discovery" and accept:
            if not value:
                raise BastionError("bastion.error.order")
            tools.give(character_id, value, 1)
            lines.append(f"item|{value}")
        update["event_yields"] = lines
        return self._save(bastion.model_copy(update=update))

    def _log(
        self,
        character_id: str,
        character_name: str,
        world_id: str | None,
        by: str,
        by_name: str,
        what: str,
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{character_id}",
                    subject_name=character_name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    scope=Scope(world=world_id),
                )
            ]
        )

    def _save(self, bastion: Bastion) -> Bastion:
        bastion.touch()
        return self._store.bastions.upsert(bastion)


__all__ = [
    "FROM_LEVEL",
    "SPACES",
    "BastionError",
    "BastionEvent",
    "BastionService",
    "Facility",
    "basic_facilities",
    "event_rows",
    "facilities",
    "special_slots",
]
