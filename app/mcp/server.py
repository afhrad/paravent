"""Paravent's MCP server: Claude reads the table and writes a draft adventure
(documentation/MCP.md).

Thin, as routes are: every tool asks the services. Built per app by
:func:`build_server`, which takes how to reach the app's stores, so tests can build as
many apps as they like. Served over streamable HTTP at ``/mcp`` behind
:class:`app.mcp.auth.BearerGuard`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from fractions import Fraction
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.mcp.common import party_levels, rating_answer, resolve_monsters, tool_errors
from app.mcp.drafts import register_drafts
from app.mcp.history import register_history
from app.mcp.models import (
    AdventureSummary,
    Attack,
    BookInfo,
    BudgetAnswer,
    CampaignObject,
    GeneratedNames,
    MonsterAnswer,
    MonsterInfo,
    MonsterInput,
    NamingStyleInfo,
    PartyAnswer,
    PartyCharacter,
    RatingAnswer,
    ReferenceAnswer,
    RolledEncounterAnswer,
    SearchAnswer,
    SearchMatch,
    SoundInfo,
    SpellKnown,
    UnservedPeople,
    WorldOverview,
    WorldSummary,
)
from app.models.gamestate import Location, Npc, SourceSet, Treasure, World
from app.models.naming import SUBJECTS, Subject
from app.services.difficulty import rules_for
from app.services.encounter_roll import EncounterRoller
from app.services.naming import GENDERS, FoundStyle, NamingError
from app.services.naming_workshop import race_names
from app.services.npc_gen import edition_for
from app.services.search import GROUP_KEYS, Row
from app.ui.insert import tag_maker
from app.ui.tags import ENTITY_CATEGORY

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry

MAX_NAMES = 50

INSTRUCTIONS = """\
Paravent is the gamemaster's D&D 5e table: worlds with declared books, player characters,
adventures written in Markdown, NPCs, encounters and treasure.

Use these tools to write adventures that fit the table exactly:
- Read the world (`world_overview`) and the party (`party`) first. Write everything the
  table reads in the world's language.
- Follow the gamemaster's instructions for the world (`world_overview.instructions`):
  `setting` says which world this is — use your knowledge of it, its places, people and
  creatures — and each kind (`npc`, `encounter`, …) how those should be made.
- A creature of the setting that 5e lacks is a reskin: a creature from the books that
  fits, under the setting's name (`label` on an encounter's monster).
- Creatures, spells and items come only from the world's books: find them with
  `find_monsters`, `search_reference` and `get_reference`, and refer to them by tag
  (`markdown_guide` explains the chapter Markdown).
- Rate every fight with `rate_encounter` for the party before you settle on it.

To write an adventure: `create_adventure` (a draft, for the chosen characters), then
`create_npc`, `create_encounter` and `create_treasure` (into the draft or one of its
chapters), then `write_chapter` for each chapter, naming those objects by tag. Mend
every problem `write_chapter` reports, then call `check_adventure` once everything exists.
You can change only drafts you created; the
gamemaster reviews a draft and takes it over by giving it another status.
"""

NEW_ADVENTURE = """\
Write a new adventure in Paravent for the world "{world}", for {characters}, to fill
{sessions}. The idea: {idea}.

Work in this order:

1. Read the table. `list_worlds` and `world_overview` for the world: its language, its
   books, its adventures, the NPCs and places that already exist, and above all the
   gamemaster's `instructions` — the setting (which world this is: draw on what you know
   of it) and how each kind of thing should be made. Follow them throughout.
   `campaign_history` for what
   happened at the table so far — the gamemaster's session notes above all, then which
   chapters were played, the fights, who fell, who went down, the levels gained — and
   let the new adventure follow on from it (a foe
   who escaped, a debt unpaid, a place the party never reached). `party` for
   the characters: their levels and classes, what they are good and bad at, and their
   backstories — weave at least one character's story into the plot.
2. Read `markdown_guide`. Everything the table reads is written in the world's language;
   tag targets stay English.
3. Plan before writing anything: the premise, the villain and what they want, how the
   party gets involved, a beginning, a middle with choices, an ending that depends on what
   the party does. One chapter per scene or location group; about two to four chapters
   per session. Call `party_budget` and plan the fights against it: varied difficulties,
   not every scene a fight, and at least one problem that can be solved without one.
