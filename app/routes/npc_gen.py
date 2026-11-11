"""The NPC generator (app/services/npc_gen.py).

* ``GET  /gm/npcs/{id}/generate``          — a fresh roll for this NPC
* ``POST /gm/npcs/{id}/generate``          — ``action``: ``roll`` (everything again),
  ``reroll:<element>`` (one part), ``apply`` (write what is ticked into the NPC)
* ``POST /gm/worlds/{id}/npcs/generate``   — a new NPC with a rolled name, at ``place``,
  opened in the generator

What was rolled comes back in ``v_<element>`` fields, so a reroll keeps the rest.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import GivenName, Npc
from app.routes.auth import html, redirect
from app.services import scope
from app.services.i18n import Translatable
from app.services.naming import MadeName
from app.services.npc_gen import GENDERS, GeneratedNpc, NpcGenerator, edition_for
from app.store.registry import StoreRegistry
from app.ui.cast_pages import world_npcs_url
from app.ui.context import Page
from app.ui.library_links import NPCS_URL, WORLDS_URL, npc_url
from app.ui.names import name_text
from app.ui.npc_gen_pages import GenerateView, generate_page
from app.ui.tags import localized_context

router = APIRouter(tags=["npc generator"])

MAX_VALUE = 500
EDITION_BOOK = {"2014": "DMG", "2024": "XDMG"}


def _generator(page: Page, registry: StoreRegistry, npc: Npc, edition: str) -> NpcGenerator:
    tables = registry.npc_tables
    world = registry.adventures.world(npc.scope.world_id)
    present = [e for e, book in EDITION_BOOK.items() if book in tables.books()]
    if edition not in present:
        edition = edition_for(world.source_set, tables)
    return NpcGenerator(
        tables,
        edition,
        axis_label=lambda axis: page.t(f"npcgen.axis.{axis}"),
        naming=registry.naming_for(world.id),
        default_style=registry.naming_for(world.id)
        .find(npc.race, npc.culture, edition, page.lang)
        .key,
        language=page.lang,
    )


def _statblocks(page: Page, registry: StoreRegistry, npc: Npc) -> list[tuple[str, str, str]]:
    """Humanoids in the world's books that are not one named person, by CR."""
    world = registry.adventures.world(npc.scope.world_id)
    rows = [
        m
        for m in registry.compendium.monsters().of_type("humanoid").in_books(world.source_set)
        if m.cr is not None and not m.npc and not getattr(m.entry, "isNamedCreature", None)
    ]
    rows.sort(key=lambda m: (m.cr.value if m.cr else 0, m.name.casefold(), m.source))
    return [
        (
            f"{m.name}|{m.source}",
            f"{name_text(page, 'monster', m.name)} ({m.source})",
            m.cr.text if m.cr else "",
        )
        for m in rows
    ]


