"""The spell builder (app/services/spell_builder.py).

* ``GET  /gm/spells``               — the designs; ``POST`` begins one (``name``,
  ``based_on`` a printed spell by name or ``Name|SOURCE``)
* ``GET  /gm/spells/{id}``          — the builder
* ``POST /gm/spells/{id}``          — the whole form, with ``action``: ``save``,
  ``publish`` or ``accept`` (Claude's draft taken in and published)
* ``GET|POST /gm/spells/{id}/delete``
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import SpellDesign
from app.routes.auth import html, redirect
from app.services.references import ReferenceEntry
from app.services.spell_builder import SpellError, SpellNotFound
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.item_links import SPELLS_URL, spell_url
from app.ui.spell_builder_pages import (
    SpellRow,
    SpellView,
    builder_page,
    delete_page,
    spells_page,
)
from app.ui.tags import localized_context

router = APIRouter(tags=["spell builder"])


def _int(raw: object, default: int, low: int, high: int) -> int:
    text = str(raw or "").strip()
    return max(low, min(high, int(text))) if text.isdigit() else default


def _design(registry: StoreRegistry, design_id: str) -> SpellDesign:
    try:
        return registry.spell_builder.get(design_id)
    except SpellNotFound as exc:
        raise HTTPException(status_code=404) from exc


def _classes(registry: StoreRegistry, world_id: str | None) -> list[tuple[str, str]]:
    """The spellcasting classes of the world's books: ``(Name|SOURCE, label)``."""
    world = registry.worlds.find(world_id) if world_id else None
    out = []
    for entry in registry.compendium.all("class"):
        data = entry.model_dump(by_alias=True, exclude_none=True)
        source = str(data.get("source") or "")
        if not data.get("casterProgression") and not data.get("spellcastingAbility"):
            continue
        if data.get("isSidekick") or (world and not world.source_set.allows(source)):
            continue
        name = str(data.get("name"))
        out.append((f"{name}|{source}", f"{name} ({source})"))
    return sorted(set(out), key=lambda x: x[1].casefold())


