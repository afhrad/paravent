"""Places (documentation/ADVENTURES.md §4), for the gamemaster.

* ``GET/POST /gm/worlds/{world_id}/places`` — a world's places; add one
* ``GET /gm/places/{id}`` — one place: its notes and what they name
* ``GET/POST /gm/places/{id}/edit`` — name, where, tags, notes (version-checked)
* ``GET/POST /gm/places/{id}/delete`` — remove it, after asking
"""

from __future__ import annotations

from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Location
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services import scope
from app.services.adventures import EditConflict, text_version
from app.services.dice import Dice
from app.services.i18n import Translatable
from app.services.locations import named_in
from app.services.media import MAX_BYTES, MediaError
from app.services.naming import NamingError
from app.services.settlement_gen import SIZES, SettlementGenerator, plays_2024
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
from app.ui.settlement_pages import settlement_page, size_text

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
    new: bool = False,
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
            new=new,
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
    """A world's places (``?tool=place`` opens the dialog for a new one)."""
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
    tags: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    picture: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """Add a place where ``place`` (the scope) says, with its tags, a first note and a
    picture if given (the new-place dialog), then open its page."""
    service = registry.locations
    try:
        location = service.create(name, place or f"w:{world_id}", world_id)
        words = [w.strip() for w in tags.split(",") if w.strip()]
        if words or notes.strip():
            location = service.update(
                location.id,
                name=location.name,
                scope_value=place or f"w:{world_id}",
                tags=words,
                notes=notes,
            )
    except Translatable as exc:
        return _locations(
            page,
            registry,
            world_id,
            selected=place,
            error=page.t.error(exc),
            status_code=422,
            new=True,
        )
    if picture is not None and picture.filename:
        data = await picture.read(MAX_BYTES + 1)
        try:
            service.upload_picture(location.id, picture.filename, data)
        except MediaError:
            # The place stands; its page says why the picture was refused.
            return redirect(f"{location_url(location.id)}?title_error=image")
    return redirect(location_url(location.id))


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
    undo = registry.trash.entry_of("place", location.id) or ""
    return redirect(f"{world_locations_url(location.scope.world_id)}?deleted=1&undo={undo}")


# -- rolled as a settlement (app/services/settlement_gen.py) ---------------------------


def _settlement_generator(
    registry: StoreRegistry, world_id: str, language: str
) -> SettlementGenerator:
    world = registry.adventures.world(world_id)
    naming = registry.naming_for(world_id)
    # The world's own names for buildings — a style of the table's, or one it ticked —
    # name the tavern; the starters every table has leave it to the guide's table.
    own = [
        e
        for e in naming.entries()
        if e.subject == "building"
        and e.style is not None
        and (e.kind != "starter" or e.key in world.naming_styles)
    ]

    def tavern() -> str:
        try:
            return next(iter(naming.names(own[0].key, count=1, language=language)), "")
        except NamingError:
            return ""

    has_style = bool(own)
    return SettlementGenerator(
        lambda caption, books: registry.book_table(world_id, caption, books),
        Dice(),
        new_rules=plays_2024(world.source_set.sources),
        tavern=tavern if has_style else None,
    )


def _settlement(
    page: Page,
    registry: StoreRegistry,
    location: Location,
    generator: SettlementGenerator,
    size: str,
    rolled: dict[str, str],
    kept: frozenset[str],
    name: str = "",
) -> HTMLResponse:
    world = registry.adventures.world(location.scope.world_id)
    return html(
        settlement_page(
            page,
            location,
            world,
            name=name or location.name,
            name_styles=registry.naming_for(world.id).entries(),
            sizes=generator.sizes(),
            size=size if size in SIZES else SIZES[0],
            rolled=rolled,
            sources={p: generator.source_of(p) for p in rolled},
            kept=kept,
        )
    )


@router.get(f"{LOCATIONS_URL}/{{location_id}}/settlement", response_class=HTMLResponse)
async def roll_settlement(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, location_id: str
) -> HTMLResponse:
    """A place rolled as a settlement, every part ticked."""
    location = registry.locations.get(location_id)
    generator = _settlement_generator(registry, location.scope.world_id, page.lang)
    rolled = generator.roll()
    return _settlement(page, registry, location, generator, SIZES[0], rolled, frozenset(rolled))


@router.post(f"{LOCATIONS_URL}/{{location_id}}/settlement", response_class=HTMLResponse)
async def settle(
    request: Request, _gm: GamemasterDep, page: PageDep, registry: RegistryDep, location_id: str
) -> Response:
    """``roll`` everything again (the ticked kept), ``reroll:<part>`` one part, or
    ``apply``: write the size and the ticked parts into the place's notes."""
    form = await request.form()
    location = registry.locations.get(location_id)
    generator = _settlement_generator(registry, location.scope.world_id, page.lang)
    parts = generator.parts()
    rolled = {p: str(form.get(f"v_{p}", ""))[:500] for p in parts}
    kept = frozenset(str(k) for k in form.getlist("keep") if str(k) in parts)
    size = str(form.get("size", SIZES[0]))
    name = " ".join(str(form.get("name", "")).split()) or location.name
    action = str(form.get("action", "roll"))
    if action.startswith("reroll:") and action[7:] in parts:
        part = action[7:]
        rolled[part] = generator.roll_part(part, rolled[part])
    elif action == "roll":
        rolled = generator.roll({p: v for p, v in rolled.items() if p in kept})
        kept = frozenset(rolled)
    elif action == "apply":
        t = page.t
        chosen = next((s for s in generator.sizes() if s.key == size), None)
        lines = [f"**{t('settlement.size')}:** {size_text(page, chosen)}"] if chosen else []
        lines += [
            f"- **{t(f'settlement.part.{p}')}:** {rolled[p]}"
            for p in parts
            if p in kept and rolled.get(p)
        ]
        tags = [t(f"settlement.size.{chosen.key}")] if chosen else []
        saved = registry.locations.add_details(location_id, "\n".join(lines), tags)
        if name != saved.name:  # a rolled name: every text naming the place follows
            # A name the world cannot take: the place keeps its own.
            with suppress(Translatable):
                registry.locations.update(
                    location_id,
                    name=name,
                    scope_value=scope.encode(saved.scope),
                    tags=saved.tags,
                    notes=saved.notes,
                )
        return redirect(location_url(location_id))
    return _settlement(page, registry, location, generator, size, rolled, kept, name)
