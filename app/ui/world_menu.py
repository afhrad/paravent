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
from app.ui.forms import alert
from app.ui.icons import icon

WORLDS_URL = "/gm/worlds"
SELECT_URL = "/gm/world/select"
SETTINGS_URL = "/gm/world/settings"
EXPORT_URL = "/gm/world/export"
IMPORT_URL = "/gm/world/import"
DUPLICATE_URL = "/gm/world/duplicate"
LINKS_URL = "/gm/world/links"
# The query field that brings an import or a duplicate back with what went wrong.
TRANSFER_ERROR = "transfer_error"
# And the one that tells, on the new world's first page, what came along:
# ``import|duplicate:records:files:creatures kept:left out``.
TRANSFERRED = "transferred"


def _here(page: Page) -> str:
    """The page as it was asked for, without the settings dialog's own switch."""
    parts = urlsplit(page.path)
    kept = [
        (k, v)
        for k, vs in parse_qs(parts.query).items()
        if k not in ("settings", TRANSFER_ERROR)
        for v in vs
    ]
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
                button(type="button", class_="usermenu-link", data_dialog="import-world")[
                    icon("upload", "sm"), span[t("transfer.import")]
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
                a(href=LINKS_URL, class_="usermenu-link")[
                    icon("shield-check", "sm"),
                    span[t("links.title")],
                    span(class_="world-menu-counts")[
                        # How many lead nowhere, asked when the menu opens (ui.js).
                        span(
                            class_="pill cond world-menu-count",
                            data_count_src=f"{LINKS_URL}/count",
                            aria_label=t("links.count_label"),
                            hidden=True,
                        ),
                        # And, quieter, how many objects nothing uses.
                        span(
                            class_="pill world-menu-count world-menu-unused",
                            data_count_src=f"{LINKS_URL}/unused-count",
                            title=t("links.unused_label"),
                            aria_label=t("links.unused_label"),
                            hidden=True,
                        ),
                    ],
                ]
                if current
                else None,
                button(type="button", class_="usermenu-link", data_dialog="duplicate-world")[
                    icon("copy", "sm"), span[t("transfer.duplicate")]
                ]
                if current
                else None,
                a(href=EXPORT_URL, class_="usermenu-link", download="")[
                    icon("download", "sm"), span[t("transfer.export")]
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


def world_notice(page: Page) -> Element:
    """Hidden until the table moves to another world while the page is open; then it says
    which and offers to reload (``table-world.js``, documentation/CURRENT-WORLD.md §8).
    The page's world is the one it was drawn in, so a page drawn after the switch stays
    quiet."""
    t = page.t
    return div(
        id="world-notice",
        class_="world-notice",
        role="status",
        hidden=True,
        data_world=page.world[0] if page.world is not None else "",
        data_text=t("world.notice", world="{world}"),
        data_text_none=t("world.notice_none"),
    )[
        icon("globe", "sm"),
        span(class_="world-notice-text"),
        button(type="button", class_="btn btn-primary btn-sm", data_world_reload="")[
            icon("refresh-cw", "sm"), span[t("world.notice_reload")]
        ],
        button(
            type="button",
            class_="btn btn-ghost btn-sm",
            data_world_dismiss="",
            aria_label=t("close"),
        )[icon("x", "sm")],
    ]


def transfer_notice(page: Page) -> Node:
    """After an import or a duplicate, what came along and what was left out."""
    raw = (parse_qs(urlsplit(page.path).query).get(TRANSFERRED) or [""])[0]
    how, *numbers = raw.split(":")
    if (
        how not in ("import", "duplicate")
        or len(numbers) != 4
        or not all(n.isdigit() for n in numbers)
    ):
        return None
    records, files, kept, skipped = (int(n) for n in numbers)
    t = page.t
    world = page.world[1] if page.world else ""
    parts = [t(f"transfer.done.{how}", world=world, records=records, files=files)]
    if kept:
        parts.append(t.plural("transfer.done.kept", kept))
    if skipped:
        parts.append(t.plural("transfer.done.skipped", skipped))
    return div(class_="notice", role="status")[icon("check"), span[" ".join(parts)]]


def no_world_button(page: Page) -> Element:
    """**Create a world**, opening the new-world dialog (its page without the script)."""
    return a(href=f"{WORLDS_URL}/new", class_="btn btn-primary", data_dialog="new-world")[
        icon("plus"), span[page.t("world.none")]
    ]


def _dialog_head(page: Page, ident: str, title: str) -> Element:
    return div(class_="place-dialog-head")[
        b(id=f"{ident}-h")[title],
        button(
            type="button",
            class_="btn btn-sm btn-ghost",
            data_close="1",
            aria_label=page.t("images.close"),
        )[icon("x", "sm")],
    ]


def _play_box(page: Page, ident: str) -> Element:
    """Whether the play so far comes along: log, clock, fights, journal, session notes."""
    t = page.t
    return div(class_="field field-check")[
        input_el(id=f"{ident}-play", type="checkbox", name="play", value="1"),
        div[
            label(for_=f"{ident}-play")[t("transfer.play")],
            p(class_="field-hint")[t("transfer.play_hint")],
        ],
    ]


def _actions(page: Page, icon_name: str, submit: str) -> Element:
    t = page.t
    return div(class_="form-actions")[
        button(type="submit", class_="btn btn-primary")[icon(icon_name), span[submit]],
        button(type="button", class_="btn btn-ghost", data_close="1")[t("common.cancel")],
    ]


def _import_dialog(page: Page, here: str, error: str | None) -> Element:
    """A world package (from **Export world**, here or at another table) made into a new
    world the table then plays in."""
    t = page.t
    return dialog(
        id="import-world",
        class_="place-dialog",
        aria_labelledby="import-world-h",
        data_open_now="1" if error else None,
    )[
        form(
            method="post",
            action=IMPORT_URL,
            enctype="multipart/form-data",
            class_="stack-form",
            data_busy=t("transfer.importing"),
        )[
            input_el(type="hidden", name="next", value=here),
            _dialog_head(page, "import-world", t("transfer.import")),
            p(class_="field-hint")[t("transfer.import_hint")],
            alert(t(error) if error else None),
            div(class_="field")[
                label(for_="f-import-file")[t("transfer.file")],
                input_el(
                    id="f-import-file",
                    type="file",
                    name="file",
                    accept=".zip,application/zip",
                    required=True,
                ),
            ],
            div(class_="field")[
                label(for_="f-import-name")[
                    t("worlds.name"), " ", span(class_="opt")[t("common.optional")]
                ],
                input_el(
                    id="f-import-name",
                    type="text",
                    name="name",
                    autocomplete="off",
                    placeholder=t("transfer.name_as_exported"),
                ),
            ],
            _play_box(page, "f-import"),
            _actions(page, "upload", t("transfer.import_submit")),
        ]
    ]


def _duplicate_dialog(page: Page, here: str, name: str, error: str | None) -> Element:
    """A copy of the table's world, for another group: a new name, the play optional."""
    t = page.t
    return dialog(
        id="duplicate-world",
        class_="place-dialog",
        aria_labelledby="duplicate-world-h",
        data_open_now="1" if error else None,
    )[
        form(
            method="post",
            action=DUPLICATE_URL,
            class_="stack-form",
            data_busy=t("transfer.duplicating"),
        )[
            input_el(type="hidden", name="next", value=here),
            _dialog_head(page, "duplicate-world", t("transfer.duplicate")),
            p(class_="field-hint")[t("transfer.duplicate_hint", world=name)],
            alert(t(error) if error else None),
            div(class_="field")[
                label(for_="f-duplicate-name")[t("worlds.name")],
                input_el(
                    id="f-duplicate-name",
                    type="text",
                    name="name",
                    required=True,
                    autocomplete="off",
                    value=t("transfer.copy_name", world=name),
                ),
            ],
            _play_box(page, "f-duplicate"),
            _actions(page, "copy", t("transfer.duplicate_submit")),
        ]
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
    error = (parse_qs(urlsplit(page.path).query).get(TRANSFER_ERROR) or [""])[0]
    if not error.startswith("transfer.error."):
        error = ""
    # A duplicate's one error is its missing name; every other one is an import's.
    error_for = "duplicate" if error == "transfer.error.no_name" else "import"
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
        _import_dialog(page, here, error if error_for == "import" else None),
        _duplicate_dialog(page, here, current[1], error if error_for == "duplicate" else None)
        if current
        else None,
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
    "DUPLICATE_URL",
    "EXPORT_URL",
    "IMPORT_URL",
    "LINKS_URL",
    "SELECT_URL",
    "SETTINGS_URL",
    "TRANSFER_ERROR",
    "TRANSFERRED",
    "no_world_button",
    "world_banner",
    "world_dialogs",
    "transfer_notice",
    "world_menu",
]
