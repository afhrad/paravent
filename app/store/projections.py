"""Typed projections over the polymorphic corpus fields (documentation/TECHSTACK.md §6c).

The corpus spells one fact several ways. Measured across all 4,458 monsters:

- ``cr`` is a string (``"1/4"``), a dict carrying lair/coven/xp variants, ``"Unknown"``,
  or absent (summons, sidekicks — 102 entries)
- ``type`` is a string, or a dict whose ``type`` is itself a string *or* ``{"choose": [...]}``,
  with ``tags`` that mix strings and ``{"tag", "prefix"}`` objects
- ``size`` is a list, because a few creatures are "Medium or Small"

Each shape is coerced **once**, here, into a frozen projection, so no feature code ever
branches on shape again. The projections keep a reference to their entry; they add typed
fields beside it, they never replace it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from functools import cached_property
from typing import Any

# Challenge rating → experience points (PHB/MM). Prose upstream until the `cr` table loads.
XP_BY_CR: dict[str, int] = {
    "0": 10,
    "1/8": 25,
    "1/4": 50,
    "1/2": 100,
    "1": 200,
    "2": 450,
    "3": 700,
    "4": 1100,
    "5": 1800,
    "6": 2300,
    "7": 2900,
    "8": 3900,
    "9": 5000,
    "10": 5900,
    "11": 7200,
    "12": 8400,
    "13": 10000,
    "14": 11500,
    "15": 13000,
    "16": 15000,
    "17": 18000,
    "18": 20000,
    "19": 22000,
    "20": 25000,
    "21": 33000,
    "22": 41000,
    "23": 50000,
    "24": 62000,
    "25": 75000,
    "26": 90000,
    "27": 105000,
    "28": 120000,
    "29": 135000,
    "30": 155000,
}

# Item type codes as the corpus writes them, without their "|SOURCE" suffix. The corpus
# names them in items-base.json's `itemType` (corpus Phase 2); until that loads, here.
ITEM_KIND_NAMES: dict[str, str] = {
    "M": "Melee weapon",
    "R": "Ranged weapon",
    "LA": "Light armor",
    "MA": "Medium armor",
    "HA": "Heavy armor",
    "S": "Shield",
    "A": "Ammunition",
    "AF": "Ammunition",
    "G": "Adventuring gear",
    "AT": "Artisan's tools",
    "T": "Tools",
    "GS": "Gaming set",
    "INS": "Instrument",
    "P": "Potion",
    "RG": "Ring",
    "RD": "Rod",
    "SC": "Scroll",
    "ST": "Staff",
    "WD": "Wand",
    "SCF": "Spellcasting focus",
    "$": "Treasure",
    "TAH": "Tack and harness",
    "MNT": "Mount",
    "VEH": "Vehicle",
    "FD": "Food and drink",
    "TG": "Trade good",
    "EXP": "Explosive",
    "OTH": "Other",
    "$A": "Art object",
    "$G": "Gemstone",
    "$C": "Coinage",
    "TB": "Trade bar",
    "SHP": "Ship",
    "AIR": "Airship",
    "SPC": "Spelljammer equipment",
}

ITEM_RARITIES = ("common", "uncommon", "rare", "very rare", "legendary", "artifact")


@dataclass(frozen=True, slots=True)
class ChallengeRating:
    """A challenge rating as a number, with the text it was written as.

    ``lair`` and ``coven`` are the alternative ratings some creatures carry when fought
    in their lair or as part of a coven; ``xp`` is the entry's own override, if any.
    """

    text: str
    value: Fraction
    lair: Fraction | None = None
    coven: Fraction | None = None
    xp_override: int | None = None

    @property
    def xp(self) -> int | None:
        """Experience for defeating the creature, from the entry or the standard table."""
        if self.xp_override is not None:
            return self.xp_override
        return XP_BY_CR.get(self.text)


def parse_cr_text(text: str) -> Fraction | None:
    """``"1/4"`` → 1/4, ``"5"`` → 5; anything else (``"Unknown"``, ``"—"``) → None."""
    try:
        return Fraction(text.strip())
    except (ValueError, ZeroDivisionError):
        return None


def coerce_cr(raw: Any) -> ChallengeRating | None:
    """Any of the corpus's CR shapes, or None when the creature has no usable rating."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        # The model may hand over its ChallengeRating object; read it as a dict.
        dumped = getattr(raw, "model_dump", None)
        raw = dumped() if dumped else raw
    if isinstance(raw, dict):
        text = str(raw.get("cr", ""))
        value = parse_cr_text(text)
        if value is None:
            return None
        lair, coven = raw.get("lair"), raw.get("coven")
        xp = raw.get("xp")
        return ChallengeRating(
            text=text,
            value=value,
            lair=parse_cr_text(str(lair)) if lair is not None else None,
            coven=parse_cr_text(str(coven)) if coven is not None else None,
            xp_override=int(xp) if isinstance(xp, int) else None,
        )
    text = str(raw)
    value = parse_cr_text(text)
    return ChallengeRating(text=text, value=value) if value is not None else None


