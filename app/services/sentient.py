"""Sentient magic items and artifacts: an item's mind, an artifact's further properties.

Both guides make a sentient item the same way. Its Intelligence, Wisdom and Charisma are
4d6 each, the lowest die dropped, and its alignment, how it communicates, what it senses
and the purpose it pursues are rolled on four tables: the 2014 guide's *Sentient Magic
Item …* and *Sentient Special Purpose*, or the 2024 guide's *Sentient Item's …*. An
artifact may have up to four minor and two major beneficial properties, and as many
detrimental ones, rolled on the *Minor/Major Beneficial/Detrimental Properties* tables of
either guide. The tables stay in the user's corpus and are found by caption
(app/services/book_tables.py), in the guide of the world's rules first. Rolled texts are
kept on the carried item (``InventoryItem.mind``, ``.artifact``), the gamemaster's to
change.

**Conflict.** When the bearer acts against the item's alignment or purpose, the 2014
guide has the item's Charisma check contest the bearer's; the 2024 guide has the bearer
make a Charisma saving throw, DC 12 + the item's Charisma modifier. If the item wins it
makes its demands. Refused, it may try to take control: the bearer saves the same way
or is charmed by it for 1d12 hours, carried as an affliction (``charm``) the clock wears
off. Repeating the save when the bearer takes damage, and the once-a-dawn limit, are the
table's.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, get_args

from app.models.pc import ArtifactKind, ItemMind
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.afflictions import AfflictionService, Outcome, Victim
from app.services.book_tables import Rows, row_for, span
from app.services.dice import Dice
from app.services.madness import MadEffect
from app.services.text import strip_tags

PARTS = ("alignment", "communication", "senses", "purpose")
SCORES = ("intelligence", "wisdom", "charisma")
KINDS: tuple[str, ...] = get_args(ArtifactKind)
# How many of each kind an artifact may have (both guides).
LIMITS = {
    "minor_beneficial": 4,
    "major_beneficial": 2,
    "minor_detrimental": 4,
    "major_detrimental": 2,
}
_ARTIFACT = {
    "minor_beneficial": "Minor Beneficial Properties",
    "major_beneficial": "Major Beneficial Properties",
    "minor_detrimental": "Minor Detrimental Properties",
    "major_detrimental": "Major Detrimental Properties",
}
TABLES: dict[str, dict[str, str]] = {
    "DMG": {
        "alignment": "Sentient Magic Item Alignment",
        "communication": "Sentient Magic Item Communication",
        "senses": "Sentient Magic Item Senses",
        "purpose": "Sentient Special Purpose",
        **_ARTIFACT,
    },
    "XDMG": {
        "alignment": "Sentient Item's Alignment",
        "communication": "Sentient Item's Communication",
        "senses": "Sentient Item's Senses",
        "purpose": "Sentient Item's Special Purpose",
        **_ARTIFACT,
    },
}
# A charm lasts 1d12 hours.
CHARM = ("1d12", 60)

# Finds a table by caption in the first of the books the world has: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]


@dataclass(frozen=True)
class Guide:
    """The guide a world rolls on: its book ("" for none), its tables by part, and
    whether a conflict is a contest of Charisma checks (2014) or the bearer's save
    (2024)."""

    source: str = ""
    rows: dict[str, Rows] = field(default_factory=dict)
    contested: bool = True

    def has(self, part: str) -> bool:
        return bool(self.rows.get(part))


def guide_for(find: Finder, rules_2024: bool) -> Guide:
    """The guide of the world's rules where the world has its tables, else the other;
    with neither, no tables and the conflict of the world's rules."""
    books = ["XDMG", "DMG"] if rules_2024 else ["DMG", "XDMG"]
    for book in books:
        rows = {
            part: found
            for part, caption in TABLES[book].items()
            if (found := find(caption, [book]))
        }
        if rows:
            return Guide(book, rows, contested=book == "DMG")
    return Guide(contested=not rules_2024)


def modifier(score: int) -> int:
    """An ability score's modifier."""
    return (score - 10) // 2


def conflict_dc(mind: ItemMind) -> int:
    """The bearer's Charisma save against the item: 12 + its Charisma modifier."""
    return 12 + modifier(mind.charisma)


def score(dice: Dice) -> int:
    """4d6, the lowest dropped."""
    rolls = sorted(dice.die(6) for _ in range(4))
    return sum(rolls[1:])


