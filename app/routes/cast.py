"""NPCs and treasure in a world (documentation/ADVENTURES.md §4).

* ``/gm/worlds/{id}/npcs``          — the world's NPCs; ``POST`` adds one (``name``, ``place``)
* ``/gm/npcs/{id}``                 — read; ``/edit``, ``/delete``, ``/portrait``
* ``/gm/worlds/{id}/treasure``      — the world's treasure; ``POST`` adds one
* ``/gm/treasure/{id}``             — the treasure, with the item picker (``?q=``);
  ``POST`` saves name, place, coins and notes
* ``POST /gm/treasure/{id}/items``  — add ``ref=Name|SOURCE``; ``/items/{i}`` sets a count

``place`` is a scope (``w:``/``a:``/``c:`` + id, :mod:`app.services.scope`). Creating from
a chapter's or adventure's panel passes its own scope; either way the new NPC or treasure
opens, since the next thing to do is fill it in.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Coins, Scope, SourceSet
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services import scope
from app.services.adventures import EditConflict, text_version
from app.services.i18n import Translatable
from app.services.media import MAX_BYTES, MediaError
from app.services.naming_workshop import races
from app.services.npcs import ObjectError
from app.services.references import ReferenceNotFound
from app.services.treasure_roll import TreasureRollError, roller_for, tables_for
from app.store.registry import StoreRegistry
from app.ui.cast_pages import (
    RollForm,
    coins_text,
    npc_delete_page,
    npc_form_page,
    npc_page,
    npcs_page,
    treasure_delete_page,
    treasure_page,
    treasures_page,
    world_npcs_url,
    world_treasure_url,
)
from app.ui.context import Page
from app.ui.library_links import NPCS_URL, TREASURE_URL, WORLDS_URL, npc_url, treasure_url
from app.ui.markdown import Rendered
from app.ui.tags import localized_context

router = APIRouter(tags=["cast"])

ITEMS_GROUP = "items"


def _notes(page: Page, registry: StoreRegistry, here: Scope, text: str) -> Rendered:
    """Render an object's notes as if written where the object is placed."""
    return paravent_markdown(page, registry, here, text)


# -- NPCs ------------------------------------------------------------------------------


