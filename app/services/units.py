"""Distances and weights in the reader's units (documentation/TRANSLATION.md §6).

A per-user setting that follows the language: switching to English shows feet and
pounds, any other language metres and kilograms (:func:`units_for_language`). Only
**structured** values are converted — a speed, a spell's or weapon's range, a weight, a
sense's radius — never prose, so "a 10-foot pole" and the books' 39,315 unit mentions in
running text stay as written. A metric reader is offered the metric value *beside* the
clear ones of them instead (:meth:`UnitFormat.prose_hints`, TRANSLATION.md §6): "60 feet"
reads "60 feet (18 m)", the text itself unchanged.

The conversion is the published German books' own, not the exact one:
**1 ft = 0.3 m** (60 ft → 18 m, 5 ft → 1.5 m, where 1/3.28 would give 18.3 m),
1 mile = 1.5 km and 1 lb = 0.5 kg. Decimals take the language's separator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

UNIT_SYSTEMS = ("feet", "metres")
FOOT = Decimal("0.3")  # metres
MILE = Decimal("1.5")  # kilometres
POUND = Decimal("0.5")  # kilograms
IMPERIAL_LANGUAGES = frozenset({"en"})
COMMA_LANGUAGES = frozenset({"de", "fr", "es", "it", "nl", "pt", "pl"})

# Our own formatting of structured values: "60 ft", "30 ft.", "20/60 ft.", "1 mi",
# "3 lb." — numbers directly followed by a unit word. Prose is never passed through this.
_UNIT_TEXT = re.compile(
    r"(?P<n>\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)\s?"
    r"(?P<u>ft\.?|feet|foot|mi\.?|miles?|lb\.?|lbs\.?|pounds?)(?![A-Za-z])"
)

# A unit mention in the books' prose, clear enough to annotate: a number written in digits
# ("60", "1,000", "20/60", "10 by 10") directly before its unit, spaced or hyphenated
# ("60 feet", "30 ft.", "10-foot", "20-foot-radius", "5 miles", "15 lb."). Not one that
# multiplies a roll ("1d6 × 10 feet"), and not spelled-out numbers or areas ("square feet").
_NUM = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
_PROSE_UNIT = re.compile(
    rf"(?<![\w.,/×*-])(?<!× )(?<!\* )"
    rf"(?:(?P<a>{_NUM})(?:\s*(?:by|×)\s*|-by-))?"
    rf"(?P<n>(?:{_NUM})(?:/(?:{_NUM}))?)"
    r"(?:\s|-)(?P<u>feet|foot|ft\.|miles?|mi\.|pounds?|lbs?\.?)"
    r"(?P<tail>(?:-[A-Za-z]+){0,2})(?![A-Za-z])"
)

# A distance typed in metres or kilometres, for reading a form back into feet.
_METRIC_TEXT = re.compile(r"(?P<n>\d+(?:[.,]\d+)?)\s?(?P<u>km|m)(?![A-Za-z])")


def _feet(metres: Decimal) -> int:
    """Metres back to feet by the books' 0.3, on a 5 ft. grid where it is that long."""
    feet = metres / FOOT
    return int((feet / 5).to_integral_value() * 5) if feet >= 5 else int(feet.to_integral_value())


@dataclass(frozen=True)
class UnitFormat:
    """How one reader sees distances and weights."""

    system: str = "feet"
    lang: str = "en"

    @property
    def metric(self) -> bool:
        """True when distances are shown in metres."""
        return self.system == "metres"

    def number(self, value: Decimal | int | float) -> str:
        """``1.5`` → ``"1.5"`` (``"1,5"`` in German), at most two decimals."""
        amount = Decimal(str(value)).quantize(Decimal("0.01")).normalize()
        text = f"{amount:f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text.replace(".", ",") if self.lang in COMMA_LANGUAGES else text

    def distance(self, feet: int | float | Decimal) -> str:
        """``30`` → ``"30 ft."`` or ``"9 m"``."""
        if not self.metric:
            return f"{self.number(feet)} ft."
        return f"{self.number(Decimal(str(feet)) * FOOT)} m"

    def weight(self, pounds: int | float | Decimal) -> str:
        """``3`` → ``"3 lb."`` or ``"1.5 kg"``."""
        if not self.metric:
            return f"{self.number(pounds)} lb."
        return f"{self.number(Decimal(str(pounds)) * POUND)} kg"

    def text(self, value: str) -> str:
        """A structured value already written with units ("Self (15 ft cone)",
        "darkvision 60 ft.", "20/60 ft.") in the reader's units. Unchanged in feet."""
        if not self.metric or not value:
            return value

        def convert(match: re.Match[str]) -> str:
            unit = match["u"].rstrip(".").lower()
            factor, symbol = (
                (FOOT, "m")
                if unit in ("ft", "feet", "foot")
                else (MILE, "km")
                if unit in ("mi", "mile", "miles")
                else (POUND, "kg")
            )
            numbers = [self.number(Decimal(n) * factor) for n in match["n"].split("/")]
            return f"{'/'.join(numbers)} {symbol}"

        return _UNIT_TEXT.sub(convert, value)

    def prose_hints(self, text: str) -> list[tuple[int, str]]:
        """Where the books' prose names a clear distance or weight, the metric value to
        show after it: ``(end, "18 m")`` for "60 feet", ``"3 × 3 m"`` for "10-by-10-foot",
        ``"6/18 m"`` for "20/60 feet". Empty for a reader in feet."""
        if not self.metric or not text:
            return []
        hints = []
        for match in _PROSE_UNIT.finditer(text):
            unit = match["u"].rstrip(".").lower()
            factor, symbol = (
                (FOOT, "m")
                if unit in ("feet", "foot", "ft")
                else (MILE, "km")
                if unit in ("mile", "miles", "mi")
                else (POUND, "kg")
            )
            numbers = [
                self.number(Decimal(n.replace(",", "")) * factor) for n in match["n"].split("/")
            ]
            value = "/".join(numbers)
            if match["a"]:
                value = f"{self.number(Decimal(match['a'].replace(',', '')) * factor)} × {value}"
            hints.append((match.end(), f"{value} {symbol}"))
        return hints


def feet_from(value: str, metric: bool) -> int | None:
    """A distance typed into a form in the reader's units, in feet: ``"9"`` metres is
    30; a decimal comma is read; None when it is no number."""
    try:
        amount = Decimal(value.strip().replace(",", "."))
    except ArithmeticError:
        return None
    if not amount.is_finite():
        return None
    return _feet(amount) if metric else int(amount.to_integral_value())


def text_in_feet(value: str) -> str:
    """A structured value typed in metres ("darkvision 18 m") as the data keeps it, in
    feet ("darkvision 60 ft."); kilometres become miles. Feet stay as they are."""

    def convert(match: re.Match[str]) -> str:
        amount = Decimal(match["n"].replace(",", "."))
        if match["u"] == "km":
            return f"{(amount / MILE).normalize():f} mi."
        return f"{_feet(amount)} ft."

    return _METRIC_TEXT.sub(convert, value)


def units_for_language(language: str) -> str:
    """The unit system a language reads in: feet for English, metres for the rest."""
    return "feet" if language in IMPERIAL_LANGUAGES else "metres"


__all__ = ["UNIT_SYSTEMS", "UnitFormat", "feet_from", "text_in_feet", "units_for_language"]
