"""Supernatural gifts given to characters (app/services/gifts.py).

* ``GET  /gm/gifts/form``             — whom a blessing, charm or boon of the books goes to
* ``POST /gm/gifts/grant``            — give it to the ticked characters
* ``POST /characters/{id}/gifts``     — spend a use (``op=use``) or, for the gamemaster,
  take it back (``op=end``)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import CharactersDep, GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.gifts import GiftError, GiftReading, read_gift
from app.services.references import ReferenceNotFound
from app.store.registry import StoreRegistry
from app.ui.forms import alert
from app.ui.gift_pages import GIFTS_URL, grant_form, grant_result

router = APIRouter(tags=["gifts"])


def _found(
    registry: StoreRegistry, world_id: str, name: str, source: str
) -> tuple[str, str, str, GiftReading, list[tuple[str, str]]] | None:
    """A gift of the world's books: its name, reference, kind, what its text says and the
    world's characters; None when the books lack it."""
    if registry.gamestate.worlds.find(world_id) is None:
        return None
    try:
        entry = registry.references.get("reward", source or "-", name, world_id)
    except ReferenceNotFound:
        return None
    shown = str(entry.data.get("name", name))
    ref = f"{shown}|{entry.data.get('source', source)}"
    party = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.of_world(world_id, drafts=False)
    ]
    kind = str(entry.data.get("type") or "")
    return shown, ref, kind, read_gift(entry.data), party


@router.get(f"{GIFTS_URL}/form", response_class=HTMLResponse)
async def gift_form(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world: str = "",
    name: str = "",
    source: str = "",
) -> HTMLResponse:
    """Whom a gift is given to, with its uses and days."""
    found = _found(registry, world, name, source)
    if found is None:
        return html(alert(page.t("gift.error.unknown")))
    shown, _ref, _kind, reading, members = found
    return html(grant_form(page, world, shown, source, reading, members))


def _number(raw: object, high: int) -> int | None:
    text = str(raw or "").strip()
    return min(high, int(text)) if text.isdigit() and int(text) > 0 else None


@router.post(f"{GIFTS_URL}/grant", response_class=HTMLResponse)
async def grant(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> HTMLResponse:
    """Give it to the ticked characters, with the uses and days the form says."""
    form = await request.form()
    world_id = str(form.get("world", ""))
    name, source = str(form.get("name", "")), str(form.get("source", ""))
    found = _found(registry, world_id, name, source)
    if found is None:
        return html(alert(page.t("gift.error.unknown")))
    shown, ref, kind, reading, members = found
    known = dict(members)
    chosen = list(dict.fromkeys(str(c) for c in form.getlist("character") if str(c) in known))
    days, hours = _number(form.get("days"), 3650), _number(form.get("hours"), 87_600)
    given = GiftReading(
        uses=_number(form.get("uses"), 99),
        lasts=days * 1440 if days else hours * 60 if hours else None,
    )
    if not chosen:
        result = alert(page.t("trap.error.no_one"))
    else:
        user = gm.user
        names = registry.gifts.grant(
            world_id,
            ref,
            kind,
            given,
            chosen,
            user.username,
            user.display_name or user.username,
        )
        result = grant_result(page, page.t.term("reward", shown) or shown, names)
    return html(grant_form(page, world_id, shown, source, given, members, result=result))


@router.post("/characters/{character_id}/gifts")
async def change_gift(
    request: Request,
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Spend a use of a gift (its holder or the gamemaster), or take it back (the
    gamemaster)."""
    form = await request.form()
    # Read after the form: a second press waiting on its form sees the first's result.
    character = characters.get_for(identity, character_id)
    op, gift_id = str(form.get("op", "")), str(form.get("gift", ""))
    user = identity.user
    name = user.display_name or user.username
    try:
        if op == "use":
            registry.gifts.use(character.id, gift_id, user.username, name)
        elif op == "end" and identity.is_gamemaster:
            registry.gifts.end(character.id, gift_id, user.username, name)
        else:
            return Response(status_code=403)
    except GiftError:
        return Response(status_code=409)
    if request.headers.get("HX-Request"):
        return Response(status_code=204)
    return Response(status_code=303, headers={"Location": f"/characters/{character_id}"})
