"""The table's own tokens (documentation/TOKENS.md §3).

* ``GET  /tokens/{world}/{kind}/{id}`` — an uploaded token (``-`` for no world)
* ``GET  /tokens/{world}/{kind}/{id}/picture`` — the element's own picture, for the dialog
* ``POST /tokens/{kind}/{id}``          — upload one (``token``, placed in the kind's
  frame at ``x``, ``y``, ``zoom``), or ``do=remove``; then back to ``next``

The gamemaster sets any; a player only their own character's.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.datastructures import UploadFile

from app.dependencies import IdentityDep, RegistryDep
from app.models.gamestate import Location, Npc, Treasure
from app.routes.auth import redirect, safe_next
from app.services.auth import Identity
from app.services.media import MediaError
from app.services.tokens import PICTURE_KINDS, TOKEN_URL, Placement
from app.store.registry import StoreRegistry

router = APIRouter(tags=["tokens"])


def owner(registry: StoreRegistry, kind: str, ident: str) -> tuple[str | None, str | None] | None:
    """The world an element's token lives in and, for a character, its player; None for
    an element that is not there."""
    store = registry.gamestate
    if kind == "character":
        pc = store.characters.find(ident)
        return (pc.world_id, pc.player) if pc is not None else None
    if kind == "encounter":
        encounter = store.encounters.find(ident)
        return (encounter.world_id, None) if encounter is not None else None
    if kind == "creature":
        design = store.creatures.find(ident)
        return (design.world_id, None) if design is not None else None
    record: Npc | Location | Treasure | None = None
    if kind == "npc":
        record = store.npcs.find(ident)
    elif kind == "place":
        record = store.locations.find(ident)
    elif kind == "treasure":
        record = store.treasures.find(ident)
    return (record.scope.world_id, None) if record is not None else None


def may_set(identity: Identity, kind: str, player: str | None) -> bool:
    """The gamemaster sets any token; a player their own character's."""
    if identity.is_gamemaster:
        return True
    return kind == "character" and player is not None and player == identity.user.username


@router.get(f"{TOKEN_URL}/{{world}}/{{kind}}/{{ident}}")
async def token_file(
    _identity: IdentityDep, registry: RegistryDep, world: str, kind: str, ident: str
) -> FileResponse:
    """An uploaded token. The address carries its version, so it may be kept long."""
    found = registry.tokens.file(None if world == "-" else world, kind, ident)
    if found is None:
        raise HTTPException(status_code=404)
    return FileResponse(
        found, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000"}
    )


@router.post(f"{TOKEN_URL}/{{kind}}/{{ident}}")
async def set_token(
    request: Request, identity: IdentityDep, registry: RegistryDep, kind: str, ident: str
) -> Response:
    """Upload an element's token (cropped round), or remove it; back to where it was set."""
    found = owner(registry, kind, ident)
    if found is None:
        raise HTTPException(status_code=404)
    world_id, player = found
    if not may_set(identity, kind, player):
        raise HTTPException(status_code=403)
    form = await request.form()
    back = safe_next(str(form.get("next") or "")) or "/"
    tokens = registry.tokens
    if form.get("do") == "remove":
        tokens.remove(world_id, kind, ident)
        return redirect(back)
    placement = Placement.parse(form.get("x", 0.5), form.get("y", 0.5), form.get("zoom", 1))
    upload = form.get("token")
    if not isinstance(upload, UploadFile) or not upload.filename:
        # No new picture: the element's own picture placed anew, if it has one.
        if kind in PICTURE_KINDS and tokens.place(world_id, kind, ident, placement):
            return redirect(back)
        return redirect(_with(back, "token_error", "none"))
    data = await upload.read()
    try:
        # One picture for both: an element with a picture of its own gets the upload as
        # that picture, and its token is made from it.
        if registry.set_own_picture(kind, ident, upload.filename, data):
            tokens.place(world_id, kind, ident, placement)
        else:
            tokens.set(world_id, kind, ident, upload.filename, data, placement)
    except MediaError:
        return redirect(_with(back, "token_error", "image"))
    return redirect(back)


@router.get(f"{TOKEN_URL}/{{world}}/{{kind}}/{{ident}}/picture")
async def token_picture(
    identity: IdentityDep, registry: RegistryDep, world: str, kind: str, ident: str
) -> FileResponse:
    """The element's own picture, for the dialog that places it in the frame."""
    found = owner(registry, kind, ident)
    picture = registry.tokens.picture(kind, ident) if found is not None else None
    if found is None or picture is None or not may_set(identity, kind, found[1]):
        raise HTTPException(status_code=404)
    return FileResponse(picture, headers={"Cache-Control": "private, no-cache"})


def _with(url: str, key: str, value: str) -> str:
    path, _, anchor = url.partition("#")
    joined = f"{path}{'&' if '?' in path else '?'}{key}={value}"
    return f"{joined}#{anchor}" if anchor else joined


__all__ = ["may_set", "owner", "router"]