@dataclass(frozen=True, slots=True)
class CreatureKind:
    """A creature type, normalized: one or more base types and the subtype tags."""

    types: tuple[str, ...]
    tags: tuple[str, ...] = ()
    swarm_size: str | None = None

    @property
    def text(self) -> str:
        """``humanoid (goblinoid)``, ``celestial or fiend``."""
        base = " or ".join(self.types)
        return f"{base} ({', '.join(self.tags)})" if self.tags else base


def coerce_type(raw: Any) -> CreatureKind:
    """A creature's type from a string, a dict, or a dict whose type is a choice."""
    if raw is None:
        return CreatureKind(types=())
    if not isinstance(raw, (str, dict)):
        dumped = getattr(raw, "model_dump", None)
        raw = dumped(by_alias=True) if dumped else str(raw)
    if isinstance(raw, str):
        return CreatureKind(types=(raw.lower(),))
    base = raw.get("type")
    if isinstance(base, dict):
        types = tuple(str(t).lower() for t in base.get("choose", []))
    else:
        types = (str(base).lower(),) if base else ()
    tags: list[str] = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, str):
            tags.append(tag)
        elif isinstance(tag, dict) and tag.get("tag"):
            prefix = tag.get("prefix")
            tags.append(f"{prefix} {tag['tag']}" if prefix else str(tag["tag"]))
    swarm = raw.get("swarmSize")
    return CreatureKind(types=types, tags=tuple(tags), swarm_size=swarm or None)


def coerce_codes(raw: Any) -> tuple[str, ...]:
    """A list-or-scalar field (``size``, ``environment``) as a tuple of strings."""
    if raw is None:
        return ()
    if isinstance(raw, (str, int)):
        return (str(raw),)
    return tuple(str(x) for x in raw if isinstance(x, (str, int)))


def first_code(raw: Any) -> str:
    """An item or property code without its source suffix: ``"M|XPHB"`` → ``"M"``."""
    return str(raw or "").split("|", 1)[0]


def _first_number(values: Any) -> int | None:
    """The first plain number in an AC list or an hp block."""
    if isinstance(values, dict):
        average = values.get("average")
        return int(average) if isinstance(average, int) else None
    for value in values or []:
        if isinstance(value, int):
            return value
        if isinstance(value, dict) and isinstance(value.get("ac"), int):
            return int(value["ac"])
    return None


def _data(entry: Any) -> dict[str, Any]:
    """The entry as the corpus wrote it, aliases and unknown fields included."""
    if isinstance(entry, dict):
        return entry
    dumped: dict[str, Any] = entry.model_dump(by_alias=True, exclude_none=True)
    return dumped


@dataclass(frozen=True, slots=True)
class MonsterProjection:
    """A creature, reduced to the typed fields browsing filters on."""

    entry: Any
    name: str
    source: str
    cr: ChallengeRating | None
    kind: CreatureKind
    sizes: tuple[str, ...]
    environments: tuple[str, ...]
    ac: int | None
    hp: int | None
    legendary: bool
    npc: bool


@dataclass(frozen=True, slots=True)
class SpellProjection:
    """A spell, reduced to level, school and the two tags people filter by."""

    entry: Any
    name: str
    source: str
    level: int
    school: str
    concentration: bool
    ritual: bool


