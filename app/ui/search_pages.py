"""``/search`` — the command palette as a page (documentation/DESIGN.md §4).

For links, history and no JavaScript: the same form and the same results fragment as the
⌘K layer, in the normal page frame.
"""

from __future__ import annotations

from htpy import Element, div

from app.services.search import SearchResult
from app.ui.context import Page
from app.ui.layout import app_shell
from app.ui.palette import search_form


def search_page(page: Page, result: SearchResult, world_id: str | None) -> Element:
    """``/search`` — the palette as a page, for links, history and no JavaScript."""
    return app_shell(
        page,
        page.t("search.title"),
        div(class_="search-page")[search_form(page, result, world_id, in_dialog=False)],
        world_id=world_id,
        palette_layer=False,
    )


__all__ = ["search_page"]
