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
import json
import re
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Adventure, MapRoute, Scope, WorldMap
from app.routes import adventures as adventure_routes
from app.routes.auth import back_with_undo, html, redirect, safe_next
from app.routes.gm import travel_abundance, travel_choices, travel_plan
from app.routes.paravent_markdown import paravent_markdown
from app.services.i18n import Translatable
from app.services.map_tiles import needs_tiles
from app.services.maps import LAYER_ROLES, MapError
from app.services.tokens import Token
from app.services.travel import Leg
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
    LabelView,
    LayerView,
    MapContext,
    PinView,
    RouteView,
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
            layer_names(page),
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


def layer_names(page: Page) -> dict[str, str]:
    """The four layers a new map starts with, named in the reader's language."""
    return {role: page.t(f"atlas.layer.role.{role}") for role in LAYER_ROLES}


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
                layer=pin.layer,
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


def _route_view(registry: StoreRegistry, found: WorldMap, route: MapRoute) -> RouteView:
    """A route as the viewer draws it: its points, its stretches with their lengths, and
    the stops a journey along it may start or end at."""
    service = registry.world_maps
    lengths = service.stretch_miles(found, route)
    return RouteView(
        id=route.id,
        layer=route.layer,
        name=route.name,
        kind=route.kind,
        points=[list(p) for p in route.points],
        stretches=[
            {
                "start": st.start,
                "terrain": st.terrain,
                "difficult": st.difficult,
                "miles": round(lengths[i], 3) if lengths is not None else None,
            }
            for i, st in enumerate(route.stretches)
        ],
        miles=round(sum(lengths), 3) if lengths is not None else None,
        stops=[{"at": x.at, "index": x.index, "name": x.name} for x in service.stops(found, route)],
    )


def _party(registry: StoreRegistry, found: WorldMap) -> dict[str, Any] | None:
    """Where the party stopped on a route of this map, if its last journey was one."""
    journey = registry.clock.of(found.scope.world_id).journey
    if journey is None or journey.map_id != found.id or journey.route_at is None:
        return None
    route = next((r for r in found.routes if r.id == journey.route_id), None)
    if route is None or found.scale is None:
        return None
    x, y = registry.world_maps.position(found, route, journey.route_at)
    return {"x": x, "y": y, "route": route.id, "at": journey.route_at, "to": journey.route_to}


