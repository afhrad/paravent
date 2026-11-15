"""Creatures a spell summons (Summon Beast, Summon Fey, Find Steed, …), at the level the
spell was cast with.

The corpus marks such a creature with ``summonedBySpell`` (the spell) and
``summonedBySpellLevel`` (its lowest level), and writes how it grows as text: AC
"11 + the spell's level", hit points "30 + 5 for each spell level above 2", attacks
``{@hitYourSpellAttack}`` and ``{@damage 1d8 + 4 + summonSpellLevel}``, save DCs
"your spell save DC", the number of attacks "half this spell's level". :func:`scaled`
reads those and returns the statblock as cast: plain numbers and ``{@hit}``/``{@dc}``
tags, so the tracker's attack and save buttons work on it unchanged. A creature's
variants (Air, Land, Water) are its ``_versions``, applied first.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from app.store.copyresolver import CopyResolver

_LEVEL = r"(?:the spell's level|the level of the spell|this spell's level)"
_AC = re.compile(r"(\d+)\s*\+\s*(?:" + _LEVEL + r"|1 per spell level)")
_AC_EXTRA = re.compile(r"\+\s*(\d+)\s*\(([^)]*?) only\)")
_HP_ABOVE = re.compile(r"\+\s*(\d+)\s*for each spell level above (\d+)")
_HP_PER = re.compile(r"^(\d+)\s*\+\s*(\d+)\s*per spell level")
_HP_BASE = re.compile(r"(\d+)\s*\(([^)]*?)(?: only)?\)")
_HIT = re.compile(r"\{@hitYourSpellAttack[^}]*\}")
_DICE_TAG = re.compile(r"\{@(damage|dice) ([^}|]*summonSpellLevel[^}|]*)(?:\|[^}]*)?\}")
_PLUS_LEVEL = re.compile(r"\{@damage ([^}|]+)\} plus " + _LEVEL)
_HALF = re.compile(r"half " + _LEVEL + r" \(round(?:ed)? down\)")
_ADD_LEVEL = re.compile(r"\bAdd " + _LEVEL)
_COUNT = re.compile(
    r"a number of ((?:[A-Z][\w-]* )*)attacks equal to half " + _LEVEL + r" \(round(?:ed)? down\)"
)
_DC_PHRASES = (
    (re.compile(r"DC equals your spell save DC"), "{{@dc {dc}}}"),
    (re.compile(r"\} Your spell save DC"), "}} {{@dc {dc}}}"),
    (re.compile(r"against your spell save DC"), "against DC {dc}"),
    (re.compile(r"escape DC equals your spell save DC"), "escape DC {dc}"),
)


@dataclass(frozen=True)
class Summoning:
    """What a spell summons: the creature, the spell's lowest level, its variants."""

    name: str
    source: str
    spell: str
    min_level: int
    variants: tuple[str, ...] = ()

    @property
    def reference(self) -> str:
        return f"{self.name}|{self.source}"


def summoning(data: dict[str, Any]) -> Summoning | None:
    """The summoning a creature's record describes, or None for any other creature."""
    spell = data.get("summonedBySpell")
    if not isinstance(spell, str):
        return None
    return Summoning(
        str(data.get("name")),
        str(data.get("source")),
        spell,
        int(data.get("summonedBySpellLevel") or 1),
        tuple(_variant_names(data)),
    )


def _variant_names(data: dict[str, Any]) -> Iterator[str]:
    base = str(data.get("name", ""))
    for version in data.get("_versions") or []:
        name = str(version.get("name", "")) if isinstance(version, dict) else ""
        match = re.fullmatch(re.escape(base) + r" \((.+)\)", name)
        if match:
            yield match.group(1)


def scaled(
    data: dict[str, Any], level: int, variant: str, attack: int | None, dc: int | None
) -> dict[str, Any]:
    """The creature as summoned at spell ``level``, in ``variant`` (one of its
    :attr:`Summoning.variants`, or ""), its attacks with the caster's ``attack``
    bonus and its saves at the caster's ``dc``."""
    out = _apply_variant(data, variant)
    out.pop("_versions", None)
    ac = _ac(out.get("ac"), level, variant)
    if ac is not None:
        out["ac"] = [ac]
    hp = _hp(out.get("hp"), level, variant)
    if hp is not None:
        out["hp"] = {"average": hp, "formula": str(hp)}
    for key in ("action", "bonus", "reaction", "trait", "legendary"):
        if key in out:
            out[key] = _texts(out[key], level, attack, dc)
    return out


