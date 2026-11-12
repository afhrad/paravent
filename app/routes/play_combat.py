"""The fight as players see it (documentation/COMBAT.md §5).

* ``GET /play/combat/{id}``                 — the round, the order, whose turn
* ``GET /play/combat/{id}/board``           — the board fragment (live updates fetch it)
* ``GET /play/combat/{id}/events``          — Server-Sent Events: the combat's revision
* ``POST /play/combat/{id}/target``         — a player's character aims at a combatant
* ``GET /play/combat/{id}/c/{cid}/details`` — what players know of a combatant
* ``GET /play/combat/{id}/c/{cid}/picture`` — the table's own picture of its kind
* ``GET /play/img/{path}``                  — a picture from the corpus's image folder
* ``GET /characters/{id}/combat``           — the strip above the sheet
* ``GET /characters/{id}/combat/events``    — Server-Sent Events: the character's fights

A player sees a combat one of their characters is in, and nothing else — any other id
answers 404, the same as an id that names nothing. The gamemaster sees every one, as the
players do. Every page is built from ``CombatService.player_view``, never from the
tracker's view.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from htpy import Element

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.models.protocol import EntryKind
from app.routes.auth import html, redirect
from app.routes.rolls import announce_roll
from app.services.auth import Identity
from app.services.combat import CombatError, CombatNotFound, PlayerView
from app.services.creature_sight import image_file
from app.services.events import combat_key, revision_stream
from app.services.media import media_type
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.knowledge_text import action_names
from app.ui.play_combat_pages import (
    combat_strip,
    member_details,
    play_image_url,
    play_picture_url,
    player_board,
    player_combat_page,
)
from app.ui.tags import localized_context

router = APIRouter(tags=["play"])


def _seen(
    identity: Identity, registry: StoreRegistry, combat_id: str
) -> tuple[PlayerView, frozenset[str]]:
    """The player view and which of its characters are the viewer's, or 404."""
    combat = registry.combats.get(combat_id)
    present = {c.character_id for c in combat.combatants if c.character_id is not None}
    if identity.is_gamemaster:
        return registry.combats.player_view(combat), frozenset()
    mine = frozenset(pc.id for pc in registry.characters.for_player(identity)) & present
    if not mine:
        raise CombatNotFound(combat_id)
    return registry.combats.player_view(combat), mine


