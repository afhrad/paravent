"""A world's factions and renown (app/services/factions.py), for the gamemaster.

* ``GET  /gm/factions?world=…``          — the factions and the characters' renown
* ``POST /gm/factions/{world_id}/{op}``  — ``create``, ``from_table``, ``from_example``,
  ``edit``, ``delete``, ``renown``
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep, WorldsDep
from app.models.gamestate import Scope, World
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services.factions import FactionError
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.faction_pages import FACTIONS_URL, factions_page, factions_url

router = APIRouter(tags=["factions"])


def _page(
    page: Page, registry: StoreRegistry, world: World, error: str | None = None
) -> HTMLResponse:
    members = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.of_world(world.id, drafts=False)
    ]
    return html(
        factions_page(
            page,
            world,
            registry.factions.of(world.id),
            sorted(members, key=lambda m: m[1].lower()),
            registry.renown_tables(world.id),
            list(registry.faction_ranks(world.id)),
            error=error,
            render=lambda text: (
                paravent_markdown(page, registry, Scope(world_id=world.id), text).html
            ),
        ),
        status_code=200 if error is None else 422,
    )


@router.get(FACTIONS_URL, response_class=HTMLResponse)
async def show_factions(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, registry: RegistryDep, world: str = ""
) -> Response:
    """A world's factions and renown."""
    chosen = worlds.find_or_current(world)
    if chosen is None:
        return redirect("/gm/worlds")
    return _page(page, registry, chosen)


@router.post(f"{FACTIONS_URL}/{{world_id}}/{{op}}")
async def change_factions(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world_id: str,
    op: str,
) -> Response:
    """Make, change or delete a faction, or a character's renown with it."""
    world = worlds.find(world_id)
    if world is None:
        return redirect("/gm/worlds")
    form = await request.form()
    factions = registry.factions
    faction_id = str(form.get("faction", ""))
    undo = ""  # the trash entry of something deleted, for its Undo
    try:
        if op == "create":
            factions.create(world.id, str(form.get("name", "")))
        elif op == "from_table":
            table = str(form.get("table", ""))
            if table not in {f"{n}|{s}" for n, s in registry.renown_tables(world.id)}:
                raise FactionError("factions.error.unknown")
            name = table.partition("|")[0].removesuffix(" Renown")
            ranks = registry.faction_ranks(world.id).get(name, [])
            factions.create(world.id, name, table=table, ranks=ranks)
        elif op == "from_example":
            name = str(form.get("example", ""))
            examples = registry.faction_ranks(world.id)
            if name not in examples:
                raise FactionError("factions.error.unknown")
            ranks = examples[name]
            # The world's books may print the faction's rewards too.
            table = next(
                (
                    f"{n}|{s}"
                    for n, s in registry.renown_tables(world.id)
                    if n.removesuffix(" Renown") == name
                ),
                "",
            )
            factions.create(world.id, name, table=table, ranks=ranks)
        elif op == "edit":
            factions.edit(
                faction_id, world.id, str(form.get("name", "")), str(form.get("note", ""))
            )
        elif op == "delete":
            undo = factions.delete(faction_id, world.id) or ""
        elif op == "renown":
            pc = registry.gamestate.characters.find(str(form.get("character", "")))
            delta_text = str(form.get("delta", "0"))
            delta = int(delta_text) if delta_text.lstrip("+-").isdigit() else 0
            if pc is None or not -50 <= delta <= 50:
                raise FactionError("factions.error.unknown")
            user = gm.user
            factions.adjust(
                faction_id, world.id, pc, delta, user.username, user.display_name or user.username
            )
        else:
            raise FactionError("factions.error.unknown")
    except FactionError as exc:
        return _page(page, registry, world, page.t.error(exc))
    # After a delete, the page offers Undo (app/ui/world_menu.py, undo_notice).
    return redirect(factions_url(world.id) + (f"&undo={undo}" if undo else ""))


__all__ = ["router"]
