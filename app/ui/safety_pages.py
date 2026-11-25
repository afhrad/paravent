"""Safety tools (app/services/safety.py): session zero per world (the gamemaster's
half, each player's expectations sheet without a name, and the sheets gathered), the
table's lines and veils, the X-card, and anonymous suggestions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    fieldset,
    form,
    h2,
    h3,
    label,
    legend,
    li,
    option,
    p,
    section,
    select,
    span,
    summary,
    table,
    tbody,
    td,
    textarea,
    th,
    thead,
    tr,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import ExpectationSheet, TableSafety, World
from app.services.safety import TOGETHER, gather
from app.ui.context import Page
from app.ui.forms import hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

SAFETY_URL = "/safety"


@dataclass
class WorldZero:
    """What a world's session zero panel needs beyond its record: the reader's own
    sheet, the optional rules the world plays with, and where the guide prints its
    parts (``zero``, ``expectations``, ``limits`` and each way the party came together)."""

    mine: ExpectationSheet | None = None
    rules: list[str] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)


def xcard_button(page: Page, world_id: str | None) -> Element:
    """The X-card: one tap tells the gamemaster, and no one else, to change course."""
    t = page.t
    return form(
        method="post",
        action=f"{SAFETY_URL}/xcard",
        hx_post=f"{SAFETY_URL}/xcard",
        hx_swap="outerHTML",
        class_="xcard-form",
    )[
        hidden("world", world_id or ""),
        button(type="submit", class_="btn btn-sm xcard", title=t("safety.xcard_hint"))[
            icon("x", "sm"), span[t("safety.xcard")]
        ],
    ]


def xcard_sent(page: Page) -> Element:
    """What the X-card button becomes once tapped."""
    return span(class_="pill neutral xcard-sent", role="status")[page.t("safety.xcard_sent")]


def safety_page(
    page: Page,
    worlds: Sequence[tuple[World, TableSafety]],
    *,
    gamemaster: bool,
    message: str | None = None,
    zero: Mapping[str, WorldZero] | None = None,
) -> Element:
    """Each of the reader's worlds: its lines and veils (the gamemaster edits them and
    settles what was suggested), a form to suggest more without a name, and the X-card."""
    t = page.t
    return app_shell(
        page,
        t("safety.title"),
        div(class_="tr-page safety-page")[
            page_head(t("safety.eyebrow"), t("safety.title"), t("safety.lede")),
            notice(message),
            [
                _world(page, world, safety, gamemaster, (zero or {}).get(world.id) or WorldZero())
                for world, safety in worlds
            ]
            if worlds
            else p(class_="muted")[t("safety.no_world")],
        ],
    )


def _world(
    page: Page, world: World, safety: TableSafety, gamemaster: bool, zero: WorldZero
) -> Element:
    t = page.t
    url = f"{SAFETY_URL}/{world.id}"
    lists: Node
    if gamemaster:
        lists = form(method="post", action=url, class_="stack")[
            label(class_="field")[
                span[t("safety.lines")],
                small_hint(t("safety.lines_hint")),
                textarea(name="lines", rows="4")["\n".join(safety.lines)],
            ],
            label(class_="field")[
                span[t("safety.veils")],
                small_hint(t("safety.veils_hint")),
                textarea(name="veils", rows="4")["\n".join(safety.veils)],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-sm")[t("safety.save")]
            ],
        ]
    else:
        lists = div(class_="safety-lists")[
            _list(t("safety.lines"), t("safety.lines_hint"), safety.lines, t("safety.none")),
            _list(t("safety.veils"), t("safety.veils_hint"), safety.veils, t("safety.none")),
        ]
    return section(class_="panel safety-world")[
        div(class_="panel-head")[
            h2[world.name], xcard_button(page, world.id) if not gamemaster else None
        ],
        _session_zero(page, world, safety, gamemaster, zero),
        h3(class_="safety-sub")[t("safety.agreements")],
        lists,
        _suggestions(page, world, safety) if gamemaster else _suggest_form(page, world),
    ]


def small_hint(text: str) -> Element:
    return span(class_="field-hint")[text]


def _list(title: str, hint: str, items: Sequence[str], empty: str) -> Element:
    return div(class_="safety-list")[
        b[title],
        small_hint(hint),
        ul[[li[x] for x in items]] if items else p(class_="muted")[empty],
    ]


def _suggest_form(page: Page, world: World) -> Element:
    t = page.t
    return form(method="post", action=f"{SAFETY_URL}/{world.id}/suggest", class_="safety-suggest")[
        b[t("safety.suggest")],
        small_hint(t("safety.suggest_hint")),
        div(class_="safety-suggest-row")[
            select(name="kind", aria_label=t("safety.suggest_kind"))[
                option(value="line")[t("safety.line")], option(value="veil")[t("safety.veil")]
            ],
            input_el(
                type="text",
                name="text",
                maxlength="300",
                required=True,
                aria_label=t("safety.suggest"),
            ),
            button(type="submit", class_="btn btn-sm")[t("safety.suggest_send")],
        ],
    ]


def _suggestions(page: Page, world: World, safety: TableSafety) -> Node:
    t = page.t
    if not safety.suggested:
        return None
    return div(class_="safety-suggested")[
        b[t("safety.suggested")],
        ul[
            [
                li(class_="safety-suggested-row")[
                    span(class_="pill neutral")[t(f"safety.{s.kind}")],
                    span[s.text],
                    form(
                        method="post",
                        action=f"{SAFETY_URL}/{world.id}/settle",
                        class_="inline-form",
                    )[
                        hidden("index", str(n)),
                        button(type="submit", name="take", value="1", class_="btn btn-sm")[
                            t("safety.take")
                        ],
                        button(
                            type="submit", name="take", value="0", class_="btn btn-sm btn-ghost"
                        )[t("safety.drop")],
                    ],
                ]
                for n, s in enumerate(safety.suggested)
            ]
        ],
    ]


def _guide(link: str | None, text: str) -> Node:
    return a(href=link, target="_blank", rel="noopener")[text] if link else text


def _session_zero(
    page: Page, world: World, safety: TableSafety, gamemaster: bool, zero: WorldZero
) -> Element:
    """Session zero: the gamemaster's half (a form for the gamemaster, to read for the
    players), a player's own sheet, and every sheet gathered without a name."""
    t = page.t
    url = f"{SAFETY_URL}/{world.id}"
    body: list[Node] = [
        p(class_="field-hint")[
            t("zero.lede"), " ", _guide(zero.links.get("zero"), t("zero.in_guide"))
        ]
    ]
    body.append(
        _zero_form(page, url, safety, zero) if gamemaster else _zero_read(page, safety, zero)
    )
    if not gamemaster:
        body.append(_sheet_form(page, url, safety, zero))
    body.append(_gathered(page, url, safety, gamemaster))
    return details(class_="safety-zero", open=True)[
        summary[h3(class_="safety-sub")[icon("clipboard-list", "sm"), span[t("zero.title")]]],
        body,
    ]


