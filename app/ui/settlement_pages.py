"""Rolling a place as a settlement (app/services/settlement_gen.py): its size, then each
part of the guide's tables with a box to keep it and a die to roll it again; **Keep**
writes what is ticked into the place's notes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from htpy import (
    Element,
    b,
    button,
    div,
    form,
    h1,
    label,
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Location, World
from app.services.settlement_gen import Size
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import WORLDS_URL, crumbs, location_url, world_url


def size_text(page: Page, size: Size) -> str:
    """ "Town (501–5,000)", in the reader's words for the size."""
    name = page.t(f"settlement.size.{size.key}")
    return f"{name} ({size.population})" if size.population else name


def settlement_page(
    page: Page,
    location: Location,
    world: World,
    *,
    sizes: Sequence[Size],
    size: str,
    rolled: Mapping[str, str],
    sources: Mapping[str, str],
    kept: frozenset[str],
) -> Element:
    """The settlement as rolled, every part ticked to keep."""
    t = page.t
    action = f"{location_url(location.id)}/settlement"
    rows = [
        li(class_="settle-row")[
            label(class_="settle-keep")[
                input_el(type="checkbox", name="keep", value=part, checked=part in kept),
                span(class_="settle-label")[t(f"settlement.part.{part}")],
            ],
            span(class_="settle-value")[value],
            hidden(f"v_{part}", value),
            small(class_="muted mono settle-book")[sources.get(part, "")],
            button(
                type="submit",
                name="action",
                value=f"reroll:{part}",
                class_="btn btn-sm btn-ghost",
                title=t("settlement.reroll"),
                aria_label=t("settlement.reroll"),
            )[icon("dices", "sm")],
        ]
        for part, value in rolled.items()
    ]
    return app_shell(
        page,
        t("settlement.title", name=location.name),
        div(class_="prose-page settlement-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (location.name, location_url(location.id)),
                (t("settlement.roll"), None),
            ),
            h1[t("settlement.title", name=location.name)],
            p(class_="muted")[t("settlement.lede")],
            form(method="post", action=action, class_="stack-form")[
                section(class_="panel")[
                    label(class_="field settle-size")[
                        b[t("settlement.size")],
                        select(name="size")[
                            [
                                option(value=s.key, selected=s.key == size)[size_text(page, s)]
                                for s in sizes
                            ]
                        ],
                    ],
                    ul(class_="settle-list")[rows]
                    if rows
                    else p(class_="muted")[t("settlement.no_tables")],
                ],
                div(class_="form-actions")[
                    button(type="submit", name="action", value="apply", class_="btn btn-primary")[
                        icon("check", "sm"), span[t("settlement.keep")]
                    ],
                    button(type="submit", name="action", value="roll", class_="btn")[
                        icon("dices", "sm"), span[t("settlement.roll_all")]
                    ],
                ],
            ],
        ],
        world_id=world.id,
    )


__all__ = ["settlement_page", "size_text"]