@dataclass(frozen=True, slots=True)
class ItemProjection:
    """An item or base item: its kind code, rarity and whether it is magic."""

    entry: Any
    category: str
    name: str
    source: str
    kind: str
    rarity: str | None
    attunement: bool
    wondrous: bool

    @property
    def magic(self) -> bool:
        """True for anything with a real rarity (``none`` and ``unknown`` are mundane)."""
        return self.rarity not in (None, "none", "unknown")


def project_monster(entry: Any) -> MonsterProjection:
    """One monster's projection."""
    data = _data(entry)
    return MonsterProjection(
        entry=entry,
        name=str(data.get("name", "")),
        source=str(data.get("source", "")),
        cr=coerce_cr(data.get("cr")),
        kind=coerce_type(data.get("type")),
        sizes=coerce_codes(data.get("size")),
        environments=tuple(e.lower() for e in coerce_codes(data.get("environment"))),
        ac=_first_number(data.get("ac")),
        hp=_first_number(data.get("hp")),
        legendary=bool(data.get("legendary")),
        npc=bool(data.get("isNpc")),
    )


def project_spell(entry: Any) -> SpellProjection:
    """One spell's projection."""
    data = _data(entry)
    durations = data.get("duration") or []
    return SpellProjection(
        entry=entry,
        name=str(data.get("name", "")),
        source=str(data.get("source", "")),
        level=int(data.get("level", 0)),
        school=str(data.get("school", "")),
        concentration=any(isinstance(d, dict) and d.get("concentration") for d in durations),
        ritual=bool((data.get("meta") or {}).get("ritual")),
    )


def project_item(entry: Any, category: str) -> ItemProjection:
    """One item's projection; ``category`` is ``item`` or ``baseitem``."""
    data = _data(entry)
    rarity = data.get("rarity")
    return ItemProjection(
        entry=entry,
        category=category,
        name=str(data.get("name", "")),
        source=str(data.get("source", "")),
        kind=first_code(data.get("type")),
        rarity=str(rarity) if rarity is not None else None,
        attunement=bool(data.get("reqAttune")),
        wondrous=bool(data.get("wondrous")),
    )


@dataclass(frozen=True)
class Projections:
    """Every projection, built once per compendium load."""

    monsters: tuple[MonsterProjection, ...]
    spells: tuple[SpellProjection, ...]
    items: tuple[ItemProjection, ...]

    @classmethod
    def build(
        cls,
        monsters: Iterable[Any],
        spells: Iterable[Any],
        items: Iterable[tuple[str, Any]],
    ) -> Projections:
        """Project the given entries; ``items`` pairs each entry with its category."""
        return cls(
            monsters=tuple(project_monster(m) for m in monsters),
            spells=tuple(project_spell(s) for s in spells),
            items=tuple(project_item(entry, category) for category, entry in items),
        )

    @cached_property
    def _monster_index(self) -> dict[str, MonsterProjection]:
        return {f"{m.name}|{m.source}".lower(): m for m in self.monsters}

    @cached_property
    def _item_index(self) -> dict[str, ItemProjection]:
        # An item and a base item may share a key; the item (more specific) wins.
        index = {f"{i.name}|{i.source}".lower(): i for i in self.items if i.category != "item"}
        index.update(
            {f"{i.name}|{i.source}".lower(): i for i in self.items if i.category == "item"}
        )
        return index

    def monster(self, name: str, source: str) -> MonsterProjection | None:
        """The creature with exactly this name and source (any case)."""
        return self._monster_index.get(f"{name}|{source}".lower())

    def item(self, name: str, source: str) -> ItemProjection | None:
        """The item or base item with exactly this name and source (any case)."""
        return self._item_index.get(f"{name}|{source}".lower())


__all__ = [
    "ITEM_KIND_NAMES",
    "ITEM_RARITIES",
    "XP_BY_CR",
    "ChallengeRating",
    "CreatureKind",
    "ItemProjection",
    "MonsterProjection",
    "Projections",
    "SpellProjection",
    "coerce_codes",
    "coerce_cr",
    "coerce_type",
    "first_code",
    "parse_cr_text",
    "project_item",
    "project_monster",
    "project_spell",
]
