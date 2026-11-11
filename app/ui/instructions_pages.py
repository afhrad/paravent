"""A world's instructions for Claude (documentation/MCP.md §5): the setting, then one
text per kind of thing Claude writes — read by Claude through the MCP server, never
shown to the table."""

from __future__ import annotations

from htpy import Element, a, b, button, div, form, label, p, span, textarea

from app.models.gamestate import World, WorldInstructions
from app.services.world_instructions import FIELDS
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import WORLDS_URL, crumbs, world_url


def instructions_url(world_id: str) -> str:
    """A world's instructions page."""
    return f"{world_url(world_id)}/claude"


def instructions_page(
    page: Page,
    world: World,
    instructions: WorldInstructions,
    *,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """One field per instruction, each with what it is for and an example."""
    t = page.t
    fields = [
        div(class_="field")[
            label(for_=f"f-{key}")[t(f"instructions.{key}")],
            p(class_="field-hint", id=f"f-{key}-hint")[t(f"instructions.{key}_hint")],
            textarea(
                id=f"f-{key}",
                name=key,
                rows="10" if key == "setting" else "4",
                placeholder=t(f"instructions.{key}_example"),
                aria_describedby=f"f-{key}-hint",
            )[getattr(instructions, key)],
        ]
        for key in FIELDS
    ]
    return app_shell(
        page,
        t("instructions.title"),
        div(class_="formpage formpage-wide")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("instructions.title"), None),
            ),
            page_head(
                t("adventures.world_eyebrow"), t("instructions.title"), t("instructions.lede")
            ),
            notice(notice_text),
            alert(error),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[icon("sparkles", "sm"), world.name],
                    b[t("instructions.title")],
                ],
                form(method="post", action=instructions_url(world.id), class_="stack-form")[
                    fields,
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"), span[t("worlds.save")]
                        ],
                        a(href=world_url(world.id), class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
        ],
        world_id=world.id,
    )


__all__ = ["instructions_page", "instructions_url"]
