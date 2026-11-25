"""Safety tools at the table (app/services/safety.py).

* ``GET  /safety``                 — the reader's worlds: lines, veils, suggestions, X-card
* ``POST /safety/xcard``           — the X-card: the gamemaster is told, with no name
* ``POST /safety/{world}``         — the gamemaster's lines and veils
* ``POST /safety/{world}/suggest`` — a player suggests a line or veil, without a name
* ``POST /safety/{world}/settle``  — the gamemaster takes or drops a suggestion
* ``POST /safety/{world}/zero``    — the gamemaster's half of session zero
* ``POST /safety/{world}/sheet``   — a player's expectations sheet, without a name
* ``POST /safety/{world}/adopt``   — a limited element made a line or a veil
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.models.gamestate import TableSafety, World
from app.routes.auth import html, redirect
from app.services import features
from app.services.auth import Identity, signing_key
from app.services.character_rules import RULES as CHARACTER_RULES
from app.services.combat_rules import RULES as COMBAT_RULES
from app.services.safety import TOGETHER_HEADINGS, sheet_token
from app.store.registry import StoreRegistry
from app.ui.safety_pages import SAFETY_URL, WorldZero, safety_page, xcard_sent

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
    zero = {w.id: _zero(identity, registry, w, safety) for w, safety in worlds}
    message = page.t("safety.saved") if saved else None
    return html(
        safety_page(page, worlds, gamemaster=identity.is_gamemaster, message=message, zero=zero)
    )


def _token(identity: Identity, registry: StoreRegistry, world_id: str) -> str:
    return sheet_token(signing_key(registry.settings), world_id, identity.user.username)


def _zero(
    identity: Identity, registry: StoreRegistry, world: World, safety: TableSafety
) -> WorldZero:
    """What the session zero panel needs of a world beyond its record: the reader's own
    sheet, the optional rules it plays with, and where the guide prints its parts."""
    rules = [
        r
        for r in (*COMBAT_RULES, *CHARACTER_RULES)
        if r in (*world.combat_rules, *world.character_rules)
    ]
    if world.milestone:
        rules.insert(0, "milestone")
    links = {
        key: url
        for key, heading in (
            ("zero", "Session Zero"),
            ("expectations", "Setting Expectations"),
            ("limits", "Hard and Soft Limits"),
            *TOGETHER_HEADINGS.items(),
        )
        if (url := registry.heading_url(world.id, (("XDMG", heading),))) is not None
    }
    return WorldZero(
        mine=safety.sheets.get(_token(identity, registry, world.id)),
        rules=rules,
        links=links,
    )


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


@router.post(f"{SAFETY_URL}/{{world_id}}/zero")
async def save_zero(
    request: Request, _gm: GamemasterDep, registry: RegistryDep, world_id: str
) -> Response:
    """The gamemaster's half of session zero: theme, sensitive elements (one a line),
    the starting location and key events, house rules, how the party came together and
    the starting level."""
    if registry.gamestate.worlds.find(world_id) is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    values: dict[str, object] = {
        key: str(form.get(key, ""))
        for key in ("theme", "start", "house_rules", "together", "together_note", "starting_level")
    }
    values["sensitive"] = str(form.get("sensitive", "")).splitlines()
    registry.safety.set_zero(world_id, values)
    return redirect(f"{SAFETY_URL}?saved=1")


@router.post(f"{SAFETY_URL}/{{world_id}}/sheet")
async def fill_sheet(
    request: Request, identity: IdentityDep, registry: RegistryDep, world_id: str
) -> Response:
    """A player's expectations sheet: kept under an anonymous token, written again
    whenever they change it."""
    if world_id not in {w.id for w in _worlds(identity, registry)}:
        raise HTTPException(status_code=404)
    form = await request.form()
    elements = registry.safety.of(world_id).sensitive
    limits = {e: str(form.get(f"limit_{n}", "")) for n, e in enumerate(elements)}
    registry.safety.fill(
        world_id,
        _token(identity, registry, world_id),
        limits,
        str(form.get("avoid", "")),
        str(form.get("hopes", "")),
        str(form.get("concerns", "")),
    )
    return redirect(f"{SAFETY_URL}?saved=1")


@router.post(f"{SAFETY_URL}/{{world_id}}/adopt")
async def adopt(
    _gm: GamemasterDep,
    registry: RegistryDep,
    world_id: str,
    element: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "line",
) -> Response:
    """A sensitive element the sheets limit, made one of the world's lines or veils."""
    registry.safety.adopt(world_id, element, kind)
    return redirect(SAFETY_URL)
