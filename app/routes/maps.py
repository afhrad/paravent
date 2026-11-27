"""The gamemaster's maps (documentation/MAPS.md).

* ``GET/POST /gm/worlds/{world_id}/maps`` — a world's maps; a new one, of the world or of
  one of its adventures (``adventure``), from an uploaded picture
* ``GET /gm/maps/{id}`` — the map in its viewer; a large one's tiles cut first
* ``GET /gm/maps/{id}/status`` — how far the cutting got (the page asks every second)
* ``GET /gm/maps/{id}/tiles/{z}/{x}/{y}.webp`` and ``/thumb`` — tiles, and a small picture
* ``POST /gm/maps/{id}/scale`` and ``/scale/clear`` — the scale given or taken away
* ``POST /gm/maps/{id}/view`` — where the user left it
* ``GET/POST /gm/maps/{id}/edit``, ``POST /gm/maps/{id}/image`` — name, notes, picture
* ``GET/POST /gm/maps/{id}/delete`` — into the trash, after asking
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Adventure, Scope, WorldMap
from app.routes import adventures as adventure_routes
from app.routes.auth import back_with_undo, html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services.i18n import Translatable
from app.services.map_tiles import needs_tiles
from app.services.maps import MapError
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.library_links import WORLDS_URL, adventure_url
from app.ui.map_pages import (
    MAPS_URL,
    map_delete_page,
    map_form_page,
    map_page,
    map_url,
    maps_page,
    tiling_panel,
    world_maps_url,
)

router = APIRouter(tags=["maps"])


def _map(registry: StoreRegistry, map_id: str) -> WorldMap:
    try:
        return registry.world_maps.get(map_id)
    except MapError as exc:
        raise HTTPException(status_code=404) from exc


def _adventure(registry: StoreRegistry, found: WorldMap) -> Adventure | None:
    ident = found.scope.adventure_id
    return registry.gamestate.adventures.find(ident) if ident else None


def _list(
    page: Page,
    registry: StoreRegistry,
    world_id: str,
    *,
    error: str | None = None,
    notice_text: str | None = None,
    new: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    world = registry.adventures.world(world_id)
    return html(
        maps_page(
            page,
            world,
            registry.world_maps.of_world(world_id),
            error=error,
            notice_text=notice_text,
            new=new,
        ),
        status_code=status_code,
    )


@router.get(f"{WORLDS_URL}/{{world_id}}/maps", response_class=HTMLResponse)
async def list_maps(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world_id: str, deleted: str = ""
) -> HTMLResponse:
    """A world's own maps."""
    notice_text = page.t("atlas.deleted") if deleted else None
    return _list(page, registry, world_id, notice_text=notice_text)


@router.post(f"{WORLDS_URL}/{{world_id}}/maps", response_class=HTMLResponse)
async def create_map(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    picture: Annotated[UploadFile, File()],
    name: Annotated[str, Form()] = "",
    adventure: Annotated[str, Form()] = "",
) -> Response:
    """A new map from the picture sent, then its page; refused, the dialog again with
    why (on the adventure's page for an adventure's map)."""
    try:
        found = await asyncio.to_thread(
            registry.world_maps.create,
            world_id,
            name,
            picture.filename or "",
            picture.file,
            adventure or None,
        )
    except Translatable as exc:
        if adventure and registry.gamestate.adventures.find(adventure) is not None:
            return adventure_routes._adventure(
                page, registry, adventure, map_error=page.t.error(exc), status_code=422
            )
        return _list(page, registry, world_id, error=page.t.error(exc), new=True, status_code=422)
    return redirect(map_url(found.id))


def _number(text: str) -> float | None:
    """A number as typed, with a decimal point or comma; None when it is none."""
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def _start_tiles(registry: StoreRegistry, found: WorldMap) -> bool:
    """Whether the map can be shown now; a large one's missing tiles start being cut."""
    if not needs_tiles(found.width, found.height):
        return True
    tiles = registry.world_maps.tiles
    if tiles.ready(found.id, found.digest):
        return True
    source = registry.world_maps.source(found)
    if source is not None:
        tiles.ensure(found.id, found.digest, source, found.width, found.height)
    return False


@router.get(f"{MAPS_URL}/{{map_id}}", response_class=HTMLResponse)
async def show_map(
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    map_id: str,
    saved: str = "",
    error: str = "",
) -> HTMLResponse:
    """The map in its viewer, where the user left it."""
    found = _map(registry, map_id)
    world = registry.adventures.world(found.scope.world_id)
    ready = _start_tiles(registry, found)
    notes = (
        paravent_markdown(page, registry, Scope(world_id=world.id), found.notes).html
        if found.notes.strip()
        else None
    )
    message = page.t(error) if error.startswith("atlas.error.") else None
    return html(
        map_page(
            page,
            found,
            world,
            _adventure(registry, found),
            ready=ready,
            job=registry.world_maps.tiles.job(found.id),
            view=registry.world_maps.view(gm.user.id, found.id),
            notes=notes,
            notice_text=page.t("atlas.saved") if saved else None,
            error=message,
        )
    )


@router.get(f"{MAPS_URL}/{{map_id}}/status", response_class=HTMLResponse)
async def map_status(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, map_id: str
) -> Response:
    """How far the cutting got; once the tiles are there, the page reloads."""
    found = _map(registry, map_id)
    if _start_tiles(registry, found):
        return Response(headers={"HX-Refresh": "true"})
    return html(tiling_panel(page, found, registry.world_maps.tiles.job(found.id)))


