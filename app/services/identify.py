"""Unidentified and cursed magic items (documentation/CHARACTER-FORMAT.md §3, ``inventory``).

An item handed out **unidentified** shows its player only what it looks like: the
gamemaster's words for it (``shown_as``), else the kind of thing it is ("an unidentified
longsword", "an unidentified ring"). Until the gamemaster identifies it, it works as its
base item — a +1 longsword as a longsword, a ring as nothing at all — so neither its name
nor its numbers give it away on the sheet, and it cannot be attuned to.

A **cursed** item (its book's ``curse``, or the gamemaster's word on the carried item)
binds whoever attunes to it: they cannot end the attunement, take it off, sell it or use
it up until the gamemaster lifts the curse, which ends the attunement (as Remove Curse
does). Its curse shows once it binds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models.pc import ChosenOption, InventoryItem, PlayerCharacter

# The kind an item without a base item is shown as, by its type code.
KINDS = {"P": "potion", "RG": "ring", "WD": "wand", "RD": "rod", "SC": "scroll", "ST": "staff"}

ItemLookup = Callable[[ChosenOption], dict[str, Any] | None]


def type_code(data: dict[str, Any]) -> str:
    """An item's type without its book: ``"RG|XDMG"`` is ``"RG"``."""
    return str(data.get("type") or "").split("|")[0].upper()


def base_of(data: dict[str, Any] | None) -> ChosenOption | None:
    """The base item a magic item is made from (``baseItem``), if any."""
    base = str((data or {}).get("baseItem") or "")
    return ChosenOption.model_validate(base) if base else None


def kind_of(data: dict[str, Any] | None) -> str:
    """What an unidentified item is shown as when it has no base item: ``potion``,
    ``ring``, ``wand``, ``rod``, ``scroll``, ``staff``, ``wondrous`` or ``item``."""
    if data is None:
        return "item"
    if kind := KINDS.get(type_code(data)):
        return kind
    return "wondrous" if data.get("wondrous") else "item"


def is_cursed(entry: InventoryItem, data: dict[str, Any] | None) -> bool:
    """Whether a carried item is cursed: the gamemaster's word, else its book's."""
    if entry.cursed is not None:
        return entry.cursed
    return bool((data or {}).get("curse"))


def is_bound(entry: InventoryItem, data: dict[str, Any] | None) -> bool:
    """Whether a carried item holds its bearer: cursed and attuned."""
    return entry.attuned and is_cursed(entry, data)


def as_seen(character: PlayerCharacter, item: ItemLookup) -> PlayerCharacter:
    """The character as its sheet is worked out: each unidentified item replaced by its
    base item (or by an unknown thing of its name), never attuned, no charges spent. The
    inventory keeps its order, so the sheet's indexes still name the carried items."""
    if not any(entry.unidentified for entry in character.inventory):
        return character
    seen = []
    for entry in character.inventory:
        if not entry.unidentified:
            seen.append(entry)
            continue
        data = item(entry.item)
        base = base_of(data)
        if base is not None and item(base) is None:  # its base's printing is not declared
            base = ChosenOption(name=base.name, source=None)
        # Without a base item, a name that resolves to nothing and says nothing.
        stand_in = base or ChosenOption(name=f"?{kind_of(data)}", source=None)
        seen.append(
            entry.model_copy(update={"item": stand_in, "attuned": False, "charges_used": 0})
        )
    return character.model_copy(update={"inventory": seen})


__all__ = ["as_seen", "base_of", "is_bound", "is_cursed", "kind_of", "type_code"]
