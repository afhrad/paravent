"""Hazards the gamemaster improvises, scaled to the party's level.

A collapsing ceiling, a burst of flame, a fall: the guides price such harm by the party's
level and how bad it should be. The 2014 guide's *Damage Severity by Level* (setback,
dangerous, deadly) with its *Trap Save DCs and Attack Bonuses*, or the 2024 guide's
*Damage Severity and Level* (nuisance, deadly) with the DCs of its *Building a Trap*
table, read from the world's books. A fall is the handbooks' 1d6 per 10 feet, at most
20d6. The hazard becomes a trap's effect (app/services/traps.py) and is sprung as one:
the saves rolled on the sheets or by the players, damage with each character's
defences, logged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.services.traps import ABILITIES, TrapEffect, TrapSave

# The kinds offered, each with the damage and the save it suggests; the gamemaster may
# change either. ``falling`` is worked out from the height instead.
KINDS: dict[str, tuple[str, str]] = {
    "collapse": ("bludgeoning", "dex"),
    "fire": ("fire", "dex"),
    "lightning": ("lightning", "dex"),
    "acid": ("acid", "dex"),
    "cold": ("cold", "con"),
    "poison": ("poison", "con"),
    "psychic": ("psychic", "wis"),
    "thunder": ("thunder", "con"),
    "necrotic": ("necrotic", "con"),
    "falling": ("bludgeoning", ""),
}
DAMAGE_TYPES = (
    "acid",
    "bludgeoning",
    "cold",
    "fire",
    "force",
    "lightning",
    "necrotic",
    "piercing",
    "poison",
    "psychic",
    "radiant",
    "slashing",
    "thunder",
)
# Each edition's severities, mildest first.
SEVERITIES = {"classic": ("setback", "dangerous", "deadly"), "one": ("nuisance", "deadly")}
# The tables, by caption and the book that prints them.
TABLES = {
    "classic": (("Damage Severity by Level", "DMG"), ("Trap Save DCs and Attack Bonuses", "DMG")),
    "one": (("Damage Severity and Level", "XDMG"), ("Building a Trap", "XDMG")),
}
FALL_DIE = "d6"
FALL_MOST = 20

Rows = list[list[str]]
_DICE = re.compile(r"\{@(?:damage|dice) ([^}|]+)")
_NUMBERS = re.compile(r"\d+")


class HazardError(ValueError):
    """A hazard asked for that cannot be built."""


@dataclass(frozen=True)
class Scale:
    """What a world's guide prices harm at, for one party level: the dice and the save DC
    by severity."""

    rules: str
    damage: dict[str, str]
    dc: dict[str, int]

    @property
    def severities(self) -> tuple[str, ...]:
        return tuple(s for s in SEVERITIES[self.rules] if s in self.damage)


@dataclass(frozen=True)
class Hazard:
    """What the gamemaster chose: the kind, how bad, the save (``""`` none), the damage
    type, half on a save, the DC, the dice (typed, else the severity's) and a fall's
    height in feet."""

    kind: str
    severity: str = ""
    ability: str = "dex"
    damage_type: str = "bludgeoning"
    half: bool = True
    dc: int | None = None
    dice: str = ""
    feet: int = 0

    def spec(self) -> str:
        """The hazard as one string, carried by the spring form between its steps."""
        return ":".join(
            (
                self.kind,
                self.severity,
                self.ability,
                self.damage_type,
                "1" if self.half else "0",
                str(self.dc or ""),
                self.dice.replace(":", ""),
                str(self.feet or ""),
            )
        )

    @classmethod
    def from_spec(cls, text: str) -> Hazard:
        """A hazard from :meth:`spec`; refused when it does not read."""
        parts = (text.split(":") + [""] * 8)[:8]
        kind, severity, ability, damage_type, half, dc, dice, feet = parts
        if kind not in KINDS:
            raise HazardError(text)
        return cls(
            kind=kind,
            severity=severity,
            ability=ability,
            damage_type=damage_type,
            half=half == "1",
            dc=int(dc) if dc.isdigit() else None,
            dice=dice.strip(),
            feet=int(feet) if feet.isdigit() else 0,
        )


def _level_span(text: str) -> tuple[int, int] | None:
    """``"1st-4th"`` or ``"5–10"`` as (1, 4), (5, 10)."""
    found = [int(n) for n in _NUMBERS.findall(text)]
    if len(found) >= 2:
        return found[0], found[1]
    return (found[0], found[0]) if found else None


def _dice(cell: str) -> str:
    found = _DICE.search(cell)
    return found[1].strip() if found else ""


def _middle(cell: str) -> int | None:
    """A DC range's middle, rounded down ("12–15" is 13); a single DC as it is."""
    found = [int(n) for n in _NUMBERS.findall(cell)]
    if not found:
        return None
    return (found[0] + found[-1]) // 2


def scale(rules: str, level: int, table: Callable[[str, str], Rows | None]) -> Scale | None:
    """The guide's prices for a party of ``level`` under ``rules`` (``classic`` or
    ``one``); ``table(caption, book)`` gives a table's rows from the world's books. None
    when the books lack the table."""
    rules = "one" if rules == "one" else "classic"
    (harm_caption, harm_book), (dc_caption, dc_book) = TABLES[rules]
    harm = table(harm_caption, harm_book)
    if not harm:
        return None
    names = SEVERITIES[rules]
    row = next(
        (r for r in harm if (b := _level_span(r[0])) is not None and b[0] <= level <= b[1]),
        None,
    )
    if row is None:
        return None
    damage = {name: d for name, cell in zip(names, row[1:], strict=False) if (d := _dice(cell))}
    dc: dict[str, int] = {}
    dcs = table(dc_caption, dc_book) or []
    if rules == "classic":
        # Setback, Dangerous, Deadly, each with its DC range.
        for line in dcs:
            name = line[0].strip().lower() if line else ""
            if name in names and len(line) > 1 and (value := _middle(line[1])) is not None:
                dc[name] = value
    else:
        # Per level: a nuisance's attack, DC and damage, then a deadly trap's.
        at = next(
            (r for r in dcs if (b := _level_span(r[0])) is not None and b[0] <= level <= b[1]),
            None,
        )
        if at is not None and len(at) >= 6:
            for name, cell in (("nuisance", at[2]), ("deadly", at[5])):
                if (value := _middle(cell)) is not None:
                    dc[name] = value
    return Scale(rules, damage, dc)


def effect(hazard: Hazard, scaled: Scale | None) -> TrapEffect:
    """The hazard as a trap's effect: a fall's damage with no save, anything else the
    severity's (or the typed) dice behind its save, half on a success where chosen."""
    if hazard.kind == "falling":
        if hazard.feet < 10:
            raise HazardError("falling")
        dice = f"{min(FALL_MOST, hazard.feet // 10)}{FALL_DIE}"
        return TrapEffect(damage=((dice, "bludgeoning"),))
    dice = hazard.dice or (scaled.damage.get(hazard.severity, "") if scaled else "")
    if not dice or not re.fullmatch(r"\d*d\d+(\s*[+-]\s*\d+)?", dice):
        raise HazardError("dice")
    if hazard.damage_type not in DAMAGE_TYPES:
        raise HazardError("type")
    if not hazard.ability:
        return TrapEffect(damage=((dice, hazard.damage_type),))
    if hazard.ability not in ABILITIES.values():
        raise HazardError("ability")
    dc = hazard.dc or (scaled.dc.get(hazard.severity) if scaled else None)
    if dc is None or not 1 <= dc <= 30:
        raise HazardError("dc")
    return TrapEffect(
        saves=(
            TrapSave(
                dc=dc,
                ability=hazard.ability,
                damage=dice,
                damage_type=hazard.damage_type,
                half=hazard.half,
            ),
        )
    )


def party_level(levels: Sequence[int]) -> int:
    """The party's level the guides scale by: the average, rounded."""
    shown = [max(1, n) for n in levels] or [1]
    return round(sum(shown) / len(shown))


__all__ = [
    "DAMAGE_TYPES",
    "KINDS",
    "SEVERITIES",
    "Hazard",
    "HazardError",
    "Scale",
    "effect",
    "party_level",
    "scale",
]
