"""The log on screen (documentation/PROTOCOL.md §5).

* ``GET  /log``          — the log as a page (what the top-bar button opens without
  JavaScript)
* ``GET  /log/panel``    — the panel's fragment: filters, entries, "older"
* ``GET  /log/badge``    — how many entries arrived since a moment, for the badge
* ``GET  /log/events``   — Server-Sent Events: a revision on every append
* ``POST /log/roll``     — a dice chip outside a sheet, rolled and logged
* ``POST /log/reveal``   — the gamemaster shows an entry to the table in full
* ``GET  /log/toasts``   — what pops up since a moment, as toast cards
* ``POST /log/toasts``   — which entries pop up for this reader (mine, all, off)

Every one reads through :class:`~app.services.protocol_view.ProtocolReader`, so what a
signed-in user receives is only what they may read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.dependencies import GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.routes.auth import html, redirect, safe_next
from app.services.dice import DiceError, is_expression
from app.services.events import PROTOCOL, revision_stream
from app.services.protocol_view import Filter, Viewer
from app.ui.layout import app_shell
from app.ui.protocol_pages import (
    log_embed,
    log_page_content,
    log_panel,
    log_rows,
    toast_cards,
)

router = APIRouter(tags=["log"])

_KINDS = frozenset({"all", "me", "fight", "character", "monsters"})
# How many entries a slice on a page shows; the rest is one tap away in the panel.
EMBED_SIZE = 15


class RollResult(BaseModel):
    """A dice chip's roll, as the page shows it."""

    total: int
    detail: str
    outcome: str


def _filter(viewer: Viewer, kind: str, combat: str | None, character: str | None) -> Filter:
    """The filter asked for, within what the viewer may ask: a player narrows to their
    own characters only, and the gamemaster has no "me"."""
    if kind not in _KINDS:
        kind = "all"
    if character is not None and not viewer.gamemaster and character not in viewer.characters:
        character = None
    if kind == "character" and character is None:
        kind = "all"
    if kind == "fight" and not combat:
        kind = "all"
    if kind == "me" and viewer.gamemaster:
        kind = "all"
    return Filter(kind, combat=combat or None, character=character)


@router.get("/log", response_class=HTMLResponse)
async def log_page(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    filter: str = "all",
    combat: str | None = None,
    character: str | None = None,
    before: str | None = None,
) -> HTMLResponse:
    """The log as a page of its own."""
    viewer = registry.viewer(identity)
    chosen = _filter(viewer, filter, combat, character)
    result = registry.protocol_reader.page(viewer, chosen, before=before)
    return html(
        app_shell(
            page,
            page.t("protocol.title"),
            log_page_content(page, result, chosen, gamemaster=viewer.gamemaster),
            log_scope=chosen,
        )
    )


@router.get("/log/panel", response_class=HTMLResponse)
async def log_panel_fragment(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    filter: str = "all",
    combat: str | None = None,
    character: str | None = None,
    before: str | None = None,
    embed: bool = False,
) -> HTMLResponse:
    """The panel's content; with ``before``, only the older rows to append; with
    ``embed``, the newest few for a slice on a page (the sheet's log, the tracker's)."""
    viewer = registry.viewer(identity)
    chosen = _filter(viewer, filter, combat, character)
    if embed:
        result = registry.protocol_reader.page(viewer, chosen, limit=EMBED_SIZE)
        return html(log_embed(page, result, chosen))
    result = registry.protocol_reader.page(viewer, chosen, before=before)
    if before is not None:
        return html(log_rows(page, result, chosen))
    return html(log_panel(page, result, chosen, gamemaster=viewer.gamemaster))


@router.get("/log/badge", response_class=HTMLResponse)
async def log_badge(
    identity: IdentityDep,
    registry: RegistryDep,
    since: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """How many entries the reader may see arrived after ``since`` (an ISO moment),
    as a bare number; every entry when ``since`` is empty or unreadable."""
    try:
        moment = datetime.fromisoformat(since) if since else None
    except ValueError:
        moment = None
    count = registry.protocol_reader.since(registry.viewer(identity), moment)
    return HTMLResponse(str(count))


@router.get("/log/toasts", response_class=HTMLResponse)
async def log_toasts(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    since: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """The toasts for entries after ``since`` (an ISO moment), at most three, by the
    reader's setting; nothing without a readable moment, so a page never pops up the
    whole evening at once."""
    try:
        moment = datetime.fromisoformat(since)
    except ValueError:
        return HTMLResponse("")
    viewer = registry.viewer(identity)
    shown = registry.protocol_reader.toasts(viewer, moment, identity.user.toasts)
    return html(toast_cards(page, shown))


@router.post("/log/toasts")
async def log_toast_setting(
    identity: IdentityDep,
    registry: RegistryDep,
    toasts: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    """Which entries pop up for this reader; an unknown value changes nothing."""
    if toasts in ("mine", "all", "off"):
        registry.auth.set_toasts(identity.user.id, toasts)
    return redirect(safe_next(next) or "/")


@router.get("/log/events")
async def log_events(
    request: Request, identity: IdentityDep, registry: RegistryDep
) -> StreamingResponse:
    """A revision on every append to any log; the page then asks for what it may see."""
    return StreamingResponse(
        revision_stream(registry.events, PROTOCOL, None, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/log/roll")
async def log_roll(
    identity: IdentityDep,
    registry: RegistryDep,
    expr: Annotated[str, Form()],
    label: Annotated[str, Form()] = "",
    world: Annotated[str, Form()] = "",
) -> RollResult:
    """A dice chip outside a sheet: rolled here, logged privately in the page's world
    (one the reader plays in; the gamemaster's any), or globally."""
    expression = expr.replace(" ", "")
    if not is_expression(expression):
        raise HTTPException(status_code=422, detail="not a dice expression")
    viewer = registry.viewer(identity)
    world_id = world or None
    if world_id is not None and not viewer.gamemaster and world_id not in viewer.worlds:
        world_id = None
    user = identity.user
    try:
        entry = registry.protocol.free_roll(
            expression,
            username=user.username,
            name=user.display_name or user.username,
            label=" ".join(label.split())[:80],
            world_id=world_id,
            gamemaster=viewer.gamemaster,
        )
    except DiceError as exc:
        raise HTTPException(status_code=422, detail="not a dice expression") from exc
    roll = entry.roll
    if roll is None:  # free_roll always records its roll
        raise HTTPException(status_code=500, detail="roll not recorded")
    parts = [f"{len(d.results)}d{d.faces}[{','.join(map(str, d.results))}]" for d in roll.dice]
    parts += [f"{'+' if b.value >= 0 else '−'} {abs(b.value)}" for b in roll.bonuses]
    return RollResult(total=roll.total, detail=" ".join(parts), outcome=entry.outcome)


@router.post("/log/reveal")
async def log_reveal(
    gm: GamemasterDep,
    registry: RegistryDep,
    entry: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    """Reveal an entry: from now on the players read it as the gamemaster does — a
    hidden roll, a private one, a monster's dice, a DC. Appended, never edited (§3)."""
    found = registry.protocol_reader.find(entry)
    if found is None:
        raise HTTPException(status_code=404, detail="no such entry")
    user = gm.user
    registry.protocol.reveal(found, username=user.username, name=user.display_name or user.username)
    return redirect(safe_next(next) or "/log")


__all__ = ["RollResult", "router"]
