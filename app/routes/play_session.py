"""The session page (app/services/play_session.py), for the gamemaster.

* ``GET  /gm/session?world=…``         — the briefing, and what happened since the start
* ``POST /gm/session/{world_id}/start`` — begin the day of play
* ``POST /gm/session/{world_id}/end``   — end it, writing the day's session note
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep, WorldsDep
from app.models.gamestate import Scope
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.routes.world_links import world_problems
from app.services.recap import recap, session_combats
from app.ui.play_session_pages import SESSION_URL, happened_lines, session_page, session_url
from app.ui.recap_pages import recap_text
from app.ui.session_pages import note_url

router = APIRouter(tags=["session"])


@router.get(SESSION_URL, response_class=HTMLResponse)
async def show_session(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, registry: RegistryDep, world: str = ""
) -> Response:
    """What to know before play, or what has happened since the session began."""
    chosen = worlds.find_or_current(world)
    if chosen is None:
        return redirect("/gm/worlds")
    sessions = registry.play_sessions
    brief = sessions.brief(chosen.id)
    if chosen.id == registry.table.world_id():
        # What leads nowhere, before the evening rather than during it (Check links).
        checked = await asyncio.to_thread(world_problems, page, registry, chosen.id)
        brief = replace(brief, broken_links=sum(len(c.problems) for c in checked))
    note = brief.last_note
    rendered = (
        paravent_markdown(
            page, registry, Scope(world_id=chosen.id, adventure_id=note.adventure_id), note.text
        )
        if note is not None
        else None
    )
    running = brief.session
    happened = (
        sessions.happened(chosen.id, running.started_at, running.minute)
        if running is not None
        else None
    )
    return html(session_page(page, chosen, brief, rendered, happened))


@router.post(f"{SESSION_URL}/{{world_id}}/start")
async def start_session(
    gm: GamemasterDep, worlds: WorldsDep, registry: RegistryDep, world_id: str
) -> Response:
    """Begin the world's day of play."""
    if worlds.find(world_id) is None:
        return redirect("/gm/worlds")
    user = gm.user
    registry.play_sessions.start(world_id, user.username, user.display_name or user.username)
    return redirect(session_url(world_id))


@router.post(f"{SESSION_URL}/{{world_id}}/end")
async def end_session(
    gm: GamemasterDep, page: PageDep, worlds: WorldsDep, registry: RegistryDep, world_id: str
) -> Response:
    """End the day of play: the session note for today, from the recap of today's fights
    and what else happened since the session began, opened to write on."""
    if worlds.find(world_id) is None:
        return redirect("/gm/worlds")
    sessions = registry.play_sessions
    running = sessions.of(world_id)
    if running is None:
        return redirect(session_url(world_id))
    today = date.today()
    parts: list[str] = []
    fights = session_combats(registry.combats.list(), world_id, today.isoformat())
    if fights:
        told = recap(
            fights, registry.combats, lambda c: registry.protocol_reader.fight(c.id, c.world_id)
        )
        parts.append(recap_text(page, told))
    lines = happened_lines(
        page, world_id, sessions.happened(world_id, running.started_at, running.minute)
    )
    if lines:
        parts.append("\n".join([f"## {page.t('session.note_heading')}", "", *lines]))
    note = registry.session_notes.create(world_id, today, None, "\n\n".join(parts))
    user = gm.user
    sessions.end(world_id, note.id, user.username, user.display_name or user.username)
    return redirect(f"{note_url(note.id)}/edit")


__all__ = ["router"]
