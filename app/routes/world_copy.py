"""Copying or moving one piece of a world into another (app/services/world_copy.py).

* ``GET  /gm/copy?kind=&id=&mode=copy|move[&target=&bring=1]`` — the choice and the plan
* ``POST /gm/copy`` — made
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.world_copy import KINDS, WorldCopyError
from app.ui.copy_links import COPY_URL
from app.ui.world_copy_pages import copied_page, copy_page, piece_url

router = APIRouter(tags=["world copy"])


@router.get(COPY_URL, response_class=HTMLResponse)
async def choose(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: str,
    id: str,  # noqa: A002 — the query's name
    mode: str = "copy",
    target: str = "",
    bring: str = "",
    checked: str = "",
) -> HTMLResponse:
    """Which world, and what the copy would hold there (the first other world at first)."""
    copier = registry.world_copy
    record = copier.find(kind, id) if kind in KINDS else None
    if record is None:
        raise HTTPException(status_code=404)
    worlds = copier.targets(kind, id)
    chosen = target or (worlds[0].id if worlds else "")
    plan = None
    error = None
    if chosen:
        try:
            # The tick is on until the form says otherwise (an unticked box sends nothing).
            plan = copier.plan(kind, id, chosen, bring=bool(bring) or not checked)
        except WorldCopyError as exc:
            error = page.t.error(exc)
    name = str(getattr(record, "name", None) or getattr(record, "title", ""))
    return html(
        copy_page(
            page,
            kind=kind,
            record_id=id,
            name=name,
            mode="move" if mode == "move" else "copy",
            worlds=worlds,
            plan=plan,
            error=error,
        )
    )


@router.post(COPY_URL, response_class=HTMLResponse)
async def make(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    id: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
    mode: Annotated[str, Form()] = "copy",
    target: Annotated[str, Form()] = "",
    bring: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Copy or move it; the page then leads to the copy."""
    copier = registry.world_copy
    if kind not in KINDS or copier.find(kind, id) is None:
        raise HTTPException(status_code=404)
    back = piece_url(kind, id)
    try:
        if mode == "move":
            done = copier.move(kind, id, target, bring=bool(bring))
        else:
            done = copier.copy(kind, id, target, bring=bool(bring))
    except WorldCopyError as exc:
        return html(
            copy_page(
                page,
                kind=kind,
                record_id=id,
                name="",
                mode=mode,
                worlds=copier.targets(kind, id),
                plan=None,
                error=page.t.error(exc),
            )
        )
    return html(copied_page(page, done, back))
