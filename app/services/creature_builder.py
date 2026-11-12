"""The creature builder (PROJECT.md "Generate custom monsters").

A :class:`~app.models.gamestate.CreatureDesign` is the record; everything a statblock
prints is derived from it here:

* **Numbers from the challenge rating.** The proficiency bonus is the CR's row in the
  *Dungeon Master's Guide*'s "Monster Statistics by Challenge Rating" (``msbcr.json``);
  hit points are the hit dice of the size's die plus Constitution; saves and skills add
  the bonus to the score's modifier.
* **Text with placeholders.** Traits and actions are written in the corpus's own
  creature-builder language — ``{@hit <$to_hit__str$>}``, ``DC <$dc__con$>``,
  ``<$damage_avg__4.5+str$> ({@damage 1d8<$damage_mod__str$>})`` — and filled when the
  statblock is made, so raising the CR or a score follows into every attack.
  :func:`link_numbers` turns a printed creature's fixed numbers into placeholders where
  they match its scores exactly; anything that does not match stays as printed.
* **The CR it plays at**, worked out as the guide describes: a defensive rating from
  effective hit points (resistances and immunities multiply them, monster features add)
  shifted one step per two points of AC from the table's, an offensive rating from damage
  per round shifted by the attack bonus or save DC, and their average. Damage per round
  is read from the filled actions: the best attack's damage times Multiattack's count.

Published, every design becomes a creature in ``library/homebrew/bestiary/
bestiary-paravent.json`` under the source ``Homebrew`` — homebrew is library data in
5eTools format (TECHSTACK.md §3) — and loads with the corpus: search, reference pages,
encounters, the tracker and NPC statblocks find it like any other. The file is rewritten
whole from the designs; edits made to it by hand are not kept.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.gamestate import (
    CreatureBlock,
    CreatureDesign,
    CreatureFeature,
    SourceSet,
)
from app.services.i18n import Translatable, Translator
from app.services.translation_targets import BUILDER_FIELDS, builder_key
from app.services.units import UnitFormat, units_for_language
from app.store.creature_tables import CreatureTables, CrRow, MonsterFeature, cr_value
from app.store.gamestate import GameStateStore
from app.store.jsonfile import write_json

HOMEBREW_SOURCE = "Homebrew"
HOMEBREW_FILE = Path("bestiary") / "bestiary-paravent.json"
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
SIZES = {"T": 4, "S": 6, "M": 8, "L": 10, "H": 12, "G": 20}
SPEEDS = ("walk", "fly", "swim", "climb", "burrow")
TYPES = (
    "aberration",
    "beast",
    "celestial",
    "construct",
    "dragon",
    "elemental",
    "fey",
    "fiend",
    "giant",
    "humanoid",
    "monstrosity",
    "ooze",
    "plant",
    "undead",
)
ALIGNMENTS = (
    "U", "A", "L G", "N G", "C G", "L N", "N", "C N", "L E", "N E", "C E",
)  # fmt: skip
DAMAGE_TYPES = (
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
)  # fmt: skip
CONDITIONS = (
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned", "prone",
    "restrained", "stunned", "unconscious",
)  # fmt: skip
BLOCK_KINDS = ("traits", "actions", "bonus", "reactions", "legendary")
DEFAULT_ATTACK = (
    "{@atk mw} {@hit <$to_hit__str$>} to hit, reach 5 ft., one target. "
    "{@h}<$damage_avg__4.5+str$> ({@damage 1d8<$damage_mod__str$>}) bludgeoning damage."
)
_PHYSICAL = {"bludgeoning", "piercing", "slashing"}
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,
}  # fmt: skip
# Multiattack as the languages a design may be written in name it.
_MULTIATTACK = ("multiattack", "mehrfachangriff")
_PLACEHOLDER = re.compile(r"<\$([^$]+)\$>")
_HIT = re.compile(r"\{@hit (-?\d+)\}")
_DAMAGE = re.compile(r"(\d+) \(\{@damage\s+(\d+)d(\d+)(?:\s*([+-])\s*(\d+))?\s*\}\)")
_DAMAGE_SHOWN = re.compile(r"(\d+) \(\{@damage [^}]*\}\)")
_DC = re.compile(r"\{@dc (\d+)\}|\bDC (\d+)\b")
_DICE = re.compile(r"(\d+)d(\d+)")


@dataclass(frozen=True)
class Wording:
    """The language and units the builder's own templates write in: the creature's
    world's language, or the reader's for a creature of no world."""

    t: Translator

    @property
    def u(self) -> UnitFormat:
        """Distances in the language's units (English feet, otherwise metres)."""
        return UnitFormat(units_for_language(self.t.code), self.t.code)

    def subject(self, design: CreatureDesign) -> str:
        """How a text names the creature: the corpus's placeholder in English ("The
        lich", following a rename), its name in any other language."""
        return "<$title_short_name$>" if self.t.code == "en" else design.name

    def __call__(self, key: str, /, **params: Any) -> str:
        """The template ``creatures.text.<key>``."""
        return self.t(f"creatures.text.{key}", **params)

    def ready_made(
        self, design: CreatureDesign, kind: str, name: str, title: str, entries: list[Any]
    ) -> CreatureBlock:
        """One of the corpus's ready-made texts (``kind``: trait, action or feature) in
        this language where the table translated it (TRANSLATION.md §6 note), its
        distances in these units, and named as :meth:`subject` names the creature."""
        data = {"name": title, "entries": entries}
        shown = self.t.overlay("builder", builder_key(kind, name), data, BUILDER_FIELDS).data
        title, text = str(shown["name"]), entries_text(list(shown["entries"]))
        if self.t.code != "en":
            text = _NAME_PLACEHOLDER.sub(lambda _: design.name, text)
        return CreatureBlock(name=title, text=self.u.text(text))


