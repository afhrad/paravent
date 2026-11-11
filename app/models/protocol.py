"""The log (Protokoll): one entry per thing that happened at the table.

documentation/PROTOCOL.md §2. An entry is a set of **facts** — who, on whose behalf, what,
the dice, the advantage and why, every bonus by its source, who may see it, where it
happened — never a sentence: players at one table read different languages, and each
screen phrases an entry in its reader's.

Entries are appended once and never changed (§3). A reveal, a clear or a correction is a
new entry that ``refers_to`` the old one.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.models.gamestate import GameStateModel, _new_id, _now


class Visibility(StrEnum):
    """Who may read an entry (PROTOCOL.md §4)."""

    PUBLIC = "public"
    # The actor and the gamemaster: a hidden Perception, an Insight check.
    PRIVATE = "private"
    # The gamemaster alone: behind the screen, a hidden combatant.
    GM = "gm"


class EntryKind(StrEnum):
    """What an entry records."""

    CHECK = "check"
    SAVE = "save"
    SKILL = "skill"
    INITIATIVE = "initiative"
    ATTACK = "attack"
    # A save effect (a breath weapon): its damage rolled once, each target's save after.
    EFFECT = "effect"
    DAMAGE = "damage"
    HEAL = "heal"
    TEMP_HP = "temp_hp"
    CAST = "cast"
    CONCENTRATION = "concentration"
    DEATH_SAVE = "death_save"
    HIT_DIE = "hit_die"
    REST = "rest"
    CONDITION = "condition"
    TURN = "turn"
    ROUND = "round"
    COMBAT_START = "combat_start"
    COMBAT_END = "combat_end"
    # The fight's own bookkeeping: combatants added, removed, split, hidden, edited …
    COMBAT = "combat"
    CUSTOM = "custom"
    # A character's progress: experience awarded, a level taken, ability scores and hit
    # points rolled in the wizard.
    EXPERIENCE = "experience"
    LEVEL_UP = "level_up"
    ABILITY_SCORES = "ability_scores"
    HIT_POINTS = "hit_points"
    STARTING_GOLD = "starting_gold"
    # The gamemaster handed something to a character (documentation/JOURNAL.md):
    # ``what`` is the handout's id, ``note`` its title.
    HANDOUT = "handout"
    # Bookkeeping over earlier entries (§3): never rolled, always ``refers_to`` one.
    REVEAL = "reveal"
    CLEAR = "clear"
    CORRECT = "correct"


Outcome = Literal["", "crit", "fumble", "success", "failure", "hit", "miss"]
Mode = Literal["normal", "advantage", "disadvantage"]


class Die(GameStateModel):
    """One kind of die in a roll: ``2d6`` is faces 6, results [3, 5], both kept."""

    faces: int
    results: list[int] = Field(default_factory=list)
    # The results that count; advantage keeps one of two d20s.
    kept: list[int] = Field(default_factory=list)


class Bonus(GameStateModel):
    """One part of a roll's flat bonus, by where it comes from.

    ``source`` is a key, not words, so it phrases in any language and can link:
    ``ability:dex``, ``proficiency``, ``expertise``, ``item:<name>``, ``feature:<name>``,
    ``feat:<name>``, ``spell:<name>``, ``condition:<key>``, ``exhaustion``,
    ``override:<key>``, ``modifier`` (a number in a dice expression), ``other``.
    """

    source: str
    value: int


class RollRecord(GameStateModel):
    """The dice as data: what was rolled, what was kept, and what was added."""

    expression: str
    dice: list[Die] = Field(default_factory=list)
    # The kept d20, for a natural 20 or 1.
    natural: int | None = None
    total: int
    # The advantage that applied, and why: "sheet" (the switch), "condition:poisoned".
    # One of each cancels; ``mode`` is then normal and both reasons stay.
    mode: Mode = "normal"
    reasons: list[str] = Field(default_factory=list)
    bonuses: list[Bonus] = Field(default_factory=list)


class Scope(GameStateModel):
    """Where it happened. No world: global (PROTOCOL.md §2)."""

    world: str | None = None
    adventure: str | None = None
    chapter: str | None = None
    combat: str | None = None
    round: int | None = None


class ProtocolEntry(GameStateModel):
    """One thing that happened, as facts each screen phrases itself."""

    id: str = Field(default_factory=_new_id)
    at: datetime = Field(default_factory=_now)
    # Who pressed the button (a username, or the name a screen gave), and on whose
    # behalf: "character:<id>", "combatant:<combat id>/<id>", "gm".
    actor: str = ""
    actor_name: str = ""
    subject: str = ""
    # The name shown for the subject when it was written ("Goblin 2"): combatants come
    # and go, the entry must still read.
    subject_name: str = ""
    target: str | None = None
    target_name: str = ""
    kind: EntryKind
    # What exactly: "save:dex", "skill:insight", "attack:Longsword", "cast:Fireball:3".
    what: str = ""
    roll: RollRecord | None = None
    # An attack's or effect's damage, one roll per part ("1d6+2" slashing, "2d6" fire).
    damage_rolls: list[RollRecord] = Field(default_factory=list)
    outcome: Outcome = ""
    # The DC or AC it was rolled against (the gamemaster's to see, §4).
    against: int | None = None
    # Damage, healing, temporary hit points; damage by type.
    amount: int | None = None
    types: dict[str, int] = Field(default_factory=dict)
    # Words a person typed, shown as written: the reason for an XP award.
    note: str = ""
    visibility: Visibility = Visibility.PUBLIC
    scope: Scope = Field(default_factory=Scope)
    # Where it was written: "sheet" (the play rules), "tracker" (the combat tracker),
    # "table" (a dice chip, the wizard, an award).
    origin: str = ""
    # For reveal, clear and correct: the entry (or, for clear, the subject's entries
    # up to this one) it is about.
    refers_to: str | None = None
    # Read once from an old character or combat log (§8), not recorded live.
    migrated: bool = False

    @model_validator(mode="after")
    def _bookkeeping_refers(self) -> ProtocolEntry:
        if self.kind in (EntryKind.REVEAL, EntryKind.CORRECT) and not self.refers_to:
            raise ValueError(f"a {self.kind} entry must refer to an entry")
        return self


__all__ = [
    "Bonus",
    "Die",
    "EntryKind",
    "Mode",
    "Outcome",
    "ProtocolEntry",
    "RollRecord",
    "Scope",
    "Visibility",
]
