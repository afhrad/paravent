"""The gamemaster's encounters.

* ``GET  /gm/encounters``                      — every encounter (``?world=`` narrows it)
* ``POST /gm/encounters``                      — start one
* ``GET  /gm/encounters/{id}``                 — the builder; ``?q=`` searches monsters to add
* ``POST /gm/encounters/{id}``                 — save its settings
* ``POST /gm/encounters/{id}/monsters``        — add ``ref=Name|SOURCE``
* ``POST /gm/encounters/{id}/monsters/{i}/side`` — line ``i`` as friend (``ally=1``) or foe
* ``POST /gm/encounters/{id}/monsters/{i}``    — set how many of line ``i`` (0 removes it)
* ``/gm/encounters/{id}/delete``               — confirm, then delete

Every post redirects back to the builder, keeping the monster search the gamemaster was
in the middle of, so adding six different monsters is six clicks and no retyping.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep, WorldsDep
from app.models.gamestate import PartySpec
from app.models.pc import DraftStatus
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.services import scope, token_export
from app.services.encounter_gen import EncounterGenerator, bands_of, pool_of
from app.services.encounter_roll import EncounterRoller
from app.services.encounters import EncounterError, EncounterView
from app.services.npc_gen import edition_for
from app.services.scope import ScopeError
from app.services.search import SearchResult
from app.services.token_export import Piece, PlannedGroup
from app.services.tokens import Token
from app.services.worlds import WorldService
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.encounter_pages import (
    ENCOUNTERS_URL,
    encounter_delete_page,
    encounter_line,
    encounter_page,
    encounter_tokens_page,
    encounter_url,
    encounters_page,
    shape_name,
)
from app.ui.markdown import Rendered, render_markdown
from app.ui.names import name_text

router = APIRouter(tags=["encounters"])

PICKER_GROUP = "bestiary"


def _with_sounds(registry: StoreRegistry, view: EncounterView) -> EncounterView:
    """The builder's choice of sounds; one chosen earlier stays even if it has gone."""
    names = registry.sounds.names()
    if view.encounter.sound and view.encounter.sound not in names:
        names.insert(0, view.encounter.sound)
    return _with_tokens(registry, replace(view, sound_names=tuple(names)))


def _with_tokens(registry: StoreRegistry, view: EncounterView) -> EncounterView:
    """Each planned line's round token (documentation/TOKENS.md)."""
    world_id = view.world.id if view.world else None
    tokens = {}
    for line in view.lines:
        entry = line.monster.entry if line.monster is not None else None
        extra = (getattr(entry, "model_extra", None) or {}) if entry is not None else {}
        planned = line.planned
        tokens[line.index] = registry.monster_token(
            planned.name, planned.source, world_id, extra.get("token")
        )
    return replace(view, tokens=tokens)


def _refused(
    page: Page,
    registry: StoreRegistry,
    worlds: WorldService,
    encounter_id: str,
    exc: EncounterError | ScopeError,
) -> HTMLResponse:
    """The builder again, saying why the change was refused."""
    service = registry.encounters
    view = service.view(service.get(encounter_id))
    result = registry.search.search("", view.source_set, group=PICKER_GROUP, term=page.t.terms())
    return html(
        encounter_page(
            page,
            _with_sounds(registry, view),
            _scopes(registry),
            result,
            place=_place(registry, view),
            error=page.t.error(exc),
            name_styles=registry.naming_for(view.encounter.world_id).entries(),
        ),
        status_code=422,
    )


def _scopes(registry: StoreRegistry) -> list[tuple[str, str, int]]:
    """Every world, adventure and chapter an encounter could belong to."""
    return [
        option
        for world in registry.worlds.list()
        for option in scope.choices(registry.adventures, world.id)
    ]


def _place(registry: StoreRegistry, view: EncounterView) -> list[str]:
    where = view.encounter.scope
    return scope.label(registry.adventures, where) if where else []


