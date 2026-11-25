"""The item builder's pages (app/services/item_builder.py): the designs, a dialog that
begins one, and the builder — one form for what the sheet applies and the text, with the
item as it will read beside it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    fieldset,
    form,
    label,
    legend,
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import ItemDesign, World
from app.services.item_builder import (
    ABILITIES,
    BASED,
    DAMAGE_TYPES,
    KINDS,
    RARITIES,
    RECHARGES,
)
from app.services.references import ReferenceEntry
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.item_links import ITEMS_URL, item_url
from app.ui.layout import app_shell
from app.ui.library_links import confirm_page
from app.ui.ref_pages import statblock
from app.ui.shop_pages import coin_text
from app.ui.tags import RenderContext
from app.ui.tool_dialog import tool_dialog

BONUSES = (
    "bonus_weapon",
    "bonus_ac",
    "bonus_saving_throw",
    "bonus_spell_attack",
    "bonus_spell_save_dc",
    "bonus_ability_check",
)
SPELL_ROWS = 3  # empty rows offered for more spells


@dataclass
class ItemRow:
    design: ItemDesign
    world: str
    unpublished: bool


@dataclass
class ItemView:
    """What the builder shows of a design."""

    design: ItemDesign
    worlds: Sequence[World]
    bases: Sequence[tuple[str, str, str]]  # (ref, label, type code)
    preview: ReferenceEntry
    value: int | None
    unpublished: bool
    published: bool
    original: ItemDesign | None = None  # what a revision changes
    revision: ItemDesign | None = None  # Claude's open revision of this design
    changes: Sequence[str] = ()


def items_page(
    page: Page,
    rows: Sequence[ItemRow],
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
) -> Element:
    """Every design, and **New item**: a dialog that begins one in the table's world."""
    t = page.t
    draft = draft or {}
    listed = [
        li(class_="list-row")[
            icon("gem", "sm"),
            div(class_="list-main")[
                a(href=item_url(r.design.id), class_="list-title list-link")[r.design.name],
                span(class_="list-meta")[
                    " · ".join(
                        x
                        for x in (
                            t(f"itembuilder.kind.{r.design.kind}"),
                            t(f"itembuilder.rarity.{r.design.rarity}"),
                            r.world,
                            t("itembuilder.based_on", ref=r.design.based_on)
                            if r.design.based_on
                            else "",
                        )
                        if x
                    )
                ],
            ],
            span(class_="pill accent", title=t("itembuilder.revision_hint"))[
                t("itembuilder.revision")
            ]
            if r.design.revision_of
            else span(class_="pill accent", title=t("itembuilder.draft_hint"))[
                t("itembuilder.draft")
            ]
            if r.design.drafted_by
            else span(class_="pill cond")[t("itembuilder.unpublished")]
            if r.unpublished
            else None,
        ]
        for r in rows
    ]
    create = tool_dialog(
        page, "item", t("itembuilder.new"), _new_form(page, draft, error), is_open=error is not None
    )
    return app_shell(
        page,
        t("itembuilder.heading"),
        [
            page_head(
                t("gm.eyebrow"),
                t("itembuilder.heading"),
                t("itembuilder.lede"),
                a(href="?tool=item", class_="btn btn-primary", data_dialog="tool-item")[
                    icon("plus"), span[t("itembuilder.new")]
                ],
            ),
            notice(notice_text),
            ul(class_="list")[listed]
            if listed
            else empty_state("gem", t("itembuilder.empty_heading"), t("itembuilder.empty_text")),
            create,
        ],
    )


def _new_form(page: Page, draft: dict[str, str], error: str | None) -> Element:
    t = page.t
    return form(method="post", action=ITEMS_URL, class_="stack")[
        alert(error),
        label(class_="field")[
            span(class_="field-label")[t("itembuilder.name")],
            input_el(type="text", name="name", maxlength="80", value=draft.get("name", "")),
        ],
        label(class_="field")[
            span(class_="field-label")[t("itembuilder.kind")],
            select(name="kind")[
                [
                    option(value=k, selected=draft.get("kind", "wondrous") == k)[
                        t(f"itembuilder.kind.{k}")
                    ]
                    for k in KINDS
                ]
            ],
            span(class_="field-hint")[t("itembuilder.kind_hint")],
        ],
        label(class_="field")[
            span(class_="field-label")[t("itembuilder.from_item")],
            input_el(
                type="text",
                name="based_on",
                maxlength="200",
                value=draft.get("based_on", ""),
                placeholder=t("itembuilder.from_item_hint"),
            ),
            span(class_="field-hint")[t("itembuilder.from_item_note")],
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("plus", "sm"), span[t("itembuilder.begin")]
            ]
        ],
    ]


def _number(name: str, value: int | float | None, low: int, high: int, **extra: str) -> Node:
    return input_el(
        type="number",
        name=name,
        min=str(low),
        max=str(high),
        value="" if value in (None, 0) else str(value),
        inputmode="numeric",
        **extra,
    )


def _field(caption: str, *content: Node, hint: str | None = None) -> Element:
    return label(class_="field")[
        span(class_="field-label")[caption],
        content,
        span(class_="field-hint")[hint] if hint else None,
    ]


