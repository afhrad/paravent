"""A character's deity and a cleric's Divine Intervention (app/services/intervention.py):
the deity on the sheet's features, the call in its Track view, and the 2024 cleric's
choice of spell on a page of its own."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby

from htpy import (
    Element,
    Node,
    a,
    button,
    div,
    form,
    h3,
    li,
    optgroup,
    option,
    p,
    select,
    span,
    ul,
)

from app.models.pc import PlayerCharacter
from app.services.intervention import Castable, Intervention
from app.services.sheet import Sheet
from app.ui.clock_pages import clock_label
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text


def intervention_url(character_id: str) -> str:
    return f"/characters/{character_id}/intervention"


def deity_field(page: Page, sheet: Sheet) -> Node:
    """The deity the character worships, chosen from those its world allows (grouped by
    pantheon); nothing where the world's books print none and none is chosen."""
    pc = sheet.character
    if not sheet.deities and pc.deity is None:
        return None
    t = page.t
    held = pc.deity.reference.lower() if pc.deity is not None else ""
    return div(class_="cs-prof cs-deity")[
        h3(class_="eyebrow")[t("deity.title")],
        form(method="post", action=f"/characters/{pc.id}/deity", class_="inline-form")[
            select(name="deity", aria_label=t("deity.title"))[
                option(value="", selected=not held)[t("deity.none")],
                [
                    optgroup(label=pantheon or t("deity.other"))[
                        [
                            option(value=ref, selected=ref.lower() == held)[
                                name_text(page, "deity", ref.partition("|")[0])
                            ]
                            for ref, _ in group
                        ]
                    ]
                    for pantheon, group in groupby(sheet.deities, key=lambda d: d[1])
                ],
            ],
            button(type="submit", class_="btn btn-sm")[t("deity.save")],
        ],
    ]


def _waits(page: Page, held: Intervention, world_id: str | None) -> str:
    t = page.t
    if held.rests:
        return t.plural("intervention.rests", held.rests, n=held.rests)
    if held.until is not None:
        return t("intervention.until", when=clock_label(page, held.until, world_id))
    return ""


def intervention_block(page: Page, sheet: Sheet) -> Node:
    """A cleric's Divine Intervention in the Track view: whom it calls on, and the call,
    or how long it still waits."""
    held: Intervention | None = sheet.intervention
    if held is None:
        return None
    t = page.t
    pc = sheet.character
    deity = (
        name_text(page, "deity", sheet.deity_name) if sheet.deity_name else t("intervention.god")
    )
    ready = held.ready(sheet.clock)
    if not ready:
        action: Node = p(class_="muted")[_waits(page, held, pc.world_id)]
    elif held.rules == "classic":
        action = form(method="post", action=intervention_url(pc.id), class_="inline-form")[
            button(type="submit", class_="btn btn-sm")[
                icon("sparkles", "sm"), span[t("intervention.call", deity=deity)]
            ],
            span(class_="muted")[
                t("intervention.always")
                if held.greater
                else t("intervention.chance", level=held.level)
            ],
        ]
    else:
        action = a(href=intervention_url(pc.id), class_="btn btn-sm")[
            icon("sparkles", "sm"), span[t("intervention.choose")]
        ]
    return section_block(t("intervention.title"), action)


def section_block(title: str, body: Node) -> Element:
    return div(class_="cs-block cs-intervention", data_m="track")[h3(class_="eyebrow")[title], body]


def intervention_page(
    page: Page,
    pc: PlayerCharacter,
    held: Intervention,
    spells: Sequence[Castable],
    *,
    ready: bool,
    error: str | None = None,
) -> Element:
    """A 2024 cleric's choice: every spell it may cast without a slot, by level, Wish at
    the end from level 20; or how long it still waits."""
    t = page.t
    name = pc.name or t("play.unnamed")
    deity = name_text(page, "deity", pc.deity.name) if pc.deity else t("intervention.god")
    url = intervention_url(pc.id)
    body: Node
    if not ready:
        body = p(class_="notice")[_waits(page, held, pc.world_id)]
    elif not spells:
        body = p(class_="muted")[t("intervention.no_spells")]
    else:
        body = [
            div(class_="intervention-level")[
                h3(class_="eyebrow")[
                    t("intervention.wish") if level == 9 else t("intervention.level", level=level)
                ],
                ul(class_="intervention-spells")[
                    [
                        li[
                            form(method="post", action=url, class_="inline-form")[
                                hidden("spell", c.reference),
                                button(type="submit", class_="btn btn-sm")[
                                    name_text(page, "spell", c.name)
                                ],
                                span(class_="muted")[t("intervention.concentration")]
                                if c.concentration
                                else None,
                            ]
                        ]
                        for c in group
                    ]
                ],
            ]
            for level, group in groupby(spells, key=lambda c: c.level)
        ]
    return app_shell(
        page,
        t("intervention.title"),
        div(class_="tr-page intervention-page")[
            a(href=f"/characters/{pc.id}?view=track", class_="backlink")[
                icon("arrow-left"), span[name]
            ],
            page_head(t("intervention.eyebrow", deity=deity), t("intervention.title"), ""),
            alert(error),
            p(class_="field-hint")[
                t("intervention.greater_hint" if held.greater else "intervention.hint")
            ],
            body,
        ],
        world_id=pc.world_id,
        player_bar=False,
    )


__all__ = ["deity_field", "intervention_block", "intervention_page", "intervention_url"]
