"""The gamemaster's world menu at the left of the top bar (documentation/CURRENT-WORLD.md §3).

The world the whole table plays in, by name; open, the worlds to choose from, then **New
world**, **World settings** and **Delete world**. New world and the settings open dialogs:
the first is small and drawn with every page, the second loads the world form when it opens
(``ui.js``), so the book list is not sent with every page. Without the script each is a link
to its page.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    dialog,
    div,
    form,
    label,
    p,
    span,
    summary,
    textarea,
)
from htpy import input as input_el

from app.ui.context import Page
from app.ui.icons import icon

WORLDS_URL = "/gm/worlds"
SELECT_URL = "/gm/world/select"
SETTINGS_URL = "/gm/world/settings"


def _here(page: Page) -> str:
    """The page as it was asked for, without the settings dialog's own switch."""
    parts = urlsplit(page.path)
    kept = [(k, v) for k, vs in parse_qs(parts.query).items() if k != "settings" for v in vs]
    return parts.path + (f"?{urlencode(kept)}" if kept else "")


def world_menu(page: Page) -> Node:
    """The menu; the gamemaster's only (a player follows the table's world)."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    t = page.t
    current = page.world
    here = _here(page)
    return details(class_="world-menu")[
        summary(
            class_="btn btn-ghost world-menu-button",
            aria_label=t("world.menu", name=current[1]) if current else t("world.none"),
            title=t("world.menu", name=current[1]) if current else t("world.none"),
        )[
            icon("globe", "md"),
            span(class_="world-menu-name")[current[1] if current else t("world.none")],
            icon("chevron-down", "sm", muted=True),
        ],
        div(class_="usermenu-panel world-menu-panel")[
            form(method="post", action=SELECT_URL, class_="usermenu-section usermenu-first")[
                input_el(type="hidden", name="next", value=here),
                p(class_="world-menu-label eyebrow")[t("world.choose")],
                [
                    button(
                        type="submit",
                        name="world",
                        value=ident,
                        class_="usermenu-link world-menu-world",
                        aria_current="true" if current and ident == current[0] else None,
                    )[
                        icon("check" if current and ident == current[0] else "globe", "sm"),
                        span[name],
                    ]
                    for ident, name in page.worlds
                ],
            ]
            if page.worlds
            else None,
            div(class_="usermenu-section")[
                a(href=f"{WORLDS_URL}/new", class_="usermenu-link", data_dialog="new-world")[
                    icon("plus", "sm"), span[t("world.new")]
                ],
                a(
                    href=f"{WORLDS_URL}/{current[0]}/edit",
                    class_="usermenu-link",
                    data_dialog="world-settings",
                )[icon("settings", "sm"), span[t("world.settings")]]
                if current
                else None,
                a(href=f"{WORLDS_URL}/{current[0]}/names", class_="usermenu-link")[
                    icon("dices", "sm"), span[t("naming.world.title")]
                ]
                if current
                else None,
                a(href=f"{WORLDS_URL}/{current[0]}/claude", class_="usermenu-link")[
                    icon("sparkles", "sm"), span[t("instructions.title")]
                ]
                if current
                else None,
                a(href=f"{WORLDS_URL}/{current[0]}/delete", class_="usermenu-link world-delete")[
                    icon("trash-2", "sm"), span[t("world.delete")]
                ]
                if current
                else None,
            ],
        ],
    ]


def world_banner(page: Page) -> Node:
    """A page of another world than the table's says so, with **Switch to it**; nothing
    switches by itself (documentation/CURRENT-WORLD.md §1)."""
    owner = page.page_world
    if owner is None or page.world is None or owner[0] == page.world[0]:
        return None
    t = page.t
    return div(class_="world-banner", role="status")[
        icon("globe", "sm"),
        span[t("world.banner", world=owner[1], current=page.world[1])],
        form(method="post", action=SELECT_URL, class_="inline-form")[
            input_el(type="hidden", name="world", value=owner[0]),
            input_el(type="hidden", name="next", value=_here(page)),
            button(type="submit", class_="btn btn-sm")[t("world.switch", world=owner[1])],
        ],
    ]


def no_world_button(page: Page) -> Element:
    """**Create a world**, opening the new-world dialog (its page without the script)."""
    return a(href=f"{WORLDS_URL}/new", class_="btn btn-primary", data_dialog="new-world")[
        icon("plus"), span[page.t("world.none")]
    ]


def world_dialogs(page: Page) -> Node:
    """The dialogs the menu opens: a new world, and the current world's settings (open
    from the start with ``?settings=1``, as after making a world)."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    t = page.t
    here = _here(page)
    current = page.world
    open_settings = "settings=1" in urlsplit(page.path).query.split("&")
    return [
        dialog(id="new-world", class_="place-dialog", aria_labelledby="new-world-h")[
            form(method="post", action=WORLDS_URL, class_="stack-form")[
                input_el(type="hidden", name="next", value=here),
                div(class_="place-dialog-head")[
                    b(id="new-world-h")[t("world.new")],
                    button(
                        type="button",
                        class_="btn btn-sm btn-ghost",
                        data_close="1",
                        aria_label=t("images.close"),
                    )[icon("x", "sm")],
                ],
                p(class_="field-hint")[t("world.new_hint")],
                div(class_="field")[
                    label(for_="f-new-world")[t("worlds.name")],
                    input_el(
                        id="f-new-world",
                        type="text",
                        name="name",
                        required=True,
                        autocomplete="off",
                    ),
                ],
                div(class_="field")[
                    label(for_="f-new-world-description")[
                        t("worlds.description"), " ", span(class_="opt")[t("common.optional")]
                    ],
                    textarea(id="f-new-world-description", name="description", rows="3"),
                ],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("plus"), span[t("worlds.create")]
                    ],
                    button(type="button", class_="btn btn-ghost", data_close="1")[
                        t("common.cancel")
                    ],
                ],
            ]
        ],
        dialog(
            id="world-settings",
            class_="place-dialog world-settings",
            aria_label=t("world.settings"),
            data_src=f"{SETTINGS_URL}?{urlencode({'next': here})}",
            data_open_now="1" if open_settings else None,
        )[div(class_="dialog-body")[p(class_="muted")[t("world.loading")]]]
        if current
        else None,
    ]


__all__ = [
    "SELECT_URL",
    "SETTINGS_URL",
    "no_world_button",
    "world_banner",
    "world_dialogs",
    "world_menu",
]
