"""Backups (app/services/backup.py), for the gamemaster.

* ``GET  /gm/backups``     — where they go, when the next is due, the ones kept
* ``POST /gm/backups/run`` — make one now
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect
from app.services.backup import BackupError
from app.ui.backup_pages import BACKUPS_URL, backup_page

router = APIRouter(tags=["backups"])


@router.get(BACKUPS_URL, response_class=HTMLResponse)
async def show(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, made: str = "", failed: str = ""
) -> HTMLResponse:
    """The backup folder, the schedule and the backups kept."""
    try:
        service = registry.backups
    except BackupError as exc:
        return html(
            backup_page(
                page, target="", times=(), keep=0, next_due=None, backups=(), error=str(exc)
            )
        )
    return html(
        backup_page(
            page,
            target=str(service.target or ""),
            times=[at.strftime("%H:%M") for at in service.times],
            keep=service.keep,
            next_due=service.next_due(),
            backups=service.backups(),
            message=page.t("backup.made", name=made) if made else None,
            error=page.t("backup.failed", error=failed) if failed else None,
        )
    )


@router.post(f"{BACKUPS_URL}/run")
async def run(_gm: GamemasterDep, registry: RegistryDep) -> Response:
    """Back the library up now, whether or not anything changed."""
    try:
        backup = await asyncio.to_thread(registry.backups.run)
    except (BackupError, OSError) as exc:
        return redirect(f"{BACKUPS_URL}?{urlencode({'failed': str(exc)})}")
    return redirect(f"{BACKUPS_URL}?{urlencode({'made': backup.path.name if backup else ''})}")
