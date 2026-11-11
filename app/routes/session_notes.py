"""Session notes (documentation/ADVENTURES.md §4b), for the gamemaster.

* ``GET  /gm/worlds/{world_id}/sessions`` — the notes, newest first, and a new one
* ``POST /gm/worlds/{world_id}/sessions`` — a note for a day; it starts from the
  recap of that day's fights, when there were any
* ``GET  /gm/sessions/{id}/edit``, ``POST /gm/sessions/{id}`` — write it
* ``GET``/``POST /gm/sessions/{id}/delete`` — remove it, after asking
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Scope, SessionNote
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services import scope
from app.services.adventures import EditConflict, text_version
from app.services.recap import recap, session_combats
from app.services.session_notes import SessionNoteError, SessionNoteNotFound
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.library_links import confirm_page
from app.ui.recap_pages import recap_text
from app.ui.session_pages import (
    SESSIONS_URL,
    day_text,
    note_url,
    session_form_page,
    sessions_page,
    sessions_url,
)

router = APIRouter(tags=["sessions"])


def _note(registry: StoreRegistry, note_id: str) -> SessionNote:
    try:
        return registry.session_notes.get(note_id)
    except SessionNoteNotFound as exc:
        raise HTTPException(status_code=404) from exc


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _adventures(registry: StoreRegistry, world_id: str) -> list[tuple[str, str]]:
    return [(a.id, a.name) for a in registry.adventures.adventures(world_id)]


def _sessions(
    page: Page,
    registry: StoreRegistry,
    world_id: str,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    world = registry.adventures.world(world_id)
    names = dict(_adventures(registry, world.id))
    notes = [
        (
            note,
            paravent_markdown(
                page, registry, Scope(world_id=world.id, adventure_id=note.adventure_id), note.text
            ),
            names.get(note.adventure_id or ""),
        )
        for note in registry.session_notes.list(world.id)
    ]
    return html(
        sessions_page(
            page,
            world,
            notes,
            _adventures(registry, world.id),
            today=date.today(),
            notice_text=notice_text,
            error=error,
        ),
        status_code=status_code,
    )


@router.get("/gm/worlds/{world_id}/sessions", response_class=HTMLResponse)
async def show_sessions(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world_id: str, deleted: str = ""
) -> HTMLResponse:
    """A world's session notes, newest first."""
    return _sessions(
        page,
        registry,
        world_id,
        notice_text=page.t("sessions.deleted", day=deleted) if deleted else None,
    )


@router.post("/gm/worlds/{world_id}/sessions")
async def create_note(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    day: Annotated[str, Form()] = "",
    adventure: Annotated[str, Form()] = "",
) -> Response:
    """A note for a day, starting from the recap of the fights run that day."""
    when = _day(day)
    if when is None:
        return _sessions(
            page, registry, world_id, error=page.t("sessions.error.day"), status_code=422
        )
    fights = session_combats(registry.combats.list(), world_id, when.isoformat())
    start = ""
    if fights:
        told = recap(
            fights,
            registry.combats,
            lambda c: registry.protocol_reader.fight(c.id, c.world_id),
        )
        start = recap_text(page, told)
    try:
        note = registry.session_notes.create(world_id, when, adventure or None, start)
    except SessionNoteError as exc:
        return _sessions(page, registry, world_id, error=page.t.error(exc), status_code=422)
    return redirect(f"{note_url(note.id)}/edit")


def _form(
    page: Page,
    registry: StoreRegistry,
    note: SessionNote,
    *,
    day: str | None = None,
    adventure_id: str | None = None,
    text: str | None = None,
    base: str | None = None,
    conflict: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    world = registry.adventures.world(note.world_id)
    chosen = note.adventure_id if adventure_id is None else adventure_id
    here = Scope(world_id=world.id, adventure_id=chosen or None)
    return html(
        session_form_page(
            page,
            world,
            note,
            _adventures(registry, world.id),
            day=day if day is not None else note.day.isoformat(),
            adventure_id=chosen,
            text=note.text if text is None else text,
            base=text_version(note.text) if base is None else base,
            scope_value=scope.encode(here),
            conflict=conflict,
            error=error,
        ),
        status_code=status_code,
    )


@router.get(f"{SESSIONS_URL}/{{note_id}}/edit", response_class=HTMLResponse)
async def edit_note(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, note_id: str
) -> HTMLResponse:
    """The note's form."""
    return _form(page, registry, _note(registry, note_id))


@router.post(f"{SESSIONS_URL}/{{note_id}}", response_class=HTMLResponse)
async def save_note(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    note_id: str,
    day: Annotated[str, Form()] = "",
    adventure: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
    base: Annotated[str, Form()] = "",
) -> Response:
    """Save a note; one written from a text changed since is shown beside the saved one."""
    note = _note(registry, note_id)
    when = _day(day)
    if when is None:
        return _form(
            page,
            registry,
            note,
            day=day,
            adventure_id=adventure,
            text=text,
            base=base,
            error=page.t("sessions.error.day"),
            status_code=422,
        )
    try:
        registry.session_notes.update(
            note.id, day=when, adventure_id=adventure or None, text=text, base=base or None
        )
    except (SessionNoteError, EditConflict) as exc:
        conflict = exc.current if isinstance(exc, EditConflict) else None
        return _form(
            page,
            registry,
            note,
            day=day,
            adventure_id=adventure,
            text=text,
            base=text_version(conflict) if conflict is not None else base,
            conflict=conflict,
            error=page.t.error(exc),
            status_code=409 if conflict is not None else 422,
        )
    return redirect(f"{sessions_url(note.world_id)}#note-{note.id}")


@router.get(f"{SESSIONS_URL}/{{note_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_note(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, note_id: str
) -> HTMLResponse:
    """Ask first."""
    note = _note(registry, note_id)
    t = page.t
    return html(
        confirm_page(
            page,
            title=t("sessions.delete"),
            heading=t("sessions.delete_heading", day=day_text(page, note.day)),
            lede=t("sessions.delete_lede"),
            action=f"{note_url(note.id)}/delete",
            back=f"{note_url(note.id)}/edit",
            submit=t("sessions.delete"),
        )
    )


@router.post(f"{SESSIONS_URL}/{{note_id}}/delete")
async def delete_note(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, note_id: str
) -> Response:
    """Delete a note for good."""
    note = registry.session_notes.delete(_note(registry, note_id).id)
    query = urlencode({"deleted": day_text(page, note.day)})
    return redirect(f"{sessions_url(note.world_id)}?{query}")
