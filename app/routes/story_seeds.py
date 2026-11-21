"""Seeds for a story rolled on the guides' tables (app/services/story_seeds.py).

* ``GET/POST /gm/npcs/{id}/villain``      — an NPC rolled as a villain
* ``GET/POST /gm/adventures/{id}/ideas``  — an adventure's ideas
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Adventure
from app.routes.auth import html, redirect
from app.services.dice import Dice
from app.services.settlement_gen import plays_2024
from app.services.story_seeds import SeedGenerator
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.library_links import adventure_url, npc_url, world_url
from app.ui.seed_pages import seed_lines, seed_page

router = APIRouter(tags=["story seeds"])


def generator(
    registry: StoreRegistry, world_id: str, kind: str, page: Page | None, level: int = 1
) -> SeedGenerator:
    """The world's generator for a villain or an adventure, a 2014 goal's kind named in
    the reader's words."""
    world = registry.adventures.world(world_id)
    t = page.t if page is not None else None
    return SeedGenerator(
        lambda caption, books: registry.book_table(world_id, caption, books),
        Dice(),
        kind=kind,
        new_rules=plays_2024(world.source_set.sources),
        level=level,
        goal_label=(lambda key: t(f"seeds.goal.{key}")) if t is not None else None,
    )


def party_level(registry: StoreRegistry, adventure: Adventure) -> int:
    """The level of the party the adventure is for (its own, or the world's
    characters): their average, 1 with none."""
    chosen = set(adventure.party or [])
    levels = [
        pc.total_level
        for pc in registry.gamestate.characters.of_world(adventure.world_id, drafts=False)
        if not chosen or pc.id in chosen
    ]
    return max(1, min(20, round(sum(levels) / len(levels)))) if levels else 1


async def _rolled(
    request: Request, gen: SeedGenerator
) -> tuple[dict[str, str], frozenset[str], str | None]:
    """The parts as the form sent them, what is ticked, and what it asks: ``roll``
    rolls again what is not ticked, ``reroll:<part>`` one part; ``apply`` is answered by
    the caller (the third value)."""
    form = await request.form()
    parts = gen.parts()
    rolled = {p: str(form.get(f"v_{p}", ""))[:500] for p in parts}
    kept = frozenset(str(k) for k in form.getlist("keep") if str(k) in parts)
    action = str(form.get("action", "roll"))
    if action.startswith("reroll:") and action[7:] in parts:
        part = action[7:]
        rolled[part] = gen.roll_part(part, rolled[part])
    elif action == "apply":
        return rolled, kept, "apply"
    else:
        rolled = gen.roll({p: v for p, v in rolled.items() if p in kept})
        kept = frozenset(rolled)
    return rolled, kept, None


# -- an NPC as a villain --------------------------------------------------------------


def _villain_page(
    page: Page,
    registry: StoreRegistry,
    npc_id: str,
    gen: SeedGenerator,
    rolled: dict[str, str],
    kept: frozenset[str],
) -> HTMLResponse:
    npc = registry.npcs.get(npc_id)
    world = registry.adventures.world(npc.scope.world_id)
    return html(
        seed_page(
            page,
            kind="villain",
            name=npc.name,
            action=f"{npc_url(npc_id)}/villain",
            trail=[(world.name, world_url(world.id)), (npc.name, npc_url(npc_id))],
            rolled=rolled,
            sources={p: gen.source_of(p) for p in rolled},
            kept=kept,
            world_id=world.id,
        )
    )


@router.get("/gm/npcs/{npc_id}/villain", response_class=HTMLResponse)
async def roll_villain(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, npc_id: str
) -> HTMLResponse:
    """An NPC rolled as a villain, every part ticked."""
    npc = registry.npcs.get(npc_id)
    gen = generator(registry, npc.scope.world_id, "villain", page)
    rolled = gen.roll()
    return _villain_page(page, registry, npc_id, gen, rolled, frozenset(rolled))


@router.post("/gm/npcs/{npc_id}/villain", response_class=HTMLResponse)
async def villain(
    request: Request, _gm: GamemasterDep, page: PageDep, registry: RegistryDep, npc_id: str
) -> Response:
    """Roll again, or keep the ticked parts in the NPC's notes."""
    npc = registry.npcs.get(npc_id)
    gen = generator(registry, npc.scope.world_id, "villain", page)
    rolled, kept, action = await _rolled(request, gen)
    if action == "apply":
        text = seed_lines(page, "villain", rolled, kept)
        if text:
            registry.npcs.add_notes(npc_id, text)
        return redirect(npc_url(npc_id))
    return _villain_page(page, registry, npc_id, gen, rolled, kept)


# -- an adventure's ideas -------------------------------------------------------------


def _ideas_page(
    page: Page,
    registry: StoreRegistry,
    adventure: Adventure,
    gen: SeedGenerator,
    level: int,
    rolled: dict[str, str],
    kept: frozenset[str],
) -> HTMLResponse:
    world = registry.adventures.world(adventure.world_id)
    return html(
        seed_page(
            page,
            kind="adventure",
            name=adventure.name,
            action=f"{adventure_url(adventure.id)}/ideas",
            trail=[
                (world.name, world_url(world.id)),
                (adventure.name, adventure_url(adventure.id)),
            ],
            rolled=rolled,
            sources={p: gen.source_of(p) for p in rolled},
            kept=kept,
            world_id=world.id,
            note=page.t("seeds.adventure.level", level=level) if "situation" in rolled else None,
        )
    )


@router.get("/gm/adventures/{adventure_id}/ideas", response_class=HTMLResponse)
async def roll_ideas(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, adventure_id: str
) -> HTMLResponse:
    """An adventure's ideas rolled, every part ticked."""
    adventure = registry.adventures.get(adventure_id)
    level = party_level(registry, adventure)
    gen = generator(registry, adventure.world_id, "adventure", page, level)
    rolled = gen.roll()
    return _ideas_page(page, registry, adventure, gen, level, rolled, frozenset(rolled))


@router.post("/gm/adventures/{adventure_id}/ideas", response_class=HTMLResponse)
async def ideas(
    request: Request, _gm: GamemasterDep, page: PageDep, registry: RegistryDep, adventure_id: str
) -> Response:
    """Roll again, or keep the ticked parts at the end of the adventure's text, in a box
    only the gamemaster reads."""
    adventure = registry.adventures.get(adventure_id)
    level = party_level(registry, adventure)
    gen = generator(registry, adventure.world_id, "adventure", page, level)
    rolled, kept, action = await _rolled(request, gen)
    if action == "apply":
        text = seed_lines(page, "adventure", rolled, kept)
        if text:
            registry.adventures.add_to_body(adventure_id, text)
        return redirect(adventure_url(adventure_id))
    return _ideas_page(page, registry, adventure, gen, level, rolled, kept)