@router.get("/play/combat/{combat_id}", response_class=HTMLResponse)
async def player_combat(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> HTMLResponse:
    """The fight on a player's phone."""
    view, mine = _seen(identity, registry, combat_id)
    return html(player_combat_page(page, view, mine))


@router.get("/play/combat/{combat_id}/board", response_class=HTMLResponse)
async def player_combat_board(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> HTMLResponse:
    """Just the board."""
    view, mine = _seen(identity, registry, combat_id)
    return html(player_board(page, view, mine))


@router.get("/play/combat/{combat_id}/events")
async def player_combat_events(
    request: Request, identity: IdentityDep, registry: RegistryDep, combat_id: str
) -> StreamingResponse:
    """The combat's revision; the board is refetched when it moves."""
    view, _ = _seen(identity, registry, combat_id)
    return StreamingResponse(
        revision_stream(registry.events, combat_id, view.revision, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/play/combat/{combat_id}/c/{combatant_id}/details", response_class=HTMLResponse)
async def player_details(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, combat_id: str, combatant_id: str
) -> HTMLResponse:
    """A combatant's Details: what anyone sees of it. A hidden one is not there."""
    _seen(identity, registry, combat_id)
    combats = registry.combats
    combat = combats.get(combat_id)
    details = combats.player_details(combat, combatant_id, page.lang)
    member = combat.combatant(combatant_id)
    ref = member.ref if member is not None else None
    version = registry.creature_pictures.version(combat.world_id, ref) if ref else None
    own = f"{play_picture_url(combat_id, combatant_id)}?v={version}" if version else None
    row = combats.view(combat).row(combatant_id)
    monster = row.monster if row is not None else None
    data = monster.entry.model_dump(by_alias=True, exclude_none=True) if monster else None
    names = action_names(page, data)
    references = registry.references
    world_id = references.known_world(combat.world_id)
    # The books' description draws its links (only to what the player may read) and,
    # for a reader in metres, its metric hints.
    ctx = localized_context(
        page.t,
        resolver=references.resolver(),
        source_set=references.source_set(world_id),
        world_id=world_id,
        readable=page.readable,
        units=page.u,
    )
    return html(member_details(page, details, play_image_url, own, names, ctx))


@router.get("/play/combat/{combat_id}/c/{combatant_id}/picture")
async def player_picture(
    identity: IdentityDep, registry: RegistryDep, combat_id: str, combatant_id: str
) -> FileResponse:
    """The table's own picture of a combatant's kind; a hidden one is not there."""
    _seen(identity, registry, combat_id)
    combat = registry.combats.get(combat_id)
    member = combat.combatant(combatant_id)
    if member is None or member.ref is None or (member.hidden and not identity.is_gamemaster):
        raise HTTPException(status_code=404)
    found = registry.creature_pictures.find(combat.world_id, member.ref)
    if found is None:
        raise HTTPException(status_code=404)
    # The address carries the picture's version, so it may be kept long.
    return FileResponse(
        found,
        media_type=media_type(found),
        headers={"Cache-Control": "private, max-age=31536000"},
    )


@router.get("/play/img/{path:path}")
async def player_image(identity: IdentityDep, registry: RegistryDep, path: str) -> FileResponse:
    """A picture from the 5eTools image folder, read in place."""
    root = registry.settings.images_root
    found = image_file(root, path) if root is not None else None
    if found is None:
        raise HTTPException(status_code=404)
    return FileResponse(found, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/play/combat/{combat_id}/target")
async def player_target(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    character_id: Annotated[str, Form()],
    target: Annotated[str, Form()] = "",
) -> Response:
    """A player picks their character's target, or clears it (an empty ``target``)."""
    _view, mine = _seen(identity, registry, combat_id)
    if not identity.is_gamemaster and character_id not in mine:
        raise CombatNotFound(combat_id)
    flash = None
    try:
        registry.combats.choose_target(combat_id, character_id, target or None)
    except CombatError as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/play/combat/{combat_id}")
    view, mine = _seen(identity, registry, combat_id)
    return html(player_board(page, view, mine or frozenset({character_id}), flash=flash))


@router.post("/play/combat/{combat_id}/save")
async def player_save(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    at: Annotated[str, Form()],
    character_id: Annotated[str, Form()],
    back: Annotated[str, Form()] = "board",
) -> Response:
    """A player rolls their character's save against a waiting save effect."""
    _view, mine = _seen(identity, registry, combat_id)
    if not identity.is_gamemaster and character_id not in mine:
        raise CombatNotFound(combat_id)
    who = identity.user.display_name or identity.user.username
    outcome = ""
    flash = None
    try:
        registry.combats.player_save(combat_id, at, character_id, who)
        outcome = _natural(registry, character_id)
    except CombatError as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(
            f"/characters/{character_id}" if back == "strip" else f"/play/combat/{combat_id}"
        )
    if back == "strip":
        response = html(strip(page, registry, character_id, flash=flash))
    else:
        view, mine = _seen(identity, registry, combat_id)
        response = html(player_board(page, view, mine or frozenset({character_id}), flash=flash))
    return response if flash else announce_roll(response, outcome)


@router.post("/play/combat/{combat_id}/repeat")
async def player_repeat(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    character_id: Annotated[str, Form()],
    condition: Annotated[str, Form()],
    back: Annotated[str, Form()] = "board",
) -> Response:
    """A player rolls their character's repeated save to end a condition."""
    _view, mine = _seen(identity, registry, combat_id)
    if not identity.is_gamemaster and character_id not in mine:
        raise CombatNotFound(combat_id)
    who = identity.user.display_name or identity.user.username
    flash = None
    outcome = ""
    try:
        registry.combats.player_repeat(combat_id, character_id, condition, who)
        outcome = _natural(registry, character_id)
    except CombatError as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(
            f"/characters/{character_id}" if back == "strip" else f"/play/combat/{combat_id}"
        )
    if back == "strip":
        response = html(strip(page, registry, character_id, flash=flash))
    else:
        view, mine = _seen(identity, registry, combat_id)
        response = html(player_board(page, view, mine or frozenset({character_id}), flash=flash))
    return response if flash else announce_roll(response, outcome)


def _natural(registry: StoreRegistry, character_id: str) -> str:
    """A natural 20 or 1 on the save the player just rolled, for its sound."""
    rolled = registry.characters.newest_roll(
        registry.characters.get_any(character_id), (EntryKind.SAVE,)
    )
    outcome = rolled.outcome if rolled is not None else ""
    return outcome if outcome in ("crit", "fumble") else ""


def strip(
    page: Page, registry: StoreRegistry, character_id: str, flash: str | None = None
) -> Element:
    """The strip of running fights for one character (access checked by the caller)."""
    combats = registry.combats
    views = [combats.player_view(c) for c in combats.joined_by([character_id])]
    return combat_strip(page, character_id, views, flash=flash)


@router.get("/characters/{character_id}/combat", response_class=HTMLResponse)
async def character_combat_strip(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, character_id: str
) -> HTMLResponse:
    """The fights above the sheet."""
    registry.characters.get_for(identity, character_id)
    return html(strip(page, registry, character_id))


@router.get("/characters/{character_id}/combat/events")
async def character_combat_events(
    request: Request, identity: IdentityDep, registry: RegistryDep, character_id: str
) -> StreamingResponse:
    """A message whenever one of the character's fights changes."""
    registry.characters.get_for(identity, character_id)
    return StreamingResponse(
        revision_stream(registry.events, combat_key(character_id), None, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router", "strip"]
