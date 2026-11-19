"""Images from a world's folder: ``GET /media/{record_id}/{path}``.

``path`` is relative to the record's folder, exactly as written in its front matter or
Markdown, so ``cover: media/cover.webp`` on an adventure is served as
``/media/<adventure id>/media/cover.webp``. Gamemaster only until the players' journal
decides what they may see (documentation/ADVENTURES.md §1); a file that may not be
served is a 404, whether it is missing or outside the world. ``?fx=torn,fade`` serves it
rendered with those effects instead, made once and kept in the cache (§5.5).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services import image_effects, media
from app.services.media_tags import orientation
from app.services.media_uses import MediaUseError
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.image_pages import IMAGES_URL, Picture, image_browser
from app.ui.library_links import MEDIA_URL, media_url

router = APIRouter(tags=["media"])


@router.get(f"{MEDIA_URL}/{{record_id}}/{{relative:path}}")
async def serve_media(
    _gm: GamemasterDep,
    registry: RegistryDep,
    record_id: str,
    relative: str,
    fx: str = "",
    w: int = 0,
) -> FileResponse:
    """One image, if it lies inside the record's world; with ``fx``, rendered with those
    effects (unknown words leave it as it is); with ``w`` one of the preview widths,
    shrunk to it."""
    path = media.resolve(registry.gamestate.prose, record_id, relative)
    if path is None:
        raise HTTPException(status_code=404)
    effects = image_effects.parse(fx) if fx else None
    width = w if w in image_effects.PREVIEW_WIDTHS else None
    rendered = bool(effects) or width is not None
    if rendered:
        try:
            path = await asyncio.to_thread(
                registry.image_effects.render, path, effects or (), width
            )
        except (OSError, ValueError) as exc:  # a file Pillow cannot read
            raise HTTPException(status_code=404) from exc
    return FileResponse(
        path,
        media_type="image/webp" if rendered else media.media_type(path),
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def _browser(
    page: Page, registry: StoreRegistry, record_id: str, message: str | None = None
) -> HTMLResponse:
    """Every picture of the record's world, grouped by the world, adventure or chapter
    whose folder holds it, each with its path as ``record_id`` writes it, its tags and
    shape; the world's tags with their counts."""
    store = registry.gamestate.prose
    world_folder = store.world_folder(record_id)
    if world_folder is None:
        raise HTTPException(status_code=404)
    world = next(
        (w for w in registry.gamestate.worlds.list() if store.folder(w.id) == world_folder),
        None,
    )
    if world is None:
        raise HTTPException(status_code=404)
    # Each folder by the record it belongs to: the world, an adventure, a chapter.
    names: dict[str, str] = {"": world.name}
    for adventure in registry.adventures.adventures(world.id):
        for record_id_, name in [
            (adventure.id, adventure.name),
            *((c.id, adventure.name) for c in registry.adventures.chapters(adventure.id)),
        ]:
            folder = store.folder(record_id_)
            if folder is not None:
                key = folder.relative_to(world_folder).as_posix()
                names.setdefault("" if key == "." else key, name)
    tags = registry.media_tags.all(record_id)
    uses = registry.media_uses.uses(world.id)
    groups: dict[str, list[Picture]] = {}
    for picture in media.world_images(store, record_id):
        seen = media.seen_from(store, record_id, picture.path)
        if seen is None:
            continue
        label = names.get(picture.folder, picture.folder)
        groups.setdefault(label, []).append(
            Picture(
                seen,
                media_url(world.id, picture.path),
                f"{media_url(world.id, picture.path)}?w={image_effects.PREVIEW_WIDTHS[0]}",
                picture.name,
                tuple(tags.get(picture.path, ())),
                orientation(world_folder / picture.path),
                tuple(uses.get(picture.path, ())),
            )
        )
    vocabulary = sorted(registry.media_tags.vocabulary(record_id).items())
    return html(
        image_browser(
            page,
            list(groups.items()),
            vocabulary,
            tags_url=f"{IMAGES_URL}/{record_id}/tags",
            rename_url=f"{IMAGES_URL}/{record_id}/rename",
            delete_url=f"{IMAGES_URL}/{record_id}/delete",
            message=message,
        )
    )


@router.get(f"{IMAGES_URL}/{{record_id}}", response_class=HTMLResponse)
async def browse_images(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, record_id: str
) -> HTMLResponse:
    """The image browser for a record (app/ui/image_pages.py)."""
    return await asyncio.to_thread(_browser, page, registry, record_id)


@router.post(f"{IMAGES_URL}/{{record_id}}/tags", response_class=HTMLResponse)
async def tag_images(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    record_id: str,
    path: Annotated[list[str] | None, Form()] = None,
    tags: Annotated[str | None, Form()] = None,
    add: Annotated[str, Form()] = "",
    remove: Annotated[str, Form()] = "",
) -> Response:
    """Tag pictures of the world, each ``path`` as ``record_id`` writes it: ``tags``
    replaces one picture's tags (204); ``add`` and ``remove`` change those of several
    (the browser's selection), answered with the browser again."""
    service = registry.media_tags
    paths = [w for w in (service.world_path(record_id, p) for p in path or []) if w]
    if not paths:
        raise HTTPException(status_code=404)
    if tags is not None:
        service.set(record_id, paths[0], [tags])
        return Response(status_code=204)
    service.change(record_id, paths, add=[add], remove=[remove])
    return await asyncio.to_thread(_browser, page, registry, record_id)


def _world_of(registry: StoreRegistry, record_id: str) -> str:
    """The id of the world a record belongs to (the browser is opened from any record)."""
    store = registry.gamestate.prose
    folder = store.world_folder(record_id)
    world = next(
        (w for w in registry.gamestate.worlds.list() if store.folder(w.id) == folder), None
    )
    if folder is None or world is None:
        raise HTTPException(status_code=404)
    return world.id


@router.post(f"{IMAGES_URL}/{{record_id}}/rename", response_class=HTMLResponse)
async def rename_image(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    record_id: str,
    path: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
) -> Response:
    """Give one picture a new name (``path`` as ``record_id`` writes it) and write every
    use of it again; answered with the browser, saying why when refused."""
    world_id = _world_of(registry, record_id)
    where = registry.media_tags.world_path(record_id, path)
    if where is None:
        raise HTTPException(status_code=404)
    message = None
    try:
        await asyncio.to_thread(registry.media_uses.rename, world_id, where, name)
    except MediaUseError as exc:
        message = page.t.error(exc)
    return await asyncio.to_thread(_browser, page, registry, record_id, message)


@router.post(f"{IMAGES_URL}/{{record_id}}/delete", response_class=HTMLResponse)
async def delete_image(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    record_id: str,
    path: Annotated[str, Form()] = "",
) -> Response:
    """Delete one picture nothing uses; answered with the browser, saying why when it is
    still used."""
    world_id = _world_of(registry, record_id)
    where = registry.media_tags.world_path(record_id, path)
    if where is None:
        raise HTTPException(status_code=404)
    message = None
    try:
        await asyncio.to_thread(registry.media_uses.delete, world_id, where)
    except MediaUseError as exc:
        message = page.t.error(exc)
    return await asyncio.to_thread(_browser, page, registry, record_id, message)


__all__ = ["router"]
