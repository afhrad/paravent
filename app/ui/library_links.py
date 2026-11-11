"""Addresses and small pieces shared by the pages of a world's content.

Worlds, adventures, chapters, NPCs and treasure link to one another constantly; keeping
their URLs (and the breadcrumb trail) in one place lets those page modules import each
other's links without importing each other.
"""

from __future__ import annotations

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
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
)

from app.services import image_effects
from app.ui.context import Page
from app.ui.icons import icon
from app.ui.layout import app_shell

WORLDS_URL = "/gm/worlds"
ADVENTURES_URL = "/gm/adventures"
CHAPTERS_URL = "/gm/chapters"
NPCS_URL = "/gm/npcs"
TREASURE_URL = "/gm/treasure"
LOCATIONS_URL = "/gm/places"
MEDIA_UPLOAD_URL = "/gm/media"
MEDIA_URL = "/media"


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
    """The breadcrumb trail; the last entry, with no link, is where the reader is."""
    return nav(class_="crumbs", aria_label=page.t("adventures.crumbs"))[
        ol[[li[a(href=href)[text] if href else span[text]] for text, href in trail]]
    ]


def cover(record_id: str, path: str | None, alt: str) -> Node:
    """A record's cover image, or nothing."""
    if not path:
        return None
    relative, effects = image_effects.split(path)
    if effects:  # cut to the banner first, so the page shows the edges the effects drew
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


__all__ = [
    "confirm_page",
    "delete_link",
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
