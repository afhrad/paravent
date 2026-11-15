"""Safety tools (app/services/safety.py): the table's lines and veils per world, the
X-card, and anonymous suggestions."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import (
    Element,
    Node,
    b,
    button,
    div,
    form,
    h2,
    label,
    li,
    option,
    p,
    section,
    select,
    span,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import TableSafety, World
from app.ui.context import Page
from app.ui.forms import hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

SAFETY_URL = "/safety"


def xcard_button(page: Page, world_id: str | None) -> Element:
    """The X-card: one tap tells the gamemaster, and no one else, to change course."""
    t = page.t
    return form(
        method="post",
        action=f"{SAFETY_URL}/xcard",
        hx_post=f"{SAFETY_URL}/xcard",
        hx_swap="outerHTML",
        class_="xcard-form",
    )[
        hidden("world", world_id or ""),
        button(type="submit", class_="btn btn-sm xcard", title=t("safety.xcard_hint"))[
            icon("x", "sm"), span[t("safety.xcard")]
        ],
    ]


def xcard_sent(page: Page) -> Element:
    """What the X-card button becomes once tapped."""
    return span(class_="pill neutral xcard-sent", role="status")[page.t("safety.xcard_sent")]


def safety_page(
    page: Page,
    worlds: Sequence[tuple[World, TableSafety]],
    *,
    gamemaster: bool,
    message: str | None = None,
) -> Element:
    """Each of the reader's worlds: its lines and veils (the gamemaster edits them and
    settles what was suggested), a form to suggest more without a name, and the X-card."""
    t = page.t
    return app_shell(
        page,
        t("safety.title"),
        div(class_="tr-page safety-page")[
            page_head(t("safety.eyebrow"), t("safety.title"), t("safety.lede")),
            notice(message),
            [_world(page, world, safety, gamemaster) for world, safety in worlds]
            if worlds
            else p(class_="muted")[t("safety.no_world")],
        ],
    )


def _world(page: Page, world: World, safety: TableSafety, gamemaster: bool) -> Element:
    t = page.t
    url = f"{SAFETY_URL}/{world.id}"
    lists: Node
    if gamemaster:
        lists = form(method="post", action=url, class_="stack")[
            label(class_="field")[
                span[t("safety.lines")],
                small_hint(t("safety.lines_hint")),
                textarea(name="lines", rows="4")["\n".join(safety.lines)],
            ],
            label(class_="field")[
                span[t("safety.veils")],
                small_hint(t("safety.veils_hint")),
                textarea(name="veils", rows="4")["\n".join(safety.veils)],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-sm")[t("safety.save")]
            ],
        ]
    else:
        lists = div(class_="safety-lists")[
            _list(t("safety.lines"), t("safety.lines_hint"), safety.lines, t("safety.none")),
            _list(t("safety.veils"), t("safety.veils_hint"), safety.veils, t("safety.none")),
        ]
    return section(class_="panel safety-world")[
        div(class_="panel-head")[
            h2[world.name], xcard_button(page, world.id) if not gamemaster else None
        ],
        lists,
        _suggestions(page, world, safety) if gamemaster else _suggest_form(page, world),
    ]


def small_hint(text: str) -> Element:
    return span(class_="field-hint")[text]


def _list(title: str, hint: str, items: Sequence[str], empty: str) -> Element:
    return div(class_="safety-list")[
        b[title],
        small_hint(hint),
        ul[[li[x] for x in items]] if items else p(class_="muted")[empty],
    ]


def _suggest_form(page: Page, world: World) -> Element:
    t = page.t
    return form(method="post", action=f"{SAFETY_URL}/{world.id}/suggest", class_="safety-suggest")[
        b[t("safety.suggest")],
        small_hint(t("safety.suggest_hint")),
        div(class_="safety-suggest-row")[
            select(name="kind", aria_label=t("safety.suggest_kind"))[
                option(value="line")[t("safety.line")], option(value="veil")[t("safety.veil")]
            ],
            input_el(
                type="text",
                name="text",
                maxlength="300",
                required=True,
                aria_label=t("safety.suggest"),
            ),
            button(type="submit", class_="btn btn-sm")[t("safety.suggest_send")],
        ],
    ]


def _suggestions(page: Page, world: World, safety: TableSafety) -> Node:
    t = page.t
    if not safety.suggested:
        return None
    return div(class_="safety-suggested")[
        b[t("safety.suggested")],
        ul[
            [
                li(class_="safety-suggested-row")[
                    span(class_="pill neutral")[t(f"safety.{s.kind}")],
                    span[s.text],
                    form(
                        method="post",
                        action=f"{SAFETY_URL}/{world.id}/settle",
                        class_="inline-form",
                    )[
                        hidden("index", str(n)),
                        button(type="submit", name="take", value="1", class_="btn btn-sm")[
                            t("safety.take")
                        ],
                        button(
                            type="submit", name="take", value="0", class_="btn btn-sm btn-ghost"
                        )[t("safety.drop")],
                    ],
                ]
                for n, s in enumerate(safety.suggested)
            ]
        ],
    ]


__all__ = ["SAFETY_URL", "safety_page", "xcard_button", "xcard_sent"]