def _rules_line(page: Page, rules: Sequence[str], *, inline: bool = False) -> Node:
    t = page.t
    if not rules:
        return None
    names = ", ".join(
        t("worlds.milestone") if r == "milestone" else t(f"worlds.rule.{r}") for r in rules
    )
    text = t("zero.rules_on", rules=names)
    return span(class_="field-hint")[text] if inline else p(class_="field-hint")[text]


def _zero_form(page: Page, url: str, safety: TableSafety, zero: WorldZero) -> Element:
    t = page.t

    def area(name: str, value: str, hint: str, rows: str = "3") -> Element:
        return label(class_="field")[
            span(class_="field-label")[t(f"zero.{name}")],
            small_hint(hint),
            textarea(name=name, rows=rows)[value],
        ]

    return form(method="post", action=f"{url}/zero", class_="safety-zero-form")[
        area("theme", safety.theme, t("zero.theme_hint")),
        label(class_="field")[
            span(class_="field-label")[t("zero.sensitive")],
            small_hint(t("zero.sensitive_hint")),
            textarea(name="sensitive", rows="3")["\n".join(safety.sensitive)],
        ],
        area("start", safety.start, t("zero.start_hint")),
        label(class_="field")[
            span(class_="field-label")[t("zero.house_rules")],
            small_hint(t("zero.house_rules_hint")),
            textarea(name="house_rules", rows="3")[safety.house_rules],
            _rules_line(page, zero.rules, inline=True),
        ],
        label(class_="field")[
            span(class_="field-label")[t("zero.together")],
            select(name="together")[
                option(value="", selected=not safety.together)[t("zero.together.")],
                [
                    option(value=k, selected=safety.together == k)[t(f"zero.together.{k}")]
                    for k in TOGETHER
                ],
            ],
            small_hint(t("zero.together_hint")),
        ],
        area("together_note", safety.together_note, t("zero.together_note_hint"), "2"),
        label(class_="field")[
            span(class_="field-label")[t("zero.starting_level")],
            input_el(
                type="number",
                name="starting_level",
                min="1",
                max="20",
                value=str(safety.starting_level) if safety.starting_level else "",
                inputmode="numeric",
            ),
        ],
        div(class_="form-actions")[button(type="submit", class_="btn btn-sm")[t("safety.save")]],
    ]


