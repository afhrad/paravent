"""Specific magic items from generic variants (TECHSTACK.md §6b, phase 2).

``magicvariants.json`` holds 214 **generic** variants — "+1 Weapon", "Flame Tongue",
"Mithral Armor" — each with rules for the base items it applies to. 5eTools builds the
**specific** items from them ("+1 Longsword", "Flame Tongue Greatsword"), and so do we,
once per load:

* ``requires`` — any one of these property sets must hold (``{"weapon": true}``,
  ``{"type": "HA|XPHB"}``, ``{"sword": true}``); a list value means any of its values;
* ``excludes`` — none of these may hold (no +1 net);
* ``inherits`` — what the specific item takes over: rarity, attunement, bonuses, text,
  ``namePrefix``/``nameSuffix``/``nameRemove`` for its name, ``valueExpression`` for its
  price (``"[[baseItem.value]] + 50000"``), and the generic's ``reprintedAs``.

**Editions stay apart.** A variant applies only to base items of its own edition; a
variant that states none takes it from its book (the 2024 *Dungeon Master's Guide*'s
carry none, and would otherwise make a second "+1 Longsword" from every 2014 one). A
base item without an edition (a setting's firearm) takes any.

Text placeholders are filled from the specific item: ``{=bonusWeapon}`` → "+1",
``{=baseName/l}`` → "longsword", ``{=dmgType}`` → "slashing".
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

# Books whose variants are 2024 rules when the variant does not say.
BOOKS_2024 = frozenset({"XPHB", "XDMG", "XMM"})
DAMAGE_TYPES = {
    "A": "acid",
    "B": "bludgeoning",
    "C": "cold",
    "F": "fire",
    "O": "force",
    "L": "lightning",
    "N": "necrotic",
    "P": "piercing",
    "I": "poison",
    "Y": "psychic",
    "R": "radiant",
    "S": "slashing",
    "T": "thunder",
}
# What the specific item does not take from its base: the base's own book facts.
NOT_INHERITED = (
    "value",
    "page",
    "srd",
    "srd52",
    "basicRules",
    "basicRules2024",
    "reprintedAs",
    "otherSources",
    "referenceSources",
    "lootTables",
    "hasFluff",
    "hasFluffImages",
)
# What the generic variant's ``inherits`` holds that is not a field to copy.
NOT_COPIED = ("namePrefix", "nameSuffix", "nameRemove", "entries", "valueExpression", "reprintedAs")
_PLACEHOLDER = re.compile(r"\{=(\w+)(?:/(\w+))?\}")
_EXPRESSION = re.compile(r"^\s*(\d+)\s*([+*])\s*(\d+)\s*$")


def variant_edition(variant: Mapping[str, Any]) -> str | None:
    """The variant's edition: stated, or its book's; None applies to both."""
    stated = variant.get("edition")
    if isinstance(stated, str):
        return stated
    source = str((variant.get("inherits") or {}).get("source", ""))
    return "one" if source in BOOKS_2024 else None


def _equal(have: Any, want: Any) -> bool:
    return want in have if isinstance(have, list) else have == want


def _holds(base: Mapping[str, Any], key: str, want: Any) -> bool:
    have = base.get(key)
    if have is None:
        return False
    if isinstance(want, list):
        return any(_equal(have, w) for w in want)
    return _equal(have, want)


def applies(variant: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
    """Does the generic variant make a specific item of this base item?"""
    edition = variant_edition(variant)
    if edition and base.get("edition") and base["edition"] != edition:
        return False
    requires = variant.get("requires") or []
    if not any(
        isinstance(r, Mapping) and r and all(_holds(base, k, v) for k, v in r.items())
        for r in requires
    ):
        return False
    excludes = variant.get("excludes") or {}
    return not any(_holds(base, k, v) for k, v in excludes.items())


def _value(expression: Any, base: Mapping[str, Any]) -> int | None:
    """``"[[baseItem.value]] + 50000"`` with the base's value; None when unreadable."""
    if not isinstance(expression, str) or not isinstance(base.get("value"), int):
        return None
    found = _EXPRESSION.match(expression.replace("[[baseItem.value]]", str(base["value"])))
    if found is None:
        return None
    left, op, right = int(found[1]), found[2], int(found[3])
    return left + right if op == "+" else left * right


