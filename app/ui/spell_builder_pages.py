"""The spell builder's pages (app/services/spell_builder.py): the designs, a dialog that
begins one, and the builder — one form for what casting it uses and its text, with the
spell as it will read beside it."""

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
    small,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import SpellDesign, World
from app.services.references import ReferenceEntry
from app.services.spell_builder import ABILITY_WORDS, DAMAGE_TYPES, SCHOOLS
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.item_links import SPELLS_URL, spell_url
from app.ui.layout import app_shell
from app.ui.library_links import confirm_page
from app.ui.ref_pages import statblock
from app.ui.revision_bits import revision_notice, revision_status
from app.ui.tags import RenderContext
from app.ui.tool_dialog import tool_dialog

TIME_UNITS = ("action", "bonus", "reaction", "minute", "hour")
RANGE_KINDS = ("feet", "miles", "self", "touch", "sight", "unlimited")
AREAS = ("", "cone", "cube", "cylinder", "line", "sphere", "emanation")
DURATIONS = ("instant", "timed", "permanent", "special")
DURATION_UNITS = ("round", "minute", "hour", "day")
EFFECTS = ("none", "save", "attack", "heal")

# The form's label for each field a revision may change.
LABELS = {
    "level": "spellbuilder.level",
    "school": "spellbuilder.school",
    "time_number": "spellbuilder.time",
    "time_unit": "spellbuilder.time",
    "range_kind": "spellbuilder.range",
    "range_amount": "spellbuilder.range",
    "area": "spellbuilder.area",
    "area_size": "spellbuilder.area",
    "verbal": "spellbuilder.verbal",
    "somatic": "spellbuilder.somatic",
    "material": "spellbuilder.material",
    "material_cost": "spellbuilder.material_cost",
    "material_consumed": "spellbuilder.consumed",
    "duration_kind": "spellbuilder.duration",
    "duration_amount": "spellbuilder.duration",
    "duration_unit": "spellbuilder.duration",
    "concentration": "spellbuilder.concentration",
    "ritual": "spellbuilder.ritual",
    "classes": "spellbuilder.classes",
    "effect": "spellbuilder.effect_kind",
    "save": "spellbuilder.save",
    "half_on_save": "spellbuilder.half_on_save",
    "attack": "spellbuilder.attack",
    "dice": "spellbuilder.dice",
    "damage_types": "spellbuilder.damage_types",
    "heal_mod": "spellbuilder.heal_mod",
    "heal_targets": "spellbuilder.heal_targets",
    "upcast": "spellbuilder.upcast",
    "write_effect": "spellbuilder.write_effect",
    "text": "itembuilder.text",
    "higher_text": "spellbuilder.higher_text",
    "based_on": "itembuilder.field.based_on",
}


@dataclass
class SpellRow:
    design: SpellDesign
    world: str
    unpublished: bool


@dataclass
class SpellView:
    design: SpellDesign
    worlds: Sequence[World]
    classes: Sequence[tuple[str, str]]  # (ref, label)
    preview: ReferenceEntry
    unpublished: bool
    published: bool
    original: Any = None  # the design a revision changes
    revision: Any = None  # Claude's open revision of this design
    changes: Sequence[str] = ()


def _choice(name: str, value: str, options: Sequence[tuple[str, str]]) -> Element:
    return select(name=name)[[option(value=k, selected=k == value)[text] for k, text in options]]


def _field(caption: str, *content: Node, hint: str | None = None) -> Element:
    return label(class_="field")[
        span(class_="field-label")[caption],
        content,
        span(class_="field-hint")[hint] if hint else None,
    ]


def _check(name: str, caption: str, checked: bool) -> Element:
    return label(class_="cs-heal-pick")[
        input_el(type="checkbox", name=name, value="1", checked=checked), span[caption]
    ]


def _number(name: str, value: int, low: int, high: int) -> Node:
    return input_el(
        type="number", name=name, min=str(low), max=str(high), value=str(value), inputmode="numeric"
    )


