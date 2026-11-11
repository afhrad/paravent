"""The gamemaster's "view as player" switch (documentation/PLAYER-VIEW.md §2)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from htpy import Node, a, span

from app.ui.context import Page
from app.ui.icons import icon


def view_switch(page: Page) -> Node:
    """For the gamemaster: this page as a player of the world reads it, or back."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    parts = urlsplit(page.path)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "as"]
    if not page.as_player:
        query.append(("as", "player"))
    href = urlunsplit(("", "", parts.path, urlencode(query), ""))
    return a(href=href, class_="btn btn-sm view-switch")[
        icon("eye-off" if page.as_player else "eye", "sm"),
        span[page.t("view.as_gamemaster" if page.as_player else "view.as_player")],
    ]


__all__ = ["view_switch"]
