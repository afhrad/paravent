"""A world's clock (app/services/clock.py): the gamemaster's panel on the party view,
and the time and a character's own timers on its sheet."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    div,
    form,
    label,
    li,
    option,
    p,
    select,
    small,
    span,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import ClockTimer, Delve, Journey
from app.services.calendar import Calendar
from app.services.clock import DAY, day_of, part_of_day, time_of
from app.services.delve import CHANCES, INTERVALS
from app.services.features import FEATURES as ALL_FEATURES
from app.services.travel import ABUNDANCE, CLIMATES, PACES, WATER
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.names import name_text
from app.ui.tool_dialog import tool_button, tool_dialog

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


# The guides' weather words (the 2014 and 2024 tables' cells, lower case, dice rolled):
# a fixed vocabulary, so the reader's language has them; anything else stays as printed.
_WEATHER_WORDS = {
    "normal for the season": "travel.wx.normal",
    "none": "travel.wx.none",
    "no wind": "travel.wx.none",
    "no precipitation": "travel.wx.none",
    "light": "travel.wx.light",
    "light wind": "travel.wx.light",
    "strong": "travel.wx.strong",
    "strong wind": "travel.wx.strong",
    "light rain": "travel.wx.light_rain",
    "light snowfall": "travel.wx.light_snow",
    "heavy rain": "travel.wx.heavy_rain",
    "heavy snowfall": "travel.wx.heavy_snow",
}
_DEGREES = re.compile(r"^(\d+) degrees fahrenheit (colder|hotter)(?: than normal)?$")


def weather_words(page: Page, text: str) -> str:
    """A cell of the guides' weather table in the reader's words; a temperature that
    differs from the season's in degrees Celsius for a reader in metres."""
    plain = text.strip().lower()
    if " or " in plain:
        return page.t("travel.wx.or").join(weather_words(page, x) for x in plain.split(" or "))
    if plain in _WEATHER_WORDS:
        return page.t(_WEATHER_WORDS[plain])
    found = _DEGREES.match(plain)
    if found is None:
        return text
    n = int(found.group(1))
    degrees = f"{round(n * 5 / 9)} °C" if page.u.metric else f"{n} °F"
    return page.t(f"travel.wx.{found.group(2)}", degrees=degrees)


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
    terrains: Sequence[str] = (),
    abundance: bool = False,
    rules: str = "classic",
    rests: tuple[int, int] = (60, 480),
    delve: Delve | None = None,
    delve_table: str = "",
    features: frozenset[str] = frozenset(ALL_FEATURES),
) -> Element:
    """The time, the moves, a party rest, the timers and what is due; the party's last
    journey and the forms to travel on and to grant downtime. ``vessels`` are the ships
    of the world's books; ``weather`` whether they print a weather table; ``calendars``
    the setting calendars they print, ``calendar`` the one in use (its key) and
    ``era`` what follows its year; ``terrains`` those the world's guide has travel
    rules for, ``abundance`` whether foraging's DC is chosen by the land's abundance
    (2014), ``rules`` the world's (``one`` for 2024); ``rests`` how long a party's short
    and long rest take (a rest variant, app/services/character_rules.py); ``delve`` the
    party's exploring, ``delve_table`` its encounter table's name; ``features`` those the
    world plays with (app/services/features.py): the road, survival, the dungeon,
    bastions and downtime show only when on."""
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
                    button(
                        type="submit",
                        class_="btn btn-sm",
                        title=t("clock.short_rest_hint", time=remaining(page, rests[0])),
                    )[icon("coffee", "sm"), span[t("clock.short_rest")]],
                ),
                post(
                    "rest",
                    hidden("kind", "long"),
                    button(
                        type="submit",
                        class_="btn btn-sm",
                        title=t("clock.long_rest_hint", time=remaining(page, rests[1])),
                    )[icon("moon", "sm"), span[t("clock.long_rest")]],
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
        if due and "bastions" in features
        else None,
        _journey(page, base, journey, world_id, hazards)
        if journey is not None and "travel" in features
        else None,
        _delve(page, base, post, delve, delve_table, world_id)
        if delve is not None and "dungeon" in features
        else None,
        div(class_="tool-row")[
            tool_button(page, "travel", t("travel.heading"), "footprints")
            if "travel" in features
            else None,
            tool_button(page, "delve", t("delve.heading"), "door-open")
            if "dungeon" in features
            else None,
            tool_button(page, "time", t("clock.more_title"), "clock"),
        ],
        tool_dialog(page, "delve", t("delve.heading"), _delve_form(page, post, environments, delve))
        if "dungeon" in features
        else None,
        tool_dialog(
            page,
            "travel",
            t("travel.heading"),
            _travel_form(
                page,
                post,
                environments,
                vessels,
                weather,
                mounted,
                terrains=terrains,
                party=names,
                abundance=abundance,
                rules=rules,
                survival="survival" in features,
            ),
        )
        if "travel" in features
        else None,
        tool_dialog(
            page,
            "time",
            t("clock.more_title"),
            _time_forms(
                page,
                post,
                minute,
                world_id,
                calendars,
                calendar,
                era,
                downtime="downtime" in features,
            ),
        ),
    ]


