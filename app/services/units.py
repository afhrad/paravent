"""Distances and weights in the reader's units (documentation/TRANSLATION.md §6).

A per-user setting that follows the language: switching to English shows feet and
pounds, any other language metres and kilograms (:func:`units_for_language`). Only
**structured** values are converted — a speed, a spell's or weapon's range, a weight, a
sense's radius — never prose, so "a 10-foot pole" and the books' 39,315 unit mentions in
running text stay as written.

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


def units_for_language(language: str) -> str:
    """The unit system a language reads in: feet for English, metres for the rest."""
    return "feet" if language in IMPERIAL_LANGUAGES else "metres"


__all__ = ["UNIT_SYSTEMS", "UnitFormat", "units_for_language"]