# How the corpus's texts name the creature; another language writes its name instead.
_NAME_PLACEHOLDER = re.compile(r"<\$(?:title_short_name|short_name|title_name|name)\$>")


# What is a design's own and never copied or compared between a creature and its revision.
_OWN_FIELDS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "published_at",
        "published_hash",
        "drafted_by",
        "revision_of",
    }
)


class CreatureError(Translatable):
    """A change a design cannot take."""


class CreatureNotFound(LookupError):
    """No design with that id."""


def modifier(score: int) -> int:
    """An ability score's modifier."""
    return (score - 10) // 2


def signed(value: int) -> str:
    """``3`` → ``"+3"``, ``-1`` → ``"-1"``."""
    return f"{value:+d}"


# -- 5eTools expressions (monster features) ---------------------------------------------

_TOKEN = re.compile(
    r"\s*(Math\.floor|\d+(?:\.\d+)?|[A-Za-z_]\w*|===|==|<=|>=|&&|\|\||[-+*/()?:<>])"
)


class _Expression:
    """A tiny evaluator for the arithmetic 5eTools writes in ``monsterfeatures.json``:
    numbers, names, ``+ - * /``, comparisons, ``&& ||``, ``?:`` and ``Math.floor``.
    Anything else raises ValueError — never ``eval``."""

    def __init__(self, text: str, names: dict[str, float]) -> None:
        self.tokens: list[str] = []
        position = 0
        text = text.strip()
        while position < len(text):
            found = _TOKEN.match(text, position)
            if found is None:
                raise ValueError(f"cannot read {text!r}")
            self.tokens.append(found[1])
            position = found.end()
        self.names = names
        self.i = 0

    def _peek(self) -> str:
        return self.tokens[self.i] if self.i < len(self.tokens) else ""

    def _take(self, expected: str | None = None) -> str:
        token = self._peek()
        if expected is not None and token != expected:
            raise ValueError(f"expected {expected!r}, found {token!r}")
        self.i += 1
        return token

    def value(self) -> float:
        result = self._ternary()
        if self.i != len(self.tokens):
            raise ValueError("trailing tokens")
        return result

    def _ternary(self) -> float:
        condition = self._or()
        if self._peek() == "?":
            self._take()
            yes = self._ternary()
            self._take(":")
            no = self._ternary()
            return yes if condition else no
        return condition

    def _or(self) -> float:
        left = self._and()
        while self._peek() == "||":
            self._take()
            right = self._and()
            left = float(bool(left) or bool(right))
        return left

    def _and(self) -> float:
        left = self._compare()
        while self._peek() == "&&":
            self._take()
            right = self._compare()
            left = float(bool(left) and bool(right))
        return left

    def _compare(self) -> float:
        left = self._add()
        operator = self._peek()
        if operator in ("<", ">", "<=", ">=", "==", "==="):
            self._take()
            right = self._add()
            checks: dict[str, Callable[[float, float], bool]] = {
                "<": lambda a, b: a < b,
                ">": lambda a, b: a > b,
                "<=": lambda a, b: a <= b,
                ">=": lambda a, b: a >= b,
                "==": lambda a, b: a == b,
                "===": lambda a, b: a == b,
            }
            return float(checks[operator](left, right))
        return left

    def _add(self) -> float:
        left = self._multiply()
        while self._peek() in ("+", "-"):
            operator = self._take()
            right = self._multiply()
            left = left + right if operator == "+" else left - right
        return left

    def _multiply(self) -> float:
        left = self._unary()
        while self._peek() in ("*", "/"):
            operator = self._take()
            right = self._unary()
            left = left * right if operator == "*" else (left / right if right else 0.0)
        return left

    def _unary(self) -> float:
        if self._peek() == "-":
            self._take()
            return -self._unary()
        return self._primary()

    def _primary(self) -> float:
        token = self._take()
        if token == "(":
            inner = self._ternary()
            self._take(")")
            return inner
        if token == "Math.floor":
            self._take("(")
            inner = self._ternary()
            self._take(")")
            return float(math.floor(inner))
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            return float(token)
        if token in self.names:
            return self.names[token]
        raise ValueError(f"unknown name {token!r}")


def evaluate(expression: str, names: dict[str, float]) -> float:
    """A monster feature's effect, or 0 when there is none or it cannot be read."""
    if not expression.strip():
        return 0.0
    try:
        return _Expression(expression, names).value()
    except (ValueError, IndexError):
        return 0.0


# -- placeholders ------------------------------------------------------------------------


@dataclass(frozen=True)
class Numbers:
    """What the placeholders are filled from."""

    name: str
    pb: int
    mods: dict[str, int]

    def fill(self, text: str) -> str:
        """Replace every placeholder this creature can answer; leave the rest."""

        def one(match: re.Match[str]) -> str:
            value = self._one(match[1])
            return match[0] if value is None else value  # "" is an answer: a +0

        return _PLACEHOLDER.sub(one, text)

    def _one(self, key: str) -> str | None:
        short = f"the {self.name.lower()}"
        if key == "short_name":
            return short
        if key == "title_short_name":
            return short[:1].upper() + short[1:]
        if key == "name":
            return self.name
        kind, _, ability = key.partition("__")
        if not ability and key.startswith("dc_"):  # one record writes "dc_wis"
            kind, ability = "dc", key[3:]
        if kind == "damage_avg":
            base, _, ability = ability.partition("+")
            if ability and ability not in self.mods:
                return None
            try:  # no ability: the dice's own average ("10.5" for 3d6)
                return str(math.floor(float(base) + self.mods.get(ability, 0)))
            except ValueError:
                return None
        if ability not in self.mods:
            return None
        mod = self.mods[ability]
        if kind == "to_hit":
            return str(self.pb + mod)
        if kind == "dc":
            return str(8 + self.pb + mod)
        if kind == "damage_mod":
            return "" if mod == 0 else f" {'+' if mod > 0 else '-'} {abs(mod)}"
        return None


