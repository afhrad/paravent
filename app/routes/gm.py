"""The gamemaster's table: the live party overview, and worlds with their books.

* ``GET  /gm``                         — every character, live (``?world=`` narrows it)
* ``GET  /gm/party``                   — just the grid, refetched when a character
  appears or disappears
* ``GET  /gm/party/{id}/card``         — one card, refetched when that character changes
* ``GET  /gm/events``                  — Server-Sent Events: ``"<id> <revision>"`` for
  every character change, from any screen or a hand edit
* ``/gm/worlds…``                      — create, edit and delete worlds (a world's own
  page, with its adventures, is in :mod:`app.routes.adventures`)
* ``POST /gm/characters/{id}/world``   — place a character in a world
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from htpy import Element

from app.dependencies import (
    CharactersDep,
    GamemasterDep,
    PageDep,
    RegistryDep,
    WorldsDep,
)
from app.routes.adventures import upload_messages
from app.routes.auth import html, redirect
from app.routes.rolls import announce_roll, new_roll
from app.services.auth import AuthError
from app.services.characters import CharacterService
from app.services.events import PARTY, revision_stream
from app.services.play import PlayError
from app.services.player_view import player_choice, player_ticks
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from app.ui.adventure_pages import images_panel
from app.ui.combat_pages import party_combats
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.gm_pages import (
    WORLDS_URL,
    party_card,
    party_grid,
    party_page,
    world_delete_page,
    world_form_page,
    worlds_page,
)

router = APIRouter(tags=["gamemaster"])


def _combats(page: Page, registry: StoreRegistry, world_id: str | None) -> Element:
    combats = registry.combats
    views = [combats.view(c) for c in combats.running(world_id=world_id)]
    return party_combats(page, views, world_id=world_id)


def _cards(page: Page, characters: CharacterService, world_id: str | None) -> list[Element]:
    return [
        party_card(
            page,
            m.sheet,
            owner=m.owner,
            world=None if world_id else m.world,
            world_filter=world_id or "",
        )
        for m in characters.party(world_id)
    ]


# -- the party overview ------------------------------------------------------------------


@router.get("/gm", response_class=HTMLResponse)
async def party(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
) -> HTMLResponse:
    """Every character at the table — or in one world — updating live."""
    chosen = worlds.find(world)
    world_id = chosen.id if chosen else None
    return html(
        party_page(
            page,
            _cards(page, characters, world_id),
            worlds=worlds.list(),
            world=chosen,
            combats=_combats(page, registry, world_id),
        )
    )


@router.get("/gm/party/combats", response_class=HTMLResponse)
async def party_combats_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
) -> HTMLResponse:
    """Just the running fights."""
    chosen = worlds.find(world)
    return html(_combats(page, registry, chosen.id if chosen else None))


@router.get("/gm/party", response_class=HTMLResponse)
async def party_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    world: str = "",
) -> HTMLResponse:
    """Just the grid."""
    chosen = worlds.find(world)
    world_id = chosen.id if chosen else None
    return html(party_grid(page, _cards(page, characters, world_id), world_id=world_id))


@router.get("/gm/party/{character_id}/card", response_class=HTMLResponse)
async def party_card_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    character_id: str,
    world: str = "",
) -> HTMLResponse:
    """One character's card."""
    member = characters.party_member(character_id)
    return html(
        party_card(
            page,
            member.sheet,
            owner=member.owner,
            world=None if world else member.world,
            world_filter=world,
        )
    )


@router.post("/gm/party/{character_id}/act/{action}", response_class=HTMLResponse)
async def party_action(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    character_id: str,
    action: str,
) -> Response:
    """Apply a sheet action from the overview and return that character's new card.

    The same rules as on the sheet — temporary HP first, concentration saves, death
    saves — and the log names the gamemaster as the one who acted. Every other screen
    showing the character updates through its live stream.
    """
    params = {k: str(v) for k, v in (await request.form()).items()}
    world = params.pop("world", "")
    started = datetime.now(UTC)  # what the action logs after this, it rolled
    flash: str | None = None
    try:
        characters.act(gm, character_id, action, params)
    except PlayError as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world})}" if world else "/gm")
    member = characters.party_member(character_id)
    response = html(
        party_card(
            page,
            member.sheet,
            owner=member.owner,
            world=None if world else member.world,
            world_filter=world,
            flash=flash,
        )
    )
    rolled = new_roll(characters.rolled_since(member.sheet.character, started))
    return announce_roll(response, rolled.outcome) if rolled else response


