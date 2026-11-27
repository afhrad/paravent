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
import re
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Adventure, Scope, WorldMap
from app.routes import adventures as adventure_routes
from app.routes.auth import back_with_undo, html, redirect, safe_next
from app.routes.paravent_markdown import paravent_markdown
from app.services.i18n import Translatable
from app.services.map_tiles import needs_tiles
from app.services.maps import MapError
from app.services.tokens import Token
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.library_links import (
    WORLDS_URL,
    adventure_url,
    chapter_url,
    location_url,
    npc_url,
    treasure_url,
)
from app.ui.map_pages import (
    MAPS_URL,
    Candidate,
    FrameView,
    MapContext,
    PinView,
    map_delete_page,
    map_form_page,
    map_page,
    map_url,
    maps_page,
    tiling_panel,
    world_maps_url,
)
from app.ui.tokens import token

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


_TAG = re.compile(r"\{@\w+ ([^}|]+)[^}]*\}")
_MARKS = re.compile(r"[#*_>`\[\]]")


def _excerpt(text: str) -> str:
    """The first line of a Markdown text, as plain words, at most 140 characters."""
    for line in text.splitlines():
        plain = " ".join(_MARKS.sub("", _TAG.sub(r"\1", line)).split())
        if plain:
            return plain if len(plain) <= 140 else plain[:139].rstrip() + "…"
    return ""


def _pin_target(registry: StoreRegistry, kind: str, target: str) -> tuple[Any, str | None]:
    """The record a pin points at and its page."""
    store = registry.gamestate
    if kind == "place":
        return store.locations.find(target), location_url(target)
    if kind == "npc":
        return store.npcs.find(target), npc_url(target)
    if kind == "treasure":
        return store.treasures.find(target), treasure_url(target)
    if kind == "encounter":
        return store.encounters.find(target), encounter_url(target)
    if kind == "adventure":
        return store.adventures.find(target), adventure_url(target)
    if kind == "chapter":
        return store.chapters.find(target), chapter_url(target)
    if kind == "map":
        return store.maps.find(target), map_url(target)
    return None, None


def _pins(page: Page, registry: StoreRegistry, found: WorldMap) -> list[PinView]:
    """The map's pins as the viewer draws them; one whose target is gone, or no longer
    of this world, grey with the name it had."""
    world_id = found.scope.world_id
    out = []
    for pin in found.pins:
        record, url = _pin_target(registry, pin.kind, pin.target)
        gone = pin.kind != "note" and not registry.world_maps.shows(found, pin.kind, pin.target)
        if gone:
            record, url = None, None
        name = (
            str(getattr(record, "name", None) or getattr(record, "title", "") or pin.name)
            if record is not None
            else pin.name
        )
        notes = str(getattr(record, "notes", "") or getattr(record, "summary", "") or "")
        if pin.kind in ("npc", "place", "encounter", "treasure") and not gone:
            tok = page.token_of(pin.kind, world_id, pin.target)
        else:
            tok = Token(pin.kind)
        out.append(
            PinView(
                id=pin.id,
                x=pin.x,
                y=pin.y,
                kind=pin.kind,
                name=name,
                label=pin.label,
                url=url if pin.kind != "map" else None,
                map_url=url if pin.kind == "map" else None,
                token=str(token(tok, "sm")),
                excerpt=_excerpt(pin.note if pin.kind == "note" else notes),
                gone=gone,
                note=pin.note,
            )
        )
    return out


def _candidates(page: Page, registry: StoreRegistry, found: WorldMap) -> list[Candidate]:
    """What may be pinned on the map, for the pin dialog."""
    world_id = found.scope.world_id
    service = registry.world_maps
    out: list[Candidate] = []

    def add(kind: str, ident: str, name: str, where: str = "") -> None:
        if service.shows(found, kind, ident):
            out.append(Candidate(kind, ident, name, where))

    for place in registry.locations.list(world_id):
        add("place", place.id, place.name)
    for npc in registry.npcs.list(world_id):
        add("npc", npc.id, npc.name)
    for encounter in registry.encounters.list(world_id):
        add("encounter", encounter.id, encounter.name)
    for treasure in registry.treasure.list(world_id):
        add("treasure", treasure.id, treasure.name)
    for adventure in registry.adventures.adventures(world_id):
        add("adventure", adventure.id, adventure.name)
        for chapter in registry.adventures.chapters(adventure.id):
            add("chapter", chapter.id, chapter.title, adventure.name)
    for other in registry.gamestate.maps.list(lambda m: m.scope.world_id == world_id):
        add("map", other.id, other.name)
    return out


