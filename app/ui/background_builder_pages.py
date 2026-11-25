"""The background builder's pages (app/services/background_builder.py): the designs, a
dialog that begins one, and the builder — what the character wizard reads, and the
background as it will read beside it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from htpy import (
    Element,
    Node,
    a,
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
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import BackgroundDesign, BackgroundItem, World
from app.services.background_builder import ABILITIES
from app.services.references import ReferenceEntry
from app.ui.context import Page
from app.ui.context_menu import row_menu
from app.ui.forms import alert, notice
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.item_links import BACKGROUNDS_URL, background_url
from app.ui.layout import app_shell
from app.ui.library_links import confirm_page
from app.ui.ref_pages import statblock
from app.ui.revision_bits import revision_notice, revision_status
from app.ui.tags import RenderContext
from app.ui.tool_dialog import tool_dialog

ITEM_ROWS = 3

# The form's label for each field a revision may change.
LABELS = {
    "edition": "bgbuilder.edition",
    "skills": "bgbuilder.proficiencies",
    "any_skills": "bgbuilder.any_skills_label",
    "tools": "bgbuilder.tools",
    "any_artisan": "bgbuilder.any_artisan_label",
    "any_gaming": "bgbuilder.any_gaming_label",
    "any_instrument": "bgbuilder.any_instrument_label",
    "languages": "bgbuilder.languages",
    "any_languages": "bgbuilder.any_languages_label",
    "equipment": "bgbuilder.equipment",
    "gold": "bgbuilder.gold",
    "abilities": "bgbuilder.origin",
    "feat": "bgbuilder.feat",
    "feature_name": "bgbuilder.feature_name",
    "feature_text": "bgbuilder.feature_text",
    "text": "itembuilder.text",
    "based_on": "itembuilder.field.based_on",
    "skill_choices": "bgbuilder.skill_choices",
    "choose_skills": "bgbuilder.skill_choices",
}


@dataclass
class BackgroundRow:
    design: BackgroundDesign
    world: str
    unpublished: bool


@dataclass
class BackgroundView:
    design: BackgroundDesign
    worlds: Sequence[World]
    skills: Sequence[str]  # every skill of the books, lower case
    preview: ReferenceEntry
    unpublished: bool
    published: bool
    original: Any = None  # the design a revision changes
    revision: Any = None  # Claude's open revision of this design
    changes: Sequence[str] = ()


def _field(caption: str, *content: Node, hint: str | None = None) -> Element:
    return label(class_="field")[
        span(class_="field-label")[caption],
        content,
        span(class_="field-hint")[hint] if hint else None,
    ]


def _number(name: str, value: int, high: int) -> Node:
    return input_el(
        type="number",
        name=name,
        min="0",
        max=str(high),
        value=str(value) if value else "",
        inputmode="numeric",
    )


def backgrounds_page(
    page: Page,
    rows: Sequence[BackgroundRow],
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
) -> Element:
    """Every design, and **New background**."""
    t = page.t
    draft = draft or {}
    listed = [
        li(class_="list-row")[
            icon("scroll", "sm"),
            div(class_="list-main")[
                a(href=background_url(r.design.id), class_="list-title list-link")[r.design.name],
                span(class_="list-meta")[
                    " · ".join(
                        x
                        for x in (
                            t(f"bgbuilder.edition.{r.design.edition}"),
                            r.world,
                            t("itembuilder.based_on", ref=r.design.based_on)
                            if r.design.based_on
                            else "",
                        )
                        if x
                    )
                ],
            ],
            span(class_="pill accent", title=t("bgbuilder.revision_hint"))[
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
            row_menu(
                page,
                background_url(r.design.id),
                r.design.name,
                fallback=f"{background_url(r.design.id)}/delete",
            ),
        ]
        for r in rows
    ]
    new = form(method="post", action=BACKGROUNDS_URL, class_="stack")[
        alert(error),
        _field(
            t("itembuilder.name"),
            input_el(type="text", name="name", maxlength="80", value=draft.get("name", "")),
        ),
        _field(
            t("bgbuilder.from_background"),
            input_el(
                type="text",
                name="based_on",
                maxlength="200",
                value=draft.get("based_on", ""),
                placeholder=t("bgbuilder.from_background_hint"),
            ),
            hint=t("bgbuilder.from_background_note"),
        ),
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("plus", "sm"), span[t("itembuilder.begin")]
            ]
        ],
    ]
    return app_shell(
        page,
        t("bgbuilder.heading"),
        [
            page_head(
                t("gm.eyebrow"),
                t("bgbuilder.heading"),
                t("bgbuilder.lede"),
                a(href="?tool=background", class_="btn btn-primary", data_dialog="tool-background")[
                    icon("plus"), span[t("bgbuilder.new")]
                ],
            ),
            notice(notice_text),
            ul(class_="list")[listed]
            if listed
            else empty_state("scroll", t("bgbuilder.empty_heading"), t("bgbuilder.empty_text")),
            tool_dialog(page, "background", t("bgbuilder.new"), new, is_open=error is not None),
        ],
    )


def _item_row(page: Page, item: BackgroundItem | None) -> Element:
    t = page.t
    return div(class_="ib-spell ib-item")[
        input_el(
            type="text",
            name="eq_item",
            maxlength="200",
            value=item.item if item else "",
            placeholder=t("bgbuilder.eq_item"),
            aria_label=t("bgbuilder.eq_item"),
        ),
        input_el(
            type="text",
            name="eq_special",
            maxlength="200",
            value=item.special if item else "",
            placeholder=t("bgbuilder.eq_special"),
            aria_label=t("bgbuilder.eq_special"),
        ),
        input_el(
            type="number",
            name="eq_quantity",
            min="1",
            max="999",
            value=str(item.quantity) if item else "1",
            aria_label=t("bgbuilder.eq_quantity"),
        ),
    ]


def builder_page(
    page: Page,
    view: BackgroundView,
    ctx: RenderContext,
    *,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """The builder: one form, the background as it will read beside it."""
    t = page.t
    d = view.design
    url = background_url(d.id)
    basics = fieldset(class_="ib-section")[
        legend[t("bgbuilder.basics")],
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
                t("bgbuilder.edition"),
                select(name="edition")[
                    [
                        option(value=k, selected=d.edition == k)[t(f"bgbuilder.edition.{k}")]
                        for k in ("one", "classic")
                    ]
                ],
                hint=t("bgbuilder.edition_hint"),
            ),
        ],
    ]
    origin = fieldset(class_="ib-section")[
        legend[t("bgbuilder.origin")],
        p(class_="field-hint")[t("bgbuilder.origin_hint")],
        div(class_="ib-checks")[
            [
                label(class_="cs-heal-pick")[
                    input_el(type="checkbox", name="abilities", value=k, checked=k in d.abilities),
                    span[t(f"rules.ability.{k}")],
                ]
                for k in ABILITIES
            ]
        ],
        _field(
            t("bgbuilder.feat"),
            input_el(type="text", name="feat", maxlength="200", value=d.feat),
            hint=t("bgbuilder.feat_hint"),
        ),
    ]
    proficiencies = fieldset(class_="ib-section")[
        legend[t("bgbuilder.proficiencies")],
        div(class_="ib-checks")[
            [
                label(class_="cs-heal-pick")[
                    input_el(type="checkbox", name="skills", value=s, checked=s in d.skills),
                    span[t(f"skill.{s}")],
                ]
                for s in view.skills
            ]
        ],
        details(class_="ib-defence", open=bool(d.skill_choices))[
            summary[t("bgbuilder.skill_choices")],
            div(class_="ib-checks")[
                [
                    label(class_="cs-heal-pick")[
                        input_el(
                            type="checkbox",
                            name="skill_choices",
                            value=s,
                            checked=s in d.skill_choices,
                        ),
                        span[t(f"skill.{s}")],
                    ]
                    for s in view.skills
                ]
            ],
            _field(
                t("bgbuilder.choose_skills_label"), _number("choose_skills", d.choose_skills, 4)
            ),
        ],
        div(class_="ib-grid ib-small")[
            _field(t("bgbuilder.any_skills_label"), _number("any_skills", d.any_skills, 4)),
            _field(
                t("bgbuilder.tools"),
                input_el(type="text", name="tools", value=", ".join(d.tools)),
                hint=t("bgbuilder.list_hint"),
            ),
            _field(t("bgbuilder.any_artisan_label"), _number("any_artisan", d.any_artisan, 3)),
            _field(t("bgbuilder.any_gaming_label"), _number("any_gaming", d.any_gaming, 3)),
            _field(
                t("bgbuilder.any_instrument_label"), _number("any_instrument", d.any_instrument, 3)
            ),
            _field(
                t("bgbuilder.languages"),
                input_el(type="text", name="languages", value=", ".join(d.languages)),
                hint=t("bgbuilder.list_hint"),
            ),
            _field(
                t("bgbuilder.any_languages_label"), _number("any_languages", d.any_languages, 4)
            ),
        ],
    ]
    equipment = fieldset(class_="ib-section")[
        legend[t("bgbuilder.equipment")],
        p(class_="field-hint")[t("bgbuilder.equipment_hint")],
        div(class_="ib-spells")[
            [_item_row(page, i) for i in d.equipment],
            [_item_row(page, None) for _ in range(ITEM_ROWS)],
        ],
        _field(t("bgbuilder.gold"), _number("gold", d.gold, 100000)),
    ]
    text = fieldset(class_="ib-section")[
        legend[t("itembuilder.text")],
        textarea(name="text", rows="4", class_="ib-text")[d.text],
        div(class_="ib-grid")[
            _field(
                t("bgbuilder.feature_name"),
                input_el(type="text", name="feature_name", maxlength="120", value=d.feature_name),
                hint=t("bgbuilder.feature_hint"),
            ),
        ],
        textarea(
            name="feature_text",
            rows="4",
            class_="ib-text",
            aria_label=t("bgbuilder.feature_text"),
        )[d.feature_text],
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
        revision_status(
            page,
            t("bgbuilder.revision_hint"),
            background_url(view.original.id),
            view.changes,
            {k: t(v) for k, v in LABELS.items()},
        )
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
            a(href=BACKGROUNDS_URL, class_="backlink")[
                icon("arrow-left"), span[t("bgbuilder.heading")]
            ],
            page_head(t("bgbuilder.heading"), d.name, t("bgbuilder.builder_lede")),
            notice(notice_text),
            alert(error),
            revision_notice(page, t("bgbuilder.revision_open"), background_url(view.revision.id))
            if view.revision is not None
            else None,
            div(class_="ib-layout")[
                form(method="post", action=url, class_="ib-form")[
                    basics,
                    origin if d.edition == "one" else None,
                    proficiencies,
                    equipment,
                    text,
                    actions,
                ],
                section(class_="ib-preview", aria_label=t("itembuilder.preview"))[
                    status, statblock(page, view.preview, ctx)
                ],
            ],
        ],
    )


def delete_page(page: Page, design: BackgroundDesign) -> Element:
    t = page.t
    return confirm_page(
        page,
        title=t("itembuilder.delete_title", name=design.name),
        heading=t("itembuilder.delete_title", name=design.name),
        lede=t("bgbuilder.delete_text"),
        action=f"{background_url(design.id)}/delete",
        back=background_url(design.id),
        submit=t("itembuilder.delete"),
    )


__all__ = [
    "BACKGROUNDS_URL",
    "BackgroundRow",
    "BackgroundView",
    "background_url",
    "backgrounds_page",
    "builder_page",
    "delete_page",
]
