"""What a bastion event brings (the 2024 guide's Bastion Events), carried out on the turn.

Most events are carried out at once: Friendly Visitors and Refugees pay 1d6 × 100 GP,
a Guest is rolled on its table (a gift, a mercenary who joins the defenders, a friendly
monster who defends the next attack, or a letter of recommendation), Lost Hirelings
shuts a special facility for the next turn, Treasure is rolled on its table (an art
object of the world's books worth its value, or a magic item of its rarity), and All
Is Well rolls its table of details. Four leave a decision to the player: a Criminal
Hireling's bribe (unpaid, a facility shuts), an Extraordinary Opportunity's 500 GP
(paid, the event is rolled again), a Request for Aid (the defenders sent each roll a
d6: 10 or more earns the reward, less earns half and costs a defender), and a Magical
Discovery's uncommon potion or scroll. The sub-tables are read from the books.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.book_tables import Rows, row_for
from app.services.dice import Dice
from app.services.text import strip_tags

GOLD = 100  # copper in a gold piece
_VALUE = re.compile(r"([\d,]+)\s*GP", re.IGNORECASE)
_RARITY = re.compile(r"\b(very rare|uncommon|common|rare|legendary)\b", re.IGNORECASE)
OPPORTUNITY = 500 * GOLD
AID_TARGET = 10


@dataclass
class EventTools:
    """What carrying events out needs from the rest of Paravent."""

    earn: Callable[[str, int], None]  # (character, copper)
    pay: Callable[[str, int, str], bool]  # (character, copper, why) → paid
    give: Callable[[str, str, int], None]  # (character, item ref, count)
    table: Callable[[str], Rows]  # a sub-table of the event's, by caption
    art: Callable[[int], str]  # an art object worth that many gp, its ref ("" none)
    magic: Callable[[str, str], str]  # (character, rarity) → a magic item's ref ("" none)


@dataclass
class Outcome:
    """What an event did: the lines to show, the bastion's changes, and an offer."""

    lines: list[str] = field(default_factory=list)
    defenders: int | None = None  # the new count, where it changed
    shut: str = ""  # a special facility shut for the next turn, by ref
    offer: str = ""
    guardian: bool | None = None


def gp_roll(dice: Dice) -> int:
    """1d6 × 100 GP, in copper."""
    return dice.die(6) * 100 * GOLD


def carry_out(
    name: str,
    character_id: str,
    *,
    defenders: int,
    facilities: Sequence[str],
    guardian: bool,
    tools: EventTools,
    dice: Dice,
) -> Outcome:
    """An event (by its name in the table) carried out on a character's bastion."""
    kind = name.strip().lower()
    out = Outcome()
    if kind == "all is well":
        detail = _rolled(tools.table("All Is Well"), dice)
        if detail:
            out.lines.append(f"detail|{detail}")
    elif kind == "friendly visitors":
        copper = gp_roll(dice)
        tools.earn(character_id, copper)
        out.lines.append(f"gold|{copper}")
    elif kind == "refugees":
        copper = gp_roll(dice)
        tools.earn(character_id, copper)
        out.lines.append(f"refugees|{dice.die(4) + dice.die(4)}|{copper}")
    elif kind == "guest":
        _guest(out, character_id, defenders, tools, dice)
    elif kind == "lost hirelings":
        if facilities:
            out.shut = facilities[dice.die(len(facilities)) - 1]
            out.lines.append(f"shut|{out.shut}")
    elif kind == "treasure":
        _treasure(out, character_id, tools, dice)
    elif kind == "criminal hireling":
        out.offer = f"bribe|{gp_roll(dice)}"
    elif kind == "extraordinary opportunity":
        out.offer = f"opportunity|{OPPORTUNITY}"
    elif kind == "request for aid":
        out.offer = "aid" if defenders else ""
    elif kind == "magical discovery":
        out.offer = "discovery"
    return out


def aid(sent: int, dice: Dice) -> tuple[int, int, int]:
    """Defenders sent to a Request for Aid: ``(total of their d6s, reward in copper,
    defenders lost)``."""
    total = sum(dice.die(6) for _ in range(sent))
    reward = gp_roll(dice)
    if total >= AID_TARGET:
        return total, reward, 0
    return total, reward // 2, 1


def _rolled(rows: Rows, dice: Dice) -> str:
    """A row of a small table, by a roll of its own size; its text."""
    if not rows:
        return ""
    row = row_for(rows, dice.die(len(rows)))
    return " ".join(strip_tags(row[1]).split()) if row and len(row) > 1 else ""


def _guest(out: Outcome, character_id: str, defenders: int, tools: EventTools, dice: Dice) -> None:
    rows = tools.table("Guest")
    if not rows:
        return
    row = row_for(rows, dice.die(len(rows)))
    text = " ".join(strip_tags(row[1]).split()) if row and len(row) > 1 else ""
    lower = text.lower()
    out.lines.append(f"guest|{text}")
    if "gift" in lower and "gp" in lower:
        copper = gp_roll(dice)
        tools.earn(character_id, copper)
        out.lines.append(f"gold|{copper}")
    elif "bastion defender" in lower and "additional" in lower:
        out.defenders = min(999, defenders + 1)
        out.lines.append("defender|1")
    elif "defends your bastion" in lower:
        out.guardian = True


def _treasure(out: Outcome, character_id: str, tools: EventTools, dice: Dice) -> None:
    rows = tools.table("Treasure")
    row = row_for(rows, dice.die(100)) if rows else None
    if not row or len(row) < 2:
        return
    text = strip_tags(row[1])
    value = _VALUE.search(text)
    rarity = _RARITY.search(text)
    ref = ""
    if "art object" in text.lower() and value is not None:
        ref = tools.art(int(value[1].replace(",", "")))
    elif rarity is not None:
        ref = tools.magic(character_id, rarity[1].lower())
    if ref:
        tools.give(character_id, ref, 1)
        out.lines.append(f"item|{ref}")


def item_ref(tag: Any) -> str:
    """``"{@item Silver ewer|XDMG}"`` → ``"Silver ewer|XDMG"``."""
    found = re.search(r"\{@item ([^}|]+)\|?([^}|]*)", str(tag))
    if found is None:
        return ""
    return f"{found[1].strip()}|{found[2].strip() or 'XDMG'}"


__all__ = ["EventTools", "Outcome", "aid", "carry_out", "gp_roll", "item_ref"]
