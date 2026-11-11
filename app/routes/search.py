"""Search across the reference set: ``GET /search`` (documentation/DESIGN.md §4).

One route for both uses. The ⌘K palette asks for ``partial=1`` and gets only the results
fragment; anything else — a link, the browser's back button, a browser without
JavaScript — gets the full page with the same form and results.

Any logged-in user may search. ``world`` scopes the results to that world's books;
``all=1`` is the explicit escape to every imported book.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from htpy import Element

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.routes.auth import html
from app.routes.editor import CREATABLE
from app.services.search import GROUPS, Row, SearchHit, SearchResult
from app.ui.combat_pages import combat_url
from app.ui.context import Page
from app.ui.editor_suggest import CALLOUT, INLINE, kind_suggestions
from app.ui.encounter_pages import encounter_url
from app.ui.insert import campaign_group, tag_maker
from app.ui.library_links import (
    chapter_url,
    character_url,
    location_url,
    npc_url,
    treasure_url,
)
from app.ui.markdown import LIBRARY_TAGS
from app.ui.palette import (
    CAMPAIGN,
    INSERT_RESULTS_ID,
    PALETTE,
    SearchTarget,
    results,
    suggestions,
)
from app.ui.search_pages import search_page
from app.ui.sound_panel import SOUNDS_URL
from app.ui.tags import ENTITY_CATEGORY

router = APIRouter(tags=["search"])


@router.get("/search", response_class=HTMLResponse)
async def search(
    _identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    q: str = "",
    group: str = "",
    world: str = "",
    all: str = "",  # noqa: A002 — the query parameter's name
    partial: str = "",
    pick: str = "",
    insert: str = "",
) -> HTMLResponse:
    """Ranked matches in every category (or one), within the world's books.

    ``pick=<encounter id>`` asks for the encounter builder's monster picker instead:
    creatures only, each an "add" button; ``pick=<combat id>`` the same for a running
    fight; ``pick=<treasure id>`` the treasure's item picker. Only the gamemaster
    picks, so for anyone else — or an id that names nothing — it is ignored.

    ``insert=1`` asks for the Markdown editors' insert pop-up: each result inserts its
    tag, and the world's own NPCs, encounters, treasure and chapters come first
    (group ``campaign``).
    """
    references = registry.references
    world_id = references.known_world(world or page.home_world or None)
    target = PALETTE
    if pick and _identity.is_gamemaster:
        encounter = registry.gamestate.encounters.find(pick)
        treasure = registry.gamestate.treasures.find(pick)
        combat = registry.gamestate.combats.find(pick)
        if combat is not None:
            url = combat_url(combat.id)
            target = SearchTarget(
                action=url,
                dom_id="picker-results",
                pick_action=f"{url}/monsters",
                pick=combat.id,
            )
            group = "bestiary"
        elif encounter is not None:
            url = encounter_url(encounter.id)
            target = SearchTarget(
                action=url,
                dom_id="picker-results",
                pick_action=f"{url}/monsters",
                pick=encounter.id,
            )
            group = "bestiary"
        elif treasure is not None:
            url = treasure_url(treasure.id)
            target = SearchTarget(
                action=url,
                dom_id="picker-results",
                pick_action=f"{url}/items",
                pick=treasure.id,
                pick_hint="treasure.hint_pick",
            )
            group = "items"
    source_set = references.source_set(world_id)
    inserting = insert == "1" and _identity.is_gamemaster
    if inserting:
        target = SearchTarget(
            dom_id=INSERT_RESULTS_ID,
            insert=True,
            tag=tag_maker(references.resolver(), source_set),
        )
    result = registry.search.search(
        q[:100],
        source_set,
        group=None if group == CAMPAIGN else group or None,
        all_books=all == "1",
        term=page.t.terms(),
    )
    if page.reader is not None:
        result = _for_player(page, result)
    if inserting:
        own = campaign_group(q[:100], _campaign(registry, world_id)) if world_id else None
        shown = () if group == CAMPAIGN else result.groups
        result = replace(result, group=group or None, groups=(own, *shown) if own else shown)
    if partial:
        return html(results(page, result, world_id, target))
    return html(search_page(page, result, world_id))


def _for_player(page: Page, result: SearchResult) -> SearchResult:
    """Only what the player may read (documentation/PLAYER-VIEW.md): a rulebook heading
    by its book, an entry as itself. What else matched is not counted, not even as
    outside the world's books."""

    def readable(row: Row) -> bool:
        return page.reads_book(row.source) if row.url else page.can_read(row.category, row.entry)

    groups = []
    for group in result.groups:
        hits = tuple(h for h in group.hits if readable(h.row))
        if hits:
            dropped = len(group.hits) - len(hits)
            groups.append(replace(group, hits=hits, total=max(len(hits), group.total - dropped)))
    return replace(result, groups=tuple(groups), outside=0)


# How many suggestions the editor's drop-down shows.
SUGGESTIONS = 12


@router.get("/search/kinds", response_class=HTMLResponse)
async def kinds(
    identity: IdentityDep, page: PageDep, context: str = "", q: str = ""
) -> HTMLResponse:
    """The editor's drop-down before a name: which kind ``{@`` or ``> [!`` may take, those
    starting with ``q``."""
    if not identity.is_gamemaster or context not in (INLINE, CALLOUT):
        return html(suggestions(page, (), lambda _row: None))
    return html(kind_suggestions(page, context, q[:30]))


