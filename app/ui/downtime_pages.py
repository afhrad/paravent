"""A character's downtime (app/services/downtime.py): the free days, the projects they
go into, and new projects to start."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

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

from app.models.gamestate import Contact, Downtime, DowntimeProject
from app.services.activities import (
    CAROUSING,
    CRIME_DCS,
    CRIME_THIRD,
    JOBS,
    SERVICE,
    offers_of,
)
from app.services.contacts import KINDS as CONTACT_KINDS
from app.services.downtime import DAILY, RECUPERATING_DAYS
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text
from app.ui.shop_pages import coin_text

# How each kind of contact is coloured.
CONTACT_TONE = {"ally": "heal", "favour": "accent", "enemy": "dmg", "rival": "dmg"}


def downtime_url(character_id: str) -> str:
    return f"/characters/{character_id}/downtime"


@dataclass(frozen=True)
class StartOptions:
    """What the page offers to start: ``craftable`` is ``(reference, name, days,
    materials in copper, tool)``; ``trainable`` ``(language:/tool: reference, name)``
    with the world's training rule ("xge", "phb", "table") and its days and daily cost;
    ``scrolls`` ``(spell, level, days, copper)``; ``activities`` whether the world has
    the book's downtime activities; ``magic`` the carried magic items that can be sold,
    ``(reference, name, rarity)``."""

    craftable: Sequence[tuple[str, str, int, int, str]] = ()
    unlisted: int = 0
    trainable: Sequence[tuple[str, str]] = ()
    training: str = "phb"
    training_days: int | None = None
    training_daily: int = 0
    scrolls: Sequence[tuple[str, int, int, int]] = ()
    activities: bool = False
    magic: Sequence[tuple[str, str, str]] = field(default=())


def project_label(page: Page, project: DowntimeProject) -> str:
    """What a project works on, in the reader's language."""
    if project.kind == "craft":
        return str(name_text(page, ("item", "baseitem"), project.label.partition("|")[0]))
    if project.kind == "scribe":
        spell = project.label.partition(";")[2]
        return str(name_text(page, "spell", spell.partition("|")[0]))
    if project.kind == "carouse":
        return page.t(f"downtime.class_name.{project.label}")
    if project.kind in ("gamble",):
        return coin_text(page, int(project.label)) if project.label.isdigit() else ""
    if project.kind == "sell_magic":
        return str(name_text(page, "item", project.label.partition("|")[0]))
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
    start: StartOptions,
    contacts: Sequence[Contact] = (),
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The free days, each project with its progress and a way to spend days on it, the
    new projects, the book's activities, whom the character knows, and what finished
    (with any offers)."""
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
                _start(page, url, start),
            ],
            section(class_="panel downtime-new")[
                div(class_="panel-head")[h2[t("downtime.activities")]],
                p(class_="muted")[t("downtime.activities_hint")],
                _activities(page, url, start, downtime.days),
            ]
            if start.activities
            else None,
            _contacts(page, url, contacts),
            section(class_="panel downtime-done")[
                div(class_="panel-head")[h2[t("downtime.finished")]],
                ul(class_="downtime-list")[[_project(page, url, x, 0) for x in reversed(finished)]],
            ]
            if finished
            else None,
        ],
    )


def _contacts(page: Page, url: str, contacts: Sequence[Contact]) -> Element:
    """Whom the character knows: allies, enemies, rivals and favours owed, each named
    and noted, called on once; and a way to write one down."""
    t = page.t

    def row(contact: Contact) -> Element:
        return li(class_="contact-row" + (" is-spent" if contact.spent else ""))[
            span(class_=f"pill {CONTACT_TONE.get(contact.kind, '')}".strip())[
                t(f"contacts.kind.{contact.kind}")
            ],
            form(method="post", action=url, class_="contact-edit")[
                hidden("op", "contact_edit"),
                hidden("contact", contact.id),
                input_el(
                    type="text",
                    name="name",
                    value=contact.name,
                    maxlength="120",
                    placeholder=t("contacts.name"),
                    aria_label=t("contacts.name"),
                ),
                input_el(
                    type="text",
                    name="note",
                    value=contact.note,
                    maxlength="2000",
                    placeholder=t("contacts.note"),
                    aria_label=t("contacts.note"),
                ),
                button(type="submit", class_="btn btn-sm")[t("contacts.save")],
            ],
            small(class_="muted")[t(f"contacts.from.{contact.source}")] if contact.source else None,
            form(method="post", action=url, class_="inline-form")[
                hidden("op", "contact_call"),
                hidden("contact", contact.id),
                button(
                    type="submit",
                    class_="btn btn-sm" + (" is-on" if contact.spent else ""),
                    aria_pressed="true" if contact.spent else "false",
                )[t("contacts.called" if contact.spent else "contacts.call")],
            ]
            if contact.kind in ("ally", "favour", "contact")
            else None,
            form(method="post", action=url, class_="inline-form")[
                hidden("op", "contact_remove"),
                hidden("contact", contact.id),
                button(type="submit", class_="btn btn-sm btn-ghost", title=t("contacts.remove"))[
                    icon("x", "sm")
                ],
            ],
        ]

    return section(class_="panel downtime-contacts")[
        div(class_="panel-head")[h2[t("contacts.heading")]],
        ul(class_="contact-list")[[row(c) for c in contacts]]
        if contacts
        else p(class_="muted")[t("contacts.none")],
        details(class_="contact-new")[
            summary[t("contacts.add")],
            form(method="post", action=url, class_="contact-edit")[
                hidden("op", "contact_add"),
                select(name="kind", aria_label=t("contacts.kind_label"))[
                    [option(value=k)[t(f"contacts.kind.{k}")] for k in CONTACT_KINDS]
                ],
                input_el(
                    type="text",
                    name="name",
                    maxlength="120",
                    required=True,
                    placeholder=t("contacts.name"),
                    aria_label=t("contacts.name"),
                ),
                input_el(
                    type="text",
                    name="note",
                    maxlength="2000",
                    placeholder=t("contacts.note"),
                    aria_label=t("contacts.note"),
                ),
                button(type="submit", class_="btn btn-sm")[
                    icon("user-plus", "sm"), span[t("contacts.add")]
                ],
            ],
        ],
    ]


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
    daily = project.daily if project.daily is not None else DAILY.get(project.kind, 0)
    return li(class_=f"downtime-row {project.kind}")[
        div(class_="downtime-what")[
            b[t(f"downtime.kind.{project.kind}")],
            span[f" · {project_label(page, project)}"] if project.label else None,
            small(class_="muted")[f" · {t('downtime.paid', coins=coin_text(page, project.paid))}"]
            if project.paid
            else None,
        ],
        progress if not project.result else None,
        ul(class_="downtime-result")[[li[_line(page, line)] for line in project.result]]
        if project.result
        else None,
        _offers(page, url, project) if project.offers else None,
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


def _line(page: Page, line: str) -> Node:
    """One line of an activity's outcome, in the reader's words."""
    t = page.t
    head, _, rest = line.partition(":")
    fields = rest.split(":")
    if head == "check":
        name, total = (fields + ["", ""])[:2]
        words: list[Node] = [b[_check_name(page, name)], " ", span(class_="mono")[total]]
        if len(fields) >= 4:
            ok = fields[3] == "1"
            words += [
                span(class_="muted")[f" · {t('downtime.against', dc=fields[2])} "],
                span(class_="pill " + ("heal" if ok else "dmg"))[
                    t("downtime.success" if ok else "downtime.failure")
                ],
            ]
        elif len(fields) == 3:
            words.append(span(class_="muted")[f" ({fields[2]})"])
        return span[words]
    if head == "row":
        return span[rest]
    if head == "complication":
        return span(class_="downtime-complication")[
            icon("triangle-alert", "sm"), b[t("downtime.complication")], f" {rest}" if rest else ""
        ]
    if head == "won":
        return b[t("downtime.won", coins=coin_text(page, _int(rest)))]
    if head == "debt":
        return b[t("downtime.debt", coins=coin_text(page, _int(rest)))]
    if head == "caught":
        loot, weeks = (fields + ["0", "0"])[:2]
        return b[t("downtime.caught", coins=coin_text(page, _int(loot)), weeks=weeks)]
    if head == "offer":
        return b[t("downtime.offer", coins=coin_text(page, _int(rest)))]
    return span[t(f"downtime.line.{head}")]


def _int(text: str) -> int:
    return int(text) if text.isdigit() else 0


def _check_name(page: Page, name: str) -> str:
    t = page.t
    if name in ("str", "dex", "con", "int", "wis", "cha"):
        return t(f"ability.{name}")
    if name in ("thieves_tools", "tools"):
        return t(f"downtime.check.{name}")
    return t(f"skill.{name}")


def _offers(page: Page, url: str, project: DowntimeProject) -> Element:
    """The items offered to a buyer, or the offer for an item, with take and refuse."""
    t = page.t

    def buttons(n: int, take: str) -> Node:
        return [
            form(method="post", action=url, class_="inline-form")[
                hidden("op", "take"),
                hidden("project", project.id),
                hidden("offer", str(n)),
                button(type="submit", class_="btn btn-sm btn-primary")[take],
            ],
            form(method="post", action=url, class_="inline-form")[
                hidden("op", "refuse"),
                hidden("project", project.id),
                hidden("offer", str(n)),
                button(type="submit", class_="btn btn-sm btn-ghost")[t("downtime.refuse")],
            ],
        ]

    if project.kind == "sell_magic":
        rows = []
        for n, offer in enumerate(project.offers):
            ref, _, price = offer.rpartition("|")
            rows.append(
                li(class_="shop-row")[
                    span[name_text(page, "item", ref.partition("|")[0])],
                    span(class_="mono shop-price")[coin_text(page, _int(price))],
                    buttons(n, t("downtime.sell")),
                ]
            )
        return ul(class_="shop-list downtime-offers")[rows]
    return ul(class_="shop-list downtime-offers")[
        [
            li(class_="shop-row")[
                span[name_text(page, "item", name)],
                small(class_="muted")[source],
                span(class_="mono shop-price")[coin_text(page, price)],
                buttons(n, t("downtime.buy")),
            ]
            for n, (name, source, price) in enumerate(offers_of(project))
        ]
    ]


def _start(page: Page, url: str, start: StartOptions) -> Node:
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

    if start.training == "xge":
        train_hint = t(
            "downtime.train_hint_xge",
            days=start.training_days or 0,
            coins=coin_text(page, start.training_daily * 5),
        )
    elif start.training == "phb":
        train_hint = t("downtime.train_hint", days=start.training_days or 0)
    else:
        train_hint = t("downtime.train_hint_table")
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
                        + (f" · {tool}" if tool else "")
                    ]
                    for ref, name, days, cost, tool in start.craftable
                ]
            ],
            p(class_="muted")[t.plural("downtime.unlisted", start.unlisted, n=start.unlisted)]
            if start.unlisted
            else None,
            hint=t("downtime.craft_hint"),
        )
        if start.craftable
        else None,
        started(
            "train",
            select(name="item", aria_label=t("downtime.item"))[
                [option(value=ref)[name] for ref, name in start.trainable]
            ],
            days_field() if start.training == "table" else None,
            hint=train_hint,
        )
        if start.trainable
        else None,
        started(
            "scribe",
            select(name="spell", aria_label=t("downtime.spell"))[
                [
                    option(value=f"{spell}|{level}")[
                        t(
                            "downtime.scroll_option",
                            name=name_text(page, "spell", spell.partition("|")[0]),
                            level=level,
                            days=days,
                            coins=coin_text(page, cost),
                        )
                    ]
                    for spell, level, days, cost in start.scrolls
                ]
            ],
            hint=t("downtime.scribe_hint"),
        )
        if start.scrolls
        else None,
        # With the book's activities, its research and work take their place.
        None
        if start.activities
        else started("research", text_field(), days_field(), hint=t("downtime.research_hint")),
        started("recuperate", hint=t("downtime.recuperate_hint", days=RECUPERATING_DAYS)),
        None if start.activities else started("work", hint=t("downtime.work_hint")),
        started("other", text_field(), days_field()),
    ]


def _activities(page: Page, url: str, start: StartOptions, free: int) -> Node:
    """The book's activities, each a workweek, done at once."""
    t = page.t

    def activity(kind: str, *content: Node) -> Element:
        return details(class_="downtime-start")[
            summary[
                b[t(f"downtime.kind.{kind}")],
                small(class_="muted")[f" · {t(f'downtime.hint.{kind}')}"],
            ],
            form(method="post", action=url, class_="downtime-form")[
                hidden("op", "activity"),
                hidden("kind", kind),
                content,
                button(type="submit", class_="btn btn-sm btn-primary", disabled=free < 5)[
                    t("downtime.do")
                ],
            ],
        ]

    def choice(name: str, values: Sequence[tuple[str, str]], label_key: str) -> Element:
        return select(name=name, aria_label=t(label_key))[
            [option(value=v)[text] for v, text in values]
        ]

    def number(name: str, low: int, high: int, value: int, label_key: str) -> Element:
        return label(class_="party-clock-label")[
            span[t(label_key)],
            input_el(
                type="number",
                name=name,
                min=str(low),
                max=str(high),
                value=str(value),
                inputmode="numeric",
                class_="mono party-clock-amount",
            ),
        ]

    return [
        activity(
            "carouse",
            choice(
                "class",
                [
                    (c, t(f"downtime.class.{c}", coins=coin_text(page, cost)))
                    for c, cost in CAROUSING.items()
                ],
                "downtime.social_class",
            ),
        ),
        activity(
            "crime",
            choice(
                "dc", [(str(dc), t("downtime.crime_dc", dc=dc)) for dc in CRIME_DCS], "downtime.dc"
            ),
            choice("third", [(s, t(f"skill.{s}")) for s in CRIME_THIRD], "downtime.third"),
        ),
        activity("gamble", number("stake", 10, 1000, 10, "downtime.stake")),
        activity("pit_fight"),
        activity(
            "service", choice("skill", [(s, t(f"skill.{s}")) for s in SERVICE], "downtime.skill")
        ),
        activity(
            "study",
            input_el(
                type="text",
                name="label",
                maxlength="200",
                required=True,
                placeholder=t("downtime.topic"),
                aria_label=t("downtime.topic"),
            ),
            number("extra", 0, 300, 0, "downtime.extra_gp"),
        ),
        activity(
            "job",
            choice(
                "skill",
                [(s, t("downtime.check.tools") if s == "tools" else t(f"skill.{s}")) for s in JOBS],
                "downtime.skill",
            ),
        ),
        activity(
            "sell_magic",
            select(name="item", aria_label=t("downtime.item"))[
                [
                    option(value=f"{ref}|{rarity}")[
                        f"{name_text(page, 'item', name)} ({t(f'downtime.rarity.{rarity}')})"
                    ]
                    for ref, name, rarity in start.magic
                ]
            ],
        )
        if start.magic
        else None,
        activity(
            "buy_magic",
            number("weeks", 0, 8, 0, "downtime.extra_weeks"),
            number("extra", 0, 100, 0, "downtime.extra_hundreds"),
        ),
    ]


def sheet_downtime_link(page: Page, character_id: str, days: int) -> Node:
    """The sheet's way to the downtime page, with the free days."""
    return a(href=downtime_url(character_id), class_="btn btn-sm")[
        icon("hourglass", "sm"),
        span[
            page.t.plural("downtime.short", days, n=days) if days else page.t("downtime.short_none")
        ],
    ]


__all__ = [
    "StartOptions",
    "downtime_page",
    "downtime_url",
    "project_label",
    "sheet_downtime_link",
]
