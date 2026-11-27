"""The maps the gamemaster shows the players (documentation/MAPS.md §9).

* ``GET  /maps``                       — the table's world's maps shown to the players
* ``GET  /maps/{id}``                  — one, with what lies on the layers players see
* ``GET  /maps/{id}/status``           — how far a large map's tiles got
* ``GET  /maps/{id}/tiles/{z}/{x}/{y}.webp``, ``/thumb``, ``/image`` — its pictures
* ``POST /maps/{id}/view``             — where the reader left it
* ``POST /maps/{id}/layers/{id}/shown`` — a layer, or the grid, shown or hidden for them

A player reads a map while it is shown to the players and belongs to the table's world
(documentation/CURRENT-WORLD.md); the gamemaster reads every map here, to see it as the
players do. Nothing on a layer the players do not see leaves the server, nor the
gamemaster's notes, nor what a pin's target says about itself.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.models.gamestate import WorldMap
from app.routes.auth import html
from app.routes.maps import (
    _TILE_HEADERS,
    _adventure,
    _excerpt,
    _party,
    _pin_target,
    _route_view,
    _start_tiles,
    grid_view,
    layer_names,
)
from app.services.auth import Identity
from app.services.map_tiles import needs_tiles
from app.services.maps import GRID, MapError
from app.services.tokens import Token
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.map_pages import (
    PLAYER_MAPS_URL,
    FrameView,
    LabelView,
    LayerView,
    MapContext,
    PinView,
    map_page,
    player_map_url,
    player_maps_page,
    tiling_panel,
)
from app.ui.tokens import token

router = APIRouter(tags=["maps"])


def readable(identity: Identity, registry: StoreRegistry, found: WorldMap) -> bool:
    """Whether the reader may see the map here: the gamemaster always, a player while it
    is shown to the players and of the table's world."""
    if identity.is_gamemaster:
        return True
    return found.players and found.scope.world_id == registry.table.world_id()


def _map(identity: Identity, registry: StoreRegistry, map_id: str) -> WorldMap:
    found = registry.gamestate.maps.find(map_id)
    if found is None or not readable(identity, registry, found):
        raise HTTPException(status_code=404)
    return found


def player_context(
    page: Page, registry: StoreRegistry, found: WorldMap, user_id: str
) -> MapContext:
    """What a player sees on the map: the layers shown to them and what lies on those,
    pins as their kind's disc and name (a note with its text), the maps lying on it and
    those it lies in that are shown to them too, the party, the grid."""
    service = registry.world_maps
    seen = service.player_layers(found)
    shown = service.shown(user_id, found)
    layers = [
        LayerView(
            id=layer.id,
            name=layer.name,
            role=layer.role,
            visible=layer.visible,
            shown=shown.get(layer.id, layer.visible),
            locked=True,
            from_zoom=layer.from_zoom,
            count=0,
            players=True,
        )
        for layer in found.layers
        if layer.id in seen
    ]
    pins = []
    for pin in found.pins:
        if pin.layer not in seen or not service.players_see(found, pin.kind, pin.target):
            continue
        record, _url = _pin_target(registry, pin.kind, pin.target)
        name = str(getattr(record, "name", None) or getattr(record, "title", "") or pin.name)
        pins.append(
            PinView(
                id=pin.id,
                layer=pin.layer,
                x=pin.x,
                y=pin.y,
                kind=pin.kind,
                name=name if pin.kind != "note" else "",
                label=pin.label,
                url=None,
                map_url=player_map_url(pin.target) if pin.kind == "map" else None,
                token=str(token(Token(pin.kind), "sm")),
                excerpt=_excerpt(pin.note) if pin.kind == "note" else "",
                gone=False,
                note=pin.note if pin.kind == "note" else "",
            )
        )
    labels = [
        LabelView(x.id, x.layer, x.x, x.y, x.text, x.style, x.size, x.angle)
        for x in found.labels
        if x.layer in seen
    ]
    routes = [_route_view(registry, found, r, stops=False) for r in found.routes if r.layer in seen]
    frames = [
        FrameView(
            child.id,
            child.name,
            player_map_url(child.id),
            child.frame.x,
            child.frame.y,
            child.frame.width,
            child.frame.height,
        )
        for child in service.framed_on(found.id)
        if child.frame is not None
        and child.players
        and child.scope.world_id == found.scope.world_id
    ]
    path: list[WorldMap] = []
    for above in reversed(service.path(found)):
        if not above.players:
            break
        path.insert(0, above)
    return MapContext(
        pins=pins,
        frames=frames,
        path=path,
        layers=layers,
        labels=labels,
        routes=routes,
        party=_party(registry, found),
        grid=grid_view(registry, found, shown),
    )


