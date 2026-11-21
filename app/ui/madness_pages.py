"""**Madness** on the party view (app/services/madness.py): a saving throw the gamemaster
sets, psychic damage for mental stress, and a grade of madness for those who fail, each
rolled on its own, to the characters ticked (``POST /gm/madness``,
app/routes/afflictions.py)."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import parse_qs, urlsplit

from htpy import (
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
from app.services.madness import LEVELS
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.tool_dialog import tool_button, tool_dialog

MADNESS_URL = "/gm/madness"
# The address it returns to: ``mad=<failed>:<characters>``.
MADNESS_DONE = "mad"
SAVES = ("wis", "int", "cha")


def madness_tool(
    page: Page, world: World | None, characters: Sequence[tuple[str, str]], *, tables: bool
) -> Node:
    """The button and its dialog. ``tables`` says whether the effects come from the
    2014 guide's tables (else the 2024 guide's prolonged effects)."""
    if world is None or not characters:
        return None
    t = page.t
    body = form(method="post", action=MADNESS_URL, class_="stack-form madness-form")[
        hidden("world", world.id),
        p(class_="field-hint")[t("madness.lede_2014" if tables else "madness.lede_2024")],
        label(class_="field")[
            span[t("madness.level")],
            select(name="level")[
                option(value="")[t("madness.level.none")],
                [
                    option(value=level, selected=level == "short")[t(f"madness.level.{level}")]
                    for level in LEVELS
                ],
            ],
        ],
        div(class_="field-row")[
            label(class_="field")[
                span[t("madness.save")],
                select(name="ability")[
                    option(value="")[t("madness.no_save")],
                    [option(value=a, selected=a == "wis")[t(f"ability.{a}")] for a in SAVES],
                ],
            ],
            label(class_="field")[
                span[t("madness.dc")],
                input_el(
                    type="number",
                    name="dc",
                    min="1",
                    max="30",
                    value="15",
                    inputmode="numeric",
                    class_="mono",
                ),
            ],
        ],
        div(class_="field-row")[
            label(class_="field")[
                span[t("madness.damage")],
                input_el(
                    type="text",
                    name="damage",
                    maxlength="20",
                    placeholder=t("madness.damage_hint"),
                    class_="mono",
                ),
            ],
            label(class_="cs-heal-pick madness-half")[
                input_el(type="checkbox", name="half", value="1"), span[t("madness.half")]
            ],
        ],
        fieldset(class_="xp-who")[
            legend[t("madness.who")],
            [
                label(class_="cs-heal-pick")[
                    input_el(type="checkbox", name="to", value=cid), span[name]
                ]
                for cid, name in characters
            ],
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("skull", "sm"), span[t("madness.go")]
            ],
            button(type="button", class_="btn btn-ghost", data_close="1")[t("common.cancel")],
        ],
    ]
    return [
        tool_button(page, "madness", t("madness.title"), "skull"),
        tool_dialog(page, "madness", t("madness.title"), body),
    ]


def madness_notice(page: Page) -> Node:
    """Afterwards: how many of those ticked it took."""
    raw = (parse_qs(urlsplit(page.path).query).get(MADNESS_DONE) or [""])[0]
    failed, _, count = raw.partition(":")
    if not (failed.isdigit() and count.isdigit()):
        return None
    return div(class_="notice", role="status")[
        icon("skull"),
        span[page.t.plural("madness.done", int(count), failed=int(failed))],
    ]


__all__ = ["MADNESS_DONE", "MADNESS_URL", "madness_notice", "madness_tool"]
