"""Springing a trap or hazard at the table (app/services/traps.py): a button beside the
entry for the gamemaster, which opens a form of the world's characters, and what the
trap did to each."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from htpy import Element, Node, b, button, div, form, label, li, p, small, span, ul
from htpy import input as input_el

from app.services.traps import Caught, TrapCheck, TrapEffect, TrapSave
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

TRAPS_URL = "/gm/traps"
TRAP_CATEGORIES = frozenset({"trap", "hazard"})


def spring_button(page: Page, world_id: str | None, category: str, name: str, source: str) -> Node:
    """For the gamemaster in a world: "Spring it", which loads the form in place."""
    if (
        world_id is None
        or category not in TRAP_CATEGORIES
        or page.identity is None
        or not page.identity.is_gamemaster
    ):
        return None
    query = urlencode({"world": world_id, "category": category, "name": name, "source": source})
    return div(class_="trap-spring")[
        button(
            type="button",
            class_="btn btn-sm",
            hx_get=f"{TRAPS_URL}/form?{query}",
            hx_target="closest .trap-spring",
            hx_swap="innerHTML",
        )[icon("zap", "sm"), span[page.t("trap.spring")]]
    ]


def spring_form(
    page: Page,
    world_id: str,
    category: str,
    name: str,
    source: str,
    effect: TrapEffect,
    members: Sequence[tuple[str, str]],
    level: int,
    *,
    result: Node = None,
) -> Element:
    """Whom the trap catches, what it will do (scaled to the party's ``level``), and the
    checks that find or undo it, to call for."""
    t = page.t
    scaled = effect.at_level(level)
    url = f"{TRAPS_URL}/spring"
    return form(
        method="post",
        action=url,
        hx_post=url,
        hx_target="closest .trap-spring",
        hx_swap="innerHTML",
        class_="trap-form",
    )[
        hidden("world", world_id),
        hidden("category", category),
        hidden("name", name),
        hidden("source", source),
        result,
        ul(class_="trap-effects")[
            [li[_save_words(page, s)] for s in scaled.saves],
            li[
                t(
                    "trap.attack",
                    bonus=f"{scaled.attack.bonus:+d}",
                    damage=_damage_words(page, scaled.attack.damage, scaled.attack.damage_type),
                )
            ]
            if scaled.attack
            else None,
        ]
        if scaled.automatic
        else p(class_="muted")[t("trap.by_hand")],
        small(class_="muted")[t("trap.scaled", level=level)] if effect.levels else None,
        div(class_="trap-who")[
            [
                label(class_="party-clock-label")[
                    input_el(type="checkbox", name="character", value=cid), span[shown]
                ]
                for cid, shown in members
            ]
        ]
        if members
        else p(class_="muted")[t("trap.no_one")],
        div(class_="trap-buttons")[
            button(type="submit", name="op", value="spring", class_="btn btn-sm btn-primary")[
                icon("zap", "sm"), span[t("trap.spring_now")]
            ]
            if scaled.automatic and members
            else None,
            [
                button(
                    type="submit",
                    name="op",
                    value=f"call:{c.skill}:{c.dc}",
                    class_="btn btn-sm",
                    title=t("trap.call_hint"),
                )[_check_words(page, c)]
                for c in effect.checks
            ]
            if members
            else None,
        ],
    ]


def spring_result(page: Page, name: str, caught: Sequence[Caught]) -> Element:
    """What the trap did, one line a character."""
    t = page.t
    return div(class_="trap-result notice")[
        b[t("trap.sprung", name=name)],
        ul[
            [
                li[
                    span[c.name],
                    ": ",
                    span[
                        t(
                            "trap.result_hit" if c.failed else "trap.result_safe",
                            roll=c.roll if c.roll is not None else "—",
                            against=c.against if c.against is not None else "—",
                        )
                    ],
                    (
                        " · "
                        + t("trap.result_damage", amount=c.damage, type=_type(page, c.damage_type))
                        if c.damage
                        else ""
                    ),
                    (
                        " · " + ", ".join(t(f"condition.{x}") for x in c.conditions)
                        if c.conditions
                        else ""
                    ),
                ]
                for c in caught
            ]
        ],
    ]


def _save_words(page: Page, save: TrapSave) -> str:
    t = page.t
    parts = [t("trap.save", ability=t(f"ability.{save.ability}"), dc=save.dc)]
    if save.damage:
        parts.append(_damage_words(page, save.damage, save.damage_type))
        if save.half:
            parts.append(t("trap.half"))
    if save.conditions:
        parts.append(", ".join(t(f"condition.{c}") for c in save.conditions))
    if save.on_hit:
        parts.append(t("trap.on_hit"))
    return " · ".join(parts)


def _damage_words(page: Page, dice: str, damage_type: str) -> str:
    shown = dice.replace("d", page.t("rules.dice_letter")) if dice else "—"
    return f"{shown} {_type(page, damage_type)}".strip()


def _type(page: Page, damage_type: str) -> str:
    if not damage_type:
        return ""
    key = f"rules.damage.{damage_type}"
    word = page.t(key)
    return damage_type if word == key else word


def _check_words(page: Page, check: TrapCheck) -> str:
    t = page.t
    return t("trap.call", skill=t(f"skill.{check.skill}"), dc=check.dc)


__all__ = ["TRAPS_URL", "TRAP_CATEGORIES", "spring_button", "spring_form", "spring_result"]
