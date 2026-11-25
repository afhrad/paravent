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


HOMEBREW = "Homebrew"
_WORLD_HOMEBREW = "homebrew-"


def world_homebrew(world_id: str | None) -> str:
    """The source a world's builder designs publish under, ``Homebrew-<world id>``;
    plain ``Homebrew`` for a design of no world."""
    return f"{HOMEBREW}-{world_id}" if world_id else HOMEBREW


def is_world_homebrew(source: str | None) -> bool:
    """True for one world's own homebrew source (``Homebrew-<world id>``)."""
    return bool(source) and str(source).lower().startswith(_WORLD_HOMEBREW)


def is_homebrew(source: str | None) -> bool:
    """True for the table's own designs, of a world or of none."""
    return bool(source) and (str(source).lower() == HOMEBREW.lower() or is_world_homebrew(source))


def shown_source(source: str) -> str:
    """A source as the page prints it: a world's own designs read "Homebrew"."""
    return HOMEBREW if is_world_homebrew(source) else source


class SourceSet(GameStateModel):
    """Which books a world draws on.

    An **empty set means "everything imported"**. The tool has to work before anyone has
    configured it: a new user with a fresh corpus gets every entry resolvable, and
    narrows later if they want the correctness boundary that a declared set provides.

    A world's own homebrew source (``Homebrew-<world id>``, :func:`world_homebrew`) is
    legal only where declared, and narrows nothing: a world that declares no books but
    its own homebrew still reads every book, and never another world's designs.
    """

    sources: list[str] = Field(default_factory=list)

    def __len__(self) -> int:
        """How many sources are declared, a world's own homebrew among them (callers
        write ``source_set or SourceSet()``); ``is_unrestricted`` says whether any book
        is."""
        return len(self.sources)

    @property
    def books(self) -> list[str]:
        """The declared sources but a world's own homebrew."""
        return [s for s in self.sources if not is_world_homebrew(s)]

    @property
    def is_unrestricted(self) -> bool:
        """True when no book is declared, so every imported book is legal."""
        return not self.books

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
        if not self.sources:
            return "*"
        own = sorted(s for s in self.normalized if is_world_homebrew(s))
        return "|".join(["*", *own] if self.is_unrestricted else sorted(self.normalized))

    def allows(self, source: str | None) -> bool:
        """True when ``source`` is legal at this table.

        A missing source is allowed: some corpus records carry none, and excluding them
        would hide content for no stated reason. A world's own homebrew only where it
        is declared.
        """
        if source is None:
            return True
        if is_world_homebrew(source):
            return source.lower() in self.normalized
        return self.is_unrestricted or source.lower() in self.normalized

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
            if is_homebrew(source):  # the table's designs belong to no edition
                continue
            found.add(EDITION_ONE if source in one else EDITION_CLASSIC)
        return found or {EDITION_CLASSIC, EDITION_ONE}

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
    # Characters level when the gamemaster says (milestones), not by experience: no XP
    # is awarded or shown.
    milestone: bool = False
    # The declared books players may read (documentation/PLAYER-VIEW.md). None: the
    # player's handbooks and the supplements among them.
    player_sources: list[str] | None = None
    # The naming styles names are rolled from here, by key (``own:…``, ``starter:…``,
    # a book's table); empty means every style, like the books (NAMES.md §5b).
    naming_styles: list[str] = Field(default_factory=list)
    # The 2014 guide's optional combat rules the tracker applies here, by key
    # (app/services/combat_rules.py): ``massive_damage``, ``injuries``, ``morale``,
    # ``mobs``, ``side_initiative``. A fight keeps those on when it started.
    combat_rules: list[str] = Field(default_factory=list)
    # The guides' variant rules for characters here, by key
    # (app/services/character_rules.py): a rest variant, hero points, Honor, Sanity,
    # slow natural healing, healer's kit dependency, defeated not dead.
    character_rules: list[str] = Field(default_factory=list)
    # The features played with here, by key (app/services/features.py): the road,
    # survival, dungeon checks, shops, lifestyle, followers, downtime, bastions, quests,
    # factions, chases, madness, safety tools. None: every one (a world from before).
    features: list[str] | None = None
    # The pantheons whose deities the characters worship here, by the books' names
    # ("Forgotten Realms", "Dwarven"); empty means every pantheon of the world's books.
    pantheons: list[str] = Field(default_factory=list)

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
    # The world's ready-made NPCs, places, encounters and treasure it uses, by id; they
    # stay the world's (app/services/holdings.py).
    uses: list[str] = Field(default_factory=list)

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


# How someone of the campaign stands toward the party (the guides' attitudes).
Attitude = Literal["friendly", "indifferent", "hostile"]


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
    # How they stand toward the party, and when they last met it by the world's clock
    # (minutes since Day 1, 00:00); None while nobody said (app/ui/attitude.py).
    attitude: Attitude | None = None
    last_seen: int | None = None