_TILE_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable"}


@router.get(f"{MAPS_URL}/{{map_id}}/tiles/{{z}}/{{x}}/{{y}}.webp")
async def map_tile(
    _gm: GamemasterDep, registry: RegistryDep, map_id: str, z: int, x: int, y: int
) -> Response:
    """One tile. Its address changes with the picture (``?d=``), so it is cached for
    good."""
    found = _map(registry, map_id)
    path = registry.world_maps.tiles.tile(found.id, found.digest, z, x, y)
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/webp", headers=_TILE_HEADERS)


@router.get(f"{MAPS_URL}/{{map_id}}/thumb")
async def map_thumb(_gm: GamemasterDep, registry: RegistryDep, map_id: str) -> Response:
    """A small picture of the map for lists: a large map's thumbnail, made with its
    tiles, a small one's picture itself."""
    found = _map(registry, map_id)
    if needs_tiles(found.width, found.height):
        path = registry.world_maps.tiles.thumb(found.id, found.digest)
        if path is None:
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="image/webp")
    source = registry.world_maps.source(found)
    if source is None:
        raise HTTPException(status_code=404)
    return FileResponse(source)


@router.post(f"{MAPS_URL}/{{map_id}}/scale")
async def set_scale(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    way: Annotated[str, Form()] = "line",
    amount: Annotated[str, Form()] = "",
    unit: Annotated[str, Form()] = "mi",
    x1: Annotated[str, Form()] = "",
    y1: Annotated[str, Form()] = "",
    x2: Annotated[str, Form()] = "",
    y2: Annotated[str, Form()] = "",
) -> Response:
    """Give the map its scale; refused, back to the map saying why."""
    found = _map(registry, map_id)
    try:
        value = _number(amount)
        if value is None:
            raise MapError("atlas.error.amount")
        line: list[float] = []
        if way == "line":
            parsed = [_number(v) for v in (x1, y1, x2, y2)]
            line = [v for v in parsed if v is not None]
        registry.world_maps.set_scale(found.id, way=way, amount=value, unit=unit, line=line)
    except Translatable as exc:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': exc.key})}")
    return redirect(f"{map_url(found.id)}?saved=1")


@router.post(f"{MAPS_URL}/{{map_id}}/scale/clear")
async def clear_scale(_gm: GamemasterDep, registry: RegistryDep, map_id: str) -> Response:
    """Take the map's scale away."""
    found = _map(registry, map_id)
    registry.world_maps.clear_scale(found.id)
    return redirect(map_url(found.id))


@router.post(f"{MAPS_URL}/{{map_id}}/view", status_code=204)
async def keep_view(
    gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    x: Annotated[float, Form()],
    y: Annotated[float, Form()],
    zoom: Annotated[float, Form()],
) -> Response:
    """Remember where the user left the map."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.keep_view(gm.user.id, found.id, x, y, zoom)
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


def _form(
    page: Page,
    registry: StoreRegistry,
    found: WorldMap,
    *,
    name: str | None = None,
    notes: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return html(
        map_form_page(
            page,
            found,
            registry.adventures.world(found.scope.world_id),
            _adventure(registry, found),
            name=found.name if name is None else name,
            notes=found.notes if notes is None else notes,
            error=error,
        ),
        status_code=status_code,
    )


@router.get(f"{MAPS_URL}/{{map_id}}/edit", response_class=HTMLResponse)
async def edit_map(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, map_id: str
) -> HTMLResponse:
    """The map's form."""
    return _form(page, registry, _map(registry, map_id))


@router.post(f"{MAPS_URL}/{{map_id}}/edit", response_class=HTMLResponse)
async def save_map(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    map_id: str,
    name: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    """Save the map's name and notes."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.update(found.id, name=name, notes=notes)
    except Translatable as exc:
        return _form(
            page, registry, found, name=name, notes=notes, error=page.t.error(exc), status_code=422
        )
    return redirect(f"{map_url(found.id)}?saved=1")


@router.post(f"{MAPS_URL}/{{map_id}}/image", response_class=HTMLResponse)
async def replace_image(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    map_id: str,
    picture: Annotated[UploadFile, File()],
) -> Response:
    """Another picture under the map."""
    found = _map(registry, map_id)
    try:
        await asyncio.to_thread(
            registry.world_maps.replace_image, found.id, picture.filename or "", picture.file
        )
    except Translatable as exc:
        return _form(page, registry, found, error=page.t.error(exc), status_code=422)
    return redirect(f"{map_url(found.id)}?saved=1")


@router.get(f"{MAPS_URL}/{{map_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_map(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, map_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(map_delete_page(page, _map(registry, map_id)))


@router.post(f"{MAPS_URL}/{{map_id}}/delete")
async def delete_map(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Into the trash; back where it was asked from, with Undo."""
    found = registry.world_maps.delete(_map(registry, map_id).id)
    undo = registry.trash.entry_of("map", found.id) or ""
    if found.scope.adventure_id:
        fallback = f"{adventure_url(found.scope.adventure_id)}#maps"
    else:
        fallback = world_maps_url(found.scope.world_id)
    return redirect(back_with_undo(next, fallback, undo, gone=map_url(found.id)))


__all__ = ["router"]
