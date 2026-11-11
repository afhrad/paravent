"""The naming workshop (documentation/NAMES.md §5), for the gamemaster.

* ``GET  /gm/names``               — the races; ``?race=`` or ``?q=`` lists styles
* ``GET  /gm/names/samples?key=``  — a style that cannot be edited, sampled
* ``POST /gm/names/copy``          — a starter as the table's own
* ``GET/POST /gm/names/new``       — a new style in the form
* ``GET/POST /gm/names/raw``       — a new style as raw JSON
* ``POST /gm/names/preview[/raw]`` — samples of what a form holds, unsaved
* ``GET  /gm/names/{id}``          — thirty samples to judge
* ``GET/POST /gm/names/{id}/edit|raw|delete``, ``POST …/reject|restore``
* ``GET/POST /gm/worlds/{id}/names`` — the styles a world rolls its names from (§5b)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from htpy import p
from starlette.datastructures import FormData

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import World
from app.models.naming import SUBJECTS, NamingStyle
from app.routes.auth import html, redirect
from app.services.naming import GENDERS, OWN_PREFIX, STARTER_PREFIX, NamingError
from app.services.naming_workshop import (
    PartFields,
    StyleError,
    StyleFields,
    fields_of,
    race_names,
    races,
    raw_text,
    search,
    style_from,
    style_from_raw,
    subjects,
)
from app.services.npc_gen import edition_for
from app.services.style_drafts import DraftRequest
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.naming_pages import (
    DRAFT_ALL_URL,
    MAX_ROWS,
    NAMING_URL,
    NEW_URL,
    NO_RACE,
    RAW_EXAMPLE,
    RAW_URL,
    WorldScope,
    delete_page,
    draftable,
    overview_page,
    raw_page,
    results_page,
    samples_list,
    samples_page,
    style_form_page,
    style_page,
    style_url,
)
from app.ui.world_naming_pages import world_naming_page, world_naming_url

router = APIRouter(tags=["naming"])

EXAMPLES = 3
ROW_LIMIT = 200


def _gender(gender: str) -> str:
    return gender if gender in GENDERS else "any"


def _fields(form: FormData) -> StyleFields:
    """A form's style; part rows run on from 0 until one is missing."""
    parts: list[PartFields] = []
    while f"part-{len(parts)}-key" in form and len(parts) < ROW_LIMIT:
        prefix = f"part-{len(parts)}-"
        parts.append(
            PartFields(
                **{
                    name: str(form.get(prefix + name) or "")
                    for name in PartFields.__dataclass_fields__
                }
            )
        )
    return StyleFields(
        name=str(form.get("name") or ""),
        summary=str(form.get("summary") or ""),
        pattern=str(form.get("pattern") or ""),
        rewrites=str(form.get("rewrites") or ""),
        parts=parts,
        banned=str(form.get("banned") or ""),
        max_cluster=str(form.get("max_cluster") or ""),
        race=str(form.get("race") or ""),
        culture=str(form.get("culture") or ""),
        sources=str(form.get("sources") or ""),
        aliases=str(form.get("aliases") or ""),
        subject=str(form.get("subject") or "person"),
        language=str(form.get("language") or ""),
    )


@router.get(NAMING_URL, response_class=HTMLResponse)
async def overview(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    q: str = "",
    race: str | None = None,
    subject: str | None = None,
    world: str = "",
) -> HTMLResponse:
    """The races, the kinds of place and what waits to be accepted; with ``race``,
    ``subject`` or ``q``, the styles found. ``world`` keeps to that world's styles and
    names the races of its people that none serves."""
    scope = _scope(registry, world)
    naming = registry.naming_for(scope.world.id) if scope.world else registry.naming
    entries = naming.entries()
    kind = subject if subject in SUBJECTS else None
    if race is None and kind is None and not q.strip():
        drafts = [s for s in registry.naming_workshop.styles() if s.draft]
        examples = {s.id: naming.sample(s, count=EXAMPLES) for s in drafts}
        return html(
            overview_page(
                page, races(entries), drafts, examples, places=subjects(entries), scope=scope
            )
        )
    wanted = None if race is None else "" if race == NO_RACE else race
    found = search(entries, q, wanted, kind)
    drawn = {e.key: naming.sample(e.style, count=EXAMPLES) for e in found[:MAX_ROWS] if e.style}
    return html(
        results_page(page, found, drawn, query=q.strip(), race=wanted, subject=kind, scope=scope)
    )


