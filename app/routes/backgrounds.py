"""The background builder (app/services/background_builder.py).

* ``GET  /gm/backgrounds``               — the designs; ``POST`` begins one (``name``,
  ``based_on`` a printed background by name or ``Name|SOURCE``)
* ``GET  /gm/backgrounds/{id}``          — the builder
* ``POST /gm/backgrounds/{id}``          — the whole form, with ``action``: ``save``,
  ``publish`` or ``accept`` (Claude's draft taken in and published)
* ``GET|POST /gm/backgrounds/{id}/delete``
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import BackgroundDesign, BackgroundItem
from app.routes.auth import html, redirect
from app.services.background_builder import BackgroundError, BackgroundNotFound
from app.services.references import ReferenceEntry
from app.store.registry import StoreRegistry
from app.ui.background_builder_pages import (
    BACKGROUNDS_URL,
    BackgroundRow,
    BackgroundView,
    background_url,
    backgrounds_page,
    builder_page,
    delete_page,
)
from app.ui.context import Page
from app.ui.tags import localized_context

router = APIRouter(tags=["background builder"])

# The skills a background may give, when the books' skill list is missing.
SKILLS = (
    "acrobatics", "animal handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight of hand", "stealth", "survival",
)  # fmt: skip


def _int(raw: object, high: int) -> int:
    text = str(raw or "").strip()
    return max(0, min(high, int(text))) if text.isdigit() else 0


def _design(registry: StoreRegistry, design_id: str) -> BackgroundDesign:
    try:
        return registry.background_builder.get(design_id)
    except BackgroundNotFound as exc:
        raise HTTPException(status_code=404) from exc


def _skills(registry: StoreRegistry) -> list[str]:
    found = sorted({str(getattr(s, "name", "")).lower() for s in registry.compendium.all("skill")})
    return [s for s in found if s] or list(SKILLS)


def _list_page(
    page: Page,
    registry: StoreRegistry,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    builder = registry.background_builder
    names = {w.id: w.name for w in registry.worlds.list()}
    rows = [
        BackgroundRow(d, names.get(d.world_id or "", ""), builder.unpublished(d))
        for d in builder.list()
    ]
    return html(
        backgrounds_page(page, rows, notice_text=notice_text, error=error, draft=draft),
        status_code=status_code,
    )


def _builder(
    page: Page,
    registry: StoreRegistry,
    design: BackgroundDesign,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    builder = registry.background_builder
    world = registry.worlds.find(design.world_id) if design.world_id else None
    view = BackgroundView(
        design=design,
        worlds=registry.worlds.list(),
        skills=_skills(registry),
        preview=ReferenceEntry("background", builder.background(design), design.world_id),
        unpublished=builder.unpublished(design),
        published=design.published_at is not None,
    )
    ctx = localized_context(
        page.t,
        resolver=registry.references.resolver(),
        source_set=world.source_set if world else None,
        world_id=design.world_id,
        units=page.u,
    )
    return html(
        builder_page(page, view, ctx, notice_text=notice_text, error=error),
        status_code=status_code,
    )


def _resolve(registry: StoreRegistry, world_id: str | None, typed: str) -> str:
    name, _, source = typed.strip().partition("|")
    if not name.strip():
        return ""
    world = registry.worlds.find(world_id) if world_id else None
    found = (
        registry.references.resolver()
        .resolve_parts(
            "background", name.strip(), source.strip() or None, world.source_set if world else None
        )
        .entry
    )
    return f"{found.name}|{getattr(found, 'source', '')}" if found is not None else typed.strip()


def _split(text: str) -> list[str]:
    return [x.strip().lower() for x in text.split(",") if x.strip()]


def _read_form(
    design: BackgroundDesign, form: FormData, registry: StoreRegistry
) -> BackgroundDesign:
    world = str(form.get("world", ""))
    equipment = [
        BackgroundItem(
            item=str(i).strip()[:200],
            special=str(s).strip()[:200],
            quantity=_int(q, 999) or 1,
        )
        for i, s, q in zip(
            form.getlist("eq_item"),
            form.getlist("eq_special"),
            form.getlist("eq_quantity"),
            strict=False,
        )
        if str(i).strip() or str(s).strip()
    ]
    fields: dict[str, object] = {
        "name": str(form.get("name", design.name)),
        "world_id": world if registry.worlds.find(world) else None,
        "edition": "classic" if str(form.get("edition")) == "classic" else "one",
        "skills": [str(x) for x in form.getlist("skills")],
        "any_skills": _int(form.get("any_skills"), 4),
        "tools": _split(str(form.get("tools", ""))),
        "any_artisan": _int(form.get("any_artisan"), 3),
        "any_gaming": _int(form.get("any_gaming"), 3),
        "any_instrument": _int(form.get("any_instrument"), 3),
        "languages": _split(str(form.get("languages", ""))),
        "any_languages": _int(form.get("any_languages"), 4),
        "equipment": equipment,
        "gold": _int(form.get("gold"), 100000),
        "feature_name": str(form.get("feature_name", "")).strip()[:120],
        "feature_text": str(form.get("feature_text", "")).strip()[:4000],
        "text": str(form.get("text", "")).strip()[:4000],
    }
    if "feat" in form:  # the 2024 part is only on the form for a 2024 background
        fields["abilities"] = [str(x) for x in form.getlist("abilities")]
        fields["feat"] = str(form.get("feat", "")).strip()[:200]
    return BackgroundDesign.model_validate({**design.model_dump(), **fields})


@router.get(BACKGROUNDS_URL, response_class=HTMLResponse)
async def list_backgrounds(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, deleted: str = ""
) -> HTMLResponse:
    """Every design."""
    return _list_page(
        page, registry, notice_text=page.t("itembuilder.deleted") if deleted else None
    )


@router.post(BACKGROUNDS_URL, response_class=HTMLResponse)
async def create_background(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request
) -> Response:
    """Begin a design in the table's world: blank, or from a printed background."""
    form = await request.form()
    world_id = registry.table.world_id()
    name = str(form.get("name", ""))
    typed = str(form.get("based_on", ""))
    try:
        design = registry.background_builder.create(
            name, world_id, _resolve(registry, world_id, typed)
        )
    except BackgroundError as exc:
        draft = {"name": name, "based_on": typed}
        return _list_page(page, registry, error=page.t.error(exc), draft=draft, status_code=422)
    return redirect(background_url(design.id))


