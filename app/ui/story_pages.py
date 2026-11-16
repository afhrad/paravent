"""A character's story page (app/services/story.py): its quests, factions, contacts,
followers and bastion, and the moments of the log that changed it."""

from __future__ import annotations

from htpy import Element, Node, a, b, div, h2, li, p, section, small, span, ul

from app.models.pc import PlayerCharacter
from app.services.experience import Progress
from app.services.factions import title_of
from app.services.story import Story
from app.ui.context import Page
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
) -> Element:
    """The character's story, newest moments first."""
    t = page.t
    name = pc.name or "?"
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
            div(class_="story-grid")[
                _quests(page, story, milestone),
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