def _context(
    page: Page, registry: StoreRegistry, found: WorldMap, user_id: str, focus: str = ""
) -> MapContext:
    service = registry.world_maps
    shown = service.shown(user_id, found)
    counts = service.counts(found)
    layers = [
        LayerView(
            id=layer.id,
            name=layer.name,
            role=layer.role,
            visible=layer.visible,
            shown=shown.get(layer.id, layer.visible),
            locked=layer.locked,
            from_zoom=layer.from_zoom,
            count=counts.get(layer.id, 0),
        )
        for layer in found.layers
    ]
    labels = [
        LabelView(
            id=x.id,
            layer=x.layer,
            x=x.x,
            y=x.y,
            text=x.text,
            style=x.style,
            size=x.size,
            angle=x.angle,
        )
        for x in found.labels
    ]
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
    routes = [_route_view(registry, found, r) for r in found.routes]
    party = _party(registry, found)
    return MapContext(
        routes=routes,
        party=party,
        travel=registry.feature(found.scope.world_id, "travel") and found.scale is not None,
        pins=_pins(page, registry, found),
        frames=frames,
        path=service.path(found),
        candidates=_candidates(page, registry, found),
        framable=service.frame_candidates(found),
        disagree=service.scales_disagree(found),
        focus=focus if any(p.id == focus for p in found.pins) else "",
        layers=layers,
        labels=labels,
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
    travelled: str = "",
) -> HTMLResponse:
    """The map in its viewer, where the user left it, or at ``pin``; after a journey
    along one of its roads, how it went."""
    found = registry.world_maps.with_layers(_map(registry, map_id).id, layer_names(page))
    world = registry.adventures.world(found.scope.world_id)
    ready = _start_tiles(registry, found)
    notes = (
        paravent_markdown(page, registry, Scope(world_id=world.id), found.notes).html
        if found.notes.strip()
        else None
    )
    known = ("atlas.error.", "travel.error.", "clock.error.")
    message = page.t(error) if error.startswith(known) else None
    notice_text = page.t("atlas.saved") if saved else None
    journey = registry.clock.of(world.id).journey if travelled else None
    if journey is not None and journey.map_id == found.id:
        notice_text = page.t(
            "atlas.route.travelled_stopped"
            if journey.check is not None
            else "atlas.route.travelled",
            miles=page.u.miles(journey.miles),
            hours=journey.hours,
        )
    return html(
        map_page(
            page,
            found,
            world,
            _adventure(registry, found),
            ready=ready,
            job=registry.world_maps.tiles.job(found.id),
            view=registry.world_maps.view(gm.user.id, found.id),
            around=_context(page, registry, found, gm.user.id, pin),
            notes=notes,
            notice_text=notice_text,
            travel_choices=travel_choices(registry, world.id)
            if registry.feature(world.id, "travel")
            else None,
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
    layer: Annotated[str, Form()] = "",
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
            found.id, x=px, y=py, kind=kind, target=target, label=label, note=note, layer=layer
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
    layer: Annotated[str, Form()] = "",
) -> Response:
    """A pin's own name, a note's text, its layer."""
    try:
        registry.world_maps.edit_pin(
            _map(registry, map_id).id, pin_id, label=label, note=note, layer=layer
        )
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


# -- layers and names (documentation/MAPS.md §7) -------------------------------------


def _back(found: WorldMap, error: Translatable | None = None) -> Response:
    if error is not None:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': error.key})}#layers")
    return redirect(f"{map_url(found.id)}#layers")


@router.post(f"{MAPS_URL}/{{map_id}}/layers")
async def add_layer(
    _gm: GamemasterDep, registry: RegistryDep, map_id: str, name: Annotated[str, Form()] = ""
) -> Response:
    """A new layer on top."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.add_layer(found.id, name)
    except Translatable as exc:
        return _back(found, exc)
    return _back(found)


@router.post(f"{MAPS_URL}/{{map_id}}/layers/{{layer_id}}")
async def edit_layer(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    name: Annotated[str, Form()] = "",
    visible: Annotated[str, Form()] = "",
    locked: Annotated[str, Form()] = "",
) -> Response:
    """A layer's name, whether a newcomer sees it, whether it is locked."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.edit_layer(
            found.id, layer_id, name=name, visible=bool(visible), locked=bool(locked)
        )
    except Translatable as exc:
        return _back(found, exc)
    return _back(found)


@router.post(f"{MAPS_URL}/{{map_id}}/layers/{{layer_id}}/lock", status_code=204)
async def lock_layer(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    locked: Annotated[str, Form()] = "",
) -> Response:
    """Lock a layer or unlock it, from the panel."""
    try:
        registry.world_maps.lock_layer(_map(registry, map_id).id, layer_id, locked == "1")
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/layers/{{layer_id}}/zoom")
async def layer_zoom(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    zoom: Annotated[str, Form()] = "",
) -> Response:
    """Show a layer only from a zoom in (``zoom``), or always (empty)."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.set_layer_zoom(found.id, layer_id, _number(zoom))
    except Translatable as exc:
        return _back(found, exc)
    return _back(found)


@router.post(f"{MAPS_URL}/{{map_id}}/layers/{{layer_id}}/move")
async def move_layer(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    step: Annotated[int, Form()] = 1,
) -> Response:
    """A layer one up or down."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.move_layer(found.id, layer_id, step)
    except Translatable as exc:
        return _back(found, exc)
    return _back(found)


@router.post(f"{MAPS_URL}/{{map_id}}/layers/{{layer_id}}/delete")
async def delete_layer(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    contents: Annotated[str, Form()] = "",
) -> Response:
    """Delete a layer; what is on it moves to the layer ``contents`` names, or goes."""
    found = _map(registry, map_id)
    try:
        registry.world_maps.delete_layer(found.id, layer_id, contents)
    except Translatable as exc:
        return _back(found, exc)
    return _back(found)


