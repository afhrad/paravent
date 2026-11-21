"""Supernatural gifts: blessings, charms and boons the gamemaster gives a character.

The books print them as rewards (``reward``: *Blessing*, *Charm*, *Boon*, a setting's
own kinds). Given from a gift's page to the characters ticked, each is kept on the
character (``PlayerCharacter.gifts``), shown on the sheet and the party card, and logged.
What its text says in numbers is read once, when it is given:

* **uses**: "This charm has 3 charges", "used three times", "Once used, …", "vanishes
  after you activate it", "Once you do so, the charm vanishes" — else the uses the book
  lists for the spells it casts (``additionalSpells``' limited uses). A blessing or a boon
  does not wear out. Each use is spent on the sheet; the last makes it vanish.
* **how long**: "lasts for 7 days", "These benefits last for 10 days", "lasts for 24
  hours" — it fades by the world's clock.

What the gift does is the table's to apply, as its text says.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.pc import ChosenOption, Gift, PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.text import strip_tags
from app.store.gamestate import GameStateStore

_NUMBER = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|twenty)"
_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "twenty": 20,
}
_CHARGES = re.compile(rf"\bhas {_NUMBER} charges?\b", re.IGNORECASE)
_TIMES = re.compile(rf"\bused {_NUMBER} times\b", re.IGNORECASE)
_ONCE = re.compile(
    r"\bonce used\b|vanishes after you (?:activate|use) it|once you do so, (?:the|this) charm"
    r"|once you use this charm|after it is used once|you can expend the charm"
    r"|until you use it|charm then vanishes|cast \{@spell [^}]+\} once\b",
    re.IGNORECASE,
)
_LASTS = re.compile(
    rf"\blasts? for {_NUMBER} (minute|hour|day|year)s?\b",
    re.IGNORECASE,
)
_MINUTES = {"minute": 1, "hour": 60, "day": 1440, "year": 525_600}


@dataclass(frozen=True)
class GiftReading:
    """What a gift's text says in numbers: its uses (None: it does not wear out) and
    how many minutes it lasts (None: until used, or for good)."""

    uses: int | None = None
    lasts: int | None = None


def _amount(word: str) -> int:
    return int(word) if word.isdigit() else _WORDS.get(word.lower(), 0)


def _text(entries: Any) -> str:
    if isinstance(entries, str):
        return entries
    if isinstance(entries, list):
        return " ".join(_text(e) for e in entries)
    if isinstance(entries, Mapping):
        return " ".join(_text(entries.get(k)) for k in ("entries", "items", "entry"))
    return ""


def _spell_uses(data: Mapping[str, Any]) -> int:
    """The uses its spells' limited castings add up to (``"3": [...]``, ``"1e": [...]``,
    an ``e`` meaning each spell so many times)."""
    total = 0
    for block in data.get("additionalSpells") or []:
        if not isinstance(block, Mapping):
            continue
        for by_level in (block.get("innate") or {}).values():
            limited = by_level.get("limited") if isinstance(by_level, Mapping) else None
            if not isinstance(limited, Mapping):
                continue
            for key, spells in limited.items():
                count = int(re.sub(r"\D", "", str(key)) or 0)
                each = str(key).endswith("e")
                total += count * (len(spells or []) if each else 1)
    return total


def read_gift(data: Mapping[str, Any]) -> GiftReading:
    """A gift's uses and how long it lasts, as far as its text says them in numbers."""
    text = strip_tags(_text(data.get("entries")))
    uses: int | None = None
    found = _CHARGES.search(text) or _TIMES.search(text)
    if found is not None:
        uses = _amount(found[1]) or None
    elif _ONCE.search(_text(data.get("entries"))) is not None:
        uses = 1
    elif str(data.get("type", "")).lower() == "charm":
        uses = _spell_uses(data) or None
    lasts = None
    if (found := _LASTS.search(text)) is not None:
        lasts = _amount(found[1]) * _MINUTES[found[2].lower()] or None
    return GiftReading(uses, lasts)


