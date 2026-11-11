"""Which naming styles a world rolls its names from (documentation/NAMES.md §5b): a
checklist like the world's books, people by race and places by kind; nothing ticked
means every style."""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence

from htpy import Element, a, b, button, details, div, form, label, p, small, span, summary
from htpy import input as input_el

from app.models.gamestate import World
from app.services.naming import PERSON, StyleEntry
from app.ui.context import Page
from app.ui.forms import notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import WORLDS_URL, crumbs, world_url
from app.ui.naming_pages import NAMING_URL, entry_label


def world_naming_url(world_id: str) -> str:
    """A world's naming styles."""
    return f"{world_url(world_id)}/names"


def _choice(page: Page, entry: StyleEntry, checked: bool) -> Element:
    field_id = f"style-{hashlib.sha1(entry.key.encode()).hexdigest()[:10]}"
    origin = "own" if entry.kind == "own" else "starter" if entry.kind == "starter" else "books"
    return label(class_="book", for_=field_id)[
        input_el(type="checkbox", id=field_id, name="style", value=entry.key, checked=checked),
        span(class_="book-name")[
            entry_label(page, entry),
            " ",
            small(class_="muted")[entry.culture] if entry.culture else None,
        ],
        span(class_="pill naming-lang", title=page.t("naming.language"))[entry.language.upper()]
        if entry.language
        else None,
        span(class_="book-code")[page.t(f"naming.origin.{origin}")],
    ]


def _groups(page: Page, entries: Sequence[StyleEntry]) -> list[tuple[str, list[StyleEntry]]]:
    """People by race (those of no race last), then each kind of place."""
    t = page.t
    styles = [e for e in entries if e.style is not None]
    people: dict[str, list[StyleEntry]] = {}
    for entry in styles:
        if entry.subject == PERSON:
            people.setdefault(entry.race.casefold(), []).append(entry)
    out = [
        (people[key][0].race or t("naming.no_race"), people[key])
        for key in sorted(people, key=lambda k: (not k, k))
    ]
    places: dict[str, list[StyleEntry]] = {}
    for entry in styles:
        if entry.subject != PERSON:
            places.setdefault(entry.subject, []).append(entry)
    out.extend((t(f"naming.subject.{subject}"), rows) for subject, rows in places.items())
    return [
        (caption, sorted(rows, key=lambda e: entry_label(page, e).casefold()))
        for caption, rows in out
    ]


def world_naming_page(
    page: Page,
    world: World,
    entries: Sequence[StyleEntry],
    chosen: Collection[str],
    *,
    notice_text: str | None = None,
) -> Element:
    """Every style, ticked when the world uses it; a group opens when one of it is."""
    t = page.t
    picked = set(chosen)
    groups = [
        details(class_="book-group", open=any(e.key in picked for e in rows))[
            summary[
                span[caption],
                span(class_="book-count mono")[
                    f"{sum(1 for e in rows if e.key in picked)}/{len(rows)}"
                ],
            ],
            div(class_="book-list")[[_choice(page, e, e.key in picked) for e in rows]],
        ]
        for caption, rows in _groups(page, entries)
    ]
    state = t.plural("naming.world.chosen", len(picked)) if picked else t("naming.world.every")
    url = world_naming_url(world.id)
    return app_shell(
        page,
        t("naming.world.title"),
        div(class_="formpage formpage-wide")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("naming.world.title"), None),
            ),
            page_head(
                t("adventures.world_eyebrow"), t("naming.world.title"), t("naming.world.lede")
            ),
            notice(notice_text),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[icon("dices", "sm"), world.name],
                    b[state],
                ],
                form(method="post", action=url, class_="stack-form books")[
                    groups,
                    p(class_="field-hint")[
                        t("naming.world.workshop"),
                        " ",
                        a(href=NAMING_URL)[t("naming.title")],
                    ],
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"), span[t("worlds.save")]
                        ],
                        button(type="submit", name="every", value="1", class_="btn")[
                            t("naming.world.use_every")
                        ]
                        if picked
                        else None,
                        a(href=world_url(world.id), class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
        ],
        world_id=world.id,
    )


__all__ = ["world_naming_page", "world_naming_url"]
