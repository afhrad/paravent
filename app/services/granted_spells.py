"""Spells a species, background or feat grants: the corpus's ``additionalSpells``.

One reader for every record that carries it — 61 feats, 79 species and subspecies, 15
backgrounds. A record holds one block, or several to choose from (Magic Initiate's
"Cleric Spells", "Druid Spells"…; a 2024 elf's lineages). A block says:

* ``ability`` — the spellcasting ability: ``"wis"``, ``{"choose": ["int", "wis",
  "cha"]}`` (the player picks), or absent.
* ``known`` / ``prepared`` / ``innate`` — keyed by the character level the spells come at
  (``"_"`` from the start, ``"3"`` at 3rd level), each a list of spells: a fixed one
  (``"thaumaturgy#c"``, ``#c`` a cantrip) or a choice — ``{"choose": "level=0|class=Wizard",
  "count": 2}`` filtered by level, class list, school, ritual and spell attack, or
  ``{"choose": {"from": [...], "count": 2}}``. ``innate`` nests how often: ``daily``
  (``"1"`` once, ``"1e"`` each once, ``"pb"`` proficiency-bonus times), ``rest``, ``will``
  (at will), ``ritual``.
* ``expanded`` — spells added to a class's list, not granted; not read here.

The planner turns a block's choices into questions; the sheet turns the answers (kept in
``spells.granted``) and the fixed spells into spells with their own ability.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.base import ABILITIES

# How often an innate spell may be cast: "" (as the class casts it), "will", "daily",
# "rest" or "ritual".
Use = str
# 5eTools's spell-attack codes in a filter: melee, ranged, other.
_ATTACK = {"m": "M", "r": "R", "o": "O"}


@dataclass(frozen=True)
class SpellFilter:
    """Which spells a choice offers."""

    levels: frozenset[int] = frozenset()
    classes: tuple[str, ...] = ()
    schools: frozenset[str] = frozenset()
    ritual: bool = False
    attack: frozenset[str] = frozenset()
    # ``{"from": [...]}``: these spells only, as ``name`` or ``name|source``.
    names: tuple[str, ...] = ()

    def admits(self, spell: Mapping[str, Any], on_list: Any = None) -> bool:
        """True when ``spell`` (a corpus record) passes; ``on_list(class_name)`` says
        whether it is on that class's list."""
        if self.names:
            name = str(spell.get("name", "")).lower()
            return any(n.split("|")[0].lower() == name for n in self.names)
        if self.levels and spell.get("level") not in self.levels:
            return False
        if self.schools and str(spell.get("school", "")) not in self.schools:
            return False
        if self.ritual and not (spell.get("meta") or {}).get("ritual"):
            return False
        if self.attack and not self.attack & set(spell.get("spellAttack") or ()):
            return False
        if self.classes:
            return on_list is not None and any(on_list(c) for c in self.classes)
        return True

    @property
    def cantrips_only(self) -> bool:
        """True when only cantrips pass (``level=0``)."""
        return self.levels == frozenset({0})


@dataclass(frozen=True)
class Line:
    """One spell a block grants, or one choice of some."""

    at: int  # the character level it comes at; 0 from the start
    kind: str  # "known", "prepared" or "innate"
    use: Use = ""
    # How often, for innate spells: 1, 2…; -1 is "proficiency bonus times".
    times: int = 0
    spell: str = ""  # a fixed spell, "thaumaturgy" or "misty step|xphb"
    cantrip: bool = False
    choice: SpellFilter | None = None
    count: int = 1


@dataclass(frozen=True)
class Block:
    """One way a record grants spells."""

    name: str
    ability: str | None
    ability_options: tuple[str, ...]
    lines: tuple[Line, ...]

    def choices(self, level: int) -> list[tuple[int, Line]]:
        """The choices due by character ``level``, with their index in the block."""
        return [(i, x) for i, x in enumerate(self.lines) if x.choice and x.at <= level]

    def fixed(self, level: int) -> list[Line]:
        """The fixed spells due by character ``level``."""
        return [x for x in self.lines if x.spell and x.at <= level]


def blocks(data: Mapping[str, Any] | None) -> list[Block]:
    """The blocks of a record's ``additionalSpells`` (a block without spells is left out)."""
    raw = (data or {}).get("additionalSpells")
    out = [_block(b) for b in raw if isinstance(b, Mapping)] if isinstance(raw, list) else []
    return [b for b in out if b.lines]


def chosen_block(found: Sequence[Block], wanted: str | None) -> Block | None:
    """The block a record's grant uses: its only one, or the one ``wanted`` names —
    "Cleric Spells", or a feat variant's "cleric" (Magic Initiate; Cleric)."""
    if len(found) == 1:
        return found[0]
    if not wanted:
        return None
    key = wanted.lower()
    return next(
        (b for b in found if b.name.lower() == key),
        next((b for b in found if b.name.lower().startswith(key)), None),
    )