def _zero_read(page: Page, safety: TableSafety, zero: WorldZero) -> Node:
    t = page.t
    rows: list[Node] = []

    def row(title: str, *content: Node) -> None:
        rows.append(div(class_="safety-zero-part")[b[title], *content])

    if safety.theme:
        row(t("zero.theme"), p[safety.theme])
    if safety.sensitive:
        row(t("zero.sensitive"), ul[[li[x] for x in safety.sensitive]])
    if safety.start:
        row(t("zero.start"), p[safety.start])
    if safety.house_rules or zero.rules:
        row(
            t("zero.house_rules"),
            p[safety.house_rules] if safety.house_rules else None,
            _rules_line(page, zero.rules),
        )
    if safety.together or safety.together_note:
        row(
            t("zero.together"),
            p[_guide(zero.links.get(safety.together), t(f"zero.together.{safety.together}"))]
            if safety.together
            else None,
            p[safety.together_note] if safety.together_note else None,
        )
    if safety.starting_level:
        row(t("zero.starting_level"), p[str(safety.starting_level)])
    return div(class_="safety-zero-read")[rows] if rows else p(class_="muted")[t("zero.not_yet")]


def _sheet_form(page: Page, url: str, safety: TableSafety, zero: WorldZero) -> Element:
    t = page.t
    mine = zero.mine or ExpectationSheet()
    return form(method="post", action=f"{url}/sheet", class_="safety-sheet")[
        b[t("zero.sheet")],
        small_hint(t("zero.sheet_hint")),
        fieldset(class_="safety-limits")[
            legend[t("zero.limits"), " ", _guide(zero.links.get("limits"), t("zero.limits_guide"))],
            [
                div(class_="safety-limit-row")[
                    span[element],
                    [
                        label(class_="safety-limit-pick")[
                            input_el(
                                type="radio",
                                name=f"limit_{n}",
                                value=value,
                                checked=mine.limits.get(element, "") == value,
                            ),
                            span[t(f"zero.limit.{value or 'none'}")],
                        ]
                        for value in ("", "soft", "hard")
                    ],
                ]
                for n, element in enumerate(safety.sensitive)
            ]
            if safety.sensitive
            else p(class_="muted")[t("zero.no_elements")],
        ],
        label(class_="field")[
            span(class_="field-label")[t("zero.avoid")],
            textarea(name="avoid", rows="2")[mine.avoid],
        ],
        label(class_="field")[
            span(class_="field-label")[t("zero.hopes")],
            textarea(name="hopes", rows="3")[mine.hopes],
        ],
        label(class_="field")[
            span(class_="field-label")[t("zero.concerns")],
            textarea(name="concerns", rows="2")[mine.concerns],
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-sm")[
                t("zero.send" if zero.mine is None else "zero.change")
            ]
        ],
    ]


def _gathered(page: Page, url: str, safety: TableSafety, gamemaster: bool) -> Element:
    t = page.t
    found = gather(safety)
    adopted = set(safety.lines) | set(safety.veils)

    def adopt(element: str) -> Node:
        if not gamemaster or element in adopted:
            return None
        return form(method="post", action=f"{url}/adopt", class_="inline-form")[
            hidden("element", element),
            button(type="submit", name="kind", value="line", class_="btn btn-sm btn-ghost")[
                t("zero.as_line")
            ],
            button(type="submit", name="kind", value="veil", class_="btn btn-sm btn-ghost")[
                t("zero.as_veil")
            ],
        ]

    def listed(title: str, items: Sequence[str]) -> Node:
        return (
            div(class_="safety-zero-part")[b[title], ul[[li[x] for x in items]]] if items else None
        )

    return div(class_="safety-gathered")[
        b[t("zero.gathered")],
        small_hint(t.plural("zero.sheets", found.sheets)),
        table(class_="safety-limits-table")[
            thead[
                tr[th[t("zero.element")], th[t("zero.limit.hard")], th[t("zero.limit.soft")], th]
            ],
            tbody[
                [
                    tr[
                        td[element],
                        td(class_="mono")[str(hard)],
                        td(class_="mono")[str(soft)],
                        td[adopt(element) if hard or soft else None],
                    ]
                    for element, (hard, soft) in found.limits.items()
                ]
            ],
        ]
        if found.limits
        else None,
        listed(t("zero.avoid"), found.avoid),
        listed(t("zero.hopes"), found.hopes),
        listed(t("zero.concerns"), found.concerns),
    ]


__all__ = ["SAFETY_URL", "WorldZero", "safety_page", "xcard_button", "xcard_sent"]
