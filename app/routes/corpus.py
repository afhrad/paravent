"""The corpus check (app/services/corpus_check.py), for the gamemaster.

* ``GET  /gm/corpus``        — the 5eTools version and the newest check
* ``POST /gm/corpus/check``  — run a check in the background
* ``GET  /gm/corpus/status`` — whether it still runs; reloads the page once it is done
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect
from app.ui.corpus_pages import CORPUS_URL, corpus_page, status_fragment

router = APIRouter(tags=["corpus"])


@router.get(CORPUS_URL, response_class=HTMLResponse)
async def show(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """Which data this is, and what the newest check found."""
    health = registry.corpus_health
    settings = registry.settings
    return html(
        corpus_page(
            page,
            configured=settings.reference_configured,
            root=str(settings.reference_dir) if settings.reference_configured else "",
            version=health.version(),
            report=health.last(),
            running=health.running,
            failure=health.failure,
        )
    )


@router.post(f"{CORPUS_URL}/check")
async def check(
    _gm: GamemasterDep,
    registry: RegistryDep,
    references: Annotated[str, Form()] = "",
) -> Response:
    """Start a check of the loaded corpus; the page follows its progress."""
    if registry.settings.reference_configured:
        registry.corpus_health.start(references=bool(references))
    return redirect(CORPUS_URL)


@router.get(f"{CORPUS_URL}/status", response_class=HTMLResponse)
async def status(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """The running check's status; once finished, HTMX reloads the page with its report."""
    health = registry.corpus_health
    response = html(status_fragment(page, health.running, health.failure))
    if not health.running:
        response.headers["HX-Refresh"] = "true"
    return response
