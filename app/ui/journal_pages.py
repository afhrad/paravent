"""The players' journal and the gamemaster's share form (documentation/JOURNAL.md)."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    div,
    form,
    img,
    label,
    li,
    p,
    section,
    span,
    ul,
)
from htpy import input as input_el
from markupsafe import Markup

from app.models.gamestate import Handout
from app.services import image_effects
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.tokens import token

JOURNAL_URL = "/journal"
SHARE_URL = "/gm/share"


def handout_url(handout_id: str) -> str:
    """One handout's page."""
    return f"{JOURNAL_URL}/{handout_id}"


def picture_url(handout_id: str, spec: str) -> str:
    """Where a handout's copied picture is read (``map.png|torn`` asks for its effects)."""
    name, effects = image_effects.split(spec)
    return image_effects.with_effects(f"{JOURNAL_URL}/{handout_id}/img/{name}", effects)


def _when(handout: Handout) -> str:
    return handout.created_at.strftime("%d.%m.%Y %H:%M")


def share_page(
    page: Page,
    *,
    chapter_title: str,
    chapter_href: str,
    preview: Node,
    fields: dict[str, str],
    title: str,
    recipients: Sequence[tuple[str, str]],
    world_id: str | None = None,
    error: str | None = None,
) -> Element:
    """What will be handed over, to whom, under which title."""
    t = page.t
    body: Node
    if not recipients:
        body = empty_state("users", t("journal.no_characters"), t("journal.no_characters_hint"))
    else:
        body = form(method="post", action=SHARE_URL, class_="panel stack-form journal-share")[
            [hidden(name, value) for name, value in fields.items()],
            div(class_="field")[
                label(for_="f-title")[t("journal.title_label")],
                input_el(id="f-title", name="title", value=title, maxlength="120"),
            ],
            div(class_="field")[
                span(class_="field-label")[t("journal.to")],
                div(class_="journal-recipients")[
                    [
                        label(class_="check-inline")[
                            input_el(type="checkbox", name="characters", value=pc_id, checked=True),
                            token(page.token_of("character", world_id, pc_id), "sm"),
                            name,
                        ]
                        for pc_id, name in recipients
                    ]
                ],
                p(class_="field-hint")[t("journal.to_hint")],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("send"), span[t("journal.share_button")]
                ],
                a(href=chapter_href, class_="btn btn-ghost")[t("common.cancel")],
            ],
        ]
    return app_shell(
        page,
        t("journal.share_title"),
        div(class_="tr-page journal-page")[
            page_head(chapter_title, t("journal.share_title"), t("journal.share_lede")),
            alert(error),
            section(class_="journal-preview")[preview],
            body,
        ],
    )


def _row(page: Page, handout: Handout, names: Sequence[str] | None) -> Element:
    t = page.t
    unread = (
        names is None
        and page.identity is not None
        and (page.identity.user.username not in handout.read_by)
    )
    return li(class_="list-row journal-row" + (" is-unread" if unread else ""))[
        span(class_="avatar journal-kind", aria_hidden="true")[
            icon("eye" if handout.kind == "image" else "scroll")
        ],
        div(class_="list-main")[
            a(href=handout_url(handout.id), class_="list-title list-link")[handout.title],
            span(class_="list-meta")[
                _when(handout),
                [" · ", t("journal.to_names", names=", ".join(names))] if names else None,
            ],
        ],
        # Who it went to, by their tokens (the gamemaster's list).
        span(class_="tok-stack")[
            [
                token(page.token_of("character", handout.world_id, c), "sm")
                for c in handout.characters
            ]
        ]
        if names
        else None,
        span(class_="journal-new")[t("journal.new")] if unread else None,
    ]


def journal_page(
    page: Page,
    handouts: Sequence[Handout],
    *,
    names: dict[str, list[str]] | None = None,
    message: str | None = None,
) -> Element:
    """Every handout the reader holds, newest first. The gamemaster's list names who each
    went to (``names``); a player's marks what they have not opened."""
    t = page.t
    gm = names is not None
    rows = [_row(page, h, names.get(h.id) if names is not None else None) for h in handouts]
    return app_shell(
        page,
        t("journal.title"),
        div(class_="tr-page journal-page")[
            page_head(
                t("journal.eyebrow_gm") if gm else t("journal.eyebrow"),
                t("journal.title"),
                t("journal.lede_gm") if gm else t("journal.lede"),
            ),
            notice(message),
            ul(class_="list journal-list")[rows]
            if rows
            else empty_state(
                "scroll",
                t("journal.empty"),
                t("journal.empty_hint_gm") if gm else t("journal.empty_hint"),
            ),
        ],
    )


def handout_page(
    page: Page,
    handout: Handout,
    content: Markup,
    *,
    names: Sequence[str],
    readers: Sequence[str],
    chapter_href: str | None,
    message: str | None = None,
) -> Element:
    """One handout as its players read it; the gamemaster also sees who has it and who
    opened it, and can withdraw it."""
    t = page.t
    gm = page.identity is not None and page.identity.is_gamemaster
    body: Node = (
        img(
            class_="journal-picture" + (" fx" if image_effects.split(handout.image)[1] else ""),
            src=picture_url(handout.id, handout.image),
            alt=handout.title,
        )
        if handout.kind == "image"
        else div(class_="md-body journal-body")[content]
    )
    return app_shell(
        page,
        handout.title,
        div(class_="tr-page journal-page")[
            a(href=JOURNAL_URL, class_="backlink")[icon("arrow-left"), span[t("journal.title")]],
            page_head(_when(handout), handout.title, ""),
            notice(message),
            body,
            section(class_="panel journal-meta")[
                p[b[t("journal.to")], " ", ", ".join(names)],
                p(class_="muted")[
                    b[t("journal.opened_by")],
                    " ",
                    ", ".join(readers) if readers else t("journal.opened_nobody"),
                ],
                div(class_="form-actions")[
                    a(href=chapter_href, class_="btn")[
                        icon("book-open"), span[t("journal.chapter")]
                    ]
                    if chapter_href
                    else None,
                    form(method="post", action=f"{handout_url(handout.id)}/delete")[
                        button(type="submit", class_="btn btn-ghost btn-danger")[
                            icon("trash-2"), span[t("journal.withdraw")]
                        ]
                    ],
                ],
            ]
            if gm
            else None,
        ],
    )


def share_href(chapter_id: str, kind: str, key: str, back: str) -> str:
    """The share form for one box or picture of a chapter."""
    return f"{SHARE_URL}?{urlencode({'chapter': chapter_id, kind: key, 'back': back})}"


__all__ = [
    "JOURNAL_URL",
    "SHARE_URL",
    "handout_page",
    "handout_url",
    "journal_page",
    "picture_url",
    "share_href",
    "share_page",
]
