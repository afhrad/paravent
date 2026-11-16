"""A character's bastion (app/services/bastions.py): its special facilities, their orders
for the next turn, and the gamemaster's bastion turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
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
    summary,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Bastion
from app.services.bastion_orders import OrderOption
from app.services.bastions import FROM_LEVEL, SPACES, Facility, special_slots
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text
from app.ui.shop_pages import coin_text
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
    options: Sequence[Mapping[str, Sequence[OrderOption]]] = (),
    tool_items: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    equipment: Sequence[tuple[int, Sequence[tuple[str, str]]]] = (),
    costs: tuple[Mapping[str, tuple[int, int]], ...] = ({}, {}),
    day_of: Mapping[int, str] | None = None,
    discoveries: Sequence[tuple[str, str]] = (),
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The bastion: its name, each facility with its order for the next turn (or the
    Maintain order) and the option it carries out, a facility to add while there is
    room, what the last turn yielded, its basic facilities (with what building and
    enlarging cost) and defenders, the event the last turn rolled, and (for the
    gamemaster) the turn. ``options`` go with the facilities, ``tool_items`` are what
    each tool crafts (``(reference, name)``), ``equipment`` per facility how many tools
    it is equipped with and from which (``(reference, name)``), ``day_of`` names a
    clock minute."""
    t = page.t
    url = bastion_url(character_id)
    slots = special_slots(level)
    taken = {f.ref.lower() for f in bastion.facilities}
    rows = [
        _facility(
            page,
            url,
            n,
            held.ref,
            held.order,
            facility,
            world_id,
            held.shut,
            bastion.maintain,
            options=options[n] if n < len(options) else {},
            chosen=held.option,
            tool_items=tool_items or {},
            equipment=equipment[n] if n < len(equipment) else (0, ()),
            tools=held.tools,
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
            _yields(page, bastion, day_of or {}) if bastion.yields or bastion.pending else None,
            _event(page, bastion, event_text, character_id, discoveries) if bastion.event else None,
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
            _basic(page, url, bastion, basic_offered, world_id, costs, day_of or {})
            if level >= FROM_LEVEL
            else None,
            _defenders(page, url, bastion, gamemaster) if level >= FROM_LEVEL else None,
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
                    hidden("turns", str(bastion.turns)),
                    button(
                        type="submit", class_="btn btn-sm btn-primary", title=t("bastion.turn_hint")
                    )[icon("hourglass", "sm"), span[t("bastion.turn")]],
                ]
                if gamemaster and bastion.facilities
                else None,
            ],
        ],
    )


def _event(
    page: Page,
    bastion: Bastion,
    text: Node,
    character_id: str = "",
    discoveries: Sequence[tuple[str, str]] = (),
) -> Element:
    """The event the last Maintain turn rolled, with the books' text, what it brought
    and the decision it waits for."""
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
        ul(class_="bastion-event-yields")[[li[_event_line(page, y)] for y in bastion.event_yields]]
        if bastion.event_yields
        else None,
        p(class_="pill heal")[t("bastion.guardian")] if bastion.guardian else None,
        _offer(page, bastion, character_id, discoveries) if bastion.offer else None,
        div(class_="entries bastion-event-text")[text] if text is not None else None,
    ]


def _event_line(page: Page, line: str) -> str:
    """One thing an event brought, in words."""
    t = page.t
    kind, _, rest = line.partition("|")
    parts = rest.split("|")
    if kind in ("gold", "paid"):
        return t(f"bastion.event_{kind}", coins=coin_text(page, int(parts[0] or 0)))
    if kind == "refugees":
        return t("bastion.event_refugees", n=parts[0], coins=coin_text(page, int(parts[1] or 0)))
    if kind in ("shut", "item"):
        ref = parts[0]
        category = "facility" if kind == "shut" else "item"
        name = page.t.term(category, ref.partition("|")[0]) or ref.partition("|")[0]
        return t(f"bastion.event_{kind}", name=name)
    if kind == "aid":
        total, reward, lost = (parts + ["", "", ""])[:3]
        return t(
            "bastion.event_aid_lost" if lost == "1" else "bastion.event_aid",
            total=total,
            coins=coin_text(page, int(reward or 0)),
        )
    if kind in ("detail", "guest"):
        return rest
    return t(f"bastion.event_{kind}", name=rest)


