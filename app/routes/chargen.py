"""The character creation wizard (documentation/CHARGEN-WIZARD.md §6).

* ``GET  /chargen/new``            — pick a world (and, for the gamemaster, a player)
* ``POST /chargen``                — start a draft, then go to its first step
* ``GET  /chargen/{id}``           — resume where the draft was left
* ``GET  /chargen/{id}/{step}``    — one step
* ``POST /chargen/{id}/finish``     — a complete draft becomes a character; its sheet opens
* ``POST /chargen/{id}/abilities/roll`` — roll the six scores (4d6, drop the lowest)
* ``POST /chargen/{id}/features/roll-hp`` — roll hit points for the levels after the first
* ``POST /chargen/{id}/equipment/roll-gold`` — roll the class's starting gold
* ``POST /chargen/{id}/{step}``    — save the step's answers. From htmx (a change)
  the form comes back with the consequence shown; "Continue" goes to the next step once
  this one is done; "Save" (without JavaScript) stays.

A player works on their own drafts; the gamemaster on anyone's. A finished character
is not a draft and answers 404 here — its sheet is where it lives.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.models.gamestate import Role
from app.routes.auth import html, redirect
from app.services.chargen.backstory import CHARACTERISTICS, Physique
from app.services.chargen.drafts import ADJUST, TEXT_FIELDS, TRADE, DraftError
from app.services.naming import GENDERS
from app.services.translations import record_key
from app.ui.chargen_pages import CHARGEN_URL, new_character_page, wizard, wizard_page, wizard_url

router = APIRouter(tags=["chargen"])


@router.get(f"{CHARGEN_URL}/new", response_class=HTMLResponse)
async def new_character(
    identity: IdentityDep, page: PageDep, registry: RegistryDep
) -> HTMLResponse:
    """Where a character starts: its world decides which books it is made from."""
    players = None
    if identity.role is Role.GAMEMASTER:
        players = [s.user.username for s in registry.players.summaries()]
    return html(new_character_page(page, registry.worlds.list(), players))


@router.post(CHARGEN_URL)
async def start_character(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    world: Annotated[str, Form()] = "",
    player: Annotated[str, Form()] = "",
) -> Response:
    """A new draft, opened at its first step."""
    try:
        draft = registry.drafts.start(identity, world or None, player or None)
    except DraftError as exc:
        players = None
        if identity.role is Role.GAMEMASTER:
            players = [s.user.username for s in registry.players.summaries()]
        return html(
            new_character_page(page, registry.worlds.list(), players, error=page.t.error(exc)),
            status_code=400,
        )
    return redirect(wizard_url(draft.id))


@router.get(f"{CHARGEN_URL}/{{character_id}}")
async def resume(identity: IdentityDep, registry: RegistryDep, character_id: str) -> Response:
    """Back to the step the draft was left at."""
    drafts = registry.drafts
    pc = drafts.get(identity, character_id)
    return redirect(wizard_url(pc.id, drafts.resume_at(pc, drafts.plan(pc))))


@router.get(f"{CHARGEN_URL}/{{character_id}}/{{step}}", response_class=HTMLResponse)
async def show_step(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, character_id: str, step: str
) -> Response:
    """One step of the wizard."""
    drafts = registry.drafts
    pc = drafts.get(identity, character_id)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if plan.step(step) is None:
        return redirect(wizard_url(pc.id, drafts.resume_at(pc, plan)))
    world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
    return html(wizard_page(page, pc, plan, step, planner, world=world))


@router.post(f"{CHARGEN_URL}/{{character_id}}/finish")
async def finish(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, character_id: str
) -> Response:
    """Finish a complete draft and open its sheet."""
    drafts = registry.drafts
    try:
        pc = drafts.finish(identity, character_id)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        planner = drafts.planner(pc)
        plan = planner.plan(pc)
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(
            wizard_page(page, pc, plan, "details", planner, world=world, flash=page.t.error(exc)),
            status_code=400,
        )
    return redirect(f"/characters/{pc.id}")


@router.post(f"{CHARGEN_URL}/{{character_id}}/abilities/roll", response_class=HTMLResponse)
async def roll_abilities(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Roll the six scores: once, unless the world allows rolling again."""
    drafts = registry.drafts
    flash: str | None = None
    try:
        pc = drafts.roll_abilities(identity, character_id)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        flash = page.t.error(exc)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if request.headers.get("HX-Request") == "true":
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(wizard(page, pc, plan, "abilities", planner, world=world, flash=flash))
    return redirect(wizard_url(pc.id, "abilities"))


