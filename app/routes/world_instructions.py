"""A world's instructions for Claude (documentation/MCP.md §5), for the gamemaster.

* ``GET  /gm/worlds/{world_id}/claude`` — the setting and one text per kind
* ``POST /gm/worlds/{world_id}/claude`` — save them
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect
from app.services.world_instructions import FIELDS, InstructionsError
from app.ui.instructions_pages import instructions_page, instructions_url

router = APIRouter(tags=["instructions"])


@router.get("/gm/worlds/{world_id}/claude", response_class=HTMLResponse)
async def show_instructions(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world_id: str, saved: str = ""
) -> HTMLResponse:
    """The world's instructions for Claude."""
    world = registry.worlds.get(world_id)
    return html(
        instructions_page(
            page,
            world,
            registry.instructions.get(world.id),
            notice_text=page.t("instructions.saved") if saved else None,
        )
    )


@router.post("/gm/worlds/{world_id}/claude", response_class=HTMLResponse)
async def save_instructions(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world_id: str, request: Request
) -> Response:
    """Save every field at once."""
    world = registry.worlds.get(world_id)
    form = await request.form()
    texts = {key: str(form.get(key) or "") for key in FIELDS}
    try:
        registry.instructions.save(world.id, texts)
    except InstructionsError as exc:
        return html(
            instructions_page(
                page, world, registry.instructions.get(world.id), error=page.t.error(exc)
            ),
            status_code=422,
        )
    return redirect(f"{instructions_url(world.id)}?saved=1")
