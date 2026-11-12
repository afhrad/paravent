"""The gamemaster's recap of a fight or a session (app/services/recap.py)."""

from __future__ import annotations

from htpy import (
    Element,
    Node,
    a,
    div,
    h2,
    li,
    p,
    section,
    span,
    table,
    tbody,
    td,
    textarea,
    th,
    thead,
    tr,
    ul,
)

from app.services.recap import Recap
from app.services.tokens import Token
from app.ui.context import Page
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import monster_label
from app.ui.tokens import token


def _name(page: Page, label: str) -> str:
    """A combatant's label with its creature's name localized ("Owlbear 2")."""
    base = label.rstrip("0123456789 ").strip()
    return monster_label(page, label, base) if base else label


def recap_text(page: Page, recap: Recap) -> str:
    """The recap as plain lines, for the gamemaster's session notes."""
    t = page.t
    lines: list[str] = []
    for fight in recap.fights:
        defeated = ", ".join(_name(page, d) for d in fight.defeated) or "—"
        lines.append(
            t(
                "recap.text.fight",
                name=fight.name,
                rounds=t.plural("recap.rounds", fight.rounds),
                defeated=defeated,
                xp=fight.xp,
            )
        )
    if recap.biggest is not None:
        hit = recap.biggest
        lines.append(
            t(
                "recap.text.biggest",
                attacker=_name(page, hit.attacker),
                what=hit.what,
                target=_name(page, hit.target),
                amount=hit.amount,
            )
        )
    crits = [f"{_name(page, f.name)} ({f.crits})" for f in recap.fighters if f.crits]
    fumbles = [f"{_name(page, f.name)} ({f.fumbles})" for f in recap.fighters if f.fumbles]
    if crits:
        lines.append(t("recap.text.crits", names=", ".join(crits)))
    if fumbles:
        lines.append(t("recap.text.fumbles", names=", ".join(fumbles)))
    down = [_name(page, f.name) for f in recap.fighters if f.party and f.down]
    if down:
        lines.append(t("recap.text.down", names=", ".join(down)))
    if recap.conditions:
        parts = [
            f"{_name(page, who)}: {t(f'condition.{what}')}" + (f" ({source})" if source else "")
            for who, what, source in recap.conditions
        ]
        lines.append(t("recap.text.conditions", list="; ".join(parts)))
    return "\n".join(lines)


def recap_page(
    page: Page, title: str, recap: Recap, back: tuple[str, str], session: str | None = None
) -> Element:
    """The recap: the fights, who did what, the moments worth retelling, and the text."""
    t = page.t
    fights = section(class_="panel recap-fights")[
        div(class_="panel-head")[h2[t("recap.fights")]],
        ul(class_="cb-defeated")[
            [
                li[
                    a(href=f"/gm/combat/{f.combat_id}")[f.name],
                    span(class_="muted")[f" · {t('combat.round_n', n=f.rounds)}"],
                    span(class_="mono muted")[f" · {t('combat.xp_n', xp=f.xp)}"],
                ]
                for f in recap.fights
            ]
        ],
    ]
    rows = [
        tr(class_="recap-party" if f.party else None)[
            th(scope="row")[
                span(class_="recap-who")[
                    token(f.token or Token("character" if f.party else "creature"), "sm"),
                    _name(page, f.name),
                    span(class_="recap-down")[" ✝"] if f.down else None,
                ]
            ],
            td(class_="num")[str(f.dealt or "—")],
            td(class_="num")[str(f.taken or "—")],
            td(class_="num")[str(f.healed or "—")],
            td(class_="num")[
                span(class_="recap-crit")[str(f.crits)] if f.crits else None,
                " / " if f.crits and f.fumbles else None,
                span(class_="recap-fumble")[str(f.fumbles)] if f.fumbles else None,
            ],
        ]
        for f in recap.fighters
    ]
    fighters = section(class_="panel")[
        div(class_="panel-head")[h2[t("recap.fighters")]],
        div(class_="recap-scroll")[
            table(class_="score-tbl recap-table")[
                thead[
                    tr[
                        th[t("recap.who")],
                        th(class_="num")[t("recap.dealt")],
                        th(class_="num")[t("recap.taken")],
                        th(class_="num")[t("recap.healed")],
                        th(class_="num", title=t("recap.crits_title"))[t("recap.crits")],
                    ]
                ],
                tbody[rows],
            ]
        ],
    ]
    moments: Node = None
    if recap.biggest is not None or recap.conditions:
        hit = recap.biggest
        moments = section(class_="panel")[
            div(class_="panel-head")[h2[t("recap.moments")]],
            p[
                icon("swords", "sm"),
                " ",
                t(
                    "recap.text.biggest",
                    attacker=_name(page, hit.attacker),
                    what=hit.what,
                    target=_name(page, hit.target),
                    amount=hit.amount,
                ),
            ]
            if hit is not None
            else None,
            ul[
                [
                    li[
                        _name(page, who),
                        ": ",
                        t(f"condition.{what}"),
                        span(class_="muted")[f" ({source})"] if source else None,
                    ]
                    for who, what, source in recap.conditions
                ]
            ]
            if recap.conditions
            else None,
        ]
    text = recap_text(page, recap)
    notes = section(class_="panel")[
        div(class_="panel-head")[h2[t("recap.notes")]],
        p(class_="field-hint")[t("recap.notes_hint")],
        textarea(class_="recap-text", readonly=True, rows=str(max(4, text.count("\n") + 2)))[text],
    ]
    return app_shell(
        page,
        title,
        div(class_="cb-list-page recap-page")[
            a(href=back[0], class_="backlink")[icon("arrow-left"), span[back[1]]],
            page_head(t("recap.eyebrow"), title, t("recap.lede")),
            a(href=session, class_="btn btn-sm")[
                icon("scroll", "sm"), span[t("recap.whole_session")]
            ]
            if session
            else None,
            fights,
            fighters,
            moments,
            notes,
        ],
    )


__all__ = ["recap_page", "recap_text"]
