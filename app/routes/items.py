"""The item builder (app/services/item_builder.py).

* ``GET  /gm/items``               — the designs; ``POST`` begins one (``name``, ``kind``,
  ``based_on`` a printed magic item by name or ``Name|SOURCE``)
* ``GET  /gm/items/{id}``          — the builder
* ``POST /gm/items/{id}``          — the whole form, with ``action``: ``save``,
  ``publish`` or ``accept`` (Claude's draft taken in and published)
* ``GET|POST /gm/items/{id}/delete``

Every action keeps what was typed: the form is read into the design, the action applies,
the design is stored. Only ``publish`` and ``accept`` write the homebrew file.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import ItemDesign, ItemSpell
from app.routes.auth import html, redirect
from app.services.item_builder import ABILITIES, BASED, ItemError, ItemNotFound
from app.services.references import ReferenceEntry
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.item_builder_pages import (
    ITEMS_URL,
    ItemRow,
    ItemView,
    builder_page,
    delete_page,
    item_url,
    items_page,
)
from app.ui.tags import localized_context

router = APIRouter(tags=["item builder"])


def _int(raw: object, low: int, high: int) -> int:
    text = str(raw or "").strip()
    return max(low, min(high, int(text))) if text.lstrip("-").isdigit() else 0


def _design(registry: StoreRegistry, design_id: str) -> ItemDesign:
    """A design, or 404."""
    try:
        return registry.item_builder.get(design_id)
    except ItemNotFound as exc:
        raise HTTPException(status_code=404) from exc


def _list_page(
    page: Page,
    registry: StoreRegistry,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    builder = registry.item_builder
    names = {w.id: w.name for w in registry.worlds.list()}
    rows = [
        ItemRow(d, names.get(d.world_id or "", ""), builder.unpublished(d)) for d in builder.list()
    ]
    return html(
        items_page(page, rows, notice_text=notice_text, error=error, draft=draft),
        status_code=status_code,
    )


def _bases(registry: StoreRegistry, world_id: str | None) -> list[tuple[str, str, str]]:
    """The base weapons, armour and shields of the world's books: ``(ref, label, type)``."""
    world = registry.worlds.find(world_id) if world_id else None
    codes = {c for kinds in BASED.values() for c in kinds}
    out = []
    for entry in registry.compendium.all("baseitem"):
        data = entry.model_dump(by_alias=True, exclude_none=True)
        code = str(data.get("type") or "").partition("|")[0]
        source = str(data.get("source") or "")
        if code not in codes or data.get("age") or (world and not world.source_set.allows(source)):
            continue
        name = str(data.get("name"))
        out.append((f"{name}|{source}", f"{name} ({source})", code))
    return sorted(out, key=lambda x: x[1].casefold())


