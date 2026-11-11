"""Themes at ``/themes`` (documentation/THEMES.md): every reader picks the look they read
Paravent in, the gamemaster also the table's, which everyone else sees by default."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, Node, b, button, code, div, form, img, li, p, section, span, ul

from app import __version__
from app.services.themes import OWN, Theme
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

THEMES_URL = "/themes"
PREVIEWS = ("preview.webp", "preview.png", "preview.jpg")


def _own_button(page: Page, value: str, current: bool) -> Element:
    """Read Paravent in this look myself."""
    t = page.t
    return form(method="post", action=THEMES_URL)[
        hidden("theme", value),
        button(
            type="submit",
            class_="btn btn-sm" + (" btn-primary" if current else ""),
            aria_pressed="true" if current else "false",
        )[
            icon("check") if current else icon("eye"),
            span[t("themes.mine") if current else t("themes.use")],
        ],
    ]


def _table_button(page: Page, value: str, current: bool) -> Node:
    """Make this the table's look; the gamemaster's only."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    t = page.t
    if current:
        return span(class_="pill")[icon("users"), " ", t("themes.table_current")]
    return form(method="post", action=f"{THEMES_URL}/table")[
        hidden("theme", value),
        button(type="submit", class_="btn btn-sm btn-ghost")[
            icon("users"), span[t("themes.use_table")]
        ],
    ]


def _problems(page: Page, theme: Theme) -> Node:
    if not theme.problems:
        return None
    return ul(class_="theme-problems")[
        [
            li[icon("triangle-alert", "sm"), " ", page.t(key, **values)]
            for key, values in theme.problems
        ]
    ]


def _facts(page: Page, theme: Theme) -> Element:
    t = page.t
    m = theme.manifest
    parts: list[Node] = [span[t("themes.version", version=m.version)]]
    if m.author:
        parts.append(span[t("themes.by", author=m.author)])
    parts.append(span[t("themes.shipped") if theme.shipped else t("themes.installed", id=theme.id)])
    # A shipped theme moves with the code; only a third party's may lag behind.
    if m.paravent and not theme.shipped and m.paravent != __version__:
        parts.append(span[t("themes.made_for", version=m.paravent)])
    if theme.fixed_mode:
        parts.append(span[t(f"themes.only_{theme.fixed_mode}")])
    if m.license:
        parts.append(span[t("themes.license", license=m.license)])
    return p(class_="theme-facts muted")[[[part, " · "] for part in parts[:-1]], parts[-1]]


def _preview(theme: Theme) -> Node:
    name = next((n for n in PREVIEWS if theme.file(n) is not None), None)
    if name is None:
        return None
    return img(class_="theme-preview", src=theme.url(name), alt="", loading="lazy")


def _card(page: Page, theme: Theme, *, own: str, table: str) -> Element:
    return section(class_="panel theme-card")[
        _preview(theme),
        div(class_="panel-head")[b[theme.manifest.name], _facts(page, theme)],
        p[theme.manifest.description] if theme.manifest.description else None,
        _problems(page, theme),
        div(class_="form-actions")[
            _own_button(page, theme.id, own == theme.id),
            _table_button(page, theme.id, table == theme.id),
        ],
    ]


def themes_page(page: Page, themes: Sequence[Theme], *, table: str) -> Element:
    """Paravent's own look, then every theme of the code and the library."""
    t = page.t
    own = page.identity.user.theme if page.identity is not None else ""
    names = {theme.id: theme.manifest.name for theme in themes}
    table_name = names.get(table, t("themes.paravent")) if table else t("themes.paravent")
    gm = page.identity is not None and page.identity.is_gamemaster
    return app_shell(
        page,
        t("themes.title"),
        div(class_="tr-page themes-page")[
            page_head(
                t("settings.menu") if gm else t("themes.eyebrow"),
                t("themes.title"),
                t("themes.lede"),
            ),
            section(class_="panel theme-card")[
                div(class_="panel-head")[
                    b[t("themes.follow_table")],
                    p(class_="muted")[t("themes.follow_table_hint", name=table_name)],
                ],
                div(class_="form-actions")[_own_button(page, "", own == "")],
            ],
            section(class_="panel theme-card")[
                div(class_="panel-head")[
                    b[t("themes.paravent")], p(class_="muted")[t("themes.paravent_hint")]
                ],
                div(class_="form-actions")[
                    _own_button(page, OWN, own == OWN),
                    _table_button(page, "", table == ""),
                ],
            ],
            [_card(page, theme, own=own, table=table) for theme in themes],
            p(class_="muted theme-install")[
                t("themes.install"), " ", code["library/themes/<name>/theme.json"]
            ]
            if gm
            else None,
        ],
    )


__all__ = ["THEMES_URL", "themes_page"]
