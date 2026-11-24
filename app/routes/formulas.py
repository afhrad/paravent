"""Magic item formulas given to characters (app/services/magic_crafting.py).

* ``GET  /gm/formulas/form`` — whom a magic item's formula goes to
* ``POST /gm/formulas/give`` — give it to the ticked characters
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.references import ReferenceNotFound
from app.store.registry import StoreRegistry
from app.ui.forms import alert, notice
from app.ui.formula_pages import FORMULAS_URL, craftable, formula_form

router = APIRouter(tags=["formulas"])


def _found(
    registry: StoreRegistry, world_id: str, name: str, source: str
) -> tuple[str, str, list[tuple[str, str, bool]]] | None:
    """A magic item of the world's books: its name, reference and the world's characters
    (with whether each holds its formula); None when the books lack it."""
    if registry.gamestate.worlds.find(world_id) is None or not registry.feature(
        world_id, "downtime"
    ):
        return None
    try:
        entry = registry.references.get("item", source or "-", name, world_id)
    except ReferenceNotFound:
        return None
    if not craftable("item", entry.data):
        return None
    shown = str(entry.data.get("name", name))
    ref = f"{shown}|{entry.data.get('source', source)}"
    party = [
        (pc.id, pc.name or "?", any(f.reference.lower() == ref.lower() for f in pc.formulas))
        for pc in registry.gamestate.characters.of_world(world_id, drafts=False)
    ]
    return shown, ref, party


@router.get(f"{FORMULAS_URL}/form", response_class=HTMLResponse)
async def give_form(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world: str = "",
    name: str = "",
    source: str = "",
) -> HTMLResponse:
    """Whom the formula goes to."""
    found = _found(registry, world, name, source)
    if found is None:
        return html(alert(page.t("crafting.error.unknown")))
    shown, _ref, members = found
    return html(formula_form(page, world, shown, source, members))


@router.post(f"{FORMULAS_URL}/give", response_class=HTMLResponse)
async def give(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> HTMLResponse:
    """Give the formula to the ticked characters."""
    form = await request.form()
    world_id = str(form.get("world", ""))
    name, source = str(form.get("name", "")), str(form.get("source", ""))
    found = _found(registry, world_id, name, source)
    if found is None:
        return html(alert(page.t("crafting.error.unknown")))
    shown, ref, members = found
    known = {cid for cid, _n, holds in members if not holds}
    chosen = list(dict.fromkeys(str(c) for c in form.getlist("character") if str(c) in known))
    if not chosen:
        result = alert(page.t("trap.error.no_one"))
    else:
        user = gm.user
        names = registry.formulas.give(
            world_id, ref, chosen, user.username, user.display_name or user.username
        )
        result = notice(page.t("crafting.given", names=", ".join(names)))
        _shown, _ref, members = _found(registry, world_id, name, source) or (shown, ref, members)
    return html(formula_form(page, world_id, shown, source, members, result=result))