def _npcs(
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
    npcs = registry.npcs
    rows = [(n, npcs.place(n).names) for n in npcs.list(world_id)]
    return html(
        npcs_page(
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


@router.get(f"{WORLDS_URL}/{{world_id}}/npcs", response_class=HTMLResponse)
async def list_npcs(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    place: str = "",
    deleted: str = "",
) -> HTMLResponse:
    """A world's NPCs."""
    notice_text = page.t("npcs.deleted") if deleted else None
    return _npcs(page, registry, world_id, selected=place, notice_text=notice_text)


@router.post(f"{WORLDS_URL}/{{world_id}}/npcs", response_class=HTMLResponse)
async def create_npc(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """Add an NPC where ``place`` says, then go back to where the form was."""
    try:
        npc = registry.npcs.create(name, place or f"w:{world_id}", world_id)
    except Translatable as exc:  # an empty name, or a place not in this world
        return _npcs(
            page, registry, world_id, selected=place, error=page.t.error(exc), status_code=422
        )
    return redirect(f"{npc_url(npc.id)}/edit")


@router.get(f"{NPCS_URL}/{{npc_id}}", response_class=HTMLResponse)
async def show_npc(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, npc_id: str, saved: str = ""
) -> HTMLResponse:
    """One NPC."""
    npcs = registry.npcs
    npc = npcs.get(npc_id)
    world = registry.adventures.world(npc.scope.world_id)
    creature = None
    if npc.statblock:
        name, _, source = npc.statblock.partition("|")
        try:
            creature = registry.references.get("monster", source or "-", name, world.id)
        except ReferenceNotFound:
            creature = None  # a book removed from the corpus: the NPC stays, unstatted
    ctx = localized_context(
        page.t,
        resolver=registry.references.resolver(),
        source_set=world.source_set,
        world_id=world.id,
        units=page.u,
    )
    return html(
        npc_page(
            page,
            npc,
            world,
            npcs.place(npc).names,
            _notes(page, registry, npc.scope, npc.notes),
            creature,
            ctx,
            notice_text=page.t("npcs.saved") if saved else None,
        )
    )


def _npc_form(
    page: Page,
    registry: StoreRegistry,
    npc_id: str,
    *,
    values: dict[str, object] | None = None,
    base: str | None = None,
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    npc = registry.npcs.get(npc_id)
    current: dict[str, object] = {
        "name": npc.name,
        "place": scope.encode(npc.scope),
        "roles": npc.roles,
        "statblock": npc.statblock or "",
        "notes": npc.notes,
        "race": npc.race,
        "culture": npc.culture,
    } | (values or {})
    groups = races(registry.naming_for(npc.scope.world_id).entries())
    roles = current["roles"]
    return html(
        npc_form_page(
            page,
            npc,
            registry.adventures.world(npc.scope.world_id),
            scope.choices(registry.adventures, npc.scope.world_id),
            name=str(current["name"]),
            place=str(current["place"]),
            roles=list(roles) if isinstance(roles, list) else [],
            statblock_ref=str(current["statblock"]),
            notes=str(current["notes"]),
            race=str(current["race"]),
            culture=str(current["culture"]),
            races=[g.race for g in groups if g.race],
            cultures=sorted({c for g in groups for c in g.cultures}),
            base=text_version(npc.notes) if base is None else base,
            conflict=conflict,
            error=error,
            notice_text=notice_text,
        ),
        status_code=status_code,
    )


@router.get(f"{NPCS_URL}/{{npc_id}}/edit", response_class=HTMLResponse)
async def edit_npc(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    npc_id: str,
    uploaded: str = "",
    error: str = "",
) -> HTMLResponse:
    """The NPC form."""
    message = None
    if error.startswith("media.error."):
        message = page.t(error, mb=MAX_BYTES // (1024 * 1024))
    notice_text = page.t("adventures.uploaded", path=uploaded) if uploaded else None
    return _npc_form(page, registry, npc_id, error=message, notice_text=notice_text)


@router.post(f"{NPCS_URL}/{{npc_id}}/edit", response_class=HTMLResponse)
async def save_npc(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    npc_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
    roles: Annotated[list[str] | None, Form()] = None,
    statblock: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    base: Annotated[str, Form()] = "",
    race: Annotated[str, Form()] = "",
    culture: Annotated[str, Form()] = "",
) -> Response:
    """Save an NPC; notes changed elsewhere since the form opened are shown beside the
    typed ones instead of being overwritten."""
    try:
        registry.npcs.update(
            npc_id,
            name=name,
            scope_value=place,
            roles=roles or [],
            statblock=statblock,
            notes=notes,
            base=base or None,
            race=race,
            culture=culture,
        )
    except Translatable as exc:
        conflict = exc.current if isinstance(exc, EditConflict) else None
        values: dict[str, object] = {
            "race": race,
            "culture": culture,
            "name": name,
            "place": place,
            "roles": roles or [],
            "statblock": statblock,
            "notes": notes,
        }
        return _npc_form(
            page,
            registry,
            npc_id,
            values=values,
            base=text_version(conflict) if conflict is not None else base,
            conflict=conflict,
            error=page.t.error(exc),
            status_code=409 if conflict is not None else 422,
        )
    return redirect(f"{npc_url(npc_id)}?saved=1")


@router.post(f"{NPCS_URL}/{{npc_id}}/portrait")
async def upload_portrait(
    _gm: GamemasterDep,
    registry: RegistryDep,
    npc_id: str,
    file: Annotated[UploadFile, File()],
) -> Response:
    """Store and use a portrait."""
    data = await file.read(MAX_BYTES + 1)
    back = f"{npc_url(npc_id)}/edit"
    try:
        relative = registry.npcs.upload_portrait(npc_id, file.filename or "portrait", data)
    except MediaError as exc:
        return redirect(f"{back}?{urlencode({'error': exc.key})}")
    return redirect(f"{back}?{urlencode({'uploaded': relative})}")


@router.post(f"{NPCS_URL}/{{npc_id}}/portrait/clear")
async def clear_portrait(_gm: GamemasterDep, registry: RegistryDep, npc_id: str) -> Response:
    """Show the NPC without a portrait."""
    registry.npcs.clear_portrait(npc_id)
    return redirect(f"{npc_url(npc_id)}/edit")


@router.get(f"{NPCS_URL}/{{npc_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_npc(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, npc_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(npc_delete_page(page, registry.npcs.get(npc_id)))


@router.post(f"{NPCS_URL}/{{npc_id}}/delete")
async def delete_npc(_gm: GamemasterDep, registry: RegistryDep, npc_id: str) -> Response:
    """Delete an NPC."""
    npc = registry.npcs.delete(npc_id)
    return redirect(f"{world_npcs_url(npc.scope.world_id)}?deleted=1")


# -- treasure ----------------------------------------------------------------------------


def _treasures(
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
    service = registry.treasure
    rows = [(x, service.place(x).names) for x in service.list(world_id)]
    return html(
        treasures_page(
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


@router.get(f"{WORLDS_URL}/{{world_id}}/treasure", response_class=HTMLResponse)
async def list_treasure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    place: str = "",
    deleted: str = "",
) -> HTMLResponse:
    """A world's treasure."""
    notice_text = page.t("treasure.deleted") if deleted else None
    return _treasures(page, registry, world_id, selected=place, notice_text=notice_text)


@router.post(f"{WORLDS_URL}/{{world_id}}/treasure", response_class=HTMLResponse)
async def create_treasure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """Add a treasure and open it, so its items can be picked."""
    try:
        treasure = registry.treasure.create(name, place or f"w:{world_id}", world_id)
    except Translatable as exc:
        return _treasures(
            page, registry, world_id, selected=place, error=page.t.error(exc), status_code=422
        )
    return redirect(treasure_url(treasure.id))


def _roll_form(
    registry: StoreRegistry, source_set: SourceSet | None, kind: str, cr: int, book: str
) -> RollForm | None:
    """The roll form's defaults: the world's tables unless another was chosen."""
    books = tuple(sorted(registry.loot.books()))
    if not books:
        return None
    chosen = book if book in books else tables_for(source_set, set(books))
    return RollForm(
        kind if kind in ("individual", "hoard") else "hoard", max(0, min(30, cr)), chosen, books
    )


def _treasure(
    page: Page,
    registry: StoreRegistry,
    treasure_id: str,
    *,
    q: str = "",
    all_books: bool = False,
    error: str | None = None,
    notice_text: str | None = None,
    status_code: int = 200,
    kind: str = "hoard",
    cr: int = 0,
    book: str = "",
    notes_text: str | None = None,
    base: str | None = None,
    conflict: str | None = None,
) -> HTMLResponse:
    service = registry.treasure
    treasure = service.get(treasure_id)
    world = registry.adventures.world(treasure.scope.world_id)
    result = registry.search.search(
        q[:100],
        world.source_set,
        group=ITEMS_GROUP,
        all_books=all_books,
        term=page.t.terms(),
    )
    return html(
        treasure_page(
            page,
            treasure,
            world,
            service.place(treasure).names,
            service.lines(treasure),
            result,
            scope.choices(registry.adventures, world.id),
            _notes(page, registry, treasure.scope, treasure.notes),
            roll=_roll_form(registry, world.source_set, kind, cr, book),
            notes_text=notes_text,
            base=text_version(treasure.notes) if base is None else base,
            conflict=conflict,
            error=error,
            notice_text=notice_text,
        ),
        status_code=status_code,
    )


@router.get(f"{TREASURE_URL}/{{treasure_id}}", response_class=HTMLResponse)
async def show_treasure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    treasure_id: str,
    q: str = "",
    all: str = "",  # noqa: A002 — the query parameter's name
    saved: str = "",
    kind: str = "hoard",
    cr: int = 0,
) -> HTMLResponse:
    """A treasure, with the item picker and the roll form (``kind``/``cr`` prefill it)."""
    notice_text = page.t("treasure.saved") if saved else None
    return _treasure(
        page,
        registry,
        treasure_id,
        q=q,
        all_books=all == "1",
        notice_text=notice_text,
        kind=kind,
        cr=cr,
    )


@router.post(f"{TREASURE_URL}/{{treasure_id}}/roll", response_class=HTMLResponse)
async def roll_treasure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    treasure_id: str,
    kind: Annotated[str, Form()] = "hoard",
    cr: Annotated[int, Form(ge=0, le=30)] = 0,
    book: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Roll on the treasure tables and put the result into the treasure."""
    treasure = registry.treasure.get(treasure_id)
    world = registry.adventures.world(treasure.scope.world_id)
    form = _roll_form(registry, world.source_set, kind, cr, book)
    if form is None:
        return _treasure(page, registry, treasure_id, error=page.t("treasure.no_tables"))
    roller = roller_for(registry.compendium, registry.loot, world.source_set, form.book)
    try:
        rolled = roller.roll("individual" if form.kind == "individual" else "hoard", cr, form.book)
    except TreasureRollError:
        return _treasure(page, registry, treasure_id, error=page.t("treasure.no_tables"))
    registry.treasure.add_rolled(treasure_id, rolled)
    found = coins_text(page, Coins(**rolled.coins))
    summary = page.t(
        "treasure.rolled",
        coins=found or page.t("treasure.no_coins"),
        items=len(rolled.items) + len(rolled.unresolved),
        detail=" · ".join(rolled.lines),
    )
    return _treasure(
        page, registry, treasure_id, notice_text=summary, kind=form.kind, cr=cr, book=form.book
    )


def _count(value: str) -> int:
    return int(value) if value.strip().isdigit() else 0


@router.post(f"{TREASURE_URL}/{{treasure_id}}", response_class=HTMLResponse)
async def save_treasure(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    treasure_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    pp: Annotated[str, Form()] = "0",
    gp: Annotated[str, Form()] = "0",
    ep: Annotated[str, Form()] = "0",
    sp: Annotated[str, Form()] = "0",
    cp: Annotated[str, Form()] = "0",
    base: Annotated[str, Form()] = "",
) -> Response:
    """Save a treasure's name, place, coins and notes; notes changed elsewhere since the
    form opened are shown beside the typed ones instead of being overwritten."""
    coins = Coins(pp=_count(pp), gp=_count(gp), ep=_count(ep), sp=_count(sp), cp=_count(cp))
    try:
        registry.treasure.update(
            treasure_id, name=name, scope_value=place, coins=coins, notes=notes, base=base or None
        )
    except Translatable as exc:
        conflict = exc.current if isinstance(exc, EditConflict) else None
        return _treasure(
            page,
            registry,
            treasure_id,
            error=page.t.error(exc),
            status_code=409 if conflict is not None else 422,
            notes_text=notes,
            base=text_version(conflict) if conflict is not None else base,
            conflict=conflict,
        )
    return redirect(f"{treasure_url(treasure_id)}?saved=1")


@router.post(f"{TREASURE_URL}/{{treasure_id}}/items")
async def add_item(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    treasure_id: str,
    ref: Annotated[str, Form()] = "",
    q: Annotated[str, Form()] = "",
    all: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Add one of an item, then come back to the same search."""
    try:
        registry.treasure.add_item(treasure_id, ref)
    except ObjectError as exc:
        return _treasure(page, registry, treasure_id, error=page.t.error(exc), status_code=422)
    params = urlencode({k: v for k, v in (("q", q.strip()), ("all", all)) if v})
    return redirect(f"{treasure_url(treasure_id)}{'?' + params if params else ''}#picker")


@router.post(f"{TREASURE_URL}/{{treasure_id}}/items/{{index}}")
async def set_item_count(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    treasure_id: str,
    index: int,
    count: Annotated[int, Form()] = 0,
) -> Response:
    """Change how many of one item there are; 0 takes it out."""
    try:
        registry.treasure.set_count(treasure_id, index, count)
    except ObjectError as exc:
        return _treasure(page, registry, treasure_id, error=page.t.error(exc), status_code=422)
    return redirect(f"{treasure_url(treasure_id)}#plan")


@router.get(f"{TREASURE_URL}/{{treasure_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_treasure(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, treasure_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(treasure_delete_page(page, registry.treasure.get(treasure_id)))


@router.post(f"{TREASURE_URL}/{{treasure_id}}/delete")
async def delete_treasure(_gm: GamemasterDep, registry: RegistryDep, treasure_id: str) -> Response:
    """Delete a treasure."""
    treasure = registry.treasure.delete(treasure_id)
    return redirect(f"{world_treasure_url(treasure.scope.world_id)}?deleted=1")


__all__ = ["router"]
