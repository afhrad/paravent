"""Game-state models: the data Paravent writes, as opposed to the data it imports.

Unlike the compendium models these are *ours* — they define the schema rather than
mirroring someone else's, so they are strict: unknown fields are rejected, because an
unknown field here means a bug or a stale client, not an upstream schema we have yet to
model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema


def _now() -> datetime:
    """Current UTC time. Timezone-aware so serialised timestamps are unambiguous."""
    return datetime.now(UTC)


def _new_id() -> str:
    """A short unique identifier for a stored record."""
    return uuid.uuid4().hex[:12]


class GameStateModel(BaseModel):
    """Base for everything Paravent persists itself."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Role(StrEnum):
    """A user's role in a campaign.

    PROJECT.md and PROGRESS.md (both under ``documentation/``) specify multiuser with
    these two roles; TECHSTACK.md §7 described a single-user setup. The multiuser reading
    is authoritative — it is the superset, and the single-user case is just one
    gamemaster with no players.
    """

    GAMEMASTER = "gamemaster"
    PLAYER = "player"


def new_id() -> str:
    """A fresh record id."""
    return _new_id()


class Record(GameStateModel):
    """A persisted entity with an identity and audit timestamps."""

    id: str = Field(default_factory=_new_id)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self) -> None:
        """Mark the record as modified now."""
        self.updated_at = _now()


class User(Record):
    """An account. Passwords are stored only as a hash, never in clear."""

    username: str
    display_name: str = ""
    role: Role = Role.PLAYER
    password_hash: str = ""
    language: str = "en"
    # How distances and weights in structured fields are shown: the books' feet and
    # pounds, or metres and kilograms (documentation/TRANSLATION.md §6). Set with the
    # language: feet for English, metres otherwise.
    units: Literal["feet", "metres"] = "feet"
    # Which log entries pop up as toasts (documentation/PROTOCOL.md §6): "mine" — a
    # player's own rolls and what befalls their characters, the gamemaster's every
    # player roll — "all" the reader may see, or "off".
    toasts: Literal["mine", "all", "off"] = "mine"
    # The look this user reads Paravent in (documentation/THEMES.md): a theme's id,
    # "paravent" for Paravent's own, or "" for the table's.
    theme: str = ""
    active: bool = True

    def public(self) -> dict[str, Any]:
        """The representation safe to send to a client — no password hash."""
        return self.model_dump(exclude={"password_hash"}, mode="json")


# Sources that mark an entry as 2024 ("one") rather than 2014 ("classic") rules. The
# corpus carries an ``edition`` field on newer records, but not on every category, so
# the source code is the reliable signal.
ONE_EDITION_SOURCES: frozenset[str] = frozenset({"XPHB", "XMM", "XDMG"})

EDITION_CLASSIC = "classic"
EDITION_ONE = "one"


class SourceSet(GameStateModel):
    """Which books a world draws on.

    An **empty set means "everything imported"**. The tool has to work before anyone has
    configured it: a new user with a fresh corpus gets every entry resolvable, and
    narrows later if they want the correctness boundary that a declared set provides.
    """

    sources: list[str] = Field(default_factory=list)

    def __len__(self) -> int:
        """How many sources are declared; 0 means unrestricted."""
        return len(self.sources)

    @property
    def is_unrestricted(self) -> bool:
        """True when nothing is declared, so every imported source is legal."""
        return not self.sources

    @property
    def normalized(self) -> frozenset[str]:
        """The declared sources, lowercased for comparison."""
        return frozenset(s.lower() for s in self.sources)

    @property
    def identity(self) -> str:
        """A stable key for caches downstream of resolution.

        Resolution is context-dependent — the same tag resolves differently in two
        worlds — so anything memoised on a resolved entry must include this
        (documentation/DESIGN.md §7). Sorted so that declaration order cannot produce
        two keys for one set.
        """
        return "*" if self.is_unrestricted else "|".join(sorted(self.normalized))

    def allows(self, source: str | None) -> bool:
        """True when ``source`` is legal at this table.

        A missing source is allowed: some corpus records carry none, and excluding them
        would hide content for no stated reason.
        """
        if self.is_unrestricted or source is None:
            return True
        return source.lower() in self.normalized

    @property
    def editions(self) -> set[str]:
        """Which rule editions this set spans.

        Edition is a *consequence* of the declared books, not a separate mode:
        ``XPHB`` present means 2024 is in play, ``PHB`` means 2014. A table may declare
        both, which is legitimate — see the caveat in documentation/TECHSTACK.md §6c
        about what supersession then does.
        """
        if self.is_unrestricted:
            return {EDITION_CLASSIC, EDITION_ONE}
        found: set[str] = set()
        one = {s.lower() for s in ONE_EDITION_SOURCES}
        for source in self.normalized:
            found.add(EDITION_ONE if source in one else EDITION_CLASSIC)
        return found

    @property
    def is_single_edition(self) -> bool:
        """True when only one edition is in play, so no choice need be offered."""
        return len(self.editions) == 1

    def with_sources(self, *sources: str) -> SourceSet:
        """A copy with ``sources`` added, de-duplicated and order-preserving."""
        seen = {s.lower() for s in self.sources}
        extra = [s for s in sources if s.lower() not in seen]
        return SourceSet(sources=[*self.sources, *extra])