@router.post(f"{CHARGEN_URL}/{{character_id}}/details/physique", response_class=HTMLResponse)
async def roll_physique(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Roll height and weight — what was typed on the step is kept first."""
    form = await request.form()
    fields = {key: str(form.get(key, "")) for key in form if key in TEXT_FIELDS}
    drafts = registry.drafts

    def line(rolled: Physique) -> str:
        feet, inches = divmod(rolled.inches, 12)
        height = (
            page.u.distance(Decimal(rolled.inches) / 12) if page.u.metric else f"{feet}'{inches}\""
        )
        return page.t("chargen.physique.line", height=height, weight=page.u.weight(rolled.pounds))

    flash: str | None = None
    try:
        drafts.save(identity, character_id, "details", {}, fields)
        pc = drafts.roll_physique(identity, character_id, line)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        flash = page.t.error(exc)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if request.headers.get("HX-Request") == "true":
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(wizard(page, pc, plan, "details", planner, world=world, flash=flash))
    return redirect(wizard_url(pc.id, "details"))


@router.post(f"{CHARGEN_URL}/{{character_id}}/details/backstory", response_class=HTMLResponse)
async def roll_backstory(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Roll on a backstory table — what was typed on the step is kept first."""
    form = await request.form()
    fields = {key: str(form.get(key, "")) for key in form if key in TEXT_FIELDS}
    drafts = registry.drafts
    flash: str | None = None
    try:
        drafts.save(identity, character_id, "details", {}, fields)
        labels = {c: page.t(f"chargen.backstory.{c}") for c in CHARACTERISTICS.values()}

        def translate(name: str, source: str, data: Mapping[str, Any]) -> Mapping[str, Any]:
            key = record_key(name, source)
            return registry.translations.overlay(page.lang, "background", key, dict(data)).data

        table = str(form.get("table", ""))
        pc = drafts.roll_backstory(identity, character_id, table, labels, translate)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        flash = page.t.error(exc)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if request.headers.get("HX-Request") == "true":
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(wizard(page, pc, plan, "details", planner, world=world, flash=flash))
    return redirect(wizard_url(pc.id, "details") + "#cg-backstory")


@router.post(f"{CHARGEN_URL}/{{character_id}}/details/name", response_class=HTMLResponse)
async def suggest_name(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Suggest a name in the world's style — what was typed on the step is kept first."""
    form = await request.form()
    fields = {key: str(form.get(key, "")) for key in form if key in TEXT_FIELDS}
    gender = str(form.get("name_gender", "any"))
    drafts = registry.drafts
    flash: str | None = None
    try:
        drafts.save(identity, character_id, "details", {}, fields)
        pc = drafts.suggest_name(
            identity,
            character_id,
            gender if gender in GENDERS else "any",
            str(form.get("name_style", "")),
            str(form.get("name_parent", "")),
        )
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        flash = page.t.error(exc)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if request.headers.get("HX-Request") == "true":
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(wizard(page, pc, plan, "details", planner, world=world, flash=flash))
    return redirect(wizard_url(pc.id, "details"))


@router.post(f"{CHARGEN_URL}/{{character_id}}/features/roll-hp", response_class=HTMLResponse)
async def roll_hit_points(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Roll the hit die for every level after the first: once, unless rerolls are allowed."""
    drafts = registry.drafts
    flash: str | None = None
    try:
        pc = drafts.roll_hit_points(identity, character_id)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        flash = page.t.error(exc)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if request.headers.get("HX-Request") == "true":
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(wizard(page, pc, plan, "features", planner, world=world, flash=flash))
    return redirect(wizard_url(pc.id, "features"))


@router.post(f"{CHARGEN_URL}/{{character_id}}/equipment/roll-gold", response_class=HTMLResponse)
async def roll_gold(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Roll the class's starting gold: once, unless the world allows rolling again."""
    drafts = registry.drafts
    flash: str | None = None
    try:
        pc = drafts.roll_gold(identity, character_id)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        flash = page.t.error(exc)
    planner = drafts.planner(pc)
    plan = planner.plan(pc)
    if request.headers.get("HX-Request") == "true":
        world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
        return html(wizard(page, pc, plan, "equipment", planner, world=world, flash=flash))
    return redirect(wizard_url(pc.id, "equipment"))


@router.post(f"{CHARGEN_URL}/{{character_id}}/{{step}}", response_class=HTMLResponse)
async def save_step(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
    step: str,
) -> Response:
    """Save a step; then show it again, or go on."""
    form = await request.form()
    answers = {key: [str(v) for v in form.getlist(key)] for key in form}
    fields = {
        key: str(form.get(key, ""))
        for key in form
        if key in TEXT_FIELDS or key.startswith("score.") or key in (ADJUST, TRADE)
    }
    go = str(form.get("go", ""))
    drafts = registry.drafts
    flash: str | None = None
    try:
        pc, plan = drafts.save(identity, character_id, step, answers, fields)
    except DraftError as exc:
        pc = drafts.get(identity, character_id)
        plan = drafts.plan(pc)
        flash = page.t.error(exc)
    current = plan.step(step)
    if go == "next" and current is not None and current.state == "done":
        following = plan.after(step)
        if following is not None:
            return redirect(wizard_url(pc.id, following))
    planner = drafts.planner(pc)
    world = registry.gamestate.worlds.find(pc.world_id) if pc.world_id else None
    if request.headers.get("HX-Request") == "true":
        return html(wizard(page, pc, plan, step, planner, world=world, flash=flash))
    if go == "stay" and flash is None:
        return redirect(wizard_url(pc.id, step))
    return html(wizard_page(page, pc, plan, step, planner, world=world, flash=flash))


__all__ = ["router"]
