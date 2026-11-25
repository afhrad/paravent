"""Downtime: days between adventures, and what each character spends them on.

The gamemaster grants the party days of downtime from the party view; the world's
clock moves on by them, the party has a long rest, and each character of the world
has that many days to spend. A player spends them on a project on the character's
downtime page:

* **Crafting** an item the shop sells: raw materials cost half its price, paid when
  the work starts. Under the 2014 rules a day of work makes 5 gp of progress; under
  the 2024 rules the work takes a day per 10 GP of its price (round up). When the
  days are done the item is in the pack.
* **Training** in a language or a tool, by the world's books: with *Xanathar's Guide*
  10 workweeks less the Intelligence modifier at 25 gp a workweek, else with the 2014
  *Player's Handbook* 250 days at 1 gp a day; the 2024 books have no such rule, so the
  days are the gamemaster's and cost nothing. Then the character knows it.
* **Scribing a spell scroll** of a spell the character has, by the book's Spell Scroll
  Costs table (the 2024 one in a 2024 world): the cost paid at the start, the scroll in
  the pack when the days are done.
* **Crafting a magic item** the character holds a formula for, by the world's guide
  (app/services/magic_crafting.py): its days, and its cost paid at the start or, under
  the 2014 guide, 25 gp each day; the item in the pack when the days are done.
* **Research**: 1 gp a day, for as many days as the gamemaster says.
* **Recuperating**: three days, then a DC 15 Constitution saving throw is rolled.
* **Working**, or **anything else**: days only.

Each step is logged; prices and progress come from here, never from a form.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from math import ceil

from app.models.gamestate import Downtime, DowntimeProject
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

KINDS = ("craft", "train", "research", "recuperate", "work", "other", "scribe", "enchant")
# The most days granted at once: a year.
MOST_DAYS = 365
TRAINING_DAYS = 250
RECUPERATING_DAYS = 3
# What a day of training or research costs, in copper.
DAILY = {"train": 100, "research": 100}


class DowntimeError(Translatable):
    """A downtime step the rules refuse."""


def crafting_days(price: int, rules: str) -> int:
    """The days crafting an item of ``price`` copper takes alone."""
    gold = price / 100
    return max(1, ceil(gold / 10)) if rules == "one" else max(1, ceil(gold / 5))


def materials(price: int) -> int:
    """What the raw materials for an item of ``price`` copper cost: half (round down)."""
    return price // 2


def training_plan(rule: str, int_mod: int, days: int | None = None) -> tuple[int | None, int]:
    """Training's days and copper a day: ``rule`` "xge" (10 workweeks less the
    Intelligence modifier, at least one, 25 gp a workweek), "phb" (250 days at 1 gp)
    or "table" (``days`` as the gamemaster says, free)."""
    if rule == "xge":
        return max(1, 10 - max(0, int_mod)) * 5, 500
    if rule == "phb":
        return TRAINING_DAYS, DAILY["train"]
    return days, 0


class DowntimeService:
    """Reads and writes ``library/downtime.json``; every step goes to the log."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        pay: Callable[[str, int, str], None],
        give: Callable[[str, str], None],
        learn: Callable[[str, str, str], None],
        recuperate: Callable[[str], None],
    ) -> None:
        """``pay(character, copper, what)`` takes coins from the purse (raising when it
        holds too little); ``give(character, item)`` puts a made item in the pack;
        ``learn(character, kind, ref)`` a trained language or tool; ``recuperate``
        rolls the Constitution save."""
        self._store = store
        self._protocol = protocol
        self._pay = pay
        self._give = give
        self._learn = learn
        self._recuperate = recuperate

    def of(self, character_id: str) -> Downtime:
        """A character's downtime (no days, no projects until granted or started)."""
        return self._store.downtime.find(character_id) or Downtime(id=character_id)

    def grant(
        self, party: Sequence[PlayerCharacter], days: int, world_id: str, by: str, by_name: str
    ) -> list[str]:
        """Give each character ``days`` of downtime: the names of those who got them."""
        if not 0 < days <= MOST_DAYS:
            raise DowntimeError("downtime.error.days")
        names = []
        for pc in party:
            held = self.of(pc.id)
            self._save(held.model_copy(update={"days": held.days + days}))
            names.append(pc.name or "?")
        self._log(world_id, by, by_name, f"downtime:{days}")
        return names

    def start(
        self,
        pc: PlayerCharacter,
        kind: str,
        label: str,
        *,
        needed: int | None = None,
        price: int | None = None,
        rules: str = "classic",
        daily: int | None = None,
        by: str,
        by_name: str,
        days_later: bool = False,
    ) -> DowntimeProject:
        """A new project. Crafting needs the item's ``price`` (its materials are paid
        now); training a ``language:`` or ``tool:`` reference with its ``needed`` days
        and ``daily`` cost (``training_plan``), or none with ``days_later``, the
        gamemaster setting them later (a world without either book's training rules);
        scribing ``"<scroll>;<spell>"`` with its days and full ``price``, paid now; a
        magic item (``enchant``) its reference, days, the ``price`` paid now and the
        ``daily`` cost (app/services/magic_crafting.py); research and anything else may
        name their days."""
        label = " ".join(label.split())[:200]
        if kind not in KINDS:
            raise DowntimeError("downtime.error.kind")
        if kind == "craft":
            if price is None or price <= 0:
                raise DowntimeError("downtime.error.item")
            needed = crafting_days(price, rules)
        elif kind == "train":
            if label.partition(":")[0] not in ("language", "tool") or ":" not in label:
                raise DowntimeError("downtime.error.item")
            if needed is None and days_later:
                pass  # waits for the gamemaster's days
            elif needed is None or not 0 < needed <= MOST_DAYS:
                raise DowntimeError("downtime.error.days")
        elif kind == "scribe":
            if ";" not in label or price is None or price < 0 or not needed:
                raise DowntimeError("downtime.error.item")
        elif kind == "enchant":
            if "|" not in label or price is None or price < 0 or not needed:
                raise DowntimeError("downtime.error.item")
        elif kind == "recuperate":
            needed = RECUPERATING_DAYS
        elif kind == "work":
            needed = None
        elif needed is not None and not 0 < needed <= MOST_DAYS:
            raise DowntimeError("downtime.error.days")
        if kind in ("research", "other") and not label:
            raise DowntimeError("downtime.error.label")
        paid = 0
        if kind == "craft" and price:
            paid = materials(price)
            self._pay(pc.id, paid, f"materials {label}")
        elif kind == "scribe" and price:
            paid = price
            self._pay(pc.id, paid, f"scroll {label.partition(';')[2]}")
        elif kind == "enchant" and price:
            paid = price
            self._pay(pc.id, paid, f"crafting {label}")
        project = DowntimeProject(
            id=uuid.uuid4().hex[:10],
            kind=kind,
            label=label,
            needed=needed,
            paid=paid,
            daily=daily if kind in ("train", "enchant") else None,
        )
        held = self.of(pc.id)
        self._save(held.model_copy(update={"projects": [*held.projects, project]}))
        self._log(pc.world_id, by, by_name, f"downtime_start:{kind}:{label}", pc)
        return project

    def set_days(self, pc: PlayerCharacter, project_id: str, days: int) -> DowntimeProject:
        """The gamemaster's days for a training that waits for them."""
        held = self.of(pc.id)
        project = next((p for p in held.projects if p.id == project_id), None)
        if project is None or project.finished or project.kind != "train":
            raise DowntimeError("downtime.error.project")
        if not 0 < days <= MOST_DAYS:
            raise DowntimeError("downtime.error.days")
        changed = project.model_copy(update={"needed": max(days, project.done)})
        projects = [changed if p.id == project_id else p for p in held.projects]
        record = held.model_copy(update={"projects": projects})
        record.touch()
        self._store.downtime.upsert(record)
        return changed

    def spend(
        self, pc: PlayerCharacter, project_id: str, days: int, by: str, by_name: str
    ) -> DowntimeProject:
        """Spend ``days`` of the character's free days on a project, paying what each
        day costs; a project done gives what it makes."""
        held = self.of(pc.id)
        project = next((p for p in held.projects if p.id == project_id), None)
        if project is None or project.finished:
            raise DowntimeError("downtime.error.project")
        if project.kind == "train" and project.needed is None:
            raise DowntimeError("downtime.error.days_unset")
        if project.needed is not None:
            days = min(days, project.needed - project.done)
        if not 0 < days <= held.days:
            raise DowntimeError("downtime.error.days")
        rate = project.daily if project.daily is not None else DAILY.get(project.kind, 0)
        cost = rate * days
        if cost:
            self._pay(pc.id, cost, f"{project.kind} {project.label}")
        done = project.done + days
        finished = project.needed is not None and done >= project.needed
        changed = project.model_copy(
            update={"done": done, "paid": project.paid + cost, "finished": finished}
        )
        projects = [changed if p.id == project_id else p for p in held.projects]
        self._save(held.model_copy(update={"days": held.days - days, "projects": projects}))
        self._log(
            pc.world_id,
            by,
            by_name,
            f"downtime_spent:{project.kind}:{days}:{project.label}",
            pc,
        )
        if finished:
            self._finish(pc, changed, by, by_name)
        return changed

    def record(
        self,
        pc: PlayerCharacter,
        kind: str,
        label: str,
        *,
        days: int,
        paid: int,
        result: Sequence[str],
        offers: Sequence[str] = (),
        by: str,
        by_name: str,
    ) -> DowntimeProject:
        """An activity done at once (app/services/activities.py): its days come off the
        free days and its outcome is kept as a finished project, logged."""
        held = self.of(pc.id)
        if not 0 < days <= held.days:
            raise DowntimeError("downtime.error.days")
        project = DowntimeProject(
            id=uuid.uuid4().hex[:10],
            kind=kind,
            label=label[:200],
            needed=days,
            done=days,
            paid=paid,
            finished=True,
            result=list(result),
            offers=list(offers),
        )
        self._save(
            held.model_copy(
                update={"days": held.days - days, "projects": [*held.projects, project]}
            )
        )
        self._log(pc.world_id, by, by_name, f"downtime_spent:{kind}:{days}:{label}", pc)
        return project

    def take_offer(self, character_id: str, project_id: str, index: int) -> str:
        """Take one of a project's offers off it (it was bought or sold): the offer."""
        held = self.of(character_id)
        project = next((p for p in held.projects if p.id == project_id), None)
        if project is None or not 0 <= index < len(project.offers):
            raise DowntimeError("downtime.error.project")
        offer = project.offers[index]
        changed = project.model_copy(
            update={"offers": [o for n, o in enumerate(project.offers) if n != index]}
        )
        projects = [changed if p.id == project_id else p for p in held.projects]
        self._save(held.model_copy(update={"projects": projects}))
        return offer

    def drop(self, character_id: str, project_id: str) -> Downtime:
        """Give up a project (or clear away a finished one); what was paid is spent."""
        held = self.of(character_id)
        kept = [p for p in held.projects if p.id != project_id]
        return self._save(held.model_copy(update={"projects": kept}))

    def _finish(self, pc: PlayerCharacter, project: DowntimeProject, by: str, by_name: str) -> None:
        if project.kind in ("craft", "enchant"):
            self._give(pc.id, project.label)
        elif project.kind == "scribe":
            self._give(pc.id, project.label)  # "<scroll>;<spell>": it keeps its spell
        elif project.kind == "train":
            kind, _, ref = project.label.partition(":")
            self._learn(pc.id, kind, ref)
        elif project.kind == "recuperate":
            self._recuperate(pc.id)
        self._log(pc.world_id, by, by_name, f"downtime_done:{project.kind}:{project.label}", pc)

    def _log(
        self,
        world_id: str | None,
        by: str,
        by_name: str,
        what: str,
        pc: PlayerCharacter | None = None,
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}" if pc is not None else "",
                    subject_name=(pc.name or "?") if pc is not None else "",
                    kind=EntryKind.CUSTOM,
                    what=what,
                    scope=Scope(world=world_id),
                )
            ]
        )

    def _save(self, downtime: Downtime) -> Downtime:
        downtime.touch()
        return self._store.downtime.upsert(downtime)


__all__ = [
    "DAILY",
    "KINDS",
    "RECUPERATING_DAYS",
    "TRAINING_DAYS",
    "DowntimeError",
    "DowntimeService",
    "crafting_days",
    "materials",
    "training_plan",
]