class Location(Record):
    """A place of the campaign — a village, a cave, a tavern — written ``{@place Name}``
    (documentation/ADVENTURES.md §4). Called Location here because :class:`Place` is
    already where a user left off. Who and what is there, the notes say: ``{@npc}``,
    ``{@encounter}`` and ``{@treasure}`` in them are what the place's page lists."""

    scope: Scope
    name: str
    tags: list[str] = Field(default_factory=list)
    # Its title image, a path relative to the scope's record folder, e.g.
    # ``media/grey-reed.webp`` (app/services/title_images.py).
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


ItemKind = Literal[
    "weapon",
    "armor",
    "shield",
    "ammunition",
    "staff",
    "wondrous",
    "ring",
    "rod",
    "wand",
    "potion",
    "scroll",
]
Rarity = Literal["common", "uncommon", "rare", "very rare", "legendary", "artifact"]


class ItemSpell(GameStateModel):
    """A spell an item casts: ``Name|SOURCE``, at will (``charges`` 0) or for so many."""

    spell: str = Field(max_length=200)
    charges: int = Field(default=0, ge=0, le=20)


class ItemDesign(Record):
    """A magic item made in the item builder (app/services/item_builder.py).

    Only what the character sheet applies is typed — the weapon, armour and other
    bonuses, damage defences, ability scores, charges and the spells it casts; anything
    else it does is its text, for the table. Published, it becomes an item in
    ``library/homebrew/items/items-paravent.json`` under the source ``Homebrew``. A
    weapon, armour, shield, ammunition or staff takes its base item's numbers.
    """

    name: str
    world_id: str | None = None
    based_on: str | None = None  # ``Name|SOURCE`` of the printed item it began from
    kind: ItemKind = "wondrous"
    base_item: str = ""  # ``Name|SOURCE`` of the base weapon, armour or shield
    rarity: Rarity = "uncommon"
    attunement: bool = False
    attune_by: str = Field(default="", max_length=120)  # "by a wizard"
    bonus_weapon: int = Field(default=0, ge=0, le=3)
    bonus_ac: int = Field(default=0, ge=0, le=3)
    bonus_saving_throw: int = Field(default=0, ge=0, le=3)
    bonus_spell_attack: int = Field(default=0, ge=0, le=3)
    bonus_spell_save_dc: int = Field(default=0, ge=0, le=3)
    bonus_ability_check: int = Field(default=0, ge=0, le=3)
    resist: list[str] = Field(default_factory=list)
    immune: list[str] = Field(default_factory=list)
    vulnerable: list[str] = Field(default_factory=list)
    ability_set: dict[str, int] = Field(default_factory=dict)  # "str": 19
    ability_raise: dict[str, int] = Field(default_factory=dict)  # "con": 2, attuned
    charges: int = Field(default=0, ge=0, le=100)
    recharge: Literal["", "dawn", "dusk", "midnight", "restLong"] = ""
    recharge_amount: str = Field(default="", max_length=40)  # "1d6+1", "" for all
    spells: list[ItemSpell] = Field(default_factory=list)
    text: str = Field(default="", max_length=8000)
    weight: float | None = Field(default=None, ge=0, le=10000)
    value: int | None = Field(default=None, ge=0)  # copper; None for the rarity's
    published_at: datetime | None = None
    published_hash: str | None = None
    # The MCP key that drafted it: Claude's draft, left out of a publish until the
    # gamemaster accepts it; None for the gamemaster's own designs.
    drafted_by: str | None = None
    # A revision: Claude's draft copy of the accepted design with this id, under the
    # same name; accepting it writes it onto that design.
    revision_of: str | None = None


class SpellDesign(Record):
    """A spell made in the spell builder (app/services/spell_builder.py).

    Typed is what casting it from the sheet uses — its header (level, school, casting
    time, range and area, components, duration, concentration, ritual), the class lists
    it is on, and its effect: a save or a spell attack with damage, or healing, with
    what a higher slot adds (a cantrip's damage grows with the caster's level). The
    builder writes the effect's sentence and its "at higher levels" in the world's
    language; ``text`` is everything else. Published as source ``Homebrew``.
    """

    name: str
    world_id: str | None = None
    based_on: str | None = None  # ``Name|SOURCE`` of the printed spell it began from
    level: int = Field(default=1, ge=0, le=9)
    school: Literal["A", "C", "D", "E", "V", "I", "N", "T"] = "V"
    time_number: int = Field(default=1, ge=1, le=99)
    time_unit: Literal["action", "bonus", "reaction", "minute", "hour"] = "action"
    range_kind: Literal["feet", "miles", "self", "touch", "sight", "unlimited"] = "feet"
    range_amount: int = Field(default=60, ge=0, le=100000)
    area: Literal["", "cone", "cube", "cylinder", "line", "sphere", "emanation"] = ""
    area_size: int = Field(default=0, ge=0, le=10000)  # feet
    verbal: bool = True
    somatic: bool = True
    material: str = Field(default="", max_length=300)  # "" for none
    material_cost: int = Field(default=0, ge=0)  # gp
    material_consumed: bool = False
    duration_kind: Literal["instant", "timed", "permanent", "special"] = "instant"
    duration_amount: int = Field(default=1, ge=1, le=999)
    duration_unit: Literal["round", "minute", "hour", "day"] = "minute"
    concentration: bool = False
    ritual: bool = False
    classes: list[str] = Field(default_factory=list)  # "Wizard|PHB"
    effect: Literal["none", "save", "attack", "heal"] = "none"
    save: Literal["str", "dex", "con", "int", "wis", "cha"] = "dex"
    half_on_save: bool = True
    attack: Literal["M", "R"] = "R"
    dice: str = Field(default="", max_length=40)  # "8d6"
    damage_types: list[str] = Field(default_factory=list)
    heal_mod: bool = True
    heal_targets: int = Field(default=1, ge=1, le=12)
    upcast: str = Field(default="", max_length=40)  # what each slot above adds, "1d6"
    # Whether the builder writes the effect's sentence (off for a printed spell's text,
    # which already says it).
    write_effect: bool = True
    text: str = Field(default="", max_length=8000)
    higher_text: str = Field(default="", max_length=2000)
    published_at: datetime | None = None
    published_hash: str | None = None
    drafted_by: str | None = None
    revision_of: str | None = None  # Claude's draft copy of the accepted design with this id


