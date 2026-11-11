"""Preparing spells (documentation/CHARGEN-WIZARD.md §10).

* ``GET  /characters/{id}/prepare`` — what may be prepared, and what is
* ``POST /characters/{id}/prepare`` — prepare the ticked spells; ``go=done`` returns to
  the sheet

The player prepares for their own character, the gamemaster for anyone's. A character
that prepares nothing after a long rest is sent back to the sheet.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.models.pc import PREPARED_PREFIX
from app.routes.auth import html, redirect
from app.services.chargen.prepare import PrepareError
from app.ui.prepare_pages import prepare_form, prepare_page

router = APIRouter(tags=["prepare"])


@router.get("/characters/{character_id}/prepare", response_class=HTMLResponse)
async def show(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, character_id: str
) -> Response:
    """The spells to prepare from."""
    try:
        pc, prep = registry.preparations.get(identity, character_id)
    except PrepareError:
        return redirect(f"/characters/{character_id}")
    planner = registry.drafts.planner(pc)
    return html(prepare_page(page, pc, prep, planner))


@router.post("/characters/{character_id}/prepare", response_class=HTMLResponse)
async def save(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Prepare the ticked spells."""
    form = await request.form()
    # One field per preparing class, "prepared.<Class>"; an empty class sends nothing.
    chosen = {
        key.removeprefix(PREPARED_PREFIX): [str(v) for v in form.getlist(key)]
        for key in form
        if key.startswith(PREPARED_PREFIX)
    }
    preparations = registry.preparations
    flash: str | None = None
    try:
        pc, prep = preparations.save(identity, character_id, chosen)
    except PrepareError as exc:
        try:
            pc, prep = preparations.get(identity, character_id)
        except PrepareError:
            return redirect(f"/characters/{character_id}")
        flash = page.t.error(exc)
    if flash is None and form.get("go") == "done":
        return redirect(f"/characters/{character_id}")
    planner = registry.drafts.planner(pc)
    if request.headers.get("HX-Request") == "true":
        return html(prepare_form(page, pc, prep, planner, flash=flash))
    return html(prepare_page(page, pc, prep, planner, flash=flash))


__all__ = ["router"]
