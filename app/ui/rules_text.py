"""Statblock facts in the reader's language: spells and creatures.

The corpus stores these as data — ``{"unit": "action", "number": 1}``, size ``"M"``,
alignment ``["N", "E"]``, damage ``"fire"`` — and this module phrases them through the
UI catalogue's ``rules.*`` vocabulary (and the term glossary, for species, classes and
languages). English reads as the statblocks always did; German as the German books
write it: "1 Aktion", "Selbst (Kegel von 4,5 m)", "Mittelgroßer Humanoider (Goblinoid),
neutral böse".

Distances go through the reader's unit setting, never through the language: a German
group may play in feet (TRANSLATION.md §6).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services.sheet import SpellView
from app.store.projections import CreatureKind, coerce_cr, coerce_type
from app.ui.context import Page
from app.ui.names import named
from app.ui.tags import dice_letter, dice_text

UNITS = ("minute", "hour", "round", "day", "week", "year")
SIZE_CODES = ("T", "S", "M", "L", "H", "G")
_SENSE = re.compile(
    r"^(blindsight|darkvision|tremorsense|truesight)\s+(\d+)\s*(?:ft\.?|feet)(.*)$", re.IGNORECASE
)
_PASSIVE = re.compile(r"^passive Perception\s+(\d+)$", re.IGNORECASE)


def _word(page: Page, key: str, fallback: str) -> str:
    """A catalogue word, or ``fallback`` when the catalogue has none for it."""
    text = page.t(key)
    return fallback if text == key else text


def _amount(page: Page, unit: str, count: Any) -> str:
    if unit in UNITS and isinstance(count, int):
        return page.t.plural(f"rules.unit.{unit}", count)
    return f"{count} {unit}".strip()


# -- spells ------------------------------------------------------------------------------


def school(page: Page, code: str) -> str:
    """A school of magic by its letter: ``V`` → "Evocation" / "Hervorrufung"."""
    return _word(page, f"chargen.school.{code}", code) if code else ""


def spell_time(page: Page, spell: SpellView) -> str:
    """The casting time: "Action", "1 minute" / "Aktion", "1 Minute"."""
    for entry in spell.time_data:
        if not isinstance(entry, Mapping):
            continue
        unit, number = str(entry.get("unit", "")), entry.get("number", 1)
        if unit in ("action", "bonus", "reaction"):
            return _word(page, f"rules.time.{unit}", unit)
        return _amount(page, unit, number)
    return spell.time


def spell_range(page: Page, spell: SpellView) -> str:
    """The range: "150 ft" / "45 m", "Touch" / "Berührung", "Self (15 ft cone)"."""
    data = spell.range_data
    if not data:
        return page.u.text(spell.range)
    distance = data.get("distance") or {}
    kind = str(distance.get("type", ""))
    if kind == "feet" and isinstance(distance.get("amount"), int | float):
        text = page.u.distance(distance["amount"])
    elif kind == "miles" and isinstance(distance.get("amount"), int | float):
        text = page.u.text(f"{distance['amount']} mi")
    else:
        text = _word(page, f"rules.range.{kind}", kind.title()) if kind else ""
    shape = str(data.get("type", ""))
    if shape in ("cone", "line", "radius", "sphere", "cube", "emanation", "hemisphere") and text:
        return page.t(
            "rules.range.area", distance=text, shape=_word(page, f"rules.area.{shape}", shape)
        )
    if shape == "special":
        return _word(page, "rules.range.special", "Special")
    return text


def spell_duration(page: Page, spell: SpellView) -> str:
    """The duration: "Instant", "10 minutes" / "Unmittelbar", "10 Minuten"."""
    for entry in spell.duration_data:
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("type")
        if kind == "instant":
            return _word(page, "rules.duration.instant", "Instant")
        if kind == "timed":
            amount = entry.get("duration") or {}
            return _amount(page, str(amount.get("type", "")), amount.get("amount", ""))
        if kind == "permanent":
            ends = entry.get("ends") or []
            key = "permanent_trigger" if "trigger" in ends else "permanent"
            return _word(page, f"rules.duration.{key}", "Until dispelled")
        return _word(page, f"rules.duration.{kind}", str(kind or "").title())
    return spell.duration


# -- creatures ---------------------------------------------------------------------------


def _tag(page: Page, tag: str) -> str:
    key = tag.lower()
    own = _word(page, f"rules.tag.{key}", "")
    if own:
        return own
    # A species or a class: the glossary's name ("elf" → "Elf", "wizard" → "Magier").
    return named(page, ("race", "class"), tag.title()) or tag


def kind_text(page: Page, sizes: Iterable[Any], kind: CreatureKind) -> str:
    """ "Medium humanoid (goblinoid)" / "Mittelgroßer Humanoider (Goblinoid)": the size
    agrees with the type's gender in German."""
    first = kind.types[0] if kind.types else ""
    gender = _word(page, f"rules.gender.{first}", "m") if first else "m"
    or_word = _word(page, "rules.or", "or")
    codes = [str(s) for s in sizes if str(s) in SIZE_CODES]
    size_words = [page.t(f"rules.size.{s}.{gender}") for s in codes]
    if page.lang != "en":
        size_words = [w if i == 0 else w.lower() for i, w in enumerate(size_words)]
    size = f" {or_word} ".join(size_words)
    base = f" {or_word} ".join(_word(page, f"rules.type.{t}", t) for t in kind.types)
    if kind.tags:
        base += f" ({', '.join(_tag(page, t) for t in kind.tags)})"
    return f"{size} {base}".strip()