def _scope(registry: StoreRegistry, world_id: str) -> WorldScope:
    """Every world to choose from, and the chosen one's styles, its unserved peoples and
    Claude's drafts for them."""
    worlds = sorted(registry.worlds.list(), key=lambda w: w.name.casefold())
    world = next((w for w in worlds if w.id == world_id), None)
    if world is None:
        return WorldScope(worlds=[(w.id, w.name) for w in worlds])
    edition = edition_for(world.source_set, registry.npc_tables)
    drafts = registry.style_drafts
    return WorldScope(
        worlds=[(w.id, w.name) for w in worlds],
        world=world,
        unserved=registry.naming_workshop.unserved(
            world.id,
            registry.naming_for(world.id),
            edition,
            race_names(registry.glossary, world.language),
        ),
        runs=drafts.runs(world.id),
        can_draft=drafts.available,
    )


def _draft(registry: StoreRegistry, world: World, race: str, aliases: Sequence[str]) -> None:
    """Start Claude's draft of a style for one people of ``world``, with what the world
    knows of it: its NPCs' names, the styles in use and the setting."""
    met = {race.casefold(), *(a.casefold() for a in aliases)}
    known = [
        n.name
        for n in registry.gamestate.npcs.list()
        if n.scope.world_id == world.id and n.race.casefold() in met
    ]
    naming = registry.naming_for(world.id)
    styles = (
        [e.style.name for e in naming.entries() if e.style is not None] if naming.restricted else []
    )
    registry.style_drafts.start(
        DraftRequest(
            world_id=world.id,
            world_name=world.name,
            race=race.strip(),
            aliases=[a.strip() for a in aliases if a.strip()],
            language=world.language,
            setting=registry.instructions.get(world.id).setting,
            known=known[:40],
            styles=styles,
        )
    )


@router.post(f"{NAMING_URL}/claude")
async def draft_with_claude(
    _gm: GamemasterDep,
    registry: RegistryDep,
    request: Request,
) -> Response:
    """Ask Claude to research and draft a style for a people of a world, in the
    background; the workshop shows how it goes."""
    form = await request.form()
    found = registry.worlds.get(str(form.get("world") or ""))
    _draft(
        registry,
        found,
        str(form.get("race") or ""),
        [str(a) for a in form.getlist("aliases")],
    )
    return redirect(WorldScope(world=found).href())


@router.post(DRAFT_ALL_URL)
async def draft_all_with_claude(
    _gm: GamemasterDep,
    registry: RegistryDep,
    world: Annotated[str, Form()],
) -> Response:
    """A draft for every people of the world still without one, queued in the
    background (three at a time); each row shows how its draft goes."""
    scope = _scope(registry, world)
    if scope.world is None:
        raise HTTPException(status_code=404)
    for people in draftable(scope):
        _draft(registry, scope.world, people.race, people.aliases)
    return redirect(scope.href())


@router.post(f"{NAMING_URL}/accept-all")
async def accept_all(_gm: GamemasterDep, registry: RegistryDep) -> Response:
    """Make every draft one of the table's styles."""
    registry.naming_workshop.accept_all()
    return redirect(NAMING_URL)


@router.get(f"{NAMING_URL}/samples", response_class=HTMLResponse)
async def samples(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, key: str, gender: str = "any"
) -> Response:
    """A starter's or the books' style; the table's own opens its page."""
    if key.startswith(OWN_PREFIX):
        return redirect(style_url(key.removeprefix(OWN_PREFIX)))
    try:
        entry = registry.naming.entry(key)
    except NamingError:
        raise HTTPException(404) from None
    gender = _gender(gender)
    names = registry.naming.names(key, gender, count=30)
    return html(samples_page(page, entry, names, gender))


@router.post(f"{NAMING_URL}/copy")
async def copy(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    key: str = Form(""),  # noqa: B008
) -> Response:
    """A starter as the table's own, under its name in the reader's language."""
    starter = key.removeprefix(STARTER_PREFIX)
    try:
        style = registry.naming_workshop.copy_starter(
            starter, page.t(f"naming.style.{starter}"), page.t(f"naming.summary.{starter}")
        )
    except StyleError:
        raise HTTPException(404) from None
    return redirect(style_url(style.id))


