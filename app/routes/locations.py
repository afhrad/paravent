"""Places (documentation/ADVENTURES.md §4), for the gamemaster.

* ``GET/POST /gm/worlds/{world_id}/places`` — a world's places; add one
* ``GET /gm/places/{id}`` — one place: its notes and what they name
* ``GET/POST /gm/places/{id}/edit`` — name, where, tags, notes (version-checked)
* ``POST /gm/places/{id}/picture``, ``…/picture/clear`` — its picture
* ``GET/POST /gm/places/{id}/delete`` — remove it, after asking
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Location
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services import scope
from app.services.adventures import EditConflict, text_version
from app.services.i18n import Translatable
from app.services.locations import named_in
from app.services.media import MAX_BYTES, MediaError
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.library_links import LOCATIONS_URL, WORLDS_URL, location_url, npc_url, treasure_url
from app.ui.location_pages import (
    Here,
    location_delete_page,
    location_form_page,
    location_page,
    locations_page,
    place_name_field,
    world_locations_url,
)
from app.ui.markdown import LIBRARY_ICON

router = APIRouter(tags=["places"])


def _locations(
    page: Page,
    registry: StoreRegistry,
    world_id: str,
    *,
    selected: str = "",
    error: str | None = None,
    notice_text: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    world = registry.adventures.world(world_id)
    service = registry.locations
    rows = [(x, service.place(x).names) for x in service.list(world_id)]
    return html(
        locations_page(
            page,
            world,
            rows,
            scope.choices(registry.adventures, world_id),
            selected=selected or f"w:{world_id}",
            error=error,
            notice_text=notice_text,
        ),
        status_code=status_code,
    )


@router.get(f"{WORLDS_URL}/{{world_id}}/places", response_class=HTMLResponse)
async def list_locations(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    place: str = "",
    deleted: str = "",
) -> HTMLResponse:
    """A world's places."""
    notice_text = page.t("places.deleted") if deleted else None
    return _locations(page, registry, world_id, selected=place, notice_text=notice_text)