@router.post(f"{MAPS_URL}/{{map_id}}/layers/{{layer_id}}/shown", status_code=204)
async def show_layer(
    gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    shown: Annotated[str, Form()] = "",
) -> Response:
    """The user showed or hid a layer; remembered for them alone."""
    try:
        registry.world_maps.set_shown(gm.user.id, _map(registry, map_id).id, layer_id, shown == "1")
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/labels")
async def add_label(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    x: Annotated[str, Form()] = "",
    y: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
    style: Annotated[str, Form()] = "settlement",
    size: Annotated[str, Form()] = "",
    angle: Annotated[str, Form()] = "0",
    layer: Annotated[str, Form()] = "",
) -> Response:
    """A name written where the map was tapped."""
    found = _map(registry, map_id)
    px, py, big = _number(x), _number(y), _number(size)
    try:
        if px is None or py is None:
            raise MapError("atlas.error.view")
        registry.world_maps.add_label(
            found.id,
            x=px,
            y=py,
            text=text,
            style=style,
            size=big or max(found.width, found.height) / 100,
            angle=_number(angle) or 0,
            layer=layer,
        )
    except Translatable as exc:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': exc.key})}")
    return redirect(map_url(found.id))


@router.post(f"{MAPS_URL}/{{map_id}}/labels/{{label_id}}", status_code=204)
async def edit_label(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    label_id: str,
    text: Annotated[str, Form()] = "",
    style: Annotated[str, Form()] = "settlement",
    size: Annotated[float, Form()] = 1,
    angle: Annotated[float, Form()] = 0,
    layer: Annotated[str, Form()] = "",
) -> Response:
    """A name's words, look, size, turn or layer, from its card."""
    try:
        registry.world_maps.edit_label(
            _map(registry, map_id).id,
            label_id,
            text=text,
            style=style,
            size=size,
            angle=angle,
            layer=layer,
        )
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/labels/{{label_id}}/move", status_code=204)
async def move_label(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    label_id: str,
    x: Annotated[float, Form()],
    y: Annotated[float, Form()],
) -> Response:
    """A name dragged to another spot."""
    try:
        registry.world_maps.move_label(_map(registry, map_id).id, label_id, x, y)
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/labels/{{label_id}}/delete", status_code=204)
async def remove_label(
    _gm: GamemasterDep, registry: RegistryDep, map_id: str, label_id: str
) -> Response:
    """A name taken off the map."""
    try:
        registry.world_maps.remove_label(_map(registry, map_id).id, label_id)
    except MapError as exc:
        raise HTTPException(status_code=404) from exc
    return Response(status_code=204)


# -- roads and travel (documentation/MAPS.md §8) -------------------------------------


