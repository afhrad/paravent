"""The gamemaster's player management: create, update and delete player accounts.

Every route requires the gamemaster role. Forms POST and redirect (post/redirect/get),
so a reload never resubmits, and the result is reported on the list through a query
parameter rather than server-side flash state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, PlayersDep
from app.routes.auth import html, redirect
from app.services.auth import AuthError
from app.services.players import PlayerInput
from app.ui.players_pages import (
    PLAYERS_URL,
    player_delete_page,
    player_form_page,
    players_page,
)

router = APIRouter(prefix=PLAYERS_URL, tags=["players"])


def _form_input(
    username: str,
    display_name: str,
    language: str,
    password: str,
    confirm: str,
    active: str | None,
) -> PlayerInput:
    # An unticked checkbox submits nothing, so absence means "off".
    return PlayerInput(
        username=username,
        display_name=display_name,
        language=language,
        password=password,
        confirm=confirm,
        active=active is not None,
    )


@router.get("", response_class=HTMLResponse)
async def list_players(
    _gm: GamemasterDep,
    page: PageDep,
    players: PlayersDep,
    created: str | None = None,
    updated: str | None = None,
    deleted: int | None = None,
) -> HTMLResponse:
    """Every player account."""
    t = page.t
    notice_text: str | None = None
    for player_id, key in ((created, "players.created"), (updated, "players.updated")):
        if player_id:
            found = players.find(player_id)
            if found is not None:
                notice_text = t(key, name=found.display_name or found.username)
    if deleted is not None:
        notice_text = t.plural("players.deleted_kept", deleted) if deleted else t("players.deleted")
    return html(players_page(page, players.summaries(), notice_text=notice_text))


@router.get("/new", response_class=HTMLResponse)
async def new_player(_gm: GamemasterDep, page: PageDep) -> HTMLResponse:
    """The empty form. A new player starts in the gamemaster's current language."""
    blank = PlayerInput(username="", language=page.lang)
    return html(player_form_page(page, player=None, values=blank))


@router.post("", response_class=HTMLResponse)
async def create_player(
    _gm: GamemasterDep,
    page: PageDep,
    players: PlayersDep,
    username: Annotated[str, Form()],
    language: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
) -> Response:
    """Create a player, or show the form again with the reason it was refused."""
    data = _form_input(username, display_name, language, password, confirm, "on")
    try:
        user = players.create(data)
    except AuthError as exc:
        view = player_form_page(page, player=None, values=data, error=page.t.error(exc))
        return html(view, status_code=422)
    return redirect(f"{PLAYERS_URL}?created={user.id}")


@router.get("/{player_id}", response_class=HTMLResponse)
async def edit_player(
    _gm: GamemasterDep, page: PageDep, players: PlayersDep, player_id: str
) -> HTMLResponse:
    """The edit form, filled in."""
    user = players.get(player_id)
    values = PlayerInput(
        username=user.username,
        display_name=user.display_name,
        language=user.language,
        active=user.active,
    )
    return html(player_form_page(page, player=user, values=values))


@router.post("/{player_id}", response_class=HTMLResponse)
async def update_player(
    _gm: GamemasterDep,
    page: PageDep,
    players: PlayersDep,
    player_id: str,
    username: Annotated[str, Form()],
    language: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
    active: Annotated[str | None, Form()] = None,
) -> Response:
    """Save changes, or show the form again with the reason they were refused."""
    user = players.get(player_id)
    data = _form_input(username, display_name, language, password, confirm, active)
    try:
        players.update(player_id, data)
    except AuthError as exc:
        view = player_form_page(page, player=user, values=data, error=page.t.error(exc))
        return html(view, status_code=422)
    return redirect(f"{PLAYERS_URL}?updated={player_id}")


@router.get("/{player_id}/delete", response_class=HTMLResponse)
async def confirm_delete(
    _gm: GamemasterDep, page: PageDep, players: PlayersDep, player_id: str
) -> HTMLResponse:
    """Ask first, and say what happens to the player's characters."""
    user = players.get(player_id)
    return html(player_delete_page(page, user, players.character_count(player_id)))


@router.post("/{player_id}/delete")
async def delete_player(_gm: GamemasterDep, players: PlayersDep, player_id: str) -> Response:
    """Delete the player; their characters stay, unassigned."""
    kept = players.delete(player_id)
    return redirect(f"{PLAYERS_URL}?deleted={kept}")


__all__ = ["router"]