def _offer(
    page: Page, bastion: Bastion, character_id: str, discoveries: Sequence[tuple[str, str]]
) -> Element:
    """The decision the last event waits for."""
    t = page.t
    kind, _, amount = bastion.offer.partition("|")
    url = f"/characters/{character_id}/bastion"
    decline = button(type="submit", name="accept", value="0", class_="btn btn-sm btn-ghost")[
        t("bastion.offer_decline")
    ]
    body: list[Node] = []
    if kind in ("bribe", "opportunity"):
        coins = coin_text(page, int(amount or 0))
        body = [
            p[t(f"bastion.offer_{kind}", coins=coins)],
            button(type="submit", name="accept", value="1", class_="btn btn-sm btn-primary")[
                t("bastion.offer_pay", coins=coins)
            ],
            decline,
        ]
    elif kind == "aid":
        body = [
            p[t("bastion.offer_aid")],
            input_el(
                type="number",
                name="value",
                min="1",
                max=str(bastion.defenders),
                value="1",
                inputmode="numeric",
                aria_label=t("bastion.offer_aid_count"),
            ),
            button(type="submit", name="accept", value="1", class_="btn btn-sm btn-primary")[
                t("bastion.offer_send")
            ],
            decline,
        ]
    elif kind == "discovery":
        body = [
            p[t("bastion.offer_discovery")],
            select(name="value", aria_label=t("bastion.offer_discovery"))[
                [option(value=ref)[page.t.term("item", name) or name] for ref, name in discoveries]
            ],
            button(type="submit", name="accept", value="1", class_="btn btn-sm btn-primary")[
                t("bastion.offer_take")
            ],
        ]
    return form(method="post", action=url, class_="bastion-offer notice")[
        input_el(type="hidden", name="op", value="offer"), body
    ]


def _yields(page: Page, bastion: Bastion, day_of: Mapping[int, str]) -> Element:
    """What the last turn's orders brought, and crafts still under way."""
    t = page.t
    lines = []
    for line in bastion.yields:
        parts = line.split("|")
        if parts and parts[0] == "":
            facility, kind, payload = "", parts[1], parts[2:]
        else:
            facility, kind, payload = parts[0], (parts[2:3] or [""])[0], parts[3:]
        where = (page.t.term("facility", facility) or facility) if facility else ""
        lines.append(li[b[where, ": "] if where else None, _yield_words(page, kind, payload)])
    for entry in bastion.pending:
        ready, _, item = entry.partition("|")
        lines.append(
            li(class_="muted")[
                t(
                    "bastion.yield.pending",
                    name=name_text(page, ("item", "baseitem"), item.partition("|")[0]),
                    day=day_of.get(int(ready), ready) if ready.isdigit() else ready,
                )
            ]
        )
    return section(class_="panel bastion-yields")[
        div(class_="panel-head")[h2[t("bastion.yields")]],
        ul(class_="bastion-yield-list")[lines],
    ]


def _yield_words(page: Page, kind: str, payload: Sequence[str]) -> Node:
    t = page.t
    first = payload[0] if payload else ""
    if kind == "defenders":
        return t.plural("bastion.yield.defenders", int(first or 0), n=first)
    if kind == "coins":
        won = int(first) if first.isdigit() else 0
        roll = payload[1] if len(payload) > 1 else ""
        return t("bastion.yield.coins", coins=coin_text(page, won), roll=roll)
    if kind == "item":
        count = payload[2] if len(payload) > 2 else "1"
        name = name_text(page, ("item", "baseitem"), first)
        return t("bastion.yield.item", name=name, n=count)
    if kind == "pending":
        return t("bastion.yield.started", name=name_text(page, ("item", "baseitem"), first))
    if kind == "unpaid":
        return span(class_="danger")[
            t("bastion.yield.unpaid", coins=coin_text(page, int(first or 0)))
        ]
    if kind == "paid":
        return span(class_="muted")[t("bastion.yield.paid", coins=coin_text(page, int(first or 0)))]
    return span(class_="muted")[t("bastion.yield.text", what=first)]


