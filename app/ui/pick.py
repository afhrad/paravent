"""Choosing several pieces of a list at once, to copy, move or save them
(app/services/world_copy.py).

A list wraps itself in :func:`pick_scope` with :func:`pick_bar` above it and a
:func:`pick_box` in each row. The boxes show only while the bar's **Select…** is open
(CSS ``:has``), and belong to the bar's form by its id, so a row needs no form of its own.
The bar's buttons open the copy page for the ticked ones: to another world, moved, or as
a file. No script.
"""

from __future__ import annotations

from htpy import Element, Node, VoidElement, button, details, div, form, span, summary
from htpy import input as input_el

from app.ui.context import Page
from app.ui.copy_links import COPY_URL
from app.ui.forms import hidden
from app.ui.icons import icon


def _form_id(kind: str) -> str:
    return f"pick-{kind}"


def pick_bar(page: Page, kind: str) -> Element:
    """**Select…**, opening the boxes and the buttons for the ticked ones."""
    t = page.t
    return details(class_="pick")[
        summary(class_="btn btn-sm btn-ghost")[icon("check", "sm"), span[t("pick.select")]],
        form(method="get", action=COPY_URL, id=_form_id(kind), class_="pick-actions")[
            hidden("kind", kind),
            span(class_="field-hint")[t("pick.hint")],
            button(type="submit", name="mode", value="copy", class_="btn btn-sm")[
                icon("copy", "sm"), span[t("menu.copy_world")]
            ],
            button(type="submit", name="mode", value="move", class_="btn btn-sm")[
                icon("send", "sm"), span[t("menu.move_world_other")]
            ],
            button(type="submit", name="mode", value="file", class_="btn btn-sm")[
                icon("download", "sm"), span[t("pick.file")]
            ],
        ],
    ]


def pick_box(page: Page, kind: str, record_id: str, name: str) -> VoidElement:
    """A row's box, belonging to the bar's form."""
    return input_el(
        type="checkbox",
        name="id",
        value=record_id,
        form=_form_id(kind),
        class_="pick-box",
        aria_label=page.t("pick.choose", name=name),
    )


def pick_scope(page: Page, kind: str, listing: Node) -> Element:
    """The bar above a list whose rows carry boxes."""
    return div(class_="pick-scope")[pick_bar(page, kind), listing]


__all__ = ["pick_bar", "pick_box", "pick_scope"]
