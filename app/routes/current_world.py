"""The current world's lists at addresses of their own (documentation/CURRENT-WORLD.md §5).

``/gm/adventures``, ``/gm/npcs``, ``/gm/places``, ``/gm/treasure`` and ``/gm/sessions`` show
the table's world, so the top bar links there whatever world is chosen. Each draws the page
the world's own address draws; a table with no world yet is asked to make one.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes import adventures, cast, locations, session_notes
from app.routes.auth import html
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.home_pages import empty_state
from app.ui.layout import app_shell
from app.ui.world_menu import no_world_button

router = APIRouter(tags=["current world"])


def _no_world(page: Page) -> HTMLResponse:
    """What a world's page says before there is a world."""
    t = page.t
    return html(
        app_shell(
            page,
            t("world.missing_heading"),
            empty_state(
                "globe", t("world.missing_heading"), t("world.missing_text"), no_world_button(page)
            ),
        )
    )


def _world(registry: StoreRegistry) -> str | None:
    return registry.table.world_id()


@router.get("/gm/adventures", response_class=HTMLResponse)
async def current_adventures(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> HTMLResponse:
    """The current world's adventures (its page)."""
    world_id = _world(registry)
    if world_id is None:
        return _no_world(page)
    return adventures._world(page, registry, world_id, notice_text=None)


@router.get("/gm/npcs", response_class=HTMLResponse)
async def current_npcs(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, new: str = "", place: str = ""
) -> HTMLResponse:
    """The current world's NPCs."""
    world_id = _world(registry)
    if world_id is None:
        return _no_world(page)
    return cast._npcs(page, registry, world_id, selected=place, new=bool(new))


@router.get("/gm/places", response_class=HTMLResponse)
async def current_places(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, new: str = "", place: str = ""
) -> HTMLResponse:
    """The current world's places."""
    world_id = _world(registry)
    if world_id is None:
        return _no_world(page)
    return locations._locations(page, registry, world_id, selected=place, new=bool(new))


@router.get("/gm/treasure", response_class=HTMLResponse)
async def current_treasure(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, new: str = "", place: str = ""
) -> HTMLResponse:
    """The current world's treasure."""
    world_id = _world(registry)
    if world_id is None:
        return _no_world(page)
    return cast._treasures(page, registry, world_id, selected=place, new=bool(new))


@router.get("/gm/sessions", response_class=HTMLResponse)
async def current_sessions(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> HTMLResponse:
    """The current world's session notes."""
    world_id = _world(registry)
    if world_id is None:
        return _no_world(page)
    return session_notes._sessions(page, registry, world_id)


__all__ = ["router"]