class BackgroundItem(GameStateModel):
    """A piece of a background's starting equipment: an item of the books by
    ``Name|SOURCE``, or something the books do not list (``special``), so many of it."""

    item: str = Field(default="", max_length=200)
    special: str = Field(default="", max_length=200)
    quantity: int = Field(default=1, ge=1, le=999)


class BackgroundDesign(Record):
    """A background made in the background builder (app/services/background_builder.py).

    Typed is what the character wizard reads: the skills, tools and languages it gives
    (fixed, or so many of the player's choice), its starting equipment and gold, a feature
    (2014), and for the 2024 rules the three abilities its +2/+1 (or +1/+1/+1) falls on and
    its origin feat. Its rules edition follows ``edition``. Published as source
    ``Homebrew``, the wizard offers it beside the books' own.
    """

    name: str
    world_id: str | None = None
    based_on: str | None = None
    edition: Literal["classic", "one"] = "one"
    skills: list[str] = Field(default_factory=list)  # "insight"
    any_skills: int = Field(default=0, ge=0, le=4)
    # So many skills the player chooses from these ("choose two from Arcana, History…").
    skill_choices: list[str] = Field(default_factory=list)
    choose_skills: int = Field(default=0, ge=0, le=4)
    tools: list[str] = Field(default_factory=list)  # "thieves' tools"
    any_artisan: int = Field(default=0, ge=0, le=3)
    any_gaming: int = Field(default=0, ge=0, le=3)
    any_instrument: int = Field(default=0, ge=0, le=3)
    languages: list[str] = Field(default_factory=list)  # "elvish"
    any_languages: int = Field(default=0, ge=0, le=4)
    equipment: list[BackgroundItem] = Field(default_factory=list)
    gold: int = Field(default=0, ge=0, le=100000)  # gp in the pouch, or option A's
    abilities: list[str] = Field(default_factory=list)  # three, 2024
    feat: str = Field(default="", max_length=200)  # "Alert|XPHB", 2024
    feature_name: str = Field(default="", max_length=120)  # 2014
    feature_text: str = Field(default="", max_length=4000)
    text: str = Field(default="", max_length=4000)
    published_at: datetime | None = None
    published_hash: str | None = None
    drafted_by: str | None = None
    revision_of: str | None = None  # Claude's draft copy of the accepted design with this id


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
    # Its title image, in its world's ``media/`` folder (app/services/title_images.py).
    cover: str | None = None

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
    "form_on",
    "form_off",
    # An optional combat rule at work (app/services/combat_rules.py): what in ``detail``.
    "rule",
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


class SpellForm(GameStateModel):
    """A shape a spell holds a creature in (Polymorph, True Polymorph), on a character's
    state or a combatant, until the caster's concentration on ``spell`` ends.

    ``hp`` is the form's own hit points under the 2014 rules, taking damage before the
    creature's (the rest carries over at 0, which ends the form); under the 2024 rules
    the form gives temporary hit points instead and ends when they are gone (None).
    ``caster`` names who concentrates: ``character:<id>``, or ``combatant:<id>`` within
    the same fight.
    """

    form: str
    spell: str
    caster: str = ""
    hp: int | None = Field(default=None, ge=0)


class Summoned(GameStateModel):
    """A creature a spell summoned (Summon Beast, Find Steed): the spell, the level it was
    cast with, the variant (``Air``), and the caster (``character:<id>``,
    ``combatant:<id>``) with the spell attack bonus and save DC it had then. The
    statblock is the corpus's, scaled to these (app/services/summons.py)."""

    spell: str
    level: int = Field(ge=1, le=9)
    variant: str = ""
    caster: str = ""
    attack: int | None = None
    dc: int | None = None