def link_numbers(text: str, pb: int, mods: dict[str, int]) -> str:
    """A printed creature's text with the numbers that follow its scores made into
    placeholders: ``{@hit 5}`` that equals proficiency + STR becomes
    ``{@hit <$to_hit__str$>}``; ``7 ({@damage 1d8 + 3})`` with STR +3 becomes
    ``<$damage_avg__4.5+str$> ({@damage 1d8<$damage_mod__str$>})``; a DC that equals
    8 + proficiency + a modifier names that ability. Numbers that match nothing stay."""
    attack_order = ("str", "dex", "con", "int", "wis", "cha")
    used: list[str] = []

    def hit(match: re.Match[str]) -> str:
        value = int(match[1])
        ability = next((a for a in attack_order if pb + mods.get(a, 0) == value), None)
        if ability is None:
            return match[0]
        used.append(ability)
        return f"{{@hit <$to_hit__{ability}$>}}"

    def damage(match: re.Match[str]) -> str:
        shown, count, die, sign, flat = match.groups()
        bonus = (int(flat) if sign == "+" else -int(flat)) if flat else 0
        base = int(count) * (int(die) + 1) / 2
        order = [*used, *(a for a in attack_order if a not in used)]
        ability = next(
            (a for a in order if bonus and mods.get(a) == bonus),
            None,
        )
        if ability is None or math.floor(base + bonus) != int(shown):
            return match[0]
        return (
            f"<$damage_avg__{base:g}+{ability}$> "
            f"({{@damage {count}d{die}<$damage_mod__{ability}$>}})"
        )

    def dc(match: re.Match[str]) -> str:
        value = int(match[1] or match[2])
        for ability in ("con", "wis", "cha", "int", "str", "dex"):
            if 8 + pb + mods.get(ability, 0) == value:
                return f"{{@dc <$dc__{ability}$>}}" if match[1] else f"DC <$dc__{ability}$>"
        return match[0]

    text = _HIT.sub(hit, text)
    text = _DAMAGE.sub(damage, text)
    return _DC.sub(dc, text)


# -- entries ↔ text ----------------------------------------------------------------------


def entries_text(entries: Any) -> str:
    """5eTools entries as the builder's text: paragraphs split by a blank line."""
    parts: list[str] = []
    for entry in entries if isinstance(entries, list) else [entries]:
        if isinstance(entry, str):
            parts.append(entry)
        elif isinstance(entry, dict):
            if entry.get("type") == "list":
                for item in entry.get("items") or []:
                    if isinstance(item, str):
                        parts.append(f"• {item}")
                    elif isinstance(item, dict):
                        inner = entries_text(item.get("entries") or item.get("entry") or [])
                        name = str(item.get("name", "")).rstrip(".")
                        parts.append(f"• {name}. {inner}" if name else f"• {inner}")
            elif entry.get("entries"):
                inner = entries_text(entry["entries"])
                name = str(entry.get("name", "")).rstrip(".")
                parts.append(f"{name}. {inner}" if name else inner)
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def text_entries(text: str) -> list[str]:
    """The builder's text as entries: one per paragraph."""
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _spellcasting_block(raw: dict[str, Any]) -> tuple[str, CreatureBlock]:
    """A statblock's spellcasting as a trait (or an action, as 2024 books print it)."""
    lines = [entries_text(raw.get("headerEntries") or [])]
    will = raw.get("will") or []
    if will:
        lines.append(f"At will: {', '.join(str(s) for s in will)}")
    for per, spells in (raw.get("daily") or {}).items():
        count, each = per.rstrip("e"), per.endswith("e")
        lines.append(f"{count}/day{' each' if each else ''}: {', '.join(map(str, spells))}")
    for level, spells in sorted((raw.get("spells") or {}).items()):
        names = ", ".join(str(s) for s in spells.get("spells") or [])
        if level == "0":
            lines.append(f"Cantrips (at will): {names}")
        else:
            slots = spells.get("slots")
            suffix = f" ({slots} slots)" if slots else ""
            lines.append(f"Level {level}{suffix}: {names}")
    lines.append(entries_text(raw.get("footerEntries") or []))
    kind = "actions" if raw.get("displayAs") == "action" else "traits"
    name = str(raw.get("name") or "Spellcasting")
    return kind, CreatureBlock(name=name, text="\n\n".join(x for x in lines if x))


def _strings(values: Any, key: str) -> list[str]:
    """``resist``/``immune`` lists mix strings and ``{"resist": [...], "note": …}``."""
    out: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            out.extend(_strings(value.get(key), key))
    return out


