"""The party view's planes (app/services/planes.py): which plane the party is on, its
rules with where the guide prints them, who each spares, the gamemaster's calls, and what
the rules just did."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

from htpy import (
    Node,
    a,
    b,
    button,
    div,
    fieldset,
    form,
    label,
    legend,
    li,
    optgroup,
    option,
    p,
    select,
    span,
    ul,
)
from htpy import input as input_el

from app.services.planes import PLANES, RULES, Befell, PlaneRule
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.tool_dialog import tool_button, tool_dialog

PLANE_URL = "/gm/plane"
# The planes in the order the guides list them.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("near", ("feywild", "shadowfell", "astral", "ethereal")),
    (
        "outer",
        (
            "mount_celestia",
            "bytopia",
            "elysium",
            "beastlands",
            "arborea",
            "ysgard",
            "limbo",
            "pandemonium",
            "abyss",
            "carceri",
            "hades",
            "gehenna",
            "nine_hells",
            "acheron",
            "mechanus",
            "arcadia",
            "outlands",
        ),
    ),
)
assert {k for _, keys in GROUPS for k in keys} == set(PLANES)


def plane_dialog_url(world_id: str) -> str:
    """The party view with the dialog open again."""
    return f"/gm?world={quote(world_id)}&tool=plane"


def plane_name(page: Page, name: str) -> str:
    """An affliction's name: a plane's rule (stored by its key) in the reader's words."""
    return page.t(f"plane.rule.{name}") if name in RULES else name


def befell_text(page: Page, x: Befell) -> str:
    """One thing a rule did, in words."""
    t = page.t
    rule = t(f"plane.rule.{x.rule}")
    if not x.character_id:
        return t("plane.done.party", rule=rule, roll=x.table_roll or "", text=x.text)
    if x.roll is not None and not x.struck:
        return t("plane.done.saved", rule=rule, name=x.name, roll=x.roll, dc=x.dc or "")
    what = x.text or t(f"plane.effect.{x.rule}")
    if x.roll is None:
        return t("plane.done.struck_now", rule=rule, name=x.name, what=what)
    return t("plane.done.struck", rule=rule, name=x.name, roll=x.roll, dc=x.dc or "", what=what)


def plane_tool(
    page: Page,
    world_id: str | None,
    characters: Sequence[tuple[str, str]],
    *,
    plane: str,
    rules: Sequence[PlaneRule],
    spared: Mapping[str, Sequence[str]],
    links: Mapping[str, str],
    done: Sequence[Befell] = (),
) -> Node:
    """The button (naming the plane while the party is on one) and its dialog."""
    if world_id is None:
        return None
    t = page.t
    base = f"{PLANE_URL}/{quote(world_id)}"
    choose = form(method="post", action=f"{base}/enter", class_="tool-section")[
        label(class_="field")[
            span[t("plane.where")],
            select(name="plane")[
                option(value="", selected=not plane)[t("plane.name.")],
                [
                    optgroup(label=t(f"plane.group.{group}"))[
                        [option(value=k, selected=k == plane)[t(f"plane.name.{k}")] for k in keys]
                    ]
                    for group, keys in GROUPS
                ],
            ],
        ],
        p(class_="field-hint")[t("plane.where_hint")],
        div(class_="form-actions")[button(type="submit", class_="btn btn-sm")[t("plane.go")]],
    ]
    shown = [
        _rule(page, base, x, characters, spared.get(x.key, ()), links.get(x.key)) for x in rules
    ]
    body = [
        p(class_="field-hint")[t("plane.lede")],
        ul(class_="plane-done")[[li[befell_text(page, x)] for x in done]] if done else None,
        choose,
        div(class_="tool-section")[
            b[t("plane.rules", plane=t(f"plane.name.{plane}"))],
            ul(class_="plane-rules")[shown],
        ]
        if shown
        else p(class_="muted tool-section")[t("plane.no_rules")]
        if plane
        else None,
    ]
    title = t("plane.title_on", plane=t(f"plane.name.{plane}")) if plane else t("plane.title")
    return [
        tool_button(page, "plane", title, "orbit"),
        tool_dialog(page, "plane", t("plane.title"), body, is_open=bool(done)),
    ]


def _rule(
    page: Page,
    base: str,
    rule: PlaneRule,
    characters: Sequence[tuple[str, str]],
    spared: Sequence[str],
    link: str | None,
) -> Node:
    t = page.t
    name = t(f"plane.rule.{rule.key}")
    picks = (
        form(method="post", action=f"{base}/spare", class_="plane-picks")[
            hidden("rule", rule.key),
            fieldset(class_="xp-who")[
                legend[t("plane.affects")],
                [
                    label(class_="cs-heal-pick")[
                        input_el(type="checkbox", name="to", value=cid, checked=cid not in spared),
                        span[cname],
                    ]
                    for cid, cname in characters
                ],
            ],
            button(type="submit", class_="btn btn-sm btn-ghost")[t("plane.keep")],
        ]
        if rule.each and rule.when != "always" and characters
        else None
    )
    call = (
        form(method="post", action=f"{base}/call", class_="inline-form")[
            hidden("rule", rule.key),
            button(type="submit", class_="btn btn-sm")[
                icon("dices", "sm"), span[t(f"plane.call.{rule.key}")]
            ],
        ]
        if rule.when == "call"
        else None
    )
    return li(class_="plane-rule")[
        div(class_="plane-rule-head")[
            a(href=link, target="_blank", rel="noopener")[name] if link else b[name],
            span(class_="pill neutral")[t(f"plane.when.{rule.when}")],
        ],
        p(class_="field-hint")[t(f"plane.hint.{rule.key}")],
        call,
        picks,
    ]


__all__ = ["PLANE_URL", "befell_text", "plane_dialog_url", "plane_name", "plane_tool"]