def _field(ident: str, caption: str, control: Node, *, wide: bool = False) -> Element:
    """A labelled field of a tool's dialog; a ``wide`` one (a number with its unit) takes
    two columns where there is room."""
    return div(class_="field tool-span" if wide else "field")[label(for_=ident)[caption], control]


def _how_long(page: Page, prefix: str, units: Node = None) -> Element:
    """How long, as a number and its unit side by side; ``units`` replaces the unit
    choice (travel's hours or travelling days)."""
    t = page.t
    return _field(
        f"{prefix}-amount",
        t("clock.amount"),
        wide=True,
        control=div(class_="tool-inline")[
            input_el(
                id=f"{prefix}-amount",
                type="number",
                name="amount",
                min="1",
                value="1",
                inputmode="numeric",
                class_="mono",
            ),
            units
            if units is not None
            else select(name="unit", aria_label=t("clock.unit"))[
                [option(value=u, selected=u == "hour")[t(f"clock.unit.{u}")] for u in _UNITS]
            ],
        ],
    )


def _time_forms(
    page: Page,
    post: Callable[..., Element],
    minute: int,
    world_id: str,
    calendars: Sequence[Calendar],
    calendar: str,
    era: str,
    *,
    downtime: bool = True,
) -> Node:
    """Let time pass, set the clock, a reminder, downtime days (where the world plays
    with downtime) and the calendar, each a form of its own with labelled fields."""
    t = page.t
    return [
        post(
            "advance",
            div(class_="tool-grid")[
                _how_long(page, "tp"),
                _field(
                    "tp-why",
                    t("clock.why"),
                    input_el(id="tp-why", type="text", name="why", maxlength="60"),
                ),
            ],
            div(class_="form-actions")[button(type="submit", class_="btn")[t("clock.pass")]],
            cls="tool-section",
        ),
        post(
            "set",
            div(class_="tool-grid")[
                _field(
                    "ts-day",
                    t("clock.day"),
                    input_el(
                        id="ts-day",
                        type="number",
                        name="day",
                        min="1",
                        value=str(day_of(minute)),
                        inputmode="numeric",
                        class_="mono",
                    ),
                ),
                _field(
                    "ts-time",
                    t("clock.time"),
                    input_el(
                        id="ts-time", type="time", name="time", value=time_of(minute), class_="mono"
                    ),
                ),
            ],
            div(class_="form-actions")[button(type="submit", class_="btn")[t("clock.set")]],
            cls="tool-section",
        ),
        post(
            "timer",
            div(class_="tool-grid")[
                _field(
                    "tt-label",
                    t("clock.timer_label"),
                    input_el(
                        id="tt-label", type="text", name="label", maxlength="200", required=True
                    ),
                ),
                _how_long(page, "tt"),
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn")[icon("bell", "sm"), span[t("clock.add_timer")]]
            ],
            cls="tool-section",
        ),
        post(
            "downtime",
            div(class_="tool-grid")[
                _field(
                    "td-days",
                    t("downtime.grant_days"),
                    input_el(
                        id="td-days",
                        type="number",
                        name="days",
                        min="1",
                        max="365",
                        value="7",
                        inputmode="numeric",
                        class_="mono",
                    ),
                ),
            ],
            p(class_="field-hint")[t("downtime.grant_hint")],
            div(class_="form-actions")[
                button(type="submit", class_="btn")[
                    icon("hourglass", "sm"), span[t("downtime.grant")]
                ]
            ],
            cls="tool-section",
        )
        if downtime
        else None,
        _calendar_form(page, post, minute, world_id, calendars, calendar, era)
        if calendars
        else None,
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
        div(class_="tool-grid")[
            _field(
                "tc-calendar",
                t("clock.calendar"),
                select(id="tc-calendar", name="calendar")[
                    option(value="")[t("clock.calendar_none")],
                    [
                        option(value=c.key, selected=c.key == chosen)[f"{c.key} ({c.source})"]
                        for c in calendars
                    ],
                ],
            ),
            _field(
                "tc-day",
                t("clock.calendar_day"),
                wide=True,
                control=div(class_="tool-inline")[
                    input_el(
                        id="tc-day",
                        type="number",
                        name="cday",
                        min="1",
                        max="31",
                        value=str(today.day if today else 1),
                        inputmode="numeric",
                        class_="mono",
                    ),
                    select(name="cmonth", aria_label=t("clock.calendar_month"))[
                        [
                            option(value=str(n), selected=today is not None and today.month == m)[
                                m.name
                            ]
                            for n, m in enumerate(months)
                        ]
                    ],
                ],
            ),
            _field(
                "tc-year",
                t("clock.calendar_year"),
                input_el(
                    id="tc-year",
                    type="number",
                    name="cyear",
                    min="0",
                    max="99999",
                    value=str(today.year if today else 1),
                    inputmode="numeric",
                    class_="mono",
                ),
            ),
            _field(
                "tc-era",
                t("clock.calendar_era"),
                input_el(id="tc-era", type="text", name="era", value=era, maxlength="12"),
            ),
        ],
        p(class_="field-hint")[t("clock.calendar_hint")],
        div(class_="form-actions")[button(type="submit", class_="btn")[t("clock.set")]],
        cls="tool-section",
    )


