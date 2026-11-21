"""The session page (app/services/play_session.py): what the gamemaster should know as a
day of play begins, starting and ending it, and what happened since it began, which
ending it writes into the day's session note."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from htpy import Element, Node, a, article, b, button, div, form, h2, li, p, section, span, ul

from app.models.gamestate import World
from app.services.play_session import Brief, Happened
from app.ui.clock_pages import clock_label, remaining, timer_label
from app.ui.context import Page
from app.ui.faction_pages import factions_url
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.markdown import Rendered
from app.ui.names import ITEM, name_text
from app.ui.quest_pages import quests_url
from app.ui.session_pages import day_text, note_url, sessions_url
from app.ui.shop_pages import coin_text
from app.ui.world_menu import LINKS_URL

SESSION_URL = "/gm/session"


def session_url(world_id: str) -> str:
    """A world's session page."""
    return f"{SESSION_URL}?{urlencode({'world': world_id})}"



def _received_name(page: Page, ref: str) -> str:
    """An item received, by name; one handed out unidentified as such."""
    if ref == "?":
        return page.t("protocol.unidentified_item")
    return str(name_text(page, ITEM, ref.partition("|")[0]))

def happened_lines(page: Page, world_id: str, happened: Happened) -> list[str]:
    """What happened beyond the fights, one Markdown list line each, in the reader's
    language: the session note's second part."""
    t = page.t
    lines: list[str] = []
    if happened.minute_from is not None and happened.minute_to != happened.minute_from:
        lines.append(
            t(
                "session.happened.time",
                start=clock_label(page, happened.minute_from, world_id),
                end=clock_label(page, happened.minute_to, world_id),
            )
        )
    if happened.miles:
        lines.append(t("session.happened.miles", distance=page.u.miles(round(happened.miles, 1))))
    if happened.travel_encounters:
        lines.append(t.plural("session.happened.encounters", happened.travel_encounters))
    for outcome in happened.chases:
        lines.append(t("session.happened.chase", outcome=t(f"chase.over.{outcome}")))
    for outcome, name in happened.quests:
        lines.append(t(f"session.happened.quest_{outcome}", name=name))
    for name in happened.traps:
        lines.append(t("session.happened.trap", name=name))
    for kind, count in sorted(happened.contacts.items()):
        lines.append(t("session.happened.contacts", count=count, kind=t(f"contacts.kind.{kind}")))
    if happened.paid or happened.owed:
        lines.append(
            t(
                "session.happened.lifestyle",
                paid=coin_text(page, happened.paid),
                owed=coin_text(page, happened.owed),
            )
        )
    for name, xp in happened.xp.items():
        lines.append(t("session.happened.xp", name=name, xp=xp))
    for name in happened.levels:
        lines.append(t("session.happened.level", name=name))
    for name, (items, copper) in happened.received.items():
        what = [
            f"{count} × {_received_name(page, ref)}" if count > 1 else _received_name(page, ref)
            for ref, count in items
        ]
        if copper:
            what.append(coin_text(page, copper))
        lines.append(t("session.happened.received", name=name, what=", ".join(what)))
    for name in happened.bastion_turns:
        lines.append(t("session.happened.bastion", name=name))
    if happened.downtime:
        lines.append(t.plural("session.happened.downtime", happened.downtime))
    return [f"- {line}" for line in lines]


def session_page(
    page: Page,
    world: World,
    brief: Brief,
    last_note: Rendered | None,
    happened: Happened | None,
) -> Element:
    """The briefing, the session's start or end, and what has happened so far."""
    t = page.t
    base = f"{SESSION_URL}/{quote(world.id)}"
    running = brief.session
    party = f"/gm?{urlencode({'world': world.id})}"
    head = page_head(
        t("session.eyebrow"),
        world.name,
        t("session.lede_running", when=clock_label(page, running.minute, world.id))
        if running is not None
        else t("session.lede"),
        div(class_="party-tools")[
            form(method="post", action=f"{base}/{'end' if running else 'start'}")[
                button(
                    type="submit",
                    class_="btn btn-sm btn-primary",
                    title=t("session.end_hint") if running else None,
                )[
                    icon("circle-check" if running else "play", "sm"),
                    span[t("session.end" if running else "session.start")],
                ]
            ],
            a(href=party, class_="btn btn-sm")[icon("users-round", "sm"), span[t("session.party")]],
            a(href=quests_url(world.id), class_="btn btn-sm")[
                icon("scroll", "sm"), span[t("quests.eyebrow")]
            ],
            a(href=factions_url(world.id), class_="btn btn-sm")[
                icon("shield", "sm"), span[t("factions.eyebrow")]
            ],
            a(
                href=f"/help/between?back={quote(session_url(world.id))}",
                class_="btn btn-sm btn-ghost",
            )[icon("book-open", "sm"), span[t("help.between")]],
            a(href=sessions_url(world.id), class_="btn btn-sm btn-ghost")[
                icon("pencil", "sm"), span[t("session.notes")]
            ],
        ],
    )
    return app_shell(
        page,
        t("session.page_title", world=world.name),
        [
            head,
            div(class_="session-grid")[
                _so_far(page, world.id, happened) if happened is not None else None,
                _now(page, world.id, brief, party),
                _settle(page, brief),
                _last(page, brief, last_note),
            ],
        ],
        main_class="shell-main shell-wide",
        world_id=world.id,
    )