def _apply_variant(data: dict[str, Any], variant: str) -> dict[str, Any]:
    out = copy.deepcopy(data)
    if not variant:
        return out
    wanted = f"{data.get('name')} ({variant})"
    for version in data.get("_versions") or []:
        if isinstance(version, dict) and version.get("name") == wanted:
            mods = version.get("_mod")
            if isinstance(mods, dict):
                _apply_mods(out, mods)
            for key, value in version.items():
                if key not in ("_mod", "name", "source"):
                    out[key] = copy.deepcopy(value)
            out["name"] = wanted
    return out


def _apply_mods(target: dict[str, Any], mods: dict[str, Any]) -> None:
    """A version's ``_mod``: renaming entries here (``renameArr``, which only versions
    use), every other mode as ``_copy`` applies it."""
    resolver = CopyResolver({})
    for prop, instructions in mods.items():
        for instruction in instructions if isinstance(instructions, list) else [instructions]:
            if isinstance(instruction, dict) and instruction.get("mode") == "renameArr":
                _rename(target, prop, instruction.get("renames"))
            else:
                resolver.apply_mods(target, {prop: instruction})


def _rename(target: dict[str, Any], prop: str, renames: Any) -> None:
    for rename in renames if isinstance(renames, list) else [renames]:
        if not isinstance(rename, dict):
            continue
        for item in target.get(prop) or []:
            if isinstance(item, dict) and item.get("name") == rename.get("rename"):
                item["name"] = rename.get("with")


def _special(value: Any) -> str | None:
    """The text of an ``{"special": …}`` value, or of the first one in a list."""
    if isinstance(value, dict) and isinstance(value.get("special"), str):
        return str(value["special"])
    if isinstance(value, list):
        for item in value:
            text = _special(item)
            if text is not None:
                return text
    return None


def _applies(condition: str, variant: str) -> bool:
    """Whether "Land and Water" (a parenthetical) names ``variant``."""
    names = {n.strip().lower() for n in re.split(r",| and | or ", condition)}
    return bool(variant) and variant.lower() in names


def _ac(value: Any, level: int, variant: str) -> int | None:
    text = _special(value)
    if text is None:
        return None
    base = _AC.search(text)
    if base is None:
        return None
    ac = int(base.group(1)) + level
    for extra, condition in _AC_EXTRA.findall(text):
        if _applies(condition, variant):
            ac += int(extra)
    return ac


def _hp(value: Any, level: int, variant: str) -> int | None:
    text = _special(value)
    if text is None:
        return None
    per = _HP_PER.search(text)
    if per:
        return int(per.group(1)) + int(per.group(2)) * level
    above = _HP_ABOVE.search(text)
    head = text[: above.start()] if above else text
    bases = _HP_BASE.findall(head)
    base: int | None = None
    if bases:
        base = next((int(n) for n, cond in bases if _applies(cond, variant)), int(bases[0][0]))
    else:
        plain = re.match(r"\s*(\d+)", head)
        base = int(plain.group(1)) if plain else None
    if base is None:
        return None
    if above:
        base += int(above.group(1)) * max(0, level - int(above.group(2)))
    return base


def _texts(value: Any, level: int, attack: int | None, dc: int | None) -> Any:
    if isinstance(value, str):
        return _text(value, level, attack, dc)
    if isinstance(value, list):
        return [_texts(v, level, attack, dc) for v in value]
    if isinstance(value, dict):
        return {k: _texts(v, level, attack, dc) for k, v in value.items()}
    return value


def _text(text: str, level: int, attack: int | None, dc: int | None) -> str:
    if attack is not None:
        text = _HIT.sub(f"{{@hit {attack}}}", text)
    text = _DICE_TAG.sub(lambda m: f"{{@{m.group(1)} {_dice(m.group(2), level)}}}", text)
    text = _PLUS_LEVEL.sub(lambda m: f"{{@damage {m.group(1).strip()} + {level}}}", text)
    text = _COUNT.sub(
        lambda m: f"{level // 2} {m.group(1)}attack{'' if level // 2 == 1 else 's'}", text
    )
    text = _HALF.sub(str(level // 2), text)
    text = _ADD_LEVEL.sub(f"Add {level}", text)
    if dc is not None:
        for pattern, replacement in _DC_PHRASES:
            text = pattern.sub(replacement.format(dc=dc), text)
    return text


def _dice(expression: str, level: int) -> str:
    """``1d8 + 4 + summonSpellLevel`` → ``1d8 + 4 + 3``; ``(summonSpellLevel - 3)d12 + 3``
    → ``2d12 + 3`` at level 5."""

    def count(match: re.Match[str]) -> str:
        return str(max(1, level - int(match.group(1))))

    expression = re.sub(r"\(summonSpellLevel\s*-\s*(\d+)\)", count, expression)
    return expression.replace("summonSpellLevel", str(level)).strip()


__all__ = ["Summoning", "scaled", "summoning"]
