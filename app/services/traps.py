"""Traps and hazards sprung at the table: their effect read from the books' text and
carried out on the characters who set them off.

A trap's text says what it does in prose. The reader takes the parts that are
mechanics: a saving throw ("a {@dc 15} Dexterity saving throw, taking 22 ({@damage
4d10}) bludgeoning damage on a failed save, or half as much damage on a successful
one"), with the conditions the same sentence names; an attack ("an attack with a +8
bonus", "{@hit 8}") and the damage it deals; and the checks that find or undo it ("a
{@dc 15} Wisdom ({@skill Perception}) check"). A 2024 trap's *At Higher Levels* table
gives the damage and DC for the party's level.

Springing a trap rolls its damage once; each character it catches rolls its save on
its sheet (or the trap's attack is rolled against its Armor Class), takes the damage
(half on a save where the text says so, its resistances, immunities and
vulnerabilities counted), and gains the conditions on a failure. Everything is
logged. What the text leaves to the table (a pit's fall, a room that floods) is shown
as the books' text, and the gamemaster's.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import span
from app.services.dice import Dice
from app.services.protocol import ProtocolService
from app.services.text import strip_tags

ABILITIES = {
    "strength": "str",
    "dexterity": "dex",
    "constitution": "con",
    "intelligence": "int",
    "wisdom": "wis",
    "charisma": "cha",
}
_SAVE = re.compile(r"\{@dc (\d+)\}\s+(\w+)\s+saving throw", re.IGNORECASE)
_DAMAGE = re.compile(r"\{@damage ([^}|]+)[^}]*\}\)?\s+(\w+)\s+damage", re.IGNORECASE)
_HALF = re.compile(r"half as much damage", re.IGNORECASE)
_CONDITION = re.compile(r"\{@condition ([^}|]+)", re.IGNORECASE)
_ATTACK = re.compile(r"(?:\{@hit (\d+)\}|attack (?:roll )?with a \+(\d+) bonus)", re.IGNORECASE)
_CHECK = re.compile(r"\{@dc (\d+)\}\s+(\w+)\s+\(\{@skill ([^}|]+)[^}]*\}\)\s+check", re.IGNORECASE)
# A sentence ends at a full stop (one closing a bracket too), or where a bracketed
# remark starts after another: "(vision is irrelevant to this attack roll). (If…"
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z{(])|(?<=[.!?]\))\s+(?=[A-Z{(])|(?<=\))\s+(?=\()")
_HIT_FIRST = re.compile(r"\b(?:is hit|that is hit|hit by)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TrapSave:
    """A saving throw the trap calls for, and what failing it costs."""

    dc: int
    ability: str  # "dex"
    damage: str = ""  # dice, "4d10"
    damage_type: str = ""
    half: bool = False
    conditions: tuple[str, ...] = ()
    on_hit: bool = False  # only a creature the trap's attack hit makes it


@dataclass(frozen=True)
class TrapAttack:
    """An attack the trap makes against each creature it catches."""

    bonus: int
    damage: str = ""
    damage_type: str = ""


@dataclass(frozen=True)
class TrapCheck:
    """A check that finds or undoes the trap ("Wisdom (Perception), DC 15")."""

    dc: int
    ability: str
    skill: str


@dataclass(frozen=True)
class TrapEffect:
    """What a trap does, as far as its text says it in numbers."""

    saves: tuple[TrapSave, ...] = ()
    attack: TrapAttack | None = None
    checks: tuple[TrapCheck, ...] = ()
    levels: tuple[tuple[int, int, str, int | None], ...] = field(default=())

    @property
    def automatic(self) -> bool:
        return bool(self.saves or self.attack)

    def at_level(self, level: int) -> TrapEffect:
        """The trap scaled to a party of ``level`` by its *At Higher Levels* table."""
        row = next((r for r in self.levels if r[0] <= level <= r[1]), None)
        if row is None:
            return self
        _low, _high, dice, dc = row
        saves = tuple(
            replace(s, damage=dice or s.damage, dc=dc if dc is not None else s.dc)
            for s in self.saves
        )
        attack = replace(self.attack, damage=dice or self.attack.damage) if self.attack else None
        return replace(self, saves=saves, attack=attack)


def _strings(node: Any, skip: tuple[str, ...] = ()) -> list[str]:
    """Every string of a text's entries, but those under the sections ``skip`` names."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [s for child in node for s in _strings(child, skip)]
    if isinstance(node, dict):
        if str(node.get("name", "")).strip().lower() in skip or node.get("type") == "table":
            return []
        return [s for key in ("entries", "items", "entry") for s in _strings(node.get(key), skip)]
    return []


