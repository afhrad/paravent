"""Addresses and small pieces shared by the pages of a world's content.

Worlds, adventures, chapters, NPCs and treasure link to one another constantly; keeping
their URLs (and the breadcrumb trail) in one place lets those page modules import each
other's links without importing each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    code,
    details,
    dialog,
    div,
    form,
    img,
    li,
    nav,
    ol,
    p,
    span,
    summary,
    textarea,
    ul,
)

from app.services import image_effects
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.world_menu import TRASH_RESTORE_URL

WORLDS_URL = "/gm/worlds"
ADVENTURES_URL = "/gm/adventures"
CHAPTERS_URL = "/gm/chapters"
NPCS_URL = "/gm/npcs"
TREASURE_URL = "/gm/treasure"
LOCATIONS_URL = "/gm/places"
MEDIA_UPLOAD_URL = "/gm/media"
MEDIA_URL = "/media"
# A missing NPC, place, treasure or encounter a text names, made on the gamemaster's word
# (app/routes/editor.py); the kinds it may make.
NEW_OBJECT_URL = "/gm/editor/new"
RELINK_URL = "/gm/editor/relink"
RENAME_BACK_URL = "/gm/editor/rename-back"
CREATABLE = frozenset({"npc", "encounter", "treasure", "place"})


def new_object_url(kind: str, name: str, scope: str, back: str, text: str = "") -> str:
    """The page asking whether to make ``name`` of ``kind`` in ``scope`` (``c:``, ``a:``,
    ``w:`` and an id), returning to ``back`` if not; ``text`` is the world, adventure or
    chapter whose link it is, where the link can be changed instead."""
    query = {"kind": kind, "name": name, "scope": scope, "back": back}
    if text:
        query["text"] = text
    return f"{NEW_OBJECT_URL}?" + urlencode(query)


def media_url(record_id: str, relative: str) -> str:
    """Where the browser fetches an image a record names (``app/routes/media.py``); a
    spec with effects (``media/x.jpg|torn``) asks for its rendering."""
    path, effects = image_effects.split(relative)
    return image_effects.with_effects(f"{MEDIA_URL}/{record_id}/{path}", effects)


def character_url(character_id: str) -> str:
    """A character's sheet, which ``{@character}`` links to."""
    return f"/characters/{character_id}"


def world_url(world_id: str) -> str:
    """A world's overview."""
    return f"{WORLDS_URL}/{world_id}"


def adventure_url(adventure_id: str) -> str:
    """An adventure's page."""
    return f"{ADVENTURES_URL}/{adventure_id}"


def chapter_url(chapter_id: str) -> str:
    """A chapter's page."""
    return f"{CHAPTERS_URL}/{chapter_id}"


def npc_url(npc_id: str) -> str:
    """An NPC's page."""
    return f"{NPCS_URL}/{npc_id}"


def location_url(location_id: str) -> str:
    """A place's page, which ``{@place}`` links to."""
    return f"{LOCATIONS_URL}/{location_id}"


def treasure_url(treasure_id: str) -> str:
    """A treasure's page."""
    return f"{TREASURE_URL}/{treasure_id}"


def crumbs(page: Page, *trail: tuple[str, str | None]) -> Element:
    """The breadcrumb trail; the last entry, with no link, is where the reader is. The
    worlds' list is no step of it: the world menu holds the worlds, so a trail starts at
    the world (documentation/CURRENT-WORLD.md §5)."""
    steps = [(text, href) for text, href in trail if href != WORLDS_URL]
    return nav(class_="crumbs", aria_label=page.t("adventures.crumbs"))[
        ol[[li[a(href=href)[text] if href else span[text]] for text, href in steps]]
    ]


def cover(record_id: str, path: str | None, alt: str) -> Node:
    """A record's cover image, or nothing."""
    if not path:
        return None
    relative, effects = image_effects.split(path)
    if effects and image_effects.frame_of(effects) is None:
        # Cut to the banner first, so the page shows the edges the effects drew; a frame
        # chosen by hand is the shape instead.
        path = image_effects.join(relative, ("banner", *effects))
    return img(class_="cover" + (" fx" if effects else ""), src=media_url(record_id, path), alt=alt)


