"""Downtime: days between adventures, and what each character spends them on.

The gamemaster grants the party days of downtime from the party view; the world's
clock moves on by them, the party has a long rest, and each character of the world
has that many days to spend. A player spends them on a project on the character's
downtime page:

* **Crafting** an item the shop sells: raw materials cost half its price, paid when
  the work starts. Under the 2014 rules a day of work makes 5 gp of progress; under
  the 2024 rules the work takes a day per 10 GP of its price (round up). When the
  days are done the item is in the pack.
* **Training** in a language or a tool: 250 days at 1 gp a day (the 2014 rules); then
  the character knows it.
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

KINDS = ("craft", "train", "research", "recuperate", "work", "other")
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
        by: str,
        by_name: str,
    ) -> DowntimeProject:
        """A new project. Crafting needs the item's ``price`` (its materials are paid
        now); training a ``language:`` or ``tool:`` reference; research and anything
        else may name their days."""
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
            needed = TRAINING_DAYS
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
        project = DowntimeProject(
            id=uuid.uuid4().hex[:10], kind=kind, label=label, needed=needed, paid=paid
        )
        held = self.of(pc.id)
        self._save(held.model_copy(update={"projects": [*held.projects, project]}))
        self._log(pc.world_id, by, by_name, f"downtime_start:{kind}:{label}", pc)
        return project

    def spend(
        self, pc: PlayerCharacter, project_id: str, days: int, by: str, by_name: str
    ) -> DowntimeProject:
        """Spend ``days`` of the character's free days on a project, paying what each
        day costs; a project done gives what it makes."""
        held = self.of(pc.id)
        project = next((p for p in held.projects if p.id == project_id), None)
        if project is None or project.finished:
            raise DowntimeError("downtime.error.project")
        if project.needed is not None:
            days = min(days, project.needed - project.done)
        if not 0 < days <= held.days:
            raise DowntimeError("downtime.error.days")
        cost = DAILY.get(project.kind, 0) * days
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

    def drop(self, character_id: str, project_id: str) -> Downtime:
        """Give up a project (or clear away a finished one); what was paid is spent."""
        held = self.of(character_id)
        kept = [p for p in held.projects if p.id != project_id]
        return self._save(held.model_copy(update={"projects": kept}))

    def _finish(self, pc: PlayerCharacter, project: DowntimeProject, by: str, by_name: str) -> None:
        if project.kind == "craft":
            self._give(pc.id, project.label)
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
]
