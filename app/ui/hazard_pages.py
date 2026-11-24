"""The party view's hazards (app/services/hazards.py): choose what strikes and how badly,
then spring it on the characters ticked as a trap is sprung (app/ui/trap_pages.py)."""

from __future__ import annotations

from urllib.parse import urlencode

from htpy import Element, Node, button, div, form, label, option, p, select, small, span
from htpy import input as input_el

from app.services.hazards import DAMAGE_TYPES, KINDS, Scale
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.icons import icon
from app.ui.rules_text import dice
from app.ui.tool_dialog import tool_button, tool_dialog
from app.ui.trap_pages import HAZARDS_URL

SAVES = ("str", "dex", "con", "int", "wis", "cha")


def hazard_tool(page: Page, world_id: str | None, scaled: Scale | None, level: int) -> Node:
    """The button and its dialog, the choice inside the box a sprung trap's form fills."""
    if world_id is None:
        return None
    t = page.t
    return [
        tool_button(page, "hazard", t("hazard.title"), "flame"),
        tool_dialog(
            page,
            "hazard",
            t("hazard.title"),
            div(class_="trap-spring hazard-spring")[hazard_form(page, world_id, scaled, level)],
        ),
    ]


def hazard_url(world_id: str) -> str:
    """The choice again, for another hazard."""
    return f"{HAZARDS_URL}/form?{urlencode({'world': world_id, 'fresh': '1'})}"


def hazard_form(
    page: Page, world_id: str, scaled: Scale | None, level: int, *, error: str | None = None
) -> Element:
    """What strikes (its kind, how bad by the world's guide at the party's ``level``, the
    save and the damage), sent to make the spring form."""
    t = page.t
    severities = scaled.severities if scaled is not None else ()

    def field(caption: str, control: Node, *, hint: str | None = None) -> Element:
        return label(class_="field")[
            span[caption], control, small(class_="field-hint")[hint] if hint else None
        ]

    return form(
        method="get",
        action=f"{HAZARDS_URL}/form",
        hx_get=f"{HAZARDS_URL}/form",
        hx_target="closest .trap-spring",
        hx_swap="innerHTML",
        class_="stack-form hazard-form",
    )[
        hidden("world", world_id),
        alert(error),
        p(class_="field-hint")[
            t(
                "hazard.lede" if scaled is not None else "hazard.lede_no_table",
                level=level,
                book=t(f"hazard.book.{scaled.rules}") if scaled is not None else "",
            )
        ],
        div(class_="tool-grid")[
            field(
                t("hazard.kind"),
                select(name="kind")[[option(value=k)[t(f"hazard.kind.{k}")] for k in KINDS],],
            ),
            field(
                t("hazard.severity"),
                select(name="severity")[
                    [
                        option(value=s, selected=n == len(severities) // 2)[
                            t(
                                "hazard.severity_with",
                                name=t(f"hazard.severity.{s}"),
                                dice=dice(page, scaled.damage[s]),
                                dc=scaled.dc.get(s, "—"),
                            )
                        ]
                        for n, s in enumerate(severities)
                        if scaled is not None
                    ]
                ],
            )
            if severities
            else None,
            field(
                t("hazard.save"),
                select(name="ability")[
                    option(value="")[t("hazard.as_kind")],
                    option(value="none")[t("hazard.no_save")],
                    [option(value=a)[t(f"ability.{a}")] for a in SAVES],
                ],
            ),
            field(
                t("hazard.type"),
                select(name="type")[
                    option(value="")[t("hazard.as_kind")],
                    [option(value=d)[t(f"creatures.damage.{d}")] for d in DAMAGE_TYPES],
                ],
            ),
            field(
                t("hazard.dc"),
                input_el(
                    type="number", name="dc", min="1", max="30", inputmode="numeric", class_="mono"
                ),
                hint=t("hazard.dc_hint"),
            ),
            field(
                t("hazard.dice"),
                input_el(type="text", name="dice", maxlength="20", class_="mono"),
                hint=t("hazard.dice_hint") if severities else t("hazard.dice_needed"),
            ),
            field(
                t("hazard.feet"),
                input_el(
                    type="text", name="feet", maxlength="6", inputmode="decimal", class_="mono"
                ),
                hint=t("hazard.feet_hint_m" if page.u.metric else "hazard.feet_hint"),
            ),
        ],
        label(class_="check-inline")[
            input_el(type="checkbox", name="half", value="1", checked=True),
            span[t("hazard.half")],
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("flame", "sm"), span[t("hazard.next")]
            ],
        ],
    ]


__all__ = ["hazard_form", "hazard_tool", "hazard_url"]
