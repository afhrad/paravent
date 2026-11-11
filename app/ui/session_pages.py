"""Session notes (documentation/ADVENTURES.md §4b): a world's days of play, newest
first, each in the gamemaster's words, and the form to write one."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from htpy import (
    Element,
    a,
    article,
    button,
    div,
    form,
    h2,
    label,
    option,
    p,
    section,
    select,
    span,
    textarea,
)
from htpy import input as input_el

from app.models.gamestate import SessionNote, World
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import WORLDS_URL, crumbs, delete_link, saved_version, world_url
from app.ui.markdown import Rendered

SESSIONS_URL = "/gm/sessions"


def sessions_url(world_id: str) -> str:
    """A world's session notes."""
    return f"{world_url(world_id)}/sessions"


def note_url(note_id: str) -> str:
    """One note's form."""
    return f"{SESSIONS_URL}/{note_id}"


def day_text(page: Page, day: date) -> str:
    """A day as the language writes it: 2026-10-25, 25.10.2026."""
    return page.t("sessions.day", y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}")


def _adventure_select(
    page: Page, adventures: Sequence[tuple[str, str]], selected: str | None
) -> Element:
    return div(class_="field")[
        label(for_="f-adventure")[page.t("sessions.adventure")],
        select(id="f-adventure", name="adventure")[
            option(value="")[page.t("sessions.no_adventure")],
            [option(value=aid, selected=aid == selected)[name] for aid, name in adventures],
        ],
    ]


def sessions_page(
    page: Page,
    world: World,
    notes: Sequence[tuple[SessionNote, Rendered, str | None]],
    adventures: Sequence[tuple[str, str]],
    *,
    today: date,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """The notes, newest first, each rendered with the adventure it belongs to."""
    t = page.t
    return app_shell(
        page,
        t("sessions.title"),
        div(class_="prose-page sessions-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("sessions.title"), None),
            ),
            page_head(t("adventures.world_eyebrow"), t("sessions.title"), t("sessions.lede")),
            notice(notice_text),
            alert(error),
            section(class_="panel")[
                form(method="post", action=sessions_url(world.id), class_="session-new")[
                    div(class_="field")[
                        label(for_="f-day")[t("sessions.date")],
                        input_el(
                            id="f-day",
                            name="day",
                            type="date",
                            value=today.isoformat(),
                            required=True,
                        ),
                    ],
                    _adventure_select(page, adventures, None),
                    button(type="submit", class_="btn btn-primary")[
                        icon("plus"), span[t("sessions.new")]
                    ],
                ],
                p(class_="field-hint")[t("sessions.new_hint")],
            ],
            [
                article(class_="panel session-note", id=f"note-{note.id}")[
                    div(class_="session-head")[
                        h2[day_text(page, note.day)],
                        span(class_="muted")[adventure] if adventure else None,
                        a(href=f"{note_url(note.id)}/edit", class_="btn btn-sm btn-ghost")[
                            icon("pencil"), span[t("players.edit")]
                        ],
                        delete_link(page, f"{note_url(note.id)}/delete", t("sessions.delete")),
                    ],
                    div(class_="md-body")[rendered.html]
                    if rendered.html.strip()
                    else p(class_="muted")[t("sessions.empty_note")],
                ]
                for note, rendered, adventure in notes
            ]
            if notes
            else empty_state("scroll", t("sessions.empty_heading"), t("sessions.empty_text")),
        ],
        world_id=world.id,
    )


def session_form_page(
    page: Page,
    world: World,
    note: SessionNote,
    adventures: Sequence[tuple[str, str]],
    *,
    day: str,
    adventure_id: str | None,
    text: str,
    base: str,
    scope_value: str,
    conflict: str | None = None,
    error: str | None = None,
) -> Element:
    """Edit a note: its day, its adventure and its text."""
    t = page.t
    back = f"{sessions_url(world.id)}#note-{note.id}"
    return app_shell(
        page,
        t("sessions.edit_title", day=day_text(page, note.day)),
        div(class_="formpage formpage-wide")[
            a(href=back, class_="backlink")[icon("arrow-left"), span[t("sessions.title")]],
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[world.name],
                    span[t("sessions.edit_title", day=day_text(page, note.day))],
                ],
                alert(error),
                form(method="post", action=note_url(note.id), class_="stack-form")[
                    div(class_="field-row")[
                        div(class_="field field-narrow")[
                            label(for_="f-day")[t("sessions.date")],
                            input_el(id="f-day", name="day", type="date", value=day, required=True),
                        ],
                        _adventure_select(page, adventures, adventure_id),
                    ],
                    div(class_="field")[
                        label(for_="f-text")[t("sessions.text")],
                        p(class_="field-hint", id="f-text-hint")[t("sessions.text_hint")],
                        textarea(
                            id="f-text",
                            name="text",
                            rows="16",
                            class_="prose-editor",
                            data_scope=scope_value,
                            aria_describedby="f-text-hint",
                        )[text],
                        hidden("base", base),
                    ],
                    saved_version(page, conflict),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"), span[t("worlds.save")]
                        ],
                        a(href=back, class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
        ],
        world_id=world.id,
        insert_layer=True,
    )


__all__ = [
    "SESSIONS_URL",
    "day_text",
    "note_url",
    "session_form_page",
    "sessions_page",
    "sessions_url",
]
