"""A dice button inside a name field, with a small menu of other styles
(documentation/NAMES.md §5).

Both are submit buttons of the field's own form, sent to ``action`` with the menu's
``style``: the dice sends the menu as it stands, whose first choice is the fitting style,
so one press names at once and the menu is only for another. A hidden save button comes
first, so Enter in the field still saves what was typed. With HTMX the answer replaces
``target`` in place, so the page neither reloads nor scrolls; without it, the form
posts as a page.
"""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, button, details, div, label, optgroup, option, select, span, summary

from app.models.naming import SUBJECTS
from app.services.naming import PERSON, StyleEntry
from app.ui.context import Page
from app.ui.icons import icon
from app.ui.naming_pages import entry_label, style_options


def _place_options(page: Page, entries: Sequence[StyleEntry], selected: str) -> list[Element]:
    """One group per kind of place, its "any style" first."""
    t = page.t
    groups = []
    for subject in SUBJECTS:
        if subject == PERSON:
            continue
        rows = sorted(
            (
                e
                for e in entries
                if e.subject == subject and e.kind in ("subject", "own", "starter")
            ),
            key=lambda e: (e.kind != "subject", entry_label(page, e).casefold()),
        )
        if rows:
            groups.append(
                optgroup(label=t(f"naming.subject.{subject}"))[
                    [
                        option(value=e.key, selected=e.key == selected)[entry_label(page, e)]
                        for e in rows
                    ]
                ]
            )
    return groups


def name_roller(
    page: Page,
    action: str,
    entries: Sequence[StyleEntry],
    *,
    field_id: str,
    places: bool = False,
    hint: str | None = None,
    target: str = "closest .name-field",
    selected: str = "",
) -> Element:
    """The dice and the menu for the name field ``field_id``; ``places`` offers the
    styles of places, whose first, settlements, the dice rolls. ``selected`` is the
    style last rolled in, which the menu keeps, so the dice roll in it again."""
    t = page.t
    caption = t("naming.roll_place" if places else "naming.roll")
    # The answer is the target alone: a selection the field's form passes down (the
    # encounter's ``#plan``) is unset, or it would filter the answer to nothing.
    swap = {
        "hx_post": action,
        "hx_target": target,
        "hx_swap": "outerHTML",
        "hx_select": "unset",
        "hx_select_oob": "unset",
    }
    choices = (
        _place_options(page, entries, selected)
        if places
        else [
            option(value="", selected=not selected)[t("naming.roll_fitting")],
            *style_options(page, entries, selected),
        ]
    )
    return span(class_="name-roll")[
        button(type="submit", class_="sr-only", tabindex="-1", aria_hidden="true")[
            t("worlds.save")
        ],
        button(
            type="submit",
            formaction=action,
            formnovalidate=True,
            **swap,
            class_="btn btn-ghost btn-sm name-roll-dice",
            title=hint or caption,
            aria_label=caption,
        )[icon("dices", "sm")],
        details(class_="name-roll-menu")[
            summary(
                class_="btn btn-ghost btn-sm",
                title=t("naming.roll_choose"),
                aria_label=t("naming.roll_choose"),
            )[icon("chevron-down", "sm")],
            div(class_="name-roll-panel")[
                label(for_=f"{field_id}-style", class_="sr-only")[t("naming.roll_choose")],
                select(id=f"{field_id}-style", name="style")[choices],
                button(
                    type="submit",
                    formaction=action,
                    formnovalidate=True,
                    **swap,
                    class_="btn btn-sm",
                )[icon("dices", "sm"), span[caption]],
            ],
        ],
    ]


__all__ = ["name_roller"]
