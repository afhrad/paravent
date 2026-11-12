"""A book's maps at a glance (``generated/gendata-maps.json``, :mod:`app.store.maps`):
on an adventure started from a book and on a rulebook's contents, every map by the
chapter it is printed in, each leading to where it sits in the text, with its player
version to show the table. For reading: no grid, no tokens.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import groupby

from htpy import Element, Node, a, details, div, figcaption, figure, h3, img, span, summary

from app.store.maps import BookMap
from app.ui.context import Page
from app.ui.icons import icon


def maps_panel(
    page: Page,
    maps: Sequence[BookMap],
    where: Callable[[BookMap], str | None],
    picture: Callable[[str], str | None],
    share: Callable[[BookMap, str], str | None] | None = None,
) -> Node:
    """The maps of a book, folded, by chapter. ``where(map)`` is the address of the map
    in the text (None where the table's text does not show it), ``picture(path)`` the
    address of a file the image folder holds, or None; ``share(map, path)`` the share
    form for a picture of it (JOURNAL.md), or None. Nothing when the book has none."""
    if not maps:
        return None
    t = page.t
    groups = [
        div(class_="maps-chapter")[
            h3(class_="maps-chapter-title")[name or t("maps.untitled_chapter")],
            div(class_="maps-grid")[[_card(page, m, where, picture, share) for m in chapter]],
        ]
        for name, chapter in ((n, list(g)) for n, g in groupby(maps, key=lambda m: m.chapter_name))
    ]
    return details(class_="maps-panel prose-section")[
        summary(class_="section-title")[
            icon("map", "sm"), span[t("maps.heading", count=len(maps))]
        ],
        groups,
    ]


def _card(
    page: Page,
    found: BookMap,
    where: Callable[[BookMap], str | None],
    picture: Callable[[str], str | None],
    share: Callable[[BookMap, str], str | None] | None,
) -> Element:
    t = page.t
    title = found.title or found.chapter_name or t("maps.untitled")
    full = picture(found.path)
    thumb = (picture(found.thumbnail) if found.thumbnail else None) or full
    player = picture(found.player.path) if found.player is not None else None
    here = where(found)
    # What players are handed: the map without its secrets where the book prints one.
    handed = found.player.path if player and found.player is not None else found.path
    shared = share(found, handed) if share is not None and (player or full) else None
    shown: Node = (
        img(src=thumb, alt="", loading="lazy")
        if thumb
        else span(class_="maps-missing")[icon("map", "lg", muted=True)]
    )
    return figure(class_="maps-card")[
        a(href=here, class_="maps-thumb")[shown] if here else span(class_="maps-thumb")[shown],
        figcaption[
            a(href=here, class_="maps-title")[title] if here else span(class_="maps-title")[title],
            span(class_="maps-links")[
                a(href=full, target="_blank", rel="noopener")[t("maps.full")] if full else None,
                a(href=player, target="_blank", rel="noopener")[t("maps.player")]
                if player
                else None,
                a(href=shared, class_="maps-share")[
                    icon("send", "sm"),
                    span[t("maps.share_player") if player else t("journal.share")],
                ]
                if shared
                else None,
                span(class_="muted")[t("maps.areas", count=found.regions)]
                if found.regions and here
                else None,
                span(class_="muted")[t("book.image_missing")] if not full else None,
            ],
        ],
    ]


__all__ = ["maps_panel"]
