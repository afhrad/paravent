"""Polymorphic value types found throughout the 5eTools corpus.

These fields are the main source of validation failure in a naive model. Each was
verified against the real corpus:

- ``ac``    is ``list[int | ArmorClass]``
- ``cr``    is ``str | ChallengeRating``
- ``type``  is ``str | CreatureType``
- ``immune``/``resist``/``vulnerable`` are lists that **mix** bare strings and objects
  in the same list
- ``value`` on items is ``int | float | None``
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.models.base import ALIGNMENT_NAMES, SIZE_NAMES, FiveToolsModel


class ArmorClass(FiveToolsModel):
    """One AC entry, optionally conditional (``13 (natural armor)``).

    Summoned creatures scale with the spell slot and carry prose in ``special``
    ("11 + the level of the spell") instead of a number, so ``ac`` is optional.
    """

    ac: int | None = None
    from_: list[str] = Field(default_factory=list, alias="from")
    condition: str | None = None
    braces: bool | None = None
    special: str | None = None

    @property
    def display(self) -> str:
        """The AC as shown in a statblock, prose form included."""
        if self.special:
            return self.special
        base = str(self.ac if self.ac is not None else "—")
        if self.from_:
            base = f"{base} ({', '.join(self.from_)})"
        if self.condition:
            base = f"{base} {self.condition}"
        return base


class HitPoints(FiveToolsModel):
    """Hit points: an average plus the dice formula that produced it.

    Some entries (swarms, templated creatures) carry ``special`` prose instead.
    """

    average: int | None = None
    formula: str | None = None
    special: str | None = None


class Speed(FiveToolsModel):
    """Movement speeds. Each mode is either a number or ``{number, condition}``."""

    walk: int | dict[str, Any] | None = None
    fly: int | dict[str, Any] | None = None
    swim: int | dict[str, Any] | None = None
    climb: int | dict[str, Any] | None = None
    burrow: int | dict[str, Any] | None = None
    canHover: bool | None = None
    choose: dict[str, Any] | None = None


class ChallengeRating(FiveToolsModel):
    """CR when it carries lair/coven variants; plain CRs are bare strings upstream."""

    cr: str
    xp: int | None = None
    lair: str | None = None
    coven: str | None = None
    xpLair: int | None = None


class CreatureType(FiveToolsModel):
    """Creature type with subtype tags: ``humanoid (goblinoid)``."""

    type: str | dict[str, Any]
    tags: list[str | dict[str, Any]] = Field(default_factory=list)
    swarmSize: str | None = None


class DamageModifier(FiveToolsModel):
    """A conditional damage immunity/resistance/vulnerability entry."""

    immune: list[Any] = Field(default_factory=list)
    resist: list[Any] = Field(default_factory=list)
    vulnerable: list[Any] = Field(default_factory=list)
    note: str | None = None
    cond: bool | None = None

    @property
    def types(self) -> list[Any]:
        """The damage types this modifier applies to, whichever key carried them."""
        return self.immune or self.resist or self.vulnerable


class AlignmentChoice(FiveToolsModel):
    """A weighted or chosen alignment (``any chaotic alignment``)."""

    alignment: list[str] = Field(default_factory=list)
    chance: int | None = None
    note: str | None = None


def format_size(sizes: list[str] | None) -> str:
    """Expand size abbreviations for display: ``["S"]`` -> ``Small``."""
    if not sizes:
        return ""
    return " or ".join(SIZE_NAMES.get(s, s) for s in sizes)


def format_alignment(alignment: list[Any] | None) -> str:
    """Expand an alignment list for display: ``["C", "E"]`` -> ``chaotic evil``."""
    if not alignment:
        return ""
    parts: list[str] = []
    for item in alignment:
        if isinstance(item, str):
            parts.append(ALIGNMENT_NAMES.get(item, item))
        elif isinstance(item, dict):
            inner = item.get("alignment", [])
            text = " ".join(ALIGNMENT_NAMES.get(a, a) for a in inner)
            if (chance := item.get("chance")) is not None:
                text = f"{text} ({chance}%)"
            parts.append(text)
    return " ".join(parts)


class Spellcasting(FiveToolsModel):
    """A creature's spellcasting block.

    The ``spells`` mapping is keyed by spell level as a *string* (``"0"``…``"9"``),
    which is why it is not typed as ``dict[int, ...]``.
    """

    name: str | None = None
    type: str | None = None
    headerEntries: list[Any] = Field(default_factory=list)
    footerEntries: list[Any] = Field(default_factory=list)
    will: list[Any] = Field(default_factory=list)
    daily: dict[str, Any] = Field(default_factory=dict)
    rest: dict[str, Any] = Field(default_factory=dict)
    weekly: dict[str, Any] = Field(default_factory=dict)
    spells: dict[str, Any] = Field(default_factory=dict)
    ability: str | None = None
    displayAs: str | None = None
    hidden: list[str] = Field(default_factory=list)


class CreatureAction(FiveToolsModel):
    """A trait, action, bonus action, reaction, legendary or mythic action."""

    name: str | None = None
    entries: list[Any] = Field(default_factory=list)

    @field_validator("entries", mode="before")
    @classmethod
    def _wrap_scalar(cls, v: Any) -> Any:
        """Some entries carry a bare string where a list is expected."""
        if isinstance(v, str):
            return [v]
        return v