class Thing(GameStateModel):
    """What makes a combatant an object (app/services/objects.py): a door, an object of
    a substance and size, or siege equipment. It takes no turn and earns no XP."""

    kind: Literal["object", "door", "siege"] = "object"
    # The DC to force a door open, where the guide gives one.
    dc: int | None = Field(default=None, ge=0, le=40)
    # A siege weapon's object in the corpus (``Ballista|DMG``): its statblock and attacks.
    ref: str = ""


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
    # A monster turned into another creature by a spell; a character's form is in its file.
    form: SpellForm | None = None
    # A creature a spell summoned, scaled to the spell's level.
    summon: Summoned | None = None
    # A character's follower (app/services/followers.py): its hit points go back to the
    # follower when the fight ends.
    follower_id: str | None = None
    # An object or a door to break (app/services/objects.py), not a creature.
    thing: Thing | None = None

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
    def shown_ref(self) -> str | None:
        """The statblock it fights with: the shape a spell holds it in, else its own."""
        return self.form.form if self.form is not None else self.ref

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
    # The world's optional combat rules as the fight started (World.combat_rules).
    rules: list[str] = Field(default_factory=list)
    # Side initiative: each side's one roll, ``{"friend": 14, "foe": 9}``.
    sides: dict[str, int] = Field(default_factory=dict)
    # Morale: the groups whose nerve was already tested (once each).
    morale: list[str] = Field(default_factory=list)
    # Lingering injuries offered and not yet rolled or waved off: combatant ids, and why
    # (``crit`` or ``down``).
    injuries: dict[str, str] = Field(default_factory=dict)

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
    # The world the whole table plays in (documentation/CURRENT-WORLD.md); None before
    # one is chosen, which reads as the first world by name.
    world_id: str | None = None


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
    # The newest "What's new" line the user has dismissed (its time, app/services/news.py).
    news_seen: str = ""


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


class BastionFacility(GameStateModel):
    """A special facility of a bastion (``Smithy|XDMG``) and the order it carries out
    on the next bastion turn ("craft"; "" for none). ``shut`` after an attack it was
    damaged in: it takes no order on the next turn."""

    ref: str
    order: str = ""
    shut: bool = False
    # The option the order carries out (app/services/bastion_orders.py): its index
    # among the order's options, and for crafting by a tool ``"<n>|<item>"``.
    option: str = ""
    # The tools it was equipped with when added (a Workshop's six Artisan's Tools,
    # ``"Carpenter's Tools|XPHB"``): what its hirelings craft with.
    tools: list[str] = Field(default_factory=list)


class BasicFacility(GameStateModel):
    """A basic facility of a bastion (``Kitchen|XDMG``), its space, and when building or
    enlarging it is done (the world's clock in minutes; None: it stands)."""

    ref: str
    space: Literal["cramped", "roomy", "vast"] = "roomy"
    ready: int | None = None


class Bastion(Record):
    """A character's bastion (2024 rules), from level 5: its name, its special
    facilities with their orders, and how many bastion turns it has had. The id is the
    character's."""

    name: str = ""
    facilities: list[BastionFacility] = Field(default_factory=list)
    turns: int = Field(default=0, ge=0)
    # The world's clock (in minutes, app/services/clock.py) at the last turn, which
    # tells when the next is due: one every seven days.
    last_turn: int | None = None
    basic: list[BasicFacility] = Field(default_factory=list)
    # Bastion Defenders on the roster; an attack may cost some.
    defenders: int = Field(default=0, ge=0, le=999)
    # The Maintain order for the next turn: no facility orders, a bastion event instead.
    maintain: bool = False
    # The event the last Maintain turn rolled: ``"<roll>|<book entry id>|<name>"``.
    event: str = ""
    # What the last turn's orders yielded, a line each: ``"<facility>|<kind>|<what>"``.
    yields: list[str] = Field(default_factory=list)
    # Crafts still under way: ``"<clock minute ready>|<item>"``.
    pending: list[str] = Field(default_factory=list)
    # What the last event brought, a line each (app/services/bastion_events.py):
    # ``"gold|<copper>"``, ``"defender|1"``, ``"shut|<facility>"``, ``"item|<ref>"``,
    # ``"detail|<text>"``, ``"guest|<text>"``, ``"aid|<total>|<copper>|<lost>"``.
    event_yields: list[str] = Field(default_factory=list)
    # A decision the last event left to the player: ``"bribe|<copper>"``,
    # ``"opportunity|<copper>"``, ``"aid"`` or ``"discovery"``; "" for none.
    offer: str = ""
    # A friendly monster staying as a guest: it defends the bastion from the next attack.
    guardian: bool = False


class SafetySuggestion(GameStateModel):
    """A line or veil a player put forward, without a name."""

    kind: Literal["line", "veil"]
    text: str = Field(max_length=300)


class ExpectationSheet(GameStateModel):
    """One player's game expectations sheet (the 2024 guide's), kept without a name:
    which of the potentially sensitive elements are a hard (X) or soft (?) limit,
    anything else to avoid, hopes and expectations, and concerns about the table."""

    limits: dict[str, Literal["hard", "soft"]] = Field(default_factory=dict)
    avoid: str = Field(default="", max_length=1000)
    hopes: str = Field(default="", max_length=2000)
    concerns: str = Field(default="", max_length=2000)


Together = Literal["", "bonding", "happenstance", "acquaintance", "history", "tavern"]


