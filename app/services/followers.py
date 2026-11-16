"""Those who follow a character: hirelings, companions, mounts and sidekicks.

A hireling is skilled or untrained and paid by the day at the handbook's wage (the 2014
*Services* table, or the 2024 *Hirelings* table); every day the world's clock passes,
each character pays its hirelings from the purse, logged with what it could not pay. A
companion, a mount or a sidekick is a creature of the world's books, with the hit
points of its statblock, kept here between fights; it can join its character's running
fight on the party's side. A sidekick gains levels when the gamemaster says: each adds
its hit die's average and its Constitution modifier to its hit points (the sidekick
rules' growth). A sidekick may take a sidekick class of the books (Tasha's Expert,
Spellcaster or Warrior): its level is one more than the levels it has gained, its
proficiency bonus follows that level, and the class's features and spell counts up to
it are listed; its choices (roles, ability increases, spells) are the table's. A
character's mount lets the party ride when every traveller has one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.models.gamestate import Follower
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

HIRELINGS = ("skilled", "untrained")
CREATURES = ("companion", "mount", "sidekick")
_HIT_DICE = re.compile(r"(\d+)d(\d+)")


class FollowerError(Translatable):
    """A change to a follower the rules refuse."""


@dataclass(frozen=True)
class Statblock:
    """What a follower needs of its creature: average HP, the hit die, CON modifier."""

    name: str
    reference: str
    hp: int
    die: int = 8
    con: int = 0


def hit_die(formula: str) -> int:
    """The hit die of an HP formula ("5d10 + 10" → 10); a d8 where it names none."""
    found = _HIT_DICE.search(formula or "")
    return int(found[2]) if found else 8


def sidekick_level(follower: Follower) -> int:
    """A sidekick's level: one, and each level it has gained."""
    return 1 + follower.levels


def proficiency(level: int) -> int:
    """The proficiency bonus at a level: +2, one more every four levels."""
    return 2 + (max(1, level) - 1) // 4


