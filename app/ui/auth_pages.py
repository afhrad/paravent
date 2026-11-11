"""Login, first-run setup, and the pages that explain a refusal.

Plain HTML forms that POST and redirect: they work without JavaScript, and a password
field has nothing to gain from HTMX.
"""

from __future__ import annotations

from htpy import Element, Node, a, button, div, form, h1, p, span

from app.services.auth import MIN_PASSWORD_LENGTH
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice, text_field
from app.ui.icons import icon
from app.ui.layout import app_shell, brand, public_shell


def _card(heading: str, lede: str, *body: Node) -> Element:
    return div(class_="auth-card")[
        div(class_="auth-head")[brand(), h1[heading], p(class_="auth-lede")[lede]],
        body,
    ]


def login_page(
    page: Page,
    *,
    error: str | None = None,
    notice_text: str | None = None,
    username: str = "",
    next_url: str = "",
) -> Element:
    """The login form. ``next_url`` is where to land afterwards."""
    t = page.t
    return public_shell(
        page,
        t("login.page_title"),
        _card(
            t("login.heading"),
            t("login.lede"),
            alert(error),
            notice(notice_text),
            form(method="post", action="/login", class_="auth-form")[
                hidden("next", next_url) if next_url else None,
                text_field(
                    "username",
                    t("login.username"),
                    value=username,
                    autocomplete="username",
                    autofocus=not username,
                ),
                text_field(
                    "password",
                    t("login.password"),
                    kind="password",
                    autocomplete="current-password",
                    autofocus=bool(username),
                ),
                button(type="submit", class_="btn btn-primary btn-block")[
                    span[t("login.submit")], icon("arrow-right")
                ],
            ],
        ),
    )


def setup_page(
    page: Page, *, error: str | None = None, username: str = "", display_name: str = ""
) -> Element:
    """First run: create the gamemaster account. Shown only while no user exists.

    There is no language field: the gamemaster gets the language the page is shown in,
    which the selector at the top switches. One control, not two that could disagree.
    """
    t = page.t
    return public_shell(
        page,
        t("setup.page_title"),
        _card(
            t("setup.heading"),
            t("setup.lede"),
            alert(error),
            form(method="post", action="/setup", class_="auth-form")[
                text_field(
                    "username",
                    t("field.username"),
                    value=username,
                    autocomplete="username",
                    autofocus=True,
                    hint=t("field.username_hint"),
                ),
                text_field(
                    "display_name",
                    t("field.display_name"),
                    value=display_name,
                    autocomplete="nickname",
                    required=False,
                    optional_text=t("common.optional"),
                ),
                text_field(
                    "password",
                    t("field.password"),
                    kind="password",
                    autocomplete="new-password",
                    hint=t("field.password_hint", min=MIN_PASSWORD_LENGTH),
                ),
                text_field(
                    "confirm",
                    t("field.confirm"),
                    kind="password",
                    autocomplete="new-password",
                ),
                p(class_="field-hint auth-langhint")[
                    icon("languages"), " ", t("setup.language_hint")
                ],
                button(type="submit", class_="btn btn-primary btn-block")[
                    icon("shield-check"), span[t("setup.submit")]
                ],
            ],
        ),
    )


def message_panel(icon_name: str, heading: str, lede: str, action: Node = None) -> Element:
    """A centred explanation with an optional way onward."""
    return div(class_="empty message")[
        icon(icon_name, "xl", muted=True), h1[heading], p[lede], action
    ]


def forbidden_page(page: Page, home_url: str) -> Element:
    """Shown when a logged-in user opens a screen their role may not use."""
    t = page.t
    return app_shell(
        page,
        t("forbidden.page_title"),
        message_panel(
            "shield",
            t("forbidden.heading"),
            t("forbidden.lede"),
            a(href=home_url, class_="btn btn-primary")[
                icon("arrow-left"), span[t("forbidden.back")]
            ],
        ),
    )


__all__ = ["forbidden_page", "login_page", "message_panel", "setup_page"]