def _page(
    page: Page,
    registry: StoreRegistry,
    npc: Npc,
    generator: NpcGenerator,
    rolled: GeneratedNpc,
    *,
    name_set: str,
    gender: str,
    parent: str = "",
    take: set[str] | None = None,
    name_ticked: bool = False,
    statblock: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    world = registry.adventures.world(npc.scope.world_id)
    books = registry.npc_tables.books()
    elements = generator.elements()
    view = GenerateView(
        npc=npc,
        world=world,
        rolled=rolled,
        elements=elements,
        name_sets=generator.name_sets(),
        name_set=name_set,
        gender=gender,
        fallback=_no_style(registry, npc, generator, name_set, page.lang),
        relatives=[
            row for row in registry.naming_workshop.relatives(world.id) if row[0] != f"npc:{npc.id}"
        ],
        parent=parent,
        edition=generator.edition,
        editions=[e for e, book in EDITION_BOOK.items() if book in books],
        statblocks=_statblocks(page, registry, npc),
        statblock=statblock,
        take={*elements, *(["name"] if name_ticked else [])} if take is None else take,
    )
    ctx = localized_context(
        page.t,
        resolver=registry.references.resolver(),
        source_set=world.source_set,
        world_id=world.id,
    )
    return html(generate_page(page, view, ctx, error=error), status_code=status_code)


def _origin(registry: StoreRegistry, npc: Npc, name_set: str) -> dict[str, str]:
    """An NPC with no race or culture yet takes those of the style that named them."""
    if npc.race or npc.culture:
        return {}
    entry = registry.naming_for(npc.scope.world_id).entry(name_set)
    return {"race": entry.race, "culture": entry.culture}


def _no_style(
    registry: StoreRegistry, npc: Npc, generator: NpcGenerator, name_set: str, language: str
) -> bool:
    """True while the NPC's race and culture have no style and the default stands in."""
    found = registry.naming_for(npc.scope.world_id).find(
        npc.race, npc.culture, generator.edition, language
    )
    return found.fallback and found.key == name_set


def _parts(carried: object) -> dict[str, str]:
    """A name's parts as the page carried them; anything else is none."""
    try:
        parts = json.loads(str(carried or "{}"))
    except ValueError:
        return {}
    if not isinstance(parts, dict):
        return {}
    return {str(k): str(v) for k, v in parts.items()}


def _choices(generator: NpcGenerator, names: str, gender: str) -> tuple[str, str]:
    keys = {entry.key for entry in generator.name_sets()}
    return (
        names if names in keys else generator.default_name_set(),
        gender if gender in GENDERS else "any",
    )


@router.get(f"{NPCS_URL}/{{npc_id}}/generate", response_class=HTMLResponse)
async def show_generator(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    npc_id: str,
    names: str = "",
    gender: str = "",
    edition: str = "",
    keep_name: str = "",
) -> HTMLResponse:
    """A fresh roll; ``keep_name`` shows the NPC's own name as the rolled one."""
    npc = registry.npcs.get(npc_id)
    generator = _generator(page, registry, npc, edition)
    name_set, gender = _choices(generator, names, gender)
    rolled = generator.roll(name_set, gender)
    if keep_name:
        rolled.name = npc.name
        rolled.parts = dict(npc.given_name.parts) if npc.given_name else {}
    # An NPC's own name is kept unless the gamemaster ticks the rolled one.
    return _page(
        page,
        registry,
        npc,
        generator,
        rolled,
        name_set=name_set,
        gender=gender,
        name_ticked=bool(keep_name),
    )


@router.post(f"{NPCS_URL}/{{npc_id}}/generate", response_class=HTMLResponse)
async def use_generator(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    request: Request,
    npc_id: str,
    action: Annotated[str, Form()] = "roll",
    names: Annotated[str, Form()] = "",
    gender: Annotated[str, Form()] = "",
    edition: Annotated[str, Form()] = "",
    statblock: Annotated[str, Form()] = "",
    parent: Annotated[str, Form()] = "",
    take: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """Roll everything, roll one part, or keep what is ticked."""
    npc = registry.npcs.get(npc_id)
    generator = _generator(page, registry, npc, edition)
    name_set, gender = _choices(generator, names, gender)
    elements = generator.elements()
    submitted = await request.form()
    kin = registry.naming_workshop.kin_parts(parent)

    def carried(key: str) -> str:
        return str(submitted.get(f"v_{key}", ""))[:MAX_VALUE].strip()

    if action == "roll":
        rolled = generator.roll(name_set, gender, kin)
        return _page(
            page,
            registry,
            npc,
            generator,
            rolled,
            name_set=name_set,
            gender=gender,
            parent=parent,
            name_ticked="name" in (take or []),
            statblock=statblock,
        )
    rolled = GeneratedNpc(name=carried("name"), parts=_parts(submitted.get("v_name_parts")))
    for element in elements:
        rolled.values[element] = carried(element)
        rolled.sources[element] = generator.source_of(element)
    ticked = set(take or [])
    if action.startswith("reroll:"):
        part = action.removeprefix("reroll:")
        if part == "name":
            made = generator.roll_made(name_set, gender, kin)
            rolled.name, rolled.parts = made.text, made.parts
        elif part in elements:
            rolled.values[part] = generator.roll_element(part, rolled.values)
        return _page(
            page,
            registry,
            npc,
            generator,
            rolled,
            name_set=name_set,
            gender=gender,
            parent=parent,
            take=ticked,
            statblock=statblock,
        )
    # apply
    lines = [
        f"- **{page.t(f'npcgen.el.{e}')}:** {rolled.values[e]}"
        for e in elements
        if e in ticked and rolled.values.get(e)
    ]
    notes = npc.notes.rstrip()
    if lines:
        notes = f"{notes}\n\n" if notes else ""
        notes += "\n".join(lines) + "\n"
    try:
        registry.npcs.update(
            npc_id,
            name=rolled.name if "name" in ticked and rolled.name else npc.name,
            scope_value=scope.encode(npc.scope),
            roles=npc.roles,
            statblock=statblock or npc.statblock or "",
            notes=notes,
        )
        if "name" in ticked and rolled.name and rolled.parts:
            given = GivenName(text=rolled.name, parts=rolled.parts)
            registry.gamestate.npcs.update(
                npc_id, given_name=given, **_origin(registry, npc, name_set)
            )
    except Translatable as exc:
        return _page(
            page,
            registry,
            npc,
            generator,
            rolled,
            name_set=name_set,
            gender=gender,
            parent=parent,
            take=ticked,
            statblock=statblock,
            error=page.t.error(exc),
            status_code=422,
        )
    return redirect(f"{npc_url(npc_id)}?saved=1")


@router.post(f"{WORLDS_URL}/{{world_id}}/npcs/generate", response_class=HTMLResponse)
async def generate_new_npc(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """A new NPC with a rolled name (or the one typed), opened in the generator."""
    world = registry.adventures.world(world_id)
    edition = edition_for(world.source_set, registry.npc_tables)
    generator = NpcGenerator(
        registry.npc_tables, edition, naming=registry.naming_for(world.id), language=page.lang
    )
    made = (
        MadeName(name.strip())
        if name.strip()
        else generator.roll_made(generator.default_name_set())
    )
    try:
        npc = registry.npcs.create(
            made.text or page.t("npcgen.unnamed"), place or f"w:{world_id}", world_id
        )
    except Translatable:  # a place not in this world: the list's own form says so
        return redirect(world_npcs_url(world_id))
    if made.parts:
        given = GivenName(text=made.text, parts=made.parts)
        registry.gamestate.npcs.update(npc.id, given_name=given)
    return redirect(f"{npc_url(npc.id)}/generate?keep_name=1")


__all__ = ["router"]
