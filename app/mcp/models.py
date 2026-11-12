"""What the MCP tools answer with (documentation/MCP.md §4).

Pydantic models, so Claude receives a schema with every answer. Kept flat and plain:
names, numbers and short text, never rendered HTML.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BookInfo(BaseModel):
    """A book the world declares."""

    code: str
    title: str
    rules_2024: bool


class WorldSummary(BaseModel):
    """One world, as `list_worlds` shows it."""

    id: str
    name: str
    language: str
    books: list[BookInfo] = Field(
        description="The declared books; empty means every imported book is allowed."
    )
    characters: int
    adventures: int


class AdventureSummary(BaseModel):
    """An adventure of a world."""

    id: str
    name: str
    status: str
    summary: str
    level_start: int | None = None
    level_end: int | None = None
    chapters: int


class CampaignObject(BaseModel):
    """An NPC or a treasure, by name and where it belongs."""

    id: str
    name: str
    scope: str = Field(description="world, adventure or chapter")
    adventure: str | None = None
    roles: list[str] = Field(default_factory=list)
    statblock: str | None = None


class WorldOverview(BaseModel):
    """What `world_overview` gives."""

    id: str
    name: str
    language: str
    description: str
    rules: str = Field(description="classic (2014) or one (2024), as the books imply")
    books: list[BookInfo]
    adventures: list[AdventureSummary]
    npcs: list[CampaignObject]
    places: list[CampaignObject] = Field(
        default_factory=list, description="Places, written {@place Name}"
    )
    treasures: list[CampaignObject]
    party: list[str] = Field(description="The world's characters: name, level")
    instructions: dict[str, str] = Field(
        default_factory=dict,
        description="The gamemaster's instructions for this world — follow them: `setting` "
        "for everything, then the one of the kind you create (adventure, chapter, npc, "
        "encounter, treasure, place)",
    )


class SpellKnown(BaseModel):
    """A spell a character has."""

    name: str
    level: int
    concentration: bool = False


class Attack(BaseModel):
    """A weapon or spell attack on the sheet."""

    name: str
    bonus: int | None
    damage: str
    damage_type: str


class PartyCharacter(BaseModel):
    """One character, as `party` gives it: who they are and what they can do."""

    id: str
    name: str
    player: str | None
    level: int
    species: str
    classes: str
    background: str
    armor_class: int | None
    hit_points: int | None
    speed: int | None
    passive_perception: int | None
    abilities: dict[str, int | None]
    saving_throws: dict[str, int | None]
    proficient_skills: dict[str, int | None]
    spell_save_dc: int | None = None
    spells: list[SpellKnown] = Field(default_factory=list)
    attacks: list[Attack] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    defenses: list[str] = Field(default_factory=list)
    appearance: str = ""
    backstory: str = ""
    notes: str = ""


class PartyAnswer(BaseModel):
    """The characters asked for."""

    characters: list[PartyCharacter]
    missing: list[str] = Field(
        default_factory=list, description="Ids that are no character of this world."
    )


class SearchMatch(BaseModel):
    """An entry the search found in the world's books."""

    name: str
    category: str
    source: str
    tag: str = Field(description="How to write it in a chapter")


class SearchAnswer(BaseModel):
    """What `search_reference` found."""

    matches: list[SearchMatch]
    outside_books: int = Field(description="Matches only in books the world leaves out")


class MonsterInfo(BaseModel):
    """A creature in the world's books."""

    name: str
    source: str
    cr: str | None
    xp: int | None
    type: str
    size: list[str]
    armor_class: int | None
    hit_points: int | None
    environments: list[str]
    legendary: bool
    tag: str


class MonsterAnswer(BaseModel):
    """What `find_monsters` found."""

    monsters: list[MonsterInfo]
    total: int = Field(description="How many matched before the limit")


class ReferenceAnswer(BaseModel):
    """One entry resolved through the world's books."""

    found: bool
    outcome: str = Field(description="How the name resolved: found, ambiguous, not in books…")
    name: str | None = None
    source: str | None = None
    category: str | None = None
    tag: str | None = None
    entry: dict[str, Any] | None = Field(
        default=None, description="The entry in 5eTools JSON, tags included"
    )


class MonsterInput(BaseModel):
    """Some number of one creature, for an encounter."""

    name: str = Field(description="The English name in the world's books")
    source: str | None = Field(default=None, description="The book, when it matters")
    count: int = Field(default=1, ge=1, le=99)
    ally: bool = Field(default=False, description="Fights on the characters' side")
    label: str | None = Field(
        default=None,
        description="A reskin: the name the table sees for this book creature, e.g. "
        '"Mire Lurker" for a Kuo-toa; its numbers stay the book\'s',
    )


