"""The gamemaster's player-management screens: list, create/edit form, delete confirm."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, a, b, button, div, form, li, p, span, ul

from app.models.gamestate import User
from app.services.auth import MIN_PASSWORD_LENGTH
from app.services.players import PlayerInput, PlayerSummary
from app.ui.auth_pages import message_panel
from app.ui.context import Page
from app.ui.forms import alert, checkbox_field, notice, select_field, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell, initials

PLAYERS_URL = "/gm/players"


def _name(user: User) -> str:
    return user.display_name or user.username


def _player_row(page: Page, summary: PlayerSummary) -> Element:
    t = page.t
    user = summary.user
    names = {lang.code: lang.name for lang in t.languages}
    return li(class_="list-row")[
        span(class_="avatar", aria_hidden="true")[initials(_name(user))],
        div(class_="list-main")[
            span(class_="list-title")[_name(user)],
            span(class_="list-meta")[
                f"@{user.username} · {names.get(user.language, user.language)} · "
                f"{t.plural('players.characters', summary.characters)}"
            ],
        ],
        span(class_="pill heal" if user.active else "pill neutral")[
            t("players.active") if user.active else t("players.inactive")
        ],
        a(
            href=f"{PLAYERS_URL}/{user.id}",
            class_="btn btn-sm",
            aria_label=t("players.edit_label", name=_name(user)),
        )[icon("pencil"), span(class_="list-action-label")[t("players.edit")]],
    ]


def players_page(
    page: Page, summaries: Sequence[PlayerSummary], *, notice_text: str | None = None
) -> Element:
    """Every player account, with a way to add one."""
    t = page.t
    new_button = a(href=f"{PLAYERS_URL}/new", class_="btn btn-primary")[
        icon("plus"), span[t("players.new")]
    ]
    listing = (
        ul(class_="list")[[_player_row(page, s) for s in summaries]]
        if summaries
        else empty_state("users", t("players.empty_heading"), t("players.empty_text"))
    )
    return app_shell(
        page,
        t("players.page_title"),
        [
            page_head(t("players.eyebrow"), t("players.heading"), t("players.lede"), new_button),
            notice(notice_text),
            listing,
        ],
    )


def player_form_page(
    page: Page,
    *,
    player: User | None,
    values: PlayerInput,
    error: str | None = None,
) -> Element:
    """Create a player (``player`` is None) or edit one."""
    t = page.t
    creating = player is None
    if player is None:
        title, action, delete_link = t("player_form.new_title"), PLAYERS_URL, None
    else:
        title = t("player_form.edit_title", name=_name(player))
        action = f"{PLAYERS_URL}/{player.id}"
        delete_link = a(
            href=f"{PLAYERS_URL}/{player.id}/delete",
            class_="btn btn-ghost btn-danger form-actions-end",
        )[t("player_form.delete")]
    languages = [(lang.code, lang.name) for lang in t.languages]

    password_fields = [
        text_field(
            "password",
            t("field.password") if creating else t("player_form.new_password"),
            kind="password",
            autocomplete="new-password",
            required=creating,
            hint=(
                t("field.password_hint", min=MIN_PASSWORD_LENGTH)
                if creating
                else t("player_form.new_password_hint")
            ),
        ),
        text_field(
            "confirm",
            t("field.confirm"),
            kind="password",
            autocomplete="new-password",
            required=creating,
        ),
    ]

    return app_shell(
        page,
        title,
        div(class_="formpage")[
            a(href=PLAYERS_URL, class_="backlink")[icon("arrow-left"), span[t("player_form.back")]],
            div(class_="panel")[
                div(class_="panel-head")[span(class_="eyebrow")[t("players.eyebrow")], b[title]],
                alert(error),
                form(method="post", action=action, class_="stack-form")[
                    text_field(
                        "username",
                        t("field.username"),
                        value=values.username,
                        autocomplete="off",
                        autofocus=creating,
                        hint=t("field.username_hint"),
                    ),
                    text_field(
                        "display_name",
                        t("field.display_name"),
                        value=values.display_name,
                        autocomplete="off",
                        required=False,
                        optional_text=t("common.optional"),
                    ),
                    select_field(
                        "language",
                        t("field.language"),
                        languages,
                        selected=values.language,
                        hint=t("player_form.language_hint"),
                    ),
                    password_fields,
                    None
                    if creating
                    else checkbox_field(
                        "active",
                        t("player_form.active"),
                        checked=values.active,
                        hint=t("player_form.active_hint"),
                    ),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"),
                            span[t("player_form.create") if creating else t("player_form.save")],
                        ],
                        a(href=PLAYERS_URL, class_="btn btn-ghost")[t("common.cancel")],
                        delete_link,
                    ],
                ],
            ],
        ],
    )


def player_delete_page(page: Page, player: User, characters: int) -> Element:
    """Confirm a deletion, saying exactly what happens to the player's characters."""
    t = page.t
    kept = (
        t.plural("player_delete.characters", characters)
        if characters
        else t("player_delete.no_characters")
    )
    return app_shell(
        page,
        t("player_delete.page_title"),
        div(class_="formpage")[
            a(href=f"{PLAYERS_URL}/{player.id}", class_="backlink")[
                icon("arrow-left"), span[t("common.cancel")]
            ],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("players.eyebrow")],
                    b[t("player_delete.heading", name=_name(player))],
                ],
                p[t("player_delete.lede")],
                p[kept],
                p(class_="field-hint")[t("player_delete.alternative")],
                form(method="post", action=f"{PLAYERS_URL}/{player.id}/delete")[
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[
                            icon("x"), span[t("player_delete.confirm")]
                        ],
                        a(href=f"{PLAYERS_URL}/{player.id}", class_="btn btn-ghost")[
                            t("common.cancel")
                        ],
                    ],
                ],
            ],
        ],
    )


def player_not_found_page(page: Page) -> Element:
    """The id does not name a player — deleted meanwhile, or never one."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "users",
            t("notfound.player"),
            t("notfound.player_lede"),
            a(href=PLAYERS_URL, class_="btn btn-primary")[
                icon("arrow-left"), span[t("player_form.back")]
            ],
        ),
    )


__all__ = [
    "PLAYERS_URL",
    "player_delete_page",
    "player_form_page",
    "player_not_found_page",
    "players_page",
]