def cantrip_choice(data: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The cantrips a subclass offers one of, as its nameless blocks of one cantrip each:
    a Path of the Giant's druidcraft or thaumaturgy. The choice itself is only in its text."""
    found = blocks(data)
    if len(found) < 2 or any(b.name or len(b.lines) != 1 or not b.lines[0].spell for b in found):
        return ()
    return tuple(str(b.lines[0].spell) for b in found)


def class_grants(data: Mapping[str, Any] | None, level: int) -> list[Line]:
    """The spells a class record itself grants outright by class ``level``: its blocks'
    fixed prepared and innate spells (the 2024 paladin's Divine Smite). Choices (the 2014
    bard's Magical Secrets) and expanded lists are not grants."""
    return [
        line
        for block in blocks(data)
        for line in block.fixed(level)
        if line.kind in ("prepared", "innate")
    ]


def always_prepared(
    class_data: Mapping[str, Any] | None,
    subclass_data: Mapping[str, Any] | None,
    level: int,
    spell_list: str | None = None,
) -> list[str]:
    """The spells a class and its subclass always have prepared by class ``level``, as
    ``name`` or ``name|source`` (``#c`` dropped): the class's own grants, then the
    subclass's ``prepared`` spells. A subclass with several named lists (the Circle of
    the Land's terrains) gives only the list ``spell_list`` names; none is guessed."""
    out = [line.spell for line in class_grants(class_data, level)]
    raw = [b for b in (subclass_data or {}).get("additionalSpells") or [] if isinstance(b, Mapping)]
    if len(raw) > 1:
        raw = [b for b in raw if spell_list and b.get("name") == spell_list]
    if len(raw) == 1:
        for key, names in (raw[0].get("prepared") or {}).items():
            if str(key).isdigit() and int(key) <= level and isinstance(names, list):
                out.extend(n.split("#")[0] for n in names if isinstance(n, str))
    return out


def _block(raw: Mapping[str, Any]) -> Block:
    ability = raw.get("ability")
    fixed = ability if isinstance(ability, str) and ability in ABILITIES else None
    options: tuple[str, ...] = ()
    if isinstance(ability, Mapping) and isinstance(ability.get("choose"), list):
        options = tuple(a for a in ability["choose"] if a in ABILITIES)
    lines: list[Line] = []
    for kind in ("known", "prepared", "innate"):
        by_level = raw.get(kind)
        if not isinstance(by_level, Mapping):
            continue
        for key, value in by_level.items():
            at = int(key) if str(key).isdigit() else 0
            if isinstance(value, list):
                lines.extend(_lines(value, at, kind, "", 0))
            elif isinstance(value, Mapping):
                lines.extend(_innate(value, at, kind))
    return Block(str(raw.get("name") or ""), fixed, options, tuple(lines))


def _innate(value: Mapping[str, Any], at: int, kind: str) -> Iterable[Line]:
    """``{"daily": {"1": [...]}, "will": [...]}`` → lines with how often."""
    for how, inner in value.items():
        if how == "_" and isinstance(inner, list):
            yield from _lines(inner, at, kind, "", 0)
        elif how in ("will", "ritual") and isinstance(inner, list):
            yield from _lines(inner, at, kind, how, 0)
        elif how in ("daily", "rest") and isinstance(inner, Mapping):
            for count, spells in inner.items():
                text = str(count).rstrip("e")
                times = -1 if text == "pb" else int(text) if text.isdigit() else 1
                yield from _lines(spells if isinstance(spells, list) else [], at, kind, how, times)


def _lines(items: list[Any], at: int, kind: str, use: Use, times: int) -> Iterable[Line]:
    for item in items:
        if isinstance(item, str):
            name, _, flag = item.partition("#")
            yield Line(at, kind, use, times, spell=name.strip(), cantrip=flag == "c")
        elif isinstance(item, Mapping) and "choose" in item:
            spec = item["choose"]
            count = int(item.get("count", 1))
            if isinstance(spec, str):
                choice = _filter(spec)
            elif isinstance(spec, Mapping):
                choice = SpellFilter(names=tuple(str(n) for n in spec.get("from") or ()))
                count = int(spec.get("count", count))
            else:
                continue
            yield Line(at, kind, use, times, choice=choice, count=count)


def _filter(spec: str) -> SpellFilter:
    """``"level=0|class=Wizard|spell attack=m;r;o"`` → a filter."""
    fields: dict[str, list[str]] = {}
    for part in spec.split("|"):
        key, _, value = part.partition("=")
        fields[key.strip().lower()] = [v.strip() for v in value.split(";") if v.strip()]
    return SpellFilter(
        levels=frozenset(int(v) for v in fields.get("level", []) if v.isdigit()),
        classes=tuple(fields.get("class", [])),
        schools=frozenset(v.upper() for v in fields.get("school", [])),
        ritual="ritual" in [v.lower() for v in fields.get("components & miscellaneous", [])],
        attack=frozenset(
            _ATTACK[v.lower()] for v in fields.get("spell attack", []) if v.lower() in _ATTACK
        ),
    )


__all__ = [
    "Block",
    "Line",
    "SpellFilter",
    "always_prepared",
    "blocks",
    "chosen_block",
    "class_grants",
]
