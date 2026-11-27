"""Where something lies on the maps (documentation/MAPS.md §6): the pins pointing at it,
and the maps it may be pinned on, for the page of anything a pin can point at."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from htpy import Element, a, div, h2, li, p, section, span, ul

from app.ui.context import Page
from app.ui.icons import icon


@dataclass(frozen=True)
class OnMaps:
    """The pins to one thing, and the maps that may take one more: ``(map name, url)``
    each, the first opening the map at its pin, the second ready to pin it."""

    pinned: Sequence[tuple[str, str]] = field(default_factory=list)
    pinnable: Sequence[tuple[str, str]] = field(default_factory=list)


def on_maps_section(page: Page, on: OnMaps | None, *, panel: bool = False) -> Element | None:
    """*On the map*: where it is pinned, and *Pin on a map* with each map that may take
    it; nothing when the world has no map for it. As a ``panel`` for a page of panels."""
    if on is None or not (on.pinned or on.pinnable):
        return None
    t = page.t
    head = (
        div(class_="panel-head")[h2[icon("map", "sm"), " ", t("atlas.on_the_map")]]
        if panel
        else h2(class_="section-title")[t("atlas.on_the_map")]
    )
    return section(class_="panel on-maps" if panel else "prose-section on-maps")[
        head,
        ul(class_="place-here")[
            [
                li[a(href=url, class_="ref")[icon("map-pinned", "xs"), name]]
                for name, url in on.pinned
            ]
        ]
        if on.pinned
        else None,
        p(class_="on-maps-pin")[
            span(class_="muted")[t("atlas.pin.on_a_map")],
            [
                a(href=url, class_="btn btn-sm btn-ghost")[icon("map-pin", "sm"), span[name]]
                for name, url in on.pinnable
            ],
        ]
        if on.pinnable
        else None,
    ]


def on_maps_line(page: Page, on: OnMaps | None) -> Element | None:
    """The same in one line, for a panel among many (a quest, a faction): the maps it
    is pinned on, then the maps that may take it."""
    if on is None or not (on.pinned or on.pinnable):
        return None
    t = page.t
    return p(class_="on-maps-line")[
        [
            a(href=url, class_="ref", title=t("atlas.on_the_map"))[icon("map-pinned", "xs"), name]
            for name, url in on.pinned
        ],
        span(class_="muted")[t("atlas.pin.on_a_map")] if on.pinnable else None,
        [
            a(href=url, class_="btn btn-sm btn-ghost")[icon("map-pin", "sm"), span[name]]
            for name, url in on.pinnable
        ],
    ]