def spells_page(
    page: Page,
    rows: Sequence[SpellRow],
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
) -> Element:
    """Every design, and **New spell**: a dialog that begins one in the table's world."""
    t = page.t
    draft = draft or {}
    listed = [
        li(class_="list-row")[
            icon("wand-sparkles", "sm"),
            div(class_="list-main")[
                a(href=spell_url(r.design.id), class_="list-title list-link")[r.design.name],
                span(class_="list-meta")[
                    " · ".join(
                        x
                        for x in (
                            t("spellbuilder.level_n", level=r.design.level)
                            if r.design.level
                            else t("spellbuilder.cantrip"),
                            t(f"chargen.school.{r.design.school}"),
                            r.world,
                            t("itembuilder.based_on", ref=r.design.based_on)
                            if r.design.based_on
                            else "",
                        )
                        if x
                    )
                ],
            ],
            span(class_="pill accent", title=t("spellbuilder.revision_hint"))[
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
    new = form(method="post", action=SPELLS_URL, class_="stack")[
        alert(error),
        _field(
            t("itembuilder.name"),
            input_el(type="text", name="name", maxlength="80", value=draft.get("name", "")),
        ),
        _field(
            t("spellbuilder.from_spell"),
            input_el(
                type="text",
                name="based_on",
                maxlength="200",
                value=draft.get("based_on", ""),
                placeholder=t("spellbuilder.from_spell_hint"),
            ),
            hint=t("spellbuilder.from_spell_note"),
        ),
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("plus", "sm"), span[t("itembuilder.begin")]
            ]
        ],
    ]
    return app_shell(
        page,
        t("spellbuilder.heading"),
        [
            page_head(
                t("gm.eyebrow"),
                t("spellbuilder.heading"),
                t("spellbuilder.lede"),
                a(href="?tool=spell", class_="btn btn-primary", data_dialog="tool-spell")[
                    icon("plus"), span[t("spellbuilder.new")]
                ],
            ),
            notice(notice_text),
            ul(class_="list")[listed]
            if listed
            else empty_state(
                "wand-sparkles", t("spellbuilder.empty_heading"), t("spellbuilder.empty_text")
            ),
            tool_dialog(page, "spell", t("spellbuilder.new"), new, is_open=error is not None),
        ],
    )


def builder_page(
    page: Page,
    view: SpellView,
    ctx: RenderContext,
    *,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """The builder: one form, the spell as it will read beside it."""
    t = page.t
    d = view.design
    url = spell_url(d.id)
    header = fieldset(class_="ib-section")[
        legend[t("spellbuilder.header")],
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
                t("spellbuilder.level"),
                _choice(
                    "level",
                    str(d.level),
                    [("0", t("spellbuilder.cantrip"))]
                    + [(str(n), t("spellbuilder.level_n", level=n)) for n in range(1, 10)],
                ),
            ),
            _field(
                t("spellbuilder.school"),
                _choice("school", d.school, [(k, t(f"chargen.school.{k}")) for k in SCHOOLS]),
            ),
            div(class_="field")[
                span(class_="field-label")[t("spellbuilder.time")],
                div(class_="ib-pair")[
                    _number("time_number", d.time_number, 1, 99),
                    _choice(
                        "time_unit",
                        d.time_unit,
                        [(k, t(f"spellbuilder.time.{k}")) for k in TIME_UNITS],
                    ),
                ],
            ],
            div(class_="field")[
                span(class_="field-label")[t("spellbuilder.range")],
                div(class_="ib-pair")[
                    _number("range_amount", d.range_amount, 0, 100000),
                    _choice(
                        "range_kind",
                        d.range_kind,
                        [(k, t(f"spellbuilder.range.{k}")) for k in RANGE_KINDS],
                    ),
                ],
                span(class_="field-hint")[t("spellbuilder.range_hint")],
            ],
            div(class_="field")[
                span(class_="field-label")[t("spellbuilder.area")],
                div(class_="ib-pair")[
                    _number("area_size", d.area_size, 0, 10000),
                    _choice(
                        "area", d.area, [(k, t(f"spellbuilder.area.{k or 'none'}")) for k in AREAS]
                    ),
                ],
                span(class_="field-hint")[t("spellbuilder.area_hint")],
            ],
            div(class_="field ib-wide")[
                span(class_="field-label")[t("spellbuilder.duration")],
                div(class_="ib-pair")[
                    _choice(
                        "duration_kind",
                        d.duration_kind,
                        [(k, t(f"spellbuilder.duration.{k}")) for k in DURATIONS],
                    ),
                    _number("duration_amount", d.duration_amount, 1, 999),
                    _choice(
                        "duration_unit",
                        d.duration_unit,
                        [(k, t(f"spellbuilder.unit.{k}")) for k in DURATION_UNITS],
                    ),
                ],
            ],
        ],
        div(class_="ib-checks")[
            _check("concentration", t("spellbuilder.concentration"), d.concentration),
            _check("ritual", t("spellbuilder.ritual"), d.ritual),
            _check("verbal", t("spellbuilder.verbal"), d.verbal),
            _check("somatic", t("spellbuilder.somatic"), d.somatic),
        ],
        div(class_="ib-grid")[
            _field(
                t("spellbuilder.material"),
                input_el(type="text", name="material", maxlength="300", value=d.material),
                hint=t("spellbuilder.material_hint"),
            ),
            _field(
                t("spellbuilder.material_cost"),
                input_el(
                    type="number",
                    name="material_cost",
                    min="0",
                    value=str(d.material_cost) if d.material_cost else "",
                ),
            ),
            div(class_="field")[
                _check("material_consumed", t("spellbuilder.consumed"), d.material_consumed)
            ],
        ],
    ]
    effect = fieldset(class_="ib-section")[
        legend[t("spellbuilder.effect")],
        p(class_="field-hint")[t("spellbuilder.effect_hint")],
        div(class_="ib-grid")[
            _field(
                t("spellbuilder.effect_kind"),
                _choice(
                    "effect", d.effect, [(k, t(f"spellbuilder.effect_kind.{k}")) for k in EFFECTS]
                ),
            ),
            _field(
                t("spellbuilder.save"),
                _choice("save", d.save, [(k, t(f"rules.ability.{k}")) for k in ABILITY_WORDS]),
            ),
            _field(
                t("spellbuilder.attack"),
                _choice(
                    "attack",
                    d.attack,
                    [("R", t("spellbuilder.attack.R")), ("M", t("spellbuilder.attack.M"))],
                ),
            ),
            _field(
                t("spellbuilder.dice"),
                input_el(type="text", name="dice", maxlength="40", value=d.dice, placeholder="8d6"),
                hint=t("spellbuilder.dice_hint"),
            ),
            _field(
                t("spellbuilder.upcast"),
                input_el(
                    type="text", name="upcast", maxlength="40", value=d.upcast, placeholder="1d6"
                ),
                hint=t("spellbuilder.upcast_hint"),
            ),
            _field(t("spellbuilder.heal_targets"), _number("heal_targets", d.heal_targets, 1, 12)),
        ],
        div(class_="ib-checks")[
            _check("half_on_save", t("spellbuilder.half_on_save"), d.half_on_save),
            _check("heal_mod", t("spellbuilder.heal_mod"), d.heal_mod),
            _check("write_effect", t("spellbuilder.write_effect"), d.write_effect),
        ],
        details(class_="ib-defence", open=bool(d.damage_types))[
            summary[
                t("spellbuilder.damage_types"),
                small(class_="muted")[
                    f" · {', '.join(t(f'rules.damage.{x}') for x in d.damage_types)}"
                ]
                if d.damage_types
                else None,
            ],
            div(class_="ib-checks")[
                [
                    _check_value("damage_types", x, t(f"rules.damage.{x}"), x in d.damage_types)
                    for x in DAMAGE_TYPES
                ]
            ],
        ],
    ]
    lists = fieldset(class_="ib-section")[
        legend[t("spellbuilder.classes")],
        p(class_="field-hint")[t("spellbuilder.classes_hint")],
        div(class_="ib-checks")[
            [_check_value("classes", ref, name, ref in d.classes) for ref, name in view.classes]
        ]
        if view.classes
        else p(class_="muted")[t("spellbuilder.no_classes")],
    ]
    text = fieldset(class_="ib-section")[
        legend[t("itembuilder.text")],
        p(class_="field-hint")[t("spellbuilder.text_hint")],
        textarea(name="text", rows="7", class_="ib-text")[d.text],
        _field(
            t("spellbuilder.higher_text"),
            textarea(name="higher_text", rows="3", class_="ib-text")[d.higher_text],
        ),
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
            t("spellbuilder.revision_hint"),
            spell_url(view.original.id),
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
            a(href=SPELLS_URL, class_="backlink")[
                icon("arrow-left"), span[t("spellbuilder.heading")]
            ],
            page_head(t("spellbuilder.heading"), d.name, t("spellbuilder.builder_lede")),
            notice(notice_text),
            alert(error),
            revision_notice(page, t("spellbuilder.revision_open"), spell_url(view.revision.id))
            if view.revision is not None
            else None,
            div(class_="ib-layout")[
                form(method="post", action=url, class_="ib-form")[
                    header, effect, lists, text, actions
                ],
                section(class_="ib-preview", aria_label=t("itembuilder.preview"))[
                    status, statblock(page, view.preview, ctx)
                ],
            ],
        ],
    )


def _check_value(name: str, value: str, caption: str, checked: bool) -> Element:
    return label(class_="cs-heal-pick")[
        input_el(type="checkbox", name=name, value=value, checked=checked), span[caption]
    ]


def delete_page(page: Page, design: SpellDesign) -> Element:
    t = page.t
    return confirm_page(
        page,
        title=t("itembuilder.delete_title", name=design.name),
        heading=t("itembuilder.delete_title", name=design.name),
        lede=t("spellbuilder.delete_text"),
        action=f"{spell_url(design.id)}/delete",
        back=spell_url(design.id),
        submit=t("itembuilder.delete"),
    )


__all__ = ["SpellRow", "SpellView", "builder_page", "delete_page", "spells_page"]