def _back(encounter_id: str, q: str = "", all_books: str = "", anchor: str = "plan") -> str:
    params = {"q": q.strip(), "all": "1" if all_books == "1" else ""}
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{encounter_url(encounter_id)}{'?' + query if query else ''}#{anchor}"


@router.get(ENCOUNTERS_URL, response_class=HTMLResponse)
async def list_encounters(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    world: str = "",
    deleted: str = "",
) -> HTMLResponse:
    """Every encounter, rated."""
    service = registry.encounters
    world_id = world if worlds.find(world or None) is not None else None
    views = [service.view(e) for e in service.list(world_id)]
    return html(
        encounters_page(
            page,
            views,
            worlds.list(),
            world_filter=world_id,
            notice_text=page.t("encounters.deleted") if deleted else None,
        )
    )


@router.post(ENCOUNTERS_URL, response_class=HTMLResponse)
async def create_encounter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    name: Annotated[str, Form()] = "",
    world: Annotated[str, Form()] = "",
    place: Annotated[str, Form()] = "",
) -> Response:
    """Start an encounter and open its builder.

    ``place`` (a scope, from a chapter or adventure page) puts it there directly.
    """
    service = registry.encounters
    try:
        where = scope.decode(place, registry.adventures) if place else None
        encounter = service.create(name, world or None, where)
    except (EncounterError, ScopeError) as exc:
        views = [service.view(e) for e in service.list()]
        view = encounters_page(
            page,
            views,
            worlds.list(),
            world_filter=None,
            error=page.t.error(exc),
            draft_name=name,
        )
        return html(view, status_code=422)
    return redirect(_back(encounter.id, anchor="picker"))


@router.get(f"{ENCOUNTERS_URL}/{{encounter_id}}", response_class=HTMLResponse)
async def builder(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    q: str = "",
    all: str = "",  # noqa: A002 — the query parameter's name
) -> HTMLResponse:
    """The plan, its rating, and a monster search scoped to the world's books."""
    service = registry.encounters
    view = service.view(service.get(encounter_id))
    result = registry.search.search(
        q[:100],
        view.source_set,
        group=PICKER_GROUP,
        all_books=all == "1",
        term=page.t.terms(),
    )
    return _builder(page, registry, view, result)


def _builder(
    page: Page,
    registry: StoreRegistry,
    view: EncounterView,
    result: SearchResult,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    built: tuple[str, str, str] = ("", "", ""),
) -> HTMLResponse:
    """The builder page, with the fights run from it and the tables to roll on.
    ``built``: the difficulty, shape and habitat a fight was last built with."""
    combats = registry.combats
    encounter_id = view.encounter.id
    running = next((c for c in combats.list(encounter_id=encounter_id) if c.running), None)
    fought = [(c, combats.summary(c)) for c in combats.finished(encounter_id)]
    source_set = view.source_set
    environments = [
        (
            env.key,
            f"{env.name} ({env.source})",
            source_set is None or source_set.is_unrestricted or source_set.allows(env.source),
        )
        for env in registry.encounter_tables.all()
    ]
    _pool, habitats = pool_of(registry.compendium.monsters().in_books(source_set))
    # Without the rules' tables (a 2014 world whose corpus lacks the guide) nothing rates.
    rated = registry.encounters.difficulty.has_tables(view.rating.rules)
    shapes = [s.name for s in registry.encounter_tables.shapes()] if rated else []
    return html(
        encounter_page(
            page,
            _with_sounds(registry, view),
            _scopes(registry),
            result,
            place=_place(registry, view),
            name_styles=registry.naming_for(view.encounter.world_id).entries(),
            running=running,
            fought=fought,
            environments=environments,
            build=(shapes, habitats, built),
            notice_text=notice_text,
            error=error,
            notes=_notes(page, registry, view),
        )
    )


