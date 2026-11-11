"""The creature builder (app/services/creature_builder.py).

* ``GET  /gm/creatures``               — the designs; ``POST`` starts one (``name``,
  ``world``, ``cr``, ``based_on=Name|SOURCE``)
* ``GET  /gm/creatures/{id}``          — the builder
* ``POST /gm/creatures/{id}``          — the whole form, with ``action``: ``save``,
  ``publish``, ``fit``, ``add:<kind>``, ``remove:<kind>:<i>``, ``attack``,
  ``template:trait|action``, ``feature``, ``unfeature:<i>``
* ``GET|POST /gm/creatures/{id}/delete``

Every action keeps what was typed: the form is read into the design first, then the
action applies, then the design is stored. Only ``publish`` writes the homebrew file.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import CreatureBlock, CreatureDesign
from app.routes.auth import html, redirect
from app.services.creature_builder import (
    ABILITIES,
    ALIGNMENTS,
    BLOCK_KINDS,
    SIZES,
    SPEEDS,
    TYPES,
    CreatureError,
    attack_text,
)
from app.services.references import ReferenceEntry
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.creature_pages import (
    CREATURES_URL,
    BuilderView,
    DesignRow,
    builder_page,
    creature_url,
    creatures_page,
    delete_page,
)
from app.ui.tags import localized_context

router = APIRouter(tags=["creature builder"])

MAX_TEXT = 4000


def _int(raw: object, default: int | None, low: int, high: int) -> int | None:
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return max(low, min(high, value))


def _list(raw: object) -> list[str]:
    seen: list[str] = []
    for part in str(raw or "").split(","):
        value = " ".join(part.split()).lower()
        if value and value not in seen:
            seen.append(value)
    return seen


def _read_form(design: CreatureDesign, form: FormData, registry: StoreRegistry) -> CreatureDesign:
    """The design as the form has it; values out of range are brought into it."""
    worlds = {w.id for w in registry.worlds.list()}
    crs = {r.cr for r in registry.creature_tables.rows()}
    changes: dict[str, object] = {
        "name": str(form.get("name", design.name))[:80],
        "world_id": (w if (w := str(form.get("world", ""))) in worlds else None),
        "size": s if (s := str(form.get("size", ""))) in SIZES else design.size,
        "type": x if (x := str(form.get("type", ""))) in TYPES else design.type,
        "type_tags": _list(form.get("type_tags", "")),
        "alignment": (
            al.split() if (al := str(form.get("alignment", ""))) in ALIGNMENTS else design.alignment
        ),
        "cr": c if (c := str(form.get("cr", ""))) in crs else design.cr,
        "ac": _int(form.get("ac"), design.ac, 0, 40),
        "ac_from": str(form.get("ac_from", ""))[:120].strip(),
        "hit_dice": _int(form.get("hit_dice"), design.hit_dice, 1, 200),
        "speed": {
            k: v
            for k in SPEEDS
            if (v := _int(form.get(f"speed_{k}", ""), 0, 0, 200) or 0) > 0 or k == "walk"
        },
        "abilities": {
            ab: _int(form.get(f"ab_{ab}"), design.abilities.get(ab, 10), 1, 30) for ab in ABILITIES
        },
        "saves": [ab for ab in ABILITIES if ab in form.getlist("saves")],
        "skills": _list(form.get("skills", "")),
        "senses": str(form.get("senses", ""))[:200].strip(),
        "languages": str(form.get("languages", ""))[:200].strip(),
        "resist": _list(form.get("resist", "")),
        "immune": _list(form.get("immune", "")),
        "vulnerable": _list(form.get("vulnerable", "")),
        "condition_immune": _list(form.get("condition_immune", "")),
        "dpr_override": _int(form.get("dpr_override", ""), None, 0, 2000),
    }
    for kind in BLOCK_KINDS:
        names = [str(x) for x in form.getlist(f"{kind}_name")]
        texts = [str(x) for x in form.getlist(f"{kind}_text")]
        changes[kind] = [
            CreatureBlock(name=n.strip()[:80], text=x.strip()[:MAX_TEXT])
            for n, x in zip(names, texts, strict=False)
        ]
    return design.model_copy(update=changes)


def _apply(
    action: str, design: CreatureDesign, form: FormData, registry: StoreRegistry
) -> CreatureDesign:
    """One builder button."""
    service = registry.creatures
    if action == "fit":
        return service.math.fit(design)
    if action == "attack":
        text = attack_text(
            ranged=form.get("attack_kind") == "ranged",
            ability=str(form.get("attack_ability", "str")),
            dice=str(form.get("attack_dice", "1d8")),
            damage_type=str(form.get("attack_type", "bludgeoning")),
        )
        name = str(form.get("attack_name", "")).strip()[:80] or "Attack"
        design.actions.append(CreatureBlock(name=name, text=text))
    elif action.startswith("add:"):
        kind = action.removeprefix("add:")
        if kind in BLOCK_KINDS:
            getattr(design, kind).append(CreatureBlock(name=""))
    elif action.startswith("remove:"):
        _, kind, index = (action.split(":") + ["", ""])[:3]
        blocks = getattr(design, kind, None) if kind in BLOCK_KINDS else None
        if blocks is not None and index.isdigit() and int(index) < len(blocks):
            del blocks[int(index)]
    elif action in ("template:trait", "template:action"):
        kind = action.removeprefix("template:")
        service.add_template(design, kind, str(form.get(f"template_{kind}", "")))
    elif action == "feature":
        value = _int(form.get("feature_value", ""), None, 0, 999)
        service.add_feature(design, str(form.get("feature", "")), value)
    elif action.startswith("unfeature:"):
        index = action.removeprefix("unfeature:")
        if index.isdigit() and int(index) < len(design.features):
            del design.features[int(index)]
    return design


def _list_page(
    page: Page,
    registry: StoreRegistry,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    service = registry.creatures
    worlds = registry.worlds.list()
    names = {w.id: w.name for w in worlds}
    rows = []
    for design in service.list():
        estimate = service.math.estimate(design)
        rows.append(
            DesignRow(
                design,
                estimate.final if estimate and estimate.final != design.cr else None,
                names.get(design.world_id or "", ""),
                service.unpublished(design),
            )
        )
    return html(
        creatures_page(
            page,
            rows,
            worlds,
            registry.creature_tables.rows(),
            notice_text=notice_text,
            error=error,
            draft=draft,
        ),
        status_code=status_code,
    )


def _builder(
    page: Page,
    registry: StoreRegistry,
    design: CreatureDesign,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    service = registry.creatures
    tables = registry.creature_tables
    world = registry.worlds.find(design.world_id)
    view = BuilderView(
        design=design,
        worlds=registry.worlds.list(),
        crs=tables.rows(),
        estimate=service.math.estimate(design),
        preview=ReferenceEntry("monster", service.math.statblock(design), design.world_id),
        hp=service.math.hp(design),
        traits=tables.traits(),
        actions=tables.actions(),
        features=tables.features(),
        unpublished=service.unpublished(design),
        published=design.published_at is not None,
    )
    ctx = localized_context(
        page.t,
        resolver=registry.references.resolver(),
        source_set=world.source_set if world else None,
        world_id=design.world_id,
    )
    return html(
        builder_page(page, view, ctx, notice_text=notice_text, error=error),
        status_code=status_code,
    )


@router.get(CREATURES_URL, response_class=HTMLResponse)
async def list_creatures(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, deleted: str = ""
) -> HTMLResponse:
    """Every design."""
    return _list_page(page, registry, notice_text=page.t("creatures.deleted") if deleted else None)


@router.post(CREATURES_URL, response_class=HTMLResponse)
async def create_creature(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    name: Annotated[str, Form()] = "",
    world: Annotated[str, Form()] = "",
    cr: Annotated[str, Form()] = "1",
    based_on: Annotated[str, Form()] = "",
) -> Response:
    """Start a design and open it."""
    world_id = world if registry.worlds.find(world) else None
    try:
        design = registry.creatures.create(name, world_id, cr, based_on)
    except CreatureError as exc:
        draft = {"name": name, "world": world, "cr": cr, "based_on": based_on}
        return _list_page(page, registry, error=page.t.error(exc), draft=draft, status_code=422)
    return redirect(creature_url(design.id))


@router.get(f"{CREATURES_URL}/{{design_id}}", response_class=HTMLResponse)
async def show_creature(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    design_id: str,
    published: str = "",
    worlds: str = "",
) -> HTMLResponse:
    """The builder."""
    design = registry.creatures.get(design_id)
    notice_text = None
    if published:
        notice_text = page.t.plural("creatures.published_n", int(published))
        if worlds:
            notice_text += " " + page.t("creatures.homebrew_added", worlds=worlds)
    return _builder(page, registry, design, notice_text=notice_text)


@router.post(f"{CREATURES_URL}/{{design_id}}", response_class=HTMLResponse)
async def change_creature(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    request: Request,
    design_id: str,
) -> Response:
    """Read the form, apply the button, store; ``publish`` also writes the homebrew."""
    service = registry.creatures
    form = await request.form()
    action = str(form.get("action", "save"))
    design = _read_form(service.get(design_id), form, registry)
    design = _apply(action, design, form, registry)
    try:
        service.save(design)
    except CreatureError as exc:
        return _builder(page, registry, design, error=page.t.error(exc), status_code=422)
    if action == "publish":
        done = service.publish()
        query = {"published": str(done.count)}
        if done.worlds:
            query["worlds"] = ", ".join(done.worlds)
        return redirect(f"{creature_url(design_id)}?{urlencode(query)}")
    anchor = ""
    if action.startswith(("add:", "remove:")):
        anchor = f"#cb-{action.split(':')[1]}"
    elif action in ("attack", "template:action"):
        anchor = "#cb-actions"
    elif action in ("feature", "template:trait") or action.startswith("unfeature:"):
        anchor = "#cb-add" if action != "template:trait" else "#cb-traits"
    return redirect(f"{creature_url(design_id)}{anchor}")


@router.get(f"{CREATURES_URL}/{{design_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_creature(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, design_id: str
) -> HTMLResponse:
    """Ask first."""
    return html(delete_page(page, registry.creatures.get(design_id)))


@router.post(f"{CREATURES_URL}/{{design_id}}/delete")
async def delete_creature(_gm: GamemasterDep, registry: RegistryDep, design_id: str) -> Response:
    """Delete a design; if it was published, its statblock leaves the homebrew file."""
    design = registry.creatures.delete(design_id)
    if design.published_at is not None:
        registry.creatures.publish()
    return redirect(f"{CREATURES_URL}?deleted=1")


__all__ = ["router"]
