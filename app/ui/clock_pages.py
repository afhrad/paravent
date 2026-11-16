"""A world's clock (app/services/clock.py): the gamemaster's panel on the party view,
and the time and a character's own timers on its sheet."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    label,
    li,
    option,
    p,
    select,
    small,
    span,
    summary,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import ClockTimer, Journey
from app.services.calendar import Calendar
from app.services.clock import DAY, day_of, part_of_day, time_of
from app.services.travel import CLIMATES, PACES, WATER
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.names import name_text

CLOCK_URL = "/gm/clock"
# The moves one tap makes, in minutes, with their labels' keys.
_STEPS = (
    (10, "clock.step.10m"),
    (60, "clock.step.1h"),
    (240, "clock.step.4h"),
    (1440, "clock.step.1d"),
)
_UNITS = ("minute", "hour", "day")
_ICONS = {"light": "flame", "spell": "sparkles", "custom": "bell"}


def clock_label(page: Page, minute: int, world_id: str | None = None) -> str:
    """ "Day 3 · 14:20", or the date by the world's calendar: "14 Mirtul 1492 DR ·
    14:20" (app/services/calendar.py)."""
    dated = page.calendars(world_id) if page.calendars is not None and world_id else None
    if dated is None:
        return page.t("clock.when", day=day_of(minute), time=time_of(minute))
    calendar, epoch, era = dated
    date = calendar.date(epoch + minute // DAY)
    year = f"{date.year} {era}".strip()
    if date.month.holiday:
        return page.t("clock.dated_holiday", name=date.month.name, year=year, time=time_of(minute))
    return page.t(
        "clock.dated", day=date.day, month=date.month.name, year=year, time=time_of(minute)
    )


def _season_key(page: Page, minute: int, world_id: str | None) -> str:
    dated = page.calendars(world_id) if page.calendars is not None and world_id else None
    if dated is None:
        return ""
    calendar, epoch, _era = dated
    return calendar.date(epoch + minute // DAY).month.season


def by_season(text: str, season: str) -> str:
    """The books' "light rain or light snowfall" as the season has it: the snow in
    winter, the rain otherwise; unchanged without a season."""
    parts = text.split(" or ")
    if not season or len(parts) != 2:
        return text
    snow = next((x for x in parts if "snow" in x.lower()), None)
    if snow is None:
        return text
    chosen = snow if season == "winter" else next(x for x in parts if x is not snow)
    return chosen[:1].upper() + chosen[1:] if text[:1].isupper() else chosen


def season_of(page: Page, minute: int, world_id: str | None) -> str:
    """The season of the day by the world's calendar, in the reader's words; ""
    without a calendar or a season."""
    dated = page.calendars(world_id) if page.calendars is not None and world_id else None
    if dated is None:
        return ""
    calendar, epoch, _era = dated
    season = calendar.date(epoch + minute // DAY).month.season
    return page.t(f"clock.season.{season}") if season else ""


def remaining(page: Page, minutes: int) -> str:
    """How long is left: "40 min", "7 h 20 min", "2 d 3 h"."""
    t = page.t
    minutes = max(0, minutes)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return t("clock.left_dh", d=days, h=hours) if hours else t("clock.left_d", d=days)
    if hours:
        return t("clock.left_hm", h=hours, m=mins) if mins else t("clock.left_h", h=hours)
    return t("clock.left_m", m=mins)


def timer_label(page: Page, timer: ClockTimer) -> str:
    """What runs out: the item's or spell's name in the reader's language, or the text."""
    if timer.kind == "custom":
        return timer.label
    name = timer.label.partition("|")[0]
    return name_text(page, ("item", "baseitem") if timer.kind == "light" else "spell", name)


def clock_panel(
    page: Page,
    world_id: str,
    minute: int,
    timers: Sequence[ClockTimer],
    names: Mapping[str, str],
    due: Sequence[str],
    *,
    journey: Journey | None = None,
    environments: Sequence[tuple[str, str]] = (),
    vessels: Sequence[str] = (),
    weather: bool = False,
    calendars: Sequence[Calendar] = (),
    calendar: str = "",
    era: str = "",
    mounted: bool = False,
    hazards: Mapping[str, str] | None = None,
) -> Element:
    """The time, the moves, a party rest, the timers and what is due; the party's last
    journey and the forms to travel on and to grant downtime. ``vessels`` are the ships
    of the world's books; ``weather`` whether they print a weather table; ``calendars``
    the setting calendars they print, ``calendar`` the one in use (its key) and
    ``era`` what follows its year."""
    t = page.t
    base = f"{CLOCK_URL}/{quote(world_id)}"

    def post(path: str, *content: Node, cls: str = "inline-form") -> Element:
        return form(
            method="post",
            action=f"{base}/{path}",
            hx_post=f"{base}/{path}",
            hx_target="#party-clock",
            hx_swap="outerHTML",
            class_=cls,
        )[content]

    return div(
        id="party-clock",
        class_="panel party-clock",
        data_url=f"{CLOCK_URL}/panel?{urlencode({'world': world_id})}",
    )[
        div(class_="party-clock-head")[
            icon("clock"),
            b(class_="party-clock-now mono")[clock_label(page, minute, world_id)],
            span(class_="muted")[
                " · ".join(
                    x
                    for x in (
                        t(f"clock.part.{part_of_day(minute)}"),
                        season_of(page, minute, world_id),
                    )
                    if x
                )
            ],
            div(class_="party-clock-steps")[
                [
                    post(
                        "advance",
                        hidden("minutes", str(n)),
                        button(type="submit", class_="btn btn-sm")[t(key)],
                    )
                    for n, key in _STEPS
                ],
                post(
                    "rest",
                    hidden("kind", "short"),
                    button(type="submit", class_="btn btn-sm", title=t("clock.short_rest_hint"))[
                        icon("coffee", "sm"), span[t("clock.short_rest")]
                    ],
                ),
                post(
                    "rest",
                    hidden("kind", "long"),
                    button(type="submit", class_="btn btn-sm", title=t("clock.long_rest_hint"))[
                        icon("moon", "sm"), span[t("clock.long_rest")]
                    ],
                ),
            ],
        ],
        ul(class_="party-clock-timers")[
            [_timer(page, base, x, names, minute, world_id) for x in timers]
        ]
        if timers
        else None,
        div(class_="notice party-clock-due")[
            icon("vault", "sm"), span[t("clock.bastion_due", names=", ".join(due))]
        ]
        if due
        else None,
        _journey(page, base, journey, world_id, hazards) if journey is not None else None,
        details(class_="party-clock-more party-travel")[
            summary[icon("footprints", "sm"), span[t("travel.heading")]],
            _travel_form(page, post, environments, vessels, weather, mounted),
        ],
        details(class_="party-clock-more")[
            summary[t("clock.more")],
            post(
                "advance",
                _amount(page),
                input_el(
                    type="text",
                    name="why",
                    maxlength="60",
                    placeholder=t("clock.why"),
                    aria_label=t("clock.why"),
                ),
                button(type="submit", class_="btn btn-sm")[t("clock.pass")],
                cls="party-clock-form",
            ),
            post(
                "set",
                label(class_="party-clock-label")[
                    span[t("clock.day")],
                    input_el(
                        type="number",
                        name="day",
                        min="1",
                        value=str(day_of(minute)),
                        inputmode="numeric",
                        class_="mono",
                    ),
                ],
                label(class_="party-clock-label")[
                    span[t("clock.time")],
                    input_el(type="time", name="time", value=time_of(minute), class_="mono"),
                ],
                button(type="submit", class_="btn btn-sm")[t("clock.set")],
                cls="party-clock-form",
            ),
            post(
                "timer",
                input_el(
                    type="text",
                    name="label",
                    maxlength="200",
                    required=True,
                    placeholder=t("clock.timer_label"),
                    aria_label=t("clock.timer_label"),
                ),
                _amount(page),
                button(type="submit", class_="btn btn-sm")[
                    icon("bell", "sm"), span[t("clock.add_timer")]
                ],
                cls="party-clock-form",
            ),
            post(
                "downtime",
                label(class_="party-clock-label")[
                    span[t("downtime.grant_days")],
                    input_el(
                        type="number",
                        name="days",
                        min="1",
                        max="365",
                        value="7",
                        inputmode="numeric",
                        class_="mono party-clock-amount",
                    ),
                ],
                button(type="submit", class_="btn btn-sm", title=t("downtime.grant_hint"))[
                    icon("hourglass", "sm"), span[t("downtime.grant")]
                ],
                cls="party-clock-form",
            ),
            _calendar_form(page, post, minute, world_id, calendars, calendar, era)
            if calendars
            else None,
        ],
    ]


def _calendar_form(
    page: Page,
    post: Callable[..., Element],
    minute: int,
    world_id: str,
    calendars: Sequence[Calendar],
    chosen: str,
    era: str,
) -> Element:
    """Which of the books' calendars dates the days, and what date today is."""
    t = page.t
    dated = page.calendars(world_id) if page.calendars is not None else None
    today = dated[0].date(dated[1] + minute // DAY) if dated else None
    months = dated[0].months if dated else calendars[0].months
    return post(
        "calendar",
        label(class_="party-clock-label")[
            span[t("clock.calendar")],
            select(name="calendar")[
                option(value="")[t("clock.calendar_none")],
                [
                    option(value=c.key, selected=c.key == chosen)[f"{c.key} ({c.source})"]
                    for c in calendars
                ],
            ],
        ],
        label(class_="party-clock-label")[
            span[t("clock.calendar_day")],
            input_el(
                type="number",
                name="cday",
                min="1",
                max="31",
                value=str(today.day if today else 1),
                inputmode="numeric",
                class_="mono party-clock-amount",
            ),
        ],
        select(name="cmonth", aria_label=t("clock.calendar_month"))[
            [
                option(value=str(n), selected=today is not None and today.month == m)[m.name]
                for n, m in enumerate(months)
            ]
        ],
        label(class_="party-clock-label")[
            span[t("clock.calendar_year")],
            input_el(
                type="number",
                name="cyear",
                min="0",
                max="99999",
                value=str(today.year if today else 1),
                inputmode="numeric",
                class_="mono",
            ),
        ],
        input_el(
            type="text",
            name="era",
            value=era,
            maxlength="12",
            placeholder=t("clock.calendar_era"),
            aria_label=t("clock.calendar_era"),
            class_="party-clock-amount",
        ),
        button(type="submit", class_="btn btn-sm")[t("clock.set")],
        small(class_="muted")[t("clock.calendar_hint")],
        cls="party-clock-form",
    )


def _travel_form(
    page: Page,
    post: Callable[..., Element],
    environments: Sequence[tuple[str, str]],
    vessels: Sequence[str] = (),
    weather: bool = False,
    mounted: bool = False,
) -> Element:
    """How long, how fast, through what, how watchful, with what."""
    t = page.t
    return post(
        "travel",
        input_el(
            type="number",
            name="amount",
            min="1",
            value="1",
            inputmode="numeric",
            class_="mono party-clock-amount",
            aria_label=t("clock.amount"),
        ),
        select(name="unit", aria_label=t("clock.unit"))[
            option(value="hour")[t("clock.unit.hour")],
            option(value="day", selected=True)[t("travel.unit.day")],
        ],
        select(name="pace", aria_label=t("travel.pace"))[
            [
                option(value=pace, selected=pace == "normal", title=t(f"travel.pace.{pace}_hint"))[
                    t(f"travel.pace.{pace}")
                ]
                for pace in PACES
            ]
        ],
        select(name="environment", aria_label=t("travel.environment"))[
            option(value="")[t("travel.no_table")],
            [option(value=key)[name] for key, name in environments],
        ],
        select(name="checks", aria_label=t("travel.checks"))[
            [
                option(value=str(n), selected=n == 1)[t.plural("travel.checks_n", n, n=n)]
                for n in range(4)
            ]
        ],
        select(name="vessel", aria_label=t("travel.by"))[
            option(value="")[t("travel.on_foot")],
            # Chosen first when every character of the world has a mount.
            option(value="mounted", selected=mounted)[t("travel.mounted")],
            [option(value=name)[t("travel.aboard", name=name)] for name in vessels],
        ],
        select(name="water", aria_label=t("travel.water"))[
            [option(value=w)[t(f"travel.water.{w}")] for w in WATER]
        ],
        label(class_="party-clock-label")[
            input_el(type="checkbox", name="difficult", value="1"), span[t("travel.difficult")]
        ],
        select(name="climate", aria_label=t("travel.climate"))[
            [option(value=c)[t(f"travel.climate.{c or 'mild'}")] for c in CLIMATES]
        ],
        label(class_="party-clock-label", title=t("travel.altitude_hint"))[
            input_el(type="checkbox", name="altitude", value="1"), span[t("travel.altitude")]
        ],
        label(class_="party-clock-label")[
            input_el(type="checkbox", name="rations", value="1", checked=True),
            span[t("travel.rations")],
        ],
        label(class_="party-clock-label")[
            input_el(type="checkbox", name="rest", value="1", checked=True),
            span[t("travel.rest")],
        ],
        label(class_="party-clock-label")[
            input_el(type="checkbox", name="night", value="1"), span[t("travel.night_check")]
        ],
        label(class_="party-clock-label", title=t("travel.around_hint"))[
            input_el(type="checkbox", name="around", value="1"), span[t("travel.around")]
        ]
        if vessels
        else None,
        label(class_="party-clock-label")[
            input_el(type="checkbox", name="weather", value="1", checked=True),
            span[t("travel.weather")],
        ]
        if weather
        else None,
        button(type="submit", class_="btn btn-sm btn-primary")[
            icon("footprints", "sm"), span[t("travel.go")]
        ],
        p(class_="muted party-travel-hint")[t("travel.hint")],
        cls="party-clock-form party-travel-form",
    )


def _part(weather: str, index: int) -> str:
    """One part of a day's weather ("temperature|wind|precipitation"), lower case."""
    return ([*weather.split("|"), "", "", ""][index]).lower()


def _journey(
    page: Page,
    base: str,
    journey: Journey,
    world_id: str | None = None,
    hazards: Mapping[str, str] | None = None,
) -> Element:
    """The party's last stretch of road: how far, what was eaten, what stopped it;
    ``hazards`` link the books' hazards it met (their pages, by name)."""
    t = page.t
    spent = remaining(page, journey.hours * 60)
    lines: list[Node] = [
        p[
            b[t("travel.went", span=spent, distance=page.u.miles(journey.miles))],
            " · ",
            span(class_="muted")[
                t("travel.aboard", name=journey.vessel)
                if journey.vessel
                else t(f"travel.pace.{journey.pace}")
            ],
            span(class_="muted")[f" · {t('travel.mounted')}"] if journey.mounted else None,
            span(class_="muted")[f" · {t('travel.difficult')}"] if journey.difficult else None,
            span(class_="muted")[f" · {journey.environment.partition('|')[0]}"]
            if journey.environment
            else None,
        ]
    ]
    if journey.rations:
        lines.append(
            p(class_="muted")[t.plural("travel.rations_eaten", journey.rations, n=journey.rations)]
        )
    if journey.hungry:
        lines.append(
            p[icon("triangle-alert", "sm"), t("travel.hungry", names=", ".join(journey.hungry))]
        )
    if journey.thirsty:
        lines.append(
            p[icon("triangle-alert", "sm"), t("travel.thirsty", names=", ".join(journey.thirsty))]
        )
    for day, weather in enumerate(journey.weather, start=1):
        temperature, wind, rain = [*weather.split("|"), "", ""][:3]
        # The weather follows the season of the day it fell on, by the world's calendar.
        season_key = _season_key(page, journey.started + (day - 1) * DAY, world_id)
        rain = by_season(rain, season_key)
        lines.append(
            p(class_="muted party-travel-weather")[
                icon("sun", "sm"),
                span(class_="pill")[page.t(f"clock.season.{season_key}")] if season_key else None,
                t(
                    "travel.weather_line",
                    day=day,
                    temperature=temperature,
                    wind=wind,
                    rain=rain,
                ),
            ]
        )
    if journey.climate or journey.altitude:
        lines.append(
            p[
                icon("snowflake" if journey.climate == "cold" else "flame", "sm"),
                " ",
                ", ".join(
                    x
                    for x in (
                        t(f"travel.climate.{journey.climate}") if journey.climate else "",
                        t("travel.altitude") if journey.altitude else "",
                    )
                    if x
                ),
                ": ",
                ", ".join(
                    t.plural(
                        "travel.exposed_one",
                        int(n.partition(":")[2] or 0),
                        name=n.partition(":")[0],
                        n=n.partition(":")[2],
                    )
                    for n in journey.exposed
                )
                or t("travel.exposed_none"),
            ]
        )
    met = [
        name
        for name, hit in (
            ("Extreme Cold", journey.climate == "cold"),
            ("Extreme Heat", journey.climate == "heat"),
            ("High Altitude", journey.altitude),
            ("Strong Wind", any("strong" in _part(w, 1) for w in journey.weather)),
            ("Heavy Precipitation", any("heavy" in _part(w, 2) for w in journey.weather)),
        )
        if hit and (hazards or {}).get(name)
    ]
    if met:
        lines.append(
            p(class_="muted")[
                icon("triangle-alert", "sm"),
                " ",
                t("travel.hazards"),
                " ",
                [
                    (
                        ", " if n else "",
                        a(href=(hazards or {})[name])[page.t.term("hazard", name) or name],
                    )
                    for n, name in enumerate(met)
                ],
            ]
        )
    if journey.exhausted:
        lines.append(
            p[
                icon("triangle-alert", "sm"),
                t("travel.exhausted", names=", ".join(journey.exhausted)),
            ]
        )
    if journey.check is not None:
        left = remaining(page, max(0, journey.planned - journey.hours) * 60)
        lines.append(
            div(class_="party-travel-encounter")[
                p[
                    icon("swords", "sm"),
                    b[
                        t(
                            "travel.encounter_night" if journey.night else "travel.encounter",
                            when=clock_label(page, journey.ended, world_id),
                        )
                    ],
                    span(class_="muted")[f" · d20 {journey.check}"],
                    span(class_="muted")[f" · {t('travel.left', span=left)}"]
                    if journey.planned > journey.hours
                    else None,
                ],
                p(class_="muted")[journey.encounter]
                if journey.encounter
                else p(class_="muted")[t("travel.your_call")],
                p[
                    b[
                        ", ".join(
                            f"{m.count} × {name_text(page, 'monster', m.name)}"
                            for m in journey.monsters
                        )
                    ]
                ]
                if journey.monsters
                else None,
                p(class_="muted")[t("travel.unresolved", names=", ".join(journey.unresolved))]
                if journey.unresolved
                else None,
                a(href=f"/gm/encounters/{journey.encounter_id}", class_="btn btn-sm")[
                    icon("swords", "sm"), span[t("travel.open_encounter")]
                ]
                if journey.encounter_id
                else form(
                    method="post",
                    action=f"{base}/travel_encounter",
                    class_="inline-form",
                )[
                    button(type="submit", class_="btn btn-sm")[
                        icon("plus", "sm"), span[t("travel.make_encounter")]
                    ]
                ]
                if journey.monsters
                else None,
            ]
        )
    return div(class_="party-journey")[
        lines,
        form(
            method="post",
            action=f"{base}/travel_clear",
            hx_post=f"{base}/travel_clear",
            hx_target="#party-clock",
            hx_swap="outerHTML",
            class_="inline-form party-journey-clear",
        )[
            button(
                type="submit",
                class_="btn btn-sm btn-ghost",
                title=t("travel.clear"),
                aria_label=t("travel.clear"),
            )[icon("x", "sm")]
        ],
    ]


def _amount(page: Page) -> list[Node]:
    t = page.t
    return [
        input_el(
            type="number",
            name="amount",
            min="1",
            value="1",
            inputmode="numeric",
            class_="mono party-clock-amount",
            aria_label=t("clock.amount"),
        ),
        select(name="unit", aria_label=t("clock.unit"))[
            [option(value=u, selected=u == "hour")[t(f"clock.unit.{u}")] for u in _UNITS]
        ],
    ]


def _timer(
    page: Page,
    base: str,
    timer: ClockTimer,
    names: Mapping[str, str],
    minute: int,
    world_id: str | None = None,
) -> Element:
    t = page.t
    who = names.get(timer.character_id or "")
    return li(class_=f"party-clock-timer {timer.kind}")[
        icon(_ICONS[timer.kind], "sm"),
        span[timer_label(page, timer)],
        small(class_="muted")[who] if who else None,
        span(class_="pill neutral mono", title=clock_label(page, timer.ends, world_id))[
            remaining(page, timer.ends - minute)
        ],
        form(
            method="post",
            action=f"{base}/timer/{timer.id}/stop",
            hx_post=f"{base}/timer/{timer.id}/stop",
            hx_target="#party-clock",
            hx_swap="outerHTML",
            class_="inline-form",
        )[
            button(
                type="submit",
                class_="btn btn-sm btn-ghost",
                title=t("clock.stop"),
                aria_label=t("clock.stop"),
            )[icon("x", "sm")]
        ],
    ]


def sheet_time(page: Page, minute: int, world_id: str | None = None) -> Element:
    """The world's time in the sheet's head."""
    title = " · ".join(
        x
        for x in (page.t(f"clock.part.{part_of_day(minute)}"), season_of(page, minute, world_id))
        if x
    )
    return span(class_="pill neutral mono cs-clock", title=title)[
        icon("clock", "sm"), clock_label(page, minute, world_id)
    ]


def sheet_timers(
    page: Page, character_id: str, minute: int, timers: Sequence[ClockTimer]
) -> Element | None:
    """A character's lit lights and running spells, each with what is left and a way to
    put it out."""
    if not timers:
        return None
    t = page.t
    return ul(class_="cs-timers")[
        [
            li(class_=f"cs-timer {x.kind}")[
                icon(_ICONS[x.kind], "sm"),
                span[timer_label(page, x)],
                span(class_="mono muted", title=clock_label(page, x.ends))[
                    remaining(page, x.ends - minute)
                ],
                form(
                    method="post",
                    action=f"/characters/{character_id}/timer/{x.id}/stop",
                    hx_post=f"/characters/{character_id}/timer/{x.id}/stop",
                    hx_swap="none",
                    class_="inline-form",
                )[
                    button(
                        type="submit",
                        class_="btn btn-sm btn-ghost",
                        title=t("clock.put_out" if x.kind == "light" else "clock.stop"),
                        aria_label=t("clock.put_out" if x.kind == "light" else "clock.stop"),
                    )[icon("x", "sm")]
                ],
            ]
            for x in timers
        ]
    ]


__all__ = [
    "CLOCK_URL",
    "clock_label",
    "clock_panel",
    "remaining",
    "sheet_time",
    "sheet_timers",
    "timer_label",
]
