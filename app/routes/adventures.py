"""A world's adventures and chapters (documentation/ADVENTURES.md).

* ``GET  /gm/worlds/{id}``                   — the world: cover, description, adventures
* ``POST /gm/worlds/{id}/adventures``        — start an adventure
* ``/gm/adventures/{id}``                    — read; ``/edit``, ``/delete``, ``/move``
* ``POST /gm/adventures/{id}/chapters``      — start a chapter
* ``/gm/chapters/{id}``                      — read; ``/edit``, ``/delete``, ``/move``
* ``POST /gm/chapters/{id}/preview``         — the editor's live preview (a fragment)
* ``GET  /gm/chapters/{id}/live``            — the chapter's text (a fragment)
* ``GET  /gm/chapters/{id}/events``          — its revision over SSE, so a page open at
  the table follows edits made in another editor
* ``POST /gm/media/{record id}``              — upload an image; ``/cover`` picks the cover

Everything here is the gamemaster's. Records are Markdown files; deleting one moves it
to ``library/.trash/``.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import replace
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from htpy import Element, Node

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Adventure, Scope, SourceSet
from app.routes.auth import html, redirect, safe_next
from app.services import image_effects, scope
from app.services.adventures import (
    AdventureError,
    AdventureView,
    ChapterView,
    EditConflict,
    text_version,
)
from app.services.creature_builder import HOMEBREW_SOURCE
from app.services.difficulty import rules_for
from app.services.encounters import EncounterError
from app.services.events import revision_stream
from app.services.media import MAX_BYTES, MediaError
from app.store.maps import BookMap
from app.store.registry import StoreRegistry
from app.ui.adventure_pages import (
    DraftReview,
    ProseServices,
    adventure_delete_page,
    adventure_form_page,
    adventure_page,
    chapter_body,
    chapter_delete_page,
    chapter_form_page,
    chapter_page,
    check_panel,
    draft_review_panel,
    prose_context,
    render_body,
    revision_waiting,
    world_page,
)
from app.ui.book_pages import anchor_id, area_links, book_chapter, book_picture, creatures_in
from app.ui.cast_pages import creatures_panel, holdings_panel
from app.ui.context import Page
from app.ui.journal_pages import share_href
from app.ui.library_links import (
    ADVENTURES_URL,
    CHAPTERS_URL,
    MEDIA_UPLOAD_URL,
    WORLDS_URL,
    adventure_url,
    chapter_url,
    world_url,
)
from app.ui.maps import maps_panel
from app.ui.markdown import MarkdownContext, Rendered
from app.ui.prose_check import Checks, Problem, check
from app.ui.tags import ENTITY_CATEGORY

router = APIRouter(tags=["adventures"])


def _services(registry: StoreRegistry) -> ProseServices:
    return ProseServices(
        registry.adventures,
        registry.encounters,
        registry.references,
        registry.npcs,
        registry.treasure,
        registry.combats,
        registry.characters,
        registry.locations,
        registry.sounds,
    )


def _holdings(
    page: Page, registry: StoreRegistry, here: Scope, heading: str, *, open_: bool = False
) -> Element:
    """What an adventure or chapter holds: its NPCs, treasure and encounters."""
    return holdings_panel(
        page,
        world=registry.adventures.world(here.world_id),
        place=scope.encode(here),
        npcs=registry.npcs.list(here.world_id, here),
        places=registry.locations.list(here.world_id, here),
        treasures=registry.treasure.list(here.world_id, here),
        encounters=registry.encounters.within(here),
        heading=heading,
        open_=open_,
    )


def _with(url: str, **params: str) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{url}?{query}" if query else url


def upload_messages(page: Page, uploaded: str, error: str) -> tuple[str | None, str | None]:
    """The notice or error an upload redirected back with."""
    notice_text = page.t("adventures.uploaded", path=uploaded) if uploaded else None
    # Only the media errors' own keys are shown; anything else in the URL is ignored.
    message = None
    if error.startswith("media.error.") or error.startswith("adventures.error."):
        message = page.t(error, mb=MAX_BYTES // (1024 * 1024))
    return notice_text, message


# -- the world -------------------------------------------------------------------------


def _world_context(page: Page, registry: StoreRegistry, world_id: str) -> MarkdownContext:
    world = registry.adventures.world(world_id)
    return prose_context(
        page,
        _services(registry),
        record_id=world.id,
        world=world,
        adventure=None,
        source_set=world.source_set,
    )


def _world(
    page: Page,
    registry: StoreRegistry,
    world_id: str,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    service = registry.adventures
    world = service.world(world_id)
    adventures = [(a, service.chapter_count(a.id)) for a in service.adventures(world_id)]
    description = render_body(world.description, _world_context(page, registry, world_id))
    counts = (len(registry.npcs.list(world_id)), len(registry.treasure.list(world_id)))
    return html(
        world_page(
            page,
            world,
            adventures,
            description,
            bosses=registry.npcs.bosses(world_id),
            counts=counts,
            sessions=len(registry.session_notes.list(world_id)),
            places=len(registry.locations.list(world_id)),
            books=registry.adventures.published.all(),
            notice_text=notice_text,
            error=error,
        ),
        status_code=status_code,
    )


@router.get(f"{WORLDS_URL}/{{world_id}}", response_class=HTMLResponse)
async def show_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    created: str = "",
    updated: str = "",
    deleted: str = "",
) -> HTMLResponse:
    """A world: its cover, its description and its adventures."""
    world = registry.adventures.world(world_id)
    notice_text = None
    if created:
        notice_text = page.t("worlds.created", name=world.name)
    elif updated:
        notice_text = page.t("worlds.updated", name=world.name)
    elif deleted:
        notice_text = page.t("adventures.deleted")
    return _world(page, registry, world_id, notice_text=notice_text)


@router.post(f"{WORLDS_URL}/{{world_id}}/adventures", response_class=HTMLResponse)
async def create_adventure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
) -> Response:
    """Start an adventure and open it."""
    try:
        adventure = registry.adventures.create(world_id, name)
    except AdventureError as exc:
        return _world(page, registry, world_id, error=page.t.error(exc), status_code=422)
    return redirect(adventure_url(adventure.id))


@router.post(f"{WORLDS_URL}/{{world_id}}/adventures/from-book", response_class=HTMLResponse)
async def create_from_book(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    book: Annotated[str, Form()] = "",
) -> Response:
    """Start an adventure on top of a published one: a chapter per section of the book."""
    try:
        adventure = registry.adventures.create_from_book(world_id, book)
    except AdventureError as exc:
        return _world(page, registry, world_id, error=page.t.error(exc), status_code=422)
    return redirect(adventure_url(adventure.id))


# -- an adventure ----------------------------------------------------------------------


def _adventure_body(page: Page, registry: StoreRegistry, adventure_id: str) -> Rendered:
    view = registry.adventures.view(adventure_id)
    ctx = prose_context(
        page,
        _services(registry),
        record_id=view.adventure.id,
        world=view.world,
        adventure=view.adventure,
        source_set=view.source_set,
    )
    return render_body(view.adventure.body, ctx)


def _adventure(
    page: Page,
    registry: StoreRegistry,
    adventure_id: str,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    view = registry.adventures.view(adventure_id)
    body = _adventure_body(page, registry, adventure_id)
    here = Scope(world_id=view.world.id, adventure_id=adventure_id)
    holdings = _holdings(page, registry, here, page.t("holdings.adventure"), open_=True)
    adv = view.adventure
    review = (
        draft_review_panel(
            page,
            adv.id,
            _draft_review(page, registry, adv, here),
            registry.revisions.changes(adv) if adv.revision_of else None,
        )
        if adv.status == "draft" and adv.drafted_by
        else None
    )
    waiting = registry.revisions.open_for(adv.id) if not adv.revision_of else None
    if waiting is not None:
        review = revision_waiting(page, waiting.id)
    return html(
        adventure_page(
            page,
            view,
            body,
            holdings=holdings,
            notice_text=notice_text,
            error=error,
            review=review,
            maps=_maps(page, registry, view),
        ),
        status_code=status_code,
    )


def _maps(page: Page, registry: StoreRegistry, view: AdventureView) -> Node:
    """For an adventure started from a book: the book's maps, each leading to the
    chapter that shows its section."""
    if view.book is None:
        return None
    maps = registry.maps.of(view.book.id)
    body = registry.published.body(view.book.id) if maps else None
    if body is None:
        return None
    sections = {c.section: c.id for c in view.chapters if c.section}
    chapters = {section: chapter_url(ident) for section, ident in sections.items()}

    def where(found: BookMap) -> str | None:
        if not 0 <= found.chapter < len(body.sections):
            return None
        url = chapters.get(body.sections[found.chapter].id)
        if url is None:
            return None
        return f"{url}#{anchor_id(found.id)}" if found.id else url

    def share(found: BookMap, path: str) -> str | None:
        """Handed out from the chapter that shows the map, back to this page."""
        if not 0 <= found.chapter < len(body.sections):
            return None
        chapter = sections.get(body.sections[found.chapter].id)
        back = adventure_url(view.adventure.id)
        return share_href(chapter, "bookimage", path, back) if chapter else None

    picture = book_picture(registry.settings.images_root)
    return maps_panel(page, maps, where, picture, share)


def _own_creatures(registry: StoreRegistry, adventure: Adventure) -> list[str]:
    """The creature-builder creatures its encounters plan, by name (MCP.md §10)."""
    here = Scope(world_id=adventure.world_id, adventure_id=adventure.id)
    return [
        m.name
        for e in registry.encounters.within(here)
        for m in e.monsters
        if m.source == HOMEBREW_SOURCE
    ]


def _draft_review(
    page: Page, registry: StoreRegistry, adventure: Adventure, here: Scope
) -> DraftReview:
    """What a draft Claude wrote holds (documentation/MCP.md §6)."""
    key = next((k for k in registry.mcp_tokens.list() if k.id == adventure.drafted_by), None)
    members = [
        pc
        for pc in registry.characters.in_world(adventure.world_id)
        if pc.total_level > 0 and (not adventure.party or pc.id in adventure.party)
    ]
    encounters = sorted(registry.encounters.within(here), key=lambda e: e.name.casefold())
    rated = [(e, registry.encounters.view(e).rating) for e in encounters]
    world = registry.adventures.world(adventure.world_id)
    rules = rules_for(world.source_set)
    return DraftReview(
        key_name=key.name if key else None,
        party=tuple(pc.name for pc in members),
        chapters=tuple(
            (ch, len(chapter_problems(page, registry, ch.id, ch.body)))
            for ch in registry.adventures.chapters(adventure.id)
        ),
        encounters=tuple(rated),
        npcs=len(registry.npcs.list(here.world_id, here)),
        treasures=len(registry.treasure.list(here.world_id, here)),
        fight_xp=sum(r.adjusted_xp for _e, r in rated),
        creatures=tuple(
            d
            for n in dict.fromkeys(_own_creatures(registry, adventure))
            if (d := registry.creatures.named(n, adventure.world_id)) and d.drafted_by
        ),
        day_xp=registry.encounters.difficulty.day_budget(rules, [pc.total_level for pc in members]),
    )


@router.post(f"{ADVENTURES_URL}/{{adventure_id}}/accept")
async def accept_draft(_gm: GamemasterDep, registry: RegistryDep, adventure_id: str) -> Response:
    """Take a draft over: planned from now on, and Claude reads it only (MCP.md §6)."""
    adventure = registry.adventures.get(adventure_id)
    # Claude's creatures its fights plan are accepted and published with it.
    registry.creatures.accept_named(adventure.world_id, _own_creatures(registry, adventure))
    if adventure.revision_of:
        changes = registry.revisions.accept(adventure_id)
        kept = sum(1 for c in changes if c.conflict or c.held)
        return redirect(
            _with(
                adventure_url(adventure.revision_of),
                revised=str(len(changes) - kept),
                kept=str(kept),
            )
        )
    if adventure.status == "draft":
        registry.gamestate.adventures.update(adventure_id, status="planned")
    return redirect(_with(adventure_url(adventure_id), accepted="1"))


@router.get(f"{ADVENTURES_URL}/{{adventure_id}}", response_class=HTMLResponse)
async def show_adventure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    adventure_id: str,
    saved: str = "",
    deleted: str = "",
    accepted: str = "",
    revised: str = "",
    kept: str = "",
) -> HTMLResponse:
    """An adventure: its text and its chapters."""
    notice_text = None
    if revised:
        notice_text = page.t("revision.accepted", done=revised, kept=kept or "0")
    elif saved:
        notice_text = page.t("adventures.saved")
    elif accepted:
        notice_text = page.t("draft.accepted")
    elif deleted:
        notice_text = page.t("chapters.deleted")
    return _adventure(page, registry, adventure_id, notice_text=notice_text)


def _adventure_form(
    page: Page,
    registry: StoreRegistry,
    adventure_id: str,
    *,
    values: dict[str, str] | None = None,
    base: str | None = None,
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
    status_code: int = 200,
    party: list[str] | None = None,
) -> HTMLResponse:
    view = registry.adventures.view(adventure_id)
    adv = view.adventure
    current = {
        "name": adv.name,
        "summary": adv.summary,
        "status": adv.status,
        "level_start": str(adv.level.start) if adv.level else "",
        "level_end": str(adv.level.end) if adv.level else "",
        "body": adv.body,
    } | (values or {})
    return html(
        adventure_form_page(
            page,
            view,
            registry.adventures.images(adv.id),
            name=current["name"],
            summary_text=current["summary"],
            status=current["status"],
            level_start=current["level_start"],
            level_end=current["level_end"],
            body=current["body"],
            base=text_version(adv.body) if base is None else base,
            conflict=conflict,
            error=error,
            notice_text=notice_text,
            characters=[
                (pc.id, pc.name or "?")
                for pc in registry.characters.in_world(adv.world_id)
                if pc.total_level > 0
            ],
            party=party if party is not None else (adv.party or []),
        ),
        status_code=status_code,
    )


@router.get(f"{ADVENTURES_URL}/{{adventure_id}}/edit", response_class=HTMLResponse)
async def edit_adventure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    adventure_id: str,
    uploaded: str = "",
    error: str = "",
) -> HTMLResponse:
    """The adventure form."""
    notice_text, message = upload_messages(page, uploaded, error)
    return _adventure_form(page, registry, adventure_id, notice_text=notice_text, error=message)


def _level(value: str) -> int | None:
    return int(value) if value.strip().isdigit() else None


@router.post(f"{ADVENTURES_URL}/{{adventure_id}}/edit", response_class=HTMLResponse)
async def save_adventure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    adventure_id: str,
    name: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "planned",
    level_start: Annotated[str, Form()] = "",
    level_end: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    base: Annotated[str, Form()] = "",
    party: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """Save an adventure; one made from a text that changed since is shown again beside
    the text saved now (:class:`EditConflict`)."""
    try:
        registry.adventures.set_party(adventure_id, party or [])
        registry.adventures.update(
            adventure_id,
            name=name,
            summary=summary,
            status=status,
            level_start=_level(level_start),
            level_end=_level(level_end),
            body=body,
            base=base or None,
        )
    except AdventureError as exc:
        conflict = exc.current if isinstance(exc, EditConflict) else None
        values = {
            "name": name,
            "summary": summary,
            "status": status,
            "level_start": level_start,
            "level_end": level_end,
            "body": body,
        }
        return _adventure_form(
            page,
            registry,
            adventure_id,
            values=values,
            base=text_version(conflict) if conflict is not None else base,
            conflict=conflict,
            error=page.t.error(exc),
            status_code=409 if conflict is not None else 422,
            party=party or [],
        )
    return redirect(_with(adventure_url(adventure_id), saved="1"))


@router.get(f"{ADVENTURES_URL}/{{adventure_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_adventure(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, adventure_id: str
) -> HTMLResponse:
    """Ask first — and what happens to what is placed in it."""
    service = registry.adventures
    view = service.view(adventure_id)
    here = Scope(world_id=view.world.id, adventure_id=adventure_id)
    return html(adventure_delete_page(page, view, service.contents(here)))


@router.post(f"{ADVENTURES_URL}/{{adventure_id}}/delete")
async def delete_adventure(
    _gm: GamemasterDep,
    registry: RegistryDep,
    adventure_id: str,
    contents: Annotated[str, Form()] = "keep",
) -> Response:
    """Move the adventure to the trash; with ``contents=all`` its NPCs, places,
    encounters and treasure are deleted too, else they move up to the world."""
    adventure = registry.adventures.get(adventure_id)
    world_id = adventure.world_id
    # A revision's records are copies: they go with it, and the original is untouched.
    registry.adventures.delete(
        adventure_id, contents=contents == "all" or bool(adventure.revision_of)
    )
    if adventure.revision_of:
        return redirect(adventure_url(adventure.revision_of))
    return redirect(_with(world_url(world_id), deleted="1"))


@router.post(f"{ADVENTURES_URL}/{{adventure_id}}/move")
async def move_adventure(
    _gm: GamemasterDep,
    registry: RegistryDep,
    adventure_id: str,
    step: Annotated[int, Form()] = 0,
) -> Response:
    """One place up or down in the world's list."""
    world_id = registry.adventures.get(adventure_id).world_id
    registry.adventures.move("adventure", adventure_id, step)
    return redirect(world_url(world_id))