def read_trap(data: dict[str, Any]) -> TrapEffect:
    """What a trap or hazard does, from its text (see the module's note)."""
    entries = data.get("entries")
    effect_text = _strings(entries, ("detect and disarm", "countermeasures", "at higher levels"))
    saves: list[TrapSave] = []
    attack: TrapAttack | None = None
    for paragraph in effect_text:
        for sentence in _SENTENCE.split(paragraph):
            hit = _ATTACK.search(sentence)
            if hit is not None and attack is None:
                attack = TrapAttack(int(hit[1] or hit[2]))
            save = _SAVE.search(sentence)
            if save is None or save[2].lower() not in ABILITIES:
                damage = _DAMAGE.search(sentence)
                if damage is not None and attack is not None and not attack.damage:
                    attack = replace(
                        attack, damage=damage[1].strip(), damage_type=damage[2].lower()
                    )
                continue
            before, after = sentence[: save.start()], sentence[save.end() :]
            # Damage written before the save is the attack's ("a target that is hit
            # takes 1d4 piercing damage and must succeed on…"); after it, the save's.
            first = _DAMAGE.search(before)
            if first is not None and attack is not None and not attack.damage:
                attack = replace(attack, damage=first[1].strip(), damage_type=first[2].lower())
            damage = _DAMAGE.search(after) or (first if attack is None else None)
            saves.append(
                TrapSave(
                    dc=int(save[1]),
                    ability=ABILITIES[save[2].lower()],
                    damage=damage[1].strip() if damage else "",
                    damage_type=damage[2].lower() if damage else "",
                    half=bool(_HALF.search(after)),
                    conditions=tuple(
                        dict.fromkeys(c.strip().lower() for c in _CONDITION.findall(sentence))
                    ),
                    on_hit=attack is not None and bool(_HIT_FIRST.search(before)),
                )
            )
    checks = tuple(
        dict.fromkeys(
            TrapCheck(int(m[1]), ABILITIES.get(m[2].lower(), m[2].lower()), m[3].strip().lower())
            for text in _strings(entries)
            for m in _CHECK.finditer(text)
        )
    )
    return TrapEffect(tuple(saves), attack, checks, _levels(entries))