class RatedMonster(BaseModel):
    """A line of a rated plan."""

    name: str
    source: str
    label: str | None = Field(default=None, description="The reskin's name, if any")
    count: int
    ally: bool
    cr: str | None
    xp_each: int | None


class RatingAnswer(BaseModel):
    """What `rate_encounter` (and the encounter tools) report."""

    rules: str = Field(description="classic (2014 thresholds) or one (2024 budgets)")
    difficulty: str | None = Field(
        description="The band reached: easy, medium, hard, deadly (2014); low, moderate, "
        "high (2024), beyond above high; trivial below all; None without monsters or party"
    )
    base_xp: int
    multiplier: float
    adjusted_xp: int
    bands: dict[str, int] = Field(description="Each band's XP for this party")
    party_levels: list[int]
    monsters: list[RatedMonster]
    unresolved: list[str] = Field(
        default_factory=list, description="Creatures not in the world's books (not rated)"
    )


class BudgetAnswer(BaseModel):
    """What `party_budget` gives: how much fighting a party can take."""

    rules: str = Field(description="classic (2014) or one (2024)")
    party_levels: list[int]
    encounter_bands: dict[str, int] = Field(
        description="One encounter's XP at each difficulty for this party"
    )
    day_xp: int | None = Field(
        description="2014: adjusted XP the party can face between long rests; None in 2024"
    )
    advice: str


class RolledEncounterAnswer(BaseModel):
    """A roll on a random-encounter table."""

    table: str
    roll: int
    text: str = Field(description="The row as written in the book")
    monsters: list[MonsterInput]
    unresolved: list[str] = Field(description="Creatures of the row not in the world's books")
    rating: RatingAnswer | None = Field(description="The creatures rated for the party")


class ChapterState(BaseModel):
    """A chapter and how far the table got with it."""

    title: str
    status: str


class AdventureRecord(BaseModel):
    """An adventure of the world and how far it got."""

    id: str
    name: str
    status: str = Field(description="draft, planned, running or finished")
    summary: str
    level_start: int | None
    level_end: int | None
    party: list[str] = Field(description="The characters it is for, by name")
    chapters: list[ChapterState]


class FightRecord(BaseModel):
    """A fight that was run, from the tracker and the log."""

    name: str
    encounter: str | None
    adventure: str | None
    chapter: str | None
    finished: bool
    rounds: int
    defeated: list[str]
    xp: int
    party: list[str]
    party_down: list[str] = Field(description="Characters down when it ended")
    biggest_hit: str | None


class NoteRecord(BaseModel):
    """The gamemaster's note of a day of play."""

    adventure: str | None
    text: str = Field(description="Markdown, in the world's language")


class SessionRecord(BaseModel):
    """One day of play: the gamemaster's notes, its fights, what the characters gained."""

    date: str
    notes: list[NoteRecord] = Field(
        default_factory=list, description="What happened, in the gamemaster's words"
    )
    fights: list[FightRecord]
    progress: list[str] = Field(description="Experience awarded and levels taken")


class CampaignHistory(BaseModel):
    """What happened in a world so far."""

    world_id: str
    characters: list[str] = Field(description="Each character: name, level, experience")
    adventures: list[AdventureRecord]
    sessions: list[SessionRecord] = Field(description="Newest first")
    older_sessions: int = Field(description="Sessions before these, left out")


class ItemInput(BaseModel):
    """Some number of one item, for a treasure."""

    name: str = Field(description="The English name in the world's books")
    source: str | None = Field(default=None, description="The book, when it matters")
    count: int = Field(default=1, ge=1, le=999)


class CoinsInput(BaseModel):
    """Coins of a treasure."""

    cp: int = Field(default=0, ge=0)
    sp: int = Field(default=0, ge=0)
    ep: int = Field(default=0, ge=0)
    gp: int = Field(default=0, ge=0)
    pp: int = Field(default=0, ge=0)


class Problem(BaseModel):
    """Something in a chapter that leads nowhere (the chapter check)."""

    line: int
    kind: str = Field(
        description="reference (not in the books), excluded (a book the world leaves out), "
        "library (no such NPC/encounter/treasure/chapter), empty (encounter without "
        "creatures), callout, heading, image, block, unclosed"
    )
    text: str
    name: str = ""
    instead: list[str] = Field(default_factory=list, description="Nearest names, best first")


class AdventureProblem(Problem):
    """Something that leads nowhere, and where in the adventure it is written."""

    where: str = Field(description='What holds it: "chapter Name", "npc Name", "encounter Name"…')
    id: str = Field(description="That thing's id")


