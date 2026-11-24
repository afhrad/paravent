"""The running costs of property (app/services/upkeep.py): the party view's dialog to give
a character a property and take it back, and the owner's list on the sheet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

from htpy import Node, b, button, div, form, label, li, option, p, select, span, ul
from htpy import input as input_el

from app.models.gamestate import Property
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.icons import icon
from app.ui.shop_pages import coin_text
from app.ui.tool_dialog import tool_button, tool_dialog

UPKEEP_URL = "/gm/upkeep"


def upkeep_tool(
    page: Page,
    world_id: str | None,
    properties: Sequence[Property],
    names: Mapping[str, str],
    costs: Sequence[tuple[str, int]],
    *,
    refused: bool = False,
) -> Node:
    """The button and its dialog: each property with its owner and price a day, and the
    form to give one, priced by the guide's table (``costs``) or by hand."""
    if world_id is None or not names:
        return None
    t = page.t
    base = f"{UPKEEP_URL}/{quote(world_id)}"
    held = ul(class_="upkeep-list")[
        [
            li[
                span[b[x.name], " · ", names.get(x.character_id, "?")],
                span(class_="mono")[t("upkeep.per_day", cost=coin_text(page, x.per_day))],
                form(method="post", action=f"{base}/remove", class_="inline-form")[
                    hidden("property", x.id),
                    button(
                        type="submit",
                        class_="btn btn-sm btn-ghost",
                        title=t("upkeep.remove"),
                        aria_label=t("upkeep.remove"),
                    )[icon("x", "sm")],
                ],
            ]
            for x in properties
        ]
    ]
    body = [
        alert(t("upkeep.error.bad")) if refused else None,
        p(class_="field-hint")[t("upkeep.lede" if costs else "upkeep.lede_no_table")],
        held if properties else p(class_="muted")[t("upkeep.none")],
        form(method="post", action=f"{base}/give", class_="tool-section")[
            div(class_="tool-grid")[
                label(class_="field")[
                    span[t("upkeep.owner")],
                    select(name="character")[[option(value=c)[n] for c, n in names.items()]],
                ],
                label(class_="field")[
                    span[t("upkeep.kind")],
                    select(name="kind")[
                        option(value="")[t("upkeep.own_price")],
                        [
                            option(value=name)[f"{name} · {coin_text(page, price)}"]
                            for name, price in costs
                        ],
                    ],
                ]
                if costs
                else None,
                label(class_="field")[
                    span[t("upkeep.name")],
                    input_el(type="text", name="name", maxlength="120"),
                ],
                label(class_="field")[
                    span[t("upkeep.gp")],
                    input_el(
                        type="text", name="gp", maxlength="10", inputmode="decimal", class_="mono"
                    ),
                ],
            ],
            p(class_="field-hint")[t("upkeep.form_hint")],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("plus", "sm"), span[t("upkeep.give")]
                ]
            ],
        ],
    ]
    return [
        tool_button(page, "upkeep", t("upkeep.title"), "landmark"),
        tool_dialog(page, "upkeep", t("upkeep.title"), body, is_open=refused),
    ]


def sheet_upkeep(page: Page, properties: Sequence[Property]) -> Node:
    """What the character owns and what it costs each day, on the Gear view."""
    if not properties:
        return None
    t = page.t
    return div(class_="cs-prof cs-upkeep")[
        span(class_="eyebrow")[t("upkeep.owned")],
        ul[
            [
                li[b[x.name], " · ", t("upkeep.per_day", cost=coin_text(page, x.per_day))]
                for x in properties
            ]
        ],
    ]


def upkeep_dialog_url(world_id: str) -> str:
    """The party view with the dialog open again."""
    return f"/gm?world={quote(world_id)}&tool=upkeep"


__all__ = ["UPKEEP_URL", "sheet_upkeep", "upkeep_dialog_url", "upkeep_tool"]