def _fill(entry: Any, item: Mapping[str, Any], base_name: str) -> Any:
    """Placeholders in a variant's text, filled from the specific item."""
    if isinstance(entry, list):
        return [_fill(e, item, base_name) for e in entry]
    if isinstance(entry, dict):
        return {k: _fill(v, item, base_name) for k, v in entry.items()}
    if not isinstance(entry, str):
        return entry

    def value(match: re.Match[str]) -> str:
        key, modifier = match[1], match[2]
        if key == "baseName":
            text = base_name
        elif key == "dmgType":
            text = DAMAGE_TYPES.get(str(item.get("dmgType", "")), str(item.get("dmgType", "")))
        else:
            raw = item.get(key)
            text = "" if raw is None else str(raw)
        if modifier in ("l", "a"):
            text = text.lower()
        if modifier in ("a", "at"):
            article = "an" if text[:1].lower() in "aeiou" else "a"
            text = f"{article.title() if modifier == 'at' else article} {text}"
        return text

    return _PLACEHOLDER.sub(value, entry)


def _reprints(raw: Any, generic: str, name: str) -> list[str]:
    """The generic's reprint targets, pointed at the specific item of the same name."""
    out = []
    for target in raw if isinstance(raw, list) else []:
        uid = target.get("uid") if isinstance(target, dict) else target
        if not isinstance(uid, str):
            continue
        target_name, _sep, source = uid.partition("|")
        if target_name.lower() == generic.lower() and source:
            out.append(f"{name}|{source}")
    return out


def specific(variant: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """The specific item one generic variant makes of one base item."""
    inherits: Mapping[str, Any] = variant.get("inherits") or {}
    base_name = str(base.get("name", ""))
    name = base_name
    if isinstance(inherits.get("nameRemove"), str):
        name = name.replace(inherits["nameRemove"], "")
    name = f"{inherits.get('namePrefix', '')}{name}{inherits.get('nameSuffix', '')}".strip()
    item = {k: copy.deepcopy(v) for k, v in base.items() if k not in NOT_INHERITED}
    for key, value in inherits.items():
        if key not in NOT_COPIED:
            item[key] = copy.deepcopy(value)
    item["name"] = name
    item["baseItem"] = f"{base_name}|{base.get('source', '')}"
    item["genericVariant"] = {"name": variant.get("name", ""), "source": inherits.get("source", "")}
    edition = variant_edition(variant)
    if edition:
        item["edition"] = edition
    price = _value(inherits.get("valueExpression"), base)
    if price is not None:
        item["value"] = price
    reprints = _reprints(inherits.get("reprintedAs"), str(variant.get("name", "")), name)
    if reprints:
        item["reprintedAs"] = reprints
    text = _fill(list(inherits.get("entries") or []), item, base_name)
    item["entries"] = [*text, *copy.deepcopy(base.get("entries") or [])]
    return item


def specific_variants(
    variants: Iterable[Mapping[str, Any]], bases: Iterable[Mapping[str, Any]]
) -> Iterator[dict[str, Any]]:
    """Every specific item the variants make of the base items, each name once.

    A variant without an edition (from a book of neither rules set, like *Wildemount*)
    matches the 2014 and the 2024 longsword alike, and both would make the same
    "Acheron Blade Longsword|EGW". Those books predate the 2024 rules, so the 2014
    base wins.
    """
    base_list = [b for b in bases if not b.get("packContents")]
    made: dict[str, tuple[dict[str, Any], bool]] = {}
    for variant in variants:
        for base in base_list:
            if not applies(variant, base):
                continue
            item = specific(variant, base)
            key = f"{item['name']}|{item.get('source', '')}".lower()
            modern = base.get("edition") == "one"
            if key not in made or (made[key][1] and not modern):
                made[key] = (item, modern)
    yield from (item for item, _modern in made.values())


def generic_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A generic variant as an entry of its own: its book, rarity and text on top.

    In the file these sit inside ``inherits``; lifted, the variant has a key
    (``name|source``) and a reference page like any item.
    """
    entry = dict(raw)
    inherits = raw.get("inherits") or {}
    for key in ("source", "page", "rarity", "reqAttune", "entries", "tier", "reprintedAs"):
        if key not in entry and key in inherits:
            entry[key] = copy.deepcopy(inherits[key])
    if isinstance(entry.get("entries"), list):
        entry["entries"] = _fill(entry["entries"], {**inherits}, "item")
    return entry


__all__ = [
    "applies",
    "generic_entry",
    "specific",
    "specific_variants",
    "variant_edition",
]
