"""Copying or moving pieces of a world into another, or into a file and back
(app/services/world_copy.py).

* ``GET  /gm/copy?kind=&id=…&mode=copy|move|file[&target=&bring=1]`` — the choice and the plan
* ``POST /gm/copy`` — copied or moved
* ``GET  /gm/copy/file?kind=&id=…[&bring=1]`` — the pieces as a file to take along
* ``GET  /gm/copy/import``, ``POST`` — such a file read into the table's world
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import UploadFile

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.world_copy import DESIGNS, KINDS, SUFFIX, WorldCopyError
from app.store.prose import slugify
from app.ui.copy_links import COPY_URL, FILE_URL, IMPORT_URL
from app.ui.world_copy_pages import copied_page, copy_page, import_page, piece_url

router = APIRouter(tags=["world copy"])

# The largest file an import reads (the world import's limit for a whole world).
MAX_UPLOAD = 512 * 1024 * 1024


def _name(registry: RegistryDep, kind: str, ids: list[str]) -> str:
    record = registry.world_copy.find(kind, ids[0]) if ids else None
    name = str(getattr(record, "name", None) or getattr(record, "title", "") or "")
    return name if len(ids) == 1 else f"{name} +{len(ids) - 1}"


@router.get(COPY_URL, response_class=HTMLResponse)
async def choose(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: str = "",
    id: Annotated[list[str] | None, Query()] = None,  # noqa: A002 — the query's name
    mode: str = "copy",
    target: str = "",
    bring: str = "",
    checked: str = "",
) -> HTMLResponse:
    """Which world, and what the copy would hold there (the first other world at first);
    for a file, only what goes into it."""
    copier = registry.world_copy
    ids = list(dict.fromkeys(id or []))
    if kind not in KINDS:
        raise HTTPException(status_code=404)
    mode = mode if mode in ("copy", "move", "file") else "copy"
    if not ids:  # Select… pressed with nothing ticked
        return html(
            copy_page(
                page,
                kind=kind,
                ids=[],
                name="",
                mode=mode,
                worlds=[],
                plan=None,
                error=page.t("copy.error.none"),
            )
        )
    error = None
    plan = None
    worlds = copier.targets(kind, ids)
    # The tick is on until the form says otherwise (an unticked box sends nothing).
    keep = bool(bring) or not checked
    try:
        bundle = copier.bundle(kind, ids)
        chosen = None if mode == "file" else (target or (worlds[0].id if worlds else None))
        if mode == "file" or chosen:
            plan = copier.plan(bundle, chosen, bring=keep, moving=mode == "move")
    except WorldCopyError as exc:
        if exc.key == "copy.error.no_piece":
            raise HTTPException(status_code=404) from exc
        error = page.t.error(exc)
    return html(
        copy_page(
            page,
            kind=kind,
            ids=ids,
            name=_name(registry, kind, ids),
            mode=mode,
            worlds=worlds,
            plan=plan,
            bring=keep,
            error=error,
        )
    )


@router.post(COPY_URL, response_class=HTMLResponse)
async def make(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    id: Annotated[list[str] | None, Form()] = None,  # noqa: A002 — the form field's name
    mode: Annotated[str, Form()] = "copy",
    target: Annotated[str, Form()] = "",
    bring: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Copy or move them; the page then leads to the copy."""
    copier = registry.world_copy
    ids = list(dict.fromkeys(id or []))
    if kind not in KINDS or not ids:
        raise HTTPException(status_code=404)
    back = piece_url(kind, ids[0])
    try:
        if mode == "move":
            done = copier.move(kind, ids, target, bring=bool(bring))
            if kind in DESIGNS and registry.restamp_homebrew():
                registry.reload_compendium()  # its homebrew entry follows it to its world
        else:
            done = copier.copy(kind, ids, target, bring=bool(bring))
    except WorldCopyError as exc:
        return html(
            copy_page(
                page,
                kind=kind,
                ids=ids,
                name=_name(registry, kind, ids),
                mode=mode,
                worlds=copier.targets(kind, ids),
                plan=None,
                bring=bool(bring),
                error=page.t.error(exc),
            )
        )
    return html(copied_page(page, done, back))


@router.get(FILE_URL)
async def save(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: str = "",
    id: Annotated[list[str] | None, Query()] = None,  # noqa: A002 — the query's name
    bring: str = "",
) -> Response:
    """The pieces as one file, named after the first."""
    copier = registry.world_copy
    ids = list(dict.fromkeys(id or []))
    if kind not in KINDS or not ids:
        raise HTTPException(status_code=404)
    try:
        bundle = copier.bundle(kind, ids)
    except WorldCopyError as exc:
        raise HTTPException(status_code=404) from exc
    data = copier.saved(bundle, bring=bool(bring))
    first = bundle.mains[0].name if bundle.mains else kind
    filename = f"{slugify(first, kind)}{SUFFIX}"
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get(IMPORT_URL, response_class=HTMLResponse)
async def import_form(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """A file of pieces to read into the table's world."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    return html(import_page(page, world))


@router.post(IMPORT_URL, response_class=HTMLResponse)
async def import_file(
    request: Request, _gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> HTMLResponse:
    """Read the file's pieces into the table's world: what came, what took another name,
    what the world had already, what its books lack."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    form = await request.form(max_part_size=MAX_UPLOAD)
    upload = form.get("file")
    bring = bool(form.get("bring"))
    if not isinstance(upload, UploadFile):
        return html(import_page(page, world, error=page.t("copy.error.no_file")))
    copier = registry.world_copy
    try:
        bundle = copier.read(await upload.read())
        done = copier.write(bundle, world.id, bring=bring)
    except WorldCopyError as exc:
        return html(import_page(page, world, error=page.t.error(exc)))
    return html(copied_page(page, done, "/gm/adventures", imported=True))
