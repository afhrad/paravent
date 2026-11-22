"""Objects in a fight: a door to break down, a chest, a rope, a window.

An object joins the tracker like any combatant, with an AC and hit points, and is attacked
and damaged as one; it takes no turn, counts toward no XP and is immune to poison and
psychic damage, as both guides say. Its numbers come from the world's guides, read by
caption (app/services/book_tables.py):

* the 2014 guide's *Object Armor Class* (by substance) and *Object Hit Points* (by size,
  fragile or resilient, the average of the dice);
* the 2024 guide's *Doors*: a door's AC, hit points and the DC to force it open;
* siege equipment, the corpus's objects of type ``SW`` in the world's books (a ballista, a
  ram): its AC, hit points and attacks, read as a statblock so the tracker rolls them. It
  fights for a side, fired by its crew on their turns, and takes none of its own.

A guide the world lacks offers nothing; an object can always be added by hand.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.book_tables import Rows
from app.services.text import strip_tags

GUIDE_2014, GUIDE_2024 = "DMG", "XDMG"
OBJECT_AC = "Object Armor Class"
OBJECT_HP = "Object Hit Points"
DOORS = "Doors"
# The corpus's object type for siege equipment.
SIEGE = "SW"
# Both guides: objects take no poison or psychic damage.
IMMUNE = ("poison", "psychic")

# Finds a table by caption in a book the world declares: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]

_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class Substance:
    name: str
    ac: int


@dataclass(frozen=True)
class Size:
    name: str
    fragile: int
    resilient: int


@dataclass(frozen=True)
class Door:
    name: str
    ac: int
    hp: int
    dc: int | None


@dataclass(frozen=True)
class Siege:
    """A siege weapon of the world's books, by its reference (``Ballista|DMG``)."""

    reference: str
    name: str
    ac: int
    hp: int


@dataclass(frozen=True)
class Objects:
    """What the world's guides offer: substances and sizes (2014), doors (2024), siege
    equipment."""

    substances: tuple[Substance, ...] = ()
    sizes: tuple[Size, ...] = ()
    doors: tuple[Door, ...] = ()
    siege: tuple[Siege, ...] = ()

    @property
    def any(self) -> bool:
        return bool(self.doors or self.siege or (self.substances and self.sizes))

    def made_of(self, substance: int, size: int, resilient: bool) -> tuple[int, int]:
        """An object's AC and hit points, by the rows chosen."""
        stuff, big = self.substances[substance], self.sizes[size]
        return stuff.ac, big.resilient if resilient else big.fragile


def _int(cell: object) -> int | None:
    found = _NUMBER.search(strip_tags(str(cell)))
    return int(found[0]) if found else None


def offered(find: Finder, objects: Sequence[Mapping[str, Any]] = ()) -> Objects:
    """The objects the world's guides have numbers for; ``objects`` are the corpus's
    object entries in the world's books, of which the siege equipment is offered."""
    substances = tuple(
        Substance(strip_tags(str(row[0])), ac)
        for row in find(OBJECT_AC, [GUIDE_2014]) or []
        if len(row) >= 2 and (ac := _int(row[1])) is not None
    )
    sizes = tuple(
        Size(strip_tags(str(row[0])), fragile, resilient)
        for row in find(OBJECT_HP, [GUIDE_2014]) or []
        if len(row) >= 3
        and (fragile := _int(row[1])) is not None
        and (resilient := _int(row[2])) is not None
    )
    doors = tuple(
        Door(strip_tags(str(row[0])), ac, hp, _int(row[3]) if len(row) > 3 else None)
        for row in find(DOORS, [GUIDE_2024]) or []
        if len(row) >= 3 and (ac := _int(row[1])) is not None and (hp := _int(row[2])) is not None
    )
    siege = tuple(
        sorted(
            (
                Siege(f"{data['name']}|{data['source']}", str(data["name"]), ac, hp)
                for data in objects
                if data.get("objectType") == SIEGE
                and isinstance(ac := data.get("ac"), int)
                and isinstance(hp := data.get("hp"), int)
                and data.get("actionEntries")
            ),
            key=lambda x: x.name.lower(),
        )
    )
    return Objects(substances, sizes, doors, siege)


def statblock(data: Mapping[str, Any]) -> dict[str, Any]:
    """A siege weapon as a creature's statblock, for the tracker's panel and its attack
    buttons: its text as a trait, each action's attack as one line of text."""
    actions = []
    for action in data.get("actionEntries") or []:
        if isinstance(action, dict):
            actions.append({"name": action.get("name", ""), "entries": _lines(action)})
    block: dict[str, Any] = {
        "name": data.get("name", ""),
        "source": data.get("source", ""),
        "page": data.get("page"),
        "size": data.get("size") or ["L"],
        "type": "object",
        "ac": [data["ac"]] if isinstance(data.get("ac"), int) else data.get("ac"),
        "hp": {"average": data["hp"]} if isinstance(data.get("hp"), int) else data.get("hp"),
        "speed": {"walk": 0},
        "immune": data.get("immune") or list(IMMUNE),
        "trait": [{"name": "", "entries": list(data.get("entries") or [])}],
        "action": actions,
    }
    for key in ("str", "dex", "con", "int", "wis", "cha"):
        if key in data:
            block[key] = data[key]
    return {k: v for k, v in block.items() if v is not None}


def _lines(action: Mapping[str, Any]) -> list[Any]:
    """An action's entries, a 2014 ``attack`` block written as the text it stands for."""
    lines: list[Any] = []
    for entry in action.get("entries") or []:
        if isinstance(entry, dict) and entry.get("type") == "attack":
            kind = str(entry.get("attackType", "")).lower()
            hit = " ".join(str(x) for x in entry.get("attackEntries") or [])
            damage = " ".join(str(x) for x in entry.get("hitEntries") or [])
            lines.append(f"{{@atk {kind}}} {hit} {{@h}}{damage}".strip())
        else:
            lines.append(entry)
    # The objects write "{@hit +6}", the creatures "{@hit 6}", which the attack reader takes.
    return [x.replace("{@hit +", "{@hit ") if isinstance(x, str) else x for x in lines]


def label_for(substance: str, size: str) -> str:
    """An object's name from its rows: "Wood (Medium)" from "Wood, bone" and "Medium
    (barrel, chandelier)"."""
    return f"{substance.split(',')[0].strip()} ({size.split('(')[0].strip()})"


__all__ = [
    "DOORS",
    "GUIDE_2014",
    "GUIDE_2024",
    "IMMUNE",
    "OBJECT_AC",
    "OBJECT_HP",
    "Door",
    "Finder",
    "Objects",
    "SIEGE",
    "Siege",
    "Size",
    "Substance",
    "label_for",
    "offered",
    "statblock",
]