class GivenName(GameStateModel):
    """The parts a person's name was built from (documentation/NAMES.md §3), which a
    child's ``kin`` part reads. Worth something only while ``text`` is still their name."""

    text: str
    parts: dict[str, str] = Field(default_factory=dict)


class World(Record):
    """A setting that owns adventures and world-scope NPCs, treasure and POIs.

    Stored as ``library/worlds/<folder>/world.md`` (documentation/ADVENTURES.md §3): the
    fields below are its front matter, ``description`` is its Markdown body.
    """

    name: str
    description: str = ""
    language: str = "en"
    # A path relative to the world's folder, e.g. ``media/cover.webp``.
    cover: str | None = None

    # Which books are in play. Empty means "everything imported", so the tool works
    # before anyone configures it (documentation/TECHSTACK.md §6c).
    source_set: SourceSet = Field(default_factory=lambda: SourceSet())
    # Whether rolled ability scores may be rolled again in the wizard. Off: a roll is a
    # commitment (documentation/CHARGEN-WIZARD.md §0).
    allow_rerolls: bool = False
    # The PHB's optional variant encumbrance: past Strength × 5 lb. speed −10 ft., past
    # × 10 −20 ft. and disadvantage on Strength, Dexterity and Constitution rolls.
    variant_encumbrance: bool = False
    # The declared books players may read (documentation/PLAYER-VIEW.md). None: the
    # player's handbooks and the supplements among them.
    player_sources: list[str] | None = None
    # The naming styles names are rolled from here, by key (``own:…``, ``starter:…``,
    # a book's table); empty means every style, like the books (NAMES.md §5b).
    naming_styles: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _forget_naming(cls, data: Any) -> Any:
        """Worlds chose naming styles for one day (2026-11-01); styles now carry their own
        race and culture, so a file written then still loads."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k != "naming"}
        return data


# ``draft``: written through the MCP server and not yet reviewed (documentation/MCP.md §6).
Status = Literal["draft", "planned", "running", "finished"]


class LevelRange(GameStateModel):
    """The character levels an adventure is written for."""

    start: int = Field(default=1, ge=1, le=20)
    end: int = Field(default=20, ge=1, le=20)


class Adventure(Record):
    """An adventure inside a world: ``adventures/<folder>/adventure.md``.

    ``world_id`` is not in the file — it is the world whose folder the adventure sits
    in, so moving the folder moves the adventure.
    """

    world_id: str
    name: str
    summary: str = ""
    body: str = ""
    order: int = 0
    status: Status = "planned"
    level: LevelRange | None = None
    cover: str | None = None
    # A published adventure from the corpus this one is built on (``LMoP``).
    based_on: str | None = None
    # The characters it is written for; its encounters are rated against them rather
    # than every character of the world. None: the world's (documentation/MCP.md §6).
    party: list[str] | None = None
    # The MCP key that drafted it. Claude may write into it while it is a draft
    # (documentation/MCP.md §6).
    drafted_by: str | None = None
    # A draft copy of the adventure with this id, to be written back onto it (MCP.md §9):
    # each record's original (copy id → original id) and how the original stood.
    revision_of: str | None = None
    origins: dict[str, str] = Field(default_factory=dict)
    baseline: dict[str, str] = Field(default_factory=dict)

    # An optional narrowing of the world's books, for the case where one module is
    # deliberately tighter than the campaign. ``None`` means "use the world's set",
    # which is the normal case — a campaign usually has one book list.
    source_set: SourceSet | None = None

    def effective_source_set(self, world: World, book_source: str | None = None) -> SourceSet:
        """This adventure's books: its own override, else the world's.

        A published adventure's own source is always added: its text says
        ``{@creature Name|SOURCE}``, and that must resolve. ``book_source`` is that source
        when it differs from the ``based_on`` id (``MOT-NSS`` cites ``MOT``).
        """
        books = self.source_set if self.source_set is not None else world.source_set
        source = book_source or self.based_on
        if source and not books.is_unrestricted:
            return books.with_sources(source)
        return books


class Chapter(Record):
    """A chapter of an adventure: ``adventures/<folder>/chapters/<name>.md``.

    Its sections are the headings of its Markdown body. ``adventure_id`` and
    ``world_id`` come from where the file sits, not from the file.
    """

    world_id: str
    adventure_id: str
    title: str
    body: str = ""
    order: int = 0
    status: Status = "planned"
    cover: str | None = None
    # With ``based_on`` on the adventure: the id of the book section this chapter shows.
    section: str | None = None


class Scope(GameStateModel):
    """Where an object belongs: a world, optionally one adventure, optionally one chapter.

    Lookups go nearest first — chapter, adventure, world — so a chapter's own "Guard
    Captain" shadows the world's (documentation/ADVENTURES.md §4). Promoting an object to
    a wider scope is clearing a field.
    """

    world_id: str
    adventure_id: str | None = None
    chapter_id: str | None = None

    @property
    def record_id(self) -> str:
        """The most specific record — whose folder holds the object's images."""
        return self.chapter_id or self.adventure_id or self.world_id

    @property
    def level(self) -> Literal["world", "adventure", "chapter"]:
        """How narrow the scope is."""
        if self.chapter_id:
            return "chapter"
        return "adventure" if self.adventure_id else "world"


