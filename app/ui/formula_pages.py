"""Magic item formulas (app/services/magic_crafting.py): **Give the formula** beside a
magic item of the world's books for the gamemaster, which opens a form of the world's
characters; who holds it then crafts it on their downtime page."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from htpy import Element, Node, button, div, form, label, p, span
from htpy import input as input_el

from app.services.activities import RARITIES, rarity_of
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

FORMULAS_URL = "/gm/formulas"


def craftable(category: str, data: Mapping[str, Any]) -> bool:
    """Whether an entry is a magic item a formula can make (a rarity the guides price,
    not a spell scroll, which is scribed)."""
    code = str(data.get("type") or "").split("|")[0].upper()
    return category == "item" and rarity_of(data.get("rarity")) in RARITIES and code != "SC"


def formula_button(
    page: Page, world_id: str | None, category: str, name: str, source: str, data: Mapping[str, Any]
) -> Node:
    """For the gamemaster in a world: "Give the formula", which loads the form in place."""
    if (
        world_id is None
        or not craftable(category, data)
        or page.identity is None
        or not page.identity.is_gamemaster
    ):
        return None
    query = urlencode({"world": world_id, "name": name, "source": source})
    return div(class_="trap-spring formula-give")[
        button(
            type="button",
            class_="btn btn-sm",
            hx_get=f"{FORMULAS_URL}/form?{query}",
            hx_target="closest .formula-give",
            hx_swap="innerHTML",
        )[icon("scroll", "sm"), span[page.t("crafting.give")]]
    ]


def formula_form(
    page: Page,
    world_id: str,
    name: str,
    source: str,
    members: Sequence[tuple[str, str, bool]],
    *,
    result: Node = None,
) -> Element:
    """Whom the formula goes to (``(id, name, holds it)``)."""
    t = page.t
    url = f"{FORMULAS_URL}/give"
    return form(
        method="post",
        action=url,
        hx_post=url,
        hx_target="closest .formula-give",
        hx_swap="innerHTML",
        class_="trap-form",
    )[
        hidden("world", world_id),
        hidden("name", name),
        hidden("source", source),
        result,
        p(class_="muted")[t("crafting.give_hint")],
        div(class_="trap-who")[
            [
                label(class_="party-clock-label")[
                    input_el(type="checkbox", name="character", value=cid, disabled=holds),
                    span[shown, f" ({t('crafting.holds')})" if holds else ""],
                ]
                for cid, shown, holds in members
            ]
        ]
        if members
        else p(class_="muted")[t("trap.no_one")],
        div(class_="trap-buttons")[
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("scroll", "sm"), span[t("crafting.give_now")]
            ]
            if members
            else None,
        ],
    ]


__all__ = ["FORMULAS_URL", "craftable", "formula_button", "formula_form"]
