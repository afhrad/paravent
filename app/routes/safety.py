"""Safety tools at the table (app/services/safety.py).

* ``GET  /safety``                 — the reader's worlds: lines, veils, suggestions, X-card
* ``POST /safety/xcard``           — the X-card: the gamemaster is told, with no name
* ``POST /safety/{world}``         — the gamemaster's lines and veils
* ``POST /safety/{world}/suggest`` — a player suggests a line or veil, without a name
* ``POST /safety/{world}/settle``  — the gamemaster takes or drops a suggestion
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.models.gamestate import World
from app.routes.auth import html, redirect
from app.services import features
from app.services.auth import Identity
from app.store.registry import StoreRegistry
from app.ui.safety_pages import SAFETY_URL, safety_page, xcard_sent

router = APIRouter(tags=["safety"])


def _worlds(identity: Identity, registry: StoreRegistry) -> list[World]:
    """The gamemaster's every world; a player's characters' worlds."""
    worlds = registry.gamestate.worlds.list()
    # Only the worlds that play with the safety tools (app/services/features.py).
    worlds = [w for w in worlds if features.on(w, "safety")]
    if identity.is_gamemaster:
        return worlds
    mine = {pc.world_id for pc in registry.characters.for_player(identity) if pc.world_id}
    return [w for w in worlds if w.id in mine]


@router.get(SAFETY_URL, response_class=HTMLResponse)
async def safety(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, saved: str = ""
) -> HTMLResponse:
    """The table's agreements in the reader's worlds."""
    worlds = [(w, registry.safety.of(w.id)) for w in _worlds(identity, registry)]
    message = page.t("safety.saved") if saved else None
    return html(safety_page(page, worlds, gamemaster=identity.is_gamemaster, message=message))


@router.post(f"{SAFETY_URL}/xcard", response_class=HTMLResponse)
async def xcard(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    world: Annotated[str, Form()] = "",
) -> Response:
    """Someone tapped the X-card: the gamemaster is told, and no one else."""
    allowed = {w.id for w in _worlds(identity, registry)}
    registry.safety.xcard(world if world in allowed else None)
    if request.headers.get("HX-Request") == "true":
        return html(xcard_sent(page))
    return redirect(SAFETY_URL)


@router.post(f"{SAFETY_URL}/{{world_id}}")
async def save_lists(
    _gm: GamemasterDep,
    registry: RegistryDep,
    world_id: str,
    lines: Annotated[str, Form()] = "",
    veils: Annotated[str, Form()] = "",
) -> Response:
    """The gamemaster's lines and veils, one per line of the box."""
    if registry.gamestate.worlds.find(world_id) is None:
        raise HTTPException(status_code=404)
    registry.safety.set_lists(world_id, lines.splitlines(), veils.splitlines())
    return redirect(f"{SAFETY_URL}?saved=1")


@router.post(f"{SAFETY_URL}/{{world_id}}/suggest")
async def suggest(
    identity: IdentityDep,
    registry: RegistryDep,
    world_id: str,
    kind: Annotated[str, Form()] = "line",
    text: Annotated[str, Form()] = "",
) -> Response:
    """A player puts a line or veil forward; no name goes with it."""
    if world_id not in {w.id for w in _worlds(identity, registry)}:
        raise HTTPException(status_code=404)
    registry.safety.suggest(world_id, kind, text)
    return redirect(f"{SAFETY_URL}?saved=1")


@router.post(f"{SAFETY_URL}/{{world_id}}/settle")
async def settle(
    _gm: GamemasterDep,
    registry: RegistryDep,
    world_id: str,
    index: Annotated[int, Form()] = -1,
    take: Annotated[str, Form()] = "0",
) -> Response:
    """The gamemaster takes a suggestion into its list, or drops it."""
    registry.safety.settle(world_id, index, take == "1")
    return redirect(SAFETY_URL)
