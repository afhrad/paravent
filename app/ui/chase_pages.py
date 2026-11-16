"""The chase panel on the party view (app/services/chase.py): start a chase with the
world's characters and creatures, then run it turn by turn — Dash, end a turn with its
complication, the escape checks at the end of the round — and end it."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote, urlencode

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
    ol,
    option,
    p,
    select,
    small,
    span,
    summary,
    textarea,
)
from htpy import input as input_el

from app.models.gamestate import Chase, ChaseRunner
from app.services.chase import ENVIRONMENTS
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon

CHASE_URL = "/gm/chase"


def chase_panel(
    page: Page,
    world_id: str,
    chase: Chase | None,
    members: Sequence[tuple[str, str]],
) -> Element:
    """The world's chase: the form that starts one (``members`` are the world's
    characters, ``(id, name)``), or the running chase."""
    t = page.t
    base = f"{CHASE_URL}/{quote(world_id)}"
    htmx = {"hx_target": "#party-chase", "hx_swap": "outerHTML"}
    head = div(class_="party-clock-head")[
        icon("footprints"),
        b[t("chase.heading")],
        span(class_="pill accent")[t("chase.round", round=chase.round)]
        if chase is not None and not chase.over
        else None,
        span(class_="pill")[t(f"chase.over.{chase.over}")] if chase and chase.over else None,
        _post(
            base,
            "end",
            htmx,
            button(type="submit", class_="btn btn-sm")[icon("x", "sm"), span[t("chase.end")]],
        )
        if chase is not None
        else None,
    ]
    return div(
        id="party-chase",
        class_="panel party-chase",
        data_url=f"{CHASE_URL}/panel?{urlencode({'world': world_id})}",
    )[
        head,
        _running(page, base, htmx, chase)
        if chase is not None
        else _start(page, base, htmx, members),
    ]


def _post(
    base: str, op: str, htmx: dict[str, str], *content: Node, cls: str = "inline-form"
) -> Element:
    return form(method="post", action=f"{base}/{op}", hx_post=f"{base}/{op}", class_=cls, **htmx)[
        content
    ]


def _start(
    page: Page, base: str, htmx: dict[str, str], members: Sequence[tuple[str, str]]
) -> Element:
    t = page.t
    return details(class_="party-clock-more")[
        summary[t("chase.start")],
        _post(
            base,
            "start",
            htmx,
            div(class_="chase-sides")[
                [
                    label(class_="party-clock-label")[
                        span[name],
                        select(name=f"side_{cid}")[
                            option(value="")[t("chase.side.none")],
                            option(value="quarry")[t("chase.side.quarry")],
                            option(value="pursuer")[t("chase.side.pursuer")],
                        ],
                    ]
                    for cid, name in members
                ]
            ],
            label(class_="field")[
                span[t("chase.quarry_creatures")],
                textarea(name="quarry", rows="2", placeholder=t("chase.creatures_placeholder")),
            ],
            label(class_="field")[
                span[t("chase.pursuer_creatures")],
                textarea(name="pursuer", rows="2", placeholder=t("chase.creatures_placeholder")),
            ],
            div(class_="chase-start-row")[
                label(class_="party-clock-label")[
                    span[t("chase.environment")],
                    select(name="environment")[
                        [option(value=e)[t(f"chase.env.{e}")] for e in ENVIRONMENTS]
                    ],
                ],
                label(class_="party-clock-label")[
                    span[t("chase.distance")],
                    input_el(
                        type="number",
                        name="distance",
                        min="0",
                        max="10000",
                        step="any",
                        value=_shown(page, 30),
                        inputmode="numeric",
                        class_="mono party-clock-amount",
                    ),
                    span[_unit(page)],
                ],
                button(type="submit", class_="btn btn-sm btn-primary")[
                    icon("footprints", "sm"), span[t("chase.begin")]
                ],
            ],
            small(class_="muted")[t("chase.start_hint")],
            cls="chase-form",
        ),
    ]


def _running(page: Page, base: str, htmx: dict[str, str], chase: Chase) -> Node:
    t = page.t
    rows = [_runner(page, base, htmx, chase, n, runner) for n, runner in enumerate(chase.runners)]
    done = chase.turn >= len(chase.runners)
    quarry = [(n, r) for n, r in enumerate(chase.runners) if r.side == "quarry" and _active(r)]
    return [
        div(class_="chase-distance")[
            _post(
                base,
                "distance",
                htmx,
                label(class_="party-clock-label")[
                    span[t("chase.distance")],
                    input_el(
                        type="number",
                        name="distance",
                        min="0",
                        max="10000",
                        step="any",
                        value=_shown(page, chase.distance),
                        inputmode="numeric",
                        class_="mono party-clock-amount",
                    ),
                    span[_unit(page)],
                ],
                button(type="submit", class_="btn btn-sm")[t("chase.set")],
            )
            if not chase.over
            else None,
            small(class_="muted")[t(f"chase.env.{chase.environment}")],
        ],
        ol(class_="chase-runners")[rows],
        _post(
            base,
            "turn",
            htmx,
            button(type="submit", class_="btn btn-sm")[
                icon("hourglass", "sm"), span[t("chase.end_turn")]
            ],
        )
        if not chase.over and not done
        else None,
        _post(
            base,
            "round",
            htmx,
            div(class_="chase-escape")[
                [
                    div(class_="chase-escape-row")[
                        span[r.name],
                        label(class_="party-clock-label")[
                            input_el(type="checkbox", name="sight", value=str(n)),
                            span[t("chase.in_sight")],
                        ],
                        select(name=f"adv_{n}", aria_label=t("chase.factor"))[
                            option(value="0")[t("chase.factor_none")],
                            option(value="1")[t("chase.factor_adv")],
                            option(value="-1")[t("chase.factor_dis")],
                        ],
                    ]
                    for n, r in quarry
                ]
            ],
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("eye-off", "sm"), span[t("chase.escape")]
            ],
            cls="chase-round",
        )
        if not chase.over
        else None,
        p(class_="muted")[t("chase.round_hint")] if not chase.over else None,
    ]


def _runner(
    page: Page, base: str, htmx: dict[str, str], chase: Chase, index: int, runner: ChaseRunner
) -> Element:
    t = page.t
    turn = index == chase.turn and not chase.over
    state = t("chase.escaped") if runner.escaped else t("chase.out") if runner.out else None
    left = max(0, runner.free_dashes - runner.dashes)
    return li(class_="chase-runner" + (" is-turn" if turn else "") + (" is-out" if state else ""))[
        span(class_="mono chase-init")[str(runner.initiative)],
        b[runner.name],
        span(class_=f"pill {'accent' if runner.side == 'quarry' else ''}")[
            t(f"chase.side.{runner.side}")
        ],
        small(class_="muted")[
            t("chase.dashes", left=left, used=runner.dashes)
            + (f" · {t('sheet.exhaustion')} {runner.exhaustion}" if runner.exhaustion else "")
            + (
                f" · {t('chase.passive', passive=runner.passive)}"
                if runner.side == "pursuer"
                else f" · {t('chase.stealth', stealth=f'{runner.stealth:+d}')}"
            )
        ],
        span(class_="pill")[state] if state else None,
        div(class_="chase-complication")[icon("triangle-alert", "sm"), span[runner.complication]]
        if runner.complication
        else None,
        div(class_="chase-acts")[
            _post(
                base,
                "dash",
                htmx,
                hidden("index", str(index)),
                button(type="submit", class_="btn btn-sm", title=t("chase.dash_hint"))[
                    t("chase.dash")
                ],
            )
            if not state and not chase.over
            else None,
            _post(
                base,
                "drop",
                htmx,
                hidden("index", str(index)),
                button(type="submit", class_="btn btn-sm btn-ghost")[
                    t("chase.back_in" if runner.out else "chase.drop")
                ],
            )
            if not runner.escaped and not chase.over
            else None,
        ],
    ]


def _unit(page: Page) -> str:
    return "m" if page.u.metric else "ft."


def _shown(page: Page, feet: int) -> str:
    """A distance in feet as the reader types it: feet, or metres (``9``)."""
    return page.u.distance(feet).rsplit(" ", 1)[0].replace(",", ".")


def _active(runner: ChaseRunner) -> bool:
    return not runner.out and not runner.escaped


__all__ = ["CHASE_URL", "chase_panel"]