class TableSafety(Record):
    """A world's table agreements (session zero): lines — what does not happen in the
    game — and veils — what happens off screen, one per entry, as the gamemaster keeps
    them; and what players suggested anonymously, until the gamemaster takes or drops
    it. The gamemaster's half of session zero (app/services/safety.py): the game's
    theme and flavor, the potentially sensitive elements, the starting location and
    key events, house rules, how the party came together and the starting level; and
    the players' expectations sheets, by an anonymous token each. The id is the
    world's."""

    lines: list[str] = Field(default_factory=list)
    veils: list[str] = Field(default_factory=list)
    suggested: list[SafetySuggestion] = Field(default_factory=list)
    theme: str = Field(default="", max_length=4000)
    sensitive: list[str] = Field(default_factory=list)
    start: str = Field(default="", max_length=4000)
    house_rules: str = Field(default="", max_length=4000)
    together: Together = ""
    together_note: str = Field(default="", max_length=2000)
    starting_level: int = Field(default=0, ge=0, le=20)  # 0: not said
    sheets: dict[str, ExpectationSheet] = Field(default_factory=dict)


class ClockTimer(GameStateModel):
    """Something that runs out on a world's clock: a lit torch or lantern (``light``,
    the item's reference), a spell cast with a duration (``spell``, its reference) or
    the gamemaster's own reminder (``custom``, its text). ``ends`` is the clock's
    minute it runs out at; ``concentration`` ends the caster's concentration with it."""

    id: str
    kind: Literal["light", "spell", "custom"]
    label: str = Field(max_length=200)
    character_id: str | None = None
    ends: int
    concentration: bool = False


class Journey(GameStateModel):
    """The party's last stretch of travel (app/services/travel.py): when it set out and
    stopped, its pace, the travelling hours planned and covered, the distance, the
    rations eaten and who went hungry, and the encounter that stopped it, if any."""

    started: int
    ended: int
    pace: Literal["slow", "normal", "fast"] = "normal"
    planned: int = Field(default=0, ge=0)
    hours: int = Field(default=0, ge=0)
    miles: float = Field(default=0, ge=0)
    difficult: bool = False
    environment: str = ""
    rations: int = Field(default=0, ge=0)
    hungry: list[str] = Field(default_factory=list)
    exhausted: list[str] = Field(default_factory=list)
    # The encounter check (a d20) that came up, the table's roll and the row it read.
    check: int | None = None
    encounter: str = ""
    encounter_roll: int | None = None
    monsters: list[EncounterMonster] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    encounter_id: str | None = None
    # Water for each day, and who went short of it.
    water: Literal["enough", "half", "none"] = "enough"
    thirsty: list[str] = Field(default_factory=list)
    # The weather of each day rolled: ``"<temperature>|<wind>|<precipitation>"``.
    weather: list[str] = Field(default_factory=list)
    mounted: bool = False
    # The vessel the party travelled on ("" on foot or mounted).
    vessel: str = ""
    # The encounter came in the night, not on the road.
    night: bool = False
    # Extreme cold or heat the party travelled through, and high altitude
    # (app/services/travel.py); who the exposure cost exhaustion, ``"<name>:<levels>"``.
    climate: Literal["", "cold", "heat"] = ""
    altitude: bool = False
    exposed: list[str] = Field(default_factory=list)
    # The terrain (a row of the 2024 *Travel Terrain* table's names), and whether it held
    # the party to a slower pace than chosen.
    terrain: str = ""
    slowed: bool = False
    # The navigator, each Wisdom (Survival) check ``"<total>/<DC>"``, the days the party
    # lost its way and the miles of the road that went astray.
    navigator: str = ""
    navigation: list[str] = Field(default_factory=list)
    lost_days: int = Field(default=0, ge=0)
    astray: float = Field(default=0, ge=0)
    # Foraged: pounds of food and gallons of water found, and the meals they made.
    foraged_food: int = Field(default=0, ge=0)
    foraged_water: int = Field(default=0, ge=0)
    foraged_meals: int = Field(default=0, ge=0)
    # When an encounter came up: its distance in feet, the creatures' lowest Stealth and
    # who it would surprise if they lay in wait, and, for a party moving stealthily, its
    # lowest Stealth and the creatures it would catch unaware.
    distance: int | None = None
    lurk_stealth: int | None = None
    ambush: list[str] = Field(default_factory=list)
    party_stealth: int | None = None
    unaware: list[str] = Field(default_factory=list)


class DelveFinding(GameStateModel):
    """An encounter check that came up while exploring (app/services/delve.py): the
    clock's minute, the d20, and the table's roll, row and creatures, as a journey has."""

    at: int = Field(ge=0)
    check: int = Field(ge=1, le=20)
    encounter: str = ""
    encounter_roll: int | None = None
    monsters: list[EncounterMonster] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    encounter_id: str | None = None


class Delve(GameStateModel):
    """The party exploring a dungeon (app/services/delve.py): an encounter check every
    ``every`` minutes the world's clock passes, coming up on a d20 of ``on`` or more,
    on the encounter table ``environment`` (its key; "" leaves the creatures to the
    gamemaster). ``checked`` is the minute of the last check, ``rolled`` how many were
    rolled; a check that came up waits in ``found`` and holds further checks until the
    gamemaster deals with it."""

    environment: str = ""
    every: int = Field(default=10, ge=1, le=7 * 24 * 60)
    on: int = Field(default=18, ge=2, le=20)
    started: int = Field(default=0, ge=0)
    checked: int = Field(default=0, ge=0)
    rolled: int = Field(default=0, ge=0)
    found: DelveFinding | None = None