class Npc(Record):
    """A person the gamemaster places in the story: a boss, an ally, a shopkeeper.

    ``statblock`` is a compendium reference (``Name|SOURCE``), looked up when shown, as
    an encounter's monsters are. ``roles`` are tags; ``boss`` puts the NPC on the
    world's page.
    """

    scope: Scope
    name: str
    roles: list[str] = Field(default_factory=list)
    statblock: str | None = None
    # A path relative to the scope's record folder, e.g. ``media/maren.webp``.
    portrait: str | None = None
    notes: str = ""
    given_name: GivenName | None = None
    # What their name is made from: a naming style's race and culture (NAMES.md §5).
    race: str = ""
    culture: str = ""


class Location(Record):
    """A place of the campaign — a village, a cave, a tavern — written ``{@place Name}``
    (documentation/ADVENTURES.md §4). Called Location here because :class:`Place` is
    already where a user left off. Who and what is there, the notes say: ``{@npc}``,
    ``{@encounter}`` and ``{@treasure}`` in them are what the place's page lists."""

    scope: Scope
    name: str
    tags: list[str] = Field(default_factory=list)
    # A path relative to the scope's record folder, e.g. ``media/grey-reed.webp``.
    picture: str | None = None
    notes: str = ""


SoundKind = Literal["effect", "ambience", "music"]


class Sound(Record):
    """A sound of the desk (documentation/SOUND.md), shared by every world: an effect
    plays once, ambience loops and layers, music is one track at a time."""

    name: str
    kind: SoundKind = "effect"
    # The file's name in ``library/sounds/``.
    file: str
    bytes: int = 0
    volume: float = Field(default=1.0, ge=0, le=1)
    # Who made it and under what licence.
    credit: str = ""


class SoundLayer(GameStateModel):
    """One sound of a scene, at its volume."""

    sound_id: str
    volume: float = Field(default=1.0, ge=0, le=1)


