"""The gamemaster's factions page (app/services/factions.py): a world's factions, each
character's renown with them, raised or lowered, and new factions by hand or from the
books."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    b,
    button,
    details,
    div,
    form,
    h2,
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Faction, World
from app.services.factions import title_of
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.title_images import title_image, title_image_panel

FACTIONS_URL = "/gm/factions"


def factions_url(world_id: str) -> str:
    """A world's factions page."""
    return f"{FACTIONS_URL}?{urlencode({'world': world_id})}"


def factions_page(
    page: Page,
    world: World,
    factions: Sequence[Faction],
    members: Sequence[tuple[str, str]],
    tables: Sequence[tuple[str, str]],
    examples: Sequence[str],
    *,
    error: str | None = None,
) -> Element:
    """Every faction of the world with the characters' renown (``members`` are the
    world's characters, ``(id, name)``); the forms that make a new one, by hand, from a
    book's renown table (``tables``, ``(name, source)``) or the guide's examples."""
    t = page.t
    base = f"{FACTIONS_URL}/{quote(world.id)}"
    return app_shell(
        page,
        t("factions.page_title", world=world.name),
        [
            page_head(t("factions.eyebrow"), world.name, t("factions.lede")),
            alert(error),
            div(class_="faction-grid")[
                [_faction(page, base, f, members) for f in factions]
                if factions
                else p(class_="muted")[t("factions.empty")],
            ],
            _new(page, base, tables, examples),
        ],
        main_class="shell-main shell-wide",
        world_id=world.id,
    )


def _post(base: str, op: str, *content: Node, cls: str = "inline-form") -> Element:
    return form(method="post", action=f"{base}/{op}", class_=cls)[content]


def _faction(
    page: Page, base: str, faction: Faction, members: Sequence[tuple[str, str]]
) -> Element:
    t = page.t
    source = faction.table.partition("|")
    found = (faction.world_id, faction.cover) if faction.cover else None
    return section(class_="panel faction-panel")[
        title_image(found, faction.name),
        div(class_="faction-head")[
            h2[faction.name],
            small(class_="muted")[t("factions.from_table", table=source[0], source=source[2])]
            if faction.table
            else None,
        ],
        p(class_="muted")[faction.note] if faction.note else None,
        ul(class_="faction-renown")[
            [
                li(class_="faction-member")[
                    span[name],
                    b(class_="mono")[str(faction.renown.get(cid, 0))],
                    small(class_="muted")[title_of(faction, faction.renown.get(cid, 0))],
                    div(class_="faction-steps")[
                        [
                            _post(
                                base,
                                "renown",
                                hidden("faction", faction.id),
                                hidden("character", cid),
                                hidden("delta", str(delta)),
                                button(
                                    type="submit",
                                    class_="btn btn-sm",
                                    aria_label=t("factions.change", name=name, delta=f"{delta:+d}"),
                                )[f"{delta:+d}"],
                            )
                            for delta in (-1, 1)
                        ]
                    ],
                ]
                for cid, name in members
            ]
        ]
        if members
        else p(class_="muted")[t("factions.no_characters")],
        details(class_="faction-edit")[
            summary[t("factions.edit")],
            _post(
                base,
                "edit",
                hidden("faction", faction.id),
                input_el(
                    type="text",
                    name="name",
                    value=faction.name,
                    maxlength="120",
                    required=True,
                    aria_label=t("factions.name"),
                ),
                textarea(name="note", rows="3", maxlength="4000", aria_label=t("factions.note"))[
                    faction.note
                ],
                button(type="submit", class_="btn btn-sm")[t("factions.save")],
                cls="faction-form",
            ),
            title_image_panel(
                page, "faction", faction.id, found, owner=faction.world_id, heading=False
            ),
            _post(
                base,
                "delete",
                hidden("faction", faction.id),
                button(type="submit", class_="btn btn-sm btn-ghost")[
                    icon("trash-2", "sm"), span[t("factions.delete")]
                ],
            ),
        ],
    ]


def _new(
    page: Page, base: str, tables: Sequence[tuple[str, str]], examples: Sequence[str]
) -> Element:
    t = page.t
    return section(class_="panel faction-new")[
        div(class_="panel-head")[h2[t("factions.new")]],
        _post(
            base,
            "create",
            input_el(
                type="text",
                name="name",
                maxlength="120",
                required=True,
                placeholder=t("factions.name"),
                aria_label=t("factions.name"),
            ),
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("plus", "sm"), span[t("factions.create")]
            ],
            cls="faction-form",
        ),
        _post(
            base,
            "from_table",
            select(name="table", aria_label=t("factions.table"))[
                [
                    option(value=f"{name}|{source}")[f"{name.removesuffix(' Renown')} ({source})"]
                    for name, source in tables
                ]
            ],
            button(type="submit", class_="btn btn-sm")[t("factions.from_book")],
            cls="faction-form",
        )
        if tables
        else None,
        _post(
            base,
            "from_example",
            select(name="example", aria_label=t("factions.example"))[
                [option(value=name)[name] for name in examples]
            ],
            button(type="submit", class_="btn btn-sm")[t("factions.from_example")],
            cls="faction-form",
        )
        if examples
        else None,
        p(class_="muted")[t("factions.new_hint")],
    ]


__all__ = ["FACTIONS_URL", "factions_page", "factions_url"]
