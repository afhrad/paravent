"""**Award XP** on the party view: experience for a challenge that was no fight — a
puzzle solved, a negotiation won, a milestone of the story — to the characters ticked,
each the same or the total shared out (``POST /gm/xp``, app/routes/gm.py)."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import parse_qs, urlsplit

from htpy import (
    Element,
    Node,
    button,
    div,
    fieldset,
    form,
    label,
    legend,
    option,
    p,
    select,
    span,
)
from htpy import input as input_el

from app.models.gamestate import World
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.tool_dialog import tool_button, tool_dialog

XP_URL = "/gm/xp"
# The address the award returns to: ``xp=<each>:<characters>``.
XP_DONE = "xp"


def xp_tool(page: Page, world: World | None, characters: Sequence[tuple[str, str]]) -> Node:
    """The button and its dialog; none in a world levelling by milestones."""
    if world is None or world.milestone or not characters:
        return None
    t = page.t
    body = form(method="post", action=XP_URL, class_="stack-form xp-form")[
        hidden("world", world.id),
        div(class_="field-row")[
            label(class_="field")[
                span[t("xp.amount")],
                input_el(
                    type="number",
                    name="xp",
                    min="1",
                    max="1000000",
                    required=True,
                    inputmode="numeric",
                    class_="mono",
                ),
            ],
            label(class_="field")[
                span[t("xp.how")],
                _how(page),
            ],
        ],
        label(class_="field")[
            span[t("xp.reason")],
            input_el(type="text", name="reason", maxlength="200", placeholder=t("xp.reason_hint")),
        ],
        fieldset(class_="xp-who")[
            legend[t("xp.who")],
            [
                label(class_="cs-heal-pick")[
                    input_el(type="checkbox", name="to", value=cid, checked=True), span[name]
                ]
                for cid, name in characters
            ],
            p(class_="field-hint")[t("xp.who_hint")],
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("sparkles", "sm"), span[t("xp.go")]
            ],
            button(type="button", class_="btn btn-ghost", data_close="1")[t("common.cancel")],
        ],
    ]
    return [
        tool_button(page, "xp", t("xp.award"), "sparkles"),
        tool_dialog(page, "xp", t("xp.award"), body),
    ]


def _how(page: Page) -> Element:
    t = page.t
    return select(name="how")[
        option(value="each")[t("xp.each")],
        option(value="split")[t("xp.split")],
    ]


def xp_notice(page: Page) -> Node:
    """After an award: how much each got, and how many."""
    raw = (parse_qs(urlsplit(page.path).query).get(XP_DONE) or [""])[0]
    each, _, count = raw.partition(":")
    if not (each.isdigit() and count.isdigit()):
        return None
    return div(class_="notice", role="status")[
        icon("sparkles"),
        span[page.t.plural("xp.done", int(count), xp=page.t.number(int(each)))],
    ]


__all__ = ["XP_DONE", "XP_URL", "xp_notice", "xp_tool"]