def _levels(entries: Any) -> tuple[tuple[int, int, str, int | None], ...]:
    """The *At Higher Levels* table: ``(low, high, damage dice, save DC)`` per row."""
    out: list[tuple[int, int, str, int | None]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if node.get("type") == "table" and node.get("colLabels"):
                labels = [strip_tags(str(c)).lower() for c in node["colLabels"]]
                if not labels or "level" not in labels[0]:
                    return
                dc_at = next((n for n, c in enumerate(labels) if "dc" in c), None)
                harm_at = next((n for n, c in enumerate(labels) if "damage" in c), None)
                for row in node.get("rows", []):
                    if not isinstance(row, list):
                        continue
                    bounds = span(str(row[0]))
                    if bounds is None:
                        continue
                    dice = ""
                    if harm_at is not None:
                        found = re.search(r"\{@damage ([^}|]+)", str(row[harm_at]))
                        dice = found[1].strip() if found else ""
                    dc_text = strip_tags(str(row[dc_at])) if dc_at is not None else ""
                    dc = int(dc_text) if dc_text.isdigit() else None
                    out.append((bounds[0], bounds[1], dice, dc))
            for key in ("entries", "items"):
                walk(node.get(key))

    walk(entries)
    return tuple(out)


@dataclass
class Caught:
    """What the trap did to one character."""

    character_id: str
    name: str
    roll: int | None = None  # the save's or the attack's total
    against: int | None = None  # the DC, or the character's AC
    failed: bool = True  # failed the save, or was hit
    damage: int = 0
    damage_type: str = ""
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Target:
    """A character a trap catches, with what the trap needs of its sheet."""

    character_id: str
    name: str
    ac: int = 10
    resist: tuple[str, ...] = ()
    immune: tuple[str, ...] = ()
    vulnerable: tuple[str, ...] = ()


class TrapService:
    """Springs traps on characters, through their sheets, and logs what happened."""

    def __init__(
        self,
        protocol: ProtocolService | None,
        *,
        save: Callable[[str, str], int],
        hurt: Callable[[str, int], None],
        afflict: Callable[[str, str], None],
        dice: Dice | None = None,
    ) -> None:
        """``save(character, ability)`` rolls a saving throw on the sheet and gives the
        total; ``hurt(character, amount)`` deals damage; ``afflict(character,
        condition)`` puts a condition on it (if not already)."""
        self._protocol = protocol
        self._save = save
        self._hurt = hurt
        self._afflict = afflict
        self._dice = dice or Dice()

    def spring(
        self,
        world_id: str,
        name: str,
        effect: TrapEffect,
        targets: Sequence[Target],
        by: str,
        by_name: str,
    ) -> list[Caught]:
        """Carry the trap out on ``targets``: each save in turn (damage rolled once for
        all), or the attack against each; what it did to each, logged."""
        out: list[Caught] = []
        lines = [f"trap:sprung:{_safe(name)}"]
        for target in targets:
            caught = Caught(target.character_id, target.name)
            if effect.attack is not None:
                roll = self._dice.d20(effect.attack.bonus).total
                caught.roll, caught.against = roll, target.ac
                caught.failed = roll >= target.ac
                if caught.failed and effect.attack.damage:
                    caught.damage = _taken(
                        self._dice.roll(effect.attack.damage).total,
                        effect.attack.damage_type,
                        target,
                    )
                    caught.damage_type = effect.attack.damage_type
                out.append(caught)
                lines.append(_line("attack", caught))
        rolled = {
            n: self._dice.roll(s.damage).total if s.damage else 0
            for n, s in enumerate(effect.saves)
        }
        hit = {c.character_id for c in out if c.failed}
        for target in targets if effect.saves else ():
            for n, save in enumerate(effect.saves):
                if save.on_hit and target.character_id not in hit:
                    continue
                total = self._save(target.character_id, save.ability)
                failed = total < save.dc
                full = rolled[n]
                dealt = full if failed else (full // 2 if save.half else 0)
                caught = Caught(
                    target.character_id,
                    target.name,
                    roll=total,
                    against=save.dc,
                    failed=failed,
                    damage=_taken(dealt, save.damage_type, target) if dealt else 0,
                    damage_type=save.damage_type,
                    conditions=save.conditions if failed else (),
                )
                out.append(caught)
                lines.append(_line("save", caught))
        for caught in out:
            if caught.damage:
                self._hurt(caught.character_id, caught.damage)
            for condition in caught.conditions:
                self._afflict(caught.character_id, condition)
        if self._protocol is not None:
            self._protocol.append(
                [
                    ProtocolEntry(
                        actor=by,
                        actor_name=by_name,
                        kind=EntryKind.CUSTOM,
                        what=line,
                        visibility=Visibility.PUBLIC,
                        scope=Scope(world=world_id),
                    )
                    for line in lines
                ]
            )
        return out


def _taken(amount: int, damage_type: str, target: Target) -> int:
    """Damage after the character's immunities, resistances and vulnerabilities."""
    if damage_type in target.immune:
        return 0
    if damage_type in target.resist:
        amount //= 2
    if damage_type in target.vulnerable:
        amount *= 2
    return amount


def _line(kind: str, caught: Caught) -> str:
    """``trap:<save|attack>:<name>:<roll>:<against>:<failed>:<damage>:<type>:<conditions>``"""
    return (
        f"trap:{kind}:{_safe(caught.name)}:{caught.roll}:{caught.against}:{int(caught.failed)}:"
        f"{caught.damage}:{caught.damage_type}:{','.join(caught.conditions)}"
    )


def _safe(text: str) -> str:
    return text.replace(":", " ")


__all__ = [
    "Caught",
    "Target",
    "TrapAttack",
    "TrapCheck",
    "TrapEffect",
    "TrapSave",
    "TrapService",
    "read_trap",
]
