"""A world's calendar: the clock's days as dates of the setting's year.

Setting books print their calendars as tables: twelve months and the holidays between
them. A world whose books hold one may use it: the gamemaster says what date the clock's
day is, and from then on every day reads as a date (the day, the month's name, the year
and its era), a holiday by its name, each with its season. The months and their lengths
are read from the user's table; where it prints no lengths, the length listed below
applies. A holiday the table marks as a leap day comes every fourth year; other leap days
are left to the table. The captions below are how those tables are found in the user's
corpus; Paravent carries none of them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.services.book_tables import Rows
from app.services.text import strip_tags

# The calendars the books print: (the table's caption, the books printing it, newest
# first, and the days of a month where the table gives none).
CALENDARS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("The Calendar of Harptos", ("FRHoF", "SCAG", "DMG"), 30),
    ("Calendar of Harptos", ("FRHoF",), 30),
    ("Eberron Months", ("ERLW",), 28),
    ("Exandrian Calendar", ("EGW",), 30),
    ("Meletian Calendar", ("MOT",), 30),
    ("Ravnica's Calendar", ("GGR",), 30),
)
SEASONS = ("winter", "spring", "summer", "autumn")
# A twelve-month year's seasons by position, where its table names none.
_BY_POSITION = ("winter",) * 2 + ("spring",) * 3 + ("summer",) * 3 + ("autumn",) * 3 + ("winter",)
_EARTH = {
    "january": "winter",
    "february": "winter",
    "march": "spring",
    "april": "spring",
    "may": "spring",
    "june": "summer",
    "july": "summer",
    "august": "summer",
    "september": "autumn",
    "october": "autumn",
    "november": "autumn",
    "december": "winter",
}
_DAYS = re.compile(r"^(\d+)(?:\s+days)?$", re.IGNORECASE)
_BRACKET = re.compile(r"\s*\(([^)]*)\)\s*$")


@dataclass(frozen=True)
class Month:
    """A month of the year, or a holiday between months (one day)."""

    name: str
    days: int
    holiday: bool = False
    season: str = ""
    every: int = 1  # a leap day falls only in years divisible by this (Shieldmeet: 4)

    def in_year(self, year: int) -> bool:
        return year % self.every == 0


@dataclass(frozen=True)
class CalendarDate:
    """A day as the calendar names it."""

    year: int
    month: Month
    day: int  # in its month, from 1


@dataclass(frozen=True)
class Calendar:
    """A setting's year, as one of the books prints it."""

    key: str  # the table's caption
    source: str
    months: tuple[Month, ...]

    @property
    def year_length(self) -> int:
        """The days of an ordinary year, leap days aside."""
        return sum(m.days for m in self.months if m.every == 1)

    def length_of(self, year: int) -> int:
        """The days of one year, its leap days counted."""
        return sum(m.days for m in self.months if m.in_year(year))

    @property
    def _cycle(self) -> int:
        """Years after which the leap days repeat."""
        return max((m.every for m in self.months), default=1)

    def date(self, index: int) -> CalendarDate:
        """The date of day ``index``, counted from the first day of year 0 (a leap
        year, where the calendar has leap days)."""
        cycle = self._cycle
        cycles, rest = divmod(index, sum(self.length_of(y) for y in range(cycle)))
        year = cycles * cycle
        while rest >= self.length_of(year):
            rest -= self.length_of(year)
            year += 1
        for month in self.months:
            if not month.in_year(year):
                continue
            if rest < month.days:
                return CalendarDate(year, month, rest + 1)
            rest -= month.days
        return CalendarDate(year, self.months[-1], self.months[-1].days)  # pragma: no cover

    def index(self, year: int, month: int, day: int) -> int:
        """Day index of a date (``month`` among :attr:`months`, ``day`` from 1)."""
        cycle = self._cycle
        cycles, within = divmod(year, cycle)
        days = cycles * sum(self.length_of(y) for y in range(cycle))
        days += sum(self.length_of(cycles * cycle + y) for y in range(within))
        before = sum(m.days for m in self.months[:month] if m.in_year(year))
        return days + before + day - 1

    def from_flat(self, index: int) -> int:
        """An index counted the old way, every year holding every leap day, as this
        calendar counts it: the same date."""
        flat = sum(m.days for m in self.months)
        year, rest = divmod(index, flat)
        for n, month in enumerate(self.months):
            if rest < month.days:
                if not month.in_year(year):  # a leap day that year lacks: the day after
                    return self.index(year, n, 1)
                return self.index(year, n, rest + 1)
            rest -= month.days
        return self.index(year, 0, 1)  # pragma: no cover

    def has(self, year: int, month: int, day: int) -> bool:
        """Whether a date exists: its month in range, in that year, the day within it."""
        return (
            0 <= month < len(self.months)
            and self.months[month].in_year(year)
            and 1 <= day <= self.months[month].days
        )


