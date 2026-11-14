"""A druid's Wild Shape: which beasts the druid may become, how often, and what a form
gives (documentation/CHARACTER-FORMAT.md §4).

The rules are prose in the class feature, so their numbers are kept here, read off the
Beast Shapes tables of both editions: the highest challenge rating by druid level, no
swimming speed before level 4 (2014) and no flying speed before level 8, and the 2024
count of known forms. The uses come from the class table's Wild Shape column (2024) or
the feature's "twice" (2014). A form is any beast in the world's books that fits; a
swarm is not a beast shape.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from app.models.gamestate import SourceSet
from app.models.pc import ChosenOption, ClassLevel
from app.services.text import strip_tags

FEATURE = "wild shape"
RESOURCE = "Wild Shape"
MOON = "moon"

# (druid level, highest challenge rating), from the Beast Shapes tables.
_MAX_CR = ((8, Fraction(1)), (4, Fraction(1, 2)), (2, Fraction(1, 4)))
# 2024: (druid level, known forms).
_KNOWN = ((8, 8), (4, 6), (2, 4))
SWIM_FROM = 4  # 2014 only
FLY_FROM = 8


@dataclass(frozen=True)
class WildShapeRules:
    """What one druid's Wild Shape allows at its level."""

    level: int
    edition: str  # "2014" or "2024"
    moon: bool
    max_cr: Fraction
    swim: bool
    fly: bool
    uses: int
    # 2024: how many forms the druid knows; 2014 knows every beast that fits.
    known: int | None
    # Temporary hit points on taking a form (2024): the druid level, three times it in
    # the Circle of the Moon.
    temp_hp: int
    hours: int

    def allows(self, beast: dict[str, Any], cr: Fraction | None) -> bool:
        """Whether a beast fits these rules (its type is checked by the caller)."""
        if cr is None or cr > self.max_cr:
            return False
        speed = beast.get("speed") if isinstance(beast.get("speed"), dict) else {}
        if not self.fly and speed.get("fly"):
            return False
        return self.swim or not speed.get("swim")


@dataclass(frozen=True)
class Form:
    """A beast the druid may become, as the sheet lists it."""

    name: str
    source: str
    cr: str
    data: dict[str, Any] = field(repr=False, compare=False)

    @property
    def reference(self) -> str:
        return f"{self.name}|{self.source}"

    @property
    def hp(self) -> int:
        hp = self.data.get("hp")
        average = hp.get("average") if isinstance(hp, dict) else None
        return average if isinstance(average, int) else 1

    @property
    def ac(self) -> int | None:
        for value in self.data.get("ac") or []:
            if isinstance(value, int):
                return value
            if isinstance(value, dict) and isinstance(value.get("ac"), int):
                return int(value["ac"])
        return None


def _has_wild_shape(record: dict[str, Any]) -> bool:
    for ref in record.get("classFeatures") or []:
        text = ref.get("classFeature") if isinstance(ref, dict) else ref
        if str(text).split("|")[0].strip().lower() == FEATURE:
            return True
    return False


def _uses_2024(record: dict[str, Any], level: int) -> int | None:
    for group in record.get("classTableGroups") or []:
        labels = [strip_tags(str(label)).lower() for label in group.get("colLabels") or []]
        if FEATURE in labels:
            rows = group.get("rows") or []
            if 1 <= level <= len(rows):
                value = rows[level - 1][labels.index(FEATURE)]
                return value if isinstance(value, int) else None
    return None


def _by_level(table: Iterable[tuple[int, Any]], level: int) -> Any:
    return next((value for start, value in table if level >= start), None)


def rules_for(
    cls: ClassLevel, record: dict[str, Any] | None, subclass: dict[str, Any] | None
) -> WildShapeRules | None:
    """The Wild Shape rules of one class, or None where it has no Wild Shape yet."""
    if record is None or not _has_wild_shape(record) or cls.level < 2:
        return None
    edition = "2024" if record.get("edition") == "one" else "2014"
    moon = str((subclass or {}).get("shortName", "")).lower() == MOON
    if edition == "2024" and moon and cls.level < 3:
        moon = False  # the 2024 circle is taken at level 3
    max_cr = _by_level(_MAX_CR, cls.level)
    if moon:
        circle = Fraction(cls.level // 3)
        max_cr = max(max_cr, circle if edition == "2024" or cls.level >= 6 else Fraction(1))
    if edition == "2024":
        uses = _uses_2024(record, cls.level) or 2
        known = _by_level(_KNOWN, cls.level)
        temp = cls.level * (3 if moon else 1)
    else:
        uses, known, temp = 2, None, 0
    return WildShapeRules(
        level=cls.level,
        edition=edition,
        moon=moon,
        max_cr=max_cr,
        swim=edition == "2024" or cls.level >= SWIM_FROM,
        fly=cls.level >= FLY_FROM,
        uses=uses,
        known=known,
        temp_hp=temp,
        hours=cls.level // 2,
    )


def eligible(
    compendium: Any | None, source_set: SourceSet | None, rules: WildShapeRules
) -> list[Form]:
    """Every beast in the world's books the druid may become, by name."""
    if compendium is None:
        return []
    found = []
    for beast in compendium.monsters().of_type("beast").in_books(source_set):
        if beast.kind.swarm_size or beast.cr is None:
            continue
        data = beast.entry.model_dump(by_alias=True, exclude_none=True)
        if rules.allows(data, beast.cr.value):
            found.append(Form(beast.name, beast.source, beast.cr.text, data))
    return sorted(found, key=lambda f: f.name.lower())


def find(forms: Iterable[Form], option: ChosenOption | None) -> Form | None:
    """The form a reference names, among those listed."""
    if option is None:
        return None
    name, source = option.name.lower(), (option.source or "").lower()
    return next(
        (f for f in forms if f.name.lower() == name and (not source or f.source.lower() == source)),
        None,
    )


__all__ = [
    "RESOURCE",
    "Form",
    "WildShapeRules",
    "eligible",
    "find",
    "rules_for",
]
