"""A statblock action read as an attack: its to-hit bonus and the damage it deals.

5eTools writes an attack as tags in the action's text — ``{@hit 4}`` and
``{@damage 1d6 + 2}`` followed by the damage type, in both editions and unchanged in a
translation — so the tracker can roll it from the statblock (documentation/COMBAT.md).

"plus 7 ({@damage 2d6}) poison damage" adds a second roll; "or 8 ({@damage 1d10 + 3})
slashing damage if used with two hands" is an alternative, and damage dealt later ("at the
start of each of its turns") is another effect, so reading stops there.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.services.dice import is_expression
from app.services.sheet import CONDITIONS

_HIT = re.compile(r"\{@hit (-?\d+)")
_DAMAGE = re.compile(r"\{@damage ([^}|]+)[^}]*\}\)?\s*([A-Za-z]+)?")
_OR = re.compile(r"\bor\b", re.IGNORECASE)
_PLUS = re.compile(r"\bplus\b", re.IGNORECASE)
_DICE_TERM = re.compile(r"(\d*)d(\d+)")


@dataclass(frozen=True)
class Damage:
    """One damage roll of an attack: ``1d6+2`` slashing."""

    expression: str
    kind: str = ""


@dataclass(frozen=True)
class Attack:
    """What rolling an action needs."""

    hit: int
    damage: tuple[Damage, ...]


def _strings(entries: Any) -> Iterator[str]:
    if isinstance(entries, str):
        yield entries
    elif isinstance(entries, (list, tuple)):  # the sheet keeps a spell's entries as a tuple
        for item in entries:
            yield from _strings(item)
    elif isinstance(entries, dict):
        yield from _strings(entries.get("entries"))


def attack_of(entry: dict[str, Any]) -> Attack | None:
    """The attack an action describes, or None when it has no to-hit bonus."""
    text = " ".join(_strings(entry.get("entries")))
    hit = _HIT.search(text)
    if hit is None:
        return None
    # "or" between two rolls starts the alternatives (versatile, a different form).
    return Attack(int(hit.group(1)), _damage_after(text, hit.end()))


_DC = re.compile(r"\{@dc (\d+)")
_ACT_SAVE = re.compile(r"\{@actSave (str|dex|con|int|wis|cha)\}")
_SAVE_WORD = re.compile(
    r"\b(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) saving throw", re.IGNORECASE
)
_CONDITION = re.compile(r"\{@condition ([^}|]+)")
_NEXT_TURN = re.compile(r"until the (end|start) of [^.]{0,40}next turn", re.IGNORECASE)
_FOR = re.compile(r"\bfor (\d+) (round|minute)s?\b", re.IGNORECASE)
_CONDITIONS = frozenset(CONDITIONS)
_IMPOSES = re.compile(
    r"\b(becomes?|be|is|are|has|have|knocked|falls?|left|gains?|actSaveFail)\b", re.IGNORECASE
)
_NEGATION = re.compile(r"\b(can't|cannot|unless|isn't|aren't|immune|not|while)\b", re.IGNORECASE)
_REPEAT = re.compile(
    r"repeat\w*\s+the\s+(?:saving throw|save)[^.]{0,60}?\b(end|start) of each of"
    # A spell's way of saying it: "At the end of each of its turns, the target can make
    # another Wisdom saving throw" (Hold Person).
    r"|\b(end|start) of each of [^.]{0,30}?turns?,[^.]{0,40}?\banother\b[^.]{0,30}?sav",
    re.IGNORECASE,
)
_HALF = re.compile(r"half as much damage|\{@actSaveSuccess\}\s*Half damage", re.IGNORECASE)


@dataclass(frozen=True)
class SaveEffect:
    """An action every target saves against: a breath weapon, Thunderwave."""

    dc: int
    ability: str  # "dex"
    damage: tuple[Damage, ...]
    half: bool  # half damage on a success, else none
    # A condition a failure imposes ("frightened"), and for how many rounds (None: until
    # the gamemaster ends it).
    condition: str = ""
    rounds: int | None = None
    # "repeat the save at the end of each of its turns": "end" or "start", else "".
    repeat: str = ""


def save_of(entry: dict[str, Any]) -> SaveEffect | None:
    """The saving throw an action calls for and the damage it deals, or None — an attack,
    or a save with no damage (Frightful Presence is a condition, not a roll here)."""
    text = " ".join(_strings(entry.get("entries")))
    dc = _DC.search(text)
    if dc is None or _HIT.search(text):
        return None
    act = _ACT_SAVE.search(text)
    word = _SAVE_WORD.search(text)
    ability = act.group(1) if act else word.group(1)[:3].lower() if word else ""
    if not ability:
        return None
    damage = _damage_after(text, dc.end())
    condition, rounds = _condition_after(text, dc.end())
    if not damage and not condition:
        return None
    repeat = _repeat(text, dc.end()) if condition else ""
    return SaveEffect(
        int(dc.group(1)),
        ability,
        damage,
        bool(_HALF.search(text)),
        condition,
        rounds,
        repeat,
    )


def _imposed(before: str) -> bool:
    """Whether the words before a condition put it on the target ("or be", "has the",
    "is knocked") rather than naming an exception ("if it isn't", "can't be")."""
    return bool(_IMPOSES.search(before)) and not _NEGATION.search(before)


def spell_effect(
    save: str | None,
    damage: str | None,
    damage_types: Sequence[str],
    entries: Any,
    dc: int,
) -> SaveEffect | None:
    """A character's spell as a save effect: its save, the caster's DC, its damage (as the
    sheet has it) and the condition its text imposes, read like a monster's action."""
    ability = (save or "").lower()[:3]
    if ability not in ("str", "dex", "con", "int", "wis", "cha"):
        return None
    text = " ".join(_strings(entries))
    rolls: tuple[Damage, ...] = ()
    expression = (damage or "").replace(" ", "")
    if is_expression(expression):
        rolls = (Damage(expression, damage_types[0].lower() if damage_types else ""),)
    condition, rounds = _condition_after(text, 0)
    if not rolls and not condition:
        return None
    return SaveEffect(
        dc,
        ability,
        rolls,
        bool(_HALF.search(text)),
        condition,
        rounds,
        _repeat(text, 0) if condition else "",
    )


