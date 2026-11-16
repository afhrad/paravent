"""Preparing spells (documentation/CHARGEN-WIZARD.md §10): one page, one list.

The list is the creation wizard's spell question — level chips, filter, previews, a
running count that stops at the limit — over what the character may prepare today:
the class list, or the spellbook. Each tick is saved as it is made, so the sheet (and
the gamemaster's party card) follows at once.
"""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, a, button, div, form, h1, h2, li, option, p, section, select, span, ul
from htpy import input as input_el

from app.models.pc import PlayerCharacter
from app.services.chargen import Pick, Plan, Planner, Step
from app.services.chargen.prepare import Preparation
from app.services.spellbook import GOLD_PER_LEVEL, HOURS_PER_LEVEL
from app.ui.chargen_pages import SCRIPTS, View, _spell_question
from app.ui.context import Page
from app.ui.forms import alert
from app.ui.icons import icon
from app.ui.layout import app_shell


def prepare_url(character_id: str) -> str:
    """The prepare page of a character."""
    return f"/characters/{character_id}/prepare"


def prepare_form(
    page: Page,
    pc: PlayerCharacter,
    prep: Preparation,
    planner: Planner,
    *,
    flash: str | None = None,
) -> Element:
    """The list of spells to prepare, with how many and what is always prepared."""
    t = page.t
    url = prepare_url(pc.id)
    split = len(prep.classes) > 1
    picks = [
        Pick(
            cls.pick_id,
            "prepared",
            cls.class_name,
            count=cls.limit,
            options=cls.options,
            label=f"{t('chargen.pick.prepared')} · {cls.class_name}" if split else "",
        )
        for cls in prep.classes
    ]
    step = Step("spells", tuple(picks))
    edition = planner.edition(None)
    view = View(
        page,
        pc.model_copy(update={"choices": {cls.pick_id: list(cls.chosen) for cls in prep.classes}}),
        Plan(edition, (step,), None, sheet=prep.sheet),
        step,
        planner,
    )
    questions = [
        _spell_question(
            view,
            pick,
            t("prepare.from_book" if cls.from_book else "prepare.from_list", n=cls.limit),
        )
        for pick, cls in zip(picks, prep.classes, strict=True)
    ]
    return form(
        id="prepare",
        class_="wizard cg-wizard",
        method="post",
        action=url,
        hx_post=url,
        hx_target="this",
        hx_swap="outerHTML",
        hx_trigger="change[!event.target.matches('[data-filter]')]",
    )[
        div(class_="cg-body cg-body-single")[
            div(class_="cg-main")[
                alert(flash),
                section(class_="cg-features")[
                    span(class_="eyebrow")[t("prepare.always")],
                    ul(class_="cg-feature-list")[
                        [
                            li[span(class_="muted", aria_hidden="true")["+"], span[n]]
                            for n in prep.always
                        ]
                    ],
                ]
                if prep.always
                else None,
                questions,
            ],
        ],
        div(class_="wizard-foot cg-foot")[
            a(href=f"/characters/{pc.id}", class_="btn btn-ghost")[
                icon("arrow-left", "sm"), span[t("levelup.back")]
            ],
            div(class_="cg-foot-actions")[
                button(type="submit", name="go", value="done", class_="btn btn-primary")[
                    icon("check", "sm"), span[t("prepare.done")]
                ],
            ],
        ],
    ]


def prepare_page(
    page: Page,
    pc: PlayerCharacter,
    prep: Preparation,
    planner: Planner,
    *,
    flash: str | None = None,
    copyable: Sequence[tuple[str, str, int]] = (),
) -> Element:
    """The prepare page; ``copyable`` are the spells a spellbook caster may copy into
    its book, ``(ref, name, level)``."""
    t = page.t
    return app_shell(
        page,
        t("prepare.page_title", name=pc.name or "?"),
        div(class_="cg-page")[
            div(class_="cg-head")[
                span(class_="eyebrow")[t("prepare.eyebrow")],
                h1[pc.name or "?"],
                p(class_="muted")[t("prepare.intro")],
            ],
            prepare_form(page, pc, prep, planner, flash=flash),
            _copy_form(page, pc.id, copyable) if copyable else None,
        ],
        extra_scripts=SCRIPTS,
        main_class="shell-main shell-wide",
        world_id=pc.world_id,
    )


def _copy_form(page: Page, character_id: str, copyable: Sequence[tuple[str, str, int]]) -> Element:
    """Copy a spell into the spellbook from another book, at its cost in gold and hours."""
    t = page.t
    return section(class_="panel prepare-copy")[
        h2[t("spellbook.copy_heading")],
        p(class_="muted")[t("spellbook.copy_hint")],
        form(method="post", action=f"/characters/{character_id}/scroll", class_="inline-form")[
            input_el(type="hidden", name="op", value="book"),
            select(name="spell", aria_label=t("spellbook.copy_heading"))[
                [
                    option(value=ref)[
                        t(
                            "spellbook.copy_option",
                            name=page.t.term("spell", name) or name,
                            level=level,
                            gold=GOLD_PER_LEVEL * level,
                            hours=HOURS_PER_LEVEL * level,
                        )
                    ]
                    for ref, name, level in copyable
                ]
            ],
            button(type="submit", class_="btn btn-sm")[t("spellbook.copy")],
        ],
    ]


__all__ = ["prepare_form", "prepare_page", "prepare_url"]