def _basic(
    page: Page,
    url: str,
    bastion: Bastion,
    offered: Sequence[Facility],
    world_id: str | None,
    costs: tuple[Mapping[str, tuple[int, int]], ...] = ({}, {}),
    day_of: Mapping[int, str] | None = None,
) -> Element:
    """The basic facilities with their space; the first two are free, any other is built
    (and any enlarged) at the guide's cost and time."""
    t = page.t
    adding, enlarging = (costs + ({}, {}))[:2]
    rows = []
    for n, held in enumerate(bastion.basic):
        name, _, source = held.ref.partition("|")
        grow = enlarging.get(held.space)
        rows.append(
            li(class_="bastion-row")[
                a(href=ref_url("facility", name, source, world_id), class_="ref")[
                    page.t.term("facility", name) or name
                ],
                span(class_="pill neutral")[t(f"bastion.space.{held.space}")],
                small(class_="muted")[
                    t(
                        "bastion.building",
                        day=(day_of or {}).get(held.ready, str(held.ready)),
                    )
                ]
                if held.ready is not None
                else None,
                form(method="post", action=url, class_="inline-form")[
                    hidden("op", "enlarge_basic"),
                    hidden("index", str(n)),
                    button(
                        type="submit",
                        class_="btn btn-sm",
                        title=t(
                            "bastion.enlarge_cost", coins=coin_text(page, grow[0]), days=grow[1]
                        ),
                    )[t("bastion.enlarge")],
                ]
                if grow is not None and held.ready is None
                else None,
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
                    option(value=sp, selected=sp == "roomy")[
                        t(f"bastion.space.{sp}")
                        + (
                            f" · {coin_text(page, adding[sp][0])}, "
                            + t.plural("bastion.days", adding[sp][1], n=adding[sp][1])
                            if sp in adding and len(bastion.basic) >= 2
                            else ""
                        )
                    ]
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


def _defenders(page: Page, url: str, bastion: Bastion, gamemaster: bool) -> Element:
    """The Bastion Defenders on the roster, one fewer at a press; one more only for the
    gamemaster (a player recruits through a Barrack)."""
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
            small(class_="muted")[
                t("bastion.defenders_gm" if gamemaster else "bastion.defenders_hint")
            ],
        ],
        div(class_="bastion-defenders-row")[
            step("-1", "−", t("bastion.defender_lose")),
            b(class_="mono bastion-defenders-count")[str(bastion.defenders)],
            step("1", "+", t("bastion.defender_add")) if gamemaster else None,
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
    *,
    options: Mapping[str, Sequence[OrderOption]] | None = None,
    chosen: str = "",
    tool_items: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    equipment: tuple[int, Sequence[tuple[str, str]]] = (0, ()),
    tools: Sequence[str] = (),
) -> Element:
    t = page.t
    name, _, source = ref.partition("|")
    shown = page.t.term("facility", name) or name
    orders = facility.orders if facility is not None else ()
    options = options or {}
    crafting = {o.tool for found in options.values() for o in found if o.tool}
    craftable = [item for tool in sorted(crafting) for item in (tool_items or {}).get(tool, ())]

    def choices(order: str) -> Node:
        found = options.get(order) or []
        if len(found) <= 1:
            return option(value=f"{order}|0", selected=order == current)[
                t(f"bastion.order.{order}")
                + (f": {found[0].title}" if found and found[0].title.lower() != order else "")
            ]
        return [
            option(value=f"{order}|{n}", selected=order == current and n == picked)[
                f"{t(f'bastion.order.{order}')}: {o.title}"
            ]
            for n, o in enumerate(found)
        ]

    current = order
    head = chosen.partition("|")[0]
    picked = int(head) if head.isdigit() else 0
    title = ""
    if order and options.get(order):
        found = options[order]
        title = found[picked].title if picked < len(found) else ""
    return li(class_="bastion-row" + (" is-shut" if shut else ""))[
        a(href=ref_url("facility", name, source, world_id), class_="ref")[shown],
        small(class_="muted")[t("bastion.level", level=facility.level)] if facility else None,
        span(class_="pill danger")[t("bastion.shut")] if shut else None,
        form(method="post", action=url, class_="bastion-order")[
            hidden("op", "order"),
            hidden("index", str(index)),
            select(name="order", aria_label=t("bastion.order"))[
                option(value="")[t("bastion.no_order")],
                [choices(o) for o in orders],
            ],
            select(name="item", aria_label=t("bastion.craft_item"))[
                option(value="")[t("bastion.craft_item")],
                [
                    option(value=ref, selected=chosen.partition("|")[2] == ref)[item_name]
                    for ref, item_name in craftable
                ],
            ]
            if craftable
            else None,
            button(type="submit", class_="btn btn-sm")[t("bastion.set")],
        ]
        if not shut and not maintain
        else None,
        b(class_="pill accent")[
            t(f"bastion.order.{order}") + (f": {title}" if title and title.lower() != order else "")
        ]
        if order and not maintain
        else None,
        _equipment(page, url, index, equipment, tools) if equipment[0] else None,
        form(method="post", action=url, class_="inline-form")[
            hidden("op", "remove"),
            hidden("index", str(index)),
            button(type="submit", class_="btn btn-sm btn-ghost", title=t("bastion.remove"))[
                icon("x", "sm")
            ],
        ],
    ]


def _equipment(
    page: Page,
    url: str,
    index: int,
    equipment: tuple[int, Sequence[tuple[str, str]]],
    tools: Sequence[str],
) -> Element:
    """The tools a facility was equipped with (a Workshop's six), chosen by ticking."""
    t = page.t
    most, offered = equipment
    held = {x.lower() for x in tools}
    return details(class_="bastion-tools", open=not tools)[
        summary[t("bastion.tools", count=len(tools), most=most)],
        form(method="post", action=url, class_="bastion-tools-form")[
            hidden("op", "tools"),
            hidden("index", str(index)),
            div(class_="bastion-tool-grid")[
                [
                    label(class_="check")[
                        input_el(
                            type="checkbox", name="tool", value=ref, checked=ref.lower() in held
                        ),
                        span[shown],
                    ]
                    for ref, shown in offered
                ]
            ],
            small(class_="muted")[t("bastion.tools_hint", most=most)],
            button(type="submit", class_="btn btn-sm")[t("bastion.save")],
        ],
    ]


__all__ = ["ORDERS", "bastion_page", "bastion_url"]