@router.get("/search/suggest", response_class=HTMLResponse)
async def suggest(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    kind: str = "",
    q: str = "",
    world: str = "",
    scope: str = "",
    browse: str = "",
) -> HTMLResponse:
    """The Markdown editor's drop-down: entries of one kind whose name matches ``q``, as
    the editor completes them after ``{@spell F`` — the reference as short as the world
    allows (``Fireball``, ``Fireball|PHB``), an NPC by its name.

    ``browse=1`` with nothing typed (Ctrl+Space after ``{@spell ``) lists the kind
    anyway: the world's own records by name, a reference kind's entries in the world's
    books that the corpus links to most.

    ``kind`` is a tag's name (``spell``, ``creature``, ``item``…), ``statblock`` (a
    creature), or one of the campaign's own (``npc``, ``encounter``, ``treasure``,
    ``character``, ``chapter``). The gamemaster's only, like the editors.

    With ``scope`` (the editor's record, ``c:<chapter id>``…), an NPC, encounter or
    treasure of a name nothing has yet is offered to create there.
    """
    references = registry.references
    world_id = references.known_world(world or page.home_world or None)
    query = q.strip()[:60]
    if not identity.is_gamemaster or not (query or browse):
        return html(suggestions(page, (), lambda _row: None))
    kind = "creature" if kind == "statblock" else kind
    if not query:
        return html(_browse(page, registry, kind, world_id))
    if kind in LIBRARY_TAGS:
        own = campaign_group(query, _campaign(registry, world_id) if world_id else (), SUGGESTIONS)
        hits = [h for h in (own.hits if own else ()) if h.row.category == kind]
        exact = any(h.row.name.casefold() == query.casefold() for h in hits)
        create = query if scope and kind in CREATABLE and not exact else None
        where = scope.partition(":")[0] if scope.partition(":")[0] in ("c", "a") else "w"
        return html(
            suggestions(page, hits, lambda row: row.name, create=(kind, create), where=where)
        )
    categories = ENTITY_CATEGORY.get(kind)
    if not categories:
        return html(suggestions(page, (), lambda _row: None))
    source_set = references.source_set(world_id)
    group = next((key for key, members in GROUPS if categories[0] in members), None)
    found = registry.search.search(query, source_set, group=group, term=page.t.terms())
    hits = [h for g in found.groups for h in g.hits if h.row.category in categories]
    reference = _reference(tag_maker(references.resolver(), source_set))
    return html(suggestions(page, hits[:SUGGESTIONS], reference))


def _browse(page: PageDep, registry: RegistryDep, kind: str, world_id: str | None) -> Element:
    """The drop-down for a kind with no name typed yet."""
    if kind in LIBRARY_TAGS:
        own = sorted(
            (r for r in (_campaign(registry, world_id) if world_id else ()) if r[0] == kind),
            key=lambda r: r[1].casefold(),
        )
        hits = [
            SearchHit(Row(k, name, "", None, url=url, context=context), 0)
            for k, name, url, context in own[:SUGGESTIONS]
        ]
        return suggestions(page, hits, lambda row: row.name)
    categories = ENTITY_CATEGORY.get(kind)
    if not categories:
        return suggestions(page, (), lambda _row: None)
    references = registry.references
    source_set = references.source_set(world_id)
    links = registry.link_counts.counts()
    hits = registry.search.browse(
        categories,
        source_set,
        weight=lambda row: links.get((categories[0], row.name.lower()), 0),
        limit=SUGGESTIONS,
        term=page.t.terms(),
    )
    return suggestions(page, hits, _reference(tag_maker(references.resolver(), source_set)))


def _reference(tag: Callable[[Row], str | None]) -> Callable[[Row], str | None]:
    """What a row completes to after ``{@spell ``: ``Fireball|PHB`` out of its tag."""

    def reference(row: Row) -> str | None:
        written = tag(row)  # "{@spell Fireball|PHB}" → "Fireball|PHB"
        return written.split(" ", 1)[1][:-1] if written else None

    return reference


def _campaign(registry: RegistryDep, world_id: str) -> Iterator[tuple[str, str, str, str]]:
    """The world's NPCs, encounters, treasure, characters and chapters, for the insert
    pop-up."""
    for npc in registry.npcs.list(world_id):
        yield "npc", npc.name, npc_url(npc.id), ""
    for encounter in registry.encounters.list(world_id):
        yield "encounter", encounter.name, encounter_url(encounter.id), ""
    for treasure in registry.treasure.list(world_id):
        yield "treasure", treasure.name, treasure_url(treasure.id), ""
    for location in registry.locations.list(world_id):
        yield "place", location.name, location_url(location.id), ""
    for pc in registry.characters.in_world(world_id):
        yield "character", pc.name, character_url(pc.id), ""
    for adventure in registry.adventures.adventures(world_id):
        for chapter in registry.adventures.chapters(adventure.id):
            yield "chapter", chapter.title, chapter_url(chapter.id), adventure.name
    for name in registry.sounds.names():
        yield "sound", name, SOUNDS_URL, ""
