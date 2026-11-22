"""Giving an item to another character of the party, or dropping it: the form a sheet
item's context menu opens as a dialog (documentation/CONTEXT-MENUS.md §2), a page of its
own without the script."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, b, button, div, form, label, p, span
from htpy import input as input_el

from app.models.pc import PlayerCharacter
from app.ui.context import Page
from app.ui.forms import hidden, select_field
from app.ui.icons import icon
from app.ui.library_links import asked_in_dialog, dialog_or_page


def hand_url(character_id: str) -> str:
    """Where an item of the character's is given away or dropped."""
    return f"/characters/{character_id}/hand"


def hand_page(
    page: Page,
    pc: PlayerCharacter,
    index: int,
    item_name: str,
    party: Sequence[PlayerCharacter],
    *,
    drop: bool,
) -> Element:
    """To whom, and how many of a stack: ``party`` are the characters it can go to;
    with ``drop`` (or nobody to give it to), it is thrown away."""
    t = page.t
    carried = pc.inventory[index]
    in_dialog, next_page = asked_in_dialog(page)
    sheet = f"/characters/{pc.id}"
    dropping = drop or not party
    count_id = "f-hand-count"
    panel = div(class_="panel panel-danger" if dropping else "panel")[
        div(class_="panel-head")[
            span(class_="eyebrow")[pc.name or t("play.unnamed")],
            b[t("hand.drop_heading" if dropping else "hand.give_heading", item=item_name)],
        ],
        p[t("hand.drop_lede" if dropping else "hand.give_lede")],
        form(method="post", action=hand_url(pc.id))[
            hidden("index", str(index)),
            hidden("ref", carried.item.reference),
            hidden("next", next_page) if next_page.startswith("/") else None,
            hidden("to", "")
            if dropping
            else select_field(
                "to",
                t("hand.to"),
                [(c.id, c.name or t("play.unnamed")) for c in party],
                selected=party[0].id,
            ),
            div(class_="field")[
                label(for_=count_id)[t("hand.count", n=carried.quantity)],
                input_el(
                    id=count_id,
                    name="count",
                    type="number",
                    min="1",
                    max=str(carried.quantity),
                    value="1",
                    required=True,
                ),
            ]
            if carried.quantity > 1
            else hidden("count", "1"),
            p(class_="field-hint")[t("hand.worn_hint")]
            if carried.equipped or carried.attuned
            else None,
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-dmg" if dropping else "btn btn-primary")[
                    icon("trash-2" if dropping else "send"),
                    span[t("hand.drop" if dropping else "hand.give")],
                ],
                button(type="button", class_="btn btn-ghost", data_close="1")[t("common.cancel")]
                if in_dialog
                else None,
            ],
        ],
    ]
    title = t("hand.drop" if dropping else "hand.give")
    return dialog_or_page(page, title, sheet, panel)


__all__ = ["hand_page", "hand_url"]