@router.get("/gm/events")
async def party_events(
    request: Request, _gm: GamemasterDep, registry: RegistryDep
) -> StreamingResponse:
    """Every character change, as ``"<id> <revision>"`` (an empty revision: deleted).

    A combat's change arrives as ``"*combats <id> <revision>"``.
    """
    return StreamingResponse(
        revision_stream(registry.events, PARTY, None, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- worlds ------------------------------------------------------------------------------


@router.get(WORLDS_URL, response_class=HTMLResponse)
async def list_worlds(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    created: str = "",
    updated: str = "",
    deleted: int | None = None,
) -> HTMLResponse:
    """Every world."""
    t = page.t
    notice_text = None
    for world_id, key in ((created, "worlds.created"), (updated, "worlds.updated")):
        found = worlds.find(world_id) if world_id else None
        if found is not None:
            notice_text = t(key, name=found.name)
    if deleted is not None:
        notice_text = t("worlds.deleted")
    all_worlds = worlds.list()
    counts = {w.id: worlds.character_count(w.id) for w in all_worlds}
    return html(worlds_page(page, all_worlds, counts, notice_text=notice_text))


def _languages(registry: StoreRegistry) -> list[tuple[str, str]]:
    """The languages a world may be written in: those Paravent speaks."""
    return [(lang.code, lang.name) for lang in registry.ui.languages]


def _language(registry: StoreRegistry, code: str) -> str:
    """The language a form chose, English when it names none Paravent speaks."""
    return code if registry.ui.supports(code) else "en"


def _groups(registry: StoreRegistry) -> dict[str, str]:
    """Every loaded book's picker group, by code."""
    return {b.code.upper(): b.group for b in registry.books.books}


def _world_input(
    registry: StoreRegistry,
    name: str,
    description: str,
    sources: list[str] | None,
    players: list[str] | None,
    rerolls: str,
    language: str,
    encumbrance: str = "",
) -> WorldInput:
    """What the world form posted; the players' ticks stored only where they differ from
    the defaults."""
    declared = tuple(sources or ())
    chosen = player_choice(players or (), declared, _groups(registry))
    return WorldInput(
        name,
        description,
        declared,
        allow_rerolls=bool(rerolls),
        variant_encumbrance=bool(encumbrance),
        language=_language(registry, language),
        player_sources=tuple(chosen) if chosen is not None else None,
    )


@router.get(f"{WORLDS_URL}/new", response_class=HTMLResponse)
async def new_world(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """The empty world form."""
    return html(
        world_form_page(
            page,
            registry.books,
            world=None,
            name="",
            description="",
            sources=(),
            players=player_ticks(None, _groups(registry)),
            language=page.lang,
            languages=_languages(registry),
        )
    )


@router.post(WORLDS_URL, response_class=HTMLResponse)
async def create_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    sources: Annotated[list[str] | None, Form()] = None,
    players: Annotated[list[str] | None, Form()] = None,
    rerolls: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    encumbrance: Annotated[str, Form()] = "",
) -> Response:
    """Create a world, or show the form again with the reason it was refused."""
    data = _world_input(
        registry, name, description, sources, players, rerolls, language, encumbrance
    )
    try:
        world = worlds.create(data)
    except AuthError as exc:
        view = world_form_page(
            page,
            registry.books,
            world=None,
            name=name,
            description=description,
            sources=data.sources,
            players=frozenset(p.upper() for p in players or ()),
            rerolls=data.allow_rerolls,
            encumbrance=data.variant_encumbrance,
            language=data.language,
            languages=_languages(registry),
            error=page.t.error(exc),
        )
        return html(view, status_code=422)
    return redirect(f"{WORLDS_URL}/{world.id}?created=1")


@router.get(f"{WORLDS_URL}/{{world_id}}/edit", response_class=HTMLResponse)
async def edit_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    world_id: str,
    uploaded: str = "",
    error: str = "",
) -> HTMLResponse:
    """The world form, filled in, with the world's images below it."""
    world = worlds.get(world_id)
    notice_text, message = upload_messages(page, uploaded, error)
    images = images_panel(
        page,
        world.id,
        world.cover,
        registry.adventures.images(world.id),
        f"{WORLDS_URL}/{world.id}/edit",
    )
    return html(
        world_form_page(
            page,
            registry.books,
            world=world,
            name=world.name,
            description=world.description,
            sources=world.source_set.sources,
            players=player_ticks(world, _groups(registry)),
            rerolls=world.allow_rerolls,
            encumbrance=world.variant_encumbrance,
            language=world.language,
            languages=_languages(registry),
            extra=[notice(notice_text), alert(message), images],
        )
    )


@router.post(f"{WORLDS_URL}/{{world_id}}", response_class=HTMLResponse)
async def update_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    sources: Annotated[list[str] | None, Form()] = None,
    players: Annotated[list[str] | None, Form()] = None,
    rerolls: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    encumbrance: Annotated[str, Form()] = "",
) -> Response:
    """Save a world's name, description and books."""
    world = worlds.get(world_id)
    data = _world_input(
        registry, name, description, sources, players, rerolls, language, encumbrance
    )
    try:
        worlds.update(world_id, data)
    except AuthError as exc:
        view = world_form_page(
            page,
            registry.books,
            world=world,
            name=name,
            description=description,
            sources=data.sources,
            players=frozenset(p.upper() for p in players or ()),
            rerolls=data.allow_rerolls,
            encumbrance=data.variant_encumbrance,
            language=data.language,
            languages=_languages(registry),
            error=page.t.error(exc),
        )
        return html(view, status_code=422)
    return redirect(f"{WORLDS_URL}/{world_id}?updated=1")


@router.get(f"{WORLDS_URL}/{{world_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_world(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, world_id: str
) -> HTMLResponse:
    """Ask first, saying what happens to the world's characters."""
    world = worlds.get(world_id)
    return html(world_delete_page(page, world, worlds.character_count(world_id)))


@router.post(f"{WORLDS_URL}/{{world_id}}/delete", response_class=HTMLResponse)
async def delete_world(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, world_id: str
) -> Response:
    """Delete a world; its characters stay, without one."""
    world = worlds.get(world_id)
    try:
        moved = worlds.delete(world_id)
    except AuthError as exc:
        view = world_delete_page(
            page, world, worlds.character_count(world_id), error=page.t.error(exc)
        )
        return html(view, status_code=409)
    return redirect(f"{WORLDS_URL}?deleted={moved}")


@router.post("/gm/characters/{character_id}/world")
async def place_character(
    _gm: GamemasterDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    character_id: str,
    world: Annotated[str, Form()] = "",
) -> Response:
    """Put a character in a world, or in none."""
    characters.get_any(character_id)
    worlds.place(character_id, world or None)
    return redirect("/gm/characters")


__all__ = ["router"]