def delete_link(page: Page, href: str, label: str, *, text: bool = False) -> Element:
    """The way to an object's delete page: a trash icon on a row (named for screen
    readers), or with ``text`` a button with its label, for a page head."""
    if text:
        return a(href=href, class_="btn btn-ghost btn-danger")[icon("trash-2"), span[label]]
    return a(href=href, class_="btn btn-sm btn-ghost btn-danger", aria_label=label, title=label)[
        icon("trash-2")
    ]


def copy_snippet(page: Page, text: str) -> Element:
    """A tag or line to paste into an editor (``{@npc Hob Wade}``, ``![](media/x.png)``),
    copied to the clipboard by a click, a tap or Enter (``ui.js``), "Copied" shown on it."""
    t = page.t
    return code(
        class_="image-snippet copy-snippet",
        role="button",
        tabindex="0",
        title=t("copy.hint"),
        data_copied=t("copy.done"),
    )[text]


def new_button(page: Page, dialog_id: str, label: str) -> Element:
    """**New …** beside a list's heading, opening :func:`new_dialog`; without the script
    it reloads the page with the dialog open (``?new=1``)."""
    return a(href="?new=1", class_="btn btn-primary", data_dialog=dialog_id)[
        icon("plus"), span[label]
    ]


def new_dialog(
    page: Page,
    dialog_id: str,
    *,
    title: str,
    action: str,
    back: str,
    fields: Node,
    submit: str,
    more: Node = None,
    is_open: bool = False,
    upload: bool = False,
) -> Element:
    """A new object of a world's list in one dialog — places, NPCs, treasure alike:
    ``fields`` posted to ``action``, ``submit`` making it, ``more`` other buttons of the
    form (a roll instead); Cancel and × close it, or go ``back`` without the script.
    Open from the start with ``is_open`` (``?new=1``, or after a refused one)."""
    t = page.t
    head = f"{dialog_id}-h"
    return dialog(id=dialog_id, class_="place-dialog", aria_labelledby=head, open=is_open)[
        form(
            method="post",
            action=action,
            enctype="multipart/form-data" if upload else None,
            class_="stack-form",
        )[
            div(class_="place-dialog-head")[
                b(id=head)[title],
                a(
                    href=back,
                    class_="btn btn-sm btn-ghost",
                    data_close="1",
                    aria_label=t("images.close"),
                )[icon("x", "sm")],
            ],
            fields,
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[icon("plus"), span[submit]],
                more,
                a(href=back, class_="btn btn-ghost", data_close="1")[t("common.cancel")],
            ],
        ]
    ]


