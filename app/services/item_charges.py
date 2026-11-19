"""Magic items' charges and attunement.

The corpus types both: an item's ``charges`` (how many), ``recharge`` (``dawn``,
``dusk``, ``midnight``, ``restLong``, or ``special`` for the table to judge) and
``rechargeAmount`` (dice such as ``{@dice 1d6 + 4}``, a number, or none for all of
them), and ``reqAttune`` for what must be attuned. A character file keeps only the
charges spent (``InventoryItem.charges_used``); the sheet derives the rest.

The world's clock brings charges back as each dawn, dusk or midnight passes; a long
rest those of a ``restLong`` item. Each recovery is logged. A character attunes to at
most three items, an artificer to four from level 10, five from 14 and six from 18.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.dice import Dice, DiceError
from app.store.gamestate import GameStateStore

DAY = 24 * 60
# The minute of the day each kind of recovery falls on.
RECHARGE_AT = {"midnight": 0, "dawn": 6 * 60, "dusk": 18 * 60}
ATTUNEMENT_SLOTS = 3
# An artificer's Magic Item Adept, Savant and Master: (level, slots).
ARTIFICER_SLOTS = ((18, 6), (14, 5), (10, 4))
_DICE_TAG = re.compile(r"\{@dice ([^}|]+)[^}]*\}")


@dataclass(frozen=True)
class Charges:
    """How many charges an item has, when they come back and how many."""

    most: int
    recharge: str = ""  # dawn, dusk, midnight, restLong, special, or "" (never)
    amount: str = ""  # dice or a number; "" for all of them

    @property
    def timed(self) -> bool:
        return self.recharge in RECHARGE_AT or self.recharge == "restLong"


def charges_of(data: Mapping[str, Any] | None) -> Charges | None:
    """An item's charges from its entry, or None. A count the book leaves to dice
    (a deck's cards) is not a number to track."""
    if data is None:
        return None
    most = data.get("charges")
    if not isinstance(most, int) or isinstance(most, bool) or most < 1:
        return None
    raw = data.get("rechargeAmount")
    if isinstance(raw, int) and not isinstance(raw, bool):
        amount = str(raw)
    elif isinstance(raw, str):
        found = _DICE_TAG.search(raw)
        amount = (found.group(1) if found else raw).replace(" ", "")
    else:
        amount = ""
    return Charges(most, str(data.get("recharge") or ""), amount)


# What an item's text says of the spells it casts: its own save DC ("save {@dc 15}"),
# a higher level for each additional charge, and how many charges a casting may take.
_ITEM_DC = re.compile(r"save \{@dc (\d+)\}|save DC (\d+)")
_UPCAST = re.compile(r"(?:slot level|spell's level) by (?:one|1) for each additional charge", re.I)
_MOST = re.compile(r"no more than (\d+) charges", re.I)


@dataclass(frozen=True)
class SpellTerms:
    """How an item casts its spells, from its text: ``dc`` when it states one save DC
    (none: the caster's own), whether more charges raise the level, and the most
    charges one casting may take."""

    dc: int | None = None
    upcast: bool = False
    most: int | None = None


def spell_terms(data: Mapping[str, Any] | None) -> SpellTerms:
    """Read an item's spell terms from its entries."""
    text = json.dumps((data or {}).get("entries") or [])
    found = {int(a or b) for a, b in _ITEM_DC.findall(text)}
    most = _MOST.search(text)
    return SpellTerms(
        dc=found.pop() if len(found) == 1 else None,
        upcast=bool(_UPCAST.search(text)),
        most=int(most.group(1)) if most else None,
    )


def attunement_allows(
    tags: Sequence[Mapping[str, Any]],
    *,
    classes: Collection[str],
    species: str,
    spellcaster: bool,
) -> bool:
    """Whether a character may attune to an item, by its ``reqAttuneTags``: any one tag
    met (all of its parts), each part a class, a species or being a spellcaster; what
    Paravent cannot judge (alignment, creature type) is the table's, and passes."""
    if not tags:
        return True
    names = {c.lower() for c in classes}
    kind = species.lower()

    def met(key: str, value: Any) -> bool:
        if key == "class":
            return str(value).partition("|")[0].lower() in names
        if key == "race":
            wanted = str(value).partition("|")[0].lower()
            return bool(kind) and wanted in kind
        if key == "spellcasting":
            return spellcaster
        return True

    return any(all(met(k, v) for k, v in tag.items()) for tag in tags)


def attunement_slots(classes: Mapping[str, int]) -> int:
    """How many items a character may attune to, by its class levels (lower case)."""
    level = classes.get("artificer", 0)
    return next((slots for at, slots in ARTIFICER_SLOTS if level >= at), ATTUNEMENT_SLOTS)


def crossings(kind: str, before: int, now: int) -> int:
    """How many times the clock passed ``kind``'s minute of the day going from
    ``before`` to ``now``."""
    at = RECHARGE_AT.get(kind)
    if at is None or now <= before:
        return 0
    return (now - at) // DAY - (before - at) // DAY


class ItemCharges:
    """Brings back the charges a world's characters spent, logged."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: Any,
        *,
        charges: Callable[[PlayerCharacter, int], Charges | None],
        dice: Dice | None = None,
    ) -> None:
        """``charges(pc, index)`` reads the charges of the item at ``index`` of a
        character's inventory from the books."""
        self._store = store
        self._protocol = protocol
        self._charges = charges
        self._dice = dice or Dice()

    def passed(self, world_id: str, before: int, now: int, by: str, by_name: str) -> None:
        """The clock moved: each dawn, dusk and midnight passed brings charges back."""
        times = {kind: crossings(kind, before, now) for kind in RECHARGE_AT}
        if not any(times.values()):
            return
        for pc in self._store.characters.list():
            if pc.world_id == world_id:
                self._recover(pc, lambda c: times.get(c.recharge, 0), by, by_name)

    def rested(self, character_id: str, by: str, by_name: str) -> None:
        """A long rest ended: a ``restLong`` item's charges come back."""
        pc = self._store.characters.find(character_id)
        if pc is not None:
            self._recover(pc, lambda c: 1 if c.recharge == "restLong" else 0, by, by_name)

    def _recover(
        self,
        pc: PlayerCharacter,
        times: Callable[[Charges], int],
        by: str,
        by_name: str,
    ) -> None:
        regained: dict[int, tuple[Charges, int]] = {}
        for index, carried in enumerate(pc.inventory):
            charges = self._charges(pc, index) if carried.charges_used else None
            if charges is None or not (n := times(charges)):
                continue
            back = sum(self._amount(charges) for _ in range(n))
            regained[index] = (charges, min(back, carried.charges_used))
        regained = {i: x for i, x in regained.items() if x[1] > 0}
        if not regained:
            return
        names: dict[int, str] = {}

        def change(fresh: PlayerCharacter) -> None:
            for index, (_charges, back) in regained.items():
                if index < len(fresh.inventory):
                    carried = fresh.inventory[index]
                    carried.charges_used = max(0, carried.charges_used - back)
                    names[index] = carried.item.reference

        self._store.characters.mutate(pc.id, change)
        for index, (charges, back) in regained.items():
            left = charges.most - max(0, pc.inventory[index].charges_used - back)
            self._log(
                pc,
                by,
                by_name,
                f"charges:{names.get(index, pc.inventory[index].item.reference)}:{back}:{left}",
            )

    def _amount(self, charges: Charges) -> int:
        if not charges.amount:
            return charges.most
        try:
            return max(0, self._dice.roll(charges.amount).total)
        except DiceError:
            return int(charges.amount) if charges.amount.isdigit() else 0

    def _log(self, pc: PlayerCharacter, by: str, by_name: str, what: str) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}",
                    subject_name=pc.name or "?",
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=pc.world_id or ""),
                )
            ]
        )


__all__ = [
    "ATTUNEMENT_SLOTS",
    "Charges",
    "ItemCharges",
    "SpellTerms",
    "attunement_allows",
    "attunement_slots",
    "charges_of",
    "crossings",
    "spell_terms",
]
