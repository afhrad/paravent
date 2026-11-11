"""The sound desk (documentation/SOUND.md), the gamemaster's.

* ``GET  /gm/sound``              — the speaker page, left open by the loudspeaker
* ``GET  /gm/sound/panel``        — the slide-over's pads
* ``GET  /gm/sound/events``       — the state, live (``?speaker=1`` from a speaker)
* ``GET  /gm/sound/file/{id}``    — a sound's file
* ``POST /gm/sound/press/{id}``   — a pad; ``/scene/{id}`` a scene; ``/trigger`` by name
* ``POST /gm/sound/volume``, ``/master``, ``/stop``
* ``POST /gm/sound/scenes``       — save the mix; ``/scenes/{id}/delete``
* ``GET|POST /gm/sounds``         — the library and uploads; ``/gm/sounds/{id}`` edits
"""

from __future__ import annotations

from typing import Annotated, get_args
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import SoundKind
from app.routes.auth import html, redirect
from app.services.sound import AUDIO_TYPES, SoundError
from app.store.errors import EntryNotFound
from app.ui.sound_pages import sound_delete_page, sounds_page, speaker_page
from app.ui.sound_panel import SOUND_URL, SOUNDS_URL, sound_panel_body

router = APIRouter(tags=["sound"])

# A form may carry a large folder of sounds.
MAX_FILES = 5000


def _kind(value: str) -> SoundKind:
    return value if value in get_args(SoundKind) else "effect"  # type: ignore[return-value]


@router.get(SOUND_URL, response_class=HTMLResponse)
async def speaker(_gm: GamemasterDep, page: PageDep) -> HTMLResponse:
    """The page that plays what the desk says."""
    return html(speaker_page(page))


@router.get(f"{SOUND_URL}/panel", response_class=HTMLResponse)
async def panel(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """The slide-over's pads."""
    library = registry.sounds
    return html(sound_panel_body(page, library.all(), library.all_scenes()))


@router.get(f"{SOUND_URL}/events")
async def events(
    request: Request, _gm: GamemasterDep, registry: RegistryDep, speaker: str = ""
) -> StreamingResponse:
    """The desk's state on every change."""
    return StreamingResponse(
        registry.sound_desk.stream(speaker=bool(speaker), is_disconnected=request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(f"{SOUND_URL}/file/{{sound_id}}")
async def sound_file(_gm: GamemasterDep, registry: RegistryDep, sound_id: str) -> FileResponse:
    """A sound's file; ranges are served, so a long track streams."""
    sound = registry.sounds.sounds.find(sound_id)
    path = registry.sounds.path(sound) if sound is not None else None
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix.lower()))


@router.post(f"{SOUND_URL}/press/{{sound_id}}", status_code=204)
async def press(_gm: GamemasterDep, registry: RegistryDep, sound_id: str) -> None:
    """A pad was pressed."""
    registry.sound_desk.press(sound_id)


@router.post(f"{SOUND_URL}/scene/{{scene_id}}", status_code=204)
async def scene(_gm: GamemasterDep, registry: RegistryDep, scene_id: str) -> None:
    """A scene replaces what is on."""
    registry.sound_desk.scene(scene_id)


@router.post(f"{SOUND_URL}/trigger", status_code=204)
async def trigger(
    _gm: GamemasterDep, registry: RegistryDep, name: Annotated[str, Form()] = ""
) -> None:
    """A ``{@sound Name}`` in a text."""
    if not registry.sound_desk.trigger(name):
        raise HTTPException(status_code=404)


@router.post(f"{SOUND_URL}/volume", status_code=204)
async def volume(
    _gm: GamemasterDep,
    registry: RegistryDep,
    id: Annotated[str, Form()],
    value: Annotated[float, Form()],
) -> None:
    """One layer's volume."""
    registry.sound_desk.volume(id, value)


@router.post(f"{SOUND_URL}/master", status_code=204)
async def master(
    _gm: GamemasterDep, registry: RegistryDep, value: Annotated[float, Form()]
) -> None:
    """The volume of everything."""
    registry.sound_desk.set_master(value)


@router.post(f"{SOUND_URL}/stop", status_code=204)
async def stop(_gm: GamemasterDep, registry: RegistryDep) -> None:
    """Silence."""
    registry.sound_desk.stop()


@router.post(f"{SOUND_URL}/scenes", status_code=204)
async def save_scene(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, name: Annotated[str, Form()] = ""
) -> Response:
    """Save what is on as a scene; a refusal's reason comes back as text."""
    try:
        registry.sound_desk.save_scene(name)
    except SoundError as exc:
        return Response(page.t.error(exc), status_code=422, media_type="text/plain")
    return Response(status_code=204)


@router.post(f"{SOUND_URL}/scenes/{{scene_id}}/delete", status_code=204)
async def delete_scene(_gm: GamemasterDep, registry: RegistryDep, scene_id: str) -> None:
    """Forget a scene; what plays keeps playing."""
    registry.sounds.scenes.delete(scene_id)


@router.get(SOUNDS_URL, response_class=HTMLResponse)
async def library(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    added: int = 0,
    skipped: int = 0,
) -> HTMLResponse:
    """Every sound, and the upload form."""
    message = None
    if added or skipped:
        message = page.t("sound.uploaded", added=added, skipped=skipped)
    return html(sounds_page(page, registry.sounds.all(), notice_text=message))


@router.post(SOUNDS_URL)
async def upload(request: Request, _gm: GamemasterDep, registry: RegistryDep) -> Response:
    """Store the files sent; what is not audio is counted, not stored. A folder sends
    everything in it, so one stray file must not refuse the rest."""
    added = skipped = 0
    async with request.form(max_files=MAX_FILES) as form:
        kind = _kind(str(form.get("kind", "")))
        credit = str(form.get("credit", "")).strip()
        for part in form.getlist("files"):
            if not isinstance(part, UploadFile) or not part.filename:
                continue
            try:
                registry.sounds.add(part.filename, part.file, kind, credit=credit)
                added += 1
            except SoundError:
                skipped += 1
    return redirect(f"{SOUNDS_URL}?" + urlencode({"added": added, "skipped": skipped}))


@router.post(f"{SOUNDS_URL}/{{sound_id}}")
async def edit(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    sound_id: str,
    name: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "effect",
    volume: Annotated[float, Form()] = 1.0,
    credit: Annotated[str, Form()] = "",
) -> Response:
    """Rename a sound, or change how it plays."""
    try:
        registry.sounds.edit(sound_id, name=name, kind=_kind(kind), volume=volume, credit=credit)
    except SoundError as exc:
        return html(
            sounds_page(page, registry.sounds.all(), error=page.t.error(exc)), status_code=422
        )
    return redirect(SOUNDS_URL)


@router.get(f"{SOUNDS_URL}/{{sound_id}}/delete", response_class=HTMLResponse)
async def ask_delete(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, sound_id: str
) -> HTMLResponse:
    """Ask first."""
    try:
        return html(sound_delete_page(page, registry.sounds.sounds.get(sound_id)))
    except EntryNotFound as exc:
        raise HTTPException(status_code=404) from exc


@router.post(f"{SOUNDS_URL}/{{sound_id}}/delete")
async def delete(_gm: GamemasterDep, registry: RegistryDep, sound_id: str) -> Response:
    """Delete a sound and its file; it stops if it was playing."""
    registry.sounds.delete(sound_id)
    registry.sound_desk.forget(sound_id)
    return redirect(SOUNDS_URL)


__all__ = ["router"]