class GiftError(Exception):
    """A gift step the rules refuse."""


class GiftService:
    """Gives, spends and ends the gifts kept on characters; every step goes to the log."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: Any | None,
        *,
        now: Callable[[str], int],
    ) -> None:
        """``now(world)`` is the world clock's minute."""
        self._store = store
        self._protocol = protocol
        self._now = now

    def grant(
        self,
        world_id: str,
        ref: str,
        kind: str,
        reading: GiftReading,
        character_ids: Sequence[str],
        by: str,
        by_name: str,
    ) -> list[str]:
        """Give the gift ``ref`` ("Name|SOURCE") to each character: their names."""
        start = self._now(world_id) if world_id else 0
        names = []
        for character_id in character_ids:
            gift = Gift(
                id=uuid.uuid4().hex[:10],
                gift=ChosenOption.model_validate(ref),
                kind=kind.lower()[:40],
                uses=reading.uses,
                since=start,
                ends=start + reading.lasts if reading.lasts else None,
            )

            def change(pc: PlayerCharacter, gift: Gift = gift) -> None:
                pc.gifts.append(gift)

            pc = self._mutate(character_id, change)
            if pc is None:
                continue
            names.append(pc.name or "?")
            self._log(world_id, by, by_name, pc, f"gift:granted:{kind.lower()}:{ref}")
        return names

    def use(self, character_id: str, gift_id: str, by: str, by_name: str) -> Gift:
        """Spend a use of a gift; the last makes it vanish. Raises for a gift that is
        gone or does not wear out."""
        spent: list[Gift] = []

        def change(pc: PlayerCharacter) -> None:
            spent.clear()
            for n, gift in enumerate(pc.gifts):
                if gift.id == gift_id and gift.left:
                    changed = gift.model_copy(update={"used": gift.used + 1})
                    spent.append(changed)
                    if changed.left:
                        pc.gifts[n] = changed
                    else:
                        del pc.gifts[n]
                    return

        pc = self._mutate(character_id, change)
        if pc is None or not spent:
            raise GiftError("gift.error.unknown")
        gift = spent[0]
        self._log(
            pc.world_id or "", by, by_name, pc, f"gift:used:{gift.gift.reference}:{gift.left or 0}"
        )
        if not gift.left:
            self._log(pc.world_id or "", by, by_name, pc, f"gift:gone:{gift.gift.reference}:used")
        return gift

    def end(
        self, character_id: str, gift_id: str, by: str, by_name: str, why: str = "taken"
    ) -> bool:
        """The gift leaves the character (taken back, or faded): False when not there."""
        gone: list[Gift] = []

        def change(pc: PlayerCharacter) -> None:
            gone.clear()
            gone.extend(g for g in pc.gifts if g.id == gift_id)
            pc.gifts[:] = [g for g in pc.gifts if g.id != gift_id]

        pc = self._mutate(character_id, change)
        if pc is None or not gone:
            return False
        self._log(pc.world_id or "", by, by_name, pc, f"gift:gone:{gone[0].gift.reference}:{why}")
        return True

    def passed(self, world_id: str, before: int, now: int, by: str, by_name: str) -> None:
        """The clock moved on to ``now``: gifts whose time is up fade."""
        del before
        for pc in self._store.characters.of_world(world_id):
            for gift in list(pc.gifts):
                if gift.ends is not None and gift.ends <= now:
                    self.end(pc.id, gift.id, by, by_name, "faded")

    def _mutate(
        self, character_id: str, change: Callable[[PlayerCharacter], None]
    ) -> PlayerCharacter | None:
        if self._store.characters.find(character_id) is None:
            return None
        return self._store.characters.mutate(character_id, change)

    def _log(self, world_id: str, by: str, by_name: str, pc: PlayerCharacter, what: str) -> None:
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
                    scope=Scope(world=world_id or None),
                )
            ]
        )


__all__ = ["GiftError", "GiftReading", "GiftService", "read_gift"]