def _panel(heading: str, ic: str, *content: Node) -> Element:
    return section(class_="panel session-panel")[
        h2(class_="session-head")[icon(ic, "sm"), span[heading]], content
    ]


def _so_far(page: Page, world_id: str, happened: Happened) -> Element:
    lines = happened_lines(page, world_id, happened)
    return _panel(
        page.t("session.so_far"),
        "scroll",
        ul(class_="session-list")[[li[line[2:]] for line in lines]]
        if lines
        else p(class_="muted")[page.t("session.nothing_yet")],
        p(class_="muted")[page.t("session.so_far_hint")],
    )


def _now(page: Page, world_id: str, brief: Brief, party: str) -> Element:
    t = page.t
    still: list[Node] = []
    if brief.chase:
        still.append(li[a(href=party)[t("session.open_chase")]])
    if brief.shop:
        still.append(li[a(href=party)[t("session.open_shop", name=brief.shop)]])
    if brief.quests:
        still.append(
            li[a(href=quests_url(world_id))[t.plural("session.open_quests", brief.quests)]]
        )
    if brief.calls:
        still.append(li[a(href=party)[t.plural("session.open_calls", brief.calls)]])
    if brief.broken_links:
        still.append(li[a(href=LINKS_URL)[t.plural("session.broken_links", brief.broken_links)]])
    return _panel(
        t("session.now"),
        "clock",
        p[b[clock_label(page, brief.minute, world_id)]],
        ul(class_="session-list")[
            [
                li[
                    span[timer_label(page, timer)],
                    " · ",
                    span(class_="muted")[remaining(page, max(0, timer.ends - brief.minute))],
                ]
                for timer in brief.timers
            ]
        ]
        if brief.timers
        else p(class_="muted")[t("session.no_timers")],
        ul(class_="session-list")[still] if still else None,
    )


def _settle(page: Page, brief: Brief) -> Element:
    t = page.t
    rows: list[Node] = []
    for name in brief.bastions:
        rows.append(li[icon("vault", "sm"), span[t("session.bastion_due", name=name)]])
    for name, copper in brief.owed:
        rows.append(
            li[
                icon("coins", "sm"),
                span[t("session.owed", name=name, amount=coin_text(page, copper))],
            ]
        )
    for cid, name, count in brief.unnamed:
        rows.append(
            li[
                icon("users", "sm"),
                a(href=f"/characters/{quote(cid)}/downtime")[
                    t.plural("session.unnamed", count, name=name)
                ],
            ]
        )
    for cid, name, days in brief.downtime:
        rows.append(
            li[
                icon("hourglass", "sm"),
                a(href=f"/characters/{quote(cid)}/downtime")[
                    t.plural("session.downtime", days, name=name)
                ],
            ]
        )
    return _panel(
        t("session.settle"),
        "clipboard-list",
        ul(class_="session-list")[rows] if rows else p(class_="muted")[t("session.all_settled")],
    )


def _last(page: Page, brief: Brief, rendered: Rendered | None) -> Element:
    t = page.t
    note = brief.last_note
    if note is None:
        return _panel(t("session.last"), "book-open", p(class_="muted")[t("session.no_note")])
    return _panel(
        t("session.last_on", day=day_text(page, note.day)),
        "book-open",
        article(class_="prose session-note")[rendered.html if rendered is not None else None],
        div(class_="party-tools")[
            a(href=f"{note_url(note.id)}/edit", class_="btn btn-sm btn-ghost")[
                icon("pencil", "sm"), span[t("session.edit_note")]
            ],
            form(method="post", action=f"{note_url(note.id)}/share", class_="inline-form")[
                button(type="submit", class_="btn btn-sm", title=t("sessions.share_hint"))[
                    icon("send", "sm"), span[t("sessions.share")]
                ]
            ],
        ],
    )


__all__ = ["SESSION_URL", "happened_lines", "session_page", "session_url"]