@router.post(f"{ADVENTURES_URL}/{{adventure_id}}/chapters", response_class=HTMLResponse)
async def create_chapter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    adventure_id: str,
    title: Annotated[str, Form()] = "",
) -> Response:
    """Start a chapter and open its editor."""
    try:
        chapter = registry.adventures.create_chapter(adventure_id, title)
    except AdventureError as exc:
        return _adventure(page, registry, adventure_id, error=page.t.error(exc), status_code=422)
    return redirect(f"{chapter_url(chapter.id)}/edit")


# -- a chapter -------------------------------------------------------------------------


def _chapter_context(page: Page, registry: StoreRegistry, chapter_id: str) -> MarkdownContext:
    view = registry.adventures.chapter_view(chapter_id)
    return prose_context(
        page,
        _services(registry),
        record_id=view.chapter.id,
        world=view.world,
        adventure=view.adventure,
        source_set=view.source_set,
        chapter_id=view.chapter.id,
    )


def _chapter_text(page: Page, registry: StoreRegistry, view: ChapterView) -> Rendered:
    """A chapter's text: its own Markdown, or its book section with the Markdown woven in;
    each box and picture with its share button (documentation/JOURNAL.md)."""
    back = chapter_url(view.chapter.id)
    ctx = replace(
        _chapter_context(page, registry, view.chapter.id),
        share=lambda kind, key: share_href(view.chapter.id, kind, key, back),
    )
    book = registry.adventures.book_section(view)
    if book is None:
        return render_body(view.chapter.body, ctx)
    body, part = book
    url = chapter_url(view.chapter.id)
    picture = book_picture(registry.settings.images_root)
    return book_chapter(
        page,
        body,
        part,
        ctx,
        view.chapter.body,
        lambda entry: f"{url}/edit?at={entry}#f-body",
        picture,
        # A map's areas lead to their text, here or in the chapter showing their section.
        area_links(
            body,
            part.id,
            {
                c.section: chapter_url(c.id)
                for c in registry.adventures.chapters(view.adventure.id)
                if c.section
            },
        ),
    )