def _list_page(
    page: Page,
    registry: StoreRegistry,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    builder = registry.spell_builder
    names = {w.id: w.name for w in registry.worlds.list()}
    rows = [
        SpellRow(d, names.get(d.world_id or "", ""), builder.unpublished(d)) for d in builder.list()
    ]
    return html(
        spells_page(page, rows, notice_text=notice_text, error=error, draft=draft),
        status_code=status_code,
    )


def _builder(
    page: Page,
    registry: StoreRegistry,
    design: SpellDesign,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    builder = registry.spell_builder
    world = registry.worlds.find(design.world_id) if design.world_id else None
    view = SpellView(
        design=design,
        worlds=registry.worlds.list(),
        classes=_classes(registry, design.world_id),
        preview=ReferenceEntry("spell", builder.spell(design), design.world_id),
        unpublished=builder.unpublished(design),
        published=design.published_at is not None,
        original=builder.get(design.revision_of) if design.revision_of else None,
        revision=builder.revision(design.id),
        changes=builder.changes(design) if design.revision_of else [],
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
            "spell", name.strip(), source.strip() or None, world.source_set if world else None
        )
        .entry
    )
    return f"{found.name}|{getattr(found, 'source', '')}" if found is not None else typed.strip()


def _read_form(design: SpellDesign, form: FormData, registry: StoreRegistry) -> SpellDesign:
    world = str(form.get("world", ""))
    fields: dict[str, object] = {
        "name": str(form.get("name", design.name)),
        "world_id": world if registry.worlds.find(world) else None,
        "level": _int(form.get("level"), design.level, 0, 9),
        "school": str(form.get("school", design.school)),
        "time_number": _int(form.get("time_number"), 1, 1, 99),
        "time_unit": str(form.get("time_unit", design.time_unit)),
        "range_kind": str(form.get("range_kind", design.range_kind)),
        "range_amount": _int(form.get("range_amount"), 0, 0, 100000),
        "area": str(form.get("area", "")),
        "area_size": _int(form.get("area_size"), 0, 0, 10000),
        "verbal": bool(form.get("verbal")),
        "somatic": bool(form.get("somatic")),
        "material": str(form.get("material", "")).strip()[:300],
        "material_cost": _int(form.get("material_cost"), 0, 0, 10_000_000),
        "material_consumed": bool(form.get("material_consumed")),
        "duration_kind": str(form.get("duration_kind", design.duration_kind)),
        "duration_amount": _int(form.get("duration_amount"), 1, 1, 999),
        "duration_unit": str(form.get("duration_unit", design.duration_unit)),
        "concentration": bool(form.get("concentration")),
        "ritual": bool(form.get("ritual")),
        "classes": [str(x) for x in form.getlist("classes")],
        "effect": str(form.get("effect", design.effect)),
        "save": str(form.get("save", design.save)),
        "half_on_save": bool(form.get("half_on_save")),
        "attack": str(form.get("attack", design.attack)),
        "dice": str(form.get("dice", "")).replace(" ", "")[:40],
        "damage_types": [str(x) for x in form.getlist("damage_types")],
        "heal_mod": bool(form.get("heal_mod")),
        "heal_targets": _int(form.get("heal_targets"), 1, 1, 12),
        "upcast": str(form.get("upcast", "")).replace(" ", "")[:40],
        "write_effect": bool(form.get("write_effect")),
        "text": str(form.get("text", "")).strip()[:8000],
        "higher_text": str(form.get("higher_text", "")).strip()[:2000],
    }
    return SpellDesign.model_validate({**design.model_dump(), **fields})


@router.get(SPELLS_URL, response_class=HTMLResponse)
async def list_spells(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, deleted: str = ""
) -> HTMLResponse:
    """Every design."""
    return _list_page(
        page, registry, notice_text=page.t("itembuilder.deleted") if deleted else None
    )


@router.post(SPELLS_URL, response_class=HTMLResponse)
async def create_spell(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request
) -> Response:
    """Begin a design in the table's world: blank, or from a printed spell."""
    form = await request.form()
    world_id = registry.table.world_id()
    name = str(form.get("name", ""))
    typed = str(form.get("based_on", ""))
    try:
        design = registry.spell_builder.create(name, world_id, _resolve(registry, world_id, typed))
    except SpellError as exc:
        draft = {"name": name, "based_on": typed}
        return _list_page(page, registry, error=page.t.error(exc), draft=draft, status_code=422)
    return redirect(spell_url(design.id))


@router.get(f"{SPELLS_URL}/{{design_id}}", response_class=HTMLResponse)
async def show_spell(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    design_id: str,
    published: str = "",
    worlds: str = "",
    revised: str = "",
) -> HTMLResponse:
    """The builder."""
    design = _design(registry, design_id)
    notice_text = page.t("spellbuilder.revision_accepted") if revised else None
    if published:
        notice_text = page.t.plural("spellbuilder.published_n", int(published))
        if worlds:
            notice_text += " " + page.t("itembuilder.homebrew_added", worlds=worlds)
    return _builder(page, registry, design, notice_text=notice_text)


@router.post(f"{SPELLS_URL}/{{design_id}}", response_class=HTMLResponse)
async def change_spell(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request, design_id: str
) -> Response:
    """Read the form, store it; ``publish`` and ``accept`` also write the homebrew."""
    builder = registry.spell_builder
    form = await request.form()
    action = str(form.get("action", "save"))
    before = _design(registry, design_id)
    design = _read_form(before, form, registry)
    if action == "accept" and before.revision_of:  # written onto its spell
        try:
            builder.save(design)
        except SpellError as exc:
            return _builder(page, registry, design, error=page.t.error(exc), status_code=422)
        accepted = builder.accept_revision(design.id)
        return redirect(f"{spell_url(accepted.id)}?revised=1")
    if action == "accept":
        design = design.model_copy(update={"drafted_by": None})
    try:
        builder.save(design)
    except SpellError as exc:
        return _builder(page, registry, design, error=page.t.error(exc), status_code=422)
    if action in ("publish", "accept"):
        done = builder.publish()
        query = {"published": str(done.count)}
        if done.worlds:
            query["worlds"] = ", ".join(done.worlds)
        return redirect(f"{spell_url(design_id)}?{urlencode(query)}")
    return redirect(spell_url(design_id))


@router.get(f"{SPELLS_URL}/{{design_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_spell(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, design_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(delete_page(page, _design(registry, design_id)))


@router.post(f"{SPELLS_URL}/{{design_id}}/delete")
async def delete_spell(_gm: GamemasterDep, registry: RegistryDep, design_id: str) -> Response:
    """Delete a design; a published one leaves the homebrew files."""
    registry.spell_builder.delete(_design(registry, design_id).id)
    return redirect(f"{SPELLS_URL}?deleted=1")


__all__ = ["router"]