def confirm_page(
    page: Page,
    *,
    title: str,
    heading: str,
    lede: str,
    action: str,
    back: str,
    submit: str,
    hint: str | None = None,
    choice: Node = None,
) -> Element:
    """Ask before deleting: what goes, and a button that does it; ``choice`` sits in the
    form above the button (how much goes with it)."""
    t = page.t
    return app_shell(
        page,
        title,
        div(class_="formpage")[
            a(href=back, class_="backlink")[icon("arrow-left"), span[t("common.cancel")]],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[span(class_="eyebrow")[t("gm.eyebrow")], b[heading]],
                p[lede],
                p(class_="field-hint")[hint] if hint else None,
                form(method="post", action=action)[
                    choice,
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[icon("x"), span[submit]],
                        a(href=back, class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
        ],
    )


def new_object_page(
    page: Page,
    *,
    kind: str,
    name: str,
    scope: str,
    where: str,
    back: str,
    near: Sequence[tuple[str, str, str]] = (),
    text: str = "",
    trashed: tuple[str, datetime] | None = None,
) -> Element:
    """Ask before making what a link names but the world lacks: what, and where it goes
    (the text's chapter, adventure or world, where the link looks first). ``near`` are
    ``(id, name, url)`` of what the text probably meant, offered first: to open, to point
    the link at (in ``text``, when known), or to rename to the name written. ``trashed``
    (the trash entry's id and when it went) offers it back first, when it was deleted
    lately under that name."""
    t = page.t
    what = t(f"md.embed.{kind}")
    restore = (
        div(class_="create-near create-restore")[
            p[
                t(
                    "md.create_deleted",
                    name=name,
                    when=trashed[1].astimezone().strftime("%Y-%m-%d %H:%M"),
                )
            ],
            form(method="post", action=TRASH_RESTORE_URL, class_="inline-form")[
                hidden("id", trashed[0]),
                hidden("back", back),
                button(type="submit", class_="btn btn-primary")[
                    icon("rotate-ccw"), span[t("trash.restore")]
                ],
            ],
        ]
        if trashed
        else None
    )
    return app_shell(
        page,
        t("md.create_title", name=name),
        div(class_="formpage")[
            a(href=back, class_="backlink")[icon("arrow-left"), span[t("md.create_back")]],
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[what], b[t("md.create_title", name=name)]
                ],
                p[t("md.create_lede", name=name, kind=what, where=where)],
                restore,
                div(class_="create-near")[
                    p(class_="field-hint")[t("md.create_near")],
                    ul(class_="create-near-list create-near-rows")[
                        [
                            li(class_="create-near-row")[
                                a(href=url, class_="btn btn-sm")[icon("arrow-right", "sm"), found],
                                form(method="post", action=RELINK_URL, class_="inline-form")[
                                    hidden("text", text),
                                    hidden("kind", kind),
                                    hidden("old", name),
                                    hidden("new", found),
                                    hidden("back", back),
                                    button(type="submit", class_="btn btn-sm")[
                                        icon("pencil", "sm"),
                                        span[t("md.create_relink", name=found)],
                                    ],
                                ]
                                if text
                                else None,
                                form(method="post", action=RENAME_BACK_URL, class_="inline-form")[
                                    hidden("kind", kind),
                                    hidden("id", ident),
                                    hidden("name", name),
                                    hidden("back", back),
                                    button(type="submit", class_="btn btn-sm btn-ghost")[
                                        span[t("links.rename", old=found, new=name)]
                                    ],
                                ],
                            ]
                            for ident, found, url in near
                        ]
                    ],
                ]
                if near
                else None,
                form(method="post", action=NEW_OBJECT_URL)[
                    hidden("kind", kind),
                    hidden("name", name),
                    hidden("scope", scope),
                    div(class_="form-actions")[
                        # With the deleted one on offer, making a new one is the quieter way.
                        button(type="submit", class_="btn" if trashed else "btn btn-primary")[
                            icon("plus"), span[t("md.create_submit", kind=what)]
                        ],
                        a(href=back, class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
        ],
    )


__all__ = [
    "CREATABLE",
    "NEW_OBJECT_URL",
    "TRASH_RESTORE_URL",
    "new_object_page",
    "new_object_url",
    "confirm_page",
    "copy_snippet",
    "delete_link",
    "new_button",
    "new_dialog",
    "ADVENTURES_URL",
    "CHAPTERS_URL",
    "MEDIA_UPLOAD_URL",
    "MEDIA_URL",
    "NPCS_URL",
    "TREASURE_URL",
    "WORLDS_URL",
    "adventure_url",
    "chapter_url",
    "cover",
    "crumbs",
    "media_url",
    "LOCATIONS_URL",
    "location_url",
    "npc_url",
    "treasure_url",
    "world_url",
]


def saved_version(page: Page, current: str | None) -> Node:
    """After a refused save: the text saved meanwhile, to compare with the editor's and
    copy from. Saving again replaces it with the editor's text."""
    if current is None:
        return None
    t = page.t
    return details(class_="panel conflict-panel", open=True)[
        summary(class_="panel-head")[b[t("prose.saved_version")]],
        p(class_="field-hint")[t("prose.saved_version_hint")],
        textarea(
            class_="prose-editor", rows="12", readonly=True, aria_label=t("prose.saved_version")
        )[current],
    ]