class SoundScene(Record):
    """A saved mix, one button: "Tavern at night"."""

    name: str
    layers: list[SoundLayer] = Field(default_factory=list)


class TreasureItem(GameStateModel):
    """Some number of one item from the compendium."""

    name: str
    source: str
    count: int = Field(default=1, ge=1, le=999)

    @property
    def ref(self) -> str:
        """The compendium reference, ``Name|SOURCE``."""
        return f"{self.name}|{self.source}"


class Coins(GameStateModel):
    """A pile of coins, by denomination."""

    cp: int = Field(default=0, ge=0)
    sp: int = Field(default=0, ge=0)
    ep: int = Field(default=0, ge=0)
    gp: int = Field(default=0, ge=0)
    pp: int = Field(default=0, ge=0)

    @property
    def copper(self) -> int:
        """The pile's worth in copper pieces."""
        return self.cp + 10 * self.sp + 50 * self.ep + 100 * self.gp + 1000 * self.pp

    @property
    def empty(self) -> bool:
        """True when there are no coins at all."""
        return self.copper == 0


class Treasure(Record):
    """A hoard, a reward, a chest: items and coins, placed in the story."""

    scope: Scope
    name: str
    items: list[TreasureItem] = Field(default_factory=list)
    coins: Coins = Field(default_factory=lambda: Coins())
    notes: str = ""


class CreatureBlock(GameStateModel):
    """A named trait or action of a designed creature.

    ``text`` is 5eTools text — tags as in the corpus, paragraphs split by a blank line
    — and may hold the corpus's own creature-builder placeholders
    (``{@hit <$to_hit__str$>}``, ``DC <$dc__con$>``, ``<$short_name$>``), filled from
    the creature's numbers when the statblock is made, so a change of CR or scores
    follows into every attack.
    """

    name: str
    text: str = ""


class CreatureFeature(GameStateModel):
    """A *Dungeon Master's Guide* monster feature that changes the effective CR
    (``monsterfeatures.json``); ``value`` is its number where it takes one."""

    name: str
    value: int | None = None


class CreatureDesign(Record):
    """A creature made in the creature builder (app/services/creature_builder.py).

    The design is the record; the statblock is derived from it and published to
    ``library/homebrew/`` in 5eTools format, so it loads like any other creature.
    Hit points come from ``hit_dice`` and the size's die, bonuses from the CR's
    proficiency bonus and the scores — the numbers a statblock prints are never stored.
    """

    name: str
    world_id: str | None = None
    based_on: str | None = None  # ``Name|SOURCE`` it was started from
    size: str = "M"
    type: str = "humanoid"
    type_tags: list[str] = Field(default_factory=list)
    alignment: list[str] = Field(default_factory=lambda: ["N"])
    cr: str = "1"
    ac: int = Field(default=12, ge=0, le=40)
    ac_from: str = ""
    hit_dice: int = Field(default=2, ge=1, le=200)
    speed: dict[str, int] = Field(default_factory=lambda: {"walk": 30})
    abilities: dict[str, int] = Field(
        default_factory=lambda: {a: 10 for a in ("str", "dex", "con", "int", "wis", "cha")}
    )
    saves: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    senses: str = ""
    languages: str = ""
    resist: list[str] = Field(default_factory=list)
    immune: list[str] = Field(default_factory=list)
    vulnerable: list[str] = Field(default_factory=list)
    condition_immune: list[str] = Field(default_factory=list)
    traits: list[CreatureBlock] = Field(default_factory=list)
    actions: list[CreatureBlock] = Field(default_factory=list)
    bonus: list[CreatureBlock] = Field(default_factory=list)
    reactions: list[CreatureBlock] = Field(default_factory=list)
    legendary: list[CreatureBlock] = Field(default_factory=list)
    features: list[CreatureFeature] = Field(default_factory=list)
    # Damage per round when the actions' text cannot tell (save effects, auras).
    dpr_override: int | None = Field(default=None, ge=0, le=2000)
    published_at: datetime | None = None
    # A hash of the statblock as last published: a design differs from its
    # published self exactly when its statblock does.
    published_hash: str | None = None
    # The MCP key that drafted it (MCP.md): Claude's draft, left out of a publish until
    # the gamemaster accepts it; None for the gamemaster's own designs.
    drafted_by: str | None = None
    # A revision (MCP.md §10): Claude's draft copy of the accepted design with this id,
    # under the same name; accepting it writes it onto that design.
    revision_of: str | None = None