4. `create_adventure` with the characters' ids and a level range.
5. Name people with `generate_names`, giving each one's race and culture, unless the
   world's instructions give their names. If it answers `fallback`, no style serves that
   people: follow `naming_style_rules` to draft one for the world (`world_id`) and tell
   the gamemaster; it works once they accept it, and joins the world's styles then. A
   world whose names keep coming from styles that do not fit it (orcs named like Tolkien's
   in Azeroth) may be given its own with `set_world_naming_styles`; say so.
   Keep the race and culture on each NPC in `create_npc`.
   Create the places with `create_place` — each with what it looks like, who is there
   and what happens there, naming its NPCs and fights — and the cast with `create_npc`:
   who they are, what they want and know, how they speak, what they do if the party
   fails. Give a statblock from the books where they may fight (`find_monsters`). Mark
   the villain with the role "villain" or "boss".
6. Create every fight with `create_encounter`. Choose creatures with `find_monsters` that
   fit the place and the story (environment, type), check the rating it answers with and
   adjust until it sits where you want it. Put terrain, tactics, morale and how it can
   end in its notes. `random_encounter` can supply travel encounters.
7. Create rewards with `create_treasure` (items from the books by name) or
   `roll_treasure` for a hoard of the right challenge rating.
8. Write each chapter with `write_chapter`: read-aloud text in `> [!read]`, secrets in
   `> [!secret]`, an NPC's card where they first enter, an encounter's card where the
   fight starts, the treasure where it is found, `{{@creature …}}`, `{{@spell …}}` and
   `{{@item …}}` references where the text mentions them. Mend every problem it reports
   and write the chapter again with the version it gave you. `list_sounds` shows what
   `{{@sound …}}` can play, for a tavern's noise or a fight's music, if any fits.
9. Call `check_adventure`, which also reads every note and catches names that did not
   exist yet when you wrote them. Mend what it reports.
10. Finish with a short report: the premise in two sentences, the chapters, the NPCs, the
   fights with their difficulty, and anything you could not resolve.

