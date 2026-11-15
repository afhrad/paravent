"""Which tool makes which item, as the world's tools say.

The 2024 rules list under each tool what it crafts ("Craft: Club, Greatclub…", "Any
Melee weapon (except Club…), Medium armor (except Hide), Heavy armor…"), and a
character needs proficiency with that tool to make the item. These lists are read out
of the tools of the world's books; an item no tool lists is the gamemaster's to assign,
and a world whose tools list nothing (the 2014 books) checks no tool at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.chargen.catalogue import Offer

_TAG = re.compile(r"\{@item ([^|}]+)[^}]*\}")
# A category the lists name: the words, and the item types it covers.
_CATEGORIES = (
    ("melee weapon", ("M",)),
    ("ranged weapon", ("R",)),
    ("light armor", ("LA",)),
    ("medium armor", ("MA",)),
    ("heavy armor", ("HA",)),
)


@dataclass(frozen=True)
class CraftRule:
    """What one tool crafts: items by name, and categories with their exceptions."""

    tool: str
    names: frozenset[str]
    categories: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = field(default=())

    def makes(self, offer: Offer) -> bool:
        name = offer.name.lower()
        if name in self.names:
            return True
        code = str(offer.data.get("type", "")).split("|")[0]
        return any(code in codes and name not in out for codes, out in self.categories)


def _segments(text: str) -> list[str]:
    """``text`` split at commas outside parentheses and tags."""
    out, depth, start = [], 0, 0
    for i, char in enumerate(text):
        if char in "({":
            depth += 1
        elif char in ")}":
            depth -= 1
        elif char == "," and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [s.strip() for s in out if s.strip()]


def _craft_text(entries: Any) -> str:
    """The text of a tool's "Craft:" item, or ""."""
    if isinstance(entries, dict):
        if str(entries.get("name", "")).strip().rstrip(":").lower() == "craft":
            body = entries.get("entries") or [entries.get("entry", "")]
            return " ".join(str(e) for e in body if isinstance(e, str))
        for key in ("entries", "items"):
            found = _craft_text(entries.get(key))
            if found:
                return found
    elif isinstance(entries, list):
        for entry in entries:
            found = _craft_text(entry)
            if found:
                return found
    return ""


def rule_for(tool: Offer) -> CraftRule | None:
    """What ``tool`` crafts, or None when its text lists nothing."""
    text = _craft_text(tool.data.get("entries"))
    if not text:
        return None
    names: set[str] = set()
    categories: list[tuple[tuple[str, ...], frozenset[str]]] = []
    for part in _segments(text):
        lowered = re.sub(r"\(.*\)", "", part).lower()
        codes = next((c for words, c in _CATEGORIES if words in lowered), None)
        if codes is not None and "{@item" not in lowered:
            excepted = frozenset(m.lower() for m in _TAG.findall(part))
            categories.append((codes, excepted))
            continue
        names.update(m.lower() for m in _TAG.findall(part))
    return CraftRule(tool.name, frozenset(names), tuple(categories))


def craft_rules(offers: Iterable[Offer]) -> list[CraftRule]:
    """The crafting lists of the tools among ``offers`` (one rule per tool name)."""
    rules: dict[str, CraftRule] = {}
    for offer in offers:
        rule = rule_for(offer)
        if rule is not None:
            rules.setdefault(offer.name.lower(), rule)
    return list(rules.values())


def tools_for(offer: Offer, rules: Sequence[CraftRule]) -> list[str]:
    """The tools that make ``offer`` (empty: no tool lists it)."""
    return [r.tool for r in rules if r.makes(offer)]


def may_craft(offer: Offer, rules: Sequence[CraftRule], known: Iterable[str]) -> bool:
    """Whether a character proficient with ``known`` tools may make ``offer``: an item
    no tool lists is the gamemaster's to judge, so it is allowed."""
    needed = tools_for(offer, rules)
    held = {k.lower() for k in known}
    return not needed or any(t.lower() in held for t in needed)


__all__ = ["CraftRule", "craft_rules", "may_craft", "rule_for", "tools_for"]
