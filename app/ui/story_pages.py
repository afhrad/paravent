"""A character's story page (app/services/story.py): its quests, marks of prestige,
factions, contacts, followers and bastion, and the moments of the log that changed it.
The gamemaster grants and takes back marks here; a favor is called in by either."""

from __future__ import annotations

from collections.abc import Mapping

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    h2,
    label,
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Mark
from app.models.pc import PlayerCharacter
from app.services.experience import Progress
from app.services.factions import title_of
from app.services.prestige import KINDS
from app.services.story import Story
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.protocol_pages import headline
from app.ui.quest_pages import STATUS_TONE, reward_text


def story_page(
    page: Page,
    pc: PlayerCharacter,
    story: Story,
    *,
    progress: Progress | None,
    milestone: bool,
    mark_links: Mapping[str, str] | None = None,
    error: str | None = None,
) -> Element:
    """The character's story, newest moments first."""
    t = page.t
    name = pc.name or "?"
    gm = page.identity is not None and page.identity.is_gamemaster
    return app_shell(
        page,
        t("story.title", name=name),
        div(class_="tr-page story-page")[
            a(href=f"/characters/{pc.id}", class_="backlink")[icon("arrow-left"), span[name]],
            page_head(
                t("story.eyebrow"),
                t("story.title", name=name),
                t(
                    "story.lede",
                    level=pc.total_level,
                    xp=t("story.xp", xp=progress.xp)
                    if progress is not None and not milestone
                    else "",
                ).strip(" ·"),
            ),
            alert(error),
            div(class_="story-grid")[
                _quests(page, story, milestone),
                _marks(page, pc.id, story.marks, mark_links or {}, gm),
                _people(page, story),
                _timeline(page, story),
            ],
        ],
    )


def _panel(heading: str, ic: str, *content: Node) -> Element:
    return section(class_="panel story-panel")[
        h2(class_="session-head")[icon(ic, "sm"), span[heading]], content
    ]


def _quests(page: Page, story: Story, milestone: bool) -> Element:
    t = page.t
    return _panel(
        t("quests.eyebrow"),
        "scroll",
        ul(class_="session-list story-list")[
            [
                li[
                    b[q.name],
                    span(class_=f"pill {STATUS_TONE[q.status]}")[t(f"quests.status.{q.status}")],
                    small(class_="muted")[reward_text(page, q, milestone)]
                    if q.status == "done" and reward_text(page, q, milestone)
                    else None,
                ]
                for q in story.quests
            ]
        ]
        if story.quests
        else p(class_="muted")[t("story.no_quests")],
    )


def _marks(
    page: Page, character_id: str, marks: list[Mark], links: Mapping[str, str], gm: bool
) -> Node:
    """Its marks of prestige, each with what the guides say of its kind; the
    gamemaster's form to grant one."""
    if not marks and not gm:
        return None
    t = page.t
    url = f"/characters/{character_id}/story"

    def post(op: str, mark: Mark, text: str, cls: str = "btn btn-sm btn-ghost") -> Element:
        return form(method="post", action=url, class_="inline-form")[
            hidden("op", op), hidden("mark", mark.id), button(type="submit", class_=cls)[text]
        ]

    def kind(mark: Mark) -> Node:
        word = t(f"prestige.kind.{mark.kind}")
        link = links.get(mark.kind)
        return small(class_="muted")[
            a(href=link, target="_blank", rel="noopener")[word] if link else word
        ]

    rows = [
        li(class_="story-mark")[
            icon(_ICON[m.kind], "sm"),
            b[m.name],
            kind(m),
            small(class_="muted")[t("prestige.from", giver=m.granted_by)] if m.granted_by else None,
            span(class_="pill")[t("prestige.spent")] if m.spent else None,
            post("call_in", m, t("prestige.call_in"))
            if m.kind == "favor" and not m.spent
            else None,
            post("take_back", m, t("prestige.take_back")) if gm else None,
            p(class_="muted story-mark-note")[m.note] if m.note else None,
        ]
        for m in marks
    ]
    grant = (
        details(class_="story-grant")[
            summary[icon("plus", "sm"), span[t("prestige.grant")]],
            form(method="post", action=url, class_="follower-form")[
                hidden("op", "grant"),
                label(class_="field")[
                    span(class_="field-label")[t("prestige.kind_label")],
                    select(name="kind")[[option(value=k)[t(f"prestige.kind.{k}")] for k in KINDS]],
                ],
                label(class_="field")[
                    span(class_="field-label")[t("prestige.name")],
                    input_el(
                        type="text",
                        name="name",
                        maxlength="160",
                        required=True,
                        placeholder=t("prestige.name_hint"),
                    ),
                ],
                label(class_="field")[
                    span(class_="field-label")[t("prestige.granted_by")],
                    input_el(type="text", name="granted_by", maxlength="160"),
                ],
                label(class_="field story-grant-note")[
                    span(class_="field-label")[t("prestige.note")],
                    textarea(name="note", rows="2", placeholder=t("prestige.note_hint")),
                ],
                button(type="submit", class_="btn btn-sm btn-primary")[
                    icon("award", "sm"), span[t("prestige.grant_now")]
                ],
            ],
        ]
        if gm
        else None
    )
    return _panel(
        t("prestige.title"),
        "award",
        ul(class_="session-list story-list")[rows]
        if rows
        else p(class_="muted")[t("prestige.none")],
        grant,
    )


_ICON = {
    "title": "crown",
    "land": "map",
    "stronghold": "castle",
    "medal": "medal",
    "letter": "scroll",
    "favor": "handshake",
    "right": "badge-check",
    "training": "graduation-cap",
}


def _people(page: Page, story: Story) -> Element:
    t = page.t
    rows: list[Node] = []
    for faction, renown in story.factions:
        title = title_of(faction, renown)
        rows.append(
            li[
                icon("shield", "sm"),
                b[faction.name],
                span(class_="pill accent mono")[t("factions.renown", renown=renown)],
                span(class_="pill")[title] if title else None,
            ]
        )
    for contact in story.contacts:
        rows.append(
            li[
                icon("users", "sm"),
                span[contact.name or t(f"contacts.kind.{contact.kind}")],
                small(class_="muted")[t(f"contacts.kind.{contact.kind}")],
                small(class_="muted")[t("contacts.called")] if contact.spent else None,
            ]
        )
    for follower in story.followers:
        rows.append(
            li[
                icon("users-round", "sm"),
                span[follower.name],
                small(class_="muted")[t(f"followers.kind.{follower.kind}")],
            ]
        )
    bastion = story.bastion
    if bastion is not None and bastion.facilities:
        rows.append(
            li[
                icon("vault", "sm"),
                span[bastion.name or t("bastion.short")],
                small(class_="muted")[
                    t.plural(
                        "story.bastion",
                        len(bastion.facilities),
                        facilities=len(bastion.facilities),
                        turns=bastion.turns,
                    )
                ],
            ]
        )
    return _panel(
        t("story.people"),
        "users",
        ul(class_="session-list story-list")[rows] if rows else p(class_="muted")[t("story.alone")],
    )


def _timeline(page: Page, story: Story) -> Element:
    t = page.t
    return _panel(
        t("story.moments"),
        "clock",
        ul(class_="session-list story-list")[
            [
                li[
                    small(class_="mono muted")[entry.at.date().isoformat()],
                    span[headline(page, entry)],
                ]
                for entry in story.timeline
            ]
        ]
        if story.timeline
        else p(class_="muted")[t("story.no_moments")],
    )


__all__ = ["story_page"]
