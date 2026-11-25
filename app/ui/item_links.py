"""Ways into the item and spell builders from elsewhere (app/ui/item_builder_pages.py,
app/ui/spell_builder_pages.py), kept apart so the reference pages can offer them
without importing the builders' pages."""

from __future__ import annotations

from htpy import Node, button, form, span

from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

ITEMS_URL = "/gm/items"
SPELLS_URL = "/gm/spells"


def item_url(design_id: str) -> str:
    return f"{ITEMS_URL}/{design_id}"


def spell_url(design_id: str) -> str:
    return f"{SPELLS_URL}/{design_id}"


def make_from_button(page: Page, ref: str, *, spell: bool = False) -> Node:
    """On a magic item's or spell's reference page, for the gamemaster: begin a design
    from it."""
    action = SPELLS_URL if spell else ITEMS_URL
    return form(method="post", action=action, class_="inline-form ref-make-from")[
        hidden("based_on", ref),
        button(type="submit", class_="btn btn-sm")[
            icon("gem", "sm"), span[page.t("itembuilder.make_from")]
        ],
    ]


__all__ = ["ITEMS_URL", "SPELLS_URL", "item_url", "make_from_button", "spell_url"]