def months_of(rows: Rows, default_days: int) -> tuple[Month, ...]:
    """A calendar table's months and holidays (see the module's note)."""
    months: list[Month] = []
    for row in rows:
        cells = [" ".join(strip_tags(c).split()) for c in row]
        if not any(cells):
            continue
        holiday = next((c for c in cells if "holiday" in c.lower()), None)
        if holiday is not None:
            name = holiday.partition(":")[2].strip() or holiday
            season = months[-1].season if months else ""
            # A leap-day holiday (named so, or by the one calendar's word for it) comes
            # every fourth year.
            every = 4 if "shieldmeet" in name.lower() or "leap" in holiday.lower() else 1
            months.append(Month(name, 1, holiday=True, season=season, every=every))
            continue
        name = cells[1] if cells[0].isdigit() and len(cells) > 1 else cells[0]
        days = next(
            (int(m[1]) for c in cells[1:] if (m := _DAYS.match(c)) and c != name), default_days
        )
        bracket = _BRACKET.search(name)
        note = bracket[1].lower() if bracket else ""
        if bracket:
            name = name[: bracket.start()].strip()
        words = " ".join([note, *cells]).lower()
        season = _EARTH.get(note) or next(
            (s for s in SEASONS if s in words or (s == "autumn" and "fall" in words)), ""
        )
        if name:
            months.append(Month(name, max(1, days), season=season))
    if sum(1 for m in months if not m.holiday) == 12:
        # A month whose table names no season takes its place's; a holiday its month's.
        seasons = iter(_BY_POSITION)
        placed: list[Month] = []
        for month in months:
            if month.holiday:
                season = placed[-1].season if placed else ""
                placed.append(Month(month.name, 1, True, season, month.every))
            else:
                place = next(seasons)
                placed.append(Month(month.name, month.days, False, month.season or place))
        months = placed
    return tuple(months)


def date_words(dated: tuple[Calendar, int, str] | None, minute: int) -> tuple[str, str]:
    """A clock minute in English, for readers outside a page (Claude over MCP): the
    date ("14 Mirtul 1492 DR, 08:00", or "Day 3, 08:00" without a calendar) and its
    season ("" without one)."""
    day, rest = divmod(minute, 24 * 60)
    time = f"{rest // 60:02d}:{rest % 60:02d}"
    if dated is None:
        return f"Day {day + 1}, {time}", ""
    calendar, epoch, era = dated
    date = calendar.date(epoch + day)
    year = f"{date.year} {era}".strip()
    if date.month.holiday:
        return f"{date.month.name}, {year}, {time}", date.month.season
    return f"{date.day} {date.month.name} {year}, {time}", date.month.season


def calendar_from(key: str, source: str, rows: Sequence[Sequence[str]], days: int) -> Calendar:
    return Calendar(key, source, months_of([list(r) for r in rows], days))


__all__ = [
    "CALENDARS",
    "SEASONS",
    "Calendar",
    "CalendarDate",
    "Month",
    "calendar_from",
    "date_words",
    "months_of",
]
