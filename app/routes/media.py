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

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.dependencies import GamemasterDep, RegistryDep
from app.services import image_effects, media
from app.ui.library_links import MEDIA_URL

router = APIRouter(tags=["media"])


@router.get(f"{MEDIA_URL}/{{record_id}}/{{relative:path}}")
async def serve_media(
    _gm: GamemasterDep, registry: RegistryDep, record_id: str, relative: str, fx: str = ""
) -> FileResponse:
    """One image, if it lies inside the record's world; with ``fx``, rendered with those
    effects (unknown words leave it as it is)."""
    path = media.resolve(registry.gamestate.prose, record_id, relative)
    if path is None:
        raise HTTPException(status_code=404)
    effects = image_effects.parse(fx) if fx else None
    if effects:
        try:
            path = await asyncio.to_thread(registry.image_effects.render, path, effects)
        except (OSError, ValueError) as exc:  # a file Pillow cannot read
            raise HTTPException(status_code=404) from exc
    return FileResponse(
        path,
        media_type="image/webp" if effects else media.media_type(path),
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


__all__ = ["router"]
