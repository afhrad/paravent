"""What the Markdown editors do besides saving: create an NPC, encounter or treasure a
chapter names, from the drop-down (static/js/complete.js).

* ``POST /gm/editor/create`` — ``kind``, ``name``, ``scope`` (the editor's record,
  ``c:<chapter id>``, ``a:<adventure id>``, ``w:<world id>``); answers 201 with the new
  record's page in ``Location``
* ``GET  /gm/editor/new``    — a link to something the world lacks, clicked: asks whether
  to make it where the text is
* ``POST /gm/editor/new``    — makes it (or finds it, made meanwhile) and opens its page
* ``POST /gm/editor/rename`` — ``kind``, ``id``, ``name``, ``scope``: the object takes the
  name the text uses, every link to its old name following (notes too); 204
* ``POST /gm/editor/relink`` — the create page: one text's links to a name pointed at another
* ``POST /gm/editor/rename-back`` — the create page: the rename, then back to the text
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Scope
from app.routes.adventures import meant_objects
from app.routes.auth import html, redirect, safe_next
from app.services import scope as scopes
from app.services.adventures import reference_fix
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.library_links import (
    CREATABLE,
    NEW_OBJECT_URL,
    location_url,
    new_object_page,
    npc_url,
    treasure_url,
)

router = APIRouter(tags=["editor"])


def _make(registry: StoreRegistry, kind: str, name: str, where: Scope) -> str:
    """The page of the NPC, place, treasure or encounter of that name at ``where``,
    made there unless one is already found from it."""
    world = where.world_id
    value = scopes.encode(where)
    if kind == "npc":
        found = registry.npcs.find(name, where)
        return npc_url((found or registry.npcs.create(name, value, world)).id)
    if kind == "place":
        found_place = registry.locations.find(name, where)
        return location_url((found_place or registry.locations.create(name, value, world)).id)
    if kind == "treasure":
        found_treasure = registry.treasure.find(name, where)
        return treasure_url((found_treasure or registry.treasure.create(name, value, world)).id)
    found_encounter = registry.encounters.find(name, where)
    made = found_encounter or registry.encounters.create(name, world, where)
    return encounter_url(made.id)


def _scope(registry: StoreRegistry, scope: str) -> Scope:
    try:
        return scopes.decode(scope, registry.adventures)
    except scopes.ScopeError as exc:
        raise HTTPException(status_code=404, detail="scope") from exc


@router.post("/gm/editor/create", status_code=201)
async def create(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()],
    name: Annotated[str, Form()],
    scope: Annotated[str, Form()],
) -> Response:
    """A new NPC, encounter or treasure of that name where the editor's record is, unless
    one of the name is already there."""
    if kind not in CREATABLE or not name.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    url = _make(registry, kind, name, _scope(registry, scope))
    return Response(status_code=201, headers={"Location": url})


# Where an NPC, place, treasure or encounter is kept on the game-state store.
RENAMEABLE: dict[str, str] = {
    "npc": "npcs",
    "place": "locations",
    "treasure": "treasures",
    "encounter": "encounters",
}


def rename_object(
    registry: StoreRegistry, kind: str, object_id: str, name: str, world_id: str | None
) -> bool:
    """Give an object of ``world_id`` the name a text uses; every link to its old name in
    the world's texts follows. False when there is no such object there."""
    collection = getattr(registry.gamestate, RENAMEABLE[kind])
    record = collection.find(object_id)
    if record is None:
        return False
    where: Scope | None = record.scope  # an encounter may have none: its world's, then
    record_world = where.world_id if where is not None else record.world_id
    if record_world != world_id:
        return False
    old, new = record.name, name.strip()
    collection.update(object_id, name=new)
    adventure_id = where.adventure_id if where is not None else None
    registry.adventures.rewrite_references(str(record_world), kind, old, new, adventure_id)
    # The world's notes and session notes follow too: rewrite_references reads the prose.
    fix = reference_fix(kind, old, new)
    store = registry.gamestate
    for name in RENAMEABLE.values():
        notes = getattr(store, name)
        for other in notes.list():
            other_scope = getattr(other, "scope", None)
            other_world = other_scope.world_id if other_scope else other.world_id
            if other_world == record_world and fix(other.notes) != other.notes:
                notes.update(other.id, notes=fix(other.notes))
    for note in store.session_notes.list(lambda n: n.world_id == record_world):
        if fix(note.text) != note.text:
            store.session_notes.update(note.id, text=fix(note.text))
    return True


