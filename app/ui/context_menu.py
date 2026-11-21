"""Context menus (documentation/CONTEXT-MENUS.md): the actions on one thing where it is.

An element that has a menu carries ``data-menu`` with the address of its menu
(:func:`data_menu`); ``static/js/menu.js`` opens it on a right-click, a long press, the
ContextMenu key or the element's **⋯** (:func:`menu_button`), fetching the fragment
:func:`menu_fragment` draws from :class:`MenuItem` groups. Every item is a link, a form
that posts, or a dialog to open, so the routes behind it stay the only ones that act and
check the reader's role; a menu is a shortcut to them, never another way in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from htpy import Element, Node, a, b, button, div, form, span

from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

MENU_URL = "/menu"


@dataclass(frozen=True)
class MenuItem:
    """One action: a link (``href``, ``new_tab``), a form posting ``fields`` to ``post``
    (through htmx onto ``hx_target`` when set), or a dialog whose fragment is fetched from
    ``dialog``. ``danger`` marks a delete; ``hint`` is a short note at its end."""

    label: str
    icon: str
    href: str = ""
    new_tab: bool = False
    post: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)
    hx_target: str = ""
    dialog: str = ""
    danger: bool = False
    hint: str = ""


@dataclass(frozen=True)
class MenuGroup:
    """Items shown together, under ``label`` when it has one; groups are parted by a line."""

    items: Sequence[MenuItem]
    label: str = ""


def data_menu(kind: str, *parts: str, **query: str) -> str:
    """The address of a menu: ``/menu/<kind>/<part>…`` with ``query``."""
    path = "/".join([MENU_URL, kind, *(quote(p, safe="") for p in parts)])
    return f"{path}?{urlencode(query)}" if query else path


def menu_button(page: Page, label: str, fallback: str = "") -> Element:
    """The **⋯** beside an element with a menu: it opens the element's menu; without the
    script it leads to ``fallback`` (the object's own page), or does nothing."""
    attrs = {
        "class_": "btn btn-sm btn-ghost ctx-more",
        "data_menu_open": "",
        "aria_label": page.t("menu.more", name=label),
        "aria_haspopup": "menu",
        "title": page.t("menu.more", name=label),
    }
    if fallback:
        return a(href=fallback, **attrs)[icon("ellipsis", "sm")]
    return button(type="button", **attrs)[icon("ellipsis", "sm")]


def row_menu(page: Page, href: str, label: str) -> Element:
    """The **⋯** of a list row, a campaign card: the menu of what ``href`` leads to
    (``/menu/link``), which a right-click or long press on the row opens too; without the
    script it leads there."""
    attrs = {
        "class_": "btn btn-sm btn-ghost ctx-more",
        "data_menu": f"{MENU_URL}/link?{urlencode({'href': href})}",
        "data_menu_open": "",
        "aria_label": page.t("menu.more", name=label),
        "aria_haspopup": "menu",
        "title": page.t("menu.more", name=label),
    }
    return a(href=href, **attrs)[icon("ellipsis", "sm")]


def _item(item: MenuItem) -> Element:
    classes = "ctx-item" + (" danger" if item.danger else "")
    inner = [
        icon(item.icon, "sm"),
        span(class_="ctx-label")[item.label],
        span(class_="ctx-hint")[item.hint] if item.hint else None,
    ]
    if item.post:
        htmx = (
            {"hx_post": item.post, "hx_target": item.hx_target, "hx_swap": "outerHTML"}
            if item.hx_target
            else {}
        )
        return form(method="post", action=item.post, class_="ctx-form", **htmx)[
            [hidden(k, v) for k, v in item.fields.items()],
            button(type="submit", class_=classes, role="menuitem")[inner],
        ]
    if item.dialog:
        return button(type="button", class_=classes, role="menuitem", data_menu_dialog=item.dialog)[
            inner
        ]
    return a(
        href=item.href,
        class_=classes,
        role="menuitem",
        target="_blank" if item.new_tab else None,
        rel="noopener" if item.new_tab else None,
    )[inner]


def menu_fragment(page: Page, title: str, meta: str, groups: Sequence[MenuGroup]) -> Element:
    """The menu as the script shows it: its title, what it is, then the groups of items;
    empty groups are left out."""
    shown = [g for g in groups if g.items]
    parts: list[Node] = []
    for n, group in enumerate(shown):
        if n:
            parts.append(div(class_="ctx-sep", role="separator"))
        if group.label:
            parts.append(div(class_="ctx-group")[group.label])
        parts.extend(_item(item) for item in group.items)
    return div(class_="ctx-menu", role="menu", aria_label=title)[
        div(class_="ctx-head")[b[title], span[meta] if meta else None],
        parts or div(class_="ctx-empty")[page.t("menu.nothing")],
    ]


__all__ = [
    "MENU_URL",
    "MenuGroup",
    "MenuItem",
    "data_menu",
    "menu_button",
    "menu_fragment",
    "row_menu",
]
