"""A character's bastion (app/services/bastions.py): its special facilities, their orders
for the next turn, and the gamemaster's bastion turn."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import (
    Element,
    a,
    b,
    button,
    div,
    form,
    h2,
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

from app.models.gamestate import Bastion
from app.services.bastions import FROM_LEVEL, Facility, special_slots
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.tags import ref_url

ORDERS = ("craft", "empower", "harvest", "recruit", "research", "trade")


def bastion_url(character_id: str) -> str:
    return f"/characters/{character_id}/bastion"


def bastion_page(
    page: Page,
    *,
    character_id: str,
    character_name: str,
    level: int,
    world_id: str | None,
    bastion: Bastion,
    built: Sequence[Facility | None],
    offered: Sequence[Facility],
    gamemaster: bool,
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The bastion: its name, each facility with its order for the next turn, a facility
    to add while there is room, and (for the gamemaster) the bastion turn."""
    t = page.t
    url = bastion_url(character_id)
    slots = special_slots(level)
    taken = {f.ref.lower() for f in bastion.facilities}
    rows = [
        _facility(page, url, n, held.ref, held.order, facility, world_id)
        for n, (held, facility) in enumerate(zip(bastion.facilities, built, strict=True))
    ]
    room = [f for f in offered if f.reference.lower() not in taken]
    return app_shell(
        page,
        t("bastion.title", name=character_name),
        div(class_="tr-page bastion-page")[
            a(href=f"/characters/{character_id}", class_="backlink")[
                icon("arrow-left"), span[character_name]
            ],
            page_head(
                t("bastion.eyebrow"),
                bastion.name or t("bastion.title", name=character_name),
                t("bastion.lede", turns=bastion.turns),
            ),
            alert(error),
            notice(message),
            p(class_="notice")[t("bastion.too_low", level=FROM_LEVEL)]
            if level < FROM_LEVEL
            else None,
            section(class_="panel bastion-facilities")[
                div(class_="panel-head")[
                    h2[t("bastion.facilities")],
                    small(class_="muted")[
                        t("bastion.slots", used=len(bastion.facilities), total=slots)
                    ],
                ],
                ul(class_="bastion-list")[rows] if rows else p(class_="muted")[t("bastion.none")],
                form(method="post", action=url, class_="bastion-add")[
                    hidden("op", "add"),
                    select(name="facility", aria_label=t("bastion.add"))[
                        [
                            option(value=f.reference)[
                                f"{page.t.term('facility', f.name) or f.name} · "
                                f"{t('bastion.level', level=f.level)}"
                            ]
                            for f in room
                        ]
                    ],
                    button(type="submit", class_="btn btn-sm")[
                        icon("plus", "sm"), span[t("bastion.add")]
                    ],
                ]
                if room and len(bastion.facilities) < slots
                else None,
            ],
            section(class_="panel bastion-meta")[
                form(method="post", action=url, class_="bastion-name")[
                    hidden("op", "rename"),
                    label(class_="field")[
                        span[t("bastion.name")],
                        input_el(type="text", name="name", value=bastion.name, maxlength="80"),
                    ],
                    button(type="submit", class_="btn btn-sm")[t("bastion.save")],
                ],
                form(method="post", action=url)[
                    hidden("op", "turn"),
                    button(
                        type="submit", class_="btn btn-sm btn-primary", title=t("bastion.turn_hint")
                    )[icon("hourglass", "sm"), span[t("bastion.turn")]],
                ]
                if gamemaster and bastion.facilities
                else None,
            ],
        ],
    )


def _facility(
    page: Page,
    url: str,
    index: int,
    ref: str,
    order: str,
    facility: Facility | None,
    world_id: str | None,
) -> Element:
    t = page.t
    name, _, source = ref.partition("|")
    shown = page.t.term("facility", name) or name
    orders = facility.orders if facility is not None else ()
    return li(class_="bastion-row")[
        a(href=ref_url("facility", name, source, world_id), class_="ref")[shown],
        small(class_="muted")[t("bastion.level", level=facility.level)] if facility else None,
        form(method="post", action=url, class_="bastion-order")[
            hidden("op", "order"),
            hidden("index", str(index)),
            select(name="order", aria_label=t("bastion.order"))[
                option(value="")[t("bastion.no_order")],
                [option(value=o, selected=o == order)[t(f"bastion.order.{o}")] for o in orders],
            ],
            button(type="submit", class_="btn btn-sm")[t("bastion.set")],
        ],
        b(class_="pill accent")[t(f"bastion.order.{order}")] if order else None,
        form(method="post", action=url, class_="inline-form")[
            hidden("op", "remove"),
            hidden("index", str(index)),
            button(type="submit", class_="btn btn-sm btn-ghost", title=t("bastion.remove"))[
                icon("x", "sm")
            ],
        ],
    ]


__all__ = ["ORDERS", "bastion_page", "bastion_url"]
