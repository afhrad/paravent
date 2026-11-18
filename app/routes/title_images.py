"""Title images of places, encounters, quests and factions (app/services/title_images.py).

* ``POST /gm/title-image/{kind}/{ident}`` — an upload becomes the title image, or
  ``do=remove`` takes it away; back where it was set
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from app.dependencies import GamemasterDep, RegistryDep
from app.routes.auth import redirect, safe_next
from app.services.media import MAX_BYTES, MediaError
from app.services.title_images import KINDS, TitleImageError
from app.ui.title_images import TITLE_URL

router = APIRouter(tags=["title images"])


def _with(url: str, problem: str) -> str:
    path, _, anchor = url.partition("#")
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}{urlencode({'title_error': problem})}" + (f"#{anchor}" if anchor else "")


@router.post(f"{TITLE_URL}/{{kind}}/{{ident}}")
async def set_title_image(
    request: Request, _gm: GamemasterDep, registry: RegistryDep, kind: str, ident: str
) -> Response:
    """Make an upload the element's title image, or take it away."""
    images = registry.title_images
    if kind not in KINDS or images.owner(kind, ident) is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    back = safe_next(str(form.get("next") or "")) or "/gm"
    if form.get("do") == "remove":
        images.clear(kind, ident)
        return redirect(back)
    upload = form.get("image")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return redirect(_with(back, "none"))
    data = await upload.read(MAX_BYTES + 1)
    try:
        images.set(kind, ident, upload.filename, data)
    except MediaError:
        return redirect(_with(back, "image"))
    except TitleImageError:
        return redirect(_with(back, "owner"))
    return redirect(back)