def _context(page: Page, registry: StoreRegistry, found: WorldMap, focus: str = "") -> MapContext:
    service = registry.world_maps
    frames = [
        FrameView(
            child.id,
            child.name,
            map_url(child.id),
            child.frame.x,
            child.frame.y,
            child.frame.width,
            child.frame.height,
        )
        for child in service.framed_on(found.id)
        if child.frame is not None and child.scope.world_id == found.scope.world_id
    ]
    return MapContext(
        pins=_pins(page, registry, found),
        frames=frames,
        path=service.path(found),
        candidates=_candidates(page, registry, found),
        framable=service.frame_candidates(found),
        disagree=service.scales_disagree(found),
        focus=focus if any(p.id == focus for p in found.pins) else "",
    )


@router.get(f"{MAPS_URL}/{{map_id}}", response_class=HTMLResponse)
async def show_map(
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    map_id: str,
    saved: str = "",
    error: str = "",
    pin: str = "",
) -> HTMLResponse:
    """The map in its viewer, where the user left it, or at ``pin``."""
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
            around=_context(page, registry, found, pin),
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


# -- pins (documentation/MAPS.md §6) -------------------------------------------------


@router.post(f"{MAPS_URL}/{{map_id}}/pins")
async def add_pin(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    x: Annotated[str, Form()] = "",
    y: Annotated[str, Form()] = "",
    choice: Annotated[str, Form()] = "",
    label: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    place_name: Annotated[str, Form()] = "",
) -> Response:
    """A pin where the map was tapped: to something of the world (``kind:id``), to a new
    place made here (``new``), or a note; then the map, at it."""
    found = _map(registry, map_id)
    px, py = _number(x), _number(y)
    try:
        if px is None or py is None:
            raise MapError("atlas.error.view")
        if choice == "new":
            here = found.scope
            where = f"a:{here.adventure_id}" if here.adventure_id else f"w:{here.world_id}"
            place = registry.locations.create(place_name, where, here.world_id)
            kind, target = "place", place.id
        elif choice == "note":
            kind, target = "note", ""
        else:
            kind, _, target = choice.partition(":")
        made = registry.world_maps.add_pin(
            found.id, x=px, y=py, kind=kind, target=target, label=label, note=note
        )
    except Translatable as exc:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': exc.key})}")
    return redirect(f"{map_url(found.id)}?{urlencode({'pin': made.id})}")


@router.post(f"{MAPS_URL}/{{map_id}}/pins/{{pin_id}}/move", status_code=204)
async def move_pin(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    pin_id: str,
    x: Annotated[float, Form()],
    y: Annotated[float, Form()],
) -> Response:
    """A pin dragged to another spot."""
    try:
        registry.world_maps.move_pin(_map(registry, map_id).id, pin_id, x, y)
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/pins/{{pin_id}}", status_code=204)
async def edit_pin(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    pin_id: str,
    label: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> Response:
    """A pin's own name, and a note's text."""
    try:
        registry.world_maps.edit_pin(_map(registry, map_id).id, pin_id, label=label, note=note)
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/pins/{{pin_id}}/delete", status_code=204)
async def remove_pin(
    _gm: GamemasterDep, registry: RegistryDep, map_id: str, pin_id: str
) -> Response:
    """A pin taken off the map."""
    try:
        registry.world_maps.remove_pin(_map(registry, map_id).id, pin_id)
    except MapError as exc:
        raise HTTPException(status_code=404) from exc
    return Response(status_code=204)


# -- maps within maps -----------------------------------------------------------------


@router.post(f"{MAPS_URL}/{{map_id}}/frames")
async def add_frame(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    child: Annotated[str, Form()] = "",
    x1: Annotated[str, Form()] = "",
    y1: Annotated[str, Form()] = "",
    x2: Annotated[str, Form()] = "",
    y2: Annotated[str, Form()] = "",
) -> Response:
    """Lay ``child`` on this map, in the rectangle drawn."""
    found = _map(registry, map_id)
    corners = [_number(v) for v in (x1, y1, x2, y2)]
    try:
        if any(v is None for v in corners):
            raise MapError("atlas.error.frame_corners")
        registry.world_maps.set_frame(child, found.id, [v for v in corners if v is not None])
    except Translatable as exc:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': exc.key})}")
    return redirect(f"{map_url(found.id)}?saved=1")


@router.post(f"{MAPS_URL}/{{map_id}}/frame/clear")
async def clear_frame(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Take a map off the map it lies on; back to ``next`` (the larger map)."""
    found = _map(registry, map_id)
    registry.world_maps.clear_frame(found.id)
    return redirect(safe_next(next) or map_url(found.id))


__all__ = ["router"]
