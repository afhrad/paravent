"""A character's downtime (app/services/downtime.py): the free days, the projects they
go into, and new projects to start."""

from __future__ import annotations

from collections.abc import Sequence

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
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Downtime, DowntimeProject
from app.services.downtime import DAILY, RECUPERATING_DAYS, TRAINING_DAYS
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text
from app.ui.shop_pages import coin_text


def downtime_url(character_id: str) -> str:
    return f"/characters/{character_id}/downtime"


def project_label(page: Page, project: DowntimeProject) -> str:
    """What a project works on, in the reader's language."""
    if project.kind == "craft":
        return str(name_text(page, ("item", "baseitem"), project.label.partition("|")[0]))
    if project.kind == "train":
        kind, _, ref = project.label.partition(":")
        name = ref.partition("|")[0]
        return str(
            name_text(page, "language" if kind == "language" else ("item", "baseitem"), name)
        )
    return project.label


def downtime_page(
    page: Page,
    *,
    character_id: str,
    character_name: str,
    downtime: Downtime,
    craftable: Sequence[tuple[str, str, int, int]],
    trainable: Sequence[tuple[str, str]],
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The free days, each project with its progress and a way to spend days on it, and
    the new projects. ``craftable`` is ``(reference, name, days, materials in copper)``;
    ``trainable`` ``(language:/tool: reference, name)``."""
    t = page.t
    url = downtime_url(character_id)
    running = [x for x in downtime.projects if not x.finished]
    finished = [x for x in downtime.projects if x.finished]
    return app_shell(
        page,
        t("downtime.title", name=character_name),
        div(class_="tr-page downtime-page")[
            a(href=f"/characters/{character_id}", class_="backlink")[
                icon("arrow-left"), span[character_name]
            ],
            page_head(
                t("downtime.eyebrow"),
                t("downtime.title", name=character_name),
                t("downtime.lede"),
            ),
            alert(error),
            notice(message),
            p(class_="downtime-days")[
                icon("hourglass", "sm"),
                b[t.plural("downtime.days_free", downtime.days, n=downtime.days)],
            ],
            section(class_="panel downtime-projects")[
                div(class_="panel-head")[h2[t("downtime.projects")]],
                ul(class_="downtime-list")[[_project(page, url, x, downtime.days) for x in running]]
                if running
                else p(class_="muted")[t("downtime.none")],
            ],
            section(class_="panel downtime-new")[
                div(class_="panel-head")[h2[t("downtime.start")]],
                _start(page, url, craftable, trainable),
            ],
            section(class_="panel downtime-done")[
                div(class_="panel-head")[h2[t("downtime.finished")]],
                ul(class_="downtime-list")[[_project(page, url, x, 0) for x in finished]],
            ]
            if finished
            else None,
        ],
    )


def _project(page: Page, url: str, project: DowntimeProject, free: int) -> Element:
    t = page.t
    progress: Node
    if project.needed is not None:
        progress = div(class_="downtime-progress")[
            div(class_="bar")[
                div(
                    class_="bar-fill",
                    style=f"width:{min(100, round(100 * project.done / project.needed))}%",
                )
            ],
            small(class_="mono muted")[
                t("downtime.progress", done=project.done, needed=project.needed)
            ],
        ]
    else:
        progress = small(class_="mono muted")[
            t.plural("downtime.days_spent", project.done, n=project.done)
        ]
    left = (project.needed - project.done) if project.needed is not None else free
    daily = DAILY.get(project.kind, 0)
    return li(class_=f"downtime-row {project.kind}")[
        div(class_="downtime-what")[
            b[t(f"downtime.kind.{project.kind}")],
            span[f" · {project_label(page, project)}"] if project.label else None,
            small(class_="muted")[f" · {t('downtime.paid', coins=coin_text(page, project.paid))}"]
            if project.paid
            else None,
        ],
        progress,
        form(method="post", action=url, class_="inline-form downtime-spend")[
            hidden("op", "spend"),
            hidden("project", project.id),
            input_el(
                type="number",
                name="days",
                min="1",
                max=str(max(1, min(free, left))),
                value=str(max(1, min(free, left))),
                inputmode="numeric",
                class_="mono party-clock-amount",
                aria_label=t("downtime.days"),
            ),
            button(
                type="submit",
                class_="btn btn-sm btn-primary",
                title=t("downtime.daily", coins=coin_text(page, daily)) if daily else None,
            )[t("downtime.spend")],
        ]
        if not project.finished and free > 0
        else None,
        form(method="post", action=url, class_="inline-form")[
            hidden("op", "drop"),
            hidden("project", project.id),
            button(
                type="submit",
                class_="btn btn-sm btn-ghost",
                title=t("downtime.clear" if project.finished else "downtime.drop"),
                aria_label=t("downtime.clear" if project.finished else "downtime.drop"),
            )[icon("x", "sm")],
        ],
    ]


def _start(
    page: Page,
    url: str,
    craftable: Sequence[tuple[str, str, int, int]],
    trainable: Sequence[tuple[str, str]],
) -> Node:
    t = page.t

    def started(kind: str, *content: Node, hint: str = "") -> Element:
        return details(class_="downtime-start")[
            summary[
                b[t(f"downtime.kind.{kind}")], small(class_="muted")[f" · {hint}"] if hint else None
            ],
            form(method="post", action=url, class_="downtime-form")[
                hidden("op", "start"),
                hidden("kind", kind),
                content,
                button(type="submit", class_="btn btn-sm btn-primary")[t("downtime.begin")],
            ],
        ]

    def days_field() -> Element:
        return label(class_="party-clock-label")[
            span[t("downtime.days_needed")],
            input_el(
                type="number",
                name="needed",
                min="1",
                max="365",
                inputmode="numeric",
                class_="mono party-clock-amount",
            ),
        ]

    def text_field() -> Node:
        return input_el(
            type="text",
            name="label",
            maxlength="200",
            required=True,
            placeholder=t("downtime.what"),
            aria_label=t("downtime.what"),
        )

    return [
        started(
            "craft",
            select(name="item", aria_label=t("downtime.item"))[
                [
                    option(value=ref)[
                        t(
                            "downtime.craft_option",
                            name=name,
                            days=days,
                            coins=coin_text(page, cost),
                        )
                    ]
                    for ref, name, days, cost in craftable
                ]
            ],
            hint=t("downtime.craft_hint"),
        )
        if craftable
        else None,
        started(
            "train",
            select(name="item", aria_label=t("downtime.item"))[
                [option(value=ref)[name] for ref, name in trainable]
            ],
            hint=t("downtime.train_hint", days=TRAINING_DAYS),
        )
        if trainable
        else None,
        started("research", text_field(), days_field(), hint=t("downtime.research_hint")),
        started("recuperate", hint=t("downtime.recuperate_hint", days=RECUPERATING_DAYS)),
        started("work", hint=t("downtime.work_hint")),
        started("other", text_field(), days_field()),
    ]


def sheet_downtime_link(page: Page, character_id: str, days: int) -> Node:
    """The sheet's way to the downtime page, with the free days."""
    return a(href=downtime_url(character_id), class_="btn btn-sm")[
        icon("hourglass", "sm"),
        span[
            page.t.plural("downtime.short", days, n=days) if days else page.t("downtime.short_none")
        ],
    ]


__all__ = ["downtime_page", "downtime_url", "project_label", "sheet_downtime_link"]
