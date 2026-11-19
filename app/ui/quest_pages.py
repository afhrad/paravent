"""Quests (app/services/quests.py): the gamemaster's page of a world's quests — write
them, tick their steps, mark them done to hand out the reward — and the section of the
players' journal that lists the quests shown to them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
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

from app.models.gamestate import Quest, World
from app.ui.context import Page
from app.ui.forms import alert, hidden
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text
from app.ui.shop_pages import coin_text
from app.ui.title_images import title_image, title_image_panel

QUESTS_URL = "/gm/quests"
STATUS_TONE = {"open": "accent", "done": "heal", "failed": "dmg"}


def quests_url(world_id: str) -> str:
    """A world's quest page."""
    return f"{QUESTS_URL}?{urlencode({'world': world_id})}"


def reward_text(page: Page, quest: Quest, milestone: bool = False) -> str:
    """A quest's reward in a line: "300 XP each · 50 gp · Potion of Healing ×2 ·
    renown +1"; XP left out where the world levels by milestone."""
    t = page.t
    parts = []
    if quest.xp and not milestone:
        parts.append(t("quests.reward_xp", xp=quest.xp))
    if quest.coins:
        parts.append(coin_text(page, quest.coins))
    for item in quest.items:
        name = str(name_text(page, ("item", "baseitem"), item.ref.partition("|")[0]))
        parts.append(f"{name} ×{item.count}" if item.count > 1 else name)
    if quest.renown and quest.faction_id:
        parts.append(t("quests.reward_renown", renown=quest.renown))
    return " · ".join(parts)


def quests_page(
    page: Page,
    world: World,
    quests: Sequence[Quest],
    members: Sequence[tuple[str, str]],
    factions: Sequence[tuple[str, str]],
    parties: Mapping[str, Sequence[str]],
    *,
    milestone: bool = False,
    error: str | None = None,
) -> Element:
    """Every quest of the world, open first, and the form for a new one. ``members``
    are the world's characters and ``factions`` its factions, each ``(id, name)``;
    ``parties`` names who is on each quest, by its id."""
    t = page.t
    base = f"{QUESTS_URL}/{quote(world.id)}"
    names = dict(members)
    return app_shell(
        page,
        t("quests.page_title", world=world.name),
        [
            page_head(t("quests.eyebrow"), world.name, t("quests.lede")),
            alert(error),
            div(class_="quest-list")[
                [
                    _quest(
                        page, base, q, members, factions, parties.get(q.id, ()), names, milestone
                    )
                    for q in quests
                ]
                if quests
                else p(class_="muted")[t("quests.empty")],
            ],
            section(class_="panel quest-new")[
                div(class_="panel-head")[h2[t("quests.new")]],
                _fields(page, base, None, members, factions, milestone),
            ],
        ],
        main_class="shell-main shell-wide",
        world_id=world.id,
    )


def _post(base: str, op: str, *content: Node, cls: str = "inline-form") -> Element:
    return form(method="post", action=f"{base}/{op}", class_=cls)[content]


def _quest(
    page: Page,
    base: str,
    quest: Quest,
    members: Sequence[tuple[str, str]],
    factions: Sequence[tuple[str, str]],
    party: Sequence[str],
    names: Mapping[str, str],
    milestone: bool,
) -> Element:
    t = page.t
    faction = dict(factions).get(quest.faction_id or "")
    reward = reward_text(page, quest, milestone)
    is_open = quest.status == "open"
    found = (quest.world_id, quest.cover) if quest.cover else None
    return section(class_=f"panel quest-panel is-{quest.status}")[
        title_image(found, quest.name),
        div(class_="quest-head")[
            h2[quest.name],
            span(class_=f"pill {STATUS_TONE[quest.status]}")[t(f"quests.status.{quest.status}")],
            span(class_="pill")[t("quests.hidden")] if not quest.shown else None,
            span(class_="pill")[t("quests.from_draft")] if quest.adventure_id else None,
        ],
        small(class_="muted")[
            " · ".join(
                x
                for x in (
                    t("quests.given_by", giver=quest.giver) if quest.giver else "",
                    t("quests.for_faction", faction=faction) if faction else "",
                    ", ".join(names.get(c, "?") for c in party) if quest.characters else "",
                )
                if x
            )
        ],
        p(class_="quest-text")[quest.text] if quest.text else None,
        ul(class_="quest-steps")[
            [
                li[
                    _post(
                        base,
                        "step",
                        hidden("quest", quest.id),
                        hidden("index", str(n)),
                        button(
                            type="submit",
                            class_="btn btn-sm btn-ghost quest-tick" + (" is-on" if s.done else ""),
                            aria_pressed="true" if s.done else "false",
                        )[
                            icon("circle-check", "sm") if s.done else span(class_="quest-box"),
                            span[s.text],
                        ],
                    )
                    if is_open
                    else span(class_="quest-done" if s.done else None)[s.text]
                ]
                for n, s in enumerate(quest.steps)
            ]
        ]
        if quest.steps
        else None,
        p[icon("gem", "sm"), " ", b[t("quests.reward")], " ", span[reward]] if reward else None,
        _post(
            base,
            "finish",
            hidden("quest", quest.id),
            [
                label(class_="quest-give")[
                    span[str(name_text(page, ("item", "baseitem"), item.ref.partition("|")[0]))],
                    select(name=f"give_{n}")[
                        [option(value=cid)[names.get(cid, "?")] for cid in party]
                    ],
                ]
                for n, item in enumerate(quest.items)
            ]
            if len(party) > 1
            else None,
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("circle-check", "sm"), span[t("quests.finish")]
            ],
            cls="quest-finish",
        )
        if is_open
        else None,
        div(class_="quest-acts")[
            _post(
                base,
                "fail",
                hidden("quest", quest.id),
                button(type="submit", class_="btn btn-sm btn-ghost")[t("quests.fail")],
            )
            if is_open
            else None,
            _post(
                base,
                "reopen",
                hidden("quest", quest.id),
                button(type="submit", class_="btn btn-sm btn-ghost")[t("quests.reopen")],
            )
            if quest.status == "failed"
            else None,
            _post(
                base,
                "delete",
                hidden("quest", quest.id),
                button(type="submit", class_="btn btn-sm btn-ghost")[
                    icon("trash-2", "sm"), span[t("quests.delete")]
                ],
            ),
        ],
        details(class_="quest-edit")[
            summary[t("quests.edit")],
            _fields(page, base, quest, members, factions, milestone),
            title_image_panel(page, "quest", quest.id, found, owner=quest.world_id, heading=False),
        ]
        if is_open
        else None,
    ]


