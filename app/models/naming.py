"""Naming styles: how a setting's names are built (documentation/NAMES.md).

A style is a pattern for the full name and a definition of each part in it. Library data,
so strict: an unknown field is a bug.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.gamestate import Record

_TOKEN = re.compile(r"\{(\w+)(?::(\w+))?\}")
MODIFIERS = ("initial",)
# What a style names: people, or one of four kinds of place (NAMES.md §2).
SUBJECTS = ("person", "settlement", "water", "landscape", "building")
Subject = Literal["person", "settlement", "water", "landscape", "building"]


class ListPart(BaseModel):
    """A name from a list: ``names`` serve anyone, ``female`` and ``male`` their own."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["list"] = "list"
    names: list[str] = Field(default_factory=list)
    female: list[str] = Field(default_factory=list)
    male: list[str] = Field(default_factory=list)


class ElementsPart(BaseModel):
    """Two elements joined: ``first`` and a second, by gender where the style has them."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["elements"] = "elements"
    first: list[str] = Field(min_length=1)
    second: list[str] = Field(default_factory=list)
    second_female: list[str] = Field(default_factory=list)
    second_male: list[str] = Field(default_factory=list)


class ByGenderPart(BaseModel):
    """Fixed text that follows the person: son or daughter, ibn or bint."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["by_gender"] = "by_gender"
    female: str
    male: str


class SoundsPart(BaseModel):
    """An invented word: syllables from templates, each capital a sound of its group.

    ``{"C": ["k", "th"], "V": ["a", "i"]}`` with ``["CV", "CVn"]`` makes "thakin". A sound
    listed twice comes up twice as often; other letters in a template stand for themselves.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sounds"] = "sounds"
    groups: dict[str, list[str]]
    syllables: list[str] = Field(min_length=1)
    min_syllables: int = Field(default=2, ge=1, le=8)
    max_syllables: int = Field(default=3, ge=1, le=8)
    ending_female: list[str] = Field(default_factory=list)
    ending_male: list[str] = Field(default_factory=list)


class LearnedPart(BaseModel):
    """New words that sound like pasted names: a Markov chain of ``order`` letters."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["learned"] = "learned"
    names: list[str] = Field(default_factory=list)
    female: list[str] = Field(default_factory=list)
    male: list[str] = Field(default_factory=list)
    order: int = Field(default=2, ge=1, le=4)


class KinPart(BaseModel):
    """A part of a chosen parent's name; ``fallback``, a part of this style, without one."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["kin"] = "kin"
    part: str
    fallback: str


NamePart = Annotated[
    ListPart | ElementsPart | ByGenderPart | SoundsPart | LearnedPart | KinPart,
    Field(discriminator="kind"),
]


def pattern_tokens(pattern: str) -> list[tuple[str, str]]:
    """``(part, modifier)`` for every ``{part}`` or ``{part:modifier}`` in a pattern."""
    return [(found[1], found[2] or "") for found in _TOKEN.finditer(pattern)]


class NamingStyle(Record):
    """How names are built in one setting."""

    name: str
    # What it names: people, settlements, waters, landscape or buildings.
    subject: Subject = "person"
    # The language its names are made for ("de"): preferred for readers of that
    # language, never picked by itself for others. Empty serves every language.
    language: str = ""
    # Who it names; either may be empty. Together they are how a style is found.
    race: str = ""
    culture: str = ""
    # Other names of the race: "Dwarf" for a style of "Zwerg", so a species finds it.
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    # Where its research came from: addresses, or a plain note.
    sources: list[str] = Field(default_factory=list)
    pattern: str
    parts: dict[str, NamePart]
    # Literal, lower-case replacements on the finished name.
    rewrites: dict[str, str] = Field(default_factory=dict)
    # Whole names never to offer, turned down among the samples.
    rejected: list[str] = Field(default_factory=list)
    # Letters no name may contain, a sound or a whole word, in any case.
    banned: list[str] = Field(default_factory=list)
    # The most consonants in a row a name may have; 0 checks nothing.
    max_cluster: int = Field(default=0, ge=0, le=8)
    # Claude's proposal: offered nowhere until the gamemaster accepts it.
    draft: bool = False
    # The world a draft was made for: accepting it ticks it there (NAMES.md §5b).
    for_world: str | None = None

    @model_validator(mode="after")
    def _pattern_names_its_parts(self) -> NamingStyle:
        tokens = pattern_tokens(self.pattern)
        if not tokens:
            raise ValueError("a pattern names at least one part")
        for part, modifier in tokens:
            if part not in self.parts:
                raise ValueError(f"the pattern names no part {part!r}")
            if modifier and modifier not in MODIFIERS:
                raise ValueError(f"no modifier {modifier!r}")
        for key, made in self.parts.items():
            if isinstance(made, KinPart) and isinstance(
                self.parts.get(made.fallback, made), KinPart
            ):
                raise ValueError(f"{key}: a kin part falls back to a part that is not kin")
        return self


__all__ = [
    "ByGenderPart",
    "ElementsPart",
    "KinPart",
    "LearnedPart",
    "ListPart",
    "MODIFIERS",
    "NamePart",
    "NamingStyle",
    "SUBJECTS",
    "SoundsPart",
    "Subject",
    "pattern_tokens",
]
