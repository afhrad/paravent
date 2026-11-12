"""Running a fight: initiative, turns, hit points and conditions (documentation/COMBAT.md).

A :class:`~app.models.gamestate.Combat` is one run of an encounter. Monsters live in the
combat record; characters stay in their own files and are changed through the same play
rules as the sheet (:class:`CharacterSide`), so a player's phone follows the tracker.

Every change goes through ``Collection.mutate``: validated, written atomically under the
file lock, so two gamemaster devices tapping at once never lose each other's change.

Rules applied to monsters (§3): temporary HP first and never stacking; damage to a
concentrating monster owes a save of DC 10 or half the damage; 0 HP is down, not dying;
a condition with a round count ends at the end of the creature's turn. Resistances are
shown, not applied — the tracker does not know a damage type.

The order is derived, never stored (§4): initiative descending, then the bonus, then
characters first, then the gamemaster's ``order``. Monsters of one ``group`` share an
initiative and a turn; ``Combat.turn`` names the group, so re-sorting keeps the turn.

A creature whose ``legendaryGroup`` has lair actions adds a **lair turn** (group
``lair``) on initiative 20, losing ties, while the fight is in its lair (``Combat.lair``).
At the start of each turn the tracker shows what is due (§4): legendary actions regained,
recharge rolls, regeneration, concentration held, or the lair's actions. Reminders, not
automation.
"""

from __future__ import annotations

import builtins
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.models.gamestate import (
    Combat,
    Combatant,
    CombatLogEntry,
    ConditionTimer,
    Encounter,
    Health,
    KnownFact,
    RepeatSave,
    health_word,
)
from app.models.protocol import EntryKind, ProtocolEntry, RollRecord, Scope
from app.services.condition_rolls import bend
from app.services.creature_sight import Sight
from app.services.dice import Advantage, Dice, is_expression
from app.services.difficulty import DifficultyRules, Rating, rules_for
from app.services.i18n import Translatable
from app.services.monster_attacks import (
    Damage,
    SaveEffect,
    against,
    critical,
    decode,
    monster_spells,
    spell_effect,
)
from app.services.naming import NamingError, creature_races
from app.services.play import Actor, PlayError
from app.services.protocol import (
    ProtocolService,
    attack_entry_id,
    from_combat,
    plain_dice,
    roll_record,
)
from app.services.protocol import verdict as attack_verdict
from app.services.sheet import CONDITIONS, damage_at, spell_view
from app.services.tokens import Token
from app.store.gamestate import GameStateStore
from app.store.projections import MonsterProjection

if TYPE_CHECKING:
    from app.models.pc import PlayerCharacter
    from app.services.characters import CharacterService
    from app.services.naming import Naming

# How many open attacks and save effects a fight keeps waiting at once.
PENDING_LIMIT = 50
MAX_ADD = 20
LABEL_LIMIT = 80
NUMBERED = re.compile(r"^(?P<base>.*?) (?P<n>\d+)$")
# A roll on the sheet this long before "Run" still counts: the table rolls when the
# gamemaster calls for it, which is often before the button is pressed.
ROLL_GRACE = timedelta(minutes=5)


def _bonus_source(member: Combatant) -> str:
    """Where a combatant's roll bonus comes from, for the log: a statblock, or the
    character's sheet (whose own parts the sheet's rolls record)."""
    return "sheet" if member.character_id is not None else "statblock"


class CombatError(Translatable):
    """A change the fight cannot take: an unknown monster, a combat already ended."""


class CombatNotFound(LookupError):
    """No combat, or no combatant, with that id."""


class CharacterSide(Protocol):
    """What the tracker needs from characters, whose state lives in their own files."""

    def name(self, character_id: str) -> str | None:
        """The character's name, or None when the file is gone."""
        ...

    def initiative_bonus(self, character_id: str) -> int:
        """The sheet's initiative modifier."""
        ...

    def is_down(self, character_id: str) -> bool:
        """True at 0 HP."""
        ...

    def conditions(self, character_id: str) -> builtins.list[str]:
        """The conditions the character is under now."""
        ...

    def act(
        self,
        character_id: str,
        action: str,
        params: dict[str, str],
        by: str,
        combat: Combat | None = None,
    ) -> None:
        """Apply a sheet action (``hp``, ``condition``, …) through the play rules; what
        it logs belongs to ``combat``."""
        ...

    def snapshot(self, character_id: str) -> CharacterSnapshot | None:
        """What the tracker's row shows, read from the sheet; None when the file is gone."""
        ...

    def defenses(self, character_id: str) -> dict[str, builtins.list[str]] | None:
        """Damage types resisted, ignored, doubled — as a statblock lists them."""
        ...

    def save_bonus(self, character_id: str, ability: str) -> int:
        """The sheet's saving throw bonus for ``ability``."""
        ...

    def has_feature(self, character_id: str, name: str) -> bool:
        """Whether the character's sheet has a feature called ``name`` (Evasion)."""
        ...

    def attack_of(self, character_id: str, subject: str) -> tuple[str, str] | None:
        """A sheet attack's damage expression and type (a weapon or an attack spell)."""
        ...

    def mastery_of(self, character_id: str, subject: str) -> tuple[str, int] | None:
        """The weapon's mastery ("Topple") and its save DC, when the character may use it."""
        ...

    def newest_roll(
        self, character_id: str, kinds: Iterable[EntryKind] | None = None
    ) -> ProtocolEntry | None:
        """The character's newest roll made on the sheet, of these kinds (the log)."""
        ...

    def spell_effect(
        self, character_id: str, reference: str, level: int = 0
    ) -> tuple[str, SaveEffect] | None:
        """A spell on the sheet that calls for a save, as a save effect, with its name;
        ``level`` is the slot it was cast with."""
        ...

    def initiative_roll(self, character_id: str, since: datetime) -> SheetRoll | None:
        """The newest initiative rolled on the sheet after ``since``, if any."""
        ...

    def award_xp(self, character_id: str, xp: int, reason: str, by: str) -> None:
        """Add experience points to the character file."""
        ...


@dataclass(frozen=True)
class SheetRoll:
    """An initiative roll from a character's log, as the tracker takes it."""

    total: int
    at: datetime
    detail: str
    by: str


@dataclass(frozen=True)
class CharacterSnapshot:
    """A character as the tracker shows it, derived from the file each time."""

    name: str
    line: str
    ac: int | None
    hp: int
    hp_max: int | None
    temp: int
    conditions: tuple[str, ...]
    concentration: str | None
    dying: bool
    death_saves: tuple[int, int]
    revision: str = ""
    level: int = 0
    # (class, level, subclass or ""): ``line``'s parts, for a page to name in its language.
    classes: tuple[tuple[str, int, str], ...] = ()

    @property
    def dead(self) -> bool:
        """Three failed death saves."""
        return self.death_saves[1] >= 3


# The lair's place in the order: initiative 20, losing ties (MM "Lair Actions").
LAIR = "lair"
LAIR_INITIATIVE = 20


@dataclass(frozen=True)
class Turn:
    """One place in the order: a character, a group of monsters acting together, or the
    lair — which has no members, only the creatures whose lair it is (``lair``)."""

    group: str
    members: tuple[Combatant, ...]
    lair: tuple[Combatant, ...] = ()

    @property
    def is_lair(self) -> bool:
        """True for the lair's turn."""
        return self.group == LAIR

    @property
    def initiative(self) -> int | None:
        """The group's shared initiative; None until rolled."""
        return LAIR_INITIATIVE if self.is_lair else self.members[0].initiative

    @property
    def is_character(self) -> bool:
        """True when this turn is a character's."""
        return not self.is_lair and self.members[0].is_character


@dataclass(frozen=True)
class Row:
    """One combatant as the tracker draws it: its numbers, wherever they live."""

    combatant: Combatant
    label: str
    ac: int | None
    hp: int | None
    hp_max: int | None
    temp: int
    conditions: tuple[str, ...]
    concentration: str | None
    health: Health
    monster: MonsterProjection | None = None
    character: CharacterSnapshot | None = None

    @property
    def down(self) -> bool:
        """At 0 HP (a character: dying)."""
        return self.health == "down"

    @property
    def dead(self) -> bool:
        """Out of the fight for good: a monster at 0 HP, a character with three failed
        death saves. A dying character is down, not dead."""
        if self.character is not None:
            return self.character.dead
        return self.combatant.is_down

    @property
    def ratio(self) -> float:
        """Hit points as a share of the maximum, for the bar."""
        if not self.hp_max or self.hp is None:
            return 0.0
        return max(0.0, min(1.0, self.hp / self.hp_max))

    @property
    def timers(self) -> dict[str, int]:
        """Rounds left per timed condition."""
        return {t.condition: t.rounds for t in self.combatant.timers}


@dataclass(frozen=True)
class TurnView:
    """A place in the order with its rows, and whether it is acting now."""

    turn: Turn
    rows: tuple[Row, ...]
    active: bool


@dataclass(frozen=True)
class Recharge:
    """An ability that comes back on a d6 (``{@recharge 5}``: on 5–6)."""

    combatant_id: str
    label: str
    ability: str
    at: int


@dataclass(frozen=True)
class Regeneration:
    """Hit points a creature regains at the start of its turn (a troll's 10)."""

    combatant_id: str
    label: str
    amount: int


@dataclass(frozen=True)
class Reminders:
    """What is due at the start of the turn acting now (§4)."""

    # (combatant id, label, legendary actions regained)
    legendary: tuple[tuple[str, str, int], ...] = ()
    recharge: tuple[Recharge, ...] = ()
    regeneration: tuple[Regeneration, ...] = ()
    concentration: tuple[tuple[str, str], ...] = ()
    # The lair's turn: each group's name and its lair actions (5eTools entries).
    lair: tuple[tuple[str, tuple[Any, ...]], ...] = ()

    @property
    def empty(self) -> bool:
        """True when nothing is due."""
        return not (
            self.legendary or self.recharge or self.regeneration or self.concentration or self.lair
        )


@dataclass(frozen=True)
class CombatView:
    """Everything the tracker draws."""

    combat: Combat
    turns: tuple[TurnView, ...]
    # A creature here has lair actions (whether or not they are on: ``combat.lair``).
    lair_available: bool = False
    reminders: Reminders = Reminders()
    # How hard the fight still is: those standing on each side (§3, friend and foe).
    rating: Rating | None = None
    # Each combatant's round token, by id (documentation/TOKENS.md).
    tokens: Mapping[str, Token] = field(default_factory=dict)

    @property
    def current(self) -> TurnView | None:
        """The turn acting now."""
        return next((t for t in self.turns if t.active), None)

    @property
    def waiting(self) -> tuple[Row, ...]:
        """Rows whose initiative is not rolled yet."""
        return tuple(r for t in self.turns for r in t.rows if r.combatant.initiative is None)

    def row(self, combatant_id: str) -> Row | None:
        """The row of one combatant."""
        return next((r for t in self.turns for r in t.rows if r.combatant.id == combatant_id), None)


@dataclass(frozen=True)
class SeenCombatant:
    """A combatant as a player sees it: a name, a state word and its conditions.

    The player view is built from these and nothing else (COMBAT.md §5), so a number
    added to the tracker never reaches a player's phone by accident. The one number is
    ``fill``, the share of hit points left in percent — for the characters' side only;
    a foe shows its state word alone.
    """

    label: str
    health: Health
    conditions: tuple[str, ...]
    character_id: str | None
    # The creature's English name, so a screen can show it in the reader's language.
    monster: str | None = None
    # On the characters' side (a character or an ally).
    friend: bool = False
    # Hit points left in percent, for friends with a known maximum; None for foes.
    fill: int | None = None
    # The combatant's id, so a player can pick it as a target.
    id: str = ""
    # Its round token (documentation/TOKENS.md).
    token: Token | None = None


@dataclass(frozen=True)
class SeenTurn:
    """A place in the order as a player sees it."""

    members: tuple[SeenCombatant, ...]
    active: bool

    @property
    def character_ids(self) -> frozenset[str]:
        """The characters acting on this turn."""
        return frozenset(m.character_id for m in self.members if m.character_id)