def _notes(page: Page, registry: StoreRegistry, view: EncounterView) -> Rendered | None:
    """The encounter's notes as Paravent Markdown, resolved where it is placed."""
    encounter = view.encounter
    if not encounter.notes.strip():
        return None
    if encounter.scope is None:  # in no world: no names or books to resolve against
        return render_markdown(encounter.notes)
    return paravent_markdown(page, registry, encounter.scope, encounter.notes)


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/roll", response_class=HTMLResponse)
async def roll_encounter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    encounter_id: str,
    environment: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Roll the environment's table for the party's level; the creatures replace the plan."""
    service = registry.encounters
    view = service.view(service.get(encounter_id))
    result = registry.search.search("", view.source_set, group=PICKER_GROUP, term=page.t.terms())
    env = registry.encounter_tables.get(environment)
    levels = [m.level for m in view.party]
    level = round(sum(levels) / len(levels)) if levels else 1
    resolver = registry.compendium.resolver()

    def resolve(name: str, source: str | None) -> tuple[str, str] | None:
        found = resolver.resolve_parts("monster", name, source, view.source_set)
        entry = found.entry
        return (str(entry.name), str(entry.source)) if entry is not None else None

    rolled = EncounterRoller(resolve).roll(env, level) if env is not None else None
    if env is not None:
        service.remember_table(encounter_id, environment)
        view = service.view(service.get(encounter_id))
    if rolled is None:
        return _builder(page, registry, view, result, error=page.t("encounters.roll_none"))
    t = page.t
    lines = [
        t(
            "encounters.rolled_note",
            table=f"{rolled.environment} ({rolled.source})",
            roll=rolled.roll,
            text=rolled.text,
        )
    ]
    if rolled.unresolved:
        lines.append(t("encounters.rolled_missing", names=", ".join(rolled.unresolved)))
    service.apply_roll(encounter_id, rolled.monsters, "\n".join(lines))
    view = service.view(service.get(encounter_id))
    notice_text = (
        t("encounters.rolled", text=rolled.text)
        if rolled.monsters
        else t("encounters.rolled_event", text=rolled.text)
    )
    return _builder(page, registry, view, result, notice_text=notice_text)


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/build", response_class=HTMLResponse)
async def build_encounter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    encounter_id: str,
    difficulty: Annotated[str, Form()] = "",
    shape: Annotated[str, Form()] = "",
    habitat: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Build a fight at a difficulty for the party, from the world's books (a habitat's
    creatures, if chosen) in one of the corpus's shapes; it replaces the monsters."""
    service = registry.encounters
    view = service.view(service.get(encounter_id))
    result = registry.search.search("", view.source_set, group=PICKER_GROUP, term=page.t.terms())
    t = page.t
    built = (difficulty, shape, habitat)
    levels = [m.level for m in view.party]
    if not levels:
        return _builder(
            page, registry, view, result, error=t("encounters.build_no_party"), built=built
        )
    monsters = registry.compendium.monsters().in_books(view.source_set)
    if habitat:
        monsters = monsters.environment(habitat)
    pool, _habitats = pool_of(monsters)
    shapes = [s for s in registry.encounter_tables.shapes() if not shape or s.name == shape]
    rules = view.rating.rules
    band = difficulty if difficulty in bands_of(rules) else bands_of(rules)[1]
    made = EncounterGenerator(service.difficulty).generate(pool, levels, rules, band, shapes)
    if made is None:
        return _builder(page, registry, view, result, error=t("encounters.build_none"), built=built)
    rating = made.rating
    note = t(
        "encounters.built_note",
        shape=shape_name(page, made.shape),
        difficulty=t(f"encounters.level.{band}"),
        xp=t.number(rating.adjusted_xp),
    )
    service.apply_roll(encounter_id, made.monsters, note)
    view = service.view(service.get(encounter_id))
    shown = t(f"encounters.level.{rating.level}") if rating.level else ""
    return _builder(
        page,
        registry,
        view,
        result,
        notice_text=t("encounters.built", shape=shape_name(page, made.shape), difficulty=shown),
        built=built,
    )


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}", response_class=HTMLResponse)
async def save_encounter(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    name: Annotated[str, Form()] = "",
    world: Annotated[str, Form()] = "",
    rules: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    sound: Annotated[str, Form()] = "",
    use_party: Annotated[str, Form()] = "",
    party_size: Annotated[int, Form()] = 4,
    party_level: Annotated[int, Form()] = 1,
) -> Response:
    """Save name, world, rules, the stated party and notes."""
    service = registry.encounters
    party = (
        PartySpec(size=max(1, min(12, party_size)), level=max(1, min(20, party_level)))
        if use_party
        else None
    )
    try:
        # ``world`` carries a scope: ``w:``/``a:``/``c:`` plus an id, a bare world id,
        # or nothing for no world at all.
        place = scope.decode(world, registry.adventures) if world else None
    except ScopeError as exc:
        return _refused(page, registry, worlds, encounter_id, exc)
    try:
        service.update(
            encounter_id,
            name=name,
            scope=place,
            notes=notes,
            rules=rules or None,
            party=party,
            sound=sound,
        )
    except EncounterError as exc:
        return _refused(page, registry, worlds, encounter_id, exc)
    return redirect(_back(encounter_id, anchor="rating-h"))


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/monsters")
async def add_monster(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    ref: Annotated[str, Form()] = "",
    q: Annotated[str, Form()] = "",
    all: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Add one of a creature, then come back to the same search."""
    try:
        registry.encounters.add_monster(encounter_id, ref)
    except EncounterError as exc:
        return _refused(page, registry, worlds, encounter_id, exc)
    return redirect(_back(encounter_id, q, all, anchor="picker"))


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/monsters/{{index}}")
async def set_count(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    index: int,
    count: Annotated[int, Form()] = 0,
) -> Response:
    """Change how many of one creature there are; 0 takes it out of the plan."""
    try:
        registry.encounters.set_count(encounter_id, index, count)
    except EncounterError as exc:
        return _refused(page, registry, worlds, encounter_id, exc)
    return redirect(_back(encounter_id))


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/monsters/{{index}}/side")
async def set_side(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    index: int,
    ally: Annotated[str, Form()] = "",
) -> Response:
    """Put a planned creature on the characters' side, or back against them."""
    try:
        registry.encounters.set_ally(encounter_id, index, ally == "1")
    except EncounterError as exc:
        return _refused(page, registry, worlds, encounter_id, exc)
    return redirect(_back(encounter_id))


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/monsters/{{index}}/label")
async def set_label(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    index: int,
    label: Annotated[str, Form()] = "",
) -> Response:
    """Show a planned creature under another name — a reskin ("Mire Lurker" for a Kuo-toa)."""
    try:
        registry.encounters.set_label(encounter_id, index, label)
    except EncounterError as exc:
        return _refused(page, registry, worlds, encounter_id, exc)
    return redirect(_back(encounter_id))


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/monsters/{{index}}/label/roll")
async def roll_label(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    encounter_id: str,
    index: int,
    request: Request,
    style: Annotated[str, Form()] = "",
) -> Response:
    """Name a planned creature: in the style its kind finds, or the one chosen. From
    HTMX the answer is the line alone, swapped in place."""
    service = registry.encounters
    encounter = service.get(encounter_id)
    world = registry.gamestate.worlds.find(encounter.world_id) if encounter.world_id else None
    edition = edition_for(world.source_set if world else None, registry.npc_tables)
    swapping = request.headers.get("HX-Request") == "true"
    try:
        encounter = service.roll_label(
            encounter_id,
            index,
            registry.naming_for(encounter.world_id),
            edition,
            style,
            page.lang,
        )
    except EncounterError as exc:
        if not swapping:
            return _refused(page, registry, worlds, encounter_id, exc)
    if swapping and 0 <= index < len(encounter.monsters):
        line = encounter_line(
            page,
            _with_tokens(registry, service.view(encounter)),
            index,
            registry.naming_for(encounter.world_id).entries(),
            style,
        )
        return html(line)
    return redirect(_back(encounter_id))


def _pieces(page: Page, registry: StoreRegistry, encounter_id: str, party: bool) -> list[Piece]:
    """The fight's tokens to take to the table (documentation/TOKENS.md §4): the world's
    characters when ``party``, then every planned creature."""
    view = _with_tokens(registry, registry.encounters.view(registry.encounters.get(encounter_id)))
    groups = [
        PlannedGroup(
            line.planned.reskin or name_text(page, "monster", line.planned.name),
            view.tokens.get(line.index, Token("creature")),
            line.planned.count,
            line.monster.sizes if line.monster is not None else (),
            line.planned.names,
        )
        for line in view.lines
    ]
    world_id = view.world.id if view.world else None
    members = (
        [
            (pc.name or pc.id, registry.tokens.of_record("character", world_id, pc.id))
            for pc in registry.characters.in_world(world_id)
            if pc.status is DraftStatus.READY
        ]
        if party and world_id
        else []
    )
    return token_export.pieces(groups, members)


@router.get(f"{ENCOUNTERS_URL}/{{encounter_id}}/tokens", response_class=HTMLResponse)
async def encounter_tokens(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    encounter_id: str,
    square: int = token_export.DEFAULT_SQUARE_MM,
    party: bool = False,
) -> HTMLResponse:
    """The fight's tokens, laid out to print at a chosen square size, or to download."""
    encounter = registry.encounters.get(encounter_id)
    square = square if square in token_export.SQUARE_MM else token_export.DEFAULT_SQUARE_MM
    return html(
        encounter_tokens_page(
            page,
            encounter,
            _pieces(page, registry, encounter_id, party),
            square=square,
            party=party,
        )
    )


@router.get(f"{ENCOUNTERS_URL}/{{encounter_id}}/tokens/{{n:int}}.png")
async def encounter_token_piece(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    encounter_id: str,
    n: int,
    party: bool = False,
) -> Response:
    """One piece, drawn sharp enough to print."""
    found = _pieces(page, registry, encounter_id, party)
    if not 0 <= n < len(found):
        raise HTTPException(status_code=404)
    piece = found[n]
    data = await asyncio.to_thread(
        token_export.render,
        piece,
        registry.tokens.path_of(piece.token),
        token_export.PRINT_PIXELS,
    )
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.get(f"{ENCOUNTERS_URL}/{{encounter_id}}/tokens.zip")
async def encounter_tokens_zip(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    encounter_id: str,
    px: int = token_export.DEFAULT_PIXELS,
    party: bool = False,
) -> Response:
    """Every piece as a PNG, ``px`` per square, in one ZIP for a virtual tabletop."""
    encounter = registry.encounters.get(encounter_id)
    px = px if px in token_export.PIXELS else token_export.DEFAULT_PIXELS
    found = _pieces(page, registry, encounter_id, party)
    data = await asyncio.to_thread(token_export.archive, found, registry.tokens.path_of, px)
    filename = Piece(encounter.name or "tokens", Token("encounter")).filename
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename[:-4] + '.zip')}"
        },
    )


@router.get(f"{ENCOUNTERS_URL}/{{encounter_id}}/delete", response_class=HTMLResponse)
async def confirm_delete(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, encounter_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(encounter_delete_page(page, registry.encounters.get(encounter_id)))


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/delete")
async def delete_encounter(
    _gm: GamemasterDep, registry: RegistryDep, encounter_id: str
) -> Response:
    """Delete the encounter."""
    registry.encounters.delete(encounter_id)
    return redirect(f"{ENCOUNTERS_URL}?deleted=1")


__all__ = ["router"]
