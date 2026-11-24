"""The party view's wild magic (app/services/wild_magic.py): the zone switched on or
off, a surge rolled on the characters ticked, and the surges just rolled."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote

from htpy import Node, b, button, div, fieldset, form, label, legend, li, p, span, ul
from htpy import input as input_el

from app.services.wild_magic import Surge
from app.ui.context import Page
from app.ui.icons import icon
from app.ui.tool_dialog import tool_button, tool_dialog

WILD_URL = "/gm/wild"


def wild_dialog_url(world_id: str) -> str:
    """The party view with the dialog open again."""
    return f"/gm?world={quote(world_id)}&tool=wild"


def wild_tool(
    page: Page,
    world_id: str | None,
    characters: Sequence[tuple[str, str]],
    *,
    zone: bool,
    has_table: bool,
    surges: Sequence[Surge] = (),
) -> Node:
    """The button (marked while the party is in a zone) and its dialog."""
    if world_id is None or not characters:
        return None
    t = page.t
    base = f"{WILD_URL}/{quote(world_id)}"
    body = [
        p(class_="field-hint")[t("wild.lede" if has_table else "wild.no_table")],
        ul(class_="wild-surges")[
            [
                li[
                    b[x.name],
                    span(class_="mono muted")[f" · {t('rules.dice_letter')}100 {x.roll}"],
                    span[" — ", x.text],
                ]
                for x in surges
            ]
        ]
        if surges
        else None,
        form(method="post", action=f"{base}/zone", class_="tool-section")[
            label(class_="cs-heal-pick")[
                input_el(type="checkbox", name="on", value="1", checked=zone),
                span[t("wild.zone")],
            ],
            p(class_="field-hint")[t("wild.zone_hint")],
            div(class_="form-actions")[button(type="submit", class_="btn btn-sm")[t("wild.save")]],
        ],
        form(method="post", action=f"{base}/surge", class_="tool-section")[
            fieldset(class_="xp-who")[
                legend[t("wild.who")],
                [
                    label(class_="cs-heal-pick")[
                        input_el(type="checkbox", name="to", value=cid), span[name]
                    ]
                    for cid, name in characters
                ],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary", disabled=not has_table)[
                    icon("sparkles", "sm"), span[t("wild.surge")]
                ],
            ],
        ],
    ]
    title = t("wild.title_zone") if zone else t("wild.title")
    return [
        tool_button(page, "wild", title, "sparkles"),
        tool_dialog(page, "wild", t("wild.title"), body, is_open=bool(surges)),
    ]


__all__ = ["WILD_URL", "wild_dialog_url", "wild_tool"]
