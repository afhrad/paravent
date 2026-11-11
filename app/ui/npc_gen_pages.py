"""The NPC generator page (``/gm/npcs/{id}/generate``, app/services/npc_gen.py).

One form: the name tables, gender and guide at the top; below, what was rolled — the
name and every element, each with its book, a box to take it and a die to roll it
again; a statblock to pick; "Use these" writes the taken parts into the NPC. What was
rolled travels in hidden fields, so rolling one element keeps the others and the page
works without JavaScript.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    div,
    form,
    h2,
    label,
    optgroup,
    option,
    p,
    section,
    select,
    span,
)
from htpy import input as input_el

from app.models.gamestate import Npc, World
from app.services.naming import StyleEntry
from app.services.npc_gen import GENDERS, GeneratedNpc
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import npc_url
from app.ui.naming_pages import style_options
from app.ui.tags import RenderContext, render


@dataclass
class GenerateView:
    """Everything the page shows."""

    npc: Npc
    world: World
    rolled: GeneratedNpc
    elements: Sequence[str]
    name_sets: Sequence[StyleEntry]
    name_set: str
    gender: str
    relatives: Sequence[tuple[str, str]]
    parent: str
    edition: str
    editions: Sequence[str]
    statblocks: Sequence[tuple[str, str, str]]  # (ref, label, CR text)
    statblock: str
    take: set[str] = field(default_factory=set)
    fallback: bool = False


def _name_select(page: Page, view: GenerateView) -> Element:
    return div(class_="field")[
        label(for_="f-names")[page.t("npcgen.names")],
        select(id="f-names", name="names")[style_options(page, view.name_sets, view.name_set)],
        p(class_="field-hint")[page.t("naming.no_style", race=view.npc.race or view.npc.culture)]
        if view.fallback
        else None,
    ]


def _options(page: Page, view: GenerateView) -> Element:
    t = page.t
    return div(class_="npcgen-options")[
        _name_select(page, view),
        div(class_="field")[
            label(for_="f-parent")[t("naming.child_of")],
            select(id="f-parent", name="parent")[
                option(value="")[t("naming.no_parent")],
                [
                    option(value=ref, selected=ref == view.parent)[name]
                    for ref, name in view.relatives
                ],
            ],
        ]
        if view.relatives
        else None,
        div(class_="field")[
            label(for_="f-gender")[t("npcgen.gender")],
            select(id="f-gender", name="gender")[
                [
                    option(value=g, selected=g == view.gender)[t(f"npcgen.gender.{g}")]
                    for g in GENDERS
                ]
            ],
        ],
        div(class_="field")[
            label(for_="f-edition")[t("npcgen.tables")],
            select(id="f-edition", name="edition")[
                [
                    option(value=e, selected=e == view.edition)[t(f"npcgen.tables.{e}")]
                    for e in view.editions
                ]
            ],
        ]
        if len(view.editions) > 1
        else hidden("edition", view.edition),
        div(class_="form-actions")[
            button(type="submit", name="action", value="roll", class_="btn btn-primary")[
                icon("dices", "sm"), span[t("npcgen.roll_all")]
            ]
        ],
    ]


def _row(page: Page, key: str, caption: str, value: Node, source: str, take: bool) -> Element:
    t = page.t
    return div(class_="npcgen-row")[
        label(class_="npcgen-take")[
            input_el(type="checkbox", name="take", value=key, checked=take),
            span(class_="sr-only")[t("npcgen.take", what=caption)],
        ],
        div(class_="npcgen-main")[
            span(class_="npcgen-label")[caption],
            span(class_="npcgen-value")[value],
        ],
        span(class_="chip-src npcgen-src")[source] if source else None,
        button(
            type="submit",
            name="action",
            value=f"reroll:{key}",
            class_="btn btn-icon btn-ghost",
            title=t("npcgen.reroll", what=caption),
            aria_label=t("npcgen.reroll", what=caption),
        )[icon("dices", "sm")],
    ]


def _statblock_select(page: Page, view: GenerateView) -> Element:
    t = page.t
    groups: dict[str, list[tuple[str, str]]] = {}
    for ref, name, cr in view.statblocks:
        groups.setdefault(cr, []).append((ref, name))
    return div(class_="field")[
        label(for_="f-statblock")[t("npcs.statblock")],
        select(id="f-statblock", name="statblock")[
            option(value="", selected=not view.statblock)[t("npcgen.statblock_keep")],
            [
                optgroup(label=t("npcgen.cr", cr=cr))[
                    [option(value=ref, selected=ref == view.statblock)[name] for ref, name in rows]
                ]
                for cr, rows in groups.items()
            ],
        ],
        p(class_="field-hint")[t("npcgen.statblock_hint")],
    ]


def generate_page(
    page: Page, view: GenerateView, ctx: RenderContext, *, error: str | None = None
) -> Element:
    """The generator for one NPC."""
    t = page.t
    url = npc_url(view.npc.id)
    rolled = view.rolled
    rows = [
        _row(
            page,
            "name",
            t("adventures.name"),
            b[rolled.name] if rolled.name else span(class_="muted")[t("npcgen.no_name")],
            "",
            "name" in view.take and bool(rolled.name),
        )
    ]
    for element in view.elements:
        value = rolled.values.get(element, "")
        rows.append(
            _row(
                page,
                element,
                t(f"npcgen.el.{element}"),
                render(value, ctx),
                rolled.sources.get(element, ""),
                element in view.take,
            )
        )
    carried = [
        hidden("v_name", rolled.name),
        hidden("v_name_parts", json.dumps(rolled.parts, ensure_ascii=False)),
    ] + [hidden(f"v_{e}", rolled.values.get(e, "")) for e in view.elements]
    return app_shell(
        page,
        t("npcgen.title", name=view.npc.name),
        div(class_="formpage formpage-wide")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[view.npc.name]],
            alert(error),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("npcs.eyebrow")],
                    b[t("npcgen.title", name=view.npc.name)],
                    p(class_="muted")[t("npcgen.lede")],
                ],
                section(aria_label=t("npcgen.title", name=view.npc.name))[
                    _form(page, url, view, rows, carried)
                ],
            ],
        ],
        world_id=view.world.id,
    )


def _form(
    page: Page,
    url: str,
    view: GenerateView,
    rows: Sequence[Element],
    carried: Sequence[Node],
) -> Element:
    t = page.t
    return form(method="post", action=f"{url}/generate", class_="stack-form")[
        _options(page, view),
        h2(class_="npcgen-heading")[t("npcgen.rolled")],
        div(class_="npcgen-rows")[rows],
        _statblock_select(page, view),
        carried,
        div(class_="form-actions")[
            button(type="submit", name="action", value="apply", class_="btn btn-primary")[
                icon("check"), span[t("npcgen.apply")]
            ],
            a(href=url, class_="btn btn-ghost")[t("common.cancel")],
        ],
        p(class_="field-hint")[t("npcgen.apply_hint")],
    ]


__all__ = ["GenerateView", "generate_page"]