def _travel_form(
    page: Page,
    post: Callable[..., Element],
    environments: Sequence[tuple[str, str]],
    vessels: Sequence[str] = (),
    weather: bool = False,
    mounted: bool = False,
    *,
    terrains: Sequence[str] = (),
    party: Mapping[str, str] | None = None,
    abundance: bool = False,
    rules: str = "classic",
    survival: bool = True,
) -> Element:
    """How long, how fast, through what, how watchful, with what; with ``terrains``, who
    navigates and forages there (of ``party``, characters' names by id). Without
    ``survival`` the party needs no rations or water and nobody forages."""
    t = page.t

    def check(name: str, caption: str, *, on: bool = False, title: str | None = None) -> Element:
        return label(class_="check-inline", title=title)[
            input_el(type="checkbox", name=name, value="1", checked=on), span[caption]
        ]

    return post(
        "travel",
        div(class_="tool-grid")[
            _how_long(
                page,
                "tv",
                select(name="unit", aria_label=t("clock.unit"))[
                    option(value="hour")[t("clock.unit.hour")],
                    option(value="day", selected=True)[t("travel.unit.day")],
                ],
            ),
            _field(
                "tv-pace",
                t("travel.pace"),
                select(id="tv-pace", name="pace")[
                    [
                        option(
                            value=pace,
                            selected=pace == "normal",
                            title=t(f"travel.pace.{pace}_hint"),
                        )[t(f"travel.pace.{pace}")]
                        for pace in PACES
                    ]
                ],
            ),
            _field(
                "tv-by",
                t("travel.by"),
                select(id="tv-by", name="vessel")[
                    option(value="")[t("travel.on_foot")],
                    # Chosen first when every character of the world has a mount.
                    option(value="mounted", selected=mounted)[t("travel.mounted")],
                    [option(value=name)[t("travel.aboard", name=name)] for name in vessels],
                ],
            ),
            _field(
                "tv-env",
                t("travel.environment"),
                select(id="tv-env", name="environment")[
                    option(value="")[t("travel.no_table")],
                    [option(value=key)[name] for key, name in environments],
                ],
            ),
            _field(
                "tv-checks",
                t("travel.checks"),
                select(id="tv-checks", name="checks")[
                    [
                        option(value=str(n), selected=n == 1)[t.plural("travel.checks_n", n, n=n)]
                        for n in range(4)
                    ]
                ],
            ),
            _field(
                "tv-water",
                t("travel.water"),
                select(id="tv-water", name="water")[
                    [option(value=w)[t(f"travel.water.{w}")] for w in WATER]
                ],
            )
            if survival
            else None,
            _field(
                "tv-climate",
                t("travel.climate"),
                select(id="tv-climate", name="climate")[
                    [option(value=c)[t(f"travel.climate.{c or 'mild'}")] for c in CLIMATES]
                ],
            ),
        ],
        div(class_="tool-checks")[
            check("difficult", t("travel.difficult")),
            check("altitude", t("travel.altitude"), title=t("travel.altitude_hint")),
            check("rations", t("travel.rations"), on=True) if survival else None,
            check("rest", t("travel.rest"), on=True),
            check("night", t("travel.night_check")),
            check("around", t("travel.around"), title=t("travel.around_hint")) if vessels else None,
            check("weather", t("travel.weather"), on=True) if weather else None,
        ],
        _terrain_fields(page, terrains, party or {}, abundance and survival, rules, survival)
        if terrains
        else None,
        p(class_="field-hint")[t("travel.hint")],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("footprints", "sm"), span[t("travel.go")]
            ]
        ],
        cls="tool-section party-travel-form",
    )


