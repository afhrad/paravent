"""What the Markdown editors do besides saving: create an NPC, encounter or treasure a
chapter names, from the drop-down (static/js/complete.js).

* ``POST /gm/editor/create`` — ``kind``, ``name``, ``scope`` (the editor's record,
  ``c:<chapter id>``, ``a:<adventure id>``, ``w:<world id>``); answers 201 with the new
  record's page in ``Location``
* ``GET  /gm/editor/new``    — a link to something the world lacks, clicked: asks whether
  to make it where the text is
* ``POST /gm/editor/new``    — makes it (or finds it, made meanwhile) and opens its page
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Scope
from app.routes.auth import html, redirect, safe_next
from app.services import scope as scopes
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
    return html(
        new_object_page(
            page,
            kind=kind,
            name=name.strip(),
            scope=scopes.encode(where),
            where=_where(page, registry, where),
            back=safe_next(back) or "/gm/adventures",
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
