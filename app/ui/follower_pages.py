"""A character's followers (app/services/followers.py): hirelings with their wage, and
the companions, mounts and sidekicks with their hit points, a way to bring one into the
character's fight, and the forms that hire or add one."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote

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
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Follower
from app.services.followers import CREATURES, HIRELINGS, proficiency, sidekick_level
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.shop_pages import coin_text


def followers_url(character_id: str) -> str:
    return f"/characters/{character_id}/followers"


def followers_page(
    page: Page,
    *,
    character_id: str,
    character_name: str,
    world_id: str | None = None,
    followers: Sequence[Follower] = (),
    wages: dict[str, int],
    in_fight: bool,
    sidekick_classes: Sequence[tuple[str, str]] = (),
    progress: Mapping[str, tuple[Sequence[tuple[int, str]], Mapping[str, int]]] | None = None,
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """Every follower of the character, and the forms to hire or add one."""
    t = page.t
    url = followers_url(character_id)
    return app_shell(
        page,
        t("followers.title", name=character_name),
        div(class_="tr-page followers-page")[
            a(href=f"/characters/{character_id}", class_="backlink")[
                icon("arrow-left"), span[character_name]
            ],
            page_head(
                t("followers.eyebrow"),
                t("followers.title", name=character_name),
                t("followers.lede"),
            ),
            alert(error),
            notice(message),
            section(class_="panel")[
                ul(class_="follower-list")[
                    [
                        _row(page, url, f, in_fight, world_id, (progress or {}).get(f.id))
                        for f in followers
                    ]
                ]
                if followers
                else p(class_="muted")[t("followers.none")],
            ],
            section(class_="panel follower-new")[
                div(class_="panel-head")[h2[t("followers.add")]],
                _post(
                    url,
                    "hire",
                    select(name="kind", aria_label=t("followers.kind_label"))[
                        [
                            option(value=k)[
                                t(f"followers.kind.{k}") + f" ({coin_text(page, wages[k])} / "
                                f"{t('followers.day')})"
                            ]
                            for k in HIRELINGS
                            if wages.get(k)
                        ]
                    ],
                    _text("name", t("followers.name"), required=False),
                    button(type="submit", class_="btn btn-sm")[
                        icon("user-plus", "sm"), span[t("followers.hire")]
                    ],
                )
                if any(wages.get(k) for k in HIRELINGS)
                else p(class_="muted")[t("followers.no_wages")],
                _post(
                    url,
                    "creature",
                    select(name="kind", aria_label=t("followers.kind_label"))[
                        [option(value=k)[t(f"followers.kind.{k}")] for k in CREATURES]
                    ],
                    _text("creature", t("followers.creature"), required=True),
                    _text("name", t("followers.name"), required=False),
                    select(name="sidekick_class", aria_label=t("followers.sidekick_class"))[
                        option(value="")[t("followers.no_sidekick_class")],
                        [option(value=ref)[name] for ref, name in sidekick_classes],
                    ]
                    if sidekick_classes
                    else None,
                    button(type="submit", class_="btn btn-sm")[
                        icon("plus", "sm"), span[t("followers.add_creature")]
                    ],
                ),
                small(class_="muted")[t("followers.hint")],
            ],
        ],
    )


def _post(url: str, op: str, *content: Node, cls: str = "follower-form") -> Element:
    return form(method="post", action=url, class_=cls)[hidden("op", op), content]


def _text(name: str, label: str, *, required: bool) -> Node:
    return input_el(
        type="text",
        name=name,
        maxlength="120",
        placeholder=label,
        aria_label=label,
        required=required,
    )


def _row(
    page: Page,
    url: str,
    follower: Follower,
    in_fight: bool,
    world_id: str | None = None,
    progress: tuple[Sequence[tuple[int, str]], Mapping[str, int]] | None = None,
) -> Element:
    t = page.t
    creature = follower.kind in CREATURES
    name, _, source = follower.monster.partition("|")
    return li(class_=f"follower-row is-{follower.kind}")[
        div(class_="follower-head")[
            b[follower.name],
            span(class_="pill")[t(f"followers.kind.{follower.kind}")],
            a(href=f"/ref/monster/{source}/{name}" + (f"?world={world_id}" if world_id else ""))[
                page.t.term("monster", name) or name
            ]
            if creature
            else small(class_="muted")[t("followers.wage", coins=coin_text(page, follower.wage))],
            small(class_="muted")[t("followers.levels", n=follower.levels)]
            if follower.levels
            else None,
        ],
        div(class_="follower-acts")[
            span(class_="mono follower-hp")[f"{follower.hp} / {follower.hp_max}"],
            [
                _post(
                    url,
                    "hp",
                    hidden("follower", follower.id),
                    hidden("delta", str(delta)),
                    button(type="submit", class_="btn btn-sm")[f"{delta:+d}"],
                    cls="inline-form",
                )
                for delta in (-5, -1, 1, 5)
            ],
            _post(
                url,
                "level",
                hidden("follower", follower.id),
                button(type="submit", class_="btn btn-sm", title=t("followers.level_hint"))[
                    icon("sparkles", "sm"), span[t("followers.level_up")]
                ],
                cls="inline-form",
            )
            if follower.kind == "sidekick"
            else None,
            _post(
                url,
                "fight",
                hidden("follower", follower.id),
                button(type="submit", class_="btn btn-sm")[
                    icon("swords", "sm"), span[t("followers.to_fight")]
                ],
                cls="inline-form",
            )
            if in_fight and follower.hp
            else None,
        ]
        if creature
        else None,
        _sidekick(page, follower, progress, world_id) if follower.sidekick_class else None,
        p(class_="muted")[follower.note] if follower.note else None,
        details(class_="follower-more")[
            summary[t("followers.dismiss")],
            _post(
                url,
                "dismiss",
                hidden("follower", follower.id),
                button(type="submit", class_="btn btn-sm btn-ghost")[
                    icon("x", "sm"), span[t("followers.dismiss_now")]
                ],
                cls="inline-form",
            ),
        ],
    ]


def _sidekick(
    page: Page,
    follower: Follower,
    progress: tuple[Sequence[tuple[int, str]], Mapping[str, int]] | None,
    world_id: str | None,
) -> Element:
    """A sidekick's class: its level, proficiency bonus, features and spell counts."""
    t = page.t
    level = sidekick_level(follower)
    name, _, source = follower.sidekick_class.partition("|")
    features, table = progress or ((), {})
    query = f"?world={world_id}" if world_id else ""
    return div(class_="follower-sidekick")[
        p[
            a(href=f"/ref/class/{source}/{quote(name)}{query}")[name],
            " · ",
            t("followers.sidekick_line", level=level, bonus=f"+{proficiency(level)}"),
        ],
        ul(class_="follower-features")[
            [
                li[small(class_="mono muted")[str(at)], " ", span[feature]]
                for at, feature in features
            ]
        ]
        if features
        else None,
        small(class_="muted")[
            " · ".join(f"{label} {value}" for label, value in table.items() if value)
        ]
        if table
        else None,
    ]


def sheet_followers_link(page: Page, character_id: str, count: int) -> Element:
    """The sheet's way to its followers, with how many there are."""
    return a(href=followers_url(character_id), class_="btn btn-sm")[
        icon("users", "sm"),
        span[page.t("followers.short")],
        span(class_="pill")[str(count)] if count else None,
    ]


__all__ = ["followers_page", "followers_url", "sheet_followers_link"]