def _form(
    page: Page,
    registry: StoreRegistry,
    fields: StyleFields,
    style: NamingStyle | None,
    error: str | None,
) -> Response:
    url, back = (
        (f"{style_url(style.id)}/edit", style_url(style.id)) if style else (NEW_URL, NAMING_URL)
    )
    title = page.t("naming.edit", name=style.name) if style else page.t("naming.new")
    groups = races(registry.naming.entries())
    return html(
        style_form_page(
            page,
            fields,
            action=url,
            back=back,
            title=title,
            races=[g.race for g in groups if g.race],
            cultures=sorted({c for g in groups for c in g.cultures}),
            error=error,
        ),
        status_code=422 if error else 200,
    )


def _raw(page: Page, text: str, style: NamingStyle | None, error: str | None) -> Response:
    url, back = (
        (f"{style_url(style.id)}/raw", style_url(style.id)) if style else (RAW_URL, NAMING_URL)
    )
    title = page.t("naming.edit", name=style.name) if style else page.t("naming.new")
    return html(
        raw_page(page, text, action=url, back=back, title=title, error=error),
        status_code=422 if error else 200,
    )


@router.get(NEW_URL, response_class=HTMLResponse)
async def new_form(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    race: str = "",
    subject: str = "",
    aliases: str = "",
) -> Response:
    """An empty style, of the race or kind of place it was asked for from."""
    if subject in SUBJECTS and subject != "person":
        fields = StyleFields(pattern="{name}", parts=[PartFields(key="name")], subject=subject)
    else:
        fields = StyleFields(
            pattern="{given}", parts=[PartFields(key="given")], race=race, aliases=aliases
        )
    return _form(page, registry, fields, None, None)


@router.post(NEW_URL, response_class=HTMLResponse)
async def create(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request
) -> Response:
    """Save a new style."""
    fields = _fields(await request.form())
    try:
        style = registry.naming_workshop.create(style_from(fields))
    except StyleError as exc:
        return _form(page, registry, fields, None, page.t.error(exc))
    return redirect(style_url(style.id))


@router.get(RAW_URL, response_class=HTMLResponse)
async def new_raw(_gm: GamemasterDep, page: PageDep) -> Response:
    """A new style as raw text, from an outline."""
    return _raw(page, RAW_EXAMPLE, None, None)


@router.post(RAW_URL, response_class=HTMLResponse)
async def create_raw(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    raw: str = Form(""),  # noqa: B008
) -> Response:
    """Save a new style from raw text."""
    try:
        style = registry.naming_workshop.create(style_from_raw(raw))
    except StyleError as exc:
        return _raw(page, raw, None, page.t.error(exc))
    return redirect(style_url(style.id))


def _preview(page: Page, registry: StoreRegistry, make: NamingStyle) -> Response:
    return html(samples_list(page, registry.naming.sample(make), wanted=30))


def _preview_error(page: Page, exc: StyleError) -> Response:
    return html(p(id="naming-samples", class_="alert")[page.t.error(exc)])


@router.post(f"{NAMING_URL}/preview", response_class=HTMLResponse)
async def preview(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request
) -> Response:
    """Samples of the form as it stands; nothing is saved."""
    try:
        return _preview(page, registry, style_from(_fields(await request.form())))
    except StyleError as exc:
        return _preview_error(page, exc)


@router.post(f"{NAMING_URL}/preview/raw", response_class=HTMLResponse)
async def preview_raw(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    raw: str = Form(""),  # noqa: B008
) -> Response:
    """Samples of the raw text as it stands; nothing is saved."""
    try:
        return _preview(page, registry, style_from_raw(raw))
    except StyleError as exc:
        return _preview_error(page, exc)


@router.get(f"{NAMING_URL}/{{style_id}}", response_class=HTMLResponse)
async def show(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, style_id: str, gender: str = "any"
) -> HTMLResponse:
    """Thirty samples to judge."""
    style = registry.naming_workshop.get(style_id)
    gender = _gender(gender)
    names = registry.naming.sample(style, gender)
    return html(style_page(page, style, names, gender))


@router.post(f"{NAMING_URL}/{{style_id}}/accept")
async def accept(_gm: GamemasterDep, registry: RegistryDep, style_id: str) -> Response:
    """Make Claude's draft one of the table's styles."""
    registry.naming_workshop.accept(style_id)
    return redirect(style_url(style_id))


