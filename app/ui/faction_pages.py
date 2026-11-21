"""The gamemaster's factions page (app/services/factions.py): a world's factions, each
character's renown with them, raised or lowered, and new factions by hand or from the
books."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    label,
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
from app.ui.tool_dialog import new_button, tool_dialog

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
    render: Callable[[str], Node] = str,
) -> Element:
    """Every faction of the world with the characters' renown (``members`` are the
    world's characters, ``(id, name)``); the forms that make a new one, by hand, from a
    book's renown table (``tables``, ``(name, source)``) or the guide's examples.
    ``render`` draws a faction's note (Paravent Markdown)."""
    t = page.t
    base = f"{FACTIONS_URL}/{quote(world.id)}"
    return app_shell(
        page,
        t("factions.page_title", world=world.name),
        [
            page_head(
                t("factions.eyebrow"),
                world.name,
                t("factions.lede"),
                new_button(page, "faction", t("factions.new")),
            ),
            alert(error),
            div(class_="faction-grid")[
                [_faction(page, base, f, members, render) for f in factions]
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
    page: Page,
    base: str,
    faction: Faction,
    members: Sequence[tuple[str, str]],
    render: Callable[[str], Node] = str,
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
        div(class_="faction-note md-body")[render(faction.note)] if faction.note else None,
        ul(class_="faction-renown")[
            [
                li(class_="faction-member")[
                    span(class_="faction-name")[name],
                    div(class_="faction-steps")[
                        _step(page, base, faction, cid, name, -1),
                        b(class_="mono faction-score")[str(faction.renown.get(cid, 0))],
                        _step(page, base, faction, cid, name, 1),
                    ],
                    small(class_="muted")[title_of(faction, faction.renown.get(cid, 0))],
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
                div(class_="field")[
                    label(for_=f"fe-name-{faction.id}")[t("factions.name")],
                    input_el(
                        id=f"fe-name-{faction.id}",
                        type="text",
                        name="name",
                        value=faction.name,
                        maxlength="120",
                        required=True,
                    ),
                ],
                div(class_="field")[
                    label(for_=f"fe-note-{faction.id}")[t("factions.note")],
                    textarea(
                        id=f"fe-note-{faction.id}",
                        name="note",
                        rows="6",
                        maxlength="4000",
                        class_="prose-editor",
                        data_scope=f"w:{faction.world_id}",
                    )[faction.note],
                ],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("check", "sm"), span[t("factions.save")]
                    ]
                ],
                cls="stack-form faction-form",
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


def _step(page: Page, base: str, faction: Faction, cid: str, name: str, delta: int) -> Element:
    """One step of a character's renown, down or up."""
    return _post(
        base,
        "renown",
        hidden("faction", faction.id),
        hidden("character", cid),
        hidden("delta", str(delta)),
        button(
            type="submit",
            class_="btn btn-sm",
            aria_label=page.t("factions.change", name=name, delta=f"{delta:+d}"),
        )[f"{delta:+d}"],
    )


def _new(
    page: Page, base: str, tables: Sequence[tuple[str, str]], examples: Sequence[str]
) -> Element:
    """A new faction three ways, each a row with its own label: by name, from a book's
    renown table, or from the guide's examples."""
    t = page.t

    def row(op: str, ident: str, caption: str, control: Node, submit: Node) -> Element:
        return _post(
            base,
            op,
            div(class_="field")[label(for_=ident)[caption], control],
            submit,
            cls="faction-new-row",
        )

    return tool_dialog(
        page,
        "faction",
        t("factions.new"),
        div(class_="faction-new")[
            row(
                "create",
                "fn-name",
                t("factions.name"),
                input_el(id="fn-name", type="text", name="name", maxlength="120", required=True),
                button(type="submit", class_="btn btn-primary")[
                    icon("plus", "sm"), span[t("factions.create")]
                ],
            ),
            row(
                "from_table",
                "fn-table",
                t("factions.table"),
                select(id="fn-table", name="table")[
                    [
                        option(value=f"{name}|{source}")[
                            f"{name.removesuffix(' Renown')} ({source})"
                        ]
                        for name, source in tables
                    ]
                ],
                button(type="submit", class_="btn")[t("factions.from_book")],
            )
            if tables
            else None,
            row(
                "from_example",
                "fn-example",
                t("factions.example"),
                select(id="fn-example", name="example")[
                    [option(value=name)[name] for name in examples]
                ],
                button(type="submit", class_="btn")[t("factions.from_example")],
            )
            if examples
            else None,
            p(class_="field-hint")[t("factions.new_hint")],
        ],
    )


__all__ = ["FACTIONS_URL", "factions_page", "factions_url"]
