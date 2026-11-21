"""A *Game Master 5e* backup made into a new world (app/services/gm5_import.py).

* ``GET  /gm/import/gm5``         — the file to upload
* ``POST /gm/import/gm5``         — keep it, and go on to choosing
* ``GET  /gm/import/gm5/{token}`` — a campaign, the books, and what that would make
* ``POST /gm/import/gm5/{token}`` — import it; the table then plays in the new world
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect
from app.services.gm5_backup import Backup, BackupError
from app.services.gm5_import import Gm5Import, Gm5ImportError, Gm5Uploads
from app.store.registry import StoreRegistry
from app.ui.gm5_import_pages import GM5_URL, choose_page, upload_page
from app.ui.nav import WORLD_SECTIONS
from app.ui.world_menu import TRANSFERRED

router = APIRouter(tags=["game master 5e import"])

# The books a 2014 table of the app most likely has, chosen to start with.
DEFAULT_BOOKS = ("PHB", "MM", "DMG")


def _uploads(registry: StoreRegistry) -> Gm5Uploads:
    return Gm5Uploads(registry.settings.library_dir / ".imports")


def _default_books(registry: StoreRegistry) -> list[str]:
    return [code for code in DEFAULT_BOOKS if registry.books.get(code) is not None]


@router.get(GM5_URL, response_class=HTMLResponse)
async def upload_form(_gm: GamemasterDep, page: PageDep, error: str = "") -> HTMLResponse:
    """The backup file to upload."""
    return html(upload_page(page, error if error.startswith("gm5.error.") else None))


@router.post(GM5_URL)
async def upload(request: Request, _gm: GamemasterDep, registry: RegistryDep) -> Response:
    """Keep the uploaded backup, if it reads as one, and choose what to import."""
    async with request.form(max_files=1) as form:
        file = form.get("file")
        if not isinstance(file, UploadFile) or not file.filename:
            return redirect(f"{GM5_URL}?{urlencode({'error': 'gm5.error.no_file'})}")
        data = await file.read()
    try:
        token = await run_in_threadpool(_uploads(registry).save, data)
    except BackupError as exc:
        return redirect(f"{GM5_URL}?{urlencode({'error': exc.key})}")
    return redirect(f"{GM5_URL}/{token}")


@router.get(f"{GM5_URL}/{{token}}", response_class=HTMLResponse)
async def choose(
    token: str,
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    campaign: int = 0,
    name: str = "",
    sources: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """A campaign and the books to match against, and what that would make."""
    try:
        backup: Backup = await run_in_threadpool(_uploads(registry).load, token)
    except Gm5ImportError as exc:
        return redirect(f"{GM5_URL}?{urlencode({'error': exc.key})}")
    chosen = sources if sources is not None else _default_books(registry)
    campaign = min(max(campaign, 0), max(len(backup.campaigns) - 1, 0))
    plan = (
        await run_in_threadpool(Gm5Import(registry).plan, backup, campaign, chosen, page.lang)
        if backup.campaigns
        else None
    )
    return html(
        choose_page(
            page,
            token=token,
            backup=backup,
            catalog=registry.books,
            campaign=campaign,
            sources=chosen,
            name=name,
            plan=plan,
            error=None if backup.campaigns else "gm5.error.empty",
        )
    )


@router.post(f"{GM5_URL}/{{token}}")
async def run(
    token: str,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    campaign: Annotated[int, Form()] = 0,
    name: Annotated[str, Form()] = "",
    sources: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """Import the campaign into a new world, which the table then plays in."""
    uploads = _uploads(registry)
    try:
        backup = await run_in_threadpool(uploads.load, token)
        made = await run_in_threadpool(
            lambda: Gm5Import(registry).run(
                backup, campaign, name=name, sources=sources or [], t=page.t
            )
        )
    except Gm5ImportError as exc:
        return redirect(f"{GM5_URL}?{urlencode({'error': exc.key})}")
    uploads.delete(token)
    registry.table.select(made.world.id)
    registry.places.forget_sections(gm.user.id, WORLD_SECTIONS)
    report = f"gm5:{made.adventures}:{made.chapters}:{made.encounters}:{made.npcs}:{made.designs}"
    return redirect(f"/gm/adventures?{urlencode({TRANSFERRED: report})}")


__all__ = ["router"]
