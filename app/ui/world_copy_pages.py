"""Copying or moving a piece into another world (app/services/world_copy.py): choose the
world, see what goes and what its books lack, then copy or move."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, Node, a, b, button, div, form, h2, li, p, section, span, ul

from app.models.gamestate import World
from app.services.world_copy import Copied, Piece, Plan
from app.ui.context import Page
from app.ui.copy_links import COPY_URL
from app.ui.creature_links import creature_url
from app.ui.encounter_pages import encounter_url
from app.ui.faction_pages import FACTIONS_URL
from app.ui.forms import alert, checkbox_field, hidden, select_field
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import adventure_url, location_url, npc_url, treasure_url
from app.ui.quest_pages import QUESTS_URL


def piece_url(kind: str, record_id: str) -> str:
    """Where a copied piece is read."""
    pages = {
        "adventure": adventure_url,
        "npc": npc_url,
        "place": location_url,
        "treasure": treasure_url,
        "encounter": encounter_url,
        "creature": creature_url,
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


def copy_page(
    page: Page,
    *,
    kind: str,
    record_id: str,
    name: str,
    mode: str,
    worlds: Sequence[World],
    plan: Plan | None,
    error: str | None = None,
) -> Element:
    """The world to copy into, what the copy holds, and the button."""
    t = page.t
    moving = mode == "move"
    title = t("copy.move_title" if moving else "copy.copy_title", name=name)
    target = plan.target.id if plan is not None else (worlds[0].id if worlds else "")
    choose = form(method="get", action=COPY_URL, class_="stack-form copy-choose")[
        hidden("kind", kind),
        hidden("id", record_id),
        hidden("mode", mode),
        select_field("target", t("copy.target"), [(w.id, w.name) for w in worlds], selected=target),
        checkbox_field(
            "bring",
            t("copy.bring"),
            checked=plan.bring if plan is not None else True,
            hint=t("copy.bring_hint"),
            value="1",
        ),
        hidden("checked", "1"),
        button(type="submit", class_="btn btn-sm")[icon("refresh-cw", "sm"), t("copy.check")],
    ]
    shown = None
    if plan is not None:
        shown = section(class_="panel copy-plan")[
            div(class_="panel-head")[h2[t("copy.plan", world=plan.target.name)]],
            _pieces(page, t("copy.goes"), plan.pieces),
            _pieces(
                page,
                t("copy.brought") if plan.bring else t("copy.not_brought"),
                plan.brought,
                muted=not plan.bring,
            ),
            _pieces(page, t("copy.reused", world=plan.target.name), plan.reused, muted=True),
            p(class_="note")[t("copy.renamed", name=plan.renamed)] if plan.renamed else None,
            div(class_="copy-group copy-outside")[
                b[icon("triangle-alert", "sm"), " ", t("copy.outside", world=plan.target.name)],
                p(class_="muted")[", ".join(x.replace("|", " · ") for x in plan.outside)],
                p(class_="field-hint")[t("copy.outside_hint")],
            ]
            if plan.outside
            else None,
            p(class_="field-hint")[t("copy.move_hint" if moving else "copy.copy_hint")],
            form(method="post", action=COPY_URL, class_="form-actions")[
                hidden("kind", kind),
                hidden("id", record_id),
                hidden("mode", mode),
                hidden("target", plan.target.id),
                hidden("bring", "1" if plan.bring else ""),
                button(type="submit", class_="btn btn-primary")[
                    icon("arrow-right" if moving else "copy"),
                    span[t("copy.move_now" if moving else "copy.copy_now")],
                ],
            ],
        ]
    return app_shell(
        page,
        title,
        div(class_="formpage")[
            page_head(t("copy.eyebrow"), title, t("copy.lede")),
            alert(error),
            p(class_="muted")[t("copy.no_worlds")] if not worlds else choose,
            shown,
        ],
    )


def copied_page(page: Page, done: Copied, back: str) -> Element:
    """What the copy made, with the way to it."""
    t = page.t
    plan = done.plan
    key = "copy.moved" if done.moved else "copy.copied"
    count = len(plan.pieces) + (len(plan.brought) if plan.bring else 0)
    return app_shell(
        page,
        t(key, name=plan.renamed or plan.name, world=plan.target.name),
        div(class_="formpage")[
            page_head(
                t("copy.eyebrow"),
                t(key, name=plan.renamed or plan.name, world=plan.target.name),
                t("copy.done_lede", n=count, files=done.files),
            ),
            p(class_="note warn")[
                icon("triangle-alert", "sm"),
                " ",
                t("copy.outside_done", n=len(plan.outside), world=plan.target.name),
            ]
            if plan.outside
            else None,
            div(class_="form-actions")[
                a(href=piece_url(plan.kind, done.id), class_="btn btn-primary")[
                    icon("arrow-right"), span[t("copy.open")]
                ],
                a(href=back, class_="btn btn-ghost")[t("copy.back")] if not done.moved else None,
            ],
        ],
    )