@router.get(f"{BACKGROUNDS_URL}/{{design_id}}", response_class=HTMLResponse)
async def show_background(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    design_id: str,
    published: str = "",
    worlds: str = "",
) -> HTMLResponse:
    """The builder."""
    design = _design(registry, design_id)
    notice_text = None
    if published:
        notice_text = page.t.plural("bgbuilder.published_n", int(published))
        if worlds:
            notice_text += " " + page.t("itembuilder.homebrew_added", worlds=worlds)
    return _builder(page, registry, design, notice_text=notice_text)


@router.post(f"{BACKGROUNDS_URL}/{{design_id}}", response_class=HTMLResponse)
async def change_background(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request, design_id: str
) -> Response:
    """Read the form, store it; ``publish`` and ``accept`` also write the homebrew."""
    builder = registry.background_builder
    form = await request.form()
    action = str(form.get("action", "save"))
    design = _read_form(_design(registry, design_id), form, registry)
    if action == "accept":
        design = design.model_copy(update={"drafted_by": None})
    try:
        builder.save(design)
    except BackgroundError as exc:
        return _builder(page, registry, design, error=page.t.error(exc), status_code=422)
    if action in ("publish", "accept"):
        done = builder.publish()
        query = {"published": str(done.count)}
        if done.worlds:
            query["worlds"] = ", ".join(done.worlds)
        return redirect(f"{background_url(design_id)}?{urlencode(query)}")
    return redirect(background_url(design_id))


@router.get(f"{BACKGROUNDS_URL}/{{design_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_background(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, design_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(delete_page(page, _design(registry, design_id)))


@router.post(f"{BACKGROUNDS_URL}/{{design_id}}/delete")
async def delete_background(_gm: GamemasterDep, registry: RegistryDep, design_id: str) -> Response:
    """Delete a design; a published one leaves the homebrew file."""
    registry.background_builder.delete(_design(registry, design_id).id)
    return redirect(f"{BACKGROUNDS_URL}?deleted=1")


__all__ = ["router"]