def _json_list(text: str) -> list[Any]:
    try:
        value = json.loads(text or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


@router.post(f"{MAPS_URL}/{{map_id}}/routes")
async def add_route(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    name: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "road",
    points: Annotated[str, Form()] = "",
    terrains: Annotated[str, Form()] = "",
    difficult: Annotated[str, Form()] = "",
    layer: Annotated[str, Form()] = "",
) -> Response:
    """A road drawn on the map: its points and each segment's terrain (JSON lists)."""
    found = _map(registry, map_id)
    drawn = [
        (float(p[0]), float(p[1]))
        for p in _json_list(points)
        if isinstance(p, list) and len(p) == 2 and all(isinstance(v, int | float) for v in p)
    ]
    try:
        registry.world_maps.add_route(
            found.id,
            name=name,
            kind=kind,
            points=drawn,
            terrains=[str(t) for t in _json_list(terrains)],
            difficult=[bool(d) for d in _json_list(difficult)],
            layer=layer,
        )
    except Translatable as exc:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': exc.key})}")
    return redirect(map_url(found.id))


@router.post(f"{MAPS_URL}/{{map_id}}/routes/{{route_id}}", status_code=204)
async def edit_route(
    request: Request,
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    route_id: str,
) -> Response:
    """A road's name, kind, layer, and each stretch's terrain (``terrain_<n>``) and
    difficulty (``difficult_<n>``), from its card."""
    form = await request.form()
    found = registry.world_maps.with_layers(_map(registry, map_id).id)
    route = next((r for r in found.routes if r.id == route_id), None)
    if route is None:
        raise HTTPException(status_code=404)
    stretches = [
        (str(form.get(f"terrain_{i}", st.terrain)), form.get(f"difficult_{i}") == "1")
        for i, st in enumerate(route.stretches)
    ]
    try:
        registry.world_maps.edit_route(
            found.id,
            route_id,
            name=str(form.get("name", route.name)),
            kind=str(form.get("kind", route.kind)),
            layer=str(form.get("layer", "")),
            stretches=stretches,
        )
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/routes/{{route_id}}/points/{{index}}/{{action}}")
async def route_point(
    _gm: GamemasterDep,
    registry: RegistryDep,
    map_id: str,
    route_id: str,
    index: int,
    action: str,
    x: Annotated[str, Form()] = "",
    y: Annotated[str, Form()] = "",
) -> Response:
    """One of a road's points: ``move`` it, ``split`` the road there into a new stretch,
    or ``delete`` it."""
    service = registry.world_maps
    found = _map(registry, map_id)
    try:
        if action == "move":
            px, py = _number(x), _number(y)
            if px is None or py is None:
                raise MapError("atlas.error.view")
            service.move_route_point(found.id, route_id, index, px, py)
        elif action == "split":
            service.split_route(found.id, route_id, index)
        elif action == "delete":
            service.remove_route_point(found.id, route_id, index)
        else:
            raise HTTPException(status_code=404)
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/routes/{{route_id}}/delete", status_code=204)
async def remove_route(
    _gm: GamemasterDep, registry: RegistryDep, map_id: str, route_id: str
) -> Response:
    """A road taken off the map."""
    try:
        registry.world_maps.remove_route(_map(registry, map_id).id, route_id)
    except MapError as exc:
        raise HTTPException(status_code=404) from exc
    return Response(status_code=204)


@router.post(f"{MAPS_URL}/{{map_id}}/travel")
async def travel_route(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep, map_id: str
) -> Response:
    """The party travels along a road, from one stop to another (miles along it), each
    stretch at its terrain's pace; the clock, rations and encounters run as on any road,
    and where the party stops is kept for the map."""
    form = await request.form()
    found = _map(registry, map_id)
    world_id = found.scope.world_id
    route = next((r for r in found.routes if r.id == str(form.get("route", ""))), None)
    start, end = _number(str(form.get("from", ""))), _number(str(form.get("to", "")))
    user = gm.user
    try:
        if not registry.feature(world_id, "travel"):
            raise MapError("clock.error.switched_off")
        if route is None or start is None or end is None:
            raise MapError("atlas.error.route_gone")
        abundance = travel_abundance(form)
        legs = [
            Leg(miles, registry.terrain(world_id, terrain, abundance), hard)
            for miles, terrain, hard in registry.world_maps.legs(found, route, start, end)
        ]
        journey, covered = registry.travel.travel_route(
            world_id,
            travel_plan(registry, world_id, form),
            legs,
            path=route.kind != "",
            rules=registry.rules_of(world_id),
            resolve=registry.monster_resolve(world_id),
            by=user.username,
            by_name=user.display_name or user.username,
            weather=registry.weather(world_id),
        )
    except Translatable as exc:
        return redirect(f"{map_url(found.id)}?{urlencode({'error': exc.key})}")
    at = start + covered if end >= start else start - covered
    kept = journey.model_copy(
        update={"map_id": found.id, "route_id": route.id, "route_at": round(at, 3),
                "route_to": end}
    )  # fmt: skip
    registry.clock.keep(world_id, kept)
    return redirect(f"{map_url(found.id)}?travelled=1")


__all__ = ["router"]