def _builder(
    page: Page,
    registry: StoreRegistry,
    design: ItemDesign,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    builder = registry.item_builder
    world = registry.worlds.find(design.world_id) if design.world_id else None
    try:
        data = builder.item(design)
    except ItemError:
        data = {"name": design.name, "source": "Homebrew", "entries": [design.text]}
    view = ItemView(
        design=design,
        worlds=registry.worlds.list(),
        bases=_bases(registry, design.world_id),
        preview=ReferenceEntry("item", data, design.world_id),
        value=builder.value(design),
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
    """A printed magic item typed by name (or ``Name|SOURCE``), as the world's books
    resolve it; what resolves to nothing is left as typed, for the builder to refuse."""
    name, _, source = typed.strip().partition("|")
    if not name.strip():
        return ""
    world = registry.worlds.find(world_id) if world_id else None
    found = (
        registry.references.resolver()
        .resolve_parts(
            "item", name.strip(), source.strip() or None, world.source_set if world else None
        )
        .entry
    )
    if found is None:
        return typed.strip()
    return f"{found.name}|{getattr(found, 'source', '')}"


def _read_form(design: ItemDesign, form: FormData, registry: StoreRegistry) -> ItemDesign:
    """The whole form into the design; what is left out keeps its value."""
    world = str(form.get("world", ""))
    spells = [
        ItemSpell(spell=str(s).strip()[:200], charges=_int(c, 0, 20))
        for s, c in zip(form.getlist("spell"), form.getlist("spell_charges"), strict=False)
        if str(s).strip()
    ]
    weight = str(form.get("weight", "")).strip()
    value = str(form.get("value", "")).strip()
    fields: dict[str, object] = {
        "name": str(form.get("name", design.name)),
        "world_id": world if registry.worlds.find(world) else None,
        "kind": str(form.get("kind", design.kind)),
        "base_item": str(form.get("base_item", "")),
        "rarity": str(form.get("rarity", design.rarity)),
        "attunement": bool(form.get("attunement")),
        "attune_by": str(form.get("attune_by", "")).strip()[:120],
        "resist": [str(x) for x in form.getlist("resist")],
        "immune": [str(x) for x in form.getlist("immune")],
        "vulnerable": [str(x) for x in form.getlist("vulnerable")],
        "ability_set": {k: _int(form.get(f"set_{k}"), 0, 30) for k in ABILITIES},
        "ability_raise": {k: _int(form.get(f"raise_{k}"), 0, 4) for k in ABILITIES},
        "charges": _int(form.get("charges"), 0, 100),
        "recharge": str(form.get("recharge", "")),
        "recharge_amount": str(form.get("recharge_amount", "")).replace(" ", "")[:40],
        "spells": spells,
        "text": str(form.get("text", "")).strip()[:8000],
        "weight": float(weight) if weight.replace(".", "", 1).isdigit() else None,
        "value": int(value) * 100 if value.isdigit() else None,
    }
    for key in (
        "bonus_weapon",
        "bonus_ac",
        "bonus_saving_throw",
        "bonus_spell_attack",
        "bonus_spell_save_dc",
        "bonus_ability_check",
    ):
        fields[key] = _int(form.get(key), 0, 3)
    return ItemDesign.model_validate({**design.model_dump(), **fields})


@router.get(ITEMS_URL, response_class=HTMLResponse)
async def list_items(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, deleted: str = ""
) -> HTMLResponse:
    """Every design."""
    return _list_page(
        page, registry, notice_text=page.t("itembuilder.deleted") if deleted else None
    )


@router.post(ITEMS_URL, response_class=HTMLResponse)
async def create_item(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request
) -> Response:
    """Begin a design in the table's world and open it: blank of a kind, or from a
    printed magic item of the world's books."""
    form = await request.form()
    world_id = registry.table.world_id()
    name = str(form.get("name", ""))
    kind = str(form.get("kind", "wondrous"))
    typed = str(form.get("based_on", ""))
    based_on = _resolve(registry, world_id, typed)
    # A weapon, armour, shield or ammunition begun blank: on the first base of its kind
    # in the world's books, changed in the builder.
    base = ""
    if not based_on and kind in BASED and kind != "staff":
        bases = [b for b in _bases(registry, world_id) if b[2] in BASED[kind]]
        base = bases[0][0] if bases else ""
    try:
        design = registry.item_builder.create(name, world_id, kind, based_on, base)
    except ItemError as exc:
        draft = {"name": name, "kind": kind, "based_on": typed}
        return _list_page(page, registry, error=page.t.error(exc), draft=draft, status_code=422)
    return redirect(item_url(design.id))


@router.get(f"{ITEMS_URL}/{{design_id}}", response_class=HTMLResponse)
async def show_item(
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
    notice_text = page.t("itembuilder.revision_accepted") if revised else None
    if published:
        notice_text = page.t.plural("itembuilder.published_n", int(published))
        if worlds:
            notice_text += " " + page.t("itembuilder.homebrew_added", worlds=worlds)
    return _builder(page, registry, design, notice_text=notice_text)


@router.post(f"{ITEMS_URL}/{{design_id}}", response_class=HTMLResponse)
async def change_item(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request, design_id: str
) -> Response:
    """Read the form, store it; ``publish`` and ``accept`` also write the homebrew."""
    builder = registry.item_builder
    form = await request.form()
    action = str(form.get("action", "save"))
    before = _design(registry, design_id)
    design = _read_form(before, form, registry)
    if action == "accept" and before.revision_of:  # written onto its item
        try:
            builder.save(design)
        except ItemError as exc:
            return _builder(page, registry, design, error=page.t.error(exc), status_code=422)
        accepted = builder.accept_revision(design.id)
        return redirect(f"{item_url(accepted.id)}?revised=1")
    if action == "accept":
        design = design.model_copy(update={"drafted_by": None})
    try:
        builder.save(design)
    except ItemError as exc:
        return _builder(page, registry, design, error=page.t.error(exc), status_code=422)
    if action in ("publish", "accept"):
        done = builder.publish()
        query = {"published": str(done.count)}
        if done.worlds:
            query["worlds"] = ", ".join(done.worlds)
        return redirect(f"{item_url(design_id)}?{urlencode(query)}")
    return redirect(item_url(design_id))


@router.get(f"{ITEMS_URL}/{{design_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_item(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, design_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(delete_page(page, _design(registry, design_id)))


@router.post(f"{ITEMS_URL}/{{design_id}}/delete")
async def delete_item(_gm: GamemasterDep, registry: RegistryDep, design_id: str) -> Response:
    """Delete a design; a published one leaves the homebrew file."""
    registry.item_builder.delete(_design(registry, design_id).id)
    return redirect(f"{ITEMS_URL}?deleted=1")


__all__ = ["router"]