class SoundInfo(BaseModel):
    """Something ``{@sound Name}`` can play."""

    name: str
    kind: str = Field(description="scene (a saved mix), ambience, music or effect")


class NamingStyleInfo(BaseModel):
    """A naming style ``generate_names`` can use."""

    key: str
    name: str
    race: str = ""
    aliases: list[str] = Field(default_factory=list)
    culture: str = ""
    subject: str = Field(
        default="person", description="person, settlement, water, landscape or building"
    )
    language: str = Field(
        default="", description="The language its names are made for; empty for any"
    )
    kind: str = Field(
        description="ancestry or style (the books), starter, own, race, or subject (any "
        "style of a kind of place)"
    )
    summary: str = ""
    examples: list[str] = Field(default_factory=list)


class UnservedPeople(BaseModel):
    """A people of a world's NPCs and characters that none of the world's naming styles
    serves: its names come from a fallback until a style is made."""

    race: str = Field(description="The people as the world names it, in the world's language")
    aliases: list[str] = Field(
        default_factory=list, description="Its other names, the English one among them"
    )
    met: list[str] = Field(
        default_factory=list, description="How the world's NPCs and characters spell it"
    )
    alias_style: str = Field(
        default="",
        description="A style of the table's that would serve it if given `alias` as another "
        "name; only the gamemaster adds that, in the workshop, so suggest it rather than "
        "drafting a new style",
    )
    alias: str = ""
    related_styles: list[str] = Field(
        default_factory=list,
        description='Styles of the table\'s for a kind of this people ("Night Elf" or '
        '"Nachtelf" for "Elf"). If one fits the world\'s people, suggest the gamemaster '
        "adds `alias` to it in the workshop instead of drafting a new style",
    )
    drafts_waiting: list[str] = Field(
        default_factory=list,
        description="Names of drafts for this people and world still waiting for the "
        "gamemaster to accept them",
    )


class GeneratedNames(BaseModel):
    """Names in one style, none of them already used in the world."""

    style: str
    fallback: bool = Field(
        default=False,
        description="No style serves this race and culture; these are the books' default. "
        "Tell the gamemaster, or draft one (`naming_style_rules`)",
    )
    names: list[str]
    parts: list[dict[str, str]] = Field(
        default_factory=list,
        description="Each name's parts, in order: pass them to create_npc as name_parts, "
        "so that a child's name can read them",
    )


class RevisionChange(BaseModel):
    """One record a revision changes."""

    kind: str
    action: str = Field(description="changed, new or removed")
    title: str
    conflict: bool = Field(description="The gamemaster changed it since: their version stays")


class WorldStyles(BaseModel):
    """The naming styles a world's names now come from."""

    world_id: str
    every: bool = Field(description="True when the world uses every style")
    styles: list[str] = Field(default_factory=list, description="The styles chosen, by name")


class DraftedStyle(BaseModel):
    """A naming style Claude proposed, waiting in the gamemaster's workshop."""

    id: str
    samples: list[str]
    problems: list[str] = Field(
        default_factory=list, description="What the samples show is wrong with the style"
    )


class ChapterText(BaseModel):
    """A chapter with its text and the version to write against."""

    id: str
    title: str
    order: int
    status: str
    version: str = Field(description="Pass as `base` when you write this chapter again")
    body: str


class ChapterWritten(BaseModel):
    """What `write_chapter` did."""

    id: str
    title: str
    version: str
    url: str
    problems: list[Problem]


class PlaceInfo(BaseModel):
    """A place of an adventure, ``{@place Name}``."""

    id: str
    name: str
    chapter_id: str | None
    tags: list[str]
    notes: str
    version: str


class NpcInfo(BaseModel):
    """An NPC of an adventure."""

    id: str
    name: str
    chapter_id: str | None
    roles: list[str]
    statblock: str | None
    notes: str
    version: str


class EncounterInfo(BaseModel):
    """An encounter of an adventure, rated for its party."""

    id: str
    name: str
    chapter_id: str | None
    notes: str
    rating: RatingAnswer


class TreasureLine(BaseModel):
    """Some number of one item in a treasure."""

    name: str
    source: str
    count: int


class TreasureInfo(BaseModel):
    """A treasure of an adventure."""

    id: str
    name: str
    chapter_id: str | None
    items: list[TreasureLine]
    coins: CoinsInput
    notes: str
    version: str
    unresolved: list[str] = Field(default_factory=list, description="Items not in the books")


