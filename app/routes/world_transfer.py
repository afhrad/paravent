"""A world exported, imported or duplicated from the world menu (documentation/CURRENT-WORLD.md §9).

* ``GET  /gm/world/export``    — the table's world as a zip file
* ``POST /gm/world/import``    — a zip file made into a new world, which the table then plays in
* ``POST /gm/world/duplicate`` — a copy of the table's world, which the table then plays in
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.dependencies import GamemasterDep, RegistryDep
from app.routes.auth import redirect, safe_next
from app.services.world_transfer import WorldTransferError
from app.store.prose import slugify
from app.store.registry import StoreRegistry
from app.ui.nav import WORLD_SECTIONS
from app.ui.world_menu import DUPLICATE_URL, EXPORT_URL, IMPORT_URL, TRANSFER_ERROR

router = APIRouter(tags=["world transfer"])


def _with_error(back: str, key: str) -> str:
    """Back to the page the dialog was opened on, the dialog open again with the error."""
    parts = urlsplit(back)
    query = f"{parts.query}&" if parts.query else ""
    return f"{parts.path}?{query}{urlencode({TRANSFER_ERROR: key})}"


def _play_in(registry: StoreRegistry, user_id: str, world_id: str) -> Response:
    """The table plays in the world just made, starting from its adventures."""
    registry.table.select(world_id)
    registry.places.forget_sections(user_id, WORLD_SECTIONS)
    return redirect("/gm/adventures")


@router.get(EXPORT_URL)
async def export_world(_gm: GamemasterDep, registry: RegistryDep) -> FileResponse:
    """The table's world as a zip file, named after it."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115 — sent, then removed
    path = Path(handle.name)
    try:
        with handle:
            await run_in_threadpool(registry.world_transfer.export_to, world.id, handle)
    except WorldTransferError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=404) from exc
    slug = slugify(world.name, "world")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{slug}.paravent.zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(slug)}.paravent.zip"},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post(IMPORT_URL)
async def import_world(request: Request, _gm: GamemasterDep, registry: RegistryDep) -> Response:
    """A world package made into a new world, the play so far with it if ticked."""
    async with request.form(max_files=1) as form:
        back = safe_next(str(form.get("next", ""))) or "/gm/adventures"
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            return redirect(_with_error(back, "transfer.error.no_file"))
        try:
            made = await run_in_threadpool(
                registry.world_transfer.import_world,
                upload.file,
                name=str(form.get("name", "")),
                play=form.get("play") == "1",
            )
        except WorldTransferError as exc:
            return redirect(_with_error(back, exc.key))
    return _play_in(registry, _gm.user.id, made.world.id)


@router.post(DUPLICATE_URL)
async def duplicate_world(
    _gm: GamemasterDep,
    registry: RegistryDep,
    name: Annotated[str, Form()] = "",
    play: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """A copy of the table's world under a new name, for another group."""
    world = registry.table.world()
    back = safe_next(next) or "/gm/adventures"
    if world is None:
        raise HTTPException(status_code=404)
    if not name.strip():
        return redirect(_with_error(back, "transfer.error.no_name"))
    made = await run_in_threadpool(
        registry.world_transfer.duplicate, world.id, name=name.strip(), play=play == "1"
    )
    return _play_in(registry, _gm.user.id, made.world.id)


__all__ = ["router"]
