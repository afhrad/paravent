"""Giving an item to another character of the party, or dropping it (a sheet item's
context menu, documentation/CONTEXT-MENUS.md §2): a player their own character's, the
gamemaster any.

* ``GET  /characters/{id}/hand?index=…&drop=1`` — to whom and how many (a dialog with
  ``dialog=1``)
* ``POST /characters/{id}/hand``                 — hand it over or drop it, logged
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.dependencies import CharactersDep, IdentityDep, PageDep, RegistryDep
from app.routes.auth import html, redirect, safe_next
from app.services.play import Actor, PlayError
from app.ui.hand_pages import hand_page, hand_url
from app.ui.names import name_text

router = APIRouter(tags=["characters"])


@router.get(hand_url("{character_id}"), response_class=HTMLResponse)
async def hand_form(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
    index: int,
    drop: bool = False,
) -> HTMLResponse:
    """The form: the party to give it to, or dropping it; how many of a stack."""
    pc = characters.get_for(identity, character_id)
    sheet = registry.characters.sheet(pc)
    shown = next((i for i in sheet.inventory if i.index == index), None)
    if shown is None or shown.bound:
        raise HTTPException(status_code=404)
    name = (
        shown.shown_as or page.t("protocol.unidentified_item")
        if shown.unidentified
        else str(name_text(page, ("item", "baseitem"), shown.name.partition("|")[0]))
    )
    party = registry.characters.companions(pc)
    return html(hand_page(page, pc, index, name, party, drop=drop))


@router.post(hand_url("{character_id}"))
async def hand_over(
    identity: IdentityDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
    index: Annotated[int, Form()],
    ref: Annotated[str, Form()],
    count: Annotated[int, Form()] = 1,
    to: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Give the item to ``to`` or, without one, drop it; back to the page asked from."""
    pc = characters.get_for(identity, character_id)
    user = identity.user
    by = Actor(user.display_name or user.username, username=user.username)
    sheet = f"/characters/{pc.id}"
    try:
        registry.characters.hand(pc.id, index, count, ref, to or None, by)
    except PlayError as exc:
        return redirect(f"{sheet}?{urlencode({'flash': exc.key})}")
    return redirect(safe_next(next) or sheet)


__all__ = ["router"]
