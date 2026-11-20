"""Diseases, poisons and curses put on characters (app/services/afflictions.py).

* ``GET  /gm/afflictions/form``            — whom a disease or poison of the books catches
* ``POST /gm/afflictions/afflict``         — put it on the ticked characters
* ``POST /gm/afflictions/{character}/end``   — it ends on the character (cured)
* ``POST /gm/afflictions/{character}/curse`` — a curse of the gamemaster's own
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep, WorldsDep
from app.routes.auth import html
from app.services.afflictions import AfflictionEffect, Victim, read_affliction
from app.services.references import ReferenceNotFound
from app.store.registry import StoreRegistry
from app.ui.affliction_pages import AFFLICTIONS_URL, afflict_form, afflict_result, afflicts
from app.ui.forms import alert

router = APIRouter(tags=["afflictions"])


def _found(
    registry: StoreRegistry, world_id: str, category: str, name: str, source: str
) -> tuple[str, str, str, AfflictionEffect, list[tuple[str, str]]] | None:
    """A disease or poison of the world's books: its kind, name, reference, what it does
    and the world's characters; None when the books lack it."""
    try:
        entry = registry.references.get(category, source or "-", name, world_id)
    except ReferenceNotFound:
        return None
    kind = afflicts(category, entry.data)
    if kind is None:
        return None
    shown = str(entry.data.get("name", name))
    ref = f"{shown}|{entry.data.get('source', source)}"
    party = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.of_world(world_id, drafts=False)
    ]
    return kind, shown, ref, read_affliction(entry.data, kind), party


@router.get(f"{AFFLICTIONS_URL}/form", response_class=HTMLResponse)
async def affliction_form(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
    category: str = "disease",
    name: str = "",
    source: str = "",
) -> HTMLResponse:
    """Whom a disease or poison is put on, and what it does."""
    found = _found(registry, world, category, name, source) if worlds.find(world) else None
    if found is None:
        return html(alert(page.t("affliction.error.unknown")))
    _kind, shown, _ref, effect, members = found
    return html(afflict_form(page, world, category, shown, source, effect, members))


@router.post(f"{AFFLICTIONS_URL}/afflict", response_class=HTMLResponse)
async def afflict(
    request: Request, gm: GamemasterDep, page: PageDep, worlds: WorldsDep, registry: RegistryDep
) -> HTMLResponse:
    """Put it on the ticked characters: each rolls its save on the sheet (``op=save``),
    or has caught it without one (``op=caught``). ``op=ask`` lets the players roll the
    save on their sheets, as for a trap: ``wait`` shows the totals coming in, ``apply``
    puts it on with them (rolling for whoever has not), ``cancel`` takes the call back."""
    form = await request.form()
    world_id = str(form.get("world", ""))
    category, name = str(form.get("category", "")), str(form.get("name", ""))
    source = str(form.get("source", ""))
    found = _found(registry, world_id, category, name, source) if worlds.find(world_id) else None
    if found is None:
        return html(alert(page.t("affliction.error.unknown")))
    kind, shown, ref, effect, members = found
    known = dict(members)
    chosen = list(dict.fromkeys(str(c) for c in form.getlist("character") if str(c) in known))
    op = str(form.get("op", ""))
    user = gm.user
    # Only an open call of this world: a closed one (applied or taken back) never
    # afflicts anyone again.
    call = registry.gamestate.checks.find(str(form.get("call", "")))
    if call is not None and (not call.open or call.world_id != world_id):
        call = None
    totals: dict[str, int] = {}
    if op in ("wait", "apply", "cancel") and call is None:
        return html(afflict_form(page, world_id, category, shown, source, effect, members))
    if op == "wait" and call is not None:
        return html(
            afflict_form(page, world_id, category, shown, source, effect, members, waiting=call)
        )
    if op == "cancel" and call is not None:
        registry.checks.close(call.id)
        return html(afflict_form(page, world_id, category, shown, source, effect, members))
    if op == "apply" and call is not None:
        chosen = [cid for cid in call.characters if cid in known]
        totals = dict(call.results)
        registry.checks.close(call.id)
        op = "save"
    if op == "ask" and chosen and effect.catch is not None:
        asked = registry.checks.call(
            world_id,
            "save",
            effect.catch.ability,
            chosen,
            user.username,
            dc=effect.catch.dc,
            purpose=shown,
        )
        return html(
            afflict_form(page, world_id, category, shown, source, effect, members, waiting=asked)
        )
    if not chosen:
        result = alert(page.t("trap.error.no_one"))
    else:
        victims = []
        for cid in chosen:
            pc = registry.gamestate.characters.find(cid)
            if pc is None:
                continue
            sheet = registry.characters.sheet(pc)
            victims.append(
                Victim(
                    cid,
                    known[cid],
                    resist=tuple(sheet.defended("resist")),
                    immune=tuple(sheet.defended("immune")),
                    vulnerable=tuple(sheet.defended("vulnerable")),
                )
            )
        outcomes = registry.afflictions.afflict(
            world_id,
            kind,
            shown,
            ref,
            effect,
            victims,
            with_save=op == "save",
            by=user.username,
            by_name=user.display_name or user.username,
            totals=totals,
        )
        result = afflict_result(page, shown, outcomes)
    return html(
        afflict_form(page, world_id, category, shown, source, effect, members, result=result)
    )


@router.post(f"{AFFLICTIONS_URL}/{{character_id}}/end")
async def end_affliction(
    request: Request, gm: GamemasterDep, registry: RegistryDep, character_id: str
) -> Response:
    """It ends on the character: cured by a spell, an herb or the gamemaster's word."""
    form = await request.form()
    user = gm.user
    registry.afflictions.end(
        character_id,
        str(form.get("affliction", "")),
        user.username,
        user.display_name or user.username,
    )
    return _back(request, character_id)


@router.post(f"{AFFLICTIONS_URL}/{{character_id}}/curse")
async def put_curse(
    request: Request, gm: GamemasterDep, registry: RegistryDep, character_id: str
) -> Response:
    """A curse of the gamemaster's own on the character."""
    pc = registry.gamestate.characters.find(character_id)
    if pc is not None:
        form = await request.form()
        user = gm.user
        registry.afflictions.curse(
            pc.world_id or "",
            character_id,
            str(form.get("name", "")),
            str(form.get("note", "")),
            user.username,
            user.display_name or user.username,
        )
    return _back(request, character_id)


def _back(request: Request, character_id: str) -> Response:
    """htmx: nothing to swap, the sheet follows the file live; without it, the sheet."""
    if request.headers.get("HX-Request"):
        return Response(status_code=204)
    return Response(status_code=303, headers={"Location": f"/characters/{character_id}"})