@router.post(f"{NAMING_URL}/{{style_id}}/alias")
async def add_alias(
    _gm: GamemasterDep,
    registry: RegistryDep,
    style_id: str,
    alias: str = Form(),
    world: str = Form(default=""),
) -> Response:
    """Let a style serve a people under another name too, as the workshop offered."""
    registry.naming_workshop.add_alias(style_id, alias)
    found = registry.gamestate.worlds.find(world) if world else None
    return redirect(WorldScope(world=found).href() if found else style_url(style_id))


@router.get(f"{NAMING_URL}/{{style_id}}/edit", response_class=HTMLResponse)
async def edit_form(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, style_id: str
) -> Response:
    """A style in the form."""
    style = registry.naming_workshop.get(style_id)
    return _form(page, registry, fields_of(style), style, None)


@router.post(f"{NAMING_URL}/{{style_id}}/edit", response_class=HTMLResponse)
async def edit(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, style_id: str, request: Request
) -> Response:
    """Save a style from the form; its rejected names stay."""
    workshop = registry.naming_workshop
    style = workshop.get(style_id)
    fields = _fields(await request.form())
    try:
        workshop.save(style_from(fields, base=style))
    except StyleError as exc:
        return _form(page, registry, fields, style, page.t.error(exc))
    return redirect(style_url(style.id))


@router.get(f"{NAMING_URL}/{{style_id}}/raw", response_class=HTMLResponse)
async def raw_form(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, style_id: str
) -> Response:
    """A style as raw text."""
    style = registry.naming_workshop.get(style_id)
    return _raw(page, raw_text(style), style, None)


@router.post(f"{NAMING_URL}/{{style_id}}/raw", response_class=HTMLResponse)
async def edit_raw(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    style_id: str,
    raw: str = Form(""),  # noqa: B008
) -> Response:
    """Save a style from raw text."""
    workshop = registry.naming_workshop
    style = workshop.get(style_id)
    try:
        workshop.save(style_from_raw(raw, base=style))
    except StyleError as exc:
        return _raw(page, raw, style, page.t.error(exc))
    return redirect(style_url(style.id))


@router.post(f"{NAMING_URL}/{{style_id}}/reject", response_class=HTMLResponse)
async def reject(
    _gm: GamemasterDep,
    registry: RegistryDep,
    request: Request,
    style_id: str,
    name: str = Form(""),  # noqa: B008
) -> Response:
    """Turn a sample down: its row goes in place, or the page comes back."""
    registry.naming_workshop.reject(style_id, name)
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("")
    return redirect(style_url(style_id))


@router.post(f"{NAMING_URL}/{{style_id}}/restore")
async def restore(
    _gm: GamemasterDep,
    registry: RegistryDep,
    style_id: str,
    name: str = Form(""),  # noqa: B008
) -> Response:
    """Take a rejected name back."""
    registry.naming_workshop.restore(style_id, name)
    return redirect(style_url(style_id))


@router.get(f"{NAMING_URL}/{{style_id}}/delete", response_class=HTMLResponse)
async def delete_form(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, style_id: str
) -> HTMLResponse:
    """Ask before a style goes."""
    style = registry.naming_workshop.get(style_id)
    return html(delete_page(page, style))


@router.post(f"{NAMING_URL}/{{style_id}}/delete")
async def delete(_gm: GamemasterDep, registry: RegistryDep, style_id: str) -> Response:
    """Remove a style."""
    registry.naming_workshop.delete(style_id)
    return redirect(NAMING_URL)


@router.get("/gm/worlds/{world_id}/names", response_class=HTMLResponse)
async def world_styles(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world_id: str, saved: str = ""
) -> HTMLResponse:
    """Every style, ticked when the world rolls names from it; none ticked is all."""
    world = registry.worlds.get(world_id)
    entries = registry.naming.entries()
    existing = {e.key for e in entries}
    return html(
        world_naming_page(
            page,
            world,
            entries,
            [k for k in world.naming_styles if k in existing],
            notice_text=page.t("naming.world.saved") if saved else None,
        )
    )


@router.post("/gm/worlds/{world_id}/names")
async def save_world_styles(
    _gm: GamemasterDep, registry: RegistryDep, world_id: str, request: Request
) -> Response:
    """The ticked styles; "every" clears the choice."""
    form = await request.form()
    keys = [] if form.get("every") else [str(v) for v in form.getlist("style")]
    registry.worlds.set_naming(world_id, keys)
    return redirect(f"{world_naming_url(world_id)}?saved=1")
