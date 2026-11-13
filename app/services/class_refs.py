"""Whose feature a class's feature reference names.

A class lists its features as references, ``name|className|classSource|level|source``,
and a subclass as ``name|className|classSource|subclassShortName|subclassSource|level``.
Most name the class itself, but a class made on another's frame borrows: a class that
copies the 2024 paladin lists ``Spellcasting|Paladin|XPHB|1`` beside its own features,
and its subclass copying the berserker lists the barbarian's subclass features. The
feature records are filed under the class that prints them, so they are found by the
owner the reference names, not by the character's class. An empty class or subclass
source means the PHB, as everywhere in 5eTools: the 2014 subclasses are filed under the
2024 classes too, their references still naming the PHB class's features.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# What an empty source in a reference means.
DEFAULT_SOURCE = "phb"


@dataclass(frozen=True)
class FeatureRef:
    """A class feature reference: its name as written, the rest lowercased."""

    name: str
    class_name: str
    class_source: str
    level: int
    source: str  # the feature's own book; the owner's class source when not written
    gains_subclass: bool = False
    source_written: bool = False

    def matches(self, record: Mapping[str, Any]) -> bool:
        """Whether ``record`` is the classFeature this reference names."""
        return (
            str(record.get("name", "")).lower() == self.name.lower()
            and record.get("level") == self.level
            and str(record.get("className", "")).lower() == self.class_name
            and str(record.get("classSource", "")).lower() == self.class_source
            and (not self.source_written or str(record.get("source", "")).lower() == self.source)
        )


def feature_refs(record: Mapping[str, Any]) -> list[FeatureRef]:
    """Every readable feature reference of the class ``record``, in its order."""
    own_name = str(record.get("name", "")).lower()
    out = []
    for ref in record.get("classFeatures") or []:
        text = ref.get("classFeature") if isinstance(ref, Mapping) else ref
        parts = str(text).split("|")
        if len(parts) < 4 or not parts[3].isdigit():
            continue
        class_name = parts[1].lower() or own_name
        class_source = parts[2].lower() or DEFAULT_SOURCE
        source = parts[4].lower() if len(parts) > 4 and parts[4] else class_source
        out.append(
            FeatureRef(
                parts[0],
                class_name,
                class_source,
                int(parts[3]),
                source,
                isinstance(ref, Mapping) and bool(ref.get("gainSubclassFeature")),
                len(parts) > 4 and bool(parts[4]),
            )
        )
    return out


def is_variant(ref: FeatureRef, record: Mapping[str, Any]) -> bool:
    """Whether ``ref`` is an optional variant from another book (Tasha's "Cantrip
    Formulas" on the PHB wizard), shown only when the character took it: its book is
    neither the class's nor the owner's."""
    return ref.source not in (str(record.get("source", "")).lower(), ref.class_source)


def feature_owners(record: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Every ``(className, classSource)`` the class's features are filed under."""
    own = (str(record.get("name", "")).lower(), str(record.get("source", "")).lower())
    return {own} | {(r.class_name, r.class_source) for r in feature_refs(record)}


def subclass_owner(
    subclass: Mapping[str, Any], class_name: str, class_source: str
) -> tuple[str, str, str, str]:
    """``(className, classSource, subclassShortName, subclassSource)``, lowercased, that
    the subclass's features are filed under: what its first feature reference names
    (an empty source the PHB), else the subclass itself under the character's class."""
    short = str(subclass.get("shortName") or subclass.get("name", "")).lower()
    own = (class_name.lower(), class_source.lower(), short, str(subclass.get("source", "")).lower())
    for ref in subclass.get("subclassFeatures") or []:
        text = ref.get("subclassFeature") if isinstance(ref, Mapping) else ref
        parts = str(text).split("|")
        if len(parts) >= 6:
            return (
                parts[1].lower() or own[0],
                parts[2].lower() or DEFAULT_SOURCE,
                parts[3].lower() or own[2],
                parts[4].lower() or DEFAULT_SOURCE,
            )
    return own


def files_under(record: Mapping[str, Any], owner: tuple[str, str, str, str]) -> bool:
    """Whether the subclassFeature ``record`` is filed under ``owner``."""
    return (
        str(record.get("className", "")).lower(),
        str(record.get("classSource", "")).lower(),
        str(record.get("subclassShortName", "")).lower(),
        str(record.get("subclassSource", "")).lower(),
    ) == owner


__all__ = [
    "FeatureRef",
    "feature_owners",
    "feature_refs",
    "files_under",
    "is_variant",
    "subclass_owner",
]
