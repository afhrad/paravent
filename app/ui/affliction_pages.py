"""Diseases, poisons and curses at the table (app/services/afflictions.py): a button
beside a disease or poison for the gamemaster, which opens a form of the world's
characters; what catching it did to each; and the block on the sheet that shows what a
character carries, with the gamemaster's curse and cure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    h3,
    label,
    li,
    p,
    section,
    small,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.pc import Affliction, PlayerCharacter
from app.services.afflictions import DAY, AfflictionEffect, Outcome
from app.ui.clock_pages import clock_label
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.tags import ref_url

AFFLICTIONS_URL = "/gm/afflictions"


def afflicts(category: str, data: Mapping[str, Any]) -> str | None:
    """The kind of affliction an entry is ("disease", "poison"), or None."""
    if category == "disease":
        return "disease"
    if category == "item" and data.get("poison"):
        return "poison"
    return None


def afflict_button(
    page: Page, world_id: str | None, category: str, name: str, source: str, data: Mapping[str, Any]
) -> Node:
    """For the gamemaster in a world: "Afflict", which loads the form in place."""
    if (
        world_id is None
        or afflicts(category, data) is None
        or page.identity is None
        or not page.identity.is_gamemaster
    ):
        return None
    query = urlencode({"world": world_id, "category": category, "name": name, "source": source})
    return div(class_="trap-spring affliction-afflict")[
        button(
            type="button",
            class_="btn btn-sm",
            hx_get=f"{AFFLICTIONS_URL}/form?{query}",
            hx_target="closest .affliction-afflict",
            hx_swap="innerHTML",
        )[icon("skull", "sm"), span[page.t("affliction.afflict")]]
    ]


def afflict_form(
    page: Page,
    world_id: str,
    category: str,
    name: str,
    source: str,
    effect: AfflictionEffect,
    members: Sequence[tuple[str, str]],
    *,
    result: Node = None,
) -> Element:
    """Whom it is put on, what the books say it does, and whether they save."""
    t = page.t
    url = f"{AFFLICTIONS_URL}/afflict"
    catch = effect.catch
    return form(
        method="post",
        action=url,
        hx_post=url,
        hx_target="closest .affliction-afflict",
        hx_swap="innerHTML",
        class_="trap-form",
    )[
        hidden("world", world_id),
        hidden("category", category),
        hidden("name", name),
        hidden("source", source),
        result,
        ul(class_="trap-effects")[[li[line] for line in effect_lines(page, effect)]],
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
            button(type="submit", name="op", value="save", class_="btn btn-sm btn-primary")[
                icon("dices", "sm"),
                span[t("affliction.roll_save", save=_save(page, catch.ability, catch.dc))],
            ]
            if catch is not None and members
            else None,
            button(type="submit", name="op", value="caught", class_="btn btn-sm")[
                t("affliction.caught_now")
            ]
            if members
            else None,
        ],
    ]


def effect_lines(page: Page, effect: AfflictionEffect) -> list[str]:
    """What the text says it does, a line each, as far as it says it in numbers."""
    t = page.t
    out: list[str] = []
    catch = effect.catch
    if catch is not None:
        parts = [_save(page, catch.ability, catch.dc)]
        if catch.damage:
            parts.append(_damage(page, catch.damage, catch.damage_type))
            if catch.half:
                parts.append(t("trap.half"))
        if catch.conditions:
            parts.append(", ".join(t(f"condition.{c}") for c in catch.conditions))
        if effect.lasts:
            parts.append(t("affliction.lasts", time=_span(page, effect.lasts, effect.lasts_unit)))
        out.append(" · ".join(parts))
    if effect.onset:
        out.append(t("affliction.onset", time=_span(page, effect.onset, effect.onset_unit)))
    symptoms = [t(f"condition.{c}") for c in effect.conditions]
    if effect.exhaustion:
        symptoms.insert(0, t.plural("affliction.exhaustion", effect.exhaustion))
    if symptoms:
        out.append(t("affliction.symptoms", what=", ".join(symptoms)))
    if effect.course and effect.dc is not None:
        out.append(course_words(page, effect.course, effect.ability, effect.dc, effect.rule))
    if not out:
        out.append(t("affliction.by_hand"))
    return out


def course_words(page: Page, course: str, ability: str, dc: int, rule: str) -> str:
    t = page.t
    when = t(f"affliction.course.{course}", save=_save(page, ability, dc))
    return f"{when} · {t(f'affliction.rule.{rule}')}" if rule else when


def afflict_result(page: Page, name: str, outcomes: Sequence[Outcome]) -> Element:
    """What it did, one line a character."""
    t = page.t
    return div(class_="trap-result notice")[
        b[t("affliction.done", name=name)],
        ul[
            [
                li[
                    span[o.name],
                    ": ",
                    span[
                        t(
                            "affliction.saved_roll" if not o.caught else "affliction.failed_roll",
                            roll=o.roll,
                            dc=o.dc,
                        )
                    ]
                    if o.roll is not None
                    else None,
                    " · " + t("trap.result_damage", amount=o.damage, type=_type(page, "poison"))
                    if o.damage
                    else "",
                    " · " + t("affliction.carries") if o.lasting else "",
                    " · " + ", ".join(t(f"condition.{c}") for c in o.conditions)
                    if o.conditions
                    else "",
                ]
                for o in outcomes
            ]
        ],
    ]


# -- on the sheet ---------------------------------------------------------------------


def sheet_afflictions(page: Page, pc: PlayerCharacter, clock: int | None) -> Node:
    """What the character carries, for the Track view: the gamemaster sees what has not
    shown yet, ends any of it and puts a curse on; a player reads what has shown."""
    t = page.t
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    shown = [x for x in pc.state.afflictions if gamemaster or x.showing]
    if not shown and not gamemaster:
        return None
    return section(class_="cs-block cs-afflictions", data_m="track")[
        h3(class_="eyebrow")[t("affliction.heading")],
        ul(class_="cs-aff-list")[[_row(page, pc, x, clock, gamemaster) for x in shown]]
        if shown
        else None,
        _curse_form(page, pc) if gamemaster else None,
    ]


def _row(
    page: Page, pc: PlayerCharacter, x: Affliction, clock: int | None, gamemaster: bool
) -> Element:
    t = page.t
    name_part: Node = span[x.name]
    if x.ref:
        title, _, source = x.ref.partition("|")
        category = "disease" if x.kind == "disease" else "item"
        shown = t.term(category, title) or x.name
        # A player reads its name; the books' page is the gamemaster's (PLAYER-VIEW.md).
        name_part = (
            a(
                href=ref_url(category, title, source, pc.world_id),
                class_="ref condition",
                data_ref=f"{category}:{source}",
            )[shown]
            if gamemaster
            else span[shown]
        )
    lines: list[str] = []
    if x.onset is not None:
        lines.append(t("affliction.incubating", when=clock_label(page, x.onset, pc.world_id)))
    if x.ends is not None:
        lines.append(t("affliction.until", when=clock_label(page, x.ends, pc.world_id)))
    if x.course and x.dc is not None:
        lines.append(course_words(page, x.course, x.ability, x.dc, x.rule))
        if x.successes or x.failures:
            lines.append(t("affliction.tally", ok=x.successes, failed=x.failures))
    if x.exhaustion:
        lines.append(t.plural("affliction.holds", x.exhaustion))
    if x.conditions:
        lines.append(", ".join(t(f"condition.{c}") for c in x.conditions))
    return li(class_=f"cs-aff {'is-hidden' if not x.showing else ''}".strip())[
        div(class_="cs-aff-head")[
            span(class_="pill neutral")[t(f"affliction.kind.{x.kind}")],
            b[name_part],
            form(
                method="post",
                action=f"{AFFLICTIONS_URL}/{pc.id}/end",
                hx_post=f"{AFFLICTIONS_URL}/{pc.id}/end",
                hx_swap="none",
                class_="inline-form",
            )[
                hidden("affliction", x.id),
                button(
                    type="submit", class_="btn btn-sm btn-ghost", title=t("affliction.end_hint")
                )[t("affliction.end")],
            ]
            if gamemaster
            else None,
        ],
        [small(class_="muted cs-aff-line")[line] for line in lines],
        p(class_="cs-aff-note")[x.note] if x.note else None,
    ]


def _curse_form(page: Page, pc: PlayerCharacter) -> Element:
    t = page.t
    url = f"{AFFLICTIONS_URL}/{pc.id}/curse"
    return details(class_="cs-aff-curse")[
        summary(class_="btn btn-sm btn-ghost")[icon("plus", "sm"), span[t("affliction.curse")]],
        form(method="post", action=url, hx_post=url, hx_swap="none", class_="stack-sm")[
            input_el(
                type="text",
                name="name",
                required=True,
                maxlength="200",
                placeholder=t("affliction.curse_name"),
                aria_label=t("affliction.curse_name"),
            ),
            textarea(
                name="note",
                rows="3",
                maxlength="2000",
                placeholder=t("affliction.curse_note"),
                aria_label=t("affliction.curse_note"),
            ),
            button(type="submit", class_="btn btn-sm btn-primary")[t("affliction.curse_put")],
        ],
    ]


def party_chips(page: Page, pc: PlayerCharacter) -> Node:
    """The afflictions on a party card, for the gamemaster: a chip each."""
    if not pc.state.afflictions:
        return None
    return [
        span(
            class_=f"pill {'cond' if x.showing else 'neutral'}",
            title=page.t(f"affliction.kind.{x.kind}"),
        )[x.name]
        for x in pc.state.afflictions
    ]


# -- words ----------------------------------------------------------------------------


def _save(page: Page, ability: str, dc: int) -> str:
    return page.t("trap.save", ability=page.t(f"ability.{ability}"), dc=dc)


def _span(page: Page, amount: str, unit: int) -> str:
    """ "1d4 days", "24 hours" in the reader's words."""
    key = "day" if unit == DAY else "hour" if unit == 60 else "minute"
    shown = amount.replace("d", page.t("rules.dice_letter"))
    if amount.isdigit():
        return page.t.plural(f"affliction.unit.{key}", int(amount))
    return page.t(f"affliction.unit.{key}_dice", dice=shown)


def _damage(page: Page, dice: str, damage_type: str) -> str:
    shown = dice.replace("d", page.t("rules.dice_letter"))
    return f"{shown} {_type(page, damage_type)}".strip()


def _type(page: Page, damage_type: str) -> str:
    if not damage_type:
        return ""
    key = f"rules.damage.{damage_type}"
    word = page.t(key)
    return damage_type if word == key else word


__all__ = [
    "AFFLICTIONS_URL",
    "afflict_button",
    "afflict_form",
    "afflict_result",
    "afflicts",
    "course_words",
    "effect_lines",
    "party_chips",
    "sheet_afflictions",
]