@dataclass(frozen=True)
class SeenDetails:
    """One combatant's Details as a player sees it: the row, and for a creature with a
    statblock what anyone sees by looking (COMBAT.md §5)."""

    member: SeenCombatant
    sight: Sight | None = None
    # What the party has learned of its kind: fact id → fact (knowledge.py).
    known: Mapping[str, KnownFact] = field(default_factory=dict)


@dataclass(frozen=True)
class SaveCall:
    """A save effect as a player sees it: which save, against what — no DC."""

    at: str
    ability: str
    name: str
    source: str
    # Character id → the total that character's player rolled.
    rolled: Mapping[str, int]


@dataclass(frozen=True)
class PlayerView:
    """A fight as the players see it: the round, the order and whose turn it is."""

    id: str
    name: str
    world_id: str | None
    round: int
    running: bool
    revision: str
    turns: tuple[SeenTurn, ...]
    # The characters whose initiative is not rolled yet.
    waiting: frozenset[str] = frozenset()
    # Save effects waiting for targets: the players in range roll their own save.
    saves: tuple[SaveCall, ...] = ()
    # Conditions a character on the turn now may shake off: (character id, condition,
    # ability) — the player rolls it.
    repeats: tuple[tuple[str, str, str], ...] = ()
    # Character id → the combatant its player chose as target (only ones players see).
    targets: Mapping[str, str] = field(default_factory=dict)

    @property
    def current(self) -> SeenTurn | None:
        """The turn acting now."""
        return next((t for t in self.turns if t.active), None)

    def acting(self, character_ids: Iterable[str]) -> bool:
        """True when one of these characters has the turn."""
        current = self.current
        return current is not None and not current.character_ids.isdisjoint(character_ids)


@dataclass(frozen=True)
class Summary:
    """What a finished (or running) fight amounts to (§6)."""

    rounds: int
    fallen: tuple[Combatant, ...]
    xp: int
    # The defeated as (label, XP), in the order they stood.
    defeated: tuple[tuple[str, int], ...] = ()
    # The characters who took part, and those of them down at the end.
    party: tuple[str, ...] = ()
    party_down: tuple[str, ...] = ()

    @property
    def characters(self) -> int:
        """How many characters took part."""
        return len(self.party)

    @property
    def share(self) -> int:
        """XP per character who took part, rounded down."""
        return self.xp // self.characters if self.characters else 0


_RECHARGE = re.compile(r"\s*\{@recharge(?:\s+(\d))?\}")
_REGENERATES = re.compile(r"regains (\d+) hit points at the start of its turn")


def _regeneration(data: Mapping[str, Any]) -> int:
    """The hit points a Regeneration trait restores at the start of the turn, or 0."""
    for trait in data.get("trait") or []:
        if not isinstance(trait, Mapping) or not str(trait.get("name", "")).startswith(
            "Regeneration"
        ):
            continue
        text = " ".join(e for e in trait.get("entries") or [] if isinstance(e, str))
        found = _REGENERATES.search(text)
        if found:
            return int(found[1])
    return 0


def proficiency_bonus(cr: Fraction | None) -> int:
    """A monster's proficiency bonus from its CR: +2 up to CR 4, +1 per four after."""
    if cr is None or cr < 1:
        return 2
    return 2 + (int(cr) - 1) // 4


def _modifier(score: int | None) -> int:
    return (score - 10) // 2 if score is not None else 0


def _save_bonus(raw: Any, fallback: int) -> int:
    try:
        return int(str(raw).replace(" ", ""))
    except (TypeError, ValueError):
        return fallback


def monster_initiative(monster: MonsterProjection) -> tuple[int, Advantage]:
    """Initiative bonus and advantage: DEX, plus proficiency for the 2024 statblocks.

    The 2024 Monster Manual states initiative as a multiple of proficiency
    (``{"proficiency": 1}``) or as advantage (``{"advantageMode": "adv"}``); a plain
    number replaces the bonus.
    """
    entry = monster.entry
    bonus = _modifier(getattr(entry, "dex", None))
    raw = getattr(entry, "initiative", None)
    advantage = Advantage.NORMAL
    if isinstance(raw, int):
        bonus = raw
    elif isinstance(raw, dict):
        proficiency = raw.get("proficiency")
        if isinstance(proficiency, int):
            bonus += proficiency * proficiency_bonus(monster.cr.value if monster.cr else None)
        if raw.get("advantageMode") == "adv":
            advantage = Advantage.ADVANTAGE
    return bonus, advantage


def concentration_bonus(monster: MonsterProjection | None) -> int:
    """The bonus to a monster's Constitution save."""
    if monster is None:
        return 0
    entry = monster.entry
    fallback = _modifier(getattr(entry, "con", None))
    saves = getattr(entry, "save", None) or {}
    return _save_bonus(saves.get("con"), fallback) if "con" in saves else fallback


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_label(label: str) -> str:
    cleaned = " ".join(label.split())[:LABEL_LIMIT]
    if not cleaned:
        raise CombatError("combat.error.label")
    return cleaned


def _numbered(base: str, taken: builtins.list[str], count: int) -> builtins.list[str]:
    """Labels for ``count`` more of ``base``: "Goblin" alone, else "Goblin 1", "Goblin 2"…

    Numbering continues after the highest already in the fight, and a lone earlier
    "Goblin" is left as it is.
    """
    highest = 0
    plain = False
    for label in taken:
        if label == base:
            plain = True
        match = NUMBERED.match(label)
        if match and match["base"] == base:
            highest = max(highest, int(match["n"]))
    if count == 1 and not plain and not highest:
        return [base]
    start = max(highest, 1 if plain else 0)
    return [f"{base} {start + i + 1}" for i in range(count)]


class SheetCharacters:
    """:class:`CharacterSide` over the character service: files, sheets, play rules."""

    def __init__(self, characters: CharacterService) -> None:
        """Bind to the character service the sheets use."""
        self._characters = characters

    def _find(self, character_id: str) -> PlayerCharacter | None:
        try:
            return self._characters.get_any(character_id)
        except LookupError:
            return None

    def name(self, character_id: str) -> str | None:
        """The character's name, or None when the file is gone."""
        pc = self._find(character_id)
        return None if pc is None else (pc.name or "?")

    def initiative_bonus(self, character_id: str) -> int:
        """The sheet's initiative modifier."""
        pc = self._find(character_id)
        return 0 if pc is None else (self._characters.sheet(pc).initiative.value or 0)

    def is_down(self, character_id: str) -> bool:
        """True at 0 HP."""
        pc = self._find(character_id)
        return pc is not None and self._characters.sheet(pc).is_dying

    def conditions(self, character_id: str) -> builtins.list[str]:
        """The conditions in the character file."""
        pc = self._find(character_id)
        return [] if pc is None else list(pc.state.conditions)

    def act(
        self,
        character_id: str,
        action: str,
        params: dict[str, str],
        by: str,
        combat: Combat | None = None,
    ) -> None:
        """A sheet action through the play rules, named in the log as ``by``."""
        pc = self._find(character_id)
        if pc is None:
            raise CombatError("combat.error.character")
        try:
            self._characters.play.act(
                character_id,
                action,
                params,
                self._characters.sheet(pc),
                Actor(
                    by,
                    via_combat=True,
                    combat=combat.id if combat is not None else None,
                    round=(combat.round or None) if combat is not None else None,
                    world=combat.world_id if combat is not None else None,
                ),
            )
        except PlayError as exc:
            raise CombatError(exc.key, **exc.params) from exc

    def attack_of(self, character_id: str, subject: str) -> tuple[str, str] | None:
        """The weapon's (or attack spell's) damage on the sheet, and its type."""
        pc = self._find(character_id)
        if pc is None:
            return None
        sheet = self._characters.sheet(pc)
        weapon = next((a for a in sheet.attacks if a.name == subject), None)
        if weapon is not None:
            return weapon.damage.replace(" ", ""), weapon.damage_type.lower()
        spell = sheet.spell(subject)
        if spell is not None and spell.damage:
            kind = spell.damage_types[0].lower() if spell.damage_types else ""
            return spell.damage.replace(" ", ""), kind
        return None

    def mastery_of(self, character_id: str, subject: str) -> tuple[str, int] | None:
        """The weapon's mastery and DC, for a character who uses it (Weapon Mastery, or the
        kind Weapon Master chose)."""
        pc = self._find(character_id)
        if pc is None:
            return None
        sheet = self._characters.sheet(pc)
        weapon = next((a for a in sheet.attacks if a.name == subject), None)
        if weapon is None or not sheet.masters(weapon):
            return None
        return weapon.mastery.partition("|")[0], weapon.mastery_dc

    def newest_roll(
        self, character_id: str, kinds: Iterable[EntryKind] | None = None
    ) -> ProtocolEntry | None:
        """The character's newest roll made on the sheet, of these kinds, in the log."""
        pc = self._find(character_id)
        return None if pc is None else self._characters.newest_roll(pc, kinds)

    def spell_effect(
        self, character_id: str, reference: str, level: int = 0
    ) -> tuple[str, SaveEffect] | None:
        """The sheet's spell with its save, the caster's DC, damage (at the slot it was
        cast with) and condition."""
        pc = self._find(character_id)
        if pc is None:
            return None
        sheet = self._characters.sheet(pc)
        spell = sheet.spell(reference)
        found = sheet.save_dc_for(spell) if spell is not None else None
        dc = found.value if found is not None else None
        if spell is None or dc is None:
            return None
        damage = damage_at(spell, level) if level else spell.damage
        effect = spell_effect(spell.save, damage, spell.damage_types, spell.entries, dc)
        return (spell.name, effect) if effect is not None else None

    def has_feature(self, character_id: str, name: str) -> bool:
        """Whether the sheet lists a feature of that name."""
        pc = self._find(character_id)
        return pc is not None and self._characters.sheet(pc).has_feature(name) is not None

    def save_bonus(self, character_id: str, ability: str) -> int:
        """The sheet's saving throw bonus; 0 when the file is gone."""
        pc = self._find(character_id)
        if pc is None:
            return 0
        found = next((a for a in self._characters.sheet(pc).abilities if a.key == ability), None)
        return (found.save.value or 0) if found is not None else 0

    def defenses(self, character_id: str) -> dict[str, builtins.list[str]] | None:
        """The sheet's resistances, immunities and vulnerabilities."""
        pc = self._find(character_id)
        if pc is None:
            return None
        sheet = self._characters.sheet(pc)
        return {kind: sheet.defended(kind) for kind in ("resist", "immune", "vulnerable")}

    def snapshot(self, character_id: str) -> CharacterSnapshot | None:
        """The row's numbers from the derived sheet."""
        pc = self._find(character_id)
        if pc is None:
            return None
        sheet = self._characters.sheet(pc)
        saves = pc.state.death_saves
        return CharacterSnapshot(
            name=pc.name or "?",
            line=sheet.class_line,
            ac=sheet.armor_class.value,
            hp=sheet.hp_current,
            hp_max=sheet.hp_max.value,
            temp=sheet.temp_hp,
            conditions=tuple(c.key for c in sheet.conditions if c.active),
            concentration=sheet.concentration.name if sheet.concentration else None,
            dying=sheet.is_dying,
            death_saves=(saves.successes, saves.failures),
            revision=sheet.revision or "",
            level=pc.total_level,
            classes=tuple(
                (c.name, c.level, c.subclass.name if c.subclass is not None else "")
                for c in pc.classes
            ),
        )

    def award_xp(self, character_id: str, xp: int, reason: str, by: str) -> None:
        """Add experience points to the character file (and its log)."""
        if self._find(character_id) is None:
            return
        self._characters.award_xp(character_id, xp, reason, by)

    def initiative_roll(self, character_id: str, since: datetime) -> SheetRoll | None:
        """The newest initiative rolled on the character's sheet after ``since``."""
        pc = self._find(character_id)
        if pc is None:
            return None
        newest = self._characters.newest_roll(pc, (EntryKind.INITIATIVE,), since=since)
        if newest is None or newest.roll is None:
            return None
        return SheetRoll(newest.roll.total, newest.at, plain_dice(newest.roll), newest.actor_name)