@router.post(f"{WORLDS_URL}/{{world_id}}/places", response_class=HTMLResponse)
async def create_location(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """Add a place where ``place`` (the scope) says, then open its form."""
    try:
        location = registry.locations.create(name, place or f"w:{world_id}", world_id)
    except Translatable as exc:
        return _locations(
            page, registry, world_id, selected=place, error=page.t.error(exc), status_code=422
        )
    return redirect(f"{location_url(location.id)}/edit")


def here_of(registry: StoreRegistry, location: Location) -> list[Here]:
    """What a place's notes name, resolved nearest first from where the place is."""
    where = location.scope
    out = []
    for kind, name in named_in(location.notes):
        url = None
        if kind == "npc":
            npc = registry.npcs.find(name, where)
            url = npc_url(npc.id) if npc else None
        elif kind == "encounter":
            encounter = registry.encounters.find(name, where)
            url = encounter_url(encounter.id) if encounter else None
        else:
            treasure = registry.treasure.find(name, where)
            url = treasure_url(treasure.id) if treasure else None
        out.append(Here(kind, name, url, LIBRARY_ICON[kind]))
    return out


@router.get(f"{LOCATIONS_URL}/{{location_id}}", response_class=HTMLResponse)
async def show_location(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, location_id: str, saved: str = ""
) -> HTMLResponse:
    """One place."""
    service = registry.locations
    location = service.get(location_id)
    return html(
        location_page(
            page,
            location,
            registry.adventures.world(location.scope.world_id),
            service.place(location).names,
            paravent_markdown(page, registry, location.scope, location.notes),
            here_of(registry, location),
            notice_text=page.t("places.saved") if saved else None,
        )
    )


def _form(
    page: Page,
    registry: StoreRegistry,
    location_id: str,
    *,
    values: dict[str, object] | None = None,
    base: str | None = None,
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    location = registry.locations.get(location_id)
    current: dict[str, object] = {
        "name": location.name,
        "place": scope.encode(location.scope),
        "tags": location.tags,
        "notes": location.notes,
    } | (values or {})
    tags = current["tags"]
    return html(
        location_form_page(
            page,
            location,
            registry.adventures.world(location.scope.world_id),
            scope.choices(registry.adventures, location.scope.world_id),
            name=str(current["name"]),
            place=str(current["place"]),
            tags=list(tags) if isinstance(tags, list) else [],
            notes=str(current["notes"]),
            base=text_version(location.notes) if base is None else base,
            name_styles=registry.naming_for(location.scope.world_id).entries(),
            conflict=conflict,
            error=error,
            notice_text=notice_text,
        ),
        status_code=status_code,
    )


@router.get(f"{LOCATIONS_URL}/{{location_id}}/edit", response_class=HTMLResponse)
async def edit_location(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    location_id: str,
    uploaded: str = "",
    error: str = "",
) -> HTMLResponse:
    """The place's form."""
    message = None
    if error.startswith("media.error."):
        message = page.t(error, mb=MAX_BYTES // (1024 * 1024))
    notice_text = page.t("adventures.uploaded", path=uploaded) if uploaded else None
    return _form(page, registry, location_id, error=message, notice_text=notice_text)


@router.post(f"{LOCATIONS_URL}/{{location_id}}/edit", response_class=HTMLResponse)
async def save_location(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    location_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    base: Annotated[str, Form()] = "",
) -> Response:
    """Save a place; notes changed elsewhere since the form opened are shown beside the
    typed ones instead of being overwritten."""
    try:
        registry.locations.update(
            location_id,
            name=name,
            scope_value=place,
            tags=[tags],
            notes=notes,
            base=base or None,
        )
    except Translatable as exc:
        conflict = exc.current if isinstance(exc, EditConflict) else None
        values: dict[str, object] = {
            "name": name,
            "place": place,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            "notes": notes,
        }
        return _form(
            page,
            registry,
            location_id,
            values=values,
            base=text_version(conflict) if conflict is not None else base,
            conflict=conflict,
            error=page.t.error(exc),
            status_code=409 if conflict is not None else 422,
        )
    return redirect(f"{location_url(location_id)}?saved=1")


@router.post(f"{LOCATIONS_URL}/{{location_id}}/edit/name", response_class=HTMLResponse)
async def roll_location_name(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    location_id: str,
    request: Request,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    base: Annotated[str, Form()] = "",
    style: Annotated[str, Form()] = "",
) -> Response:
    """A rolled name in place of the typed one, nothing saved: from HTMX the name field
    alone, swapped in place; else the form again with everything as typed."""
    location = registry.locations.get(location_id)
    naming = registry.naming_for(location.scope.world_id)
    rolled = registry.locations.roll_name(location_id, naming, style, page.lang)
    if request.headers.get("HX-Request") == "true":
        styles = naming.entries()
        return html(place_name_field(page, location, rolled or name, styles, style))
    values: dict[str, object] = {
        "name": rolled or name,
        "place": place,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "notes": notes,
    }
    return _form(
        page,
        registry,
        location_id,
        values=values,
        base=base,
        error=None if rolled else page.t("naming.error.roll"),
        status_code=200 if rolled else 422,
    )


@router.post(f"{LOCATIONS_URL}/{{location_id}}/picture")
async def upload_picture(
    _gm: GamemasterDep,
    registry: RegistryDep,
    location_id: str,
    file: Annotated[UploadFile, File()],
) -> Response:
    """Store and use a picture."""
    data = await file.read(MAX_BYTES + 1)
    back = f"{location_url(location_id)}/edit"
    try:
        relative = registry.locations.upload_picture(location_id, file.filename or "place", data)
    except MediaError as exc:
        return redirect(f"{back}?{urlencode({'error': exc.key})}")
    return redirect(f"{back}?{urlencode({'uploaded': relative})}")


@router.post(f"{LOCATIONS_URL}/{{location_id}}/picture/clear")
async def clear_picture(_gm: GamemasterDep, registry: RegistryDep, location_id: str) -> Response:
    """Show the place without a picture."""
    registry.locations.clear_picture(location_id)
    return redirect(f"{location_url(location_id)}/edit")


@router.get(f"{LOCATIONS_URL}/{{location_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_location(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, location_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(location_delete_page(page, registry.locations.get(location_id)))


@router.post(f"{LOCATIONS_URL}/{{location_id}}/delete")
async def delete_location(_gm: GamemasterDep, registry: RegistryDep, location_id: str) -> Response:
    """Delete a place."""
    location = registry.locations.delete(location_id)
    return redirect(f"{world_locations_url(location.scope.world_id)}?deleted=1")
