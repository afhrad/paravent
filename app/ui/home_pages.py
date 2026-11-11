"""The two landing screens a login leads to, one per role.

Both are deliberately thin: the gamemaster screen and the character screen are their
own pieces of work. What exists here is the routing target and the shell, so login can
land somewhere real.
"""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, Node, a, div, h1, h2, li, p, span, ul

from app.models.pc import DraftStatus, PlayerCharacter
from app.ui.context import Page
from app.ui.icons import icon
from app.ui.layout import app_shell


def page_head(eyebrow: str, heading: str, lede: str, action: Node = None) -> Element:
    """The heading block at the top of a screen, with an optional primary action."""
    return div(class_="page-head")[
        div(class_="page-head-text")[
            span(class_="eyebrow")[eyebrow],
            h1[heading],
            p(class_="page-lede")[lede],
        ],
        div(class_="page-head-action")[action] if action is not None else None,
    ]


def empty_state(icon_name: str, heading: str, text: str, action: Node = None) -> Element:
    """What a list shows when it has nothing in it."""
    return div(class_="empty")[icon(icon_name, "xl", muted=True), h2[heading], p[text], action]


def _character_summary(page: Page, pc: PlayerCharacter) -> str:
    parts: list[str] = []
    if pc.species is not None:
        parts.append(pc.species.name)
    parts.extend(f"{c.name} {c.level}" for c in pc.classes)
    return " · ".join(parts) or page.t("play.not_started")


def _character_row(page: Page, pc: PlayerCharacter) -> Element:
    t = page.t
    is_draft = pc.status is DraftStatus.DRAFT
    # A draft begun in the wizard goes back there; one made by hand opens its sheet.
    in_wizard = is_draft and pc.wizard_step is not None
    return li(class_="list-row")[
        span(class_="avatar", aria_hidden="true")[(pc.name or "?")[:1].upper()],
        div(class_="list-main")[
            a(
                href=f"/chargen/{pc.id}" if in_wizard else f"/characters/{pc.id}",
                class_="list-title list-link",
            )[pc.name or t("play.unnamed")],
            span(class_="list-meta")[_character_summary(page, pc)],
        ],
        span(class_="pill cond" if is_draft else "pill heal")[
            t("status.draft") if is_draft else t("status.ready")
        ],
        delete_link(page, pc),
    ]


def delete_link(page: Page, pc: PlayerCharacter) -> Element:
    """The button that leads to the confirmation, for a row of a character list."""
    label = page.t("char_delete.action", name=pc.name or page.t("play.unnamed"))
    return a(
        href=f"/characters/{pc.id}/delete",
        class_="btn btn-sm btn-ghost btn-danger",
        title=label,
        aria_label=label,
    )[icon("trash-2")]


def player_home(page: Page, characters: Sequence[PlayerCharacter], fights: Node = None) -> Element:
    """The player's landing screen: their characters, and ``fights`` they are in now."""
    t = page.t
    body: Node
    if characters:
        body = ul(class_="list")[[_character_row(page, pc) for pc in characters]]
    else:
        body = empty_state("users-round", t("play.empty_heading"), t("play.empty_text"))
    new = a(href="/chargen/new", class_="btn btn-primary")[
        icon("sparkles"), span[t("chargen.title")]
    ]
    return app_shell(
        page,
        t("play.page_title"),
        div(class_="play-home")[
            page_head(t("play.eyebrow"), t("play.heading"), t("play.lede"), new), fights, body
        ],
    )


__all__ = ["delete_link", "empty_state", "page_head", "player_home"]