class EncounterMonster(GameStateModel):
    """Some number of one creature, planned into an encounter.

    A reference into the compendium, as a character's choices are: the statblock is
    looked up when needed, so a corpus update or a change of books re-derives the XP
    instead of leaving a stale number behind.
    """

    name: str
    source: str
    count: int = Field(default=1, ge=1, le=99)
    # On the characters' side (COMBAT.md §3): rated with the party, and starting the
    # fight as allies.
    ally: bool = False
    # A reskin: the name the table sees ("Mire Lurker") for a creature from the books whose
    # numbers it keeps (a Kuo-toa) — rated, run and statted as that creature (MCP.md §5).
    # A group's label may instead list a name for each, comma-separated ("Gruk, Ugrash").
    label: str | None = None

    @property
    def ref(self) -> str:
        """``Name|SOURCE`` — how the corpus and the combatants point at it."""
        return f"{self.name}|{self.source}"

    @property
    def names(self) -> list[str]:
        """The members' own names when the label lists them, else none."""
        if not self.label or "," not in self.label:
            return []
        return [n for n in (" ".join(part.split()) for part in self.label.split(",")) if n]

    @property
    def reskin(self) -> str | None:
        """The label as one name for the kind (a reskin), not a list of names."""
        return None if self.names else self.label


class PartySpec(GameStateModel):
    """A party described by size and level, for an encounter planned without characters."""

    size: int = Field(default=4, ge=1, le=12)
    level: int = Field(default=1, ge=1, le=20)


# Encounter fields that were never written with data; dropped when read.
_UNUSED_ENCOUNTER_FIELDS = frozenset({"module_id", "combatants", "round", "turn_index", "active"})


class Encounter(Record):
    """A prepared fight: the plan a :class:`Combat` is run from.

    Difficulty is rated against the characters in ``world_id`` unless ``party``
    describes one, under the rules the world's books imply unless ``rules`` says
    otherwise.
    """

    name: str
    # Where it belongs: a world, optionally narrowed to an adventure and a chapter
    # (documentation/ADVENTURES.md §4). Flat rather than a ``Scope`` because an
    # encounter may also belong to no world at all, planned ahead of one.
    world_id: str | None = None
    adventure_id: str | None = None
    chapter_id: str | None = None
    monsters: list[EncounterMonster] = Field(default_factory=list)
    party: PartySpec | None = None
    rules: Literal["classic", "one"] | None = None
    notes: str = ""
    # What starts when the fight is run: a sound or scene, by name (SOUND.md).
    sound: str = ""
    # The random-encounter table last rolled on, chosen again for the next roll.
    roll_table: str = ""

    @model_validator(mode="before")
    @classmethod
    def _drop_unused_fields(cls, data: Any) -> Any:
        # ``module_id`` was never set by anything; files written before chapters
        # existed carry it as null. The run-state skeleton (``combatants``, ``round``,
        # ``turn_index``, ``active``) moved to :class:`Combat` before anything wrote it.
        # Read such files rather than reject them.
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k not in _UNUSED_ENCOUNTER_FIELDS}
        return data

    @property
    def scope(self) -> Scope | None:
        """Where the encounter belongs, or None when it belongs to no world."""
        if self.world_id is None:
            return None
        return Scope(
            world_id=self.world_id, adventure_id=self.adventure_id, chapter_id=self.chapter_id
        )


# -- combat (documentation/COMBAT.md) ------------------------------------------------

CombatLogKind = Literal[
    "start",
    "begin",
    "turn",
    "initiative",
    "damage",
    "heal",
    "temp",
    "condition_on",
    "condition_off",
    "condition_expired",
    "concentration",
    "concentration_save",
    "add",
    "remove",
    "split",
    "hide",
    "reveal",
    "side",
    "edit",
    "end",
    "award",
    "lair",
    "lair_on",
    "lair_off",
    "recharge",
    "attack",
    "save_effect",
    "save_damage",
    "repeat_save",
    "mastery",
    "cast",
    "condition_immune",
]