def _terrain_fields(
    page: Page,
    terrains: Sequence[str],
    party: Mapping[str, str],
    abundance: bool,
    rules: str,
    survival: bool = True,
) -> Element:
    """The terrain, its navigator and foragers (with ``survival``), and moving
    stealthily."""
    t = page.t

    def check(name: str, caption: str, value: str = "1", title: str | None = None) -> Element:
        return label(class_="check-inline", title=title)[
            input_el(type="checkbox", name=name, value=value), span[caption]
        ]

    return div(class_="tool-section-part")[
        div(class_="tool-grid")[
            _field(
                "tv-terrain",
                t("travel.terrain"),
                select(id="tv-terrain", name="terrain")[
                    option(value="")[t("travel.terrain.from_table")],
                    [option(value=n)[t(f"travel.terrain.{n.lower()}")] for n in terrains],
                ],
            ),
            _field(
                "tv-navigator",
                t("travel.navigator"),
                select(id="tv-navigator", name="navigator")[
                    option(value="")[t("travel.navigator.none")],
                    [option(value=cid)[name] for cid, name in party.items()],
                ],
            ),
            _field(
                "tv-abundance",
                t("travel.abundance"),
                select(id="tv-abundance", name="abundance")[
                    [
                        option(value=a, selected=a == "limited")[t(f"travel.abundance.{a}")]
                        for a in ABUNDANCE
                    ]
                ],
            )
            if abundance
            else None,
        ],
        div(class_="tool-checks")[
            check(
                "helped",
                t("travel.helped"),
                title=t("travel.helped_hint_one" if rules == "one" else "travel.helped_hint"),
            ),
            check(
                "stealthy",
                t("travel.stealthy"),
                title=t("travel.stealthy_hint_one" if rules == "one" else "travel.stealthy_hint"),
            ),
        ],
        div(class_="tool-checks")[
            span(class_="muted")[t("travel.foragers")],
            [check("forager", name, value=cid) for cid, name in party.items()],
        ]
        if party and survival
        else None,
    ]


