"""Choices between features: a class or subclass feature whose text offers several and
says how many to take — a Hunter's Prey (Colossus Slayer, Giant Killer or Horde
Breaker), a Totem Spirit, a 2024 cleric's Divine Order (Protector or Thaumaturge).

The corpus writes them as an ``{"type": "options", "count": 1, "entries": [...]}`` block
inside the feature's entries, each entry a ``refSubclassFeature`` or ``refClassFeature``
to the option's own record. A block without a ``count`` is a grouping, not a choice — a
Storm Herald gets every aura of its environment — and is not read here.

The option chosen is kept in the character's ``optional_features`` by name and book
(``"Colossus Slayer|PHB"``), beside fighting styles and invocations.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

_REF_TYPES = ("refSubclassFeature", "refClassFeature")


@dataclass(frozen=True)
class OptionRef:
    """One option a block offers, from its reference string."""

    name: str
    class_name: str
    subclass: str  # the subclass's short name; "" for a class feature
    level: int
    source: str  # the option record's book, where the reference names it

    @property
    def kind(self) -> str:
        """``subclassFeature`` or ``classFeature``."""
        return "subclassFeature" if self.subclass else "classFeature"


@dataclass(frozen=True)
class OptionBlock:
    """A choice inside one feature: take ``count`` of ``options``."""

    count: int
    options: tuple[OptionRef, ...]


def parse_ref(kind: str, text: str) -> OptionRef | None:
    """``"Colossus Slayer|Ranger||Hunter||3"`` (subclass feature: name, class, class
    source, subclass, subclass source, level, own source) or ``"Protector|Cleric|XPHB|1|
    XPHB"`` (class feature: name, class, class source, level, own source)."""
    parts = text.split("|")
    if kind == "refSubclassFeature" and len(parts) >= 6 and parts[5].isdigit():
        source = parts[6] if len(parts) > 6 and parts[6] else parts[4] or parts[2]
        return OptionRef(parts[0], parts[1], parts[3], int(parts[5]), source or "PHB")
    if kind == "refClassFeature" and len(parts) >= 4 and parts[3].isdigit():
        source = parts[4] if len(parts) > 4 and parts[4] else parts[2]
        return OptionRef(parts[0], parts[1], "", int(parts[3]), source or "PHB")
    return None


def option_blocks(entries: Any) -> Iterator[OptionBlock]:
    """Every counted choice inside a feature's ``entries``, depth first."""
    if isinstance(entries, list):
        for entry in entries:
            yield from option_blocks(entry)
    elif isinstance(entries, Mapping):
        if entries.get("type") == "options" and isinstance(entries.get("count"), int):
            refs = [
                parse_ref(str(e.get("type")), str(e.get(_field(str(e.get("type"))), "")))
                for e in entries.get("entries") or []
                if isinstance(e, Mapping) and e.get("type") in _REF_TYPES
            ]
            found = tuple(r for r in refs if r is not None)
            if found:
                yield OptionBlock(int(entries["count"]), found)
                return
        for value in entries.values():
            if isinstance(value, list | Mapping):
                yield from option_blocks(value)


def _field(kind: str) -> str:
    """``refSubclassFeature`` → ``subclassFeature``: where the reference string is."""
    return kind[3].lower() + kind[4:]


def option_names(features: Iterable[Mapping[str, Any]]) -> set[str]:
    """The names (lower case) of every option the given features offer by choice."""
    return {
        ref.name.lower()
        for feature in features
        for block in option_blocks(feature.get("entries"))
        for ref in block.options
    }


__all__ = ["OptionBlock", "OptionRef", "option_blocks", "option_names", "parse_ref"]