def level_hp(die: int, con: int) -> int:
    """What a sidekick's level adds: the die's average (rounded up) and CON, at least 1."""
    return max(1, die // 2 + 1 + con)


class FollowerService:
    """Each character's followers (``library/followers.json``); wages and arrivals logged."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: ProtocolService | None,
        *,
        wages: Callable[[str | None], dict[str, int]],
        statblock: Callable[[str | None, str], Statblock | None],
        sidekick_classes: Callable[[str | None], list[tuple[str, str]]] = lambda _w: [],
        purse: Callable[[str], int] = lambda _cid: 0,
        pay: Callable[[str, int], None] = lambda _cid, _copper: None,
    ) -> None:
        """``wages(world)`` is copper a day by kind of hireling; ``statblock(world,
        name)`` finds a creature of the world's books; ``purse(character)`` what it
        holds in copper and ``pay(character, copper)`` takes it, as for lifestyle."""
        self._store = store
        self._protocol = protocol
        self._wages = wages
        self._statblock = statblock
        self._sidekick_classes = sidekick_classes
        self._purse = purse
        self._pay = pay

    def of(self, character_id: str) -> list[Follower]:
        found = self._store.followers.list(lambda f: f.character_id == character_id)
        return sorted(found, key=lambda f: (f.kind not in HIRELINGS, f.created_at))

    def get(self, character_id: str, follower_id: str) -> Follower:
        found = self._store.followers.find(follower_id)
        if found is None or found.character_id != character_id:
            raise FollowerError("followers.error.unknown")
        return found

    def hire(self, pc: PlayerCharacter, kind: str, name: str, note: str = "") -> Follower:
        """A hireling at the world's daily wage."""
        if kind not in HIRELINGS:
            raise FollowerError("followers.error.kind")
        wage = self._wages(pc.world_id).get(kind, 0)
        if not wage:
            raise FollowerError("followers.error.no_wages")
        return self._store.followers.create(
            Follower(
                character_id=pc.id,
                world_id=pc.world_id,
                kind=kind,
                name=_name(name) or kind,
                note=note.strip()[:2000],
                wage=wage,
            )
        )

    def add_creature(
        self,
        pc: PlayerCharacter,
        kind: str,
        creature: str,
        name: str = "",
        note: str = "",
        sidekick_class: str = "",
    ) -> Follower:
        """A companion, mount or sidekick: a creature of the world's books; a sidekick
        may take a sidekick class of them."""
        if kind not in CREATURES:
            raise FollowerError("followers.error.kind")
        if sidekick_class and (
            kind != "sidekick"
            or sidekick_class not in {ref for ref, _ in self._sidekick_classes(pc.world_id)}
        ):
            raise FollowerError("followers.error.sidekick_class")
        found = self._statblock(pc.world_id, creature.strip())
        if found is None:
            raise FollowerError("followers.error.creature", name=creature.strip())
        return self._store.followers.create(
            Follower(
                character_id=pc.id,
                world_id=pc.world_id,
                kind=kind,
                name=_name(name) or found.name,
                note=note.strip()[:2000],
                monster=found.reference,
                hp=found.hp,
                hp_max=found.hp,
                sidekick_class=sidekick_class,
            )
        )

    def hit_points(self, character_id: str, follower_id: str, delta: int) -> Follower:
        """Damage (negative) or healing, between 0 and its maximum."""
        follower = self.get(character_id, follower_id)
        hp = max(0, min(follower.hp_max, follower.hp + delta))
        return self._put(follower.model_copy(update={"hp": hp}))

    def level_up(self, character_id: str, follower_id: str) -> Follower:
        """A sidekick gains a level: its hit points rise by its die's average and CON."""
        follower = self.get(character_id, follower_id)
        found = self._statblock(follower.world_id, follower.monster)
        if follower.kind != "sidekick" or found is None or follower.levels >= 20:
            raise FollowerError("followers.error.level")
        more = level_hp(found.die, found.con)
        return self._put(
            follower.model_copy(
                update={
                    "levels": follower.levels + 1,
                    "hp_max": follower.hp_max + more,
                    "hp": follower.hp + more,
                }
            )
        )

    def rest(self, character_id: str) -> None:
        """A long rest: every creature following the character back to full."""
        for follower in self.of(character_id):
            if follower.kind in CREATURES and follower.hp < follower.hp_max:
                self._put(follower.model_copy(update={"hp": follower.hp_max}))

    def dismiss(self, character_id: str, follower_id: str) -> None:
        self.get(character_id, follower_id)
        self._store.followers.delete(follower_id)

    def mounted(self, character_ids: Sequence[str]) -> bool:
        """Whether every one of these characters has a mount."""
        riders = {f.character_id for f in self._store.followers.list() if f.kind == "mount"}
        return bool(character_ids) and all(c in riders for c in character_ids)

    def pay_wages(self, world_id: str, days: int, by: str, by_name: str) -> None:
        """``days`` of wages for every hireling of the world's characters, from each
        character's purse, logged ``wages:<days>:<paid>:<owed>``."""
        if days <= 0:
            return
        owing: dict[str, int] = {}
        for follower in self._store.followers.list(lambda f: f.world_id == world_id):
            if follower.kind in HIRELINGS and follower.wage:
                owing[follower.character_id] = (
                    owing.get(follower.character_id, 0) + follower.wage * days
                )
        entries = []
        for character_id, due in owing.items():
            pc = self._store.characters.find(character_id)
            if pc is None:
                continue
            paid = min(due, self._purse(character_id))
            if paid:
                self._pay(character_id, paid)
            entries.append(
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}",
                    subject_name=pc.name,
                    kind=EntryKind.CUSTOM,
                    what=f"wages:{days}:{paid}:{due - paid}",
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            )
        if entries and self._protocol is not None:
            self._protocol.append(entries)

    def _put(self, follower: Follower) -> Follower:
        follower.touch()
        return self._store.followers.upsert(follower)


def _name(text: str) -> str:
    return " ".join(text.split())[:120]


__all__ = [
    "CREATURES",
    "HIRELINGS",
    "FollowerError",
    "FollowerService",
    "Statblock",
    "hit_die",
    "level_hp",
    "proficiency",
    "sidekick_level",
]
