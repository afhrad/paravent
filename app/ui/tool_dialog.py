"""A tool's form in a dialog of its own, opened from a narrow panel (the party view's
clock, shop and chase): the panel keeps its summary, the form gets room.

The opener is a link to ``?tool=<key>`` naming the dialog (``data-dialog``): with the
script it opens the dialog in place (``ui.js``); without it the page reloads with that
dialog already open. The forms inside swap their panel (htmx), which closes the dialog
with the old markup; one that is worked with several times in a row (a shop's shelf)
carries ``data-keep-open`` and opens again after the swap.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from htpy import Element, Node, a, b, dialog, div, span

from app.ui.context import Page
from app.ui.icons import icon

TOOL = "tool"


def tool_open(page: Page, key: str) -> bool:
    """Whether the page was asked with this tool's dialog open (``?tool=<key>``)."""
    return key in parse_qs(urlsplit(page.path).query).get(TOOL, [])


def tool_button(page: Page, key: str, label: str, icon_name: str | None = None) -> Element:
    """The link that opens a tool's dialog."""
    return a(href=f"?{TOOL}={key}", class_="btn btn-sm tool-open", data_dialog=f"tool-{key}")[
        icon(icon_name, "sm") if icon_name else None, span[label]
    ]


def tool_dialog(
    page: Page,
    key: str,
    title: str,
    body: Node,
    *,
    keep_open: bool = False,
    is_open: bool = False,
) -> Element:
    """The dialog itself: a head with its title and ×, then ``body``; open from the
    start with ``is_open`` (a form refused, shown again) or ``?tool=<key>``."""
    ident = f"tool-{key}"
    back = urlsplit(page.path).path
    return dialog(
        id=ident,
        class_="place-dialog tool-dialog",
        aria_labelledby=f"{ident}-h",
        open=is_open or tool_open(page, key),
        data_keep_open="1" if keep_open else None,
    )[
        div(class_="place-dialog-head")[
            b(id=f"{ident}-h")[title],
            a(
                href=back,
                class_="btn btn-sm btn-ghost",
                data_close="1",
                aria_label=page.t("images.close"),
            )[icon("x", "sm")],
        ],
        div(class_="tool-dialog-body")[body],
    ]


__all__ = ["TOOL", "tool_button", "tool_dialog", "tool_open"]
