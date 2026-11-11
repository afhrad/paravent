"""A help page: one of the app's own Markdown files, rendered like a chapter."""

from __future__ import annotations

from htpy import Element, a, div, span

from app.ui.context import Page
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.markdown import MarkdownContext, render_markdown


def help_page(page: Page, title: str, text: str, back: str) -> Element:
    """The help text, with a way back to where it was opened from."""
    rendered = render_markdown(text, MarkdownContext(t=page.t, breaks=False))
    return app_shell(
        page,
        title,
        div(class_="prose-page help-page")[
            a(href=back, class_="backlink")[icon("arrow-left"), span[page.t("ref.back")]],
            div(class_="md-body")[rendered.html],
        ],
    )


__all__ = ["help_page"]