def _creatures(page: Page, registry: StoreRegistry, view: ChapterView) -> Node:
    """For a book chapter: the creatures its section names, each a click from an encounter."""
    book = registry.adventures.book_section(view)
    if book is None:
        return None
    mentions = creatures_in(book[1], registry.references.resolver(), view.source_set)
    ch = view.chapter
    here = Scope(world_id=ch.world_id, adventure_id=ch.adventure_id, chapter_id=ch.id)
    return creatures_panel(page, view, mentions, registry.encounters.within(here))


@router.get(f"{CHAPTERS_URL}/{{chapter_id}}", response_class=HTMLResponse)
async def show_chapter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter_id: str,
    saved: str = "",
    added: str = "",
    error: str = "",
) -> HTMLResponse:
    """A chapter to run the table from."""
    view = registry.adventures.chapter_view(chapter_id)
    body = _chapter_text(page, registry, view)
    store = registry.gamestate.prose
    ch = view.chapter
    here = Scope(world_id=ch.world_id, adventure_id=ch.adventure_id, chapter_id=ch.id)
    return html(
        chapter_page(
            page,
            view,
            body,
            store.revision(chapter_id),
            holdings=[
                _creatures(page, registry, view),
                _holdings(page, registry, here, page.t("holdings.chapter")),
            ],
            notice_text=page.t("chapters.saved")
            if saved
            else page.t("book.added", name=added)
            if added
            else None,
            error=store.error(chapter_id)
            or (page.t(error, ref="") if error.startswith("encounters.error.") else None),
        )
    )


