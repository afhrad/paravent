"""Checks the gamemaster calls for outside a fight (app/services/checks.py): the panel on
the party view, and the call on each asked player's sheet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    b,
    button,
    details,
    div,
    form,
    label,
    li,
    optgroup,
    option,
    select,
    small,
    span,
    summary,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import CheckCall
from app.services.checks import ABILITIES, SKILLS
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

CHECKS_URL = "/gm/checks"


def check_label(page: Page, call: CheckCall) -> str:
    """ "Perception", "Wisdom check", "Dexterity saving throw"."""
    t = page.t
    if call.kind == "skill":
        return t(f"skill.{call.subject}")
    ability = t(f"ability.{call.subject}")
    return t(f"checks.kind.{call.kind}", ability=ability)


def checks_panel(
    page: Page,
    calls: Sequence[CheckCall],
    characters: Sequence[tuple[str, str]],
    world_id: str | None,
) -> Element:
    """Call for a check — asked of the players or rolled secretly — and the calls with
    their results as they come in."""
    t = page.t
    query = f"?{urlencode({'world': world_id})}" if world_id else ""
    names = dict(characters)
    return div(id="party-checks", class_="party-checks", data_url=f"{CHECKS_URL}/panel{query}")[
        details(class_="panel party-checks-call")[
            summary(class_="party-checks-head")[icon("dices", "sm"), span[t("checks.call")]],
            _call_form(page, characters, world_id) if characters else small[t("checks.no_one")],
        ],
        ul(class_="party-checks-list")[[_call(page, c, names, world_id) for c in calls]]
        if calls
        else None,
    ]


def _call_form(page: Page, characters: Sequence[tuple[str, str]], world_id: str | None) -> Element:
    t = page.t
    return form(
        method="post",
        action=CHECKS_URL,
        hx_post=CHECKS_URL,
        hx_target="#party-checks",
        hx_swap="outerHTML",
        class_="party-checks-form",
    )[
        hidden("world", world_id or ""),
        label(class_="field")[
            span[t("checks.what")],
            select(name="what")[
                optgroup(label=t("checks.group.skills"))[
                    [option(value=f"skill:{s}")[t(f"skill.{s}")] for s in SKILLS]
                ],
                optgroup(label=t("checks.group.checks"))[
                    [
                        option(value=f"check:{a}")[
                            t("checks.kind.check", ability=t(f"ability.{a}"))
                        ]
                        for a in ABILITIES
                    ]
                ],
                optgroup(label=t("checks.group.saves"))[
                    [
                        option(value=f"save:{a}")[t("checks.kind.save", ability=t(f"ability.{a}"))]
                        for a in ABILITIES
                    ]
                ],
            ],
        ],
        label(class_="field party-checks-dc")[
            span[t("checks.dc")],
            input_el(
                type="number",
                name="dc",
                min="1",
                max="40",
                inputmode="numeric",
                placeholder="–",
                class_="mono",
            ),
        ],
        div(class_="party-checks-who")[
            [
                label(class_="cs-heal-pick")[
                    input_el(type="checkbox", name="character", value=cid, checked=True),
                    span[name],
                ]
                for cid, name in characters
            ]
        ],
        label(class_="cs-heal-pick")[
            input_el(type="checkbox", name="group", value="1"), span[t("checks.group_check")]
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("send", "sm"), span[t("checks.ask")]
            ],
            button(
                type="submit",
                name="secret",
                value="1",
                class_="btn btn-sm",
                title=t("checks.secret_hint"),
            )[icon("eye-off", "sm"), span[t("checks.secret")]],
        ],
    ]


def _call(page: Page, call: CheckCall, names: Mapping[str, str], world_id: str | None) -> Element:
    t = page.t
    rows = []
    for character_id in call.characters:
        total = call.results.get(character_id)
        passed = call.passed(character_id)
        mark = "" if passed is None else ("✓" if passed else "✗")
        rows.append(
            li(class_=f"party-check-row {'ok' if passed else 'bad' if passed is False else ''}")[
                span[names.get(character_id, "?")],
                b(class_="mono")[f"{total} {mark}".strip()]
                if total is not None
                else small(class_="muted")[t("checks.waiting")],
            ]
        )
    outcome: Node = None
    if call.group and call.group_passed is not None:
        outcome = span(class_=f"pill {'heal' if call.group_passed else 'dmg'}")[
            t("checks.group_passed" if call.group_passed else "checks.group_failed")
        ]
    query = f"?{urlencode({'world': world_id})}" if world_id else ""
    return li(class_=f"panel party-check {'open' if call.open else 'closed'}")[
        div(class_="party-check-head")[
            b[check_label(page, call)],
            span(class_="pill neutral mono")[t("checks.dc_value", dc=call.dc)] if call.dc else None,
            span(class_="pill neutral")[t("checks.group_check")] if call.group else None,
            span(class_="pill neutral")[t("checks.secret")] if call.secret else None,
            outcome,
            form(
                method="post",
                action=f"{CHECKS_URL}/{call.id}/close{query}",
                hx_post=f"{CHECKS_URL}/{call.id}/close{query}",
                hx_target="#party-checks",
                hx_swap="outerHTML",
                class_="party-check-close",
            )[button(type="submit", class_="btn btn-sm btn-ghost")[t("checks.close")]]
            if call.open
            else None,
        ],
        ul(class_="party-check-rows")[rows],
    ]


def check_banner(page: Page, call: CheckCall, character_id: str, roll: Node) -> Element:
    """A call on an asked player's sheet: what to roll, and the button, or their total.
    ``roll`` is the sheet's own roll button for that check."""
    t = page.t
    total = call.results.get(character_id)
    return div(class_="notice cs-call", role="status")[
        icon("dices"),
        span[t("checks.asked", what=check_label(page, call))],
        b(class_="mono")[t("checks.you_rolled", total=total)] if total is not None else roll,
    ]


__all__ = ["CHECKS_URL", "check_banner", "check_label", "checks_panel"]