Health = Literal["unhurt", "hurt", "bloodied", "critical", "down"]


def health_word(hp: int, hp_max: int) -> Health:
    """Bloodied at or below half, critical under a quarter, down at 0 (COMBAT.md §3).

    Without a known maximum nothing can be said, so the word is "unhurt".
    """
    if hp_max <= 0:
        return "unhurt"
    if hp <= 0:
        return "down"
    if hp * 4 < hp_max:
        return "critical"
    if hp * 2 <= hp_max:
        return "bloodied"
    return "hurt" if hp < hp_max else "unhurt"


class ConditionTimer(GameStateModel):
    """A condition that ends by itself after some rounds ("frightened, 2 rounds").

    Counted down at the end of the creature's turn; at zero the condition is removed.
    """

    condition: str
    rounds: int = Field(ge=1, le=100)


class RepeatSave(GameStateModel):
    """A condition its creature may shake off: the save it repeats at the end (or start)
    of each of its turns, until one succeeds."""

    condition: str
    ability: str
    dc: int = Field(ge=1, le=40)
    when: Literal["end", "start"] = "end"
    source: str = ""
    # The round it was last rolled in: asked once per turn.
    rolled_round: int = 0


class Combatant(GameStateModel):
    """One participant in a combat: a monster, a character or someone ad hoc.

    A **monster** has a ``ref`` into the compendium (``Goblin|MM``); a **character** a
    ``character_id`` and nothing else of its own but its initiative — its hit points,
    conditions and concentration stay in the character file. Anyone else (a summoned
    wolf, an NPC without a statblock) has neither and carries its numbers here.

    ``group`` joins monsters of one kind: they share one initiative and one turn
    (COMBAT.md §2). A character is always a group of its own.
    """

    id: str = Field(default_factory=_new_id)
    label: str
    ref: str | None = None
    character_id: str | None = None
    group: str = Field(default_factory=_new_id)
    initiative: int | None = None
    initiative_bonus: int = 0
    # When the initiative was last set, so a roll on the sheet made later replaces it.
    initiative_at: datetime | None = None
    # The gamemaster's order within a tie, lowest first.
    order: int = 0
    ac: int | None = None
    hp: int = Field(default=0, ge=0)
    hp_max: int = Field(default=0, ge=0)
    hp_temp: int = Field(default=0, ge=0)
    conditions: list[str] = Field(default_factory=list)
    concentration: str | None = None
    # The DC of a concentration save owed after damage, until rolled or settled.
    save_due: int | None = None
    timers: list[ConditionTimer] = Field(default_factory=list)
    repeat_saves: list[RepeatSave] = Field(default_factory=list)
    hidden: bool = False
    # Fights on the characters' side: rated with the party, earns no XP when it falls.
    # Characters are always on their own side and never carry it.
    ally: bool = False
    # A character's chosen target, a combatant id: set by its player on the fight view,
    # offered first when the gamemaster resolves the character's attack.
    target: str | None = None
    # A creature's spell slots and daily uses spent in this fight, by pool
    # (``MonsterSpell.pool``): ``{"slot:3": 1, "0:daily:1e:Fly": 1}``.
    spent: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_identity(self) -> Combatant:
        if self.ref is not None and self.character_id is not None:
            raise ValueError("a combatant is a monster or a character, not both")
        return self

    @property
    def is_character(self) -> bool:
        """True for a player character, whose state lives in its own file."""
        return self.character_id is not None

    @property
    def is_friend(self) -> bool:
        """On the characters' side: a character, or an ally."""
        return self.is_character or self.ally

    @property
    def is_down(self) -> bool:
        """A monster or ad-hoc combatant at 0 HP. Characters are read from their file."""
        return not self.is_character and self.hp_max > 0 and self.hp <= 0

    @property
    def health(self) -> Health:
        """The state word players see, and the row's stripe (DESIGN.md §4)."""
        return health_word(self.hp, self.hp_max)


