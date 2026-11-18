"""Start and End for an adventure or a chapter: on its page, in its adventure's list
and on the gamemaster's table (app/services/adventures.py, ``start`` and ``end``)."""

from __future__ import annotations

from htpy import Node, button, form, span
from htpy import input as input_el

from app.ui.context import Page
from app.ui.icons import icon


def play_button(page: Page, url: str, status: str, *, back: str = "", small_: bool = False) -> Node:
    """Start (a planned one), End (a running one) or Resume (a finished one), posted to
    ``url`` (an adventure's or chapter's own) and back to ``back``; none for a draft."""
    if status == "draft":
        return None
    t = page.t
    op = "end" if status == "running" else "start"
    words = {"planned": "adventures.start", "running": "adventures.end"}.get(
        status, "adventures.resume"
    )
    tone = "btn-primary" if op == "start" and status == "planned" else ""
    return form(method="post", action=f"{url}/{op}", class_="inline-form play-form")[
        input_el(type="hidden", name="back", value=back) if back else None,
        button(type="submit", class_=f"btn {'btn-sm' if small_ else ''} {tone}".strip())[
            icon("play" if op == "start" else "circle-check", "sm"), span[t(words)]
        ],
    ]


__all__ = ["play_button"]
