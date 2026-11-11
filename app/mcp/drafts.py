"""The MCP server's writing tools: a draft adventure and what lies in it
(documentation/MCP.md §4, §6).

The rule every tool here keeps: Claude writes only into an adventure that is a
**draft drafted through MCP** — its chapters, and the NPCs, encounters and treasure
scoped to it or to one of its chapters. Anything else is read, never changed. The
gamemaster ends the draft by giving the adventure another status.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.mcp.auth import bearer
from app.mcp.common import rating_answer, resolve_monsters, tool_errors
from app.mcp.models import (
    AdventureDetail,
    AdventureProblem,
    ChapterText,
    ChapterWritten,
    CoinsInput,
    DraftedStyle,
    EncounterInfo,
    ItemInput,
    MonsterInput,
    NpcInfo,
    PlaceInfo,
    Problem,
    RevisionChange,
    TreasureInfo,
    TreasureLine,
    WorldStyles,
)
from app.models.gamestate import (
    Adventure,
    Chapter,
    Coins,
    Encounter,
    EncounterMonster,
    GivenName,
    LevelRange,
    Location,
    Npc,
    Scope,
    SourceSet,
    Treasure,
    TreasureItem,
    World,
)
from app.models.naming import Subject
from app.services import scope as scopes
from app.services.adventures import text_version
from app.services.i18n import english
from app.services.naming import sample_notes
from app.services.naming_workshop import style_from_raw
from app.services.treasure_roll import TreasureRollError, roller_for, tables_for
from app.ui.context import Page
from app.ui.library_links import adventure_url, chapter_url

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry

KINDS = ("chapter", "npc", "place", "encounter", "treasure", "adventure")


def register_drafts(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add the writing tools to ``server``."""

    # -- the guard ------------------------------------------------------------------

    def writable(adventure_id: str) -> Adventure:
        with tool_errors():
            adventure = stores().adventures.get(adventure_id)
        if adventure.status != "draft" or not adventure.drafted_by:
            raise ToolError(
                f"“{adventure.name}” is not a draft written through Paravent's MCP server: "
                "it can be read, not changed. Create a new adventure with create_adventure."
            )
        return adventure

    def place(adventure: Adventure, chapter_id: str | None) -> Scope:
        if not chapter_id:
            return Scope(world_id=adventure.world_id, adventure_id=adventure.id)
        chapter = chapter_of(adventure, chapter_id)
        return Scope(world_id=adventure.world_id, adventure_id=adventure.id, chapter_id=chapter.id)

    def chapter_of(adventure: Adventure, chapter_id: str) -> Chapter:
        with tool_errors():
            chapter = stores().adventures.get_chapter(chapter_id)
        if chapter.adventure_id != adventure.id:
            raise ToolError(f"Chapter {chapter_id} is not in “{adventure.name}”.")
        return chapter

    def owner(where: Scope | None) -> Adventure:
        """The draft an object lies in, or a refusal."""
        if where is None or not where.adventure_id:
            raise ToolError("That belongs to the world, not to a draft: it can be read only.")
        return writable(where.adventure_id)

    def world(adventure: Adventure) -> World:
        return stores().adventures.world(adventure.world_id)

    # -- reading back ---------------------------------------------------------------

    def npc_info(npc: Npc) -> NpcInfo:
        return NpcInfo(
            id=npc.id,
            name=npc.name,
            chapter_id=npc.scope.chapter_id,
            roles=list(npc.roles),
            statblock=npc.statblock,
            notes=npc.notes,
            version=text_version(npc.notes),
        )

    def place_info(location: Location) -> PlaceInfo:
        return PlaceInfo(
            id=location.id,
            name=location.name,
            chapter_id=location.scope.chapter_id,
            tags=list(location.tags),
            notes=location.notes,
            version=text_version(location.notes),
        )

    def encounter_info(encounter: Encounter) -> EncounterInfo:
        view = stores().encounters.view(encounter)
        levels = [member.level for member in view.party]
        return EncounterInfo(
            id=encounter.id,
            name=encounter.name,
            chapter_id=encounter.chapter_id,
            notes=encounter.notes,
            rating=rating_answer(view.rating, view.lines, levels),
        )

    def treasure_info(treasure: Treasure, unresolved: Sequence[str] = ()) -> TreasureInfo:
        return TreasureInfo(
            id=treasure.id,
            name=treasure.name,
            chapter_id=treasure.scope.chapter_id,
            items=[
                TreasureLine(name=i.name, source=i.source, count=i.count) for i in treasure.items
            ],
            coins=CoinsInput(**treasure.coins.model_dump()),
            notes=treasure.notes,
            version=text_version(treasure.notes),
            unresolved=list(unresolved),
        )

    def english_page() -> Page:
        registry = stores()
        return Page(
            t=registry.ui.translator(
                "en", registry.glossary, registry.translations, registry.item_names.compose
            )
        )

    def problems(chapter_id: str, body: str) -> list[Problem]:
        from app.routes.adventures import chapter_problems

        registry = stores()
        page = english_page()
        return [
            Problem(line=p.line, kind=p.kind, text=p.text, name=p.name, instead=list(p.instead))
            for p in chapter_problems(page, registry, chapter_id, body)
        ]

    @server.tool()
    def read_adventure(adventure_id: str) -> AdventureDetail:
        """An adventure — any, drafts and the gamemaster's own — with its chapters' text,
        NPCs, encounters (rated for its party) and treasure."""
        registry = stores()
        with tool_errors():
            adventure = registry.adventures.get(adventure_id)
        inside = Scope(world_id=adventure.world_id, adventure_id=adventure.id)

        def in_it(where: Scope | None) -> bool:
            return where is not None and scopes.within(where, inside)

        return AdventureDetail(
            id=adventure.id,
            world_id=adventure.world_id,
            name=adventure.name,
            status=adventure.status,
            writable=adventure.status == "draft" and bool(adventure.drafted_by),
            revision_of=adventure.revision_of,
            summary=adventure.summary,
            level_start=adventure.level.start if adventure.level else None,
            level_end=adventure.level.end if adventure.level else None,
            party=list(adventure.party or []),
            url=adventure_url(adventure.id),
            chapters=[
                ChapterText(
                    id=c.id,
                    title=c.title,
                    order=c.order,
                    status=c.status,
                    version=text_version(c.body),
                    body=c.body,
                )
                for c in registry.adventures.chapters(adventure.id)
            ],
            npcs=[npc_info(n) for n in registry.npcs.list(adventure.world_id) if in_it(n.scope)],
            places=[
                place_info(x) for x in registry.locations.list(adventure.world_id) if in_it(x.scope)
            ],
            encounters=[
                encounter_info(e)
                for e in registry.encounters.list(adventure.world_id)
                if in_it(e.scope)
            ],
            treasures=[
                treasure_info(t)
                for t in registry.treasure.list(adventure.world_id)
                if in_it(t.scope)
            ],
        )

    # -- the adventure and its chapters -----------------------------------------------

    @server.tool()
    def create_adventure(
        ctx: Context,
        world_id: str,
        name: str,
        summary: str,
        character_ids: Annotated[
            list[str], Field(description="The characters it is written for (from `party`)")
        ],
        level_start: Annotated[int | None, Field(ge=1, le=20)] = None,
        level_end: Annotated[int | None, Field(ge=1, le=20)] = None,
    ) -> AdventureDetail:
        """Start a new adventure as a draft, written for these characters. Everything
        else you create goes into it; the gamemaster reviews it and sets it planned."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        with tool_errors():
            adventure = registry.adventures.create(world_id, name)
            level = None
            if level_start or level_end:
                low = level_start or level_end or 1
                level = LevelRange(start=low, end=max(low, level_end or low))
            registry.gamestate.adventures.update(
                adventure.id,
                status="draft",
                drafted_by=found[0].id,
                summary=" ".join(summary.split()),
                level=level,
            )
            registry.adventures.set_party(adventure.id, character_ids)
        return read_adventure(adventure.id)

    @server.tool()
    def start_revision(adventure_id: str, ctx: Context) -> AdventureDetail:
        """A draft copy of an adventure you wrote that the gamemaster accepted, to revise
        with the same tools: its chapters, NPCs, places, encounters and treasure. Answers
        with the copy (write to its ids), or the copy already open. The original stays as
        it is until the gamemaster accepts the revision. Your own drafts need no copy."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        with tool_errors():
            copy = registry.revisions.start(adventure_id, found[0].id)
        return read_adventure(copy.id)

    @server.tool()
    def revision_changes(adventure_id: str) -> list[RevisionChange]:
        """What a revision changes against the original, as the gamemaster will see it."""
        registry = stores()
        with tool_errors():
            copy = registry.adventures.get(adventure_id)
            if not copy.revision_of:
                raise ToolError("That adventure is not a revision.")
            return [
                RevisionChange(kind=c.kind, action=c.action, title=c.title, conflict=c.conflict)
                for c in registry.revisions.changes(copy)
            ]

    @server.tool()
    def write_chapter(
        adventure_id: str,
        title: str,
        body: Annotated[str, Field(description="The whole chapter in Paravent's Markdown")],
        chapter_id: Annotated[
            str | None, Field(description="To replace a chapter's text; omit for a new one")
        ] = None,
        base: Annotated[
            str | None,
            Field(description="The chapter's `version` you last read; required to replace"),
        ] = None,
    ) -> ChapterWritten:
        """Write a chapter of a draft: a new one at the end, or a chapter's whole text
        again. Answers with the chapter check's problems — mend them and write again."""
        registry = stores()
        adventure = writable(adventure_id)
        with tool_errors():
            if chapter_id:
                chapter = chapter_of(adventure, chapter_id)
                if base is None:
                    raise ToolError(
                        "Replacing a chapter needs `base`: its `version` from read_adventure "
                        "or your last write_chapter."
                    )
            else:
                chapter = registry.adventures.create_chapter(adventure.id, title)
                base = text_version(chapter.body)
            saved = registry.adventures.update_chapter(
                chapter.id, title=title, status=chapter.status, body=body, base=base
            )
        return ChapterWritten(
            id=saved.id,
            title=saved.title,
            version=text_version(saved.body),
            url=chapter_url(saved.id),
            problems=problems(saved.id, saved.body),
        )

    @server.tool()
    def check_chapter(chapter_id: str) -> list[Problem]:
        """What in a chapter leads nowhere: references not in the books (with the nearest
        names), missing NPCs, encounters or treasure, empty encounters, unknown callouts,
        missing headings and images."""
        with tool_errors():
            chapter = stores().adventures.get_chapter(chapter_id)
        return problems(chapter.id, chapter.body)

    @server.tool()
    def check_adventure(adventure_id: str) -> list[AdventureProblem]:
        """Everything in an adventure that leads nowhere: every chapter, and the notes of
        its NPCs, places, encounters and treasure. Call it last, when all of it exists: a
        chapter or NPC may name something written later."""
        from app.routes.adventures import notes_problems

        registry = stores()
        with tool_errors():
            adventure = registry.adventures.get(adventure_id)
        inside = Scope(world_id=adventure.world_id, adventure_id=adventure.id)
        page = english_page()
        found: list[AdventureProblem] = []

        def add(where: str, record_id: str, faults: Sequence[Any]) -> None:
            found.extend(
                AdventureProblem(
                    where=where,
                    id=record_id,
                    line=p.line,
                    kind=p.kind,
                    text=p.text,
                    name=p.name,
                    instead=list(p.instead),
                )
                for p in faults
            )

        for chapter in registry.adventures.chapters(adventure.id):
            add(f"chapter {chapter.title}", chapter.id, problems(chapter.id, chapter.body))
        world_id = adventure.world_id
        held: list[tuple[str, str, str, str, Scope | None]] = [
            *(("npc", n.name, n.id, n.notes, n.scope) for n in registry.npcs.list(world_id)),
            *(("place", x.name, x.id, x.notes, x.scope) for x in registry.locations.list(world_id)),
            *(
                ("encounter", e.name, e.id, e.notes, e.scope)
                for e in registry.encounters.list(world_id)
            ),
            *(
                ("treasure", x.name, x.id, x.notes, x.scope)
                for x in registry.treasure.list(world_id)
            ),
        ]
        for kind, name, record_id, notes, where in held:
            if where is not None and scopes.within(where, inside):
                add(f"{kind} {name}", record_id, notes_problems(page, registry, where, notes))
        return found

    # -- NPCs -----------------------------------------------------------------------

    def statblock_ref(adventure: Adventure, statblock: str | None) -> str:
        if not statblock:
            return ""
        name, _, source = statblock.partition("|")
        planned, unresolved = resolve_monsters(
            stores(), world(adventure), [MonsterInput(name=name, source=source or None)]
        )
        if unresolved:
            raise ToolError(f"“{statblock}” is not a creature in this world's books.")
        return planned[0].ref

    @server.tool()
    def create_npc(
        adventure_id: str,
        name: str,
        notes: Annotated[
            str, Field(description="Who they are, want, know; how they speak (Markdown)")
        ],
        roles: Annotated[list[str], Field(description='Tags such as "villain", "boss"')] = [],  # noqa: B006
        statblock: Annotated[
            str | None, Field(description='A creature of the books, "Name" or "Name|SOURCE"')
        ] = None,
        chapter_id: Annotated[
            str | None, Field(description="Only in this chapter; omit for the whole adventure")
        ] = None,
        name_parts: Annotated[
            dict[str, str] | None, Field(description="The name's parts from generate_names")
        ] = None,
        race: Annotated[str, Field(description="The race their name comes from")] = "",
        culture: Annotated[str, Field(description="The culture their name comes from")] = "",
    ) -> NpcInfo:
        """An NPC of a draft adventure (or of one of its chapters)."""
        registry = stores()
        adventure = writable(adventure_id)
        where = place(adventure, chapter_id)
        ref = statblock_ref(adventure, statblock)  # refused before anything is made
        with tool_errors():
            npc = registry.npcs.create(name, scopes.encode(where), adventure.world_id)
            npc = registry.npcs.update(
                npc.id,
                name=name,
                scope_value=scopes.encode(where),
                roles=roles,
                statblock=ref,
                notes=notes,
                race=race,
                culture=culture,
            )
            if name_parts:
                given = GivenName(text=name, parts=name_parts)
                npc = registry.gamestate.npcs.update(npc.id, given_name=given)
        return npc_info(npc)

    @server.tool()
    def draft_naming_style(
        name: str,
        pattern: Annotated[str, Field(description='The full name: "{given} {family}"')],
        parts: Annotated[
            dict[str, Any],
            Field(
                description="Each part in the pattern, by its kind: list, elements, by_gender, "
                "sounds, learned or kin (documentation/NAMES.md §3)"
            ),
        ],
        race: Annotated[str, Field(description="The people, as the setting names them")] = "",
        culture: Annotated[str, Field(description="A group within them")] = "",
        aliases: Annotated[
            list[str] | None,
            Field(description='Other names of the race, such as the books\' "Dwarf"'),
        ] = None,
        sources: Annotated[
            list[str] | None,
            Field(
                description="Pages you actually read; a plain note when you could not "
                "research (naming_style_rules)"
            ),
        ] = None,
        summary: str = "",
        rewrites: dict[str, str] | None = None,
        banned: list[str] | None = None,
        max_cluster: Annotated[int, Field(ge=0, le=8)] = 3,
        subject: Annotated[
            Subject, Field(description="What it names: people or a kind of place")
        ] = "person",
        language: Annotated[
            str,
            Field(
                description='The language its names are made for ("de"); empty when they '
                "fit any language, as Norse or English names do"
            ),
        ] = "",
        style_id: Annotated[str | None, Field(description="A draft of yours to replace")] = None,
        world_id: Annotated[
            str | None,
            Field(
                description="The world it is for: accepting it adds it to the styles that "
                "world uses (when it limits them)"
            ),
        ] = None,
    ) -> DraftedStyle:
        """Propose a naming style for a people no style fits, after `naming_style_rules`. It
        waits in the gamemaster's workshop and makes no names until accepted."""
        registry = stores()
        text = json.dumps(
            {
                "name": name,
                "subject": subject,
                "language": language,
                "race": race,
                "culture": culture,
                "aliases": aliases or [],
                "sources": sources or [],
                "summary": summary,
                "pattern": pattern,
                "parts": parts,
                "rewrites": rewrites or {},
                "banned": banned or [],
                "max_cluster": max_cluster,
            }
        )
        with tool_errors():
            style = registry.naming_workshop.propose(
                style_from_raw(text), style_id, world_id or None
            )
        samples = registry.naming.sample(style, count=20)
        say = english()
        problems = [say(key, **params) for key, params in sample_notes(samples, 20)]
        if not style.sources:
            problems.append("No sources: name what you read, or say it was not researched.")
        return DraftedStyle(id=style.id, samples=samples, problems=problems)

    @server.tool()
    def set_world_naming_styles(
        world_id: str,
        keys: Annotated[
            list[str],
            Field(
                description="Keys from list_naming_styles (without world_id) of the styles "
                "the world's names come from; empty for every style"
            ),
        ],
    ) -> WorldStyles:
        """Choose the naming styles a world uses, so every name rolled in it — by the
        gamemaster or by `generate_names` — fits the setting. Only styles, not `race:`
        or `subject:` pools; drafts count once accepted. Tell the gamemaster what you
        chose."""
        registry = stores()
        styles = {e.key: e.style.name for e in registry.naming.entries() if e.style is not None}
        unknown = [k for k in keys if k.strip() not in styles]
        if unknown:
            raise ToolError(f"Not naming styles: {', '.join(unknown)}; see list_naming_styles")
        with tool_errors():
            world = registry.worlds.set_naming(world_id, keys)
        return WorldStyles(
            world_id=world.id,
            every=not world.naming_styles,
            styles=[styles[k] for k in world.naming_styles if k in styles],
        )

    @server.tool()
    def update_npc(
        npc_id: str,
        base: Annotated[str, Field(description="The NPC's `version` you last read")],
        name: str | None = None,
        notes: str | None = None,
        roles: list[str] | None = None,
        statblock: Annotated[
            str | None, Field(description='"Name" or "Name|SOURCE"; "" removes it')
        ] = None,
        chapter_id: Annotated[
            str | None, Field(description='Move to this chapter; "" for the whole adventure')
        ] = None,
    ) -> NpcInfo:
        """Change an NPC of a draft; what you leave out stays as it is."""
        registry = stores()
        with tool_errors():
            npc = registry.npcs.get(npc_id)
        adventure = owner(npc.scope)
        where = place(adventure, chapter_id) if chapter_id is not None else npc.scope
        with tool_errors():
            saved = registry.npcs.update(
                npc.id,
                name=name if name is not None else npc.name,
                scope_value=scopes.encode(where),
                roles=roles if roles is not None else list(npc.roles),
                statblock=statblock_ref(adventure, statblock)
                if statblock is not None
                else (npc.statblock or ""),
                notes=notes if notes is not None else npc.notes,
                base=base,
            )
        return npc_info(saved)

    # -- places ---------------------------------------------------------------------

    @server.tool()
    def create_place(
        adventure_id: str,
        name: str,
        notes: Annotated[
            str,
            Field(
                description="What it looks like, who is there ({@npc …}), what happens there "
                "({@encounter …}), what can be found ({@treasure …}) — Paravent Markdown"
            ),
        ],
        tags: Annotated[list[str], Field(description='Such as "village", "dungeon"')] = [],  # noqa: B006
        chapter_id: Annotated[
            str | None, Field(description="Only in this chapter; omit for the whole adventure")
        ] = None,
    ) -> PlaceInfo:
        """A place of a draft adventure (or of one of its chapters), written
        {@place Name} in a chapter; alone on its line it shows as the place's card."""
        registry = stores()
        adventure = writable(adventure_id)
        where = place(adventure, chapter_id)
        with tool_errors():
            location = registry.locations.create(name, scopes.encode(where), adventure.world_id)
            location = registry.locations.update(
                location.id, name=name, scope_value=scopes.encode(where), tags=tags, notes=notes
            )
        return place_info(location)

    @server.tool()
    def update_place(
        place_id: str,
        base: Annotated[str, Field(description="The place's `version` you last read")],
        name: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        chapter_id: Annotated[
            str | None, Field(description='Move to this chapter; "" for the whole adventure')
        ] = None,
    ) -> PlaceInfo:
        """Change a place of a draft; what you leave out stays as it is."""
        registry = stores()
        with tool_errors():
            location = registry.locations.get(place_id)
        adventure = owner(location.scope)
        where = place(adventure, chapter_id) if chapter_id is not None else location.scope
        with tool_errors():
            saved = registry.locations.update(
                location.id,
                name=name if name is not None else location.name,
                scope_value=scopes.encode(where),
                tags=tags if tags is not None else list(location.tags),
                notes=notes if notes is not None else location.notes,
                base=base,
            )
        return place_info(saved)

    # -- encounters -----------------------------------------------------------------

    def planned(adventure: Adventure, monsters: Sequence[MonsterInput]) -> list[EncounterMonster]:
        found, unresolved = resolve_monsters(stores(), world(adventure), monsters)
        if unresolved:
            raise ToolError(
                "Not in this world's books: "
                + ", ".join(unresolved)
                + ". Find creatures with find_monsters or search_reference."
            )
        return found

    @server.tool()
    def create_encounter(
        adventure_id: str,
        name: str,
        monsters: list[MonsterInput],
        notes: Annotated[
            str, Field(description="Setup, terrain, tactics, how it ends (Markdown)")
        ] = "",
        chapter_id: Annotated[
            str | None, Field(description="Only in this chapter; omit for the whole adventure")
        ] = None,
    ) -> EncounterInfo:
        """An encounter of a draft, rated for the adventure's party."""
        registry = stores()
        adventure = writable(adventure_id)
        where = place(adventure, chapter_id)
        plan = planned(adventure, monsters)
        with tool_errors():
            encounter = registry.encounters.create(name, adventure.world_id, where)
            registry.encounters.set_monsters(encounter.id, plan)
            encounter = registry.encounters.update(
                encounter.id, name=name, scope=where, notes=notes, rules=None, party=None
            )
        return encounter_info(encounter)

    @server.tool()
    def update_encounter(
        encounter_id: str,
        name: str | None = None,
        monsters: Annotated[
            list[MonsterInput] | None, Field(description="The whole new plan")
        ] = None,
        notes: str | None = None,
        chapter_id: Annotated[
            str | None, Field(description='Move to this chapter; "" for the whole adventure')
        ] = None,
    ) -> EncounterInfo:
        """Change an encounter of a draft; what you leave out stays as it is."""
        registry = stores()
        with tool_errors():
            encounter = registry.encounters.get(encounter_id)
        adventure = owner(encounter.scope)
        where = place(adventure, chapter_id) if chapter_id is not None else encounter.scope
        with tool_errors():
            if monsters is not None:
                registry.encounters.set_monsters(encounter.id, planned(adventure, monsters))
            encounter = registry.encounters.update(
                encounter.id,
                name=name if name is not None else encounter.name,
                scope=where,
                notes=notes if notes is not None else encounter.notes,
                rules=encounter.rules,
                party=encounter.party,
            )
        return encounter_info(encounter)

    # -- treasure -------------------------------------------------------------------

    def with_hint(name: str, books: SourceSet) -> str:
        """An item not in the books, with its right name when only an addition to it was
        wrong: "Stone of Good Luck (Luckstone)" is the book's "Stone of Good Luck"."""
        plain = name.split("(")[0].strip().casefold()
        result = stores().search.search(plain, books, group="items") if plain else None
        hits = [hit.row.name for g in (result.groups if result else ()) for hit in g.hits]
        same = next((hit for hit in hits if hit.casefold() == plain), None)
        return f"{name} (the books call it {same})" if same else name

    def items_of(
        adventure: Adventure, items: Sequence[ItemInput]
    ) -> tuple[list[TreasureItem], list[str]]:
        resolver = stores().references.resolver()
        books = world(adventure).source_set
        found, unresolved = [], []
        for wanted in items:
            entry = None
            for category in ("item", "baseitem"):
                entry = resolver.resolve_parts(category, wanted.name, wanted.source, books).entry
                if entry is not None:
                    break
            if entry is None:
                unresolved.append(with_hint(wanted.name, books))
                continue
            found.append(
                TreasureItem(name=str(entry.name), source=str(entry.source), count=wanted.count)
            )
        return found, unresolved

    @server.tool()
    def create_treasure(
        adventure_id: str,
        name: str,
        items: list[ItemInput] = [],  # noqa: B006
        coins: CoinsInput | None = None,
        notes: Annotated[str, Field(description="Where it lies, what else is there")] = "",
        chapter_id: Annotated[
            str | None, Field(description="Only in this chapter; omit for the whole adventure")
        ] = None,
    ) -> TreasureInfo:
        """A treasure of a draft: items from the world's books and coins. Items not in
        the books are left out and named in `unresolved`."""
        registry = stores()
        adventure = writable(adventure_id)
        where = place(adventure, chapter_id)
        lines, unresolved = items_of(adventure, items)
        with tool_errors():
            treasure = registry.treasure.create(name, scopes.encode(where), adventure.world_id)
            registry.treasure.set_items(treasure.id, lines)
            treasure = registry.treasure.update(
                treasure.id,
                name=name,
                scope_value=scopes.encode(where),
                coins=Coins(**(coins or CoinsInput()).model_dump()),
                notes=notes,
            )
        return treasure_info(treasure, unresolved)

    @server.tool()
    def update_treasure(
        treasure_id: str,
        base: Annotated[str, Field(description="The treasure's `version` you last read")],
        name: str | None = None,
        items: Annotated[list[ItemInput] | None, Field(description="All items, anew")] = None,
        coins: CoinsInput | None = None,
        notes: str | None = None,
        chapter_id: Annotated[
            str | None, Field(description='Move to this chapter; "" for the whole adventure')
        ] = None,
    ) -> TreasureInfo:
        """Change a treasure of a draft; what you leave out stays as it is."""
        registry = stores()
        with tool_errors():
            treasure = registry.treasure.get(treasure_id)
        adventure = owner(treasure.scope)
        where = place(adventure, chapter_id) if chapter_id is not None else treasure.scope
        unresolved: list[str] = []
        with tool_errors():
            if items is not None:
                lines, unresolved = items_of(adventure, items)
                registry.treasure.set_items(treasure.id, lines)
            saved = registry.treasure.update(
                treasure.id,
                name=name if name is not None else treasure.name,
                scope_value=scopes.encode(where),
                coins=Coins(**coins.model_dump()) if coins is not None else treasure.coins,
                notes=notes if notes is not None else treasure.notes,
                base=base,
            )
        return treasure_info(saved, unresolved)

    @server.tool()
    def roll_treasure(
        treasure_id: str,
        kind: Annotated[str, Field(description="individual (one creature's) or hoard")],
        cr: Annotated[int, Field(ge=0, le=30, description="The challenge rating it is for")],
    ) -> TreasureInfo:
        """Roll on the books' treasure tables (the world's edition) and add the coins and
        items to a treasure of a draft. What the roll names outside the world's books
        goes into the notes."""
        registry = stores()
        with tool_errors():
            treasure = registry.treasure.get(treasure_id)
        adventure = owner(treasure.scope)
        books = world(adventure).source_set
        available = set(registry.loot.books())
        if not available:
            raise ToolError("The corpus holds no treasure tables.")
        book = tables_for(books, available)
        roller = roller_for(registry.compendium, registry.loot, books, book)
        try:
            rolled = roller.roll("individual" if kind == "individual" else "hoard", cr, book)
        except TreasureRollError as exc:
            raise ToolError(f"No treasure table for CR {cr} in {book}.") from exc
        with tool_errors():
            saved = registry.treasure.add_rolled(treasure.id, rolled)
        return treasure_info(saved)

    # -- removing ---------------------------------------------------------------------

    @server.tool()
    def delete(
        kind: Annotated[str, Field(description=f"One of: {', '.join(KINDS)}")],
        id: str,  # noqa: A002
    ) -> str:
        """Remove something of a draft — a chapter, NPC, encounter or treasure, or the
        whole draft adventure. Nothing outside a draft can be removed."""
        registry = stores()
        with tool_errors():
            if kind == "adventure":
                adventure = writable(id)
                # Its NPCs, places, encounters and treasure go with it: a draft's contents
                # must not land in the world.
                registry.adventures.delete(adventure.id, contents=True)
                return f"Deleted the draft “{adventure.name}” and everything in it."
            if kind == "chapter":
                chapter = registry.adventures.get_chapter(id)
                writable(chapter.adventure_id)
                registry.adventures.delete_chapter(chapter.id)
                return f"Deleted the chapter “{chapter.title}”."
            if kind == "npc":
                npc = registry.npcs.get(id)
                owner(npc.scope)
                registry.npcs.delete(npc.id)
                return f"Deleted the NPC “{npc.name}”."
            if kind == "encounter":
                encounter = registry.encounters.get(id)
                owner(encounter.scope)
                registry.encounters.delete(encounter.id)
                return f"Deleted the encounter “{encounter.name}”."
            if kind == "place":
                location = registry.locations.get(id)
                owner(location.scope)
                registry.locations.delete(location.id)
                return f"Deleted the place “{location.name}”."
            if kind == "treasure":
                treasure = registry.treasure.get(id)
                owner(treasure.scope)
                registry.treasure.delete(treasure.id)
                return f"Deleted the treasure “{treasure.name}”."
        raise ToolError(f"Unknown kind {kind!r}; one of: {', '.join(KINDS)}.")


def bearer_of(ctx: Context) -> str:
    """The key this tool call came with (checked again by the caller)."""
    headers = ctx.headers or {}
    return bearer([(k.encode(), v.encode()) for k, v in headers.items()])


__all__ = ["KINDS", "register_drafts"]
