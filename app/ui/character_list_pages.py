"""The gamemaster's list of every character at the table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    li,
    option,
    p,
    select,
    span,
    summary,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import User, World
from app.models.pc import DraftStatus, PlayerCharacter
from app.services.characters import CharacterSummary
from app.ui.context import Page
from app.ui.forms import alert
from app.ui.home_pages import delete_link, empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.tokens import token


def _player_select(
    page: Page, players: Sequence[User], selected: str | None, field_id: str
) -> Element:
    wanted = (selected or "").casefold()
    names = {u.username.casefold() for u in players}
    return select(name="player", id=field_id, aria_label=page.t("chars.player"))[
        option(value="", selected=not selected)[page.t("chars.unassigned")],
        [
            option(value=u.username, selected=u.username.casefold() == wanted)[
                u.display_name or u.username
            ]
            for u in players
        ],
        # A file naming a player with no account keeps that name selectable.
        option(value=selected, selected=True)[f"@{selected}"]
        if selected and wanted not in names
        else None,
    ]


def _world_select(
    page: Page, worlds: Sequence[World], selected: str | None, field_id: str
) -> Element:
    return select(name="world", id=field_id, aria_label=page.t("party.world"))[
        option(value="", selected=not selected)[page.t("worlds.none")],
        [option(value=w.id, selected=w.id == selected)[w.name] for w in worlds],
    ]


def _row(
    page: Page, row: CharacterSummary, players: Sequence[User], worlds: Sequence[World]
) -> Element:
    t = page.t
    pc = row.character
    classes = " / ".join(f"{c.name} {c.level}" for c in pc.classes) or t("play.not_started")
    status = {
        DraftStatus.DRAFT: ("pill cond", "status.draft"),
        DraftStatus.READY: ("pill heal", "status.ready"),
        DraftStatus.RETIRED: ("pill neutral", "status.retired"),
    }[pc.status]
    assign_id = f"assign-{pc.id}"
    return li(class_="list-row list-row-wrap")[
        token(page.token_of("character", pc.world_id, pc.id)),
        div(class_="list-main")[
            a(href=f"/characters/{pc.id}", class_="list-title list-link")[
                pc.name or t("play.unnamed")
            ],
            span(class_="list-meta")[f"{classes} · {row.owner or t('chars.unassigned')}"],
        ],
        span(class_="pill dmg", title=row.file_error)[t("chars.file_error")]
        if row.file_error
        else None,
        span(class_=status[0])[t(status[1])],
        form(method="post", action=f"/gm/characters/{pc.id}/assign", class_="inline-form")[
            _player_select(page, players, pc.player, assign_id),
            button(type="submit", class_="btn btn-sm")[t("chars.assign")],
        ],
        form(method="post", action=f"/gm/characters/{pc.id}/world", class_="inline-form")[
            _world_select(page, worlds, pc.world_id, f"world-{pc.id}"),
            button(type="submit", class_="btn btn-sm")[t("chars.place")],
        ]
        if worlds
        else None,
        a(href=f"/characters/{pc.id}", class_="btn btn-sm btn-primary")[
            icon("book-open"), span(class_="list-action-label")[t("chars.open")]
        ],
        delete_link(page, pc),
    ]


def character_delete_page(page: Page, pc: PlayerCharacter, back: str, in_fight: bool) -> Element:
    """Ask before a character's file is removed, and say that it cannot come back."""
    t = page.t
    name = pc.name or t("play.unnamed")
    warning: Node = p[b[t("char_delete.in_fight")]] if in_fight else None
    return app_shell(
        page,
        t("char_delete.page_title"),
        div(class_="formpage")[
            a(href=back, class_="backlink")[icon("arrow-left"), span[t("common.cancel")]],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("char_delete.eyebrow")],
                    b[t("char_delete.heading", name=name)],
                ],
                p[t("char_delete.lede")],
                warning,
                form(method="post", action=f"/characters/{pc.id}/delete")[
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[
                            icon("trash-2"), span[t("char_delete.confirm")]
                        ],
                        a(href=back, class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
        ],
    )


def characters_page(
    page: Page,
    rows: Sequence[CharacterSummary],
    players: Sequence[User],
    unreadable: Mapping[str, str],
    worlds: Sequence[World] = (),
) -> Element:
    """Every character file, with who plays it."""
    t = page.t
    create = details(class_="create-menu")[
        summary(class_="btn btn-primary")[icon("plus"), span[t("chars.new")]],
        form(method="post", action="/gm/characters", class_="create-form")[
            div(class_="field")[
                span(class_="eyebrow")[t("chars.new")],
                input_el(
                    type="text",
                    name="name",
                    required=True,
                    placeholder=t("chars.name"),
                    aria_label=t("chars.name"),
                    autocomplete="off",
                ),
            ],
            _player_select(page, players, None, "new-player"),
            button(type="submit", class_="btn btn-primary")[icon("check"), span[t("chars.create")]],
            p(class_="field-hint")[t("chars.new_hint")],
            a(href="/chargen/new", class_="btn")[icon("sparkles"), span[t("chargen.with_wizard")]],
        ],
    ]
    problems = [
        alert(t("chars.unreadable", file=name, error=error))
        for name, error in sorted(unreadable.items())
    ]
    listing = (
        ul(class_="list")[[_row(page, row, players, worlds) for row in rows]]
        if rows
        else empty_state("users-round", t("chars.empty_heading"), t("chars.empty_text"))
    )
    return app_shell(
        page,
        t("chars.page_title"),
        [
            page_head(t("gm.eyebrow"), t("chars.heading"), t("chars.lede"), create),
            problems,
            listing,
        ],
    )


__all__ = ["character_delete_page", "characters_page"]