def _cr_text(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("cr")
    text = str(raw or "")
    return text if re.fullmatch(r"\d+(/\d+)?", text) else "1"


# -- the derived creature -----------------------------------------------------------------


@dataclass
class CrEstimate:
    """The CR a design plays at, by the guide's method, with its working."""

    hp: int
    effective_hp: int
    ac: int
    effective_ac: int
    defensive: str
    dpr: int
    attack: int | None
    dc: int | None
    offensive: str
    final: str
    target: CrRow | None
    notes: list[str] = field(default_factory=list)


class CreatureMath:
    """Statblocks and CR estimates for designs, over the builder's tables."""

    def __init__(self, tables: CreatureTables, skill_abilities: dict[str, str]) -> None:
        """``skill_abilities`` maps a skill ("perception") to its ability ("wis")."""
        self._tables = tables
        self._skills = skill_abilities

    # -- the numbers -----------------------------------------------------------------

    def pb(self, design: CreatureDesign) -> int:
        """The proficiency bonus of the design's CR."""
        row = self._tables.row(design.cr)
        if row is not None:
            return row.pb
        return max(2, 2 + (int(cr_value(design.cr)) - 1) // 4)

    def mods(self, design: CreatureDesign) -> dict[str, int]:
        """Each ability's modifier."""
        return {a: modifier(design.abilities.get(a, 10)) for a in ABILITIES}

    def hit_die(self, design: CreatureDesign) -> int:
        """The size's hit die."""
        return SIZES.get(design.size, 8)

    def hp(self, design: CreatureDesign) -> tuple[int, str]:
        """Average hit points and the formula, ``(45, "6d10 + 12")``."""
        die = self.hit_die(design)
        flat = design.hit_dice * self.mods(design)["con"]
        average = max(1, math.floor(design.hit_dice * (die + 1) / 2) + flat)
        formula = f"{design.hit_dice}d{die}"
        if flat:
            formula += f" {'+' if flat > 0 else '-'} {abs(flat)}"
        return average, formula

    def numbers(self, design: CreatureDesign) -> Numbers:
        """What its placeholders are filled from."""
        return Numbers(design.name, self.pb(design), self.mods(design))

    def skill_bonus(self, design: CreatureDesign, skill: str) -> int:
        """A proficient skill's bonus."""
        ability = self._skills.get(skill.lower(), "wis")
        return self.mods(design)[ability] + self.pb(design)

    # -- the statblock ---------------------------------------------------------------

    def statblock(self, design: CreatureDesign) -> dict[str, Any]:
        """The design as a 5eTools creature."""
        fill = self.numbers(design).fill
        mods, pb = self.mods(design), self.pb(design)
        average, formula = self.hp(design)
        perception = (
            self.skill_bonus(design, "perception") if "perception" in design.skills else mods["wis"]
        )
        out: dict[str, Any] = {
            "name": design.name,
            "source": HOMEBREW_SOURCE,
            "size": [design.size],
            "type": {"type": design.type, "tags": design.type_tags}
            if design.type_tags
            else design.type,
            "alignment": [c for c in " ".join(design.alignment).split() if c],
            "ac": [{"ac": design.ac, "from": [design.ac_from]}] if design.ac_from else [design.ac],
            "hp": {"average": average, "formula": formula},
            "speed": {k: v for k, v in design.speed.items() if v > 0} or {"walk": 0},
            **{a: design.abilities.get(a, 10) for a in ABILITIES},
            "passive": 10 + perception,
            "cr": design.cr,
        }
        if design.saves:
            out["save"] = {a: signed(mods[a] + pb) for a in ABILITIES if a in design.saves}
        if design.skills:
            out["skill"] = {s: signed(self.skill_bonus(design, s)) for s in design.skills}
        senses = [s.strip() for s in design.senses.split(",") if s.strip()]
        if senses:
            out["senses"] = senses
        languages = [s.strip() for s in design.languages.split(",") if s.strip()]
        if languages:
            out["languages"] = languages
        for key, values in (
            ("resist", design.resist),
            ("immune", design.immune),
            ("vulnerable", design.vulnerable),
            ("conditionImmune", design.condition_immune),
        ):
            if values:
                out[key] = list(values)
        for key, blocks in (
            ("trait", design.traits),
            ("action", design.actions),
            ("bonus", design.bonus),
            ("reaction", design.reactions),
            ("legendary", design.legendary),
        ):
            made = [
                {"name": fill(b.name), "entries": text_entries(fill(b.text))}
                for b in blocks
                if b.name.strip() or b.text.strip()
            ]
            if made:
                out[key] = made
        if design.legendary:
            out["legendaryActions"] = 3
        return out

    # -- the CR it plays at ----------------------------------------------------------

    def _index(self, value: float, key: Callable[[CrRow], tuple[int, int]]) -> int:
        rows = self._tables.rows()
        for index, row in enumerate(rows):
            if value <= key(row)[1]:
                return index
        return len(rows) - 1

    def _shift(self, index: int, difference: int) -> int:
        steps = int(difference / 2)  # one step per two points, toward zero
        return max(0, min(len(self._tables.rows()) - 1, index + steps))

    def estimate(self, design: CreatureDesign) -> CrEstimate | None:
        """The guide's CR calculation; None without the table."""
        rows = self._tables.rows()
        if not rows:
            return None
        target = self._tables.row(design.cr)
        expected = float(cr_value(design.cr))
        hp, _formula = self.hp(design)
        names = {"hp": float(hp), "hpActual": float(hp), "expectedCr": expected, "trait": 0.0}
        extra = {"ac": 0.0, "hp": 0.0, "attack": 0.0, "dpr": 0.0}
        notes: list[str] = []
        for chosen in design.features:
            feature = self._tables.feature(chosen.name)
            if feature is None:
                continue
            names["trait"] = float(chosen.value or 0)
            for key in extra:
                extra[key] += evaluate(getattr(feature, key), names)
        # Defence: hit points, multiplied for resistances and immunities.
        multiplier = 1.0
        band = 0 if expected <= 4 else 1 if expected <= 10 else 2 if expected <= 16 else 3
        if _many(design.immune):
            multiplier = (2.0, 2.0, 1.5, 1.25)[band]
            notes.append("immune")
        elif _many(design.resist):
            multiplier = (2.0, 1.5, 1.25, 1.0)[band]
            notes.append("resist")
        effective_hp = math.floor(hp * multiplier + extra["hp"])
        ac = design.ac + int(extra["ac"])
        if len(design.saves) >= 5:
            ac += 4
            notes.append("saves")
        elif len(design.saves) >= 3:
            ac += 2
            notes.append("saves")
        defence = self._index(effective_hp, lambda r: (r.hp_min, r.hp_max))
        defence = self._shift(defence, ac - rows[defence].ac)
        # Offence: damage per round from the filled actions.
        statblock = self.statblock(design)
        dpr, attack, dc = read_offence(statblock.get("action") or [])
        if design.dpr_override is not None:
            dpr = design.dpr_override
            notes.append("dpr_override")
        dpr += int(extra["dpr"])
        if attack is not None:
            attack += int(extra["attack"])
        offence = self._index(dpr, lambda r: (r.dpr_min, r.dpr_max))
        if attack is not None:
            offence = self._shift(offence, attack - rows[offence].attack)
        elif dc is not None:
            offence = self._shift(offence, dc - rows[offence].save_dc)
        final = math.floor((defence + offence) / 2 + 0.5)
        return CrEstimate(
            hp=hp,
            effective_hp=effective_hp,
            ac=design.ac,
            effective_ac=ac,
            defensive=rows[defence].cr,
            dpr=dpr,
            attack=attack,
            dc=dc,
            offensive=rows[offence].cr,
            final=rows[final].cr,
            target=target,
            notes=notes,
        )

    def on_target(self, design: CreatureDesign, wording: Wording | None = None) -> CreatureDesign:
        """A blank design's attack sized to its CR: Strength for the table's attack
        bonus, and a slam (two from damage 30 up, with Multiattack) whose dice reach
        the middle of the table's damage per round."""
        row = self._tables.row(design.cr)
        slam_name = wording("slam") if wording else "Slam"
        if row is None:
            text = (
                DEFAULT_ATTACK
                if wording is None
                else attack_text(
                    ranged=False,
                    ability="str",
                    dice="1d8",
                    damage_type="bludgeoning",
                    wording=wording,
                )
            )
            return design.model_copy(update={"actions": [CreatureBlock(name=slam_name, text=text)]})
        strength = max(1, min(30, 10 + 2 * (row.attack - row.pb)))
        mod = modifier(strength)
        middle = (row.dpr_min + row.dpr_max) / 2
        attacks = 2 if middle >= 30 else 1
        wanted = middle / attacks - mod
        if wanted >= 4.5:
            count, die = max(1, round(wanted / 4.5)), 8
        else:
            count, die = 1, 4 if wanted < 3 else 6
        slam = attack_text(
            ranged=False,
            ability="str",
            dice=f"{count}d{die}",
            damage_type="bludgeoning",
            wording=wording,
        )
        actions = [CreatureBlock(name=slam_name, text=slam)]
        if attacks > 1:
            multiattack = (
                CreatureBlock(
                    name=wording("multiattack"),
                    text=wording("two_slams", name=wording.subject(design)),
                )
                if wording
                else CreatureBlock(
                    name="Multiattack", text="<$title_short_name$> makes two slam attacks."
                )
            )
            actions.insert(0, multiattack)
        abilities = {**design.abilities, "str": strength}
        return design.model_copy(update={"abilities": abilities, "actions": actions})

    def fit(self, design: CreatureDesign) -> CreatureDesign:
        """AC and hit dice set to the table's for the design's CR (the middle of its
        hit-point range); attacks follow through their placeholders."""
        row = self._tables.row(design.cr)
        if row is None:
            return design
        per_die = (self.hit_die(design) + 1) / 2 + self.mods(design)["con"]
        middle = (row.hp_min + row.hp_max) / 2
        dice = max(1, min(200, round(middle / per_die))) if per_die > 0 else design.hit_dice
        return design.model_copy(update={"ac": row.ac, "hit_dice": dice})


def _many(kinds: Sequence[str]) -> bool:
    """Resistances or immunities that count: the physical types, or three or more."""
    return bool(_PHYSICAL & set(kinds)) or len(kinds) >= 3


def read_offence(actions: Sequence[dict[str, Any]]) -> tuple[int, int | None, int | None]:
    """Damage per round, the best attack bonus and the best save DC in filled actions.

    Each action's damage is the sum of its ``N ({@damage …})`` figures; Multiattack's
    first number word says how many attacks a round makes, of the best one.
    """
    best_attack_damage, best_damage, count = 0, 0, 1
    attack: int | None = None
    dc: int | None = None
    for action in actions:
        text = " ".join(e for e in action.get("entries") or [] if isinstance(e, str))
        name = str(action.get("name", ""))
        if name.lower().startswith(_MULTIATTACK):
            words = re.findall(rf"\b({'|'.join(_NUMBER_WORDS)})\b", text.lower())
            if words:
                count = _NUMBER_WORDS[words[0]]
            continue
        damage = sum(int(m) for m in _DAMAGE_SHOWN.findall(text))
        best_damage = max(best_damage, damage)
        hit = _HIT.search(text)
        if hit:
            attack = max(attack or -99, int(hit[1]))
            best_attack_damage = max(best_attack_damage, damage)
        for found in _DC.finditer(text):
            dc = max(dc or 0, int(found[1] or found[2]))
    dpr = best_attack_damage * count if best_attack_damage else best_damage
    return dpr, attack, dc


def feature_block(feature: MonsterFeature) -> CreatureBlock:
    """A monster feature's text as a trait, about this creature."""
    text = " ".join(s for s in re.split(r"(?<=\.)\s+", feature.effect) if not s.startswith("Input"))
    text = text.replace("The creature", "<$title_short_name$>").replace(
        "the creature", "<$short_name$>"
    )
    return CreatureBlock(name=feature.name, text=text)


def from_monster(raw: dict[str, Any], pb: int) -> dict[str, Any]:
    """A design's fields from a printed creature (as the corpus wrote it, ``_copy``
    resolved), with its numbers linked to its scores where they match."""
    mods = {a: modifier(int(raw.get(a) or 10)) for a in ABILITIES}
    kind = raw.get("type")
    type_name, tags = "humanoid", []
    if isinstance(kind, str):
        type_name = kind
    elif isinstance(kind, dict):
        inner = kind.get("type")
        type_name = inner if isinstance(inner, str) else "monstrosity"
        tags = [t for t in kind.get("tags") or [] if isinstance(t, str)]
    ac_value, ac_from = 10, ""
    first_ac = (raw.get("ac") or [10])[0]
    if isinstance(first_ac, int):
        ac_value = first_ac
    elif isinstance(first_ac, dict):
        ac_value = int(first_ac.get("ac") or 10)
        ac_from = ", ".join(str(x) for x in first_ac.get("from") or [])
    size = (raw.get("size") or ["M"])[0]
    hp = raw.get("hp") or {}
    dice = _DICE.search(str(hp.get("formula", "")))
    if dice:
        hit_dice = int(dice[1])
    else:
        per_die = (SIZES.get(size, 8) + 1) / 2 + mods["con"]
        hit_dice = max(1, round(int(hp.get("average") or 1) / per_die)) if per_die > 0 else 1
    speed: dict[str, int] = {}
    for key, value in (raw.get("speed") or {}).items():
        number = value.get("number") if isinstance(value, dict) else value
        if key in SPEEDS and isinstance(number, int):
            speed[key] = number
    blocks: dict[str, list[CreatureBlock]] = {kind: [] for kind in BLOCK_KINDS}
    for key, kind_name in (
        ("trait", "traits"),
        ("action", "actions"),
        ("bonus", "bonus"),
        ("reaction", "reactions"),
        ("legendary", "legendary"),
    ):
        for block in raw.get(key) or []:
            if isinstance(block, dict):
                text = link_numbers(entries_text(block.get("entries") or []), pb, mods)
                blocks[kind_name].append(
                    CreatureBlock(name=str(block.get("name") or ""), text=text)
                )
    for spellcasting in raw.get("spellcasting") or []:
        if isinstance(spellcasting, dict):
            kind_name, block = _spellcasting_block(spellcasting)
            block = block.model_copy(update={"text": link_numbers(block.text, pb, mods)})
            blocks[kind_name].insert(0, block)
    alignment = [a for a in raw.get("alignment") or [] if isinstance(a, str)] or ["U"]
    return {
        "size": size if size in SIZES else "M",
        "type": type_name if type_name in TYPES else "monstrosity",
        "type_tags": tags,
        "alignment": alignment,
        "cr": _cr_text(raw.get("cr")),
        "ac": max(0, min(40, ac_value)),
        "ac_from": ac_from,
        "hit_dice": max(1, min(200, hit_dice)),
        "speed": speed or {"walk": 30},
        "abilities": {a: int(raw.get(a) or 10) for a in ABILITIES},
        "saves": [a for a in ABILITIES if a in (raw.get("save") or {})],
        "skills": [s for s in (raw.get("skill") or {}) if s != "other"],
        "senses": ", ".join(str(s) for s in raw.get("senses") or [] if isinstance(s, str)),
        "languages": ", ".join(str(s) for s in raw.get("languages") or [] if isinstance(s, str)),
        "resist": _strings(raw.get("resist"), "resist"),
        "immune": _strings(raw.get("immune"), "immune"),
        "vulnerable": _strings(raw.get("vulnerable"), "vulnerable"),
        "condition_immune": _strings(raw.get("conditionImmune"), "conditionImmune"),
        **blocks,
    }


# -- the designs in the library ------------------------------------------------------------

# ``(name, source)`` → the printed creature as the corpus wrote it, or None.
MonsterLookup = Callable[[str, str], dict[str, Any] | None]


@dataclass
class Published:
    """What a publish did."""

    count: int
    worlds: list[str] = field(default_factory=list)  # names of worlds given Homebrew


class CreatureService:
    """Creature designs in the library, and their homebrew statblocks."""

    def __init__(
        self,
        store: GameStateStore,
        math_: CreatureMath,
        tables: CreatureTables,
        homebrew_dir: Path,
        monster: MonsterLookup,
        reload: Callable[[], object],
    ) -> None:
        """Bind to the library, the maths, the homebrew folder and a corpus lookup;
        ``reload`` re-reads the compendium once the homebrew file is written."""
        self._store = store
        self.math = math_
        self._tables = tables
        self._homebrew = homebrew_dir
        self._monster = monster
        self._reload = reload

    def known_cr(self, cr: str) -> bool:
        """Whether the builder's table has this challenge rating ("5", "1/2")."""
        return self._tables.row(cr) is not None

    def feature_name(self, name: str) -> str | None:
        """A monster feature of the guide's table by its name, as the table spells it."""
        found = self._tables.feature(name)
        return found.name if found is not None else None

    def cr_order(self) -> builtins.list[str]:
        """Every challenge rating of the table, lowest first."""
        return [row.cr for row in self._tables.rows()]

    @property
    def homebrew_file(self) -> Path:
        """Where the statblocks are published."""
        return self._homebrew / HOMEBREW_FILE

    def list(self, world_id: str | None = None) -> builtins.list[CreatureDesign]:
        """Every design, or a world's, by name."""
        found = self._store.creatures.list(
            None if world_id is None else (lambda c: c.world_id == world_id)
        )
        return sorted(found, key=lambda c: c.name.casefold())

    def get(self, design_id: str) -> CreatureDesign:
        """One design, or :class:`CreatureNotFound`."""
        found = self._store.creatures.find(design_id)
        if found is None:
            raise CreatureNotFound(design_id)
        return found

    def _clean_name(
        self, name: str, own_id: str | None = None, revision_of: str | None = None
    ) -> str:
        cleaned = " ".join(name.split())[:80]
        if not cleaned:
            raise CreatureError("creatures.error.name")
        # A design shares its name only with its own revision.
        kin = {own_id, revision_of} - {None}
        clash = next(
            (
                c
                for c in self._store.creatures.list()
                if c.name.casefold() == cleaned.casefold()
                and c.id not in kin
                and not (own_id is not None and c.revision_of == own_id)
                and not (revision_of is not None and c.revision_of == revision_of)
            ),
            None,
        )
        if clash is not None:
            raise CreatureError("creatures.error.name_taken", name=cleaned)
        return cleaned

    def create(
        self,
        name: str,
        world_id: str | None,
        cr: str,
        based_on: str = "",
        wording: Wording | None = None,
    ) -> CreatureDesign:
        """A new design: from a printed creature, or blank with the CR's numbers (its
        slam written in ``wording``'s language and units)."""
        fields: dict[str, Any] = {}
        ref = based_on.strip()
        if ref:
            base_name, _, source = ref.partition("|")
            raw = self._monster(base_name.strip(), source.strip())
            if raw is None:
                raise CreatureError("creatures.error.base", ref=ref)
            row = self._tables.row(_cr_text(raw.get("cr")))
            fields = from_monster(raw, row.pb if row else 2)
            ref = f"{raw.get('name')}|{raw.get('source')}"
            name = name.strip() or f"{raw.get('name')} (variant)"
        if cr.strip() and self._tables.row(cr.strip()) is not None:
            fields["cr"] = cr.strip()
        design = CreatureDesign(
            name=self._clean_name(name),
            world_id=world_id or None,
            based_on=ref or None,
            **fields,
        )
        if not ref:
            design = self.math.fit(self.math.on_target(design, wording))
        return self._store.creatures.create(design)

    def save(self, design: CreatureDesign) -> CreatureDesign:
        """Store a changed design (not yet published)."""
        if design.revision_of:  # a revision keeps its creature's name and world
            original = self.get(design.revision_of)
            design.name, design.world_id = original.name, original.world_id
        design.name = self._clean_name(design.name, design.id, design.revision_of)
        design.touch()
        return self._store.creatures.replace(design)

    def delete(self, design_id: str) -> CreatureDesign:
        """Delete a design; its statblock leaves the homebrew file on the next publish."""
        design = self.get(design_id)
        self._store.creatures.delete(design_id)
        if (revision := self.revision(design_id)) is not None:  # nothing left to revise
            self._store.creatures.delete(revision.id)
        return design

    def _hash(self, design: CreatureDesign) -> str:
        made = json.dumps(self.math.statblock(design), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(made.encode("utf-8")).hexdigest()[:16]

    def unpublished(self, design: CreatureDesign) -> bool:
        """True when its statblock differs from the one last published."""
        return design.published_hash != self._hash(design)

    def named(self, name: str, world_id: str | None = None) -> CreatureDesign | None:
        """The design called ``name`` (names are unique), in ``world_id`` when given."""
        wanted = " ".join(name.split()).casefold()
        return next(
            (
                d
                for d in self._store.creatures.list()
                if d.name.casefold() == wanted
                and not d.revision_of
                and (world_id is None or d.world_id == world_id)
            ),
            None,
        )

    def statblock_named(self, name: str) -> dict[str, Any] | None:
        """The statblock a design will publish, for an encounter of a draft adventure
        that plans Claude's creature before it is published (documentation/MCP.md §10)."""
        design = self.named(name)
        return self.math.statblock(design) if design is not None else None

    def accept_named(
        self, world_id: str | None, names: Sequence[str]
    ) -> builtins.list[CreatureDesign]:
        """The world's designs among ``names`` (an accepted adventure's creatures):
        Claude's drafts accepted, and published when any is not yet; the drafts accepted."""
        designs = [d for n in dict.fromkeys(names) if (d := self.named(n, world_id))]
        drafts = [d for d in designs if d.drafted_by]
        for design in drafts:
            self.accept(design.id)
        if drafts or any(self.unpublished(d) for d in designs):
            self.publish()
        return drafts

    def accept(self, design_id: str) -> CreatureDesign:
        """Make Claude's draft one of the gamemaster's designs, published from now on;
        a revision is written onto its creature (:meth:`accept_revision`)."""
        if self.get(design_id).revision_of:
            return self.accept_revision(design_id)
        return self._store.creatures.update(design_id, drafted_by=None)

    def revision(self, design_id: str) -> CreatureDesign | None:
        """The open revision of a design, if Claude has one."""
        return next((d for d in self._store.creatures.list() if d.revision_of == design_id), None)

    def start_revision(self, design_id: str, drafted_by: str) -> CreatureDesign:
        """A draft copy of an accepted design for Claude to change, or the one already
        open; the design itself stays as it is, published, until the copy is accepted."""
        original = self.get(design_id)
        if original.drafted_by:
            raise CreatureError("creatures.error.revise_draft", name=original.name)
        found = self.revision(design_id)
        if found is not None:
            return found
        copy = CreatureDesign.model_validate(
            {
                **original.model_dump(exclude=set(_OWN_FIELDS)),
                "drafted_by": drafted_by,
                "revision_of": original.id,
            }
        )
        return self._store.creatures.create(copy)

    def changes(self, revision: CreatureDesign) -> builtins.list[str]:
        """The fields a revision changes against its creature, in the design's order."""
        if not revision.revision_of:
            return []
        original = self._store.creatures.find(revision.revision_of)
        if original is None:
            return []
        mine, theirs = revision.model_dump(), original.model_dump()
        kept = _OWN_FIELDS | {"name", "world_id"}  # the creature's, followed by its revision
        return [k for k in mine if k not in kept and mine[k] != theirs.get(k)]

    def accept_revision(self, design_id: str) -> CreatureDesign:
        """Write a revision onto its creature (same id, name and picture, so encounters
        keep it), drop the copy, and publish when the creature was published."""
        revision = self.get(design_id)
        original = self.get(revision.revision_of or "")
        changed = original.model_copy(
            update={k: getattr(revision, k) for k in self.changes(revision)}
        )
        changed.touch()
        self._store.creatures.replace(changed)
        self._store.creatures.delete(revision.id)
        if original.published_at is not None:
            self.publish()
        return self.get(original.id)

    def publish(self) -> Published:
        """Write every design but Claude's drafts to the homebrew file and reload the
        compendium.

        A world that declares its books and has a design gets ``Homebrew`` declared,
        so its creatures resolve there (TECHSTACK.md §6c).
        """
        designs = [d for d in self.list() if d.drafted_by is None]
        now = datetime.now(UTC)
        data = {
            "_meta": {
                "sources": [
                    {
                        "json": HOMEBREW_SOURCE,
                        "abbreviation": HOMEBREW_SOURCE,
                        "full": "Homebrew (Paravent creature builder)",
                    }
                ],
                "note": "Written by Paravent from library/creatures.json; edits here are "
                "replaced on the next publish.",
            },
            "monster": [self.math.statblock(d) for d in designs],
        }
        self.homebrew_file.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.homebrew_file, data)
        for design in designs:
            self._store.creatures.update(
                design.id, published_at=now, published_hash=self._hash(design)
            )
        worlds: list[str] = []
        for world_id in {d.world_id for d in designs if d.world_id}:
            world = self._store.worlds.find(world_id)
            if world is None or world.source_set.allows(HOMEBREW_SOURCE):
                continue
            sources = [*world.source_set.sources, HOMEBREW_SOURCE]
            self._store.worlds.update(world.id, source_set=SourceSet(sources=sources))
            worlds.append(world.name)
        self._reload()
        return Published(len(designs), sorted(worlds))

    def add_feature(
        self, design: CreatureDesign, name: str, value: int | None, wording: Wording | None = None
    ) -> None:
        """Add a monster feature: it counts towards the CR and its text becomes a trait,
        in ``wording``'s language and units where given (the corpus writes it in English
        and feet)."""
        feature = self._tables.feature(name)
        if feature is None:
            return
        design.features.append(
            CreatureFeature(name=feature.name, value=value if feature.takes_number else None)
        )
        block = feature_block(feature)
        if wording is not None:
            block = wording.ready_made(design, "feature", feature.name, block.name, [block.text])
        design.traits.append(block)

    def add_template(
        self, design: CreatureDesign, kind: str, name: str, wording: Wording | None = None
    ) -> None:
        """Add a ready-made trait or action from the corpus, in ``wording``'s language and
        units where given."""
        template = self._tables.template(kind, name)
        if template is None:
            return
        entries = list(template.entries)
        block = (
            wording.ready_made(design, kind, template.name, template.title, entries)
            if wording is not None
            else CreatureBlock(name=template.title, text=entries_text(entries))
        )
        (design.traits if kind == "trait" else design.actions).append(block)


def attack_text(
    *,
    ranged: bool,
    ability: str,
    dice: str,
    damage_type: str,
    reach: int = 5,
    short_range: int = 80,
    long_range: int = 320,
    wording: Wording | None = None,
) -> str:
    """A weapon attack in placeholders, so its numbers follow the creature; in
    ``wording``'s language and units, English and feet without one."""
    found = _DICE.fullmatch(dice.strip().lower())
    count, die = (int(found[1]), int(found[2])) if found else (1, 6)
    ability = ability if ability in ABILITIES else "str"
    average = count * (die + 1) / 2
    if wording is not None:
        hit = f"{{@hit <$to_hit__{ability}$>}}"
        if ranged:
            where = wording.u.text(f"{short_range}/{long_range} ft.")
            head = wording("ranged", atk="{@atk rw}", hit=hit, range=where)
        else:
            head = wording("melee", atk="{@atk mw}", hit=hit, reach=wording.u.distance(reach))
        kind = damage_type if damage_type in DAMAGE_TYPES else "bludgeoning"
        damage = wording(
            "damage",
            h="{@h}",
            average=f"<$damage_avg__{average:g}+{ability}$>",
            dice=f"{{@damage {count}d{die}<$damage_mod__{ability}$>}}",
            type=wording(f"damage.{kind}"),
        )
        return f"{head} {damage}"
    if ranged:
        where = f"range {short_range}/{long_range} ft."
        head = f"{{@atk rw}} {{@hit <$to_hit__{ability}$>}} to hit, {where}, one target."
    else:
        head = f"{{@atk mw}} {{@hit <$to_hit__{ability}$>}} to hit, reach {reach} ft., one target."
    return (
        f"{head} {{@h}}<$damage_avg__{average:g}+{ability}$> "
        f"({{@damage {count}d{die}<$damage_mod__{ability}$>}}) {damage_type} damage."
    )


__all__ = [
    "ABILITIES",
    "ALIGNMENTS",
    "BLOCK_KINDS",
    "CONDITIONS",
    "DAMAGE_TYPES",
    "HOMEBREW_SOURCE",
    "SIZES",
    "SPEEDS",
    "TYPES",
    "CrEstimate",
    "CreatureError",
    "CreatureMath",
    "CreatureNotFound",
    "CreatureService",
    "Numbers",
    "Published",
    "Wording",
    "attack_text",
    "entries_text",
    "evaluate",
    "from_monster",
    "link_numbers",
    "read_offence",
    "text_entries",
]
