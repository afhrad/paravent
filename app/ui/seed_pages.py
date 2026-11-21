"""Rolling an NPC as a villain or an adventure's ideas (app/services/story_seeds.py): each
part of the guides' tables with a box to keep it and a die to roll it again; **Keep**
writes what is ticked into the NPC's notes or the adventure's text."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from htpy import Element, Node, b, button, div, form, h1, label, li, p, section, small, span, ul
from htpy import input as input_el

from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import crumbs


def seed_lines(page: Page, kind: str, rolled: Mapping[str, str], kept: frozenset[str]) -> str:
    """The ticked parts as Markdown, as they are kept: an NPC's lines beneath a heading,
    an adventure's in a secret box only the gamemaster reads."""
    t = page.t
    lines = [f"- **{t(f'seeds.part.{p}')}:** {v}" for p, v in rolled.items() if p in kept and v]
    if not lines:
        return ""
    title = t(f"seeds.{kind}.kept")
    if kind == "adventure":
        return "\n".join([f"> [!secret] {title}", *(f"> {line}" for line in lines)])
    return "\n".join([f"**{title}**", "", *lines])


def seed_page(
    page: Page,
    *,
    kind: str,
    name: str,
    action: str,
    trail: Sequence[tuple[str, str | None]],
    rolled: Mapping[str, str],
    sources: Mapping[str, str],
    kept: frozenset[str],
    world_id: str,
    note: Node = None,
) -> Element:
    """The parts as rolled, each ticked to keep; ``note`` says what the roll went by."""
    t = page.t
    title = t(f"seeds.{kind}.title", name=name)
    rows = [
        li(class_="settle-row")[
            label(class_="settle-keep")[
                input_el(type="checkbox", name="keep", value=part, checked=part in kept),
                span(class_="settle-label")[t(f"seeds.part.{part}")],
            ],
            span(class_="settle-value")[value],
            hidden(f"v_{part}", value),
            small(class_="muted mono settle-book")[sources.get(part, "")],
            button(
                type="submit",
                name="action",
                value=f"reroll:{part}",
                class_="btn btn-sm btn-ghost",
                title=t("settlement.reroll"),
                aria_label=t("settlement.reroll"),
            )[icon("dices", "sm")],
        ]
        for part, value in rolled.items()
    ]
    return app_shell(
        page,
        title,
        div(class_="prose-page settlement-page")[
            crumbs(page, *trail, (t(f"seeds.{kind}.open"), None)),
            h1[title],
            p(class_="muted")[t(f"seeds.{kind}.lede")],
            note,
            form(method="post", action=action, class_="stack-form")[
                section(class_="panel")[
                    ul(class_="settle-list")[rows]
                    if rows
                    else p(class_="muted")[b[t(f"seeds.{kind}.no_tables")]],
                ],
                div(class_="form-actions")[
                    button(type="submit", name="action", value="apply", class_="btn btn-primary")[
                        icon("check", "sm"), span[t("settlement.keep")]
                    ]
                    if rows
                    else None,
                    button(type="submit", name="action", value="roll", class_="btn")[
                        icon("dices", "sm"), span[t("settlement.roll_all")]
                    ],
                ],
            ],
        ],
        world_id=world_id,
    )


__all__ = ["seed_lines", "seed_page"]
