"""A character's bastion (app/services/bastions.py): its special facilities, their orders
for the next turn, and the gamemaster's bastion turn."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import (
    Element,
    Node,
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
from app.services.bastions import FROM_LEVEL, SPACES, Facility, special_slots
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
    basic_offered: Sequence[Facility] = (),
    event_text: Node = None,
    next_turn: int | None = None,
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The bastion: its name, each facility with its order for the next turn (or the
    Maintain order), a facility to add while there is room, its basic facilities and
    defenders, the event the last turn rolled, and (for the gamemaster) the turn."""
    t = page.t
    url = bastion_url(character_id)
    slots = special_slots(level)
    taken = {f.ref.lower() for f in bastion.facilities}
    rows = [
        _facility(
            page, url, n, held.ref, held.order, facility, world_id, held.shut, bastion.maintain
        )
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
                t.plural("bastion.lede", bastion.turns, turns=bastion.turns),
            ),
            p(class_="muted bastion-next")[t("bastion.next_turn", day=next_turn)]
            if next_turn is not None
            else None,
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
            _event(page, bastion, event_text) if bastion.event else None,
            section(class_="panel bastion-maintain")[
                form(method="post", action=url, class_="bastion-maintain-form")[
                    hidden("op", "maintain"),
                    hidden("on", "" if bastion.maintain else "1"),
                    button(
                        type="submit",
                        class_="btn btn-sm" + (" is-on" if bastion.maintain else ""),
                        aria_pressed="true" if bastion.maintain else "false",
                    )[icon("shield-check", "sm"), span[t("bastion.maintain")]],
                    small(class_="muted")[t("bastion.maintain_hint")],
                ],
            ]
            if bastion.facilities and level >= FROM_LEVEL
            else None,
            _basic(page, url, bastion, basic_offered, world_id) if level >= FROM_LEVEL else None,
            _defenders(page, url, bastion) if level >= FROM_LEVEL else None,
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


def _event(page: Page, bastion: Bastion, text: Node) -> Element:
    """The event the last Maintain turn rolled, with the books' text."""
    t = page.t
    parts = bastion.event.split("|")
    roll, name = parts[0], parts[2] if len(parts) > 2 else ""
    lost = parts[3] if len(parts) > 3 else None
    shut = parts[4].partition("|")[0] if len(parts) > 4 else ""
    return section(class_="panel bastion-event")[
        div(class_="panel-head")[
            h2[t("bastion.event", name=name)], small(class_="muted mono")[f"d100 {roll}"]
        ],
        p[t.plural("bastion.attack_lost", int(lost), n=lost, left=bastion.defenders)]
        if lost is not None
        else None,
        p(class_="notice")[t("bastion.attack_shut", name=page.t.term("facility", shut) or shut)]
        if shut
        else None,
        div(class_="entries bastion-event-text")[text] if text is not None else None,
    ]


def _basic(
    page: Page,
    url: str,
    bastion: Bastion,
    offered: Sequence[Facility],
    world_id: str | None,
) -> Element:
    """The basic facilities with their space; the first two are free."""
    t = page.t
    rows = []
    for n, held in enumerate(bastion.basic):
        name, _, source = held.ref.partition("|")
        rows.append(
            li(class_="bastion-row")[
                a(href=ref_url("facility", name, source, world_id), class_="ref")[
                    page.t.term("facility", name) or name
                ],
                span(class_="pill neutral")[t(f"bastion.space.{held.space}")],
                form(method="post", action=url, class_="inline-form")[
                    hidden("op", "remove_basic"),
                    hidden("index", str(n)),
                    button(type="submit", class_="btn btn-sm btn-ghost", title=t("bastion.remove"))[
                        icon("x", "sm")
                    ],
                ],
            ]
        )
    return section(class_="panel bastion-basic")[
        div(class_="panel-head")[
            h2[t("bastion.basic")], small(class_="muted")[t("bastion.basic_hint")]
        ],
        ul(class_="bastion-list")[rows] if rows else p(class_="muted")[t("bastion.basic_none")],
        form(method="post", action=url, class_="bastion-add")[
            hidden("op", "add_basic"),
            select(name="facility", aria_label=t("bastion.basic_add"))[
                [
                    option(value=f.reference)[page.t.term("facility", f.name) or f.name]
                    for f in offered
                ]
            ],
            select(name="space", aria_label=t("bastion.space"))[
                [
                    option(value=sp, selected=sp == "roomy")[t(f"bastion.space.{sp}")]
                    for sp in SPACES
                ]
            ],
            button(type="submit", class_="btn btn-sm")[
                icon("plus", "sm"), span[t("bastion.basic_add")]
            ],
        ]
        if offered
        else None,
    ]


def _defenders(page: Page, url: str, bastion: Bastion) -> Element:
    """The Bastion Defenders on the roster, one more or one fewer at a press."""
    t = page.t

    def step(change: str, glyph: str, title: str) -> Element:
        return form(method="post", action=url, class_="inline-form")[
            hidden("op", "defenders"),
            hidden("change", change),
            button(type="submit", class_="btn btn-sm", title=title, aria_label=title)[glyph],
        ]

    return section(class_="panel bastion-defenders")[
        div(class_="panel-head")[
            h2[t("bastion.defenders")],
            small(class_="muted")[t("bastion.defenders_hint")],
        ],
        div(class_="bastion-defenders-row")[
            step("-1", "−", t("bastion.defender_lose")),
            b(class_="mono bastion-defenders-count")[str(bastion.defenders)],
            step("1", "+", t("bastion.defender_add")),
        ],
    ]


def _facility(
    page: Page,
    url: str,
    index: int,
    ref: str,
    order: str,
    facility: Facility | None,
    world_id: str | None,
    shut: bool = False,
    maintain: bool = False,
) -> Element:
    t = page.t
    name, _, source = ref.partition("|")
    shown = page.t.term("facility", name) or name
    orders = facility.orders if facility is not None else ()
    return li(class_="bastion-row" + (" is-shut" if shut else ""))[
        a(href=ref_url("facility", name, source, world_id), class_="ref")[shown],
        small(class_="muted")[t("bastion.level", level=facility.level)] if facility else None,
        span(class_="pill danger")[t("bastion.shut")] if shut else None,
        form(method="post", action=url, class_="bastion-order")[
            hidden("op", "order"),
            hidden("index", str(index)),
            select(name="order", aria_label=t("bastion.order"))[
                option(value="")[t("bastion.no_order")],
                [option(value=o, selected=o == order)[t(f"bastion.order.{o}")] for o in orders],
            ],
            button(type="submit", class_="btn btn-sm")[t("bastion.set")],
        ]
        if not shut and not maintain
        else None,
        b(class_="pill accent")[t(f"bastion.order.{order}")] if order and not maintain else None,
        form(method="post", action=url, class_="inline-form")[
            hidden("op", "remove"),
            hidden("index", str(index)),
            button(type="submit", class_="btn btn-sm btn-ghost", title=t("bastion.remove"))[
                icon("x", "sm")
            ],
        ],
    ]


__all__ = ["ORDERS", "bastion_page", "bastion_url"]