def _condition_after(text: str, start: int) -> tuple[str, int | None]:
    """The first standard condition named after the DC, and its duration in rounds."""
    found = next(
        (
            m
            for m in _CONDITION.finditer(text, start)
            if m.group(1).lower() in _CONDITIONS
            and _imposed(text[max(0, m.start() - 40) : m.start()])
        ),
        None,
    )
    if found is None:
        return "", None
    rest = text[found.end() : found.end() + 160]
    if _NEXT_TURN.search(rest):
        return found.group(1).lower(), 1
    span = _FOR.search(rest)
    if span is None:
        return found.group(1).lower(), None
    amount = int(span.group(1)) * (10 if span.group(2).lower() == "minute" else 1)
    return found.group(1).lower(), amount if amount <= 100 else None


def _repeat(text: str, start: int) -> str:
    """ "end" or "start" when the text lets a target repeat its save each turn."""
    found = _REPEAT.search(text, start)
    return (found.group(1) or found.group(2)).lower() if found else ""


def _damage_after(text: str, start: int) -> tuple[Damage, ...]:
    damage: list[Damage] = []
    previous_end = start
    for match in _DAMAGE.finditer(text, start):
        between = text[previous_end : match.start()]
        # Only "plus" adds a roll; "or" begins alternatives, anything else (a later
        # turn's damage, a different form) is another effect.
        if damage and (_OR.search(between) or not _PLUS.search(between)):
            break
        expression = match.group(1).replace(" ", "")
        if is_expression(expression):
            damage.append(Damage(expression, (match.group(2) or "").lower()))
        previous_end = match.end()
    return tuple(damage)


def critical(expression: str) -> str:
    """The dice doubled, the modifier not: ``1d6+2`` → ``2d6+2``."""
    return _DICE_TERM.sub(lambda m: f"{int(m.group(1) or 1) * 2}d{m.group(2)}", expression)


def _plain(values: Any) -> set[str]:
    """A statblock's damage list, unconditional entries only: "fire", not
    ``{"resist": [...], "note": "from nonmagical attacks"}`` — that one is a judgement."""
    return {v.lower() for v in values or [] if isinstance(v, str)}


_SPELL = re.compile(r"\{@spell ([^|}]+)")
# Words a spell's name keeps small: "Protection from Evil and Good".
_SMALL = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)


def spell_title(name: str) -> str:
    """ "protection from evil and good" → "Protection from Evil and Good"."""
    words = name.strip().split()
    return " ".join(
        w if i and w.lower() in _SMALL else w[:1].upper() + w[1:] for i, w in enumerate(words)
    )


@dataclass(frozen=True)
class MonsterSpell:
    """A spell a creature's spellcasting lists, and how it casts it: the slot it uses
    (its level list, or "(level 4 version)"; None for the spell's own level), the save
    DC and spell attack bonus its block states, and its caster level (2014 statblocks
    say "a 9th-level spellcaster"; a cantrip's damage steps by it).

    ``pool`` names what a cast spends and ``uses`` how many it holds: the slots of a
    level (``slot:3``, shared by that level's spells), a "3/day each" use of its own, or
    a "3/day" pool its list shares. None for at will and cantrips."""

    name: str
    tag: str
    source: str | None
    slot: int | None
    dc: int | None
    hit: int | None
    caster_level: int
    pool: str | None = None
    uses: int | None = None


_SPELL_TAG = re.compile(r"\{@spell ([^|}]+)(?:\|([^|}]*))?[^}]*\}([^{]*)")
_VERSION = re.compile(r"level (\d) version", re.IGNORECASE)
_DC = re.compile(r"\{@dc (\d+)")
_HIT_TAG = re.compile(r"\{@hit \+?(-?\d+)")
_CASTER_LEVEL = re.compile(r"(\d+)(?:st|nd|rd|th)-level spellcaster")
# "3e" is three a day each, "3" three a day between the list's spells.
_PER = re.compile(r"(\d+)(e?)$")
# The keys of a block that count uses by day or rest, as 5eTools writes them.
_PER_KEYS = ("daily", "rest", "restLong")


