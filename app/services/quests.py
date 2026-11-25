"""A world's quests: who gave them, what they ask, their steps and their reward.

The gamemaster writes a quest: its name, who gave it (anyone, or a faction the quest
serves), its text, the steps to tick off, the characters on it (none: the world's
party), and its reward — experience for each, coins shared out evenly, items to the
characters the gamemaster picks, renown with its faction for each, and a mark of
prestige for each (a medal, a title; app/services/prestige.py). Players read the quests
shown to them in their journal. Marking a quest done hands the reward out through the
play rules (XP into the file, coins and items into the pack, renown into the faction,
the mark onto the character's story), each logged; a failed quest hands out nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import get_args

from app.models.gamestate import MarkKind, Quest, QuestItem, QuestStep
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.services.trash import Trash
from app.store.gamestate import GameStateStore


class QuestError(Translatable):
    """A change to a quest the rules refuse."""


@dataclass(frozen=True)
class Handout:
    """How the reward of a finished quest is handed out."""

    xp: Callable[[str, int, str], None]  # (character, xp, reason)
    receive: Callable[[str, list[tuple[str, int]], dict[str, int]], None]  # items, coins
    renown: Callable[[str, str, int], None]  # (faction, character, delta)
    # (character, kind, name, granted by): a mark of prestige for each on the quest
    mark: Callable[[str, str, str, str], None] = lambda _c, _k, _n, _g: None


def split_coins(copper: int, people: int) -> list[int]:
    """Copper shared out evenly, the remainder to the first."""
    if people < 1:
        return []
    each, rest = divmod(max(0, copper), people)
    return [each + (rest if n == 0 else 0) for n in range(people)]


def as_coins(copper: int) -> dict[str, int]:
    """Copper as gold, silver and copper pieces."""
    gp, rest = divmod(copper, 100)
    sp, cp = divmod(rest, 10)
    return {"gp": gp, "sp": sp, "cp": cp}


class QuestService:
    """Each world's quests (``library/quests.json``); new, finished and failed ones
    logged."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def of(self, world_id: str) -> list[Quest]:
        """A world's quests: the open ones first, then the newest."""
        found = self._store.quests.list(lambda q: q.world_id == world_id)
        found.sort(key=lambda q: q.created_at, reverse=True)
        return sorted(found, key=lambda q: q.status != "open")

    def shown_to(self, world_id: str, character_ids: Sequence[str]) -> list[Quest]:
        """The quests a player reads: shown, and the party's or one of theirs."""
        mine = set(character_ids)
        return [
            q
            for q in self.of(world_id)
            if q.shown and (not q.characters or mine & set(q.characters))
        ]

    def get(self, quest_id: str, world_id: str | None = None) -> Quest:
        quest = self._store.quests.find(quest_id)
        if quest is None or (world_id is not None and quest.world_id != world_id):
            raise QuestError("quests.error.unknown")
        return quest

    def save(
        self,
        world_id: str,
        values: Mapping[str, object],
        quest_id: str = "",
        by: str = "",
        by_name: str = "",
    ) -> Quest:
        """Write a quest from the form's ``values`` (new without ``quest_id``)."""
        name = " ".join(str(values.get("name", "")).split())[:160]
        if not name:
            raise QuestError("quests.error.name")
        faction_id = str(values.get("faction_id") or "") or None
        if faction_id is not None:
            faction = self._store.factions.find(faction_id)
            if faction is None or faction.world_id != world_id:
                faction_id = None
        members = {pc.id for pc in self._store.characters.of_world(world_id)}
        chosen = values.get("characters") or []
        assert isinstance(chosen, list)
        steps_in = values.get("steps") or []
        assert isinstance(steps_in, list)
        items_in = values.get("items") or []
        assert isinstance(items_in, list)
        fields = {
            "name": name,
            "giver": " ".join(str(values.get("giver", "")).split())[:160],
            "faction_id": faction_id,
            "text": str(values.get("text", "")).strip()[:8000],
            "steps": [s for s in steps_in if isinstance(s, QuestStep)][:30],
            "characters": [c for c in chosen if isinstance(c, str) and c in members],
            "shown": bool(values.get("shown", True)),
            "xp": _bounded(values.get("xp"), 1_000_000),
            "coins": _bounded(values.get("coins"), 100_000_000),
            "items": [i for i in items_in if isinstance(i, QuestItem)][:20],
            "renown": _bounded(values.get("renown"), 50),
            **_mark(values),
        }
        if quest_id:
            quest = self.get(quest_id, world_id)
            changed = quest.model_copy(update=fields)
            changed.touch()
            return self._store.quests.upsert(changed)
        created = self._store.quests.create(Quest(world_id=world_id, **fields))
        if created.shown:
            self._log(world_id, by, by_name, f"quest:new:{_safe(created.name)}")
        return created

    def step(self, quest_id: str, world_id: str, index: int) -> Quest:
        """Tick a step off, or back on."""
        quest = self.get(quest_id, world_id)
        if not 0 <= index < len(quest.steps):
            raise QuestError("quests.error.unknown")
        steps = [
            s.model_copy(update={"done": not s.done}) if n == index else s
            for n, s in enumerate(quest.steps)
        ]
        changed = quest.model_copy(update={"steps": steps})
        changed.touch()
        return self._store.quests.upsert(changed)

    def finish(
        self,
        quest_id: str,
        world_id: str,
        handout: Handout,
        *,
        given: Mapping[int, str],
        by: str,
        by_name: str,
    ) -> Quest:
        """Mark the quest done and hand out its reward: XP and renown to each on it,
        the coins shared evenly, each item to the character ``given`` names (by the
        item's place; the first on it otherwise)."""
        quest = self.get(quest_id, world_id)
        if quest.status != "open":
            raise QuestError("quests.error.closed")
        party = self.party(quest)
        if not party:
            raise QuestError("quests.error.no_one")
        reason = quest.name
        # A faction deleted since leaves the quest without renown, not unfinishable.
        faction = self._store.factions.find(quest.faction_id) if quest.faction_id else None
        renown = quest.renown if faction is not None and faction.world_id == world_id else 0
        items: dict[str, list[tuple[str, int]]] = {}
        for n, item in enumerate(quest.items):
            whom = given.get(n)
            items.setdefault(whom if whom in party else party[0], []).append((item.ref, item.count))
        shares = dict(zip(party, split_coins(quest.coins, len(party)), strict=True))
        # Done first, then handed out: a failure partway leaves the rest unpaid rather
        # than letting a second press pay everyone again.
        done = quest.model_copy(update={"status": "done"})
        done.touch()
        saved = self._store.quests.upsert(done)
        self._log(world_id, by, by_name, f"quest:done:{_safe(quest.name)}")
        for character_id in party:
            if quest.xp:
                handout.xp(character_id, quest.xp, reason)
            if renown and faction is not None:
                handout.renown(faction.id, character_id, renown)
            if quest.mark_kind and quest.mark_name:
                giver = quest.giver or (faction.name if faction is not None else "")
                handout.mark(character_id, quest.mark_kind, quest.mark_name, giver)
            got = items.get(character_id, [])
            copper = shares.get(character_id, 0)
            if got or copper:
                handout.receive(character_id, got, as_coins(copper))
        return saved

    def fail(self, quest_id: str, world_id: str, by: str, by_name: str) -> Quest:
        quest = self.get(quest_id, world_id)
        if quest.status != "open":
            raise QuestError("quests.error.closed")
        failed = quest.model_copy(update={"status": "failed"})
        failed.touch()
        self._log(world_id, by, by_name, f"quest:failed:{_safe(quest.name)}")
        return self._store.quests.upsert(failed)

    def reopen(self, quest_id: str, world_id: str) -> Quest:
        """A failed quest taken up again (a finished one keeps its reward handed out)."""
        quest = self.get(quest_id, world_id)
        if quest.status != "failed":
            raise QuestError("quests.error.closed")
        opened = quest.model_copy(update={"status": "open"})
        opened.touch()
        return self._store.quests.upsert(opened)

    def delete(self, quest_id: str, world_id: str) -> str | None:
        """Delete a quest, kept in the trash to restore; the trash entry's id."""
        quest = self.get(quest_id, world_id)
        kept = Trash(self._store).keep("quest", quest)
        self._store.quests.delete(quest_id)
        return kept

    def party(self, quest: Quest) -> list[str]:
        """Those on a quest: its characters still in the world, or the world's party."""
        world = [pc.id for pc in self._store.characters.of_world(quest.world_id, drafts=False)]
        if quest.characters:
            return [c for c in quest.characters if c in world]
        return world

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


def _mark(values: Mapping[str, object]) -> dict[str, str]:
    """A quest's mark of prestige from the form: a known kind and a name, or none."""
    kind = str(values.get("mark_kind") or "")
    name = " ".join(str(values.get("mark_name") or "").split())[:160]
    if kind not in get_args(MarkKind) or not name:
        return {"mark_kind": "", "mark_name": ""}
    return {"mark_kind": kind, "mark_name": name}


def _bounded(value: object, most: int) -> int:
    text = str(value or "").strip()
    return max(0, min(most, int(text))) if text.isdigit() else 0


def _safe(text: str) -> str:
    return text.replace(":", " ")


__all__ = ["Handout", "QuestError", "QuestService", "as_coins", "split_coins"]
