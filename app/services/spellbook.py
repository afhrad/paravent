"""Spellbooks and spell scrolls.

A spell scroll in the pack holds a spell — written in when it was scribed in downtime,
or by the player for one found in a treasure. A character casts it if the spell is on
one of its classes' lists: of a level it can cast, at once; of a higher one, after an
ability check with its spellcasting ability against DC 10 + the spell's level. The
scroll is used up either way.

A character who prepares from a spellbook (a wizard) copies a spell of its class's list
into it, of a level it can cast, at 50 GP and 2 hours a level: from another book
outright, or from a scroll after an Intelligence (Arcana) check against DC 10 + the
spell's level, the scroll used up either way. The hours are the table's to pass.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.pc import ChosenOption, PlayerCharacter
from app.services.chargen.catalogue import Catalogue, Offer
from app.services.i18n import Translatable
from app.services.sheet import Sheet

GOLD_PER_LEVEL = 50
HOURS_PER_LEVEL = 2
DC_BASE = 10


class SpellbookError(Translatable):
    """A scroll or a copy the rules refuse."""


@dataclass(frozen=True)
class Outcome:
    """What a scroll or a copy came to: whether it worked, and the check if one was
    rolled."""

    spell: str
    ok: bool
    total: int | None = None
    dc: int | None = None


def max_level(sheet: Sheet) -> int:
    """The highest spell level the character has slots for."""
    levels = [s.level for s in sheet.slots]
    if sheet.pact is not None:
        levels.append(sheet.pact.level)
    return max(levels, default=0)


def _classes(pc: PlayerCharacter, catalogue: Catalogue) -> list[tuple[Offer, Offer | None]]:
    out = []
    for entry in pc.classes:
        cls = catalogue.find("class", ChosenOption(name=entry.name, source=entry.source))
        if cls is None:
            continue
        subclass = (
            next((o for o in catalogue.subclasses(cls) if o.matches(entry.subclass)), None)
            if entry.subclass is not None
            else None
        )
        out.append((cls, subclass))
    return out


def on_list(pc: PlayerCharacter, catalogue: Catalogue, spell: Offer) -> bool:
    """Whether a spell is on one of the character's classes' lists, at any level."""
    return any(
        spell.name.lower() in {o.name.lower() for o in catalogue.class_spells(cls, 9, sub)}
        for cls, sub in _classes(pc, catalogue)
    )


def spellbook_class(sheet: Sheet) -> str | None:
    """The class that prepares from a spellbook, if the character has one."""
    return next((p.class_name for p in sheet.preparing if p.from_book), None)


def copyable(pc: PlayerCharacter, catalogue: Catalogue, sheet: Sheet) -> list[Offer]:
    """The spells of the spellbook class's list the character could copy: a level it
    can cast, not in the book yet."""
    name = spellbook_class(sheet)
    if name is None:
        return []
    known = {o.name.lower() for o in pc.spells.known}
    top = max_level(sheet)
    found = [
        o
        for cls, sub in _classes(pc, catalogue)
        if cls.name == name
        for o in catalogue.class_spells(cls, top, sub)
        if int(o.data.get("level", 0)) >= 1 and o.name.lower() not in known
    ]
    unique = {o.name.lower(): o for o in found}
    return sorted(unique.values(), key=lambda o: (int(o.data.get("level", 0)), o.name.lower()))


class SpellbookService:
    """Casts from scrolls and copies into spellbooks, through the play rules."""

    def __init__(
        self,
        *,
        catalogue: Callable[[PlayerCharacter], Catalogue],
        sheet: Callable[[PlayerCharacter], Sheet],
        roll: Callable[[str, str, str], int],
        act: Callable[[str, str, dict[str, str]], None],
    ) -> None:
        """``catalogue(pc)`` is what its world's books offer; ``sheet(pc)`` its sheet;
        ``roll(character, kind, subject)`` rolls a check on the sheet and gives the
        total; ``act(character, action, params)`` carries a play action out."""
        self._catalogue = catalogue
        self._sheet = sheet
        self._roll = roll
        self._act = act

    def _spell(self, pc: PlayerCharacter, ref: str) -> Offer:
        found = self._catalogue(pc).find("spell", ChosenOption.model_validate(ref)) if ref else None
        if found is None:
            raise SpellbookError("spellbook.error.spell")
        return found

    def _scroll(self, pc: PlayerCharacter, index: int) -> str:
        if not 0 <= index < len(pc.inventory):
            raise SpellbookError("spellbook.error.scroll")
        item = pc.inventory[index]
        if not item.item.name.lower().startswith("spell scroll") or not item.spell:
            raise SpellbookError("spellbook.error.scroll")
        return item.spell

    def inscribe(self, pc: PlayerCharacter, index: int, spell: str) -> None:
        """Write the spell a found scroll holds, as the world's books name it."""
        if not 0 <= index < len(pc.inventory) or not pc.inventory[
            index
        ].item.name.lower().startswith("spell scroll"):
            raise SpellbookError("spellbook.error.scroll")
        offer = self._spell(pc, spell)
        self._act(pc.id, "inscribe", {"index": str(index), "spell": offer.reference})

    def cast(self, pc: PlayerCharacter, index: int) -> Outcome:
        """Cast the spell a scroll holds, if it is on one of the character's lists."""
        offer = self._spell(pc, self._scroll(pc, index))
        catalogue = self._catalogue(pc)
        if not on_list(pc, catalogue, offer):
            raise SpellbookError("spellbook.error.not_on_list")
        sheet = self._sheet(pc)
        level = int(offer.data.get("level", 0))
        total = dc = None
        ok = True
        if level > max_level(sheet):
            ability = sheet.spellcasting_ability or "int"
            dc = DC_BASE + level
            total = self._roll(pc.id, "check", ability)
            ok = total >= dc
        self._used(pc.id, index, "cast", ok, total, dc)
        return Outcome(offer.name, ok, total, dc)

    def copy(self, pc: PlayerCharacter, spell: str, index: int | None = None) -> Outcome:
        """Copy a spell into the spellbook: from another book (``index`` None), or from
        the scroll at ``index`` after an Intelligence (Arcana) check."""
        sheet = self._sheet(pc)
        catalogue = self._catalogue(pc)
        if index is not None:
            spell = self._scroll(pc, index)
        offer = self._spell(pc, spell)
        allowed = {o.name.lower() for o in copyable(pc, catalogue, sheet)}
        if offer.name.lower() not in allowed:
            raise SpellbookError("spellbook.error.cannot_copy")
        level = int(offer.data.get("level", 0))
        cost = GOLD_PER_LEVEL * level * 100
        self._act(pc.id, "spend", {"amount": str(cost), "for": f"spellbook {offer.name}"})
        total = dc = None
        ok = True
        if index is not None:
            dc = DC_BASE + level
            total = self._roll(pc.id, "skill", "arcana")
            ok = total >= dc
            self._used(pc.id, index, "copy", ok, total, dc)
        if ok:
            self._act(pc.id, "learn", {"kind": "spell", "ref": offer.reference})
        return Outcome(offer.name, ok, total, dc)

    def _used(
        self, character_id: str, index: int, what: str, ok: bool, total: int | None, dc: int | None
    ) -> None:
        params = {"index": str(index), "what": what, "ok": str(int(ok))}
        if total is not None:
            params |= {"total": str(total), "dc": str(dc)}
        self._act(character_id, "scroll", params)


__all__ = [
    "DC_BASE",
    "GOLD_PER_LEVEL",
    "HOURS_PER_LEVEL",
    "Outcome",
    "SpellbookError",
    "SpellbookService",
    "copyable",
    "max_level",
    "on_list",
    "spellbook_class",
]
