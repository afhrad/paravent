"""Supernatural gifts at the table (app/services/gifts.py): **Grant** beside a blessing,
charm or boon of the world's books for the gamemaster, which opens a form of the world's
characters, its uses and how long it lasts filled in from its text; and the block on the
sheet that lists the gifts a character holds, a charm's uses spent there."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from htpy import Element, Node, a, b, button, div, form, h3, label, li, p, section, small, span, ul
from htpy import input as input_el

from app.models.pc import Gift, PlayerCharacter
from app.services.gifts import GiftReading
from app.ui.clock_pages import clock_label
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.tags import ref_url

GIFTS_URL = "/gm/gifts"
DAY = 1440


def gift_url(character_id: str) -> str:
    """Where a character's gifts are spent and taken back."""
    return f"/characters/{character_id}/gifts"


def grant_button(page: Page, world_id: str | None, category: str, name: str, source: str) -> Node:
    """For the gamemaster in a world, beside a gift of the books: "Grant", which loads
    the form in place."""
    if (
        world_id is None
        or category != "reward"
        or page.identity is None
        or not page.identity.is_gamemaster
    ):
        return None
    query = urlencode({"world": world_id, "name": name, "source": source})
    return div(class_="trap-spring gift-grant")[
        button(
            type="button",
            class_="btn btn-sm",
            hx_get=f"{GIFTS_URL}/form?{query}",
            hx_target="closest .gift-grant",
            hx_swap="innerHTML",
        )[icon("sparkles", "sm"), span[page.t("gift.grant")]]
    ]


def grant_form(
    page: Page,
    world_id: str,
    name: str,
    source: str,
    reading: GiftReading,
    members: Sequence[tuple[str, str]],
    *,
    result: Node = None,
) -> Element:
    """Whom it is given to, with its uses and days as its text says them, to change."""
    t = page.t
    url = f"{GIFTS_URL}/grant"
    days = reading.lasts // DAY if reading.lasts and reading.lasts % DAY == 0 else None
    return form(
        method="post",
        action=url,
        hx_post=url,
        hx_target="closest .gift-grant",
        hx_swap="innerHTML",
        class_="trap-form",
    )[
        hidden("world", world_id),
        hidden("name", name),
        hidden("source", source),
        hidden("hours", str(reading.lasts // 60) if reading.lasts and days is None else ""),
        result,
        div(class_="field-row")[
            label(class_="party-clock-label")[
                span[t("gift.uses")],
                input_el(
                    type="number",
                    name="uses",
                    min="1",
                    max="99",
                    value=str(reading.uses) if reading.uses else "",
                    placeholder="—",
                    inputmode="numeric",
                    class_="mono party-clock-amount",
                ),
            ],
            label(class_="party-clock-label")[
                span[t("gift.days")],
                input_el(
                    type="number",
                    name="days",
                    min="1",
                    max="3650",
                    value=str(days) if days else "",
                    placeholder="—",
                    inputmode="numeric",
                    class_="mono party-clock-amount",
                ),
            ],
        ],
        small(class_="muted")[t("gift.read_hint")],
        div(class_="trap-who")[
            [
                label(class_="party-clock-label")[
                    input_el(type="checkbox", name="character", value=cid), span[shown]
                ]
                for cid, shown in members
            ]
        ]
        if members
        else p(class_="muted")[t("trap.no_one")],
        div(class_="trap-buttons")[
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("sparkles", "sm"), span[t("gift.give")]
            ]
            if members
            else None,
        ],
    ]


def grant_result(page: Page, name: str, names: Sequence[str]) -> Element:
    """Who received it."""
    return div(class_="trap-result notice")[page.t("gift.given", name=name, names=", ".join(names))]


def sheet_gifts(page: Page, pc: PlayerCharacter) -> Node:
    """The gifts a character holds, for the Track view: a charm's uses spent with a tap,
    and the gamemaster takes any back."""
    if not pc.gifts:
        return None
    return section(class_="cs-block cs-gifts", data_m="track")[
        h3(class_="eyebrow")[page.t("gift.heading")],
        ul(class_="cs-aff-list")[[_row(page, pc, g) for g in pc.gifts]],
    ]


def gift_name(page: Page, gift: Gift) -> str:
    """A gift's name in the reader's language."""
    return page.t.term("reward", gift.gift.name) or gift.gift.name


def _row(page: Page, pc: PlayerCharacter, gift: Gift) -> Element:
    t = page.t
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    url = gift_url(pc.id)
    lines: list[str] = []
    if gift.uses is not None:
        lines.append(t("gift.left", left=gift.left or 0, uses=gift.uses))
    if gift.ends is not None:
        lines.append(t("gift.until", when=clock_label(page, gift.ends, pc.world_id)))

    def action(op: str, text: str, cls: str, hint: str = "") -> Element:
        return form(method="post", action=url, hx_post=url, hx_swap="none", class_="inline-form")[
            hidden("gift", gift.id),
            hidden("op", op),
            button(type="submit", class_=cls, title=hint or None)[text],
        ]

    return li(class_="cs-aff")[
        div(class_="cs-aff-head")[
            span(class_="pill neutral")[
                t(f"gift.kind.{gift.kind}") if gift.kind in KINDS else gift.kind.title() or "—"
            ],
            b[
                a(
                    href=ref_url("reward", gift.gift.name, gift.gift.source or "", pc.world_id),
                    class_="ref",
                    data_ref=f"reward:{gift.gift.source or ''}",
                )[gift_name(page, gift)]
            ],
            action("use", t("gift.use"), "btn btn-sm", t("gift.use_hint")) if gift.left else None,
            action("end", t("gift.end"), "btn btn-sm btn-ghost", t("gift.end_hint"))
            if gamemaster
            else None,
        ],
        [small(class_="muted cs-aff-line")[line] for line in lines],
    ]


def party_gifts(page: Page, pc: PlayerCharacter) -> list[Element]:
    """The party card's pills, a gift each with the uses left."""
    return [
        span(class_="pc-slot pc-magic-item pc-gift")[
            gift_name(page, g),
            span(class_="mono")[f" {g.left}/{g.uses}"] if g.uses is not None else None,
        ]
        for g in pc.gifts
    ]


# The kinds named in the reader's language; any other the books' word.
KINDS = ("blessing", "charm", "boon")

__all__ = [
    "GIFTS_URL",
    "KINDS",
    "gift_name",
    "gift_url",
    "grant_button",
    "grant_form",
    "grant_result",
    "party_gifts",
    "sheet_gifts",
]