class WorldClock(Record):
    """A world's time in the game: minutes since midnight of its first day, and the
    timers running against it. The id is the world's. ``hungry`` counts, per character,
    the days in a row it has gone without food on the road."""

    minute: int = Field(default=8 * 60, ge=0)
    timers: list[ClockTimer] = Field(default_factory=list)
    journey: Journey | None = None
    hungry: dict[str, int] = Field(default_factory=dict)
    # Minutes already travelled on the travelling day ``road_day`` (a day index), when
    # the last stretch stopped partway through it.
    road: int = Field(default=0, ge=0)
    road_day: int | None = None
    # The setting's calendar its days are dated by (app/services/calendar.py): the
    # caption of the books' table ("" for plain day numbers), the calendar's day index
    # of the clock's first day, and the era written after the year ("DR").
    calendar: str = ""
    calendar_epoch: int = 0
    calendar_era: str = Field(default="", max_length=12)
    # The epoch counts leap days (Shieldmeet); one set before they were counted is
    # converted once when read.
    calendar_leaps: bool = False
    # The last day whose lifestyle and wages were paid: a clock set back and moved on
    # again does not charge those days twice.
    charged_day: int | None = None
    # The party exploring a dungeon, its encounter checks run by the clock.
    delve: Delve | None = None
    # The party in a wild magic zone: a spell slot spent there may surge
    # (app/services/wild_magic.py).
    wild_magic: bool = False
    # The plane the party is on ("" the Material Plane) and since when, the minute up to
    # which its hourly rules have run, and per rule the characters it spares
    # (app/services/planes.py).
    plane: str = ""
    plane_since: int = Field(default=0, ge=0)
    plane_hour: int = Field(default=0, ge=0)
    plane_spared: dict[str, list[str]] = Field(default_factory=dict)


class DowntimeProject(GameStateModel):
    """Something a character spends downtime days on (app/services/downtime.py):
    crafting an item or a magic item (``enchant``; ``label`` its reference), training in
    a language or tool (its reference), research, recuperating, working, or anything
    else (``label`` its text). ``needed`` is the days it takes (None: as long as it is
    kept up); ``paid`` the copper spent on it so far."""

    id: str
    kind: Literal[
        "craft",
        "train",
        "research",
        "recuperate",
        "work",
        "other",
        "scribe",
        "carouse",
        "crime",
        "gamble",
        "pit_fight",
        "service",
        "study",
        "job",
        "sell_magic",
        "buy_magic",
        "enchant",
    ]
    label: str = Field(default="", max_length=200)
    needed: int | None = Field(default=None, ge=1)
    done: int = Field(default=0, ge=0)
    paid: int = Field(default=0, ge=0)
    finished: bool = False
    # What a day of it costs in copper (None: the kind's own rate).
    daily: int | None = Field(default=None, ge=0)
    # An activity's outcome, line by line (app/services/activities.py).
    result: list[str] = Field(default_factory=list)
    # What waits to be taken: items offered for sale, or an offer for an item.
    offers: list[str] = Field(default_factory=list)


class Downtime(Record):
    """A character's downtime: the days it has free to spend, and its projects. The id
    is the character's."""

    days: int = Field(default=0, ge=0)
    projects: list[DowntimeProject] = Field(default_factory=list)


class ShopItem(GameStateModel):
    """A magic item the gamemaster put on a shop's shelf: its reference, its price in
    copper before the shop's markup, and how many are left (None: no end of them)."""

    ref: str
    price: int = Field(ge=0)
    count: int | None = Field(default=None, ge=0, le=999)


class Shop(Record):
    """A shop the gamemaster opens in a world for its characters (or those ticked):
    priced mundane gear at ``markup`` percent of the list price, buying back at
    ``buys`` percent, and the magic items put on its shelf. The id is the world's."""

    open: bool = False
    name: str = Field(default="", max_length=80)
    characters: list[str] = Field(default_factory=list)
    markup: int = Field(default=100, ge=10, le=1000)
    buys: int = Field(default=50, ge=0, le=100)
    stock: list[ShopItem] = Field(default_factory=list)


class Contact(Record):
    """Someone a character knows (app/services/contacts.py): an ally or enemy made
    carousing, a rival behind a complication, a favour a temple owes, or anyone the
    player writes down. ``spent`` once called on (a favour used, an ally asked)."""

    character_id: str
    world_id: str | None = None
    kind: Literal["ally", "enemy", "rival", "favour", "contact"] = "contact"
    name: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=2000)
    source: str = ""  # the downtime activity it came from ("carouse", "service")
    spent: bool = False
    faction_id: str | None = None  # the faction the contact belongs to
    # How they stand toward the party, and when last seen by the world's clock.
    attitude: Attitude | None = None
    last_seen: int | None = None


MarkKind = Literal["title", "land", "stronghold", "medal", "letter", "favor", "right", "training"]