def spotting(page: Page, journey: Journey) -> list[str]:
    """What the road's encounter came with, as sentences: its distance, who it would
    surprise if the creatures lay in wait, whom a stealthy party would catch unaware."""
    t = page.t
    out = []
    if journey.distance:
        out.append(t("travel.distance", distance=page.u.distance(journey.distance)))
    if journey.lurk_stealth is not None:
        out.append(
            t(
                "travel.ambush",
                stealth=journey.lurk_stealth,
                names=", ".join(journey.ambush) or t("travel.nobody"),
            )
        )
    if journey.party_stealth is not None:
        out.append(
            t(
                "travel.unaware",
                stealth=journey.party_stealth,
                names=", ".join(str(name_text(page, "monster", n)) for n in journey.unaware)
                or t("travel.nobody"),
            )
        )
    return out


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
            span(class_="muted")[" · ", t(f"travel.terrain.{journey.terrain.lower()}")]
            if journey.terrain
            else None,
        ]
    ]
    if journey.slowed:
        lines.append(
            p(class_="muted")[
                t(
                    "travel.slowed",
                    terrain=t(f"travel.terrain.{journey.terrain.lower()}"),
                    pace=t(f"travel.pace.{journey.pace}"),
                )
            ]
        )
    if journey.navigator:
        checks = ", ".join(
            f"{total} {'✓' if int(total) >= int(dc) else '✗'}"
            for total, _, dc in (c.partition("/") for c in journey.navigation)
        )
        lines.append(
            p[
                icon("compass", "sm"),
                " ",
                t.plural(
                    "travel.lost",
                    journey.lost_days,
                    name=journey.navigator,
                    n=journey.lost_days,
                    distance=page.u.miles(journey.astray),
                )
                if journey.lost_days
                else t("travel.on_course", name=journey.navigator),
                span(class_="muted")[f" · {t('travel.navigation_checks', checks=checks)}"]
                if checks
                else None,
            ]
        )
    if journey.foraged_food or journey.foraged_water:
        lines.append(
            p(class_="muted")[
                icon("backpack", "sm"),
                " ",
                t(
                    "travel.foraged",
                    food=page.u.weight(journey.foraged_food),
                    water=page.u.gallons(journey.foraged_water),
                    meals=journey.foraged_meals,
                ),
            ]
        )
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
                    temperature=weather_words(page, temperature),
                    wind=weather_words(page, wind),
                    rain=weather_words(page, rain),
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
                    span(class_="muted")[f" · {t('rules.dice_letter')}20 {journey.check}"],
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
                [p(class_="muted")[line] for line in spotting(page, journey)],
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