def monster_spells(monster: Mapping[str, Any]) -> list[MonsterSpell]:
    """Every spell in a creature's spellcasting (at will, per day, by level), each once,
    in statblock order. A block that states no DC uses the one before it ("using the
    same spellcasting ability as Spellcasting")."""
    found: dict[str, MonsterSpell] = {}
    dc: int | None = None
    hit: int | None = None
    level = 1
    for index, block in enumerate(monster.get("spellcasting") or []):
        if not isinstance(block, Mapping):
            continue
        header = " ".join(_strings(block.get("headerEntries") or []))
        dc = int(m.group(1)) if (m := _DC.search(header)) else dc
        hit = int(m.group(1)) if (m := _HIT_TAG.search(header)) else hit
        level = int(m.group(1)) if (m := _CASTER_LEVEL.search(header)) else level
        # (slot, spells, pool, uses, each): what a list casts at and spends.
        lists: list[tuple[int | None, Any, str | None, int | None, bool]] = [
            (None, block.get("will") or [], None, None, False)
        ]
        for per in _PER_KEYS:
            for key, spells in (block.get(per) or {}).items():
                m = _PER.match(str(key))
                pool = f"{index}:{per}:{key}" if m else None
                uses = int(m.group(1)) if m else None
                lists.append((None, spells, pool, uses, bool(m and m.group(2))))
        for key, info in (block.get("spells") or {}).items():
            if not isinstance(info, Mapping):
                continue
            slot = int(key) if str(key).isdigit() and int(key) else None
            slots = info.get("slots")
            counted = slot is not None and isinstance(slots, int)
            lists.append(
                (
                    slot,
                    info.get("spells") or [],
                    f"slot:{slot}" if counted else None,
                    slots if counted else None,
                    False,
                )
            )
        for slot, spells, pool, uses, each in lists:
            for text in spells:
                for tag, source, after in _SPELL_TAG.findall(str(text)):
                    version = _VERSION.search(after)
                    name = spell_title(tag)
                    if name in found:
                        continue
                    found[name] = MonsterSpell(
                        name,
                        tag.strip(),
                        source.strip() or None,
                        int(version.group(1)) if version else slot,
                        dc,
                        hit,
                        level,
                        f"{pool}:{name}" if pool and each else pool,
                        uses,
                    )
    return list(found.values())


def spells_of(monster: Mapping[str, Any]) -> list[str]:
    """The names of the spells a creature's spellcasting lists, titled."""
    return [spell.name for spell in monster_spells(monster)]


def damage_list(monster: Mapping[str, Any], key: str) -> list[str]:
    """The damage types a statblock lists under ``key`` (``immune``, ``resist``,
    ``vulnerable``), its unconditional entries only."""
    return sorted(_plain(monster.get(key)))


def against(damage: Mapping[str, int], monster: Mapping[str, Any] | None) -> int:
    """The damage a creature takes: immune none, resistant half (rounded down),
    vulnerable double, per damage type. Untyped damage is taken as rolled."""
    if monster is None:
        return sum(damage.values())
    immune = _plain(monster.get("immune"))
    resist = _plain(monster.get("resist"))
    vulnerable = _plain(monster.get("vulnerable"))
    total = 0
    for kind, amount in damage.items():
        if kind in immune:
            continue
        if kind in resist:
            amount //= 2
        if kind in vulnerable:
            amount *= 2
        total += amount
    return total


Defence = Literal["immune", "resist", "vulnerable", "normal"]


def defence(kind: str, monster: Mapping[str, Any] | None) -> Defence:
    """What a creature's statblock does to one damage type: ``immune``, ``resist``,
    ``vulnerable`` or ``normal`` — the unconditional entries, as :func:`against` counts."""
    if monster is None:
        return "normal"
    if kind in _plain(monster.get("immune")):
        return "immune"
    if kind in _plain(monster.get("resist")):
        return "resist"
    if kind in _plain(monster.get("vulnerable")):
        return "vulnerable"
    return "normal"


def encode(attack: Attack) -> str:
    """The damage as one form field: ``1d6+2:slashing;2d6:poison``."""
    return ";".join(f"{d.expression}:{d.kind}" for d in attack.damage)


def decode(field: str) -> tuple[Damage, ...]:
    """The damage from a form field; anything that is not dice is dropped."""
    out: list[Damage] = []
    for part in field.split(";")[:6]:
        expression, _, kind = part.partition(":")
        if is_expression(expression) and (not kind or kind.isalpha()):
            out.append(Damage(expression, kind.lower()))
    return tuple(out)


__all__ = [
    "Attack",
    "Damage",
    "SaveEffect",
    "against",
    "damage_list",
    "defence",
    "attack_of",
    "critical",
    "decode",
    "encode",
    "save_of",
    "MonsterSpell",
    "monster_spells",
    "spell_title",
    "spells_of",
    "spell_effect",
]
