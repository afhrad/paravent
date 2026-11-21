"""How someone of the campaign stands toward the party — friendly, indifferent or hostile —
and when they last met it by the world's clock: on an NPC's page and in the NPC list, and
on a character's contacts (``attitude`` and ``last_seen`` on Npc and Contact)."""

from __future__ import annotations

from htpy import Element, Node, button, div, form, option, select, small, span

from app.ui.clock_pages import clock_label, remaining
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

ATTITUDES = ("friendly", "indifferent", "hostile")
TONE = {"friendly": "heal", "hostile": "dmg"}


def attitude_pill(page: Page, attitude: str | None) -> Node:
    """The attitude as a coloured pill; nothing while none is set."""
    if attitude not in ATTITUDES:
        return None
    return span(class_=f"pill attitude-pill {TONE.get(attitude, '')}".strip())[
        page.t(f"attitude.{attitude}")
    ]


def seen_text(page: Page, last_seen: int | None, now: int | None, world_id: str | None) -> Node:
    """When they were last seen, by the world's clock, and how long ago."""
    if last_seen is None:
        return None
    t = page.t
    when = clock_label(page, last_seen, world_id)
    ago = (
        t("attitude.ago", time=remaining(page, now - last_seen)) if now and now > last_seen else ""
    )
    return small(class_="muted attitude-seen")[t("attitude.seen", when=when), " ", ago]


def attitude_select(page: Page, attitude: str | None) -> Element:
    """A choice of attitude, "—" for none (a form's field ``attitude``)."""
    t = page.t
    return select(name="attitude", aria_label=t("attitude.label"))[
        option(value="", selected=attitude is None)["—"],
        [option(value=a, selected=a == attitude)[t(f"attitude.{a}")] for a in ATTITUDES],
    ]


def attitude_panel(
    page: Page,
    action: str,
    attitude: str | None,
    last_seen: int | None,
    now: int | None,
    world_id: str | None,
) -> Element:
    """An NPC's attitude as three buttons, **Seen now** and when they were last seen."""
    t = page.t
    return div(class_="attitude-panel")[
        span(class_="eyebrow")[t("attitude.label")],
        div(class_="attitude-buttons", role="group", aria_label=t("attitude.label"))[
            [
                form(method="post", action=action, class_="inline-form")[
                    hidden("op", "set"),
                    hidden("attitude", "" if a == attitude else a),
                    button(
                        type="submit",
                        class_=f"btn btn-sm {'is-on ' + TONE.get(a, '') if a == attitude else ''}",
                        aria_pressed="true" if a == attitude else "false",
                    )[t(f"attitude.{a}")],
                ]
                for a in ATTITUDES
            ],
        ],
        form(method="post", action=action, class_="inline-form")[
            hidden("op", "seen"),
            button(type="submit", class_="btn btn-sm btn-ghost", title=t("attitude.seen_now_hint"))[
                icon("eye", "sm"), span[t("attitude.seen_now")]
            ],
        ]
        if now is not None
        else None,
        seen_text(page, last_seen, now, world_id),
    ]


__all__ = ["ATTITUDES", "attitude_panel", "attitude_pill", "attitude_select", "seen_text"]