def _delve_form(
    page: Page,
    post: Callable[..., Element],
    environments: Sequence[tuple[str, str]],
    delve: Delve | None,
) -> Element:
    """Explore a dungeon: the encounter table, how often a check is rolled and what it
    comes up on; changing them keeps the checks already rolled."""
    t = page.t
    table = delve.environment if delve is not None else ""
    every = delve.every if delve is not None else INTERVALS[0]
    on = delve.on if delve is not None else 18
    return post(
        "delve",
        p(class_="field-hint")[t("delve.intro")],
        div(class_="tool-grid")[
            _field(
                "dv-env",
                t("travel.environment"),
                select(id="dv-env", name="environment")[
                    option(value="", selected=not table)[t("delve.no_table")],
                    [option(value=k, selected=k == table)[n] for k, n in environments],
                ],
                wide=True,
            ),
            _field(
                "dv-every",
                t("delve.every_label"),
                select(id="dv-every", name="every")[
                    [
                        option(value=str(n), selected=n == every)[remaining(page, n)]
                        for n in sorted({*INTERVALS, every})
                    ]
                ],
            ),
            _field(
                "dv-on",
                t("delve.on_label"),
                select(id="dv-on", name="on")[
                    [
                        option(value=str(n), selected=n == on)[
                            t("delve.chance", on=n, percent=(21 - n) * 5)
                        ]
                        for n in CHANCES
                    ]
                ],
            ),
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("door-open", "sm"),
                span[t("delve.change" if delve is not None else "delve.start")],
            ]
        ],
        cls="tool-section",
    )


def _delve(
    page: Page,
    base: str,
    post: Callable[..., Element],
    delve: Delve,
    table: str,
    world_id: str,
) -> Element:
    """The party exploring: the table, the interval, the checks rolled, the next one
    due, and a check that came up, to make an encounter of or put aside."""
    t = page.t
    found = delve.found
    head = [
        p[icon("door-open", "sm"), b[t("delve.exploring")]],
        p(class_="muted")[
            " · ".join(
                (
                    table or t("delve.no_table"),
                    t("delve.every", span=remaining(page, delve.every)),
                    f"{t('rules.dice_letter')}20\u00a0≥\u00a0{delve.on}",
                    t.plural("delve.rolled", delve.rolled, n=delve.rolled),
                )
            )
        ],
    ]
    if found is None:
        body: Node = div(class_="party-delve-next")[
            span(class_="muted")[
                t("delve.next", when=clock_label(page, delve.checked + delve.every, world_id))
            ],
            post(
                "delve_check",
                button(type="submit", class_="btn btn-sm")[
                    icon("dices", "sm"), span[t("delve.check_now")]
                ],
            ),
        ]
    else:
        body = div(class_="party-travel-encounter")[
            p[
                icon("swords", "sm"),
                b[t("delve.encounter", when=clock_label(page, found.at, world_id))],
            ],
            p(class_="muted")[
                f"{t('rules.dice_letter')}20 {found.check} · ",
                found.encounter or t("travel.your_call"),
            ],
            p[
                b[
                    ", ".join(
                        f"{m.count} × {name_text(page, 'monster', m.name)}" for m in found.monsters
                    )
                ]
            ]
            if found.monsters
            else None,
            p(class_="muted")[t("travel.unresolved", names=", ".join(found.unresolved))]
            if found.unresolved
            else None,
            p(class_="muted")[t("delve.held")],
            div(class_="party-delve-next")[
                a(href=f"/gm/encounters/{found.encounter_id}", class_="btn btn-sm")[
                    icon("swords", "sm"), span[t("travel.open_encounter")]
                ]
                if found.encounter_id
                else form(method="post", action=f"{base}/delve_encounter", class_="inline-form")[
                    button(type="submit", class_="btn btn-sm")[
                        icon("plus", "sm"), span[t("travel.make_encounter")]
                    ]
                ]
                if found.monsters
                else None,
                post(
                    "delve_clear",
                    button(type="submit", class_="btn btn-sm")[
                        icon("check", "sm"), span[t("delve.clear")]
                    ],
                ),
            ],
        ]
    return div(class_="party-journey party-delve")[
        head,
        body,
        post(
            "delve_leave",
            button(
                type="submit",
                class_="btn btn-sm btn-ghost",
                title=t("delve.leave"),
                aria_label=t("delve.leave"),
            )[icon("x", "sm")],
            cls="inline-form party-journey-clear",
        ),
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
    "spotting",
    "CLOCK_URL",
    "clock_label",
    "clock_panel",
    "remaining",
    "sheet_time",
    "sheet_timers",
    "timer_label",
]
