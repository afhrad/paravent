"""Copying or moving pieces into another world, or into a file and back
(app/services/world_copy.py): choose the world, see what goes and what its books lack,
then copy, move or save; read a saved file into the table's world."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from htpy import Element, Node, a, b, button, div, form, h2, label, li, p, section, span, ul
from htpy import input as input_el

from app.models.gamestate import World
from app.services.world_copy import Copied, Piece, Plan
from app.ui.context import Page
from app.ui.copy_links import COPY_URL, FILE_URL, IMPORT_URL
from app.ui.creature_links import creature_url
from app.ui.encounter_pages import encounter_url
from app.ui.faction_pages import FACTIONS_URL
from app.ui.forms import alert, checkbox_field, hidden, select_field
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.item_links import background_url, item_url, spell_url
from app.ui.layout import app_shell
from app.ui.library_links import adventure_url, location_url, npc_url, treasure_url
from app.ui.map_pages import MAPS_URL, map_url
from app.ui.quest_pages import QUESTS_URL

# Each kind's list, to go back to.
LISTS = {
    "adventure": "/gm/adventures",
    "npc": "/gm/npcs",
    "place": "/gm/places",
    "treasure": "/gm/treasure",
    "encounter": "/gm/encounters",
    "creature": "/gm/creatures",
    "quest": QUESTS_URL,
    "faction": FACTIONS_URL,
    "map": MAPS_URL,
}


def piece_url(kind: str, record_id: str) -> str:
    """Where a piece is read."""
    pages = {
        "adventure": adventure_url,
        "npc": npc_url,
        "place": location_url,
        "treasure": treasure_url,
        "encounter": encounter_url,
        "creature": creature_url,
        "item_design": item_url,
        "spell_design": spell_url,
        "background_design": background_url,
        "map": map_url,
    }
    if kind in pages:
        return pages[kind](record_id)
    return QUESTS_URL if kind == "quest" else FACTIONS_URL


def _pieces(page: Page, heading: str, pieces: Sequence[Piece], muted: bool = False) -> Node:
    if not pieces:
        return None
    t = page.t
    return div(class_="copy-group")[
        b[heading],
        ul(class_="copy-list" + (" muted" if muted else ""))[
            [li[span(class_="pill neutral")[t(f"copy.kind.{x.kind}")], " ", x.name] for x in pieces]
        ],
    ]


def _plan_panel(page: Page, plan: Plan, heading: str) -> list[Node]:
    """What goes, what comes along, what the world has, new names, missing books."""
    t = page.t
    world = plan.target.name if plan.target is not None else ""
    renamed = [
        t("copy.renamed_one", old=x.name, name=plan.renamed[x.id])
        for x in plan.pieces
        if x.id in plan.renamed
    ]
    return [
        div(class_="panel-head")[h2[heading]],
        _pieces(page, t("copy.goes"), plan.pieces),
        _pieces(
            page,
            t("copy.brought") if plan.bring else t("copy.not_brought"),
            plan.brought,
            muted=not plan.bring,
        ),
        _pieces(page, t("copy.reused", world=world), plan.reused, muted=True),
        div(class_="note")[[p[x] for x in renamed]] if renamed else None,
        div(class_="copy-group copy-outside")[
            b[icon("triangle-alert", "sm"), " ", t("copy.outside", world=world)],
            p(class_="muted")[", ".join(x.replace("|", " · ") for x in plan.outside)],
            p(class_="field-hint")[t("copy.outside_hint")],
        ]
        if plan.outside
        else None,
    ]


def copy_page(
    page: Page,
    *,
    kind: str,
    ids: Sequence[str],
    name: str,
    mode: str,
    worlds: Sequence[World],
    plan: Plan | None,
    bring: bool = True,
    error: str | None = None,
) -> Element:
    """The world to copy into (none for a file), what goes, and the button."""
    t = page.t
    title = t(f"copy.{mode}_title", name=name)
    target = plan.target.id if plan is not None and plan.target is not None else ""
    pieces = [hidden("id", i) for i in ids]
    choose = form(method="get", action=COPY_URL, class_="stack-form copy-choose")[
        hidden("kind", kind),
        pieces,
        hidden("mode", mode),
        select_field("target", t("copy.target"), [(w.id, w.name) for w in worlds], selected=target)
        if mode != "file"
        else None,
        checkbox_field(
            "bring", t("copy.bring"), checked=bring, hint=t("copy.bring_hint"), value="1"
        ),
        hidden("checked", "1"),
        button(type="submit", class_="btn btn-sm")[icon("refresh-cw", "sm"), t("copy.check")],
    ]
    shown = None
    if plan is not None and mode == "file":
        query = urlencode(
            [("kind", kind), *(("id", i) for i in ids), ("bring", "1" if bring else "")]
        )
        shown = section(class_="panel copy-plan")[
            _plan_panel(page, plan, t("copy.file_plan")),
            p(class_="field-hint")[t("copy.file_hint")],
            div(class_="form-actions")[
                a(href=f"{FILE_URL}?{query}", class_="btn btn-primary", download="")[
                    icon("download"), span[t("copy.file_now")]
                ]
            ],
        ]
    elif plan is not None and plan.target is not None:
        moving = mode == "move"
        shown = section(class_="panel copy-plan")[
            _plan_panel(page, plan, t("copy.plan", world=plan.target.name)),
            p(class_="field-hint")[t("copy.move_hint" if moving else "copy.copy_hint")],
            form(method="post", action=COPY_URL, class_="form-actions")[
                hidden("kind", kind),
                pieces,
                hidden("mode", mode),
                hidden("target", plan.target.id),
                hidden("bring", "1" if plan.bring else ""),
                button(type="submit", class_="btn btn-primary")[
                    icon("arrow-right" if moving else "copy"),
                    span[t("copy.move_now" if moving else "copy.copy_now")],
                ],
            ],
        ]
    no_world = mode != "file" and not worlds
    if not ids:  # nothing ticked: only why, and the way back
        title = t("copy.eyebrow")
        choose = a(href=LISTS.get(kind, "/gm"), class_="btn btn-ghost")[t("copy.back_list")]
        no_world = False
    return app_shell(
        page,
        title,
        div(class_="formpage")[
            page_head(t("copy.eyebrow"), title, t("copy.lede")) if ids else None,
            alert(error),
            p(class_="muted")[t("copy.no_worlds")] if no_world else choose,
            shown,
        ],
    )


def copied_page(page: Page, done: Copied, back: str, *, imported: bool = False) -> Element:
    """What the copy or import made, with the way to it."""
    t = page.t
    plan = done.plan
    world = plan.target.name if plan.target is not None else ""
    names = [plan.renamed.get(x.id, x.name) for x in plan.pieces if x.id in done.ids]
    shown = names[0] if len(names) == 1 else t.plural("copy.pieces", len(names), n=len(names))
    key = "copy.imported" if imported else "copy.moved" if done.moved else "copy.copied"
    heading = t(key, name=shown, world=world)
    count = len(plan.pieces) + (len(plan.brought) if plan.bring else 0)
    first = done.first
    return app_shell(
        page,
        heading,
        div(class_="formpage")[
            page_head(
                t("copy.eyebrow"),
                heading,
                t.plural("copy.done_lede", count, n=count, files=done.files),
            ),
            div(class_="panel copy-plan")[_plan_panel(page, plan, t("copy.what_came"))]
            if imported
            else None,
            p(class_="note warn")[
                icon("triangle-alert", "sm"),
                " ",
                t.plural("copy.outside_done", len(plan.outside), n=len(plan.outside), world=world),
            ]
            if plan.outside and not imported
            else None,
            p(class_="note warn")[t("copy.skipped", names=", ".join(done.skipped))]
            if done.skipped
            else None,
            div(class_="form-actions")[
                a(href=piece_url(*first), class_="btn btn-primary")[
                    icon("arrow-right"), span[t("copy.open")]
                ]
                if first is not None
                else None,
                a(href=back, class_="btn btn-ghost")[t("copy.back")]
                if not done.moved and not imported
                else None,
            ],
        ],
    )


def import_page(page: Page, world: World, *, error: str | None = None) -> Element:
    """A saved file of pieces, read into the table's world."""
    t = page.t
    title = t("copy.import_title", world=world.name)
    return app_shell(
        page,
        title,
        div(class_="formpage")[
            page_head(t("copy.eyebrow"), title, t("copy.import_lede")),
            alert(error),
            form(
                method="post",
                action=IMPORT_URL,
                enctype="multipart/form-data",
                class_="stack-form",
            )[
                div(class_="field")[
                    label(for_="f-file")[t("copy.import_file")],
                    input_el(id="f-file", type="file", name="file", accept=".zip", required=True),
                ],
                checkbox_field(
                    "bring",
                    t("copy.import_bring"),
                    checked=True,
                    hint=t("copy.import_bring_hint"),
                    value="1",
                ),
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("upload"), span[t("copy.import_now")]
                    ]
                ],
            ],
        ],
    )