# 5eTools spells "any evil alignment" out as the alignments it allows.
_ANY_ALIGNMENT = {
    frozenset({"L", "NX", "C", "NY", "E"}): "evil",
    frozenset({"L", "NX", "C", "E"}): "evil",
    frozenset({"NX", "C", "G", "NY", "E"}): "nonlawful",
    frozenset({"C", "G", "NY", "E"}): "chaotic",
}


def creature_type(page: Page, data: Mapping[str, Any]) -> str:
    """The statblock's type line with its alignment: "…, neutral evil" / "…, neutral böse"."""
    text = kind_text(page, data.get("size") or [], coerce_type(data.get("type")))
    codes = [a for a in data.get("alignment") or [] if isinstance(a, str)]
    group = _ANY_ALIGNMENT.get(frozenset(codes)) if len(codes) > 2 else None
    if group:
        return f"{text}, {page.t(f'rules.align_any.{group}')}"
    words = [_word(page, f"rules.align.{'N' if a in ('NX', 'NY') else a}", a) for a in codes]
    neutral = _word(page, "rules.align.N", "neutral")
    if words == [neutral, neutral]:
        words = [neutral]
    align = " ".join(words)
    return f"{text}{', ' + align if align else ''}".strip()


def speed(page: Page, data: Mapping[str, Any]) -> str:
    """ "30 ft., fly 60 ft. (hover)" / "9 m, Fliegen 18 m (Schweben)"."""
    raw = data.get("speed") or {}
    if isinstance(raw, int):
        return page.u.distance(raw)
    parts = []
    for mode in ("walk", "burrow", "climb", "fly", "swim"):
        value = raw.get(mode)
        if value is None:
            continue
        if isinstance(value, dict):
            number = value.get("number")
            shown = page.u.distance(number) if isinstance(number, int | float) else ""
            text = f"{shown} {value.get('condition', '')}".strip()
        elif isinstance(value, int | float):
            text = page.u.distance(value)
        else:
            text = str(value)
        if mode == "fly" and raw.get("canHover"):
            text += " " + _word(page, "rules.speed.hover", "(hover)")
        label = _word(page, f"rules.speed.{mode}", mode)
        parts.append(text if mode == "walk" else f"{label} {text}")
    return ", ".join(parts)


def property_word(page: Page, word: str) -> str:
    """A weapon property: "finesse" / "Finesse", "two-handed" / "Zweihändig"."""
    return _word(page, f"rules.property.{word.lower()}", word)


