"""A world's calendar: the clock's days as dates of the setting's year.

The books print their settings' calendars as tables: the Forgotten Realms' Calendar of
Harptos (twelve months of thirty days and the annual holidays between them), Eberron's
twelve months of twenty-eight days, Exandria's, Theros's and Ravnica's. A world whose
books hold one may use it: the gamemaster says what date the clock's day is, and from
then on every day reads as a date ("14 Mirtul 1492 DR"), a holiday by its name, each
with its season. The months and their lengths are read from the table; where it prints
no lengths, the setting's (thirty, Eberron's twenty-eight) apply. Leap days are left to
the table.
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
        return sum(m.days for m in self.months)

    def date(self, index: int) -> CalendarDate:
        """The date of day ``index``, counted from the first day of year 0."""
        year, rest = divmod(index, self.year_length)
        for month in self.months:
            if rest < month.days:
                return CalendarDate(year, month, rest + 1)
            rest -= month.days
        return CalendarDate(year, self.months[-1], self.months[-1].days)  # pragma: no cover

    def index(self, year: int, month: int, day: int) -> int:
        """Day index of a date (``month`` among :attr:`months`, ``day`` from 1)."""
        before = sum(m.days for m in self.months[:month])
        return year * self.year_length + before + day - 1


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
            months.append(Month(name, 1, holiday=True, season=season))
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
                placed.append(Month(month.name, 1, True, placed[-1].season if placed else ""))
            else:
                place = next(seasons)
                placed.append(Month(month.name, month.days, False, month.season or place))
        months = placed
    return tuple(months)


def calendar_from(key: str, source: str, rows: Sequence[Sequence[str]], days: int) -> Calendar:
    return Calendar(key, source, months_of([list(r) for r in rows], days))


__all__ = [
    "CALENDARS",
    "SEASONS",
    "Calendar",
    "CalendarDate",
    "Month",
    "calendar_from",
    "months_of",
]
