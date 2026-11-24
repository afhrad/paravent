"""A sentient item's mind and an artifact's further properties (app/services/sentient.py):
the gamemaster's page for one carried item, and what the sheet shows of them."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    div,
    form,
    h2,
    label,
    li,
    option,
    p,
    section,
    select,
    span,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.pc import ArtifactProperty, ItemMind, PlayerCharacter
from app.services.sentient import KINDS, LIMITS, PARTS, SCORES, Guide, conflict_dc, room
from app.services.sheet import ItemView
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

# The hidden fields naming the item, for a form doing ``op``.
Keep = Callable[[str], list[Node]]
# The abilities' short keys, for their labels.
_ABBR = {"intelligence": "int", "wisdom": "wis", "charisma": "cha"}


def mind_url(character_id: str, index: int | None = None, ref: str = "") -> str:
    """The page for a carried item's mind; with ``index`` and ``ref``, that item's."""
    url = f"/characters/{character_id}/mind"
    if index is None:
        return url
    return f"{url}?{urlencode({'index': index, 'ref': ref})}"


def mind_page(
    page: Page,
    pc: PlayerCharacter,
    item: ItemView,
    name: str,
    guide: Guide,
    *,
    result: Mapping[str, str] | None = None,
    error: str | None = None,
) -> Element:
    """The item's mind (rolled, rerolled part by part, or written), the conflict with its
    bearer, and its artifact properties."""
    t = page.t
    ref = item.true_name or item.name
    url = mind_url(pc.id)

    def keep(op: str) -> list[Node]:
        return [hidden("op", op), hidden("index", str(item.index)), hidden("ref", ref)]

    hints = [
        p(class_="notice")[t("sentient.book_sentient")] if item.sentient_book else None,
        p(class_="notice")[t("sentient.book_artifact")] if item.artifact_book else None,
        p(class_="field-hint")[t("sentient.no_guide")] if not guide.source else None,
    ]
    return app_shell(
        page,
        name,
        div(class_="tr-page sentient-page")[
            a(href=f"/characters/{pc.id}?tab=gear", class_="backlink")[
                icon("arrow-left"), span[pc.name or t("play.unnamed")]
            ],
            page_head(t("sentient.eyebrow"), name, t("sentient.lede")),
            alert(error),
            notice(_result(page, pc, result)) if result else None,
            hints,
            section(class_="panel sentient-mind")[
                h2(class_="eyebrow")[t("sentient.mind")],
                _mind_form(page, item.mind, guide, keep, url)
                if item.mind
                else form(method="post", action=url, class_="inline-form")[
                    keep("awaken"),
                    p(class_="field-hint")[t("sentient.awaken_hint")],
                    button(type="submit", class_="btn btn-primary")[
                        icon("dices", "sm"), span[t("sentient.awaken")]
                    ],
                ],
            ],
            _conflict(page, item.mind, guide, keep, url) if item.mind else None,
            _artifact(page, item, guide, keep, url),
        ],
        world_id=pc.world_id,
        player_bar=False,
    )


def _reroll(page: Page, part: str) -> Element:
    return button(
        type="submit",
        name="reroll",
        value=part,
        class_="btn btn-sm btn-ghost",
        title=page.t("sentient.reroll"),
        aria_label=page.t("sentient.reroll"),
    )[icon("dices", "sm")]


def _mind_form(page: Page, mind: ItemMind, guide: Guide, keep: Keep, url: str) -> Element:
    t = page.t
    scores = div(class_="sentient-scores")[
        [
            label(class_="field")[
                span[t(f"ability.{_ABBR[name]}")],
                span(class_="sentient-row")[
                    input_el(
                        type="number",
                        name=name,
                        value=str(getattr(mind, name)),
                        min="1",
                        max="30",
                        class_="mono",
                    ),
                    _reroll(page, name),
                ],
            ]
            for name in SCORES
        ]
    ]
    parts = [
        label(class_="field")[
            span[t(f"sentient.part.{part}")],
            span(class_="sentient-row")[
                input_el(type="text", name=part, value=mind.alignment, maxlength="200")
                if part == "alignment"
                else textarea(name=part, rows="2", maxlength="1000")[getattr(mind, part)],
                _reroll(page, part) if guide.has(part) else None,
            ],
        ]
        for part in PARTS
    ]
    return div[
        form(method="post", action=url, class_="stack-form")[
            keep("save"),
            scores,
            parts,
            label(class_="field")[
                span[t("sentient.languages")],
                input_el(type="text", name="languages", value=mind.languages, maxlength="200"),
            ],
            label(class_="field")[
                span[t("sentient.notes")],
                textarea(name="notes", rows="3", maxlength="2000")[mind.notes],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[t("sentient.save")]
            ],
        ],
        form(method="post", action=url, class_="inline-form")[
            keep("forget"),
            button(type="submit", class_="btn btn-sm btn-ghost")[
                icon("trash-2", "sm"), span[t("sentient.forget")]
            ],
        ],
    ]


def _conflict(page: Page, mind: ItemMind, guide: Guide, keep: Keep, url: str) -> Element:
    t = page.t
    dc = conflict_dc(mind)
    return section(class_="panel sentient-conflict")[
        h2(class_="eyebrow")[t("sentient.conflict")],
        p(class_="field-hint")[
            t("sentient.conflict_contested" if guide.contested else "sentient.conflict_save", dc=dc)
        ],
        p(class_="field-hint")[t("sentient.demands")],
        div(class_="form-actions")[
            form(method="post", action=url, class_="inline-form")[
                keep("conflict"),
                button(type="submit", class_="btn")[
                    icon("swords", "sm"), span[t("sentient.conflict_roll")]
                ],
            ],
            form(method="post", action=url, class_="inline-form")[
                keep("control"),
                button(type="submit", class_="btn")[
                    icon("brain", "sm"), span[t("sentient.control", dc=dc)]
                ],
            ],
        ],
        p(class_="field-hint")[t("sentient.control_hint")],
    ]


def _artifact(page: Page, item: ItemView, guide: Guide, keep: Keep, url: str) -> Element:
    t = page.t
    held = list(item.artifact)
    rows = [
        li(class_=f"sentient-property {x.kind}")[
            span(class_="pill neutral")[t(f"sentient.kind.{x.kind}")],
            span[x.text],
            form(method="post", action=url, class_="inline-form")[
                keep("drop_property"),
                hidden("n", str(n)),
                button(
                    type="submit",
                    class_="btn btn-sm btn-ghost",
                    title=t("sentient.drop_property"),
                    aria_label=t("sentient.drop_property"),
                )[icon("x", "sm")],
            ],
        ]
        for n, x in enumerate(held)
    ]
    rolls = [
        form(method="post", action=url, class_="inline-form")[
            keep("roll_property"),
            hidden("kind", kind),
            button(
                type="submit",
                class_="btn btn-sm",
                disabled=not (room(held, kind) and guide.has(kind)),
            )[
                icon("dices", "sm"),
                span[
                    t(
                        "sentient.roll_property",
                        kind=t(f"sentient.kind.{kind}"),
                        n=sum(1 for x in held if x.kind == kind),
                        most=LIMITS[kind],
                    )
                ],
            ],
        ]
        for kind in KINDS
    ]
    return section(class_="panel sentient-artifact")[
        h2(class_="eyebrow")[t("sentient.artifact")],
        p(class_="field-hint")[t("sentient.artifact_hint")],
        ul(class_="sentient-properties")[rows] if rows else p(class_="muted")[t("sentient.none")],
        div(class_="sentient-rolls")[rolls],
        form(method="post", action=url, class_="stack-form")[
            keep("add_property"),
            div(class_="tool-grid")[
                label(class_="field")[
                    span[t("sentient.kind")],
                    select(name="kind")[[option(value=k)[t(f"sentient.kind.{k}")] for k in KINDS]],
                ],
                label(class_="field")[
                    span[t("sentient.own_property")],
                    input_el(type="text", name="text", maxlength="2000"),
                ],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-sm")[
                    icon("plus", "sm"), span[t("sentient.add_property")]
                ]
            ],
        ],
    ]


def _result(page: Page, pc: PlayerCharacter, result: Mapping[str, str]) -> str:
    """What the last conflict or attempt at control came to."""
    t = page.t
    who = pc.name or t("play.unnamed")
    won = result.get("won") == "1"
    if result.get("what") == "conflict":
        if result.get("item"):
            key = "sentient.result.contest_won" if won else "sentient.result.contest_lost"
            return t(key, item=result.get("item", ""), bearer=result.get("bearer", ""), name=who)
        key = "sentient.result.save_won" if won else "sentient.result.save_lost"
        return t(key, bearer=result.get("bearer", ""), dc=result.get("dc", ""), name=who)
    key = "sentient.result.charmed" if won else "sentient.result.resisted"
    return t(key, bearer=result.get("bearer", ""), dc=result.get("dc", ""), name=who)


def sheet_mind(page: Page, item: ItemView, gm: bool) -> Node:
    """Under a carried item on the sheet: that it is sentient (how it speaks to its
    bearer), and an artifact's further properties; nothing of an unidentified item for
    its player."""
    if item.unidentified and not gm:
        return None
    t = page.t
    shown: list[ArtifactProperty] = list(item.artifact)
    if item.mind is None and not shown:
        return None
    return div(class_="cs-item-mind")[
        p[b[t("sentient.tag")], " · ", item.mind.communication]
        if item.mind is not None and item.mind.communication
        else None,
        ul(class_="cs-item-props")[
            [
                li(class_=x.kind)[span(class_="muted")[t(f"sentient.kind.{x.kind}"), ": "], x.text]
                for x in shown
            ]
        ]
        if shown
        else None,
    ]


__all__ = ["mind_page", "mind_url", "sheet_mind"]