def builder_page(
    page: Page,
    view: ItemView,
    ctx: RenderContext,
    *,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """The builder: one form, the item as it will read beside it."""
    t = page.t
    d = view.design
    url = item_url(d.id)
    kind_bases = [x for x in view.bases if x[2] in BASED.get(d.kind, ())]
    basics = fieldset(class_="ib-section")[
        legend[t("itembuilder.basics")],
        div(class_="ib-grid")[
            _field(t("itembuilder.name"), input_el(type="text", name="name", value=d.name)),
            _field(
                t("itembuilder.world"),
                select(name="world")[
                    option(value="", selected=not d.world_id)[t("itembuilder.no_world")],
                    [option(value=w.id, selected=w.id == d.world_id)[w.name] for w in view.worlds],
                ],
            ),
            _field(
                t("itembuilder.kind"),
                select(name="kind")[
                    [
                        option(value=k, selected=d.kind == k)[t(f"itembuilder.kind.{k}")]
                        for k in KINDS
                    ]
                ],
            ),
            _field(
                t("itembuilder.base_item"),
                select(name="base_item")[
                    option(value="")[t("itembuilder.choose_base")],
                    [
                        option(value=ref, selected=ref == d.base_item)[name]
                        for ref, name, _code in kind_bases
                    ],
                ],
                hint=t("itembuilder.base_hint"),
            )
            if d.kind in BASED
            else None,
            _field(
                t("itembuilder.rarity"),
                select(name="rarity")[
                    [
                        option(value=r, selected=d.rarity == r)[t(f"itembuilder.rarity.{r}")]
                        for r in RARITIES
                    ]
                ],
            ),
            div(class_="field")[
                label(class_="cs-heal-pick")[
                    input_el(type="checkbox", name="attunement", value="1", checked=d.attunement),
                    span[t("itembuilder.attunement")],
                ],
                input_el(
                    type="text",
                    name="attune_by",
                    maxlength="120",
                    value=d.attune_by,
                    placeholder=t("itembuilder.attune_by"),
                    aria_label=t("itembuilder.attune_by"),
                ),
            ],
            _field(
                t("itembuilder.weight"),
                input_el(
                    type="number",
                    name="weight",
                    min="0",
                    step="0.1",
                    value="" if d.weight is None else f"{d.weight:g}",
                ),
            ),
            _field(
                t("itembuilder.value"),
                input_el(
                    type="number",
                    name="value",
                    min="0",
                    value="" if d.value is None else str(d.value // 100),
                ),
                hint=t("itembuilder.value_hint", value=coin_text(page, view.value))
                if view.value and d.value is None
                else t("itembuilder.value_free"),
            ),
        ],
    ]
    bonuses = fieldset(class_="ib-section")[
        legend[t("itembuilder.bonuses")],
        p(class_="field-hint")[t("itembuilder.bonuses_hint")],
        div(class_="ib-grid ib-small")[
            [_field(t(f"itembuilder.{k}"), _number(k, getattr(d, k), 0, 3)) for k in BONUSES]
        ],
    ]
    defences = fieldset(class_="ib-section")[
        legend[t("itembuilder.defences")],
        [
            details(class_="ib-defence", open=bool(getattr(d, key)))[
                summary[
                    t(f"itembuilder.{key}"),
                    small(class_="muted")[
                        f" · {', '.join(t(f'rules.damage.{x}') for x in getattr(d, key))}"
                    ]
                    if getattr(d, key)
                    else None,
                ],
                div(class_="ib-checks")[
                    [
                        label(class_="cs-heal-pick")[
                            input_el(
                                type="checkbox",
                                name=key,
                                value=x,
                                checked=x in getattr(d, key),
                            ),
                            span[t(f"rules.damage.{x}")],
                        ]
                        for x in DAMAGE_TYPES
                    ]
                ],
            ]
            for key in ("resist", "immune", "vulnerable")
        ],
    ]
    abilities = fieldset(class_="ib-section")[
        legend[t("itembuilder.abilities")],
        p(class_="field-hint")[t("itembuilder.abilities_hint")],
        div(class_="ib-abilities")[
            [
                div(class_="ib-ability")[
                    b[t(f"rules.ability.{k}")],
                    _number(
                        f"set_{k}",
                        d.ability_set.get(k),
                        1,
                        30,
                        aria_label=t("itembuilder.set_to"),
                        placeholder="=",
                        title=t("itembuilder.set_to"),
                    ),
                    _number(
                        f"raise_{k}",
                        d.ability_raise.get(k),
                        1,
                        4,
                        aria_label=t("itembuilder.raise_by"),
                        placeholder="+",
                        title=t("itembuilder.raise_by"),
                    ),
                ]
                for k in ABILITIES
            ]
        ],
    ]
    charges = fieldset(class_="ib-section")[
        legend[t("itembuilder.charges")],
        div(class_="ib-grid ib-small")[
            _field(t("itembuilder.charges_n"), _number("charges", d.charges, 0, 100)),
            _field(
                t("itembuilder.recharge"),
                select(name="recharge")[
                    option(value="", selected=not d.recharge)[t("itembuilder.recharge.")],
                    [
                        option(value=r, selected=d.recharge == r)[t(f"itembuilder.recharge.{r}")]
                        for r in RECHARGES
                    ],
                ],
            ),
            _field(
                t("itembuilder.recharge_amount"),
                input_el(
                    type="text",
                    name="recharge_amount",
                    maxlength="40",
                    value=d.recharge_amount,
                    placeholder="1d6+1",
                ),
                hint=t("itembuilder.recharge_amount_hint"),
            ),
        ],
        b(class_="ib-sub")[t("itembuilder.spells")],
        p(class_="field-hint")[t("itembuilder.spells_hint")],
        div(class_="ib-spells")[
            [
                div(class_="ib-spell")[
                    input_el(
                        type="text",
                        name="spell",
                        maxlength="200",
                        value=s.spell,
                        placeholder=t("itembuilder.spell"),
                        aria_label=t("itembuilder.spell"),
                    ),
                    input_el(
                        type="number",
                        name="spell_charges",
                        min="0",
                        max="20",
                        value=str(s.charges) if s.charges else "",
                        placeholder=t("itembuilder.at_will"),
                        aria_label=t("itembuilder.spell_charges"),
                    ),
                ]
                for s in d.spells
            ],
            [
                div(class_="ib-spell")[
                    input_el(
                        type="text",
                        name="spell",
                        maxlength="200",
                        placeholder=t("itembuilder.spell"),
                        aria_label=t("itembuilder.spell"),
                    ),
                    input_el(
                        type="number",
                        name="spell_charges",
                        min="0",
                        max="20",
                        placeholder=t("itembuilder.at_will"),
                        aria_label=t("itembuilder.spell_charges"),
                    ),
                ]
                for _ in range(SPELL_ROWS)
            ],
        ],
    ]
    text = fieldset(class_="ib-section")[
        legend[t("itembuilder.text")],
        p(class_="field-hint")[t("itembuilder.text_hint")],
        textarea(name="text", rows="8", class_="ib-text")[d.text],
    ]
    actions = div(class_="form-actions ib-actions")[
        button(type="submit", name="action", value="save", class_="btn")[
            icon("check", "sm"), span[t("itembuilder.save")]
        ],
        button(type="submit", name="action", value="accept", class_="btn btn-primary")[
            icon("check", "sm"), span[t("itembuilder.accept")]
        ]
        if d.drafted_by
        else button(type="submit", name="action", value="publish", class_="btn btn-primary")[
            icon("upload", "sm"), span[t("itembuilder.publish")]
        ],
        a(href=f"{url}/delete", class_="btn btn-ghost")[
            icon("trash-2", "sm"), span[t("itembuilder.delete")]
        ],
    ]
    status = (
        div(class_="ib-revision")[
            p(class_="pill accent")[t("itembuilder.revision_hint")],
            p(class_="muted")[
                t(
                    "itembuilder.revision_changes",
                    fields=", ".join(t(f"itembuilder.field.{k}") for k in view.changes),
                )
                if view.changes
                else t("itembuilder.revision_same")
            ],
            a(href=item_url(view.original.id))[t("itembuilder.revision_original")],
        ]
        if view.original is not None
        else p(class_="pill accent")[t("itembuilder.draft_hint")]
        if d.drafted_by
        else p(class_="muted")[
            t("itembuilder.state_new")
            if not view.published
            else t("itembuilder.state_unpublished")
            if view.unpublished
            else t("itembuilder.state_published")
        ]
    )
    return app_shell(
        page,
        d.name,
        div(class_="ib-page")[
            a(href=ITEMS_URL, class_="backlink")[
                icon("arrow-left"), span[t("itembuilder.heading")]
            ],
            page_head(t("itembuilder.heading"), d.name, t("itembuilder.builder_lede")),
            notice(notice_text),
            alert(error),
            p(class_="notice")[
                t("itembuilder.revision_open"),
                " ",
                a(href=item_url(view.revision.id))[t("itembuilder.revision_see")],
            ]
            if view.revision is not None
            else None,
            div(class_="ib-layout")[
                form(method="post", action=url, class_="ib-form")[
                    basics, bonuses, defences, abilities, charges, text, actions
                ],
                section(class_="ib-preview", aria_label=t("itembuilder.preview"))[
                    status, statblock(page, view.preview, ctx)
                ],
            ],
        ],
    )


def delete_page(page: Page, design: ItemDesign) -> Element:
    t = page.t
    return confirm_page(
        page,
        title=t("itembuilder.delete_title", name=design.name),
        heading=t("itembuilder.delete_title", name=design.name),
        lede=t("itembuilder.delete_text"),
        action=f"{item_url(design.id)}/delete",
        back=item_url(design.id),
        submit=t("itembuilder.delete"),
    )


__all__ = [
    "ITEMS_URL",
    "ItemRow",
    "ItemView",
    "builder_page",
    "delete_page",
    "item_url",
    "items_page",
]