class CombatLogEntry(GameStateModel):
    """One thing that happened in a fight, as facts: the tracker builds one for every
    event and writes it to the log; an attack or save effect also waits as ``pending``
    until the gamemaster resolves it."""

    at: datetime = Field(default_factory=_now)
    round: int = 0
    kind: CombatLogKind
    subject: str = ""
    amount: int | None = None
    detail: str = ""
    outcome: Literal["", "success", "failure", "crit", "fumble"] = ""
    # An attack's damage, and the dice behind the numbers ("d20 15 +4 · 1d6+2[3]").
    damage: int | None = None
    dice: str = ""
    # An attack's damage by type ({"slashing": 5, "poison": 7}), who made it, and what
    # became of it: the target's label once applied, "-" for a miss.
    damage_types: dict[str, int] = Field(default_factory=dict)
    actor: str = ""
    applied: str = ""
    # A save effect's ability ("dex") and whether a success halves the damage.
    save_ability: str = ""
    half: bool = False
    # The condition a failed save imposes, and for how many rounds (None: no timer).
    condition: str = ""
    rounds: int | None = None
    repeat: str = ""
    # An attack's damage dice, so a hit can still be made a critical: "1d6+2:slashing".
    damage_dice: str = ""
    # A player's weapon mastery on an attack, with its DC: "Topple:13".
    mastery: str = ""
    # Saves players rolled on their own phones for a save effect: combatant id → total.
    rolls: dict[str, int] = Field(default_factory=dict)
    by: str = ""
    # The log entry this pending item's verdict refers to (a player's attack rolled on
    # the sheet); empty for one the tracker rolled, whose id follows from its time.
    entry_id: str = ""


class Combat(Record):
    """One run of an encounter (documentation/COMBAT.md §2).

    ``round`` is 0 while initiative is being rolled. ``turn`` names the *group* whose
    turn it is rather than a position, because the order is derived from initiative
    and changes when an initiative does; the turn stays with its creature.
    """

    name: str
    encounter_id: str | None = None
    world_id: str | None = None
    round: int = Field(default=0, ge=0)
    turn: str | None = None
    combatants: list[Combatant] = Field(default_factory=list)
    # What still waits for the gamemaster, newest first: a rolled attack's Hit or Miss, a
    # save effect's targets. Resolved, an item leaves; what happened is in the log
    # (documentation/PROTOCOL.md §8).
    pending: list[CombatLogEntry] = Field(default_factory=list)
    # The fight's lines before the log: read from an older record, copied into the log
    # on start, never written again.
    log: SkipJsonSchema[list[CombatLogEntry]] = Field(default_factory=list, exclude=True)
    ended: datetime | None = None
    # Lair actions on initiative 20 while a creature with lair actions fights in its lair;
    # the gamemaster turns them off when the fight is elsewhere.
    lair: bool = True
    # XP handed to the characters after the end, once: how much each, and when.
    awarded_xp: int | None = Field(default=None, ge=0)
    awarded_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _pending_from_old_log(cls, data: Any) -> Any:
        """An older record kept its open attacks in its log: they become ``pending``."""
        if isinstance(data, dict) and data.get("log") and not data.get("pending"):
            data = dict(data)
            data["pending"] = [
                e
                for e in data["log"]
                if isinstance(e, dict)
                and e.get("kind") in ("attack", "save_effect")
                and e.get("damage") is not None
                and not e.get("applied")
            ]
        return data

    @property
    def running(self) -> bool:
        """True until the combat is ended."""
        return self.ended is None

    def combatant(self, combatant_id: str) -> Combatant | None:
        """The combatant with this id."""
        return next((c for c in self.combatants if c.id == combatant_id), None)


class SessionNote(Record):
    """What happened at the table on one day of play, in the gamemaster's words
    (documentation/ADVENTURES.md §4b). Markdown, so ``{@npc …}`` links; read by Claude
    through the MCP server's campaign memory."""

    world_id: str
    day: date
    # The adventure played that day, if one: its NPCs resolve in the text.
    adventure_id: str | None = None
    text: str = ""


INSTRUCTION_KINDS = ("adventure", "chapter", "npc", "encounter", "treasure", "place")


