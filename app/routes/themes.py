"""Themes (documentation/THEMES.md).

* ``GET  /themes``                — every theme; a reader picks their own, the gamemaster
  the table's
* ``POST /themes``                — a reader's own choice
* ``POST /themes/table``          — the table's theme, for the gamemaster
* ``GET  /themes/{id}/{path}``    — a theme's stylesheets, fonts and pictures, public like
  the login page they style
"""

from __future__ import annotations

from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.dependencies import GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.routes.auth import html, redirect, safe_next
from app.services.themes import MEDIA_TYPES
from app.ui.theme_pages import THEMES_URL, themes_page

router = APIRouter(tags=["themes"])

# A theme's file is never a page: an SVG opened on its own runs no script.
FILE_HEADERS = {
    "Cache-Control": "public, max-age=86400",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; sandbox",
}


@router.get(THEMES_URL, response_class=HTMLResponse)
async def show(_identity: IdentityDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """Every theme, the reader's choice and the table's."""
    service = registry.themes
    return html(themes_page(page, service.library.all(), table=service.table_choice()))


@router.post(THEMES_URL)
async def choose_own(
    identity: IdentityDep,
    registry: RegistryDep,
    theme: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> Response:
    """The reader's own theme: one by id, ``paravent`` for Paravent's own, or "" for the
    table's. An unknown id changes nothing."""
    with suppress(KeyError):
        registry.themes.set_user(identity.user.id, theme)
    return redirect(safe_next(next) or THEMES_URL)


@router.post(f"{THEMES_URL}/table")
async def choose_table(
    _gm: GamemasterDep, registry: RegistryDep, theme: Annotated[str, Form()] = ""
) -> Response:
    """The theme everyone reads in unless they chose their own; "" for Paravent's own."""
    try:
        registry.themes.set_table(theme)
    except KeyError as exc:
        raise HTTPException(404) from exc
    return redirect(THEMES_URL)


@router.get(THEMES_URL + "/{theme_id}/{path:path}", include_in_schema=False)
async def theme_file(registry: RegistryDep, theme_id: str, path: str) -> FileResponse:
    """One file of a theme: inside its folder and of a kind a theme hands out."""
    theme = registry.themes.library.get(theme_id)
    file = theme.file(path) if theme is not None else None
    if file is None:
        raise HTTPException(404)
    return FileResponse(file, media_type=MEDIA_TYPES[file.suffix.lower()], headers=FILE_HEADERS)


__all__ = ["router"]