Never invent rules text or statblocks: everything mechanical comes from the books through
tags. Do not change anything outside the draft you created.
"""


def level_gap_warning(levels: Sequence[int]) -> str:
    """What a party's bands hide when its levels lie far apart; "" when they don't."""
    if not levels or max(levels) - min(levels) < LEVEL_GAP:
        return ""
    return (
        f" Levels here run from {min(levels)} to {max(levels)}: the bands add them up, so a "
        "fight rated medium can kill the lowest character in one hit. Check its hit points "
        "against what the creatures deal, and give it something else to do."
    )


GUIDE = files("app.mcp").joinpath("guide.md")
NAMING_RULES = files("app.mcp").joinpath("naming_rules.md")
# How many characters of a character's backstory and notes the party tool hands over.
STORY_LIMIT = 1500
MONSTER_LIMIT = 200
# How far apart levels must be for the budget to warn that its bands average them.
LEVEL_GAP = 4


def _books(registry: StoreRegistry, source_set: SourceSet) -> list[BookInfo]:
    out = []
    for code in source_set.sources:
        book = registry.books.get(code)
        out.append(
            BookInfo(
                code=code,
                title=book.name if book else code,
                rules_2024=bool(book and book.is_2024),
            )
        )
    return out


def naming_request(
    description: str = "",
    category: str = "",
    race: str = "",
    culture: str = "",
    setting: str = "",
    language: str = "",
) -> str:
    """The task the `new_naming_style` prompt opens with: what to draft, as asked."""
    subject = category.strip().lower()
    if subject and subject not in SUBJECTS:
        subject = ""
    who = ", ".join(x for x in (race.strip(), culture.strip()) if x)
    lines = ["Research and draft a naming style."]
    if description.strip():
        lines.append(f"What the gamemaster asked for: {description.strip()}")
    if subject:
        lines.append(f"It names: {subject} (draft_naming_style's `subject`).")
    else:
        lines.append(
            "Read from the request what it names: people (`person`) or a kind of place "
            "(`settlement`, `water`, `landscape`, `building`); ask if it is unclear."
        )
    if who:
        lines.append(f"Whose names: {who}.")
    if setting.strip():
        lines.append(f"Setting: {setting.strip()}.")
    if language.strip():
        lines.append(f"Made for the language {language.strip()!r} (`language`).")
    return "\n".join(lines) + "\n\n"


def build_server(stores: Callable[[], StoreRegistry]) -> MCPServer:
    """The server and its tools, reading the stores ``stores()`` returns at call time."""
    server = MCPServer(name="paravent", title="Paravent", instructions=INSTRUCTIONS)

    def world_of(world_id: str) -> World:
        with tool_errors():
            return stores().worlds.get(world_id)

    # -- the table ----------------------------------------------------------------

    @server.tool()
    def list_sounds() -> list[SoundInfo]:
        """What `{@sound Name}` can play: the sound desk's scenes and sounds, every world's."""
        library = stores().sounds
        return [SoundInfo(name=s.name, kind="scene") for s in library.all_scenes()] + [
            SoundInfo(name=s.name, kind=s.kind) for s in library.all()
        ]

    @server.tool()
    def list_worlds() -> list[WorldSummary]:
        """Every world: its language, declared books, characters and adventures."""
        registry = stores()
        return [
            WorldSummary(
                id=world.id,
                name=world.name,
                language=world.language,
                books=_books(registry, world.source_set),
                characters=registry.worlds.character_count(world.id),
                adventures=len(registry.adventures.adventures(world.id)),
            )
            for world in registry.worlds.list()
        ]

    @server.tool()
    def world_overview(world_id: str) -> WorldOverview:
        """A world's description, books, adventures, NPCs, places, treasure and characters."""
        registry = stores()
        world = world_of(world_id)
        adventures = registry.adventures.adventures(world.id)
        names = {a.id: a.name for a in adventures}

        def placed(item: Npc | Treasure | Location) -> CampaignObject:
            scope = item.scope
            return CampaignObject(
                id=item.id,
                name=item.name,
                scope=scope.level,
                adventure=names.get(scope.adventure_id or ""),
                roles=list(getattr(item, "roles", None) or getattr(item, "tags", [])),
                statblock=getattr(item, "statblock", None),
            )

        return WorldOverview(
            id=world.id,
            name=world.name,
            language=world.language,
            description=world.description,
            rules=rules_for(world.source_set),
            books=_books(registry, world.source_set),
            adventures=[
                AdventureSummary(
                    id=a.id,
                    name=a.name,
                    status=a.status,
                    summary=a.summary,
                    level_start=a.level.start if a.level else None,
                    level_end=a.level.end if a.level else None,
                    chapters=registry.adventures.chapter_count(a.id),
                )
                for a in adventures
            ],
            npcs=[placed(n) for n in registry.npcs.list(world.id)],
            places=[placed(x) for x in registry.locations.list(world.id)],
            treasures=[placed(t) for t in registry.treasure.list(world.id)],
            party=[
                f"{pc.name}, level {pc.total_level}"
                for pc in registry.characters.in_world(world.id)
                if pc.total_level > 0
            ],
            instructions=registry.instructions.given(registry.instructions.get(world.id)),
        )

    @server.tool()
    def party(
        world_id: str,
        character_ids: Annotated[
            list[str] | None,
            Field(description="The characters to describe; all of the world's when omitted"),
        ] = None,
    ) -> PartyAnswer:
        """The characters an adventure is for: levels, defences, skills, spells, story."""
        registry = stores()
        world = world_of(world_id)
        present = {pc.id: pc for pc in registry.characters.in_world(world.id)}
        wanted = character_ids or [i for i, pc in present.items() if pc.total_level > 0]
        out, missing = [], []
        for character_id in wanted:
            pc = present.get(character_id)
            if pc is None:
                missing.append(character_id)
                continue
            sheet = registry.characters.sheet(pc)
            out.append(
                PartyCharacter(
                    id=pc.id,
                    name=pc.name,
                    player=pc.player,
                    level=sheet.level,
                    species=sheet.species_name,
                    classes=sheet.class_line,
                    background=sheet.background_name,
                    armor_class=sheet.armor_class.value,
                    hit_points=sheet.hp_max.value,
                    speed=sheet.speed.value,
                    passive_perception=sheet.passive_perception.value,
                    abilities={a.key: a.score.value for a in sheet.abilities},
                    saving_throws={a.key: a.save.value for a in sheet.abilities},
                    proficient_skills={s.name: s.value.value for s in sheet.skills if s.proficient},
                    spell_save_dc=sheet.spell_save_dc.value if sheet.spell_save_dc else None,
                    spells=[
                        SpellKnown(name=s.name, level=s.level, concentration=s.concentration)
                        for s in sheet.spells
                    ],
                    attacks=[
                        Attack(
                            name=a.name,
                            bonus=a.bonus.value,
                            damage=a.damage,
                            damage_type=a.damage_type,
                        )
                        for a in sheet.attacks
                    ],
                    features=list(dict.fromkeys(f.name for f in sheet.features)),
                    languages=list(sheet.languages),
                    defenses=[f"{d.kind} {d.damage} ({d.origin})" for d in sheet.defenses],
                    appearance=pc.appearance[:STORY_LIMIT],
                    backstory=pc.backstory[:STORY_LIMIT],
                    notes=pc.notes[:STORY_LIMIT],
                )
            )
        return PartyAnswer(characters=out, missing=missing)

    # -- the books ----------------------------------------------------------------

    @server.tool()
    def search_reference(
        world_id: str,
        query: str,
        group: Annotated[
            str | None,
            Field(description=f"Narrow to one group: {', '.join(GROUP_KEYS)}"),
        ] = None,
    ) -> SearchAnswer:
        """Search the world's books by name: spells, creatures, items, options, rules."""
        registry = stores()
        world = world_of(world_id)
        result = registry.search.search(query, world.source_set, group=group)
        tag = tag_maker(registry.references.resolver(), world.source_set)
        matches = []
        for found in result.groups:
            for hit in found.hits:
                written = tag(hit.row)
                if written is None:
                    continue
                matches.append(
                    SearchMatch(
                        name=hit.row.name,
                        category=hit.row.category,
                        source=hit.row.source,
                        tag=written,
                    )
                )
        return SearchAnswer(matches=matches, outside_books=result.outside)

    @server.tool()
    def find_monsters(
        world_id: str,
        cr_min: Annotated[str | None, Field(description='Lowest CR, e.g. "1/4" or "3"')] = None,
        cr_max: Annotated[str | None, Field(description="Highest CR")] = None,
        creature_type: Annotated[
            str | None, Field(description="undead, fey, humanoid, dragon…")
        ] = None,
        environment: Annotated[
            str | None, Field(description="forest, swamp, underdark, urban…")
        ] = None,
        name: Annotated[str | None, Field(description="Part of the name")] = None,
        limit: Annotated[int, Field(ge=1, le=MONSTER_LIMIT)] = 40,
    ) -> MonsterAnswer:
        """Creatures in the world's books by challenge rating, type and environment."""
        registry = stores()
        world = world_of(world_id)
        query = registry.compendium.monsters().in_books(world.source_set)
        if cr_min is not None or cr_max is not None:
            query = query.cr_between(cr_min or "0", cr_max or "30")
        if creature_type:
            query = query.of_type(creature_type)
        if environment:
            query = query.environment(environment)
        if name:
            query = query.named(name)
        found = sorted(
            query.all(),
            key=lambda m: (m.cr.value if m.cr else Fraction(-1), m.name.casefold()),
        )
        tag = tag_maker(registry.references.resolver(), world.source_set)
        return MonsterAnswer(
            monsters=[
                MonsterInfo(
                    name=m.name,
                    source=m.source,
                    cr=m.cr.text if m.cr else None,
                    xp=m.cr.xp if m.cr else None,
                    type=m.kind.text,
                    size=list(m.sizes),
                    armor_class=m.ac,
                    hit_points=m.hp,
                    environments=list(m.environments),
                    legendary=m.legendary,
                    tag=tag(Row("monster", m.name, m.source, m.entry)) or "",
                )
                for m in found[:limit]
            ],
            total=len(found),
        )

    @server.tool()
    def get_reference(
        world_id: str,
        kind: Annotated[
            str, Field(description="The tag name: creature, spell, item, condition, feat…")
        ],
        name: str,
        source: Annotated[str | None, Field(description="The book, when it matters")] = None,
    ) -> ReferenceAnswer:
        """One entry resolved through the world's books, as 5eTools JSON."""
        registry = stores()
        world = world_of(world_id)
        categories = ENTITY_CATEGORY.get(kind.lower())
        if categories is None:
            return ReferenceAnswer(found=False, outcome=f"unknown kind {kind!r}")
        resolver = registry.references.resolver()
        outcome = "not found"
        for category in categories:
            result = resolver.resolve_parts(category, name, source, world.source_set)
            if result.entry is None:
                outcome = str(result.outcome)
                continue
            entry = result.entry
            data: dict[str, Any] = entry.model_dump(by_alias=True, exclude_none=True)
            row = Row(category, str(entry.name), str(entry.source), entry)
            return ReferenceAnswer(
                found=True,
                outcome=str(result.outcome),
                name=str(entry.name),
                source=str(entry.source),
                category=category,
                tag=tag_maker(resolver, world.source_set)(row),
                entry=data,
            )
        return ReferenceAnswer(found=False, outcome=outcome)

    # -- balance --------------------------------------------------------------------

    @server.tool()
    def rate_encounter(
        world_id: str,
        monsters: list[MonsterInput],
        character_ids: Annotated[
            list[str] | None,
            Field(description="The party; the world's characters when omitted"),
        ] = None,
        party_size: Annotated[int | None, Field(ge=1, le=12)] = None,
        party_level: Annotated[int | None, Field(ge=1, le=20)] = None,
    ) -> RatingAnswer:
        """Rate a fight for a party under the rules the world's books imply; saves nothing.

        The party is the characters named, else a stated size and level, else every
        character of the world.
        """
        registry = stores()
        world = world_of(world_id)
        planned, unresolved = resolve_monsters(registry, world, monsters)
        levels = party_levels(registry, world, character_ids, party_size, party_level)
        rating, lines = registry.encounters.rate_plan(world, planned, levels)
        return rating_answer(rating, lines, levels, unresolved)

    @server.tool()
    def party_budget(
        world_id: str,
        character_ids: Annotated[
            list[str] | None,
            Field(description="The party; the world's characters when omitted"),
        ] = None,
    ) -> BudgetAnswer:
        """How much fighting a party can take: one encounter's XP at each difficulty,
        and under 2014 rules the adjusted XP of a whole adventuring day."""
        registry = stores()
        world = world_of(world_id)
        levels = party_levels(registry, world, character_ids)
        rules = rules_for(world.source_set)
        difficulty = registry.encounters.difficulty
        day = difficulty.day_budget(rules, levels)
        if not difficulty.has_tables(rules):
            advice = (
                "2014 rules, but their thresholds and adventuring day are tables of the "
                "2014 Dungeon Master's Guide, which is not in the table's 5eTools data: "
                "there is nothing to rate against. Add the book, or rate the encounters "
                "under the 2024 rules."
            )
        elif rules == "classic":
            advice = (
                "2014 rules: an adventuring day's encounters together reach about day_xp "
                "(adjusted XP) before the party needs a long rest — typically six to eight "
                "medium or hard encounters with two short rests. Fewer, harder fights "
                "drain less of the party's resources and feel easier than the XP says."
            )
        else:
            advice = (
                "2024 rules: rate each encounter against its budget (low, moderate, high); "
                "the 2024 guide sets no daily budget. Mix difficulties, and give high "
                "encounters room to breathe with rests or story beats between them."
            )
        advice += level_gap_warning(levels)
        return BudgetAnswer(
            rules=rules,
            party_levels=levels,
            encounter_bands={b.key: b.xp for b in difficulty.bands(rules, levels)},
            day_xp=day,
            advice=advice,
        )

    @server.tool()
    def random_encounter(
        world_id: str,
        environment: Annotated[
            str,
            Field(description='A table as "Name|SOURCE"; an unknown one lists the tables'),
        ],
        character_ids: Annotated[
            list[str] | None,
            Field(description="The party; the world's characters when omitted"),
        ] = None,
    ) -> RolledEncounterAnswer:
        """Roll the books' random-encounter table of an environment for the party's
        level; the row's creatures are resolved in the world's books and rated."""
        registry = stores()
        world = world_of(world_id)
        tables = registry.encounter_tables
        env = tables.get(environment)
        if env is None:
            raise ToolError(
                "No such table. The tables are: " + ", ".join(e.key for e in tables.all())
            )
        levels = party_levels(registry, world, character_ids)
        level = round(sum(levels) / len(levels)) if levels else 1
        resolver = registry.references.resolver()

        def resolve(name: str, source: str | None) -> tuple[str, str] | None:
            entry = resolver.resolve_parts("monster", name, source, world.source_set).entry
            return (str(entry.name), str(entry.source)) if entry is not None else None

        rolled = EncounterRoller(resolve).roll(env, level)
        if rolled is None:
            raise ToolError(f"{env.key} has no table for level {level}.")
        monsters = [MonsterInput(name=n, source=s, count=c) for n, s, c in rolled.monsters]
        rating = None
        if monsters:
            planned, _ = resolve_monsters(registry, world, monsters)
            found, lines = registry.encounters.rate_plan(world, planned, levels)
            rating = rating_answer(found, lines, levels)
        return RolledEncounterAnswer(
            table=f"{rolled.environment} ({rolled.source})",
            roll=rolled.roll,
            text=rolled.text,
            monsters=monsters,
            unresolved=rolled.unresolved,
            rating=rating,
        )

    @server.tool()
    def list_naming_styles(
        race: Annotated[str, Field(description="Only this race; empty for all")] = "",
        culture: Annotated[str, Field(description="Only this culture; empty for all")] = "",
        subject: Annotated[
            str,
            Field(description="Only styles of person, settlement, water, landscape or building"),
        ] = "",
        world_id: Annotated[
            str, Field(description="Only the styles this world uses; empty for every style")
        ] = "",
    ) -> list[NamingStyleInfo]:
        """The naming styles `generate_names` can use: the books' tables, Paravent's
        starters and the gamemaster's own, each with three examples. A key `race:Name`
        rolls among all that race's styles, `subject:water` among a kind of place's.
        A world may limit its names to some styles; give `world_id` to see only those,
        which are all `generate_names` accepts for that world."""
        naming = stores().naming_for(world_of(world_id).id) if world_id else stores().naming
        wanted_race, wanted_culture = race.strip().casefold(), culture.strip().casefold()
        return [
            NamingStyleInfo(
                key=entry.key,
                name=entry.style.name if entry.style else entry.label or "Any 2024 style",
                race=entry.race,
                aliases=list(entry.style.aliases) if entry.style else [],
                culture=entry.culture,
                subject=entry.subject,
                language=entry.language,
                kind=entry.kind,
                summary=entry.style.summary if entry.style else "",
                examples=naming.sample(entry.style, count=3) if entry.style else [],
            )
            for entry in naming.entries()
            if (not wanted_race or entry.of_race(wanted_race))
            and (not wanted_culture or entry.culture.casefold() == wanted_culture)
            and (not subject or entry.subject == subject)
        ]

    @server.tool()
    def unserved_peoples(world_id: str) -> list[UnservedPeople]:
        """The peoples of a world's NPCs and characters that none of its naming styles
        serves, as the workshop lists them: a people met under its English and its
        translated name is one. For each, draft a style with `draft_naming_style(…,
        world_id)` after `naming_style_rules`, unless `alias_style` says an existing style
        would do with another name, or `drafts_waiting` shows one is already proposed."""
        registry = stores()
        world = world_of(world_id)
        workshop = registry.naming_workshop
        found = workshop.unserved(
            world.id,
            registry.naming_for(world.id),
            edition_for(world.source_set, registry.npc_tables),
            race_names(registry.glossary, world.language),
        )
        drafts = [s for s in workshop.styles() if s.draft and s.for_world == world.id]
        out = []
        for people in found:
            names = {people.race.casefold(), *(a.casefold() for a in people.aliases)}
            waiting = [
                s.name
                for s in drafts
                if s.race.casefold() in names or names & {a.casefold() for a in s.aliases}
            ]
            out.append(
                UnservedPeople(
                    race=people.race,
                    aliases=list(people.aliases),
                    met=list(people.met),
                    alias_style=people.alias_style[1] if people.alias_style else "",
                    alias=people.alias,
                    drafts_waiting=waiting,
                )
            )
        return out

    @server.tool()
    def naming_style_rules() -> str:
        """How to research a people's names and draft a naming style."""
        return NAMING_RULES.read_text(encoding="utf-8")

    @server.tool()
    def generate_names(
        world_id: str,
        style: Annotated[
            str | None,
            Field(description="A key from list_naming_styles; omit to find one by race"),
        ] = None,
        gender: Annotated[str, Field(description="any, female or male")] = "any",
        count: Annotated[int, Field(ge=1, le=MAX_NAMES)] = 10,
        race: Annotated[str, Field(description="The person's race")] = "",
        culture: Annotated[str, Field(description="The person's culture")] = "",
        parent: Annotated[
            str, Field(description="An NPC or character of the world this person descends from")
        ] = "",
        subject: Annotated[
            Subject,
            Field(description="person, or a kind of place: settlement, water, landscape, building"),
        ] = "person",
    ) -> GeneratedNames:
        """Names for a race and culture, or in a style, none already an NPC, character or
        place of the world. Styles made for the world's language come first. With neither,
        or nothing that fits, the books' default: see `fallback`. Places take `subject`."""
        registry = stores()
        world = world_of(world_id)
        if gender not in GENDERS:
            raise ToolError(f"Gender is one of: {', '.join(GENDERS)}.")
        naming = registry.naming_for(world.id)
        edition = edition_for(world.source_set, registry.npc_tables)
        found = (
            FoundStyle(style)
            if style
            else naming.find(race, culture, edition, world.language, subject)
        )
        key = found.key
        used = [
            *(n.name for n in registry.gamestate.npcs.list(lambda n: n.scope.world_id == world.id)),
            *(c.name for c in registry.gamestate.characters.list(lambda c: c.world_id == world.id)),
            *(x.name for x in registry.locations.list(world.id)),
        ]
        workshop = registry.naming_workshop
        wanted = parent.strip().casefold()
        refs = [ref for ref, name in workshop.relatives(world.id) if name.casefold() == wanted]
        if wanted and not refs:
            raise ToolError(f"{parent!r} has no name made by Paravent in this world.")
        kin = workshop.kin_parts(refs[0]) if refs else {}
        try:
            made = naming.made(
                key, gender, count, used, spread=True, kin=kin, language=world.language
            )
            return GeneratedNames(
                style=key,
                fallback=found.fallback,
                names=[m.text for m in made],
                parts=[m.parts for m in made],
            )
        except NamingError:
            raise ToolError(
                f"No naming style {key!r} in this world; list_naming_styles with its world_id"
                " shows them."
            ) from None

    # -- writing ------------------------------------------------------------------

    @server.tool()
    def markdown_guide() -> str:
        """How chapters are written: tags, objects alone on a line, callouts, links."""
        return GUIDE.read_text(encoding="utf-8")

    @server.resource("paravent://guide/naming-styles", mime_type="text/markdown")
    def naming_rules_resource() -> str:
        """How to research a people's names and draft a naming style."""
        return NAMING_RULES.read_text(encoding="utf-8")

    @server.prompt(title="New naming style")
    def new_naming_style(
        description: Annotated[
            str,
            Field(
                description='What the names are for, in your own words: "German dwarven '
                'holds of the Alliance", "harbour taverns of a pirate coast"'
            ),
        ] = "",
        category: Annotated[
            str,
            Field(
                description="What it names: person, settlement, water, landscape or "
                "building; empty to read it from the description"
            ),
        ] = "",
        race: Annotated[str, Field(description="The people, as the setting names them")] = "",
        culture: Annotated[str, Field(description="A group within them, or nothing")] = "",
        setting: Annotated[str, Field(description="Where they are from")] = "",
        language: Annotated[
            str,
            Field(description='The language the names are made for ("de"); empty for any'),
        ] = "",
    ) -> str:
        """Research names from a description and draft a naming style for the
        gamemaster: for a people, or for a kind of place."""
        return naming_request(description, category, race, culture, setting, language) + (
            NAMING_RULES.read_text(encoding="utf-8")
        )

    @server.resource("paravent://guide/markdown", mime_type="text/markdown")
    def guide_resource() -> str:
        """Paravent's chapter Markdown."""
        return GUIDE.read_text(encoding="utf-8")

    @server.prompt(title="New adventure")
    def new_adventure(
        world: Annotated[str, Field(description="The world's name or id")],
        characters: Annotated[
            str, Field(description="Who it is for, by name; empty for every character")
        ] = "",
        sessions: Annotated[
            str, Field(description="How many evenings of play; empty for the world's instructions")
        ] = "",
        idea: Annotated[str, Field(description="Tone, theme, a premise — or nothing")] = "",
    ) -> str:
        """Write a new adventure for a party in a world, ready to review in Paravent."""
        return NEW_ADVENTURE.format(
            world=world,
            characters=characters or "every character of the world",
            sessions=f"{sessions} session(s) of play"
            if sessions
            else "as many sessions of play as the world's instructions name, else one",
            idea=idea or "your choice, fitting the world and the characters' stories",
        )

    register_history(server, stores)
    register_drafts(server, stores)
    return server


__all__ = ["INSTRUCTIONS", "build_server"]