class AdventureDetail(BaseModel):
    """An adventure with everything in it."""

    id: str
    world_id: str
    name: str
    status: str
    writable: bool = Field(description="True while it is a draft written through MCP")
    revision_of: str | None = Field(
        default=None, description="The adventure this draft copy will be written back onto"
    )
    summary: str
    level_start: int | None
    level_end: int | None
    party: list[str] = Field(description="Character ids it is written for")
    url: str
    chapters: list[ChapterText]
    npcs: list[NpcInfo]
    places: list[PlaceInfo] = Field(default_factory=list)
    encounters: list[EncounterInfo]
    treasures: list[TreasureInfo]


__all__ = [
    "AdventureProblem",
    "AdventureRecord",
    "SoundInfo",
    "NamingStyleInfo",
    "GeneratedNames",
    "CampaignHistory",
    "ChapterState",
    "FightRecord",
    "NoteRecord",
    "SessionRecord",
    "BudgetAnswer",
    "RolledEncounterAnswer",
    "AdventureDetail",
    "ChapterText",
    "ChapterWritten",
    "CoinsInput",
    "EncounterInfo",
    "ItemInput",
    "NpcInfo",
    "PlaceInfo",
    "Problem",
    "TreasureInfo",
    "TreasureLine",
    "AdventureSummary",
    "Attack",
    "BookInfo",
    "CampaignObject",
    "MonsterAnswer",
    "MonsterInfo",
    "MonsterInput",
    "PartyAnswer",
    "PartyCharacter",
    "RatedMonster",
    "RatingAnswer",
    "ReferenceAnswer",
    "SearchAnswer",
    "SearchMatch",
    "SpellKnown",
    "WorldOverview",
    "WorldSummary",
    "WorldStyles",
]


class CreatureBlockInput(BaseModel):
    """A trait, action, bonus action, reaction or legendary action of a creature."""

    name: str
    text: str = Field(
        description="5eTools text with the builder's placeholders, so numbers follow the "
        "creature: {@atk mw} {@hit <$to_hit__str$>} to hit … {@h}<$damage_avg__4.5+str$> "
        "({@damage 1d8<$damage_mod__str$>}) slashing damage; DC <$dc__int$>; "
        "<$title_short_name$> for 'The lich'. Paragraphs split by a blank line"
    )


class ReadyMadeInput(BaseModel):
    """One of the builder's ready-made texts to add to a creature (`ready_made_texts`)."""

    kind: Literal["trait", "action", "feature"] = Field(
        description="trait or action: one of the builder's ready-made blocks; feature: a "
        "monster feature of the guide, which also counts towards the CR"
    )
    name: str = Field(
        description='Its name as `ready_made_texts` lists it: "Bite (Cloaker)", or its '
        'title alone ("Pack Tactics") when only one has it'
    )
    value: int | None = Field(
        default=None, description="A feature's number (uses a day, dice of extra damage)"
    )


class ReadyMadeText(BaseModel):
    """A ready-made text of the builder, in the world's language where translated."""

    kind: str
    name: str = Field(description="What `ready_made` takes")
    title: str = Field(description="The block's name as the creature will show it")
    text: str
    translated: bool = Field(description="False: the table has not translated it yet")
    takes_number: bool = False


class CreatureProblem(BaseModel):
    """A reference in the creature's text that the world's books do not have, or a
    placeholder the builder cannot fill."""

    kind: str = Field(
        default="reference",
        description="reference: not in the world's books; placeholder: a <$…$> the builder "
        "cannot fill, which the table would read as written",
    )
    text: str
    instead: list[str] = Field(default_factory=list, description="The nearest names, best first")


class CreatureDraft(BaseModel):
    """A creature design Claude drafted, as the builder works it out."""

    id: str
    name: str
    url: str
    based_on: str | None = None
    target_cr: str
    plays_at: str | None = Field(
        default=None,
        description="The CR it plays at by the Dungeon Master's Guide method; aim for the "
        "target, within one step",
    )
    defensive_cr: str | None = None
    offensive_cr: str | None = None
    hit_points: int
    hit_point_formula: str
    armor_class: int
    damage_per_round: int | None = None
    attack_bonus: int | None = None
    save_dc: int | None = None
    advice: str = ""
    statblock: list[str] = Field(
        default_factory=list,
        description="Every trait and action with its numbers filled in, as the table sees it",
    )
    problems: list[CreatureProblem] = Field(
        default_factory=list,
        description="Spells, items or creatures named that are not in the world's books: "
        "mend them before you hand over",
    )
    revision_of: str | None = Field(
        default=None,
        description="The accepted creature this draft revises: it stays as it is until the "
        "gamemaster accepts the revision",
    )
    changes: list[str] = Field(
        default_factory=list, description="The fields a revision changes on its creature"
    )
    picture: str | None = Field(
        default=None, description="Where the gamemaster sees its picture, if it has one"
    )