def roll_part(guide: Guide, part: str, dice: Dice) -> str:
    """One of the mind's parts, or one artifact property, rolled on the guide's table;
    "" where the world has no such table."""
    rows = guide.rows.get(part)
    if not rows:
        return ""
    ends = [end for r in rows if r and (found := span(r[0])) for end in found]
    die = max((e for e in ends if 0 < e < 10_000), default=len(rows))
    total = dice.die(die)
    row = row_for(rows, total) or rows[(total - 1) % len(rows)]
    return strip_tags(row[-1]).strip() if row else ""


def roll_mind(guide: Guide, dice: Dice) -> ItemMind:
    """A whole mind: its three scores and every part the guide has a table for."""
    return ItemMind(
        **{name: score(dice) for name in SCORES},
        **{part: roll_part(guide, part, dice) for part in PARTS},
    )


def reroll(mind: ItemMind, part: str, guide: Guide, dice: Dice) -> ItemMind:
    """The mind with one part (or score) rolled again."""
    if part in SCORES:
        return mind.model_copy(update={part: score(dice)})
    if part in PARTS and guide.has(part):
        return mind.model_copy(update={part: roll_part(guide, part, dice)})
    return mind


def room(held: Sequence[Any], kind: str) -> bool:
    """Whether an artifact has room for another property of ``kind``."""
    return kind in LIMITS and sum(1 for p in held if p.kind == kind) < LIMITS[kind]


@dataclass(frozen=True)
class Conflict:
    """How a conflict between an item and its bearer went: the item's Charisma check
    (2014; None under the 2024 rule), the bearer's roll, the DC of a save (2024), and
    whether the item won and makes its demands."""

    bearer: int
    item: int | None = None
    dc: int | None = None
    item_wins: bool = False


class SentientService:
    """Conflicts between sentient items and their bearers."""

    def __init__(
        self,
        protocol: Any | None,
        afflictions: AfflictionService,
        *,
        roll: Callable[[str, str, str], int],
        dice: Dice | None = None,
    ) -> None:
        """``roll(character, kind, ability)`` rolls a check or save on the sheet."""
        self._protocol = protocol
        self._afflictions = afflictions
        self._roll = roll
        self._dice = dice or Dice()

    def conflict(
        self,
        world_id: str,
        character_id: str,
        character_name: str,
        item: str,
        mind: ItemMind,
        contested: bool,
        by: str,
        by_name: str,
    ) -> Conflict:
        """The bearer acts against the item: a contest of Charisma checks (``contested``,
        the item winning only by beating the bearer) or the bearer's Charisma save."""
        if contested:
            mine = self._dice.die(20) + modifier(mind.charisma)
            theirs = self._roll(character_id, "check", "cha")
            done = Conflict(theirs, item=mine, item_wins=mine > theirs)
        else:
            dc = conflict_dc(mind)
            theirs = self._roll(character_id, "save", "cha")
            done = Conflict(theirs, dc=dc, item_wins=theirs < dc)
        what = (
            f"sentient:conflict:{_safe(item)}:{_n(done.item)}:{done.bearer}:{_n(done.dc)}:"
            f"{int(done.item_wins)}"
        )
        self._log(world_id, by, by_name, character_id, character_name, what)
        return done

    def control(
        self,
        world_id: str,
        character_id: str,
        character_name: str,
        item: str,
        mind: ItemMind,
        by: str,
        by_name: str,
    ) -> Outcome:
        """The item tries to take control: the bearer's Charisma save, charmed for 1d12
        hours on a failure."""
        lasts, unit = CHARM
        [outcome] = self._afflictions.madden(
            world_id,
            item,
            lambda: MadEffect("short", "", ("charmed",), lasts, unit),
            [Victim(character_id, character_name)],
            save=("cha", conflict_dc(mind)),
            damage="",
            half=False,
            by=by,
            by_name=by_name,
            kind="charm",
        )
        return outcome

    def _log(
        self, world_id: str, by: str, by_name: str, character_id: str, name: str, what: str
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{character_id}",
                    subject_name=name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            ]
        )


def _safe(text: str) -> str:
    return text.replace(":", " ")


def _n(value: int | None) -> str:
    return "" if value is None else str(value)


__all__ = [
    "KINDS",
    "LIMITS",
    "PARTS",
    "SCORES",
    "TABLES",
    "Conflict",
    "Guide",
    "SentientService",
    "conflict_dc",
    "guide_for",
    "modifier",
    "reroll",
    "roll_mind",
    "roll_part",
    "room",
    "score",
]