@router.get(PLAYER_MAPS_URL, response_class=HTMLResponse)
async def player_maps(identity: IdentityDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """The maps of the table's world the gamemaster shows the players."""
    world_id = registry.table.world_id()
    world = registry.gamestate.worlds.find(world_id) if world_id else None
    maps = registry.world_maps.for_players(world_id)
    names = {a.id: a.name for a in registry.adventures.adventures(world_id)} if world_id else {}
    return html(player_maps_page(page, world, maps, names))


@router.get(PLAYER_MAPS_URL + "/{map_id}", response_class=HTMLResponse)
async def player_map(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, map_id: str
) -> HTMLResponse:
    """One map as the players see it, where the reader left it."""
    found = _map(identity, registry, map_id)
    found = registry.world_maps.with_layers(found.id, layer_names(page))
    world = registry.adventures.world(found.scope.world_id)
    return html(
        map_page(
            page,
            found,
            world,
            _adventure(registry, found),
            ready=_start_tiles(registry, found),
            job=registry.world_maps.tiles.job(found.id),
            view=registry.world_maps.view(identity.user.id, found.id),
            around=player_context(page, registry, found, identity.user.id),
            player=True,
        )
    )


@router.get(PLAYER_MAPS_URL + "/{map_id}/status", response_class=HTMLResponse)
async def player_map_status(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, map_id: str
) -> Response:
    """How far the cutting got; once the tiles are there, the page reloads."""
    found = _map(identity, registry, map_id)
    if _start_tiles(registry, found):
        return Response(headers={"HX-Refresh": "true"})
    job = registry.world_maps.tiles.job(found.id)
    return html(tiling_panel(page, found, job, player=True))


@router.get(PLAYER_MAPS_URL + "/{map_id}/tiles/{z}/{x}/{y}.webp")
async def player_map_tile(
    identity: IdentityDep, registry: RegistryDep, map_id: str, z: int, x: int, y: int
) -> Response:
    """One tile of a map shown to the reader."""
    found = _map(identity, registry, map_id)
    path = registry.world_maps.tiles.tile(found.id, found.digest, z, x, y)
    if path is None:
        _start_tiles(registry, found, retry=False)
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/webp", headers=_TILE_HEADERS)


@router.get(PLAYER_MAPS_URL + "/{map_id}/thumb")
async def player_map_thumb(identity: IdentityDep, registry: RegistryDep, map_id: str) -> Response:
    """A small picture of a map shown to the reader, for the list."""
    found = _map(identity, registry, map_id)
    if needs_tiles(found.width, found.height):
        path = registry.world_maps.tiles.thumb(found.id, found.digest)
        if path is None:
            _start_tiles(registry, found, retry=False)
    else:
        path = registry.world_maps.source(found)
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path)


@router.get(PLAYER_MAPS_URL + "/{map_id}/image")
async def player_map_image(identity: IdentityDep, registry: RegistryDep, map_id: str) -> Response:
    """A small map's picture itself, which the viewer shows whole."""
    found = _map(identity, registry, map_id)
    source = registry.world_maps.source(found)
    if source is None or needs_tiles(found.width, found.height):
        raise HTTPException(status_code=404)
    return FileResponse(source)


@router.post(PLAYER_MAPS_URL + "/{map_id}/view", status_code=204)
async def player_keep_view(
    identity: IdentityDep,
    registry: RegistryDep,
    map_id: str,
    x: Annotated[float, Form()],
    y: Annotated[float, Form()],
    zoom: Annotated[float, Form()],
) -> Response:
    """Remember where the reader left the map."""
    found = _map(identity, registry, map_id)
    try:
        registry.world_maps.keep_view(identity.user.id, found.id, x, y, zoom)
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)


@router.post(PLAYER_MAPS_URL + "/{map_id}/layers/{layer_id}/shown", status_code=204)
async def player_show_layer(
    identity: IdentityDep,
    registry: RegistryDep,
    map_id: str,
    layer_id: str,
    shown: Annotated[str, Form()] = "",
) -> Response:
    """The reader showed or hid a layer they see, or the grid; remembered for them."""
    found = _map(identity, registry, map_id)
    if layer_id != GRID and layer_id not in registry.world_maps.player_layers(found):
        raise HTTPException(status_code=404)
    try:
        registry.world_maps.set_shown(identity.user.id, found.id, layer_id, shown == "1")
    except MapError as exc:
        raise HTTPException(status_code=422) from exc
    return Response(status_code=204)
