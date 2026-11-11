"""Levelling up (documentation/CHARGEN-WIZARD.md §9).

* ``POST /characters/{id}/grant-level``     — the gamemaster grants one more level
* ``POST /characters/{id}/withdraw-level``  — and can take a grant back
* ``POST /gm/party/grant-level``             — a level for every ready character in a world
* ``GET  /characters/{id}/level-up``         — the level's questions
* ``POST /characters/{id}/level-up``         — save the answers; ``go=take`` takes the level
* ``POST /characters/{id}/level-up/roll-hp`` — roll the new level's hit die

The player answers for their own character, the gamemaster for anyone's; only the
gamemaster grants. Without a granted level the page answers with the sheet.
"""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.routes.auth import html, redirect, safe_next
from app.services.chargen.levelup import LevelUpError
from app.ui.levelup_pages import levelup_form, levelup_page, levelup_url

router = APIRouter(tags=["levelup"])


@router.post("/characters/{character_id}/grant-level")
async def grant_level(
    gm: GamemasterDep,
    registry: RegistryDep,
    character_id: str,
    next: Annotated[str, Form()] = "",  # noqa: A002 — where to return, e.g. a fight's summary
) -> Response:
    """One more level for this character to take (a draft is not levelled)."""
    with contextlib.suppress(LevelUpError):
        registry.levelups.grant(gm, character_id, 1)
    return redirect(safe_next(next) or f"/characters/{character_id}")


@router.post("/characters/{character_id}/withdraw-level")
async def withdraw_level(gm: GamemasterDep, registry: RegistryDep, character_id: str) -> Response:
    """Take a granted level back, before it is taken."""
    with contextlib.suppress(LevelUpError):
        registry.levelups.grant(gm, character_id, -1)
    return redirect(f"/characters/{character_id}")


@router.post("/gm/party/grant-level")
async def grant_party(
    gm: GamemasterDep, registry: RegistryDep, world: Annotated[str, Form()] = ""
) -> Response:
    """A level for everyone at the table — or in the world the overview shows."""
    registry.levelups.grant_world(gm, world or None)
    return redirect(f"/gm?world={world}" if world else "/gm")


@router.get("/characters/{character_id}/level-up", response_class=HTMLResponse)
async def show(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, character_id: str
) -> Response:
    """What the next level brings."""
    levelups = registry.levelups
    try:
        pc, plan = levelups.get(identity, character_id)
    except LevelUpError:
        return redirect(f"/characters/{character_id}")
    world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
    return html(levelup_page(page, pc, plan, levelups.planner(pc).planner, world=world))


@router.post("/characters/{character_id}/level-up", response_class=HTMLResponse)
async def save(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Save the answers; take the level when asked and complete."""
    form = await request.form()
    answers = {key: [str(v) for v in form.getlist(key)] for key in form if key != "go"}
    levelups = registry.levelups
    try:
        pc, plan = levelups.save(identity, character_id, answers)
    except LevelUpError:
        return redirect(f"/characters/{character_id}")
    flash: str | None = None
    if form.get("go") == "take":
        try:
            levelups.take(identity, character_id)
            return redirect(f"/characters/{character_id}")
        except LevelUpError as exc:
            flash = page.t.error(exc)
    world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
    planner = levelups.planner(pc).planner
    if request.headers.get("HX-Request") == "true":
        return html(levelup_form(page, pc, plan, planner, world=world, flash=flash))
    if flash is None:
        return redirect(levelup_url(character_id))
    return html(levelup_page(page, pc, plan, planner, world=world, flash=flash))


@router.post("/characters/{character_id}/level-up/roll-hp", response_class=HTMLResponse)
async def roll_hp(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Roll the new level's hit die: once, unless the world allows rolling again."""
    levelups = registry.levelups
    flash: str | None = None
    try:
        levelups.roll_hit_points(identity, character_id)
    except LevelUpError as exc:
        flash = page.t.error(exc)
    pc, plan = levelups.get(identity, character_id)
    world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
    planner = levelups.planner(pc).planner
    if request.headers.get("HX-Request") == "true":
        return html(levelup_form(page, pc, plan, planner, world=world, flash=flash))
    return redirect(levelup_url(character_id))


__all__ = ["router"]