def _fields(
    page: Page,
    base: str,
    quest: Quest | None,
    members: Sequence[tuple[str, str]],
    factions: Sequence[tuple[str, str]],
    milestone: bool,
) -> Element:
    """The quest's form: new, or editing ``quest``."""
    t = page.t
    steps = "\n".join(("[x] " if s.done else "") + s.text for s in quest.steps) if quest else ""
    items = "\n".join(f"{i.count} {i.ref}" for i in quest.items) if quest else ""
    return _post(
        base,
        "save",
        hidden("quest", quest.id if quest else ""),
        label(class_="field")[
            span[t("quests.name")],
            input_el(
                type="text",
                name="name",
                value=quest.name if quest else "",
                maxlength="160",
                required=True,
            ),
        ],
        label(class_="field")[
            span[t("quests.giver")],
            input_el(
                type="text", name="giver", value=quest.giver if quest else "", maxlength="160"
            ),
        ],
        label(class_="field")[
            span[t("quests.faction")],
            select(name="faction_id")[
                option(value="")[t("factions.none")],
                [
                    option(value=fid, selected=quest is not None and quest.faction_id == fid)[name]
                    for fid, name in factions
                ],
            ],
        ]
        if factions
        else None,
        label(class_="field quest-wide")[
            span[t("quests.text")],
            textarea(name="text", rows="3", maxlength="8000")[quest.text if quest else ""],
        ],
        label(class_="field quest-wide")[
            span[t("quests.steps")],
            textarea(name="steps", rows="3", placeholder=t("quests.steps_hint"))[steps],
        ],
        div(class_="field quest-wide")[
            span[t("quests.characters")],
            div(class_="quest-who")[
                [
                    label(class_="cs-heal-pick")[
                        input_el(
                            type="checkbox",
                            name="character",
                            value=cid,
                            checked=quest is not None and cid in quest.characters,
                        ),
                        span[name],
                    ]
                    for cid, name in members
                ]
            ],
            small(class_="muted")[t("quests.characters_hint")],
        ],
        label(class_="field")[
            span[t("quests.xp")],
            input_el(
                type="number",
                name="xp",
                min="0",
                value=str(quest.xp) if quest and quest.xp else "",
                inputmode="numeric",
            ),
        ]
        if not milestone
        else None,
        label(class_="field")[
            span[t("quests.coins")],
            input_el(
                type="number",
                name="gold",
                min="0",
                value=str(quest.coins // 100) if quest and quest.coins else "",
                inputmode="numeric",
            ),
        ],
        label(class_="field")[
            span[t("quests.renown")],
            input_el(
                type="number",
                name="renown",
                min="0",
                max="50",
                value=str(quest.renown) if quest and quest.renown else "",
                inputmode="numeric",
            ),
        ]
        if factions
        else None,
        label(class_="field quest-wide")[
            span[t("quests.items")],
            textarea(name="items", rows="2", placeholder=t("quests.items_hint"))[items],
        ],
        label(class_="cs-heal-pick")[
            input_el(
                type="checkbox", name="shown", value="1", checked=quest is None or quest.shown
            ),
            span[t("quests.shown")],
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("plus" if quest is None else "check", "sm"),
                span[t("quests.create" if quest is None else "quests.save")],
            ]
        ],
        cls="quest-form",
    )


def journal_quests(
    page: Page, world: str, quests: Sequence[Quest], milestone: bool = False
) -> Element:
    """A world's quests as its players read them, in the journal."""
    t = page.t
    return section(class_="panel journal-quests")[
        div(class_="panel-head")[h2[t("quests.journal_heading", world=world)]],
        ul(class_="quest-read")[
            [
                li(class_=f"quest-row is-{q.status}")[
                    div(class_="quest-head")[
                        b[q.name],
                        span(class_=f"pill {STATUS_TONE[q.status]}")[
                            t(f"quests.status.{q.status}")
                        ],
                    ],
                    small(class_="muted")[t("quests.given_by", giver=q.giver)] if q.giver else None,
                    p(class_="quest-text")[q.text] if q.text else None,
                    ul(class_="quest-steps")[
                        [
                            li(class_="quest-done" if s.done else None)[
                                icon("circle-check", "sm") if s.done else span(class_="quest-box"),
                                " ",
                                s.text,
                            ]
                            for s in q.steps
                        ]
                    ]
                    if q.steps
                    else None,
                    small[t("quests.reward"), " ", reward_text(page, q, milestone)]
                    if reward_text(page, q, milestone)
                    else None,
                ]
                for q in quests
            ]
        ]
        if quests
        else p(class_="muted")[t("quests.none_open")],
    ]


__all__ = ["QUESTS_URL", "journal_quests", "quests_page", "quests_url", "reward_text"]