class Mark(Record):
    """A mark of prestige the guides describe (app/services/prestige.py): a title, a
    parcel of land, a stronghold, a medal, a letter of recommendation, a special favor
    or right, or training, granted to a character by someone of standing. ``spent``
    once a favor is called in."""

    character_id: str
    world_id: str | None = None
    kind: MarkKind
    name: str = Field(max_length=160)
    granted_by: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=2000)
    spent: bool = False


class Property(Record):
    """A business, stronghold or garrison a character owns, and what it costs each day
    to run (app/services/upkeep.py): ``kind`` the property of the guide's *Maintenance
    Costs* table it is priced by ("" for a price of the table's own), ``per_day`` in
    copper, paid from the owner's purse each day the world's clock passes."""

    world_id: str
    character_id: str
    name: str = Field(max_length=120)
    kind: str = Field(default="", max_length=120)
    per_day: int = Field(default=0, ge=0, le=100_000_000)
    note: str = Field(default="", max_length=2000)


class ChaseRunner(GameStateModel):
    """One participant of a chase (app/services/chase.py): a character of the world or
    a creature, on one side, in initiative order, with what the chase needs to know of
    it and what the chase has cost it."""

    name: str
    side: Literal["quarry", "pursuer"]
    character_id: str | None = None
    monster: str = ""  # a creature's reference, ``"Thug|MM"``
    initiative: int = 0
    con_mod: int = 0
    stealth: int = 0
    passive: int = 10
    dashes: int = Field(default=0, ge=0)
    free_dashes: int = Field(default=3, ge=0)
    exhaustion: int = Field(default=0, ge=0, le=6)  # levels gained during the chase
    out: bool = False  # dropped out (five levels of exhaustion, or by the gamemaster)
    escaped: bool = False
    complication: str = ""  # what hit it on the last turn before its own
    # The same complication in each language that translates the table, by language.
    complication_texts: dict[str, str] = Field(default_factory=dict)


class Chase(Record):
    """A chase in a world (the guide's chase rules), run from the party view; the id is
    the world's. ``turn`` indexes the runner whose turn it is; ``over`` is "" while it
    runs, else how it ended (``escaped``, ``caught``, ``stopped``)."""

    environment: Literal["urban", "wilderness"] = "urban"
    rules: Literal["classic", "one"] = "classic"
    distance: int = Field(default=30, ge=0, le=10_000)
    round: int = Field(default=1, ge=1)
    turn: int = Field(default=0, ge=0)
    runners: list[ChaseRunner] = Field(default_factory=list)
    over: str = ""


class Follower(Record):
    """Someone who follows a character (app/services/followers.py): a hireling paid by
    the day, or a creature of the books — a companion, a mount or a sidekick — with its
    own hit points. A sidekick's levels raise its hit points."""

    character_id: str
    world_id: str | None = None
    kind: Literal["skilled", "untrained", "companion", "mount", "sidekick"]
    name: str = Field(max_length=120)
    note: str = Field(default="", max_length=2000)
    monster: str = ""  # a creature's reference, ``"Riding Horse|XMM"``
    hp: int = Field(default=0, ge=0)
    hp_max: int = Field(default=0, ge=0)
    levels: int = Field(default=0, ge=0, le=20)  # a sidekick's levels gained
    # A sidekick's class of the books (Tasha's "Warrior Sidekick|TCE"), "" for none.
    sidekick_class: str = ""
    wage: int = Field(default=0, ge=0)  # copper a day, for a hireling
    # The guides' Loyalty Score, kept by the gamemaster in secret; None while untracked.
    loyalty: int | None = Field(default=None, ge=0, le=20)
    # The vessel it crews ("The Grey Heron"), "" for none: its crew's loyalty decides a
    # mutiny (the 2024 guide's Crew Loyalty and Mutiny).
    ship: str = Field(default="", max_length=80)


class QuestStep(GameStateModel):
    """A step of a quest, ticked off when done."""

    text: str = Field(max_length=500)
    done: bool = False


class QuestItem(GameStateModel):
    """An item a quest rewards: ``"Name|SOURCE"`` and how many."""

    ref: str = Field(max_length=200)
    count: int = Field(default=1, ge=1, le=999)


class Quest(Record):
    """A quest of a world (app/services/quests.py): who gave it, what it asks, its steps
    and its reward, handed out when the gamemaster marks it done. ``characters`` are
    those on it (none: the world's party); ``shown`` lets the players read it."""

    world_id: str
    name: str = Field(max_length=160)
    giver: str = Field(default="", max_length=160)
    faction_id: str | None = None  # the faction it serves; its renown is the reward
    text: str = Field(default="", max_length=8000)
    steps: list[QuestStep] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    shown: bool = True
    status: Literal["open", "done", "failed"] = "open"
    xp: int = Field(default=0, ge=0, le=1_000_000)  # for each character
    coins: int = Field(default=0, ge=0, le=100_000_000)  # copper, shared out evenly
    items: list[QuestItem] = Field(default_factory=list)  # each to the first on it
    renown: int = Field(default=0, ge=0, le=50)  # with the faction, for each character
    # A mark of prestige for each character on it ("medal", "Golden Bear"); "" for none.
    mark_kind: MarkKind | Literal[""] = ""
    mark_name: str = Field(default="", max_length=160)
    # The draft adventure Claude wrote it for (app/mcp/drafts.py); hidden from the
    # players until the gamemaster shows it.
    adventure_id: str | None = None
    # Its title image, in its world's ``media/`` folder (app/services/title_images.py).
    cover: str | None = None