class Handout(Record):
    """Something the gamemaster handed to chosen characters (documentation/JOURNAL.md): a
    frozen copy of a box from a chapter, or of a picture, read in the players' journal."""

    world_id: str
    chapter_id: str | None = None
    kind: Literal["box", "image"]
    title: str = ""
    # A box: the callout's kind and its Markdown as it read when shared, its pictures
    # copied beside it and named by file name.
    callout: str = ""
    text: str = ""
    # A box of a published adventure's text instead: the book's entry as it read in the
    # sharer's language (5eTools JSON), drawn by the entries renderer.
    entry: dict[str, Any] | None = None
    # A picture: its file in the handout's folder, with its effects (``map.png|torn``).
    image: str = ""
    characters: list[str] = Field(default_factory=list)
    shared_by: str = ""
    # Who has opened it: usernames.
    read_by: list[str] = Field(default_factory=list)


class TableSettings(Record):
    """Settings of the whole table, one record with the id ``table``."""

    # The theme everyone reads Paravent in unless they chose their own
    # (documentation/THEMES.md); "" is Paravent's own look.
    theme: str = ""


class WorldInstructions(Record):
    """What Claude is told about a world before it writes for it (documentation/MCP.md §5):
    the setting, then one text per kind of thing it makes. Plain Markdown for Claude to
    read, never rendered for the table."""

    world_id: str
    setting: str = ""
    adventure: str = ""
    chapter: str = ""
    npc: str = ""
    encounter: str = ""
    treasure: str = ""
    place: str = ""


class McpToken(Record):
    """A key Claude connects to the MCP server with (documentation/MCP.md §3).

    Only its hash is kept; the token itself is shown once, when made. It acts as the
    gamemaster who made it.
    """

    name: str
    token_hash: str
    user_id: str
    last_used_at: datetime | None = None


class Session(Record):
    """A login session bound to a user."""

    user_id: str
    token: str
    expires_at: datetime
    role: Role = Role.PLAYER

    @property
    def is_expired(self) -> bool:
        """True once the session's expiry has passed."""
        return _now() >= self.expires_at


class SheetView(GameStateModel):
    """What a character sheet showed: its tab, its phone view, its spell-level filter."""

    tab: str = ""
    view: str = ""
    filter: str = ""


class Place(Record):
    """Where a user left the app, so logging in again carries on from there.

    One per user, keyed by the user's id: the last page read, the page each navigation
    section was left on (app/ui/nav.py), and how each character sheet was shown. Kept on
    the server rather than in the browser, so it survives a logout, a cleared browser
    and a change of device.
    """

    last: str = ""
    nav: dict[str, str] = Field(default_factory=dict)
    sheets: dict[str, SheetView] = Field(default_factory=dict)


class KnownFact(GameStateModel):
    """One thing a world's party knows about a kind of creature (COMBAT.md §5).

    Keyed in :class:`Knowledge` by an id: ``action:<name>`` for an attack or ability it
    was seen to use, ``damage:<type>`` for what that damage type did to it; revealed by
    the gamemaster also ``trait:<name>``, ``immune:<condition>``, ``ac`` and ``cr`` (as a
    band, never the number) and ``note``.
    """

    how: Literal["observed", "revealed"] = "observed"
    at: datetime = Field(default_factory=_now)
    # An action: the damage types it dealt and the save it called for, as seen.
    types: list[str] = Field(default_factory=list)
    save: str = ""
    # A damage type: what it did — half, nothing, double, or as dealt.
    effect: Literal["", "resist", "immune", "vulnerable", "normal"] = ""
    # A trait's text, the gamemaster's note, or a band ("medium") for AC and CR.
    text: str = ""


class Knowledge(Record):
    """What a world's party knows about kinds of creatures; the id is the world's
    (``global`` for fights in no world). Kept per kind ("Goblin|MM"), so the next fight
    with goblins starts from what was learned."""

    creatures: dict[str, dict[str, KnownFact]] = Field(default_factory=dict)


__all__ = [
    "Adventure",
    "Combat",
    "CombatLogEntry",
    "Combatant",
    "ConditionTimer",
    "CreatureBlock",
    "CreatureDesign",
    "CreatureFeature",
    "health_word",
    "Knowledge",
    "KnownFact",
    "Encounter",
    "EncounterMonster",
    "GameStateModel",
    "PartySpec",
    "Place",
    "Record",
    "Role",
    "Session",
    "SheetView",
    "User",
    "World",
]