# The texts a link may be changed in, one at a time: where each keeps its Markdown.
PROSE_FIELDS: dict[str, str] = {"worlds": "description", "adventures": "body", "chapters": "body"}


def relink_text(
    registry: StoreRegistry, text_id: str, kind: str, old: str, new: str, world_id: str | None
) -> bool:
    """Point the links to ``old`` in one world, adventure or chapter text at ``new``, their
    labels kept. False when there is no such text of ``world_id``, or nothing to change."""
    store = registry.gamestate
    for name, field in PROSE_FIELDS.items():
        collection = getattr(store, name)
        record = collection.find(text_id)
        if record is None:
            continue
        own_world = record.id if name == "worlds" else record.world_id
        text = getattr(record, field)
        fixed = reference_fix(kind, old, new)(text)
        if own_world != world_id or fixed == text:
            return False
        collection.update(text_id, **{field: fixed})
        return True
    return False


@router.post("/gm/editor/rename", status_code=204)
async def rename(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()],
    id: Annotated[str, Form()],  # noqa: A002 — the form field's name
    name: Annotated[str, Form()],
    scope: Annotated[str, Form()],
) -> Response:
    """The editor's check: the object takes the name the text uses (in the editor's world)."""
    if kind not in RENAMEABLE or not name.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    if not rename_object(registry, kind, id, name, _scope(registry, scope).world_id):
        raise HTTPException(status_code=404)
    return Response(status_code=204)


@router.post("/gm/editor/relink")
async def relink(
    _gm: GamemasterDep,
    registry: RegistryDep,
    text: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    old: Annotated[str, Form()],
    new: Annotated[str, Form()],
    back: Annotated[str, Form()] = "",
) -> Response:
    """The create page: change the link in the text it came from to the object meant."""
    if kind not in CREATABLE or not old.strip() or not new.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    relink_text(registry, text, kind, old, new.strip(), registry.table.world_id())
    return redirect(safe_next(back) or "/gm/adventures")


@router.post("/gm/editor/rename-back")
async def rename_back(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()],
    id: Annotated[str, Form()],  # noqa: A002 — the form field's name
    name: Annotated[str, Form()],
    back: Annotated[str, Form()] = "",
) -> Response:
    """The create page: the object takes the name the text uses, then back to the text."""
    if kind not in RENAMEABLE or not name.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    if not rename_object(registry, kind, id, name, registry.table.world_id()):
        raise HTTPException(status_code=404)
    return redirect(safe_next(back) or "/gm/adventures")


def _where(page: Page, registry: StoreRegistry, where: Scope) -> str:
    """The chapter, adventure or world a new object goes into, as the page names it."""
    if where.chapter_id:
        chapter = registry.gamestate.chapters.find(where.chapter_id)
        if chapter is not None:
            return page.t("md.create_in_chapter", name=chapter.title)
    if where.adventure_id:
        adventure = registry.gamestate.adventures.find(where.adventure_id)
        if adventure is not None:
            return page.t("md.create_in_adventure", name=adventure.name)
    world = registry.gamestate.worlds.find(where.world_id)
    return page.t("md.create_in_world", name=world.name if world else "")


@router.get(NEW_OBJECT_URL, response_class=HTMLResponse)
async def ask_new(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: str = "",
    name: str = "",
    scope: str = "",
    back: str = "",
    text: str = "",
) -> Response:
    """A link to something the world lacks: make it where the text is? Something of that
    name found there meanwhile opens at once."""
    if kind not in CREATABLE or not name.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    where = _scope(registry, scope)
    finders = {
        "npc": registry.npcs.find,
        "place": registry.locations.find,
        "treasure": registry.treasure.find,
        "encounter": registry.encounters.find,
    }
    if finders[kind](name, where) is not None:
        return redirect(_make(registry, kind, name, where))
    # What the text probably meant: each to open, to point the link at, or to rename.
    near = [
        (ident, found, _make(registry, kind, found, where))
        for ident, found in meant_objects(registry, where, kind, name)
    ]
    return html(
        new_object_page(
            page,
            kind=kind,
            name=name.strip(),
            scope=scopes.encode(where),
            where=_where(page, registry, where),
            back=safe_next(back) or "/gm/adventures",
            near=near,
            text=text,
        )
    )


@router.post(NEW_OBJECT_URL)
async def make_new(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "",
) -> Response:
    """Make it and open its page, to fill in."""
    if kind not in CREATABLE or not name.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    return redirect(_make(registry, kind, name.strip(), _scope(registry, scope)))


__all__ = ["router"]