class FactionRank(GameStateModel):
    """A title a faction gives from a renown on ("Harpshadow" at 3)."""

    renown: int = Field(ge=0, le=1000)
    title: str = Field(max_length=80)


class Faction(Record):
    """A faction of a world (app/services/factions.py): its name and the gamemaster's
    note, the book's renown table its rewards come from (``"Dale Renown|FRAiF"``), the
    titles it gives, and each character's renown with it."""

    world_id: str
    name: str = Field(max_length=120)
    note: str = Field(default="", max_length=4000)
    table: str = ""
    ranks: list[FactionRank] = Field(default_factory=list)
    renown: dict[str, int] = Field(default_factory=dict)  # by character id
    # Its title image, an emblem or banner, in its world's ``media/`` folder.
    cover: str | None = None


class PlaySession(Record):
    """A day of play in a world, from the gamemaster's "Start the session" to "End the
    session" (app/services/play_session.py); the id is the world's. ``minute`` is the
    world's clock when it began."""

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    minute: int = 0
    ended_at: datetime | None = None
    note_id: str | None = None  # the session note written when it ended


class PartyNotes(Record):
    """The notes a world's party keeps together, written by its players and the
    gamemaster; the id is the world's."""

    text: str = ""
    by: str = ""


class CheckCall(Record):
    """A check the gamemaster asks characters for outside a fight ("everyone roll
    Perception"): what to roll (``skill``, ``check`` of an ability, or ``save``), a DC
    the players do not see, whether it is a group check (passed when at least half
    succeed) or rolled secretly by the gamemaster, whom it asks, and their totals as
    they come in. ``open`` until the gamemaster closes it."""

    world_id: str | None = None
    kind: Literal["skill", "check", "save"]
    subject: str
    dc: int | None = Field(default=None, ge=1, le=40)
    group: bool = False
    secret: bool = False
    characters: list[str] = Field(default_factory=list)
    results: dict[str, int] = Field(default_factory=dict)
    by: str = ""
    open: bool = True
    # What the call is for, shown with it (a trap's name when its saves are the
    # players' to roll); "" for a plain call.
    purpose: str = Field(default="", max_length=200)

    def passed(self, character_id: str) -> bool | None:
        """Whether one character's total met the DC; None without a DC or a total."""
        total = self.results.get(character_id)
        return None if self.dc is None or total is None else total >= self.dc

    @property
    def group_passed(self) -> bool | None:
        """A group check: at least half of those asked succeeded; None until all rolled
        or without a DC."""
        if self.dc is None or len(self.results) < len(self.characters) or not self.characters:
            return None
        made = sum(1 for cid in self.characters if self.passed(cid))
        return made * 2 >= len(self.characters)


class TrashedObject(Record):
    """An NPC, place, treasure, encounter, quest or faction deleted, kept for a while to
    restore (app/services/trash.py): the record as it was, its world and when it went. A
    faction also keeps the quests and contacts it was unlinked from (``links``). A chapter
    or an adventure keeps only where its file went in ``library/.trash/`` and its parent
    (``record``: ``kept``, ``parent_id``); what was deleted with it names it in
    ``part_of`` and comes back with it."""

    kind: Literal[
        "npc",
        "place",
        "treasure",
        "encounter",
        "quest",
        "faction",
        "chapter",
        "adventure",
        "world",
    ]
    world_id: str
    name: str
    record: dict[str, Any]
    part_of: str | None = None
    links: dict[str, list[str]] = Field(default_factory=dict)
    # What a chapter's or adventure's delete moved up to its adventure or world:
    # ``collection``, ``id``, ``from`` and ``to`` scopes; put back on a restore.
    lifted: list[dict[str, Any]] = Field(default_factory=list)
    deleted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Knowledge(Record):
    """What a world's party knows about kinds of creatures; the id is the world's
    (``global`` for fights in no world). Kept per kind ("Goblin|MM"), so the next fight
    with goblins starts from what was learned."""

    creatures: dict[str, dict[str, KnownFact]] = Field(default_factory=dict)


__all__ = [
    "HOMEBREW",
    "is_homebrew",
    "is_world_homebrew",
    "shown_source",
    "world_homebrew",
    "TrashedObject",
    "Adventure",
    "Combat",
    "CombatLogEntry",
    "Combatant",
    "ConditionTimer",
    "CreatureBlock",
    "CreatureDesign",
    "CreatureFeature",
    "health_word",
    "CheckCall",
    "ClockTimer",
    "Downtime",
    "DowntimeProject",
    "Journey",
    "Shop",
    "ShopItem",
    "WorldClock",
    "Knowledge",
    "PartyNotes",
    "SafetySuggestion",
    "Bastion",
    "BastionFacility",
    "BasicFacility",
    "TableSafety",
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
