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

Loyalty is the guides' optional rule (2014 and 2024 alike): the gamemaster keeps a
follower's Loyalty Score in secret, from 0 to the highest Charisma score in the party,
starting at half of it. Helping it toward its goal, treating it particularly well or
rescuing it raises it by 1d4; acting against its alignment or bond lowers it by 1d4,
abusing, misleading or endangering it for selfish reasons by 2d4. From 10 it risks
anything for the party, below it is tenuous, and at 0 it no longer acts in the party's
interest. A score above a lowered maximum counts as the maximum.

A follower may crew a ship (the 2024 guide's Crew Loyalty and Mutiny): when at least half
of a ship's crew stand at a Loyalty Score of 0 during a voyage, the crew turns hostile and
mutinies, which the gamemaster is told in the log the moment it happens; berthed, the
disloyal crew leave the ship and never return.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from app.models.gamestate import Combatant, Follower
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.dice import Dice
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

HIRELINGS = ("skilled", "untrained")
CREATURES = ("companion", "mount", "sidekick")
# The strongest creature a player adds alone: a sidekick's limit (Tasha's).
PLAYER_FOLLOWER_CR = Fraction(1, 2)
_HIT_DICE = re.compile(r"(\d+)d(\d+)")
# What moves a Loyalty Score, by the guides: the dice and their direction.
SWAYS: dict[str, tuple[str, int]] = {
    "goal": ("1d4", 1),
    "kindness": ("1d4", 1),
    "against": ("1d4", -1),
    "abused": ("2d4", -1),
}
LOYAL_FROM = 10
# Where the guides print the rule, the world's rules' guide first.
LOYALTY_PRINTED = (("XDMG", "Loyalty"), ("DMG", "Loyalty"))


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
    cr: Fraction | None = None


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


def standing(score: int) -> str:
    """What a Loyalty Score means: loyal from 10, tenuous below, disloyal at 0."""
    return "loyal" if score >= LOYAL_FROM else "tenuous" if score else "disloyal"


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
        charisma: Callable[[str | None], int] = lambda _w: 10,
        dice: Dice | None = None,
    ) -> None:
        """``wages(world)`` is copper a day by kind of hireling; ``statblock(world,
        name)`` finds a creature of the world's books; ``purse(character)`` what it
        holds in copper and ``pay(character, copper)`` takes it, as for lifestyle;
        ``charisma(world)`` is the highest Charisma score among the world's characters."""
        self._store = store
        self._protocol = protocol
        self._wages = wages
        self._statblock = statblock
        self._sidekick_classes = sidekick_classes
        self._purse = purse
        self._pay = pay
        self._charisma = charisma
        self._dice = dice or Dice()

    def of(self, character_id: str) -> list[Follower]:
        found = self._store.followers.list(lambda f: f.character_id == character_id)
        return sorted(found, key=lambda f: (f.kind not in HIRELINGS, f.created_at))

    def get(self, character_id: str, follower_id: str) -> Follower:
        found = self._store.followers.find(follower_id)
        if found is None or found.character_id != character_id:
            raise FollowerError("followers.error.unknown")
        return found

    def hire(
        self, pc: PlayerCharacter, kind: str, name: str, note: str = "", ship: str = ""
    ) -> Follower:
        """A hireling at the world's daily wage, crewing ``ship`` if named."""
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
                ship=_name(ship)[:80],
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
        most_cr: Fraction | None = None,
    ) -> Follower:
        """A companion, mount or sidekick: a creature of the world's books, no stronger
        than ``most_cr`` when given (what a player may add alone); a sidekick may take
        a sidekick class of them."""
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
        if most_cr is not None and (found.cr is None or found.cr > most_cr):
            raise FollowerError("followers.error.too_strong", cr=str(most_cr))
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
        if (
            follower.kind != "sidekick" or found is None or follower.levels >= 19
        ):  # level 20 at most
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

    def after_fight(self, combatants: Sequence[Combatant]) -> None:
        """A fight over: each follower that fought keeps the hit points it ended with."""
        for member in combatants:
            found = self._store.followers.find(member.follower_id) if member.follower_id else None
            if found is not None and found.hp != min(member.hp, found.hp_max):
                self._put(found.model_copy(update={"hp": min(member.hp, found.hp_max)}))

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

    def loyalty_max(self, world_id: str | None) -> int:
        """A Loyalty Score's maximum: the highest Charisma score in the party."""
        return max(0, min(20, self._charisma(world_id)))

    def loyalty(self, follower: Follower) -> int | None:
        """A follower's Loyalty Score, no higher than its maximum now; None untracked."""
        if follower.loyalty is None:
            return None
        return min(follower.loyalty, self.loyalty_max(follower.world_id))

    def track(self, character_id: str, follower_id: str, by: str, by_name: str) -> Follower:
        """Start keeping a follower's Loyalty Score, at half its maximum."""
        follower = self.get(character_id, follower_id)
        if follower.loyalty is not None:
            return follower
        start = self.loyalty_max(follower.world_id) // 2
        self._log_loyalty(follower, "track", None, None, start, by, by_name)
        return self._put(follower.model_copy(update={"loyalty": start}))

    def untrack(self, character_id: str, follower_id: str) -> Follower:
        """Stop keeping a follower's Loyalty Score."""
        follower = self.get(character_id, follower_id)
        return self._put(follower.model_copy(update={"loyalty": None}))

    def sway(
        self,
        character_id: str,
        follower_id: str,
        reason: str,
        by: str,
        by_name: str,
        *,
        to: int | None = None,
    ) -> Follower:
        """Move a follower's Loyalty Score by one of the guides' reasons (rolled), or set
        it ``to`` a number (reason ``set``); kept between 0 and its maximum, logged for
        the gamemaster alone ``loyalty:<reason>:<roll>:<before>:<after>:<name>``."""
        follower = self.get(character_id, follower_id)
        before = self.loyalty(follower)
        if before is None:
            raise FollowerError("followers.error.untracked")
        roll: int | None = None
        if reason == "set" and to is not None:
            wanted = to
        elif reason in SWAYS:
            dice, sign = SWAYS[reason]
            roll = self._dice.roll(dice).total
            wanted = before + sign * roll
        else:
            raise FollowerError("followers.error.unknown")
        after = max(0, min(self.loyalty_max(follower.world_id), wanted))
        self._log_loyalty(follower, reason, roll, before, after, by, by_name)
        mutinous = bool(follower.ship) and self.mutiny(character_id, follower.ship)
        saved = self._put(follower.model_copy(update={"loyalty": after}))
        if follower.ship and not mutinous and self.mutiny(character_id, follower.ship):
            self._log_crew(saved, f"mutiny:{follower.ship}", by, by_name)
        return saved

    def crews(self, character_id: str) -> dict[str, list[Follower]]:
        """A character's ships and the followers crewing each, by ship name."""
        out: dict[str, list[Follower]] = {}
        for follower in self.of(character_id):
            if follower.ship:
                out.setdefault(follower.ship, []).append(follower)
        return out

    def mutiny(self, character_id: str, ship: str) -> bool:
        """Whether at least half of a ship's crew stand at a Loyalty Score of 0."""
        crew = self.crews(character_id).get(ship, [])
        disloyal = sum(1 for f in crew if self.loyalty(f) == 0)
        return bool(crew) and disloyal * 2 >= len(crew)

    def berth(self, character_id: str, ship: str, by: str, by_name: str) -> list[Follower]:
        """The ship is berthed: its disloyal crew (Loyalty 0) leave and never return."""
        leaving = [f for f in self.crews(character_id).get(ship, []) if self.loyalty(f) == 0]
        for follower in leaving:
            self._store.followers.delete(follower.id)
        if leaving:
            self._log_crew(leaving[0], f"crew_left:{len(leaving)}:{ship}", by, by_name)
        return leaving

    def _log_crew(self, follower: Follower, what: str, by: str, by_name: str) -> None:
        pc = self._store.characters.find(follower.character_id)
        if self._protocol is None or pc is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}",
                    subject_name=pc.name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.GM,
                    scope=Scope(world=follower.world_id),
                )
            ]
        )

    def _log_loyalty(
        self,
        follower: Follower,
        reason: str,
        roll: int | None,
        before: int | None,
        after: int,
        by: str,
        by_name: str,
    ) -> None:
        pc = self._store.characters.find(follower.character_id)
        if self._protocol is None or pc is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}",
                    subject_name=pc.name,
                    kind=EntryKind.CUSTOM,
                    what=f"loyalty:{reason}:{_num(roll)}:{_num(before)}:{after}:{follower.name}",
                    visibility=Visibility.GM,
                    scope=Scope(world=follower.world_id),
                )
            ]
        )

    def _put(self, follower: Follower) -> Follower:
        follower.touch()
        return self._store.followers.upsert(follower)


def _num(n: int | None) -> str:
    return "" if n is None else str(n)


def _name(text: str) -> str:
    return " ".join(text.split())[:120]


__all__ = [
    "CREATURES",
    "HIRELINGS",
    "LOYALTY_PRINTED",
    "LOYAL_FROM",
    "SWAYS",
    "PLAYER_FOLLOWER_CR",
    "FollowerError",
    "FollowerService",
    "Statblock",
    "hit_die",
    "level_hp",
    "proficiency",
    "sidekick_level",
    "standing",
]