def damage_word(page: Page, word: str) -> str:
    """A damage type: "fire" / "Feuer"; a word the vocabulary lacks stays as written."""
    return _word(page, f"rules.damage.{word.lower()}", word)


def sense(page: Page, text: str) -> str:
    """One sense: "darkvision 60 ft." / "Dunkelsicht 18 m"; anything else as written."""
    found = _SENSE.match(text.strip())
    if found:
        name, feet, rest = found.groups()
        word = _word(page, f"rules.sense.{name.lower()}", name)
        return f"{word} {page.u.distance(int(feet))}{rest}"
    passive = _PASSIVE.match(text.strip())
    if passive:
        return page.t("rules.passive", value=passive.group(1))
    return page.u.text(text)


def senses(page: Page, data: Mapping[str, Any]) -> list[str]:
    """Every sense, then passive Perception, as the statblock lists them."""
    out = [sense(page, str(x)) for x in data.get("senses") or []]
    out.append(page.t("rules.passive", value=data.get("passive", "—")))
    return out


def saves(page: Page, data: Mapping[str, Any]) -> str:
    """ "Dex +4, Wis +2" / "GE +4, WE +2"."""
    raw = data.get("save") or {}
    if page.lang == "en":
        return ", ".join(f"{k.title()} {v}" for k, v in raw.items())
    return ", ".join(f"{_word(page, f'abbr.{k}', k.title())} {v}" for k, v in raw.items())


def skills(page: Page, data: Mapping[str, Any]) -> str:
    """ "Stealth +6" / "Heimlichkeit +6"."""
    raw = data.get("skill") or {}
    return ", ".join(
        f"{k.title() if page.lang == 'en' else _word(page, f'skill.{k}', k.title())} {v}"
        for k, v in raw.items()
        if isinstance(v, str)
    )


def languages(page: Page, values: Sequence[Any]) -> list[Any]:
    """Languages by the glossary's names where a value is exactly one language."""
    return [
        (named(page, "language", value) or value) if isinstance(value, str) else value
        for value in values
    ]


def challenge(page: Page, data: Mapping[str, Any]) -> str:
    """ "1/4 (50 XP)" / "1/4 (50 EP)", the number in the reader's notation."""
    cr = coerce_cr(data.get("cr"))
    if cr is None:
        return "—"
    xp = cr.xp
    return page.t("rules.cr_xp", cr=cr.text, xp=page.t.number(xp)) if xp is not None else cr.text


def legendary_header(page: Page, data: Mapping[str, Any], english_name: str) -> Any:
    """The data's own header, or the standard sentence 2014 statblocks leave implied."""
    if data.get("legendaryHeader"):
        return data["legendaryHeader"]
    if not data.get("legendary"):
        return None
    short = str(data.get("shortName") or english_name)
    # English reads "The adult red dragon can take …"; German names it as the glossary does.
    name = short.lower() if page.lang == "en" else named(page, "monster", english_name) or short
    count = data.get("legendaryActions", 3)
    return [page.t("rules.legendary_header", name=name, count=count)]


def dice(page: Page, text: str) -> str:
    """Dice in the reader's notation: "1d8 + 3" / "1W8 + 3" — and the conditions that bent
    a roll ("[poisoned]") in the reader's words ("[Vergiftet]")."""

    def conditions(match: re.Match[str]) -> str:
        names = [_word(page, f"condition.{c}", c) for c in match.group(1).split(", ")]
        return f"[{', '.join(names)}]"

    return _BENT.sub(conditions, dice_text(text, dice_letter(page.t)))


_BENT = re.compile(r"\[([a-z]+(?:, [a-z]+)*)\]")


def joined(values: Iterable[str]) -> str:
    """Comma-separated, as statblock lists are."""
    return ", ".join(values)


__all__ = [
    "challenge",
    "creature_type",
    "damage_word",
    "dice",
    "joined",
    "kind_text",
    "languages",
    "legendary_header",
    "saves",
    "school",
    "sense",
    "senses",
    "skills",
    "speed",
    "spell_duration",
    "spell_range",
    "spell_time",
]