@router.get(f"{CHAPTERS_URL}/{{chapter_id}}/live", response_class=HTMLResponse)
async def chapter_live(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, chapter_id: str
) -> HTMLResponse:
    """The chapter's contents and text, for the page to swap in after a change."""
    view = registry.adventures.chapter_view(chapter_id)
    body = _chapter_text(page, registry, view)
    return html(chapter_body(page, view, body, registry.gamestate.prose.revision(chapter_id)))


@router.get(f"{CHAPTERS_URL}/{{chapter_id}}/events")
async def chapter_events(
    request: Request, _gm: GamemasterDep, registry: RegistryDep, chapter_id: str
) -> StreamingResponse:
    """The chapter's revision; the page re-fetches its text when it moves."""
    registry.adventures.get_chapter(chapter_id)
    current = registry.gamestate.prose.revision(chapter_id)
    return StreamingResponse(
        revision_stream(registry.events, chapter_id, current, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _chapter_form(
    page: Page,
    registry: StoreRegistry,
    chapter_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    body: str | None = None,
    base: str | None = None,
    conflict: str | None = None,
    at: str = "",
    error: str | None = None,
    notice_text: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    view = registry.adventures.chapter_view(chapter_id)
    ch = view.chapter
    text = ch.body if body is None else body
    book = registry.adventures.book_section(view)
    places = book[0].places(book[1].id) if book else []
    if at and book and not re.search(rf"^\s*:{{3,}}\s*at\s+{re.escape(at)}\s*$", text, re.M):
        # "Add a note here" from the chapter: open the editor with the block ready.
        text = f"{text.rstrip()}\n\n:::: at {at}\n\n::::\n".lstrip()
    context = _chapter_context(page, registry, chapter_id)
    preview = render_body(text, context)
    return html(
        chapter_form_page(
            page,
            view,
            registry.adventures.images(ch.id),
            preview,
            problems=check(text, context, _chapter_checks(registry, view)),
            places=places,
            title=ch.title if title is None else title,
            status=ch.status if status is None else status,
            body=text,
            base=text_version(ch.body) if base is None else base,
            conflict=conflict,
            error=error or registry.gamestate.prose.error(chapter_id),
            notice_text=notice_text,
        ),
        status_code=status_code,
    )


@router.get(f"{CHAPTERS_URL}/{{chapter_id}}/edit", response_class=HTMLResponse)
async def edit_chapter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter_id: str,
    uploaded: str = "",
    error: str = "",
    at: str = "",
) -> HTMLResponse:
    """The chapter's editor; ``?at=<entry id>`` opens it with a note block for that entry."""
    notice_text, message = upload_messages(page, uploaded, error)
    return _chapter_form(
        page, registry, chapter_id, at=at[:32], notice_text=notice_text, error=message
    )


@router.post(f"{CHAPTERS_URL}/{{chapter_id}}/edit", response_class=HTMLResponse)
async def save_chapter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter_id: str,
    title: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "planned",
    body: Annotated[str, Form()] = "",
    base: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "save",
) -> Response:
    """Save a chapter, or (without JavaScript) show the preview of what was typed.

    ``base`` is the fingerprint of the text the editor opened. When the text on disk has
    changed since — a hand edit, another tab — nothing is written: the editor comes back
    with the typed text and, beside it, the text saved now; saving again replaces it."""
    if action == "preview":
        return _chapter_form(
            page, registry, chapter_id, title=title, status=status, body=body, base=base
        )
    try:
        registry.adventures.update_chapter(
            chapter_id, title=title, status=status, body=body, base=base or None
        )
    except AdventureError as exc:
        conflict = exc.current if isinstance(exc, EditConflict) else None
        return _chapter_form(
            page,
            registry,
            chapter_id,
            title=title,
            status=status,
            body=body,
            base=text_version(conflict) if conflict is not None else base,
            conflict=conflict,
            error=page.t.error(exc),
            status_code=409 if conflict is not None else 422,
        )
    return redirect(_with(chapter_url(chapter_id), saved="1"))


@router.post(f"{CHAPTERS_URL}/{{chapter_id}}/preview", response_class=HTMLResponse)
async def preview_chapter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter_id: str,
    body: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """What ``body`` renders to in this chapter — nothing is saved."""
    rendered = render_body(body, _chapter_context(page, registry, chapter_id))
    return HTMLResponse(str(rendered.html))


@router.post(f"{CHAPTERS_URL}/{{chapter_id}}/check", response_class=HTMLResponse)
async def check_chapter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter_id: str,
    body: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """What in ``body`` leads nowhere in this chapter (ADVENTURES.md §5.7) — the editor's
    panel beside the preview; nothing is saved."""
    return html(check_panel(page, chapter_problems(page, registry, chapter_id, body)))


def chapter_problems(
    page: Page, registry: StoreRegistry, chapter_id: str, body: str
) -> list[Problem]:
    """What in ``body`` leads nowhere when it is this chapter's text (ADVENTURES.md
    §5.7); the editor's panel and the MCP tools (documentation/MCP.md §4) ask it."""
    view = registry.adventures.chapter_view(chapter_id)
    context = _chapter_context(page, registry, chapter_id)
    return check(body, context, _chapter_checks(registry, view))


def _chapter_checks(registry: StoreRegistry, view: ChapterView) -> Checks:
    here = Scope(
        world_id=view.world.id,
        adventure_id=view.adventure.id if view.adventure else None,
        chapter_id=view.chapter.id,
    )
    return _checks(registry, here, view.source_set)


def notes_problems(page: Page, registry: StoreRegistry, here: Scope, text: str) -> list[Problem]:
    """The same check of notes written at ``here``: an NPC's, a place's, a fight's."""
    adventures = registry.adventures
    books = (
        adventures.view(here.adventure_id).source_set
        if here.adventure_id
        else adventures.world(here.world_id).source_set
    )
    from app.routes.paravent_markdown import scoped_context

    return check(text, scoped_context(page, registry, here), _checks(registry, here, books))


def _checks(registry: StoreRegistry, here: Scope, books: SourceSet) -> Checks:
    """The chapter check's lookups beyond the Markdown: empty encounters, near names."""

    def empty(name: str) -> bool | None:
        encounter = registry.encounters.find(name, here)
        return None if encounter is None else not encounter.monsters

    def nearest(tag: str, name: str) -> list[str]:
        names: dict[str, str] = {}
        for category in ENTITY_CATEGORY.get(tag, ()):
            for entry in registry.compendium.all(category):
                if books.allows(getattr(entry, "source", None)):
                    shown = str(getattr(entry, "name", ""))
                    names.setdefault(shown.lower(), shown)
        close = difflib.get_close_matches(name.lower(), list(names), n=3, cutoff=0.75)
        return [names[x] for x in close]

    return Checks(empty_encounter=empty, nearest=nearest)


@router.post(f"{CHAPTERS_URL}/{{chapter_id}}/creatures")
async def add_creature(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter_id: str,
    ref: Annotated[str, Form()] = "",
    encounter: Annotated[str, Form()] = "",
) -> Response:
    """Add a creature the book names to one of the chapter's encounters, or a new one."""
    ch = registry.adventures.get_chapter(chapter_id)
    service = registry.encounters
    here = Scope(world_id=ch.world_id, adventure_id=ch.adventure_id, chapter_id=ch.id)
    name, _, source = ref.partition("|")
    if service.monster(name.strip(), source.strip()) is None:
        # Checked first, so a bad reference does not leave a new, empty encounter behind.
        return redirect(
            f"{chapter_url(chapter_id)}?{urlencode({'error': 'encounters.error.monster'})}"
        )
    target = next((e for e in service.within(here) if e.id == encounter), None)
    if target is None:
        target = service.create(ch.title, ch.world_id, here)
    try:
        service.add_monster(target.id, ref)
    except EncounterError as exc:
        return redirect(f"{chapter_url(chapter_id)}?{urlencode({'error': exc.key})}")
    query = urlencode({"added": f"{name.strip()} → {target.name}"})
    return redirect(f"{chapter_url(chapter_id)}?{query}#creatures")


@router.get(f"{CHAPTERS_URL}/{{chapter_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_chapter(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, chapter_id: str
) -> HTMLResponse:
    """Ask first — and what happens to what is placed in it."""
    service = registry.adventures
    view = service.chapter_view(chapter_id)
    chapter = view.chapter
    here = Scope(
        world_id=chapter.world_id, adventure_id=chapter.adventure_id, chapter_id=chapter.id
    )
    return html(chapter_delete_page(page, view, service.contents(here)))


@router.post(f"{CHAPTERS_URL}/{{chapter_id}}/delete")
async def delete_chapter(
    _gm: GamemasterDep,
    registry: RegistryDep,
    chapter_id: str,
    contents: Annotated[str, Form()] = "keep",
) -> Response:
    """Move the chapter to the trash; with ``contents=all`` what is placed in it goes
    too, else it moves up to the adventure."""
    chapter = registry.adventures.delete_chapter(chapter_id, contents=contents == "all")
    return redirect(_with(adventure_url(chapter.adventure_id), deleted="1"))


@router.post(f"{CHAPTERS_URL}/{{chapter_id}}/move")
async def move_chapter(
    _gm: GamemasterDep,
    registry: RegistryDep,
    chapter_id: str,
    step: Annotated[int, Form()] = 0,
) -> Response:
    """One place up or down in the adventure."""
    adventure_id = registry.adventures.get_chapter(chapter_id).adventure_id
    registry.adventures.move("chapter", chapter_id, step)
    return redirect(adventure_url(adventure_id))


# -- images ----------------------------------------------------------------------------


@router.post(f"{MEDIA_UPLOAD_URL}/{{record_id}}")
async def upload_image(
    _gm: GamemasterDep,
    registry: RegistryDep,
    record_id: str,
    file: Annotated[UploadFile, File()],
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
    as_cover: Annotated[str, Form()] = "",
) -> Response:
    """Store an image beside a world, adventure or chapter, and go back."""
    back = safe_next(next) or "/gm/worlds"
    data = await file.read(MAX_BYTES + 1)
    try:
        relative = registry.adventures.upload(
            record_id, file.filename or "image", data, as_cover=bool(as_cover)
        )
    except (MediaError, AdventureError) as exc:
        return redirect(_with(back, error=exc.key))
    return redirect(_with(back, uploaded=relative))


@router.post(f"{MEDIA_UPLOAD_URL}/{{record_id}}/inline")
async def upload_inline(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    record_id: str,
    file: Annotated[UploadFile, File()],
) -> Response:
    """An image dropped or pasted into an editor: stored beside the record, its path to
    write (``media/map.png``) in ``X-Media-Path`` (201); a refusal's reason, in the
    reader's language, as the body (422)."""
    data = await file.read(MAX_BYTES + 1)
    try:
        relative = registry.adventures.upload(
            record_id, file.filename or "image", data, as_cover=False
        )
    except (MediaError, AdventureError) as exc:
        return Response(page.t.error(exc), status_code=422, media_type="text/plain")
    return Response(status_code=201, headers={"X-Media-Path": relative})


@router.post(f"{MEDIA_UPLOAD_URL}/{{record_id}}/cover")
async def choose_cover(
    _gm: GamemasterDep,
    registry: RegistryDep,
    record_id: str,
    path: Annotated[str, Form()] = "",
    effects: Annotated[list[str] | None, Form()] = None,
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Make one of the record's images its cover, with the effects ticked, or (empty)
    clear it (documentation/ADVENTURES.md §5.5)."""
    back = safe_next(next) or "/gm/worlds"
    try:
        registry.adventures.set_cover(
            record_id, image_effects.join(path, effects or []) if path else None
        )
    except AdventureError as exc:
        return redirect(_with(back, error=exc.key))
    return redirect(back)


__all__ = ["router", "upload_messages"]