class CombatService:
    """Combats in the library: started from encounters, run turn by turn."""

    def __init__(
        self,
        store: GameStateStore,
        monster: Callable[[str, str], MonsterProjection | None],
        characters: CharacterSide,
        dice: Dice | None = None,
        changed: Callable[[Combat | None, str], None] | None = None,
        legendary_group: Callable[[Any], dict[str, Any] | None] | None = None,
        rules: DifficultyRules | None = None,
        protocol: ProtocolService | None = None,
        sight: Callable[[MonsterProjection, str], Sight] | None = None,
        known: Callable[[str | None, str], Mapping[str, KnownFact]] | None = None,
        spell: Callable[[str, str | None, str | None], dict[str, Any] | None] | None = None,
        token: Callable[[Combatant, str | None], Token] | None = None,
    ) -> None:
        """``monster(name, source)`` looks up a creature exactly, as the encounters do;
        ``token(combatant, world)`` its round token (documentation/TOKENS.md).

        ``legendary_group(pointer)`` finds the lair a creature's ``legendaryGroup``
        points at; without it there are no lair turns.

        ``changed(combat, combat_id)`` is told after every write (``combat`` is None
        once deleted), so open screens can follow. ``protocol`` is the log every line
        of the fight's own log is appended to as well (documentation/PROTOCOL.md).
        ``sight(monster, lang)`` is what anyone sees of a creature, for the players' Details;
        ``known(world, ref)`` what the world's party learned of its kind;
        ``spell(name, source, world)`` a spell's record in the world's books.
        """
        self._store = store
        self._token = token
        self._sight = sight
        self._known = known
        self._spell = spell
        self._monster = monster
        self._characters = characters
        self._dice = dice or Dice()
        self._changed = changed
        self._legendary_group = legendary_group
        self._rules = rules or DifficultyRules()
        self._groups: dict[str, dict[str, Any] | None] = {}
        # Set while this thread writes a combat: a character change it causes (the
        # tracker's own damage) is not a roll to take, and must not write again.
        self._writing = threading.local()
        self._protocol = protocol
        # The log entries of the change this thread is applying, appended once stored.
        self._staged = threading.local()

    # -- reading -----------------------------------------------------------------------

    def list(
        self, *, world_id: str | None = None, encounter_id: str | None = None
    ) -> builtins.list[Combat]:
        """Combats, newest first, optionally of a world or an encounter."""
        found = self._store.combats.list(
            lambda c: (
                (world_id is None or c.world_id == world_id)
                and (encounter_id is None or c.encounter_id == encounter_id)
            )
        )
        return sorted(found, key=lambda c: c.created_at, reverse=True)

    def running(self, *, world_id: str | None = None) -> builtins.list[Combat]:
        """The combats not yet ended."""
        return [c for c in self.list(world_id=world_id) if c.running]

    def get(self, combat_id: str) -> Combat:
        """One combat, or :class:`CombatNotFound`."""
        combat = self._store.combats.find(combat_id)
        if combat is None:
            raise CombatNotFound(combat_id)
        return combat

    def monster_of(self, combatant: Combatant) -> MonsterProjection | None:
        """The statblock behind a monster combatant."""
        if combatant.ref is None or "|" not in combatant.ref:
            return None
        name, source = combatant.ref.split("|", 1)
        return self._monster(name, source)

    def token_of(self, combatant: Combatant, world_id: str | None) -> Token | None:
        """A combatant's round token (documentation/TOKENS.md), None without a source."""
        return self._token(combatant, world_id) if self._token else None

    def label_of(self, combatant: Combatant) -> str:
        """A character's current name from its file; anyone else's label."""
        if combatant.character_id is not None:
            return self._characters.name(combatant.character_id) or combatant.label
        return combatant.label

    def is_down(self, combatant: Combatant) -> bool:
        """Down, reading a character's hit points from its file."""
        if combatant.character_id is not None:
            return self._characters.is_down(combatant.character_id)
        return combatant.is_down

    def order(self, combat: Combat) -> builtins.list[Turn]:
        """The turn order (§4): initiative, bonus, characters first, the manual order."""
        groups: dict[str, builtins.list[Combatant]] = {}
        for combatant in combat.combatants:
            groups.setdefault(combatant.group, []).append(combatant)

        def key(members: builtins.list[Combatant]) -> tuple[Any, ...]:
            lead = members[0]
            unrolled = lead.initiative is None
            return (
                unrolled,
                -(lead.initiative or 0),
                -lead.initiative_bonus,
                not lead.is_character,
                min(m.order for m in members),
                self.label_of(lead).casefold(),
            )

        turns = []
        for members in sorted(groups.values(), key=key):
            members.sort(key=lambda m: (self.is_down(m), _natural(m.label)))
            turns.append(Turn(members[0].group, tuple(members)))
        lair = self.lair_creatures(combat)
        if lair and combat.lair:
            # Initiative 20, losing ties: after everyone on 20 or more, before the rest.
            at = next(
                (
                    i
                    for i, t in enumerate(turns)
                    if t.initiative is None or t.initiative < LAIR_INITIATIVE
                ),
                len(turns),
            )
            turns.insert(at, Turn(LAIR, (), tuple(lair)))
        return turns

    def lair_group(self, combatant: Combatant) -> dict[str, Any] | None:
        """The legendary group of a monster, when it has lair actions."""
        monster = self.monster_of(combatant)
        if monster is None or self._legendary_group is None:
            return None
        key = f"{monster.name}|{monster.source}".lower()
        if key not in self._groups:
            pointer = getattr(monster.entry, "legendaryGroup", None)
            group = self._legendary_group(pointer) if pointer else None
            self._groups[key] = group if group and group.get("lairActions") else None
        return self._groups[key]

    def lair_creatures(self, combat: Combat) -> builtins.list[Combatant]:
        """The monsters in the fight whose lair has lair actions."""
        return [
            c for c in combat.combatants if not c.is_character and self.lair_group(c) is not None
        ]

    def current(self, combat: Combat) -> Turn | None:
        """Whose turn it is; None before round 1 or in an empty fight."""
        if combat.round == 0 or combat.turn is None:
            return None
        return next((t for t in self.order(combat) if t.group == combat.turn), None)

    def reminders(self, combat: Combat) -> Reminders:
        """What is due at the start of the turn acting now (§4): reminders only."""
        turn = self.current(combat)
        if turn is None:
            return Reminders()
        if turn.is_lair:
            lair = []
            seen: set[str] = set()
            for member in turn.lair:
                group = self.lair_group(member)
                if group is None or self.is_down(member):
                    continue
                name = str(group.get("name", ""))
                if name not in seen:
                    seen.add(name)
                    lair.append((name, tuple(group.get("lairActions") or ())))
            return Reminders(lair=tuple(lair))
        legendary, recharge, regeneration, concentration = [], [], [], []
        for member in turn.members:
            label = self.label_of(member)
            row = self.row(member)
            if row.concentration:
                concentration.append((label, row.concentration))
            monster = self.monster_of(member)
            if monster is None or member.is_character or self.is_down(member):
                continue
            data = self._monster_data(monster)
            count = data.get("legendaryActions") or (3 if data.get("legendary") else 0)
            if count:
                legendary.append((member.id, label, int(count)))
            for block in ("action", "bonus", "reaction"):
                for action in data.get(block) or []:
                    name = str(action.get("name", "")) if isinstance(action, Mapping) else ""
                    found = _RECHARGE.search(name)
                    if found:
                        ability = _RECHARGE.sub("", name).strip()
                        recharge.append(Recharge(member.id, label, ability, int(found[1] or 6)))
            amount = _regeneration(data)
            if amount and (row.hp is None or row.hp_max is None or row.hp < row.hp_max):
                regeneration.append(Regeneration(member.id, label, amount))
        return Reminders(
            tuple(legendary), tuple(recharge), tuple(regeneration), tuple(concentration)
        )

    def _monster_data(self, monster: MonsterProjection) -> dict[str, Any]:
        data: dict[str, Any] = monster.entry.model_dump(by_alias=True, exclude_none=True)
        return data

    def cast(self, combat_id: str, combatant_id: str, spell: str, by: str) -> Combat:
        """A creature casts one of its statblock's spells: a line in the log for the table
        to see (and the party to learn), then the spell itself where it can be rolled —
        a save spell waits for its targets as a save effect (the block's DC, its damage
        at the slot it is cast with), an attack spell rolls to hit with the block's bonus
        and waits for Hit or Miss. Anything else is the gamemaster's to apply.

        A spell cast from a slot or a daily use spends it; with none left it is refused."""
        caster = self._member(self.get(combat_id), combatant_id)
        monster = self.monster_of(caster)
        spells = monster_spells(self._monster_data(monster)) if monster is not None else []
        chosen = next((s for s in spells if s.name == spell), None)
        if chosen is None:
            raise CombatError("combat.error.bad_input")

        def apply(combat: Combat) -> None:
            member = self._member(combat, combatant_id)
            if chosen.pool is not None and chosen.uses is not None:
                used = member.spent.get(chosen.pool, 0)
                if used >= chosen.uses:
                    raise CombatError("combat.error.no_uses")
                member.spent = {**member.spent, chosen.pool: used + 1}
            self._log(combat, "cast", by, subject=self.label_of(member), detail=spell)

        result = self._mutate(combat_id, apply)
        data = (
            self._spell(chosen.tag, chosen.source, result.world_id)
            if self._spell is not None
            else None
        )
        if data is None:
            return result
        view = spell_view(data, chosen.caster_level)
        slot = chosen.slot or view.level
        damage = damage_at(view, slot) if slot else view.damage
        if view.save and chosen.dc is not None:
            effect = spell_effect(view.save, damage, view.damage_types, view.entries, chosen.dc)
            if effect is not None:
                return self.save_effect(combat_id, combatant_id, spell, effect, by)
        if view.attack and chosen.hit is not None and damage:
            kind = view.damage_types[0].lower() if view.damage_types else ""
            rolls = (Damage(damage.replace(" ", ""), kind),)
            return self.attack(combat_id, combatant_id, spell, chosen.hit, rolls, by)
        return result

    def restore_spells(self, combat_id: str, combatant_id: str, by: str) -> Combat:
        """Every slot and daily use the creature spent comes back (a mistaken cast)."""

        def apply(combat: Combat) -> None:
            self._member(combat, combatant_id).spent = {}

        return self._mutate(combat_id, apply)

    def roll_recharge(
        self, combat_id: str, combatant_id: str, ability: str, at: int, by: str
    ) -> Combat:
        """Roll a d6 for an ability's recharge, and log whether it came back."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            roll = self._dice.die(6)
            self._log(
                combat,
                "recharge",
                by,
                subject=self.label_of(target),
                amount=roll,
                detail=ability,
                outcome="success" if roll >= max(1, min(6, at)) else "failure",
            )

        return self._mutate(combat_id, apply)

    def attack(
        self,
        combat_id: str,
        combatant_id: str,
        name: str,
        hit: int,
        damage: Sequence[Damage],
        by: str,
        advantage: Advantage = Advantage.NORMAL,
    ) -> Combat:
        """Roll a statblock attack: d20 to hit, then its damage — dice doubled on a
        natural 20, none on a natural 1 — logged as one entry (app/services/monster_attacks.py)."""

        def apply(combat: Combat) -> None:
            attacker = self._member(combat, combatant_id)
            bent = bend(advantage, self._conditions(attacker), "attack")
            to_hit = self._dice.d20(hit, bent.advantage)
            parts = [to_hit.detail + (f" [{', '.join(bent.reasons)}]" if bent.reasons else "")]
            total: int | None = None
            by_type: dict[str, int] = {}
            damage_rolls: builtins.list[RollRecord] = []
            if to_hit.kind != "fumble" and damage:
                total = 0
                for part in damage:
                    expression = (
                        critical(part.expression) if to_hit.kind == "crit" else part.expression
                    )
                    rolled = self._dice.roll(expression)
                    damage_rolls.append(roll_record(rolled, rest="modifier"))
                    total += rolled.total
                    by_type[part.kind] = by_type.get(part.kind, 0) + max(0, rolled.total)
                    parts.append(f"{rolled.detail} {part.kind}".strip())
            natural: Literal["", "crit", "fumble"] = ""
            if to_hit.kind == "crit":
                natural = "crit"
            elif to_hit.kind == "fumble":
                natural = "fumble"
            self._log(
                combat,
                "attack",
                by,
                subject=self.label_of(attacker),
                amount=to_hit.total,
                detail=name.strip()[:80],
                outcome=natural,
                damage=max(0, total) if total is not None else None,
                dice=" · ".join(parts),
                damage_types=by_type,
                actor=attacker.id,
                damage_dice=";".join(f"{d.expression}:{d.kind}" for d in damage),
                roll=roll_record(
                    to_hit,
                    advantage=bent.advantage,
                    chosen=advantage,
                    reasons=bent.reasons,
                    rest=_bonus_source(attacker),
                ),
                damage_rolls=damage_rolls,
            )

        return self._mutate(combat_id, apply)

    def save_effect(
        self,
        combat_id: str,
        combatant_id: str,
        name: str,
        effect: SaveEffect,
        by: str,
    ) -> Combat:
        """Roll a save effect's damage once (a breath weapon); each target's save is
        settled when it is resolved (:meth:`resolve_save`)."""

        def apply(combat: Combat) -> None:
            source = self._member(combat, combatant_id)
            parts: builtins.list[str] = []
            by_type: dict[str, int] = {}
            damage_rolls: builtins.list[RollRecord] = []
            for part in effect.damage:
                rolled = self._dice.roll(part.expression)
                damage_rolls.append(roll_record(rolled, rest="modifier"))
                by_type[part.kind] = by_type.get(part.kind, 0) + max(0, rolled.total)
                parts.append(f"{rolled.detail} {part.kind}".strip())
            self._log(
                combat,
                "save_effect",
                by,
                subject=self.label_of(source),
                amount=effect.dc,
                detail=name.strip()[:80],
                damage=sum(by_type.values()),
                dice=" · ".join(parts),
                damage_types=by_type,
                actor=source.id,
                save_ability=effect.ability,
                half=effect.half,
                condition=effect.condition,
                rounds=effect.rounds,
                repeat=effect.repeat,
                damage_rolls=damage_rolls,
            )

        return self._mutate(combat_id, apply)

    def resolve_save(self, combat_id: str, at: str, verdicts: Mapping[str, str], by: str) -> Combat:
        """Settle a save effect for each target named: ``fail``, ``save``, or ``roll``
        (the target's save rolled here). Full damage on a failure, half or none on a
        success, the target's defences counted; one log line per target."""
        combat = self.get(combat_id)
        entry = next(
            (
                e
                for e in pending_attacks(combat)
                if e.kind == "save_effect" and e.at.isoformat() == at
            ),
            None,
        )
        if entry is None:
            raise CombatError("combat.error.attack_gone")
        chosen = {k: v for k, v in verdicts.items() if v in ("fail", "save", "roll")}
        if not chosen:
            raise CombatError("combat.error.no_targets")
        ability, dc = entry.save_ability, entry.amount or 0
        types = entry.damage_types or {"": entry.damage or 0}
        for target_id, verdict in chosen.items():
            target = self._member(combat, target_id)
            total: int | None = None
            dice = ""
            record: RollRecord | None = None
            if verdict == "roll" and target_id in entry.rolls:
                # The player rolled it on their own phone.
                total = entry.rolls[target_id]
                success = total >= dc
            elif verdict == "roll":
                rolled = self._dice.d20(self._save_bonus(target, ability))
                total, success = rolled.total, rolled.total >= dc
                dice = rolled.detail
                record = roll_record(rolled, rest=_bonus_source(target))
            else:
                success = verdict == "save"
                total = entry.rolls.get(target_id)  # shown when the player rolled one
            halved = {k: v // 2 for k, v in types.items()}
            if ability == "dex" and entry.half and self._evades(target):
                # Evasion: nothing on a success, half on a failure.
                taken = {} if success else halved
            elif success:
                taken = halved if entry.half else {}
            else:
                taken = dict(types)
            amount = against(taken, self._defenses_of(target)) if taken else 0
            label = self.label_of(target)

            def note(
                combat: Combat,
                label: str = label,
                total: int | None = total,
                success: bool = success,
                amount: int = amount,
                dice: str = dice,
                record: RollRecord | None = record,
            ) -> None:
                self._log(
                    combat,
                    "save_damage",
                    by,
                    subject=label,
                    amount=total,
                    detail=ability,
                    outcome="success" if success else "failure",
                    dice=dice,
                    damage=amount,
                    roll=record,
                    refers_to=attack_entry_id(combat.id, entry.at),
                    against=dc,
                )

            # The save first, then what it caused: the damage, the condition (or that the
            # target is immune to it), in the order the table sees them.
            self._mutate(combat_id, note)
            if amount:
                self.damage(combat_id, target_id, amount, by)
            if not success and entry.condition:
                self._impose(combat_id, target_id, entry, by)

        def mark(combat: Combat) -> None:
            # Settled: it waits no longer (what happened to each target is in the log).
            combat.pending = [
                e for e in combat.pending if not (e.kind == "save_effect" and e.at == entry.at)
            ]

        return self._mutate(combat_id, mark)

    def _save_bonus(self, target: Combatant, ability: str) -> int:
        """A monster's statblock save (or ability modifier); a character's sheet save."""
        if target.character_id is not None:
            return self._characters.save_bonus(target.character_id, ability)
        monster = self.monster_of(target)
        if monster is None:
            return 0
        data = self._monster_data(monster)
        listed = (data.get("save") or {}).get(ability)
        if listed is not None:
            try:
                return int(str(listed).replace("+", "").strip())
            except ValueError:
                pass
        return (int(data.get(ability, 10)) - 10) // 2

    def _impose(self, combat_id: str, combatant_id: str, entry: CombatLogEntry, by: str) -> None:
        """Put a save effect's condition on, unless the target has it or its statblock is
        immune; one it may repeat its save against is remembered on its row."""
        condition, rounds = entry.condition, entry.rounds
        target = self._member(self.get(combat_id), combatant_id)
        if condition in self._conditions(target):
            return
        monster = self.monster_of(target)
        if monster is not None:
            immune = self._monster_data(monster).get("conditionImmune") or []
            if condition in {str(c).lower() for c in immune if isinstance(c, str)}:
                # Seen at the table: the condition did not take.
                self._mutate(
                    combat_id,
                    lambda combat: self._log(
                        combat,
                        "condition_immune",
                        by,
                        subject=self.label_of(self._member(combat, combatant_id)),
                        detail=condition,
                    ),
                )
                return
        self.toggle_condition(combat_id, combatant_id, condition, by, rounds=rounds)
        if entry.repeat in ("end", "start"):

            def remember(combat: Combat) -> None:
                row = self._member(combat, combatant_id)
                row.repeat_saves = [
                    *(r for r in row.repeat_saves if r.condition != condition),
                    RepeatSave(
                        condition=condition,
                        ability=entry.save_ability,
                        dc=entry.amount or 10,
                        when="start" if entry.repeat == "start" else "end",
                        source=entry.detail,
                    ),
                ]

            self._mutate(combat_id, remember)

    def repeat_save(
        self,
        combat_id: str,
        combatant_id: str,
        condition: str,
        by: str,
        total: int | None = None,
    ) -> bool:
        """Roll a repeated save (or take ``total``, a player's own roll): a success ends
        the condition, a failure keeps it until the next turn. Returns the success."""
        combat = self.get(combat_id)
        target = self._member(combat, combatant_id)
        due = next((r for r in target.repeat_saves if r.condition == condition), None)
        if due is None or due.rolled_round == combat.round:
            raise CombatError("combat.error.no_repeat")
        dice = ""
        record: RollRecord | None = None
        if total is None:
            rolled = self._dice.d20(self._save_bonus(target, due.ability))
            total, dice = rolled.total, rolled.detail
            record = roll_record(rolled, rest=_bonus_source(target))
        success = total >= due.dc

        def settle(combat: Combat) -> None:
            row = self._member(combat, combatant_id)
            if success:
                self._drop_condition(row, condition, by)
            else:
                row.repeat_saves = [
                    r.model_copy(update={"rolled_round": combat.round})
                    if r.condition == condition
                    else r
                    for r in row.repeat_saves
                ]
            self._log(
                combat,
                "repeat_save",
                by,
                subject=self.label_of(row),
                amount=total,
                detail=condition,
                outcome="success" if success else "failure",
                dice=dice,
                save_ability=due.ability,
                roll=record,
                against=due.dc,
            )

        self._mutate(combat_id, settle)
        return success

    def _evades(self, target: Combatant) -> bool:
        """Evasion: a rogue's or monk's feature, or a creature's trait of that name."""
        if target.character_id is not None:
            return self._characters.has_feature(target.character_id, "Evasion")
        monster = self.monster_of(target)
        traits = self._monster_data(monster).get("trait") or [] if monster is not None else []
        return any(
            isinstance(trait, dict) and str(trait.get("name", "")).lower() == "evasion"
            for trait in traits
        )

    def _defenses_of(self, target: Combatant) -> Mapping[str, Any] | None:
        monster = self.monster_of(target)
        if monster is not None:
            return self._monster_data(monster)
        if target.character_id is not None:
            return self._characters.defenses(target.character_id)
        return None

    def resolve_attack(
        self,
        combat_id: str,
        at: str,
        target_id: str | None,
        by: str,
        *,
        hit: bool = True,
        crit: bool = False,
    ) -> Combat:
        """A rolled attack hit ``target_id`` — its damage applied, the target's
        immunities, resistances and vulnerabilities counted — or missed it (``hit`` false,
        or no target). A player's weapon mastery follows (Topple's save, Graze on a miss)."""
        combat = self.get(combat_id)
        entry = next(
            (e for e in pending_attacks(combat) if e.kind == "attack" and e.at.isoformat() == at),
            None,
        )
        if entry is None:
            raise CombatError("combat.error.attack_gone")
        hit_target: Combatant | None = None
        taken: int | None = None
        taken_types: dict[str, int] = {}
        extra: dict[str, int] = {}
        if crit and hit and entry.outcome != "crit" and entry.damage_dice:
            # Made a critical at the table (a paralyzed target in reach): the damage dice
            # are rolled once more and added, the modifiers not.
            for part in decode(entry.damage_dice):
                dice_only = _DICE_ONLY.findall(part.expression)
                if dice_only:
                    rolled = self._dice.roll("+".join(dice_only))
                    extra[part.kind] = extra.get(part.kind, 0) + rolled.total
        if target_id is not None and hit:
            target = self._member(combat, target_id)
            data = self._defenses_of(target)
            types = dict(entry.damage_types or {"": entry.damage or 0})
            for kind, amount in extra.items():
                types[kind] = types.get(kind, 0) + amount
            amount = against(types, data)
            if amount:
                self.damage(combat_id, target_id, amount, by)
            hit_target, taken, taken_types = target, amount, types

        def mark(combat: Combat) -> None:
            self._stage(
                attack_verdict(
                    combat,
                    entry,
                    hit_target,
                    hit=hit_target is not None,
                    amount=taken,
                    types=taken_types,
                    by=by,
                )
            )
            # Resolved: it waits no longer; the verdict above carries what landed.
            combat.pending = [
                e for e in combat.pending if not (e.kind == "attack" and e.at == entry.at)
            ]

        result = self._mutate(combat_id, mark)
        if entry.mastery and target_id is not None:
            self._mastery(combat_id, entry, target_id, hit, by)
            result = self.get(combat_id)
        return result

    def _mastery(
        self, combat_id: str, entry: CombatLogEntry, target_id: str, hit: bool, by: str
    ) -> None:
        """What a weapon mastery does after the attack: Topple calls a Constitution save
        (rolled at once for a creature; a character's player rolls it), Graze works on a
        miss, the others are noted for the table."""
        name, _, dc = entry.mastery.partition(":")
        key = name.lower()
        if key == "topple" and hit:
            effect = SaveEffect(int(dc or 10), "con", (), False, "prone")
            self.save_effect(combat_id, entry.actor, "Topple", effect, by)
            target = self._member(self.get(combat_id), target_id)
            if target.character_id is None:
                called = self.get(combat_id).pending[0]
                self.resolve_save(combat_id, called.at.isoformat(), {target_id: "roll"}, by)
            return
        if (key == "graze") != hit:
            label = self.label_of(self._member(self.get(combat_id), target_id))

            def note(combat: Combat) -> None:
                self._log(combat, "mastery", by, subject=label, detail=key)

            self._mutate(combat_id, note)

    def toggle_lair(self, combat_id: str, by: str) -> Combat:
        """Lair actions on or off — off when the fight is not in the creature's lair."""

        def apply(combat: Combat) -> None:
            if combat.turn == LAIR and combat.lair:
                # The lair had the turn: it passes to whoever came next.
                order = self.order(combat)
                index = self._index(order, LAIR)
                following = order[(index + 1) % len(order)] if len(order) > 1 else None
                if following is not None and index + 1 >= len(order):
                    combat.round += 1
                combat.turn = following.group if following is not None else None
            combat.lair = not combat.lair
            self._log(combat, "lair_on" if combat.lair else "lair_off", by)

        return self._mutate(combat_id, apply)

    def summary(self, combat: Combat) -> Summary:
        """Rounds, who fell, and the XP for the defeated monsters."""
        fallen = tuple(c for c in combat.combatants if not c.is_friend and self.is_down(c))
        defeated = []
        for combatant in fallen:
            monster = self.monster_of(combatant)
            xp = (monster.cr.xp or 0) if monster is not None and monster.cr is not None else 0
            defeated.append((self.label_of(combatant), xp))
        characters = [c for c in combat.combatants if c.is_character]
        return Summary(
            combat.round,
            fallen,
            sum(xp for _, xp in defeated),
            tuple(defeated),
            tuple(self.label_of(c) for c in characters),
            tuple(self.label_of(c) for c in characters if self.is_down(c)),
        )

    def finished(self, encounter_id: str) -> builtins.list[Combat]:
        """The encounter's ended combats, newest first."""
        done = [c for c in self.list(encounter_id=encounter_id) if not c.running]
        return sorted(done, key=lambda c: c.created_at, reverse=True)

    def row(self, combatant: Combatant) -> Row:
        """One combatant's numbers, a character's read from its sheet."""
        if combatant.character_id is not None:
            pc = self._characters.snapshot(combatant.character_id)
            if pc is None:
                return Row(combatant, combatant.label, None, None, None, 0, (), None, "unhurt")
            return Row(
                combatant,
                pc.name,
                pc.ac,
                pc.hp,
                pc.hp_max,
                pc.temp,
                pc.conditions,
                pc.concentration,
                "down" if pc.dying else health_word(pc.hp, pc.hp_max or 0),
                character=pc,
            )
        return Row(
            combatant,
            combatant.label,
            combatant.ac,
            combatant.hp if combatant.hp_max else None,
            combatant.hp_max or None,
            combatant.hp_temp,
            tuple(combatant.conditions),
            combatant.concentration,
            combatant.health,
            monster=self.monster_of(combatant),
        )

    def view(self, combat: Combat) -> CombatView:
        """The order with every row, and which turn is acting."""
        acting = combat.turn if combat.round else None
        turns = tuple(
            TurnView(turn, tuple(self.row(m) for m in turn.members), turn.group == acting)
            for turn in self.order(combat)
        )
        snapshots = {row.combatant.id: row.character for turn in turns for row in turn.rows}
        token = self._token
        return CombatView(
            combat,
            turns,
            lair_available=bool(self.lair_creatures(combat)),
            reminders=self.reminders(combat) if combat.running else Reminders(),
            rating=self.difficulty(combat, snapshots),
            tokens={c.id: token(c, combat.world_id) for c in combat.combatants} if token else {},
        )

    def difficulty(
        self,
        combat: Combat,
        snapshots: Mapping[str, CharacterSnapshot | None] | None = None,
    ) -> Rating:
        """How hard the fight is now, by the encounter's rules (§3).

        Only those still standing count, so the rating falls as foes fall. The party is
        the characters not down plus the allies standing, an ally counted as a member
        of a level equal to its challenge rating (at least 1); the foes are the monsters
        standing against them, rated by their XP. Someone without a challenge rating (an
        ad-hoc combatant) counts on neither side. A fight without characters is rated
        against the party the encounter states, if it states one.

        ``snapshots`` are the characters as the rows already read them, by combatant id.
        """
        encounter = (
            self._store.encounters.find(combat.encounter_id) if combat.encounter_id else None
        )
        world = self._store.worlds.find(combat.world_id) if combat.world_id else None
        rules = rules_for(
            world.source_set if world else None, encounter.rules if encounter else None
        )
        levels: builtins.list[int] = []
        foes: builtins.list[tuple[int, int]] = []
        has_characters = False
        for combatant in combat.combatants:
            if combatant.is_character:
                has_characters = True
                if snapshots is not None and combatant.id in snapshots:
                    pc = snapshots[combatant.id]
                else:
                    pc = self._characters.snapshot(combatant.character_id or "")
                if pc is not None and pc.level > 0 and not pc.dying and not pc.dead:
                    levels.append(pc.level)
                continue
            if combatant.is_down:
                continue
            monster = self.monster_of(combatant)
            cr = monster.cr if monster is not None else None
            if cr is None:
                continue
            if combatant.ally:
                levels.append(max(1, int(cr.value)))
            elif cr.xp is not None:
                foes.append((cr.xp, 1))
        if not has_characters and encounter is not None and encounter.party is not None:
            levels = [encounter.party.level] * encounter.party.size + levels
        return self._rules.rate(rules, levels, foes)

    def player_view(self, combat: Combat) -> PlayerView:
        """What the players see: hidden combatants left out, no numbers but the round
        and the hit points friends have left (as a share, for the bar)."""
        turns = []
        view = self.view(combat)
        for turn in view.turns:
            members = tuple(
                SeenCombatant(
                    row.label,
                    row.health,
                    row.conditions,
                    row.combatant.character_id,
                    row.monster.name if row.monster else None,
                    row.combatant.is_friend,
                    round(row.ratio * 100) if row.combatant.is_friend and row.hp_max else None,
                    row.combatant.id,
                    view.tokens.get(row.combatant.id),
                )
                for row in turn.rows
                if not row.combatant.hidden
            )
            if members:
                turns.append(SeenTurn(members, turn.active))
        return PlayerView(
            combat.id,
            combat.name,
            combat.world_id,
            combat.round,
            combat.running,
            combat.updated_at.isoformat(),
            tuple(turns),
            frozenset(
                c.character_id
                for c in combat.combatants
                if c.character_id is not None and c.initiative is None
            ),
            self._save_calls(combat),
            self._repeat_calls(combat),
            self._targets(combat),
        )

    def player_details(self, combat: Combat, combatant_id: str, lang: str = "en") -> SeenDetails:
        """A combatant's Details for the players, the books' description in ``lang``
        where translated; a hidden one is not there."""
        combatant = combat.combatant(combatant_id)
        if combatant is None or combatant.hidden:
            raise CombatNotFound(combatant_id)
        member = next(
            m
            for turn in self.player_view(combat).turns
            for m in turn.members
            if m.id == combatant_id
        )
        monster = self.view(combat).row(combatant_id)
        found = monster.monster if monster is not None else None
        seen = self._sight(found, lang) if found is not None and self._sight is not None else None
        known = (
            self._known(combat.world_id, combatant.ref)
            if combatant.ref is not None and self._known is not None
            else {}
        )
        return SeenDetails(member, seen, known)

    @staticmethod
    def _targets(combat: Combat) -> dict[str, str]:
        seen = {c.id for c in combat.combatants if not c.hidden}
        return {
            c.character_id: c.target
            for c in combat.combatants
            if c.character_id is not None and c.target in seen
        }

    def choose_target(self, combat_id: str, character_id: str, target_id: str | None) -> Combat:
        """A player picks the creature their character aims at, or none. Bookkeeping for
        the table, not an event: nothing is logged."""

        def apply(combat: Combat) -> None:
            me = next((c for c in combat.combatants if c.character_id == character_id), None)
            if me is None:
                raise CombatError("combat.error.not_in_fight")
            if target_id is not None:
                aimed = self._member(combat, target_id)
                if aimed.hidden or aimed.id == me.id:
                    raise CombatNotFound(target_id)
            me.target = target_id

        return self._mutate(combat_id, apply)

    def _repeat_calls(self, combat: Combat) -> tuple[tuple[str, str, str], ...]:
        if not combat.running or not combat.round:
            return ()
        current = self.view(combat).current
        if current is None:
            return ()
        return tuple(
            (row.combatant.character_id, due.condition, due.ability)
            for row in current.rows
            if row.combatant.character_id
            for due in row.combatant.repeat_saves
            if due.rolled_round != combat.round
        )

    def player_attack(self, character_id: str, subject: str, by: str) -> Combat | None:
        """An attack the player just rolled on the sheet joins their running fight: its
        damage is rolled here (dice doubled on a natural 20, none on a 1) and logged in
        the fight, and the attack waits for the gamemaster's hit or miss — whose verdict
        refers to the sheet's entry, the roll already in the log."""
        fights = self.joined_by([character_id])
        rolled = self._characters.newest_roll(character_id, (EntryKind.ATTACK,))
        if not fights or rolled is None or rolled.roll is None:
            return None
        to_hit = rolled.roll.total
        name = rolled.what.partition(":")[2] or rolled.what
        combat = fights[0]
        row = next((c for c in combat.combatants if c.character_id == character_id), None)
        weapon = self._characters.attack_of(character_id, subject)
        mastery = self._characters.mastery_of(character_id, subject)
        if row is None:
            return None
        parts = [plain_dice(rolled.roll)]
        by_type: dict[str, int] = {}
        damage_record: RollRecord | None = None
        if rolled.outcome != "fumble" and weapon is not None and is_expression(weapon[0]):
            expression, kind = weapon
            if rolled.outcome == "crit":
                expression = critical(expression)
            damage = self._dice.roll(expression)
            by_type[kind] = max(0, damage.total)
            parts.append(f"{damage.detail} {kind}".strip())
            damage_record = roll_record(damage, rest="modifier")
        natural: Literal["", "crit", "fumble"] = ""
        if rolled.outcome in ("crit", "fumble"):
            natural = "crit" if rolled.outcome == "crit" else "fumble"

        def apply(combat: Combat) -> None:
            if damage_record is not None:
                self._stage(
                    ProtocolEntry(
                        actor_name=by,
                        subject=f"character:{character_id}",
                        subject_name=rolled.subject_name,
                        kind=EntryKind.DAMAGE,
                        what=f"damage:{name}",
                        roll=damage_record,
                        types=dict(by_type),
                        visibility=rolled.visibility,
                        scope=Scope(
                            world=combat.world_id, combat=combat.id, round=combat.round or None
                        ),
                        origin="tracker",
                    )
                )
            self._log(
                combat,
                "attack",
                by,
                subject=self.label_of(row),
                amount=to_hit,
                detail=name[:80],
                outcome=natural,
                damage=sum(by_type.values()) if by_type else None,
                dice=" · ".join(parts),
                damage_types=by_type,
                actor=row.id,
                mastery=f"{mastery[0]}:{mastery[1]}" if mastery else "",
                damage_dice=f"{weapon[0]}:{weapon[1]}" if weapon is not None else "",
                entry_id=rolled.id,
                recorded=False,  # the roll is in the log already, from the sheet
            )

        return self._mutate(combat.id, apply)

    def player_spell(self, character_id: str, reference: str, by: str, level: int = 0) -> Combat:
        """A player's spell that calls for saves joins their running fight as a save
        effect: damage rolled once, each target settled by the gamemaster."""
        fights = self.joined_by([character_id])
        if not fights:
            raise CombatError("combat.error.not_in_fight")
        combat = fights[0]
        row = next((c for c in combat.combatants if c.character_id == character_id), None)
        found = self._characters.spell_effect(character_id, reference, level)
        if row is None or found is None:
            raise CombatError("combat.error.bad_input")
        name, effect = found
        return self.save_effect(combat.id, row.id, name, effect, by)

    def player_repeat(self, combat_id: str, character_id: str, condition: str, by: str) -> bool:
        """A player rolls their character's repeated save on the sheet; returns the success."""
        combat = self.get(combat_id)
        row = next((c for c in combat.combatants if c.character_id == character_id), None)
        due = next((r for r in (row.repeat_saves if row else []) if r.condition == condition), None)
        if row is None or due is None or due.rolled_round == combat.round:
            raise CombatError("combat.error.no_repeat")
        self._characters.act(
            character_id, "roll", {"kind": "save", "subject": due.ability}, by, combat
        )
        rolled = self._characters.newest_roll(character_id, (EntryKind.SAVE,))
        total = rolled.roll.total if rolled is not None and rolled.roll is not None else None
        return self.repeat_save(combat_id, row.id, condition, by, total=total)

    def _save_calls(self, combat: Combat) -> tuple[SaveCall, ...]:
        if not combat.running:
            return ()
        by_row = {c.id: c.character_id for c in combat.combatants if c.character_id}
        source = {c.id: c for c in combat.combatants}
        calls = []
        for entry in pending_attacks(combat):
            if entry.kind != "save_effect":
                continue
            origin = source.get(entry.actor)
            calls.append(
                SaveCall(
                    entry.at.isoformat(),
                    entry.save_ability,
                    entry.detail,
                    "" if origin is None or origin.hidden else entry.subject,
                    {by_row[k]: v for k, v in entry.rolls.items() if k in by_row},
                )
            )
        return tuple(calls)

    def player_save(self, combat_id: str, at: str, character_id: str, by: str) -> int:
        """A player rolls their character's save against a waiting save effect, on the
        sheet (it lands in their log); the total is kept for the gamemaster. Returns it."""
        combat = self.get(combat_id)
        entry = next(
            (
                e
                for e in pending_attacks(combat)
                if e.kind == "save_effect" and e.at.isoformat() == at
            ),
            None,
        )
        row = next((c for c in combat.combatants if c.character_id == character_id), None)
        if entry is None or row is None:
            raise CombatError("combat.error.attack_gone")
        if row.id in entry.rolls:
            raise CombatError("combat.error.save_rolled")
        self._characters.act(
            character_id, "roll", {"kind": "save", "subject": entry.save_ability}, by, combat
        )
        rolled = self._characters.newest_roll(character_id, (EntryKind.SAVE,))
        total = rolled.roll.total if rolled is not None and rolled.roll is not None else None
        if total is None:
            raise CombatError("combat.error.bad_input")

        def keep(combat: Combat) -> None:
            for index, e in enumerate(combat.pending):
                if e.kind == "save_effect" and e.at == entry.at:
                    combat.pending[index] = e.model_copy(
                        update={"rolls": {**e.rolls, row.id: total}}
                    )

        self._mutate(combat_id, keep)
        return total

    def joined_by(self, character_ids: Iterable[str]) -> builtins.list[Combat]:
        """The running combats any of these characters is in, newest first."""
        wanted = set(character_ids)
        found = [c for c in self.running() if any(m.character_id in wanted for m in c.combatants)]
        return sorted(found, key=lambda c: c.created_at, reverse=True)

    # -- starting and ending -----------------------------------------------------------

    def start(
        self,
        encounter: Encounter,
        by: str,
        characters: builtins.list[str] | None = None,
    ) -> Combat:
        """A new combat from an encounter's plan, or the one already running for it.

        Each planned line becomes a group, its members numbered when more than one, its
        initiative rolled. ``characters`` defaults to every character in the
        encounter's world; their initiative waits for the player's roll.
        """
        for existing in self.list(encounter_id=encounter.id):
            if existing.running:
                return existing
        combat = Combat(name=encounter.name, encounter_id=encounter.id, world_id=encounter.world_id)
        for planned in encounter.monsters:
            monster = self._monster(planned.name, planned.source)
            if monster is not None:
                self._add_monsters(
                    combat,
                    monster,
                    planned.count,
                    ally=planned.ally,
                    label=planned.reskin,
                    names=planned.names,
                )
        if characters is None:
            characters = [
                pc.id
                for pc in self._store.characters.list(
                    lambda pc: encounter.world_id is not None and pc.world_id == encounter.world_id
                )
            ]
        for character_id in characters:
            self._add_character(combat, character_id)
        staged: builtins.list[ProtocolEntry] = []
        outer = getattr(self._staged, "items", None)
        self._staged.items = staged
        try:
            self._log(combat, "start", by, subject=combat.name)
        finally:
            self._staged.items = outer
        created = self._store.combats.create(combat)
        self._flush(staged)
        self._notify(created, created.id)
        return created

    def begin(self, combat_id: str, by: str) -> Combat:
        """Round 1: the first in the order acts. Unrolled initiatives sort last."""

        def apply(combat: Combat) -> None:
            if combat.round:
                raise CombatError("combat.error.begun")
            order = self.order(combat)
            if not order:
                raise CombatError("combat.error.empty")
            combat.round = 1
            combat.turn = order[0].group
            self._log(combat, "begin", by)
            self._log_turn(combat, order[0], by)

        return self._mutate(combat_id, apply)

    def end(self, combat_id: str, by: str) -> Combat:
        """Stamp the end; the record stays as history until deleted."""

        def apply(combat: Combat) -> None:
            combat.ended = _now()
            self._log(combat, "end", by)

        return self._mutate(combat_id, apply)

    def award(self, combat_id: str, xp_each: int, by: str) -> Combat:
        """Give each character who took part ``xp_each`` XP: once, after the end (§6)."""
        combat = self.get(combat_id)
        if combat.running:
            raise CombatError("combat.error.award_running")
        if combat.awarded_at is not None:
            raise CombatError("combat.error.awarded")
        if xp_each < 0:
            raise CombatError("combat.error.award_amount")
        for combatant in combat.combatants:
            if combatant.character_id:
                self._characters.award_xp(combatant.character_id, xp_each, combat.name, by)

        def apply(record: Combat) -> None:
            record.awarded_xp = xp_each
            record.awarded_at = _now()
            self._log(record, "award", by, amount=xp_each)

        return self._mutate(combat_id, apply, after_end=True)

    def delete(self, combat_id: str) -> None:
        """Remove a combat for good."""
        self.get(combat_id)
        self._store.combats.delete(combat_id)
        self._notify(None, combat_id)

    # -- turns -------------------------------------------------------------------------

    def next_turn(self, combat_id: str, by: str) -> Combat:
        """The next group acts; past the last, a new round begins.

        Timers on the outgoing group's members count down first. A group of monsters
        all down is skipped; a down character is not — death saves happen on its turn.
        """

        def apply(combat: Combat) -> None:
            order = self._started_order(combat)
            index = self._index(order, combat.turn)
            self._tick_timers(combat, order[index], by)
            for step in range(1, len(order) + 1):
                position = index + step
                turn = order[position % len(order)]
                if self._skippable(turn) and step < len(order):
                    continue
                if position >= len(order):
                    combat.round += 1
                combat.turn = turn.group
                self._log_turn(combat, turn, by)
                return

        return self._mutate(combat_id, apply)

    def previous_turn(self, combat_id: str, by: str) -> Combat:
        """Back one turn, for the mis-tap. Timers that ran down are not restored."""

        def apply(combat: Combat) -> None:
            order = self._started_order(combat)
            index = self._index(order, combat.turn)
            if index == 0:
                if combat.round <= 1:
                    return
                combat.round -= 1
            turn = order[index - 1]
            combat.turn = turn.group
            self._log_turn(combat, turn, by)

        return self._mutate(combat_id, apply)

    def _started_order(self, combat: Combat) -> builtins.list[Turn]:
        if combat.round == 0:
            raise CombatError("combat.error.not_begun")
        order = self.order(combat)
        if not order:
            raise CombatError("combat.error.empty")
        return order

    @staticmethod
    def _index(order: builtins.list[Turn], group: str | None) -> int:
        return next((i for i, t in enumerate(order) if t.group == group), 0)

    def _skippable(self, turn: Turn) -> bool:
        if turn.is_lair:  # no lair actions once its creatures are down
            return all(self.is_down(m) for m in turn.lair)
        return not turn.is_character and all(self.is_down(m) for m in turn.members)

    def _log_turn(self, combat: Combat, turn: Turn, by: str) -> None:
        if turn.is_lair:
            self._log(combat, "lair", by, subject=self._lair_label(turn))
        else:
            self._log(combat, "turn", by, subject=self._turn_label(turn))

    def _lair_label(self, turn: Turn) -> str:
        names: builtins.list[str] = []
        for member in turn.lair:
            group = self.lair_group(member)
            name = str((group or {}).get("name") or self.label_of(member))
            if name not in names:
                names.append(name)
        return ", ".join(names)

    def _turn_label(self, turn: Turn) -> str:
        labels = [self.label_of(m) for m in turn.members]
        lead = turn.members[0]
        monster = self.monster_of(lead)
        # "Goblin ×4", as the tracker names the turn.
        same_kind = all(m.ref == lead.ref for m in turn.members)
        if len(labels) > 1 and monster is not None and same_kind:
            return f"{monster.name} ×{len(labels)}"
        return labels[0] if len(labels) == 1 else ", ".join(labels)

    def _tick_timers(self, combat: Combat, turn: Turn, by: str) -> None:
        for member in turn.members:
            kept = []
            for timer in member.timers:
                if timer.rounds > 1:
                    kept.append(ConditionTimer(condition=timer.condition, rounds=timer.rounds - 1))
                    continue
                self._drop_condition(member, timer.condition, by)
                self._log(
                    combat,
                    "condition_expired",
                    by,
                    subject=self.label_of(member),
                    detail=timer.condition,
                )
            member.timers = kept

    # -- initiative --------------------------------------------------------------------

    def set_initiative(self, combat_id: str, combatant_id: str, value: int, by: str) -> Combat:
        """Set a group's initiative (the gamemaster's override, or a player's roll)."""
        if not -10 <= value <= 50:
            raise CombatError("combat.error.initiative")

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            for member in combat.combatants:
                if member.group == target.group:
                    member.initiative = value
                    member.initiative_at = _now()
            self._log(combat, "initiative", by, subject=self.label_of(target), amount=value)

        return self._mutate(combat_id, apply)

    def roll_initiative(self, combat_id: str, combatant_id: str, by: str) -> Combat:
        """Roll for a combatant's group: d20 + its bonus, with advantage where it has it."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            self._roll_group(combat, target.group, by)

        return self._mutate(combat_id, apply)

    def _roll_group(self, combat: Combat, group: str, by: str, *, log: bool = True) -> None:
        members = [c for c in combat.combatants if c.group == group]
        lead = members[0]
        advantage = Advantage.NORMAL
        monster = self.monster_of(lead)
        if monster is not None:
            advantage = monster_initiative(monster)[1]
        roll = self._dice.d20(lead.initiative_bonus, advantage)
        for member in members:
            member.initiative = roll.total
            member.initiative_at = _now()
        if not log:
            return
        self._log(
            combat,
            "initiative",
            by,
            subject=self.label_of(lead),
            amount=roll.total,
            detail=roll.detail,
            roll=roll_record(roll, advantage=advantage, rest=_bonus_source(lead)),
        )

    def set_order(self, combat_id: str, combatant_id: str, step: int, by: str) -> Combat:
        """Move a group up (−1) or down (+1) among those it ties with."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            # A tie is what the rules leave undecided: same count, bonus and kind.
            rank = _rank(target)
            ties = [t for t in self.order(combat) if t.members and _rank(t.members[0]) == rank]
            index = next(i for i, t in enumerate(ties) if t.group == target.group)
            other = index + (1 if step > 0 else -1)
            if not 0 <= other < len(ties):
                return
            ties[index], ties[other] = ties[other], ties[index]
            position = {turn.group: i for i, turn in enumerate(ties)}
            for member in combat.combatants:
                if member.group in position:
                    member.order = position[member.group]

        return self._mutate(combat_id, apply)

    # -- hit points --------------------------------------------------------------------

    def damage(self, combat_id: str, combatant_id: str, amount: int, by: str) -> Combat:
        """Damage: temporary HP first; a concentrating monster owes a save."""
        return self._hit_points(combat_id, combatant_id, "damage", amount, by)

    def heal(self, combat_id: str, combatant_id: str, amount: int, by: str) -> Combat:
        """Healing up to the maximum; temporary HP is not restored by it."""
        return self._hit_points(combat_id, combatant_id, "heal", amount, by)

    def temp(self, combat_id: str, combatant_id: str, amount: int, by: str) -> Combat:
        """Temporary HP: the higher of what is held and what is given, never the sum."""
        return self._hit_points(combat_id, combatant_id, "temp", amount, by)

    def _hit_points(
        self,
        combat_id: str,
        combatant_id: str,
        mode: Literal["damage", "heal", "temp"],
        amount: int,
        by: str,
    ) -> Combat:
        amount = abs(amount)
        if not amount:
            raise CombatError("combat.error.amount")

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if target.character_id is not None:
                self._characters.act(
                    target.character_id, "hp", {"mode": mode, "amount": str(amount)}, by, combat
                )
            elif mode == "temp":
                target.hp_temp = max(target.hp_temp, amount)
            elif mode == "heal":
                ceiling = target.hp_max if target.hp_max else target.hp + amount
                target.hp = min(ceiling, target.hp + amount)
            else:
                absorbed = min(target.hp_temp, amount)
                target.hp_temp -= absorbed
                rest = amount - absorbed
                target.hp = max(0, target.hp - rest)
                if rest and target.concentration is not None:
                    target.save_due = max(10, rest // 2, target.save_due or 0)
                if target.hp == 0:
                    target.save_due = None
                    target.concentration = None
            self._log(combat, mode, by, subject=self.label_of(target), amount=amount)

        return self._mutate(combat_id, apply)

    # -- conditions and concentration --------------------------------------------------

    def toggle_condition(
        self,
        combat_id: str,
        combatant_id: str,
        condition: str,
        by: str,
        rounds: int | None = None,
    ) -> Combat:
        """Put a condition on (for ``rounds`` rounds, if given) or take it off."""
        condition = condition.lower()
        if condition not in CONDITIONS:
            raise CombatError("combat.error.condition", condition=condition)
        if rounds is not None and not 1 <= rounds <= 100:
            raise CombatError("combat.error.rounds")

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            label = self.label_of(target)
            if condition in self._conditions(target):
                self._drop_condition(target, condition, by)
                self._log(combat, "condition_off", by, subject=label, detail=condition)
                return
            if target.character_id is not None:
                self._characters.act(
                    target.character_id, "condition", {"key": condition}, by, combat
                )
            else:
                target.conditions = [*target.conditions, condition]
            if rounds is not None:
                target.timers = [
                    *target.timers,
                    ConditionTimer(condition=condition, rounds=rounds),
                ]
            self._log(combat, "condition_on", by, subject=label, detail=condition, amount=rounds)

        return self._mutate(combat_id, apply)

    def _conditions(self, combatant: Combatant) -> builtins.list[str]:
        if combatant.character_id is not None:
            return self._characters.conditions(combatant.character_id)
        return combatant.conditions

    def _drop_condition(self, combatant: Combatant, condition: str, by: str) -> None:
        if condition in self._conditions(combatant):
            if combatant.character_id is not None:
                self._characters.act(combatant.character_id, "condition", {"key": condition}, by)
            else:
                combatant.conditions = [c for c in combatant.conditions if c != condition]
        combatant.timers = [t for t in combatant.timers if t.condition != condition]
        combatant.repeat_saves = [r for r in combatant.repeat_saves if r.condition != condition]

    def concentrate(self, combat_id: str, combatant_id: str, spell: str | None, by: str) -> Combat:
        """A monster starts (``spell``) or stops (None) concentrating."""
        cleaned = " ".join((spell or "").split())[:LABEL_LIMIT] or None

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if target.character_id is not None:
                raise CombatError("combat.error.on_sheet")
            target.concentration = cleaned
            target.save_due = None
            self._log(combat, "concentration", by, subject=target.label, detail=cleaned or "")

        return self._mutate(combat_id, apply)

    def concentration_save(
        self, combat_id: str, combatant_id: str, by: str, kept: bool | None = None
    ) -> Combat:
        """Settle the save a monster owes: rolled with its CON save, or as the GM says."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if target.save_due is None:
                raise CombatError("combat.error.no_save")
            dc = target.save_due
            detail = f"DC {dc}"
            total: int | None = None
            record: RollRecord | None = None
            if kept is None:
                roll = self._dice.d20(concentration_bonus(self.monster_of(target)))
                success = roll.total >= dc
                detail, total = f"{roll.detail} vs DC {dc}", roll.total
                record = roll_record(roll, rest=_bonus_source(target))
            else:
                success = kept
            target.save_due = None
            if not success:
                target.concentration = None
            self._log(
                combat,
                "concentration_save",
                by,
                subject=target.label,
                amount=total,
                detail=detail,
                outcome="success" if success else "failure",
                roll=record,
                against=dc,
            )

        return self._mutate(combat_id, apply)

    # -- one entry point for the tracker's buttons ---------------------------------------

    COMBATANT_ACTIONS = frozenset(
        {
            "hp",
            "initiative",
            "roll",
            "order",
            "condition",
            "concentrate",
            "save",
            "split",
            "hide",
            "reveal",
            "edit",
            "remove",
            "attack",
            "save_effect",
            "rage",
            "repeat_save",
        }
    )

    def act(
        self,
        combat_id: str,
        combatant_id: str,
        action: str,
        params: Mapping[str, str],
        by: str,
    ) -> Combat:
        """Apply a row's button by name, reading its form fields from ``params``."""
        if action == "hp":
            mode = params.get("mode", "")
            amount = _number(params, "amount")
            if mode == "damage":
                return self.damage(combat_id, combatant_id, amount, by)
            if mode == "heal":
                return self.heal(combat_id, combatant_id, amount, by)
            if mode == "temp":
                return self.temp(combat_id, combatant_id, amount, by)
        elif action == "initiative":
            return self.set_initiative(combat_id, combatant_id, _number(params, "value"), by)
        elif action == "roll":
            return self.roll_initiative(combat_id, combatant_id, by)
        elif action == "order":
            return self.set_order(combat_id, combatant_id, _number(params, "step"), by)
        elif action == "condition":
            rounds = _optional(params, "rounds")
            return self.toggle_condition(
                combat_id, combatant_id, params.get("key", ""), by, rounds=rounds
            )
        elif action == "concentrate":
            return self.concentrate(combat_id, combatant_id, params.get("spell") or None, by)
        elif action == "save":
            kept = {"1": True, "0": False}.get(params.get("kept", ""))
            return self.concentration_save(combat_id, combatant_id, by, kept=kept)
        elif action == "split":
            return self.split(combat_id, combatant_id, by)
        elif action == "side":
            return self.switch_side(combat_id, combatant_id, by)
        elif action in ("hide", "reveal"):
            return self.hide(combat_id, combatant_id, action == "hide", by)
        elif action == "edit":
            return self.edit(
                combat_id,
                combatant_id,
                by,
                label=params.get("label"),
                ac=_optional(params, "ac"),
                hp=_optional(params, "hp"),
                hp_max=_optional(params, "hp_max"),
            )
        elif action == "remove":
            return self.remove(combat_id, combatant_id, by)
        elif action == "attack":
            return self.attack(
                combat_id,
                combatant_id,
                params.get("name", ""),
                _number(params, "hit"),
                decode(params.get("dmg", "")),
                by,
                Advantage.parse(params.get("adv")),
            )
        elif action == "repeat_save":
            self.repeat_save(combat_id, combatant_id, params.get("condition", ""), by)
            return self.get(combat_id)
        elif action == "rage":
            target = self._member(self.get(combat_id), combatant_id)
            if target.character_id is None:
                raise CombatError("combat.error.bad_input")
            self._characters.act(target.character_id, "rage", {}, by)
            return self.get(combat_id)
        elif action == "save_effect":
            condition = params.get("condition", "").lower()
            effect = SaveEffect(
                _number(params, "dc"),
                params.get("ability", ""),
                decode(params.get("dmg", "")),
                params.get("half") == "1",
                condition if condition in CONDITIONS else "",
                _optional(params, "rounds"),
                params.get("repeat", "") if params.get("repeat") in ("end", "start") else "",
            )
            if effect.ability not in ("str", "dex", "con", "int", "wis", "cha") or not (
                effect.damage or effect.condition
            ):
                raise CombatError("combat.error.bad_input")
            if effect.rounds is not None and not 1 <= effect.rounds <= 100:
                raise CombatError("combat.error.rounds")
            return self.save_effect(combat_id, combatant_id, params.get("name", ""), effect, by)
        elif action == "cast":
            return self.cast(combat_id, combatant_id, params.get("spell", ""), by)
        elif action == "restore_spells":
            return self.restore_spells(combat_id, combatant_id, by)
        elif action == "recharge":
            return self.roll_recharge(
                combat_id, combatant_id, params.get("ability", ""), _number(params, "at"), by
            )
        raise CombatError("combat.error.bad_input")

    # -- the roster --------------------------------------------------------------------

    def add_monster(
        self, combat_id: str, ref: str, count: int, by: str, *, ally: bool = False
    ) -> Combat:
        """A new group of monsters, initiative rolled on entry; ``ally`` on the party's side."""
        name, _, source = ref.partition("|")
        monster = self._monster(name.strip(), source.strip()) if source else None
        if monster is None:
            raise CombatError("combat.error.monster", ref=ref)
        if not 1 <= count <= MAX_ADD:
            raise CombatError("combat.error.count", limit=MAX_ADD)

        def apply(combat: Combat) -> None:
            self._add_monsters(combat, monster, count, ally=ally)
            self._log(combat, "add", by, subject=monster.name, amount=count)

        return self._mutate(combat_id, apply)

    def add_character(self, combat_id: str, character_id: str, by: str) -> Combat:
        """A character joins; its initiative waits for the player's roll."""
        if self._characters.name(character_id) is None:
            raise CombatError("combat.error.character")

        def apply(combat: Combat) -> None:
            if any(c.character_id == character_id for c in combat.combatants):
                raise CombatError("combat.error.already_in")
            added = self._add_character(combat, character_id)
            self._log(combat, "add", by, subject=added.label, amount=1)

        return self._mutate(combat_id, apply)

    def add_other(
        self,
        combat_id: str,
        label: str,
        by: str,
        *,
        ac: int | None = None,
        hp: int = 0,
        initiative: int | None = None,
        ally: bool = False,
    ) -> Combat:
        """Someone without a statblock: a summoned wolf, an NPC the book never statted."""
        cleaned = _clean_label(label)
        if hp < 0 or (ac is not None and not 0 <= ac <= 40):
            raise CombatError("combat.error.numbers")

        def apply(combat: Combat) -> None:
            taken = [c.label for c in combat.combatants]
            combat.combatants.append(
                Combatant(
                    label=_numbered(cleaned, taken, 1)[0],
                    ac=ac,
                    hp=hp,
                    hp_max=hp,
                    initiative=initiative,
                    ally=ally,
                )
            )
            self._log(combat, "add", by, subject=cleaned, amount=1)

        return self._mutate(combat_id, apply)

    def edit(
        self,
        combat_id: str,
        combatant_id: str,
        by: str,
        *,
        label: str | None = None,
        ac: int | None = None,
        hp: int | None = None,
        hp_max: int | None = None,
    ) -> Combat:
        """Correct a monster's or ad-hoc combatant's numbers. Characters edit their sheet."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if target.character_id is not None:
                raise CombatError("combat.error.on_sheet")
            if label is not None:
                target.label = _clean_label(label)
            if ac is not None:
                if not 0 <= ac <= 40:
                    raise CombatError("combat.error.numbers")
                target.ac = ac
            if hp_max is not None:
                if hp_max < 0:
                    raise CombatError("combat.error.numbers")
                target.hp_max = hp_max
            if hp is not None:
                if hp < 0:
                    raise CombatError("combat.error.numbers")
                target.hp = min(hp, target.hp_max) if target.hp_max else hp
            self._log(combat, "edit", by, subject=target.label)

        return self._mutate(combat_id, apply)

    def roll_names(
        self,
        combat_id: str,
        combatant_id: str,
        naming: Naming,
        edition: str,
        by: str,
        *,
        group: bool = False,
        style: str = "",
        language: str = "",
    ) -> Combat:
        """Name a creature in ``style``, or in the style its kind finds (NAMES.md §5);
        ``group`` names every member of its group, each differently. Names already in
        the fight are not given twice."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if target.character_id is not None:
                raise CombatError("combat.error.on_sheet")
            members = (
                [c for c in combat.combatants if c.group == target.group and not c.character_id]
                if group
                else [target]
            )
            if style:
                key = style
            else:
                name, _, source = (target.ref or "").partition("|")
                monster = self._monster(name, source) if source else None
                races = creature_races(name or target.label, monster.kind.tags if monster else ())
                key = naming.for_creature(races, edition, language).key
            avoid = [self.label_of(c) for c in combat.combatants if c not in members]
            try:
                names = naming.names(
                    key, count=len(members), avoid=avoid, spread=True, language=language
                )
            except NamingError as exc:
                raise CombatError("naming.error.roll") from exc
            if not names:
                raise CombatError("naming.error.roll")
            for member, rolled in zip(members, names, strict=False):
                member.label = _clean_label(rolled)
                self._log(combat, "edit", by, subject=member.label)

        return self._mutate(combat_id, apply)

    def switch_side(self, combat_id: str, combatant_id: str, by: str) -> Combat:
        """Friend becomes foe and back, for the whole group: its members act together.
        A charmed goblin who turns alone is split off first. Characters stay friends."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if target.is_character:
                raise CombatError("combat.error.side_character")
            ally = not target.ally
            members = [c for c in combat.combatants if c.group == target.group]
            for member in members:
                member.ally = ally
            turn = Turn(target.group, tuple(members))
            self._log(
                combat,
                "side",
                by,
                subject=self._turn_label(turn),
                detail="friend" if ally else "foe",
            )

        return self._mutate(combat_id, apply)

    def split(self, combat_id: str, combatant_id: str, by: str) -> Combat:
        """Take a monster out of its group: its own group, its own initiative."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            if sum(1 for c in combat.combatants if c.group == target.group) < 2:
                raise CombatError("combat.error.alone")
            # A fresh group; the rest of the old one keeps the turn if it had it.
            target.group = Combatant(label="-").group
            self._log(combat, "split", by, subject=target.label)
            self._roll_group(combat, target.group, by)

        return self._mutate(combat_id, apply)

    def hide(self, combat_id: str, combatant_id: str, hidden: bool, by: str) -> Combat:
        """Hide a combatant from the players, or reveal it."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            target.hidden = hidden
            self._log(combat, "hide" if hidden else "reveal", by, subject=self.label_of(target))

        return self._mutate(combat_id, apply)

    def remove(self, combat_id: str, combatant_id: str, by: str) -> Combat:
        """Take a combatant out. If its group had the turn and is now empty, the turn
        passes to whoever came next."""

        def apply(combat: Combat) -> None:
            target = self._member(combat, combatant_id)
            following: str | None = None
            if combat.turn == target.group:
                order = self.order(combat)
                index = self._index(order, target.group)
                if len(order[index].members) == 1 and len(order) > 1:
                    following = order[(index + 1) % len(order)].group
                    if index + 1 >= len(order):
                        combat.round += 1
            combat.combatants = [c for c in combat.combatants if c.id != combatant_id]
            if following is not None:
                combat.turn = following
            elif not combat.combatants:
                combat.turn = None
            self._log(combat, "remove", by, subject=self.label_of(target))

        return self._mutate(combat_id, apply)

    # -- helpers -----------------------------------------------------------------------

    def _add_monsters(
        self,
        combat: Combat,
        monster: MonsterProjection,
        count: int,
        *,
        ally: bool = False,
        label: str | None = None,
        names: builtins.list[str] | None = None,
    ) -> str:
        bonus, _advantage = monster_initiative(monster)
        group = Combatant(label="-").group
        taken = [c.label for c in combat.combatants]
        hp = monster.hp or 0
        # Members with their own names take them in order; the rest, or a reskinned
        # line ("Mire Lurker" for a Kuo-toa), are numbered.
        own = [_clean_label(n) for n in (names or [])][:count]
        rest = _numbered(label or monster.name, taken, count - len(own)) if count > len(own) else []
        for numbered in [*own, *rest]:
            combat.combatants.append(
                Combatant(
                    label=numbered,
                    ref=f"{monster.name}|{monster.source}",
                    group=group,
                    initiative_bonus=bonus,
                    ac=monster.ac,
                    hp=hp,
                    hp_max=hp,
                    ally=ally,
                )
            )
        # Rolled quietly: the start or the add says it.
        self._roll_group(combat, group, "", log=False)
        return group

    def _add_character(self, combat: Combat, character_id: str) -> Combatant:
        name = self._characters.name(character_id)
        if name is None:
            raise CombatError("combat.error.character")
        combatant = Combatant(
            label=name,
            character_id=character_id,
            initiative_bonus=self._characters.initiative_bonus(character_id),
        )
        combat.combatants.append(combatant)
        self._take_roll(combat, combatant)
        return combatant

    def take_initiative(self, character_id: str) -> builtins.list[Combat]:
        """Put a character's new initiative roll from the sheet into its running combats.

        Called on every change to the character; only a roll newer than the one the
        tracker holds — or than the gamemaster's typed value — is taken (COMBAT.md §4).
        Returns the combats that changed.
        """
        if getattr(self._writing, "active", False):
            return []
        changed = []
        for combat in self.running():
            member = next((c for c in combat.combatants if c.character_id == character_id), None)
            if member is None or self._sheet_roll(combat, member) is None:
                continue

            def apply(draft: Combat, member_id: str = member.id) -> None:
                self._take_roll(draft, self._member(draft, member_id))

            changed.append(self._mutate(combat.id, apply))
        return changed

    def _sheet_roll(self, combat: Combat, combatant: Combatant) -> SheetRoll | None:
        """A roll on the sheet this combatant has not taken yet.

        It counts from shortly before the combat started (:data:`ROLL_GRACE`), but never
        from before the end of an earlier fight the character was in, nor from before
        the initiative was last set here.
        """
        if combatant.character_id is None:
            return None
        since = combat.created_at - ROLL_GRACE
        for other in self._store.combats.list(
            lambda c: (
                c.id != combat.id
                and c.ended is not None
                and any(m.character_id == combatant.character_id for m in c.combatants)
            )
        ):
            assert other.ended is not None
            since = max(since, other.ended)
        if combatant.initiative_at is not None:
            since = max(since, combatant.initiative_at)
        roll = self._characters.initiative_roll(combatant.character_id, since)
        if roll is None or not -10 <= roll.total <= 50:
            return None
        return roll

    def _take_roll(self, combat: Combat, combatant: Combatant) -> None:
        roll = self._sheet_roll(combat, combatant)
        if roll is None:
            return
        combatant.initiative = roll.total
        combatant.initiative_at = roll.at
        self._log(
            combat,
            "initiative",
            roll.by,
            subject=combatant.label,
            amount=roll.total,
            detail=roll.detail,
        )

    @staticmethod
    def _member(combat: Combat, combatant_id: str) -> Combatant:
        found = combat.combatant(combatant_id)
        if found is None:
            raise CombatNotFound(combatant_id)
        return found

    def _log(
        self,
        combat: Combat,
        kind: Any,
        by: str,
        *,
        subject: str = "",
        amount: int | None = None,
        detail: str = "",
        outcome: Literal["", "success", "failure", "crit", "fumble"] = "",
        damage: int | None = None,
        dice: str = "",
        damage_types: dict[str, int] | None = None,
        actor: str = "",
        save_ability: str = "",
        half: bool = False,
        condition: str = "",
        rounds: int | None = None,
        repeat: str = "",
        mastery: str = "",
        damage_dice: str = "",
        roll: RollRecord | None = None,
        damage_rolls: Sequence[RollRecord] = (),
        refers_to: str | None = None,
        against: int | None = None,
        entry_id: str = "",
        recorded: bool = True,
    ) -> None:
        """Write what happened to the log; an attack or save effect with damage also
        waits as ``pending`` for the gamemaster. ``recorded=False`` keeps it out of the
        log — a player's attack, already there from the sheet (``entry_id``)."""
        entry = CombatLogEntry(
            round=combat.round,
            kind=kind,
            subject=subject,
            amount=amount,
            detail=detail,
            outcome=outcome,
            damage=damage,
            dice=dice,
            damage_types=damage_types or {},
            actor=actor,
            save_ability=save_ability,
            half=half,
            condition=condition,
            rounds=rounds,
            repeat=repeat,
            mastery=mastery,
            damage_dice=damage_dice,
            by=by,
            entry_id=entry_id,
        )
        if kind in ("attack", "save_effect") and damage is not None:
            # Waiting for a verdict; an older round's leftovers are dropped as it goes.
            combat.pending = [
                entry,
                *(e for e in combat.pending if e.round == combat.round),
            ][:PENDING_LIMIT]
        if not recorded:
            return
        recorded_entry = from_combat(combat, entry, roll=roll, damage_rolls=damage_rolls)
        updates: dict[str, Any] = {}
        if refers_to is not None:
            updates["refers_to"] = refers_to
        if against is not None:
            updates["against"] = against
        self._stage(recorded_entry.model_copy(update=updates) if updates else recorded_entry)

    def _stage(self, entry: ProtocolEntry) -> None:
        """Collect a log entry for the change being applied; appended once it is stored."""
        staged = getattr(self._staged, "items", None)
        if staged is not None:
            staged.append(entry)

    def _flush(self, staged: builtins.list[ProtocolEntry]) -> None:
        if self._protocol is not None and staged:
            self._protocol.append(staged)

    def _mutate(
        self, combat_id: str, apply: Callable[[Combat], None], *, after_end: bool = False
    ) -> Combat:
        """Change the record; an ended fight only takes what comes after it (an award)."""
        combat = self.get(combat_id)
        if combat.running == after_end:
            raise CombatError("combat.error.award_running" if after_end else "combat.error.ended")

        staged: builtins.list[ProtocolEntry] = []

        def guarded(record: Combat) -> None:
            # Work on a copy: a refused change must leave the stored record untouched.
            draft = record.model_copy(deep=True)
            staged.clear()
            apply(draft)
            for name in Combat.model_fields:
                setattr(record, name, getattr(draft, name))

        outer = getattr(self._staged, "items", None)
        self._writing.active = True
        self._staged.items = staged
        try:
            changed = self._store.combats.mutate(combat_id, guarded)
        finally:
            self._writing.active = False
            self._staged.items = outer
        # Logged once stored: a refused change leaves nothing in the log.
        self._flush(staged)
        self._notify(changed, combat_id)
        return changed

    def _notify(self, combat: Combat | None, combat_id: str) -> None:
        if self._changed is not None:
            self._changed(combat, combat_id)


_DICE_ONLY = re.compile(r"\d*d\d+")


def pending_attacks(combat: Combat) -> list[CombatLogEntry]:
    """This round's rolled attacks whose damage nobody has dealt with yet, newest first."""
    return [
        e
        for e in combat.pending
        if e.kind in ("attack", "save_effect")
        and e.round == combat.round
        and e.damage is not None
        and not e.applied
    ]


def _number(params: Mapping[str, str], key: str) -> int:
    value = _optional(params, key)
    if value is None:
        raise CombatError("combat.error.bad_input")
    return value


def _optional(params: Mapping[str, str], key: str) -> int | None:
    raw = str(params.get(key, "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise CombatError("combat.error.bad_input") from None


def _rank(combatant: Combatant) -> tuple[int | None, int, bool]:
    """What decides the order before the gamemaster does."""
    return (combatant.initiative, combatant.initiative_bonus, combatant.is_character)


def _natural(label: str) -> tuple[Any, ...]:
    """ "Goblin 2" before "Goblin 10"."""
    return tuple(int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", label))


__all__ = [
    "LAIR",
    "Recharge",
    "Regeneration",
    "Reminders",
    "CharacterSide",
    "CombatError",
    "CombatNotFound",
    "CombatService",
    "SheetCharacters",
    "Summary",
    "Turn",
    "concentration_bonus",
    "monster_initiative",
    "proficiency_bonus",
]
