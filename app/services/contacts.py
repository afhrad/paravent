"""Whom a character knows: allies and enemies, rivals, and favours owed.

Downtime makes them. Carousing's table says how many allied contacts a week brought,
or a hostile one; religious service earns favours; a complication the book marks with
an asterisk has a rival behind it. Each becomes a contact of the character, unnamed
until the player or gamemaster names it, with a note. A contact may be called on (an
ally asked for help, a favour used), which is logged; and anyone may be written down by
hand.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.models.gamestate import Contact
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import count_word
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

KINDS = ("ally", "enemy", "rival", "favour", "contact")
_ALLIES = re.compile(r"(\w+) allied contacts?", re.IGNORECASE)
_HOSTILE = re.compile(r"(\w+) hostile contacts?", re.IGNORECASE)
_FAVOURS = re.compile(r"(\w+) favou?rs?\b", re.IGNORECASE)


class ContactError(Translatable):
    """A change to a contact the rules refuse."""


def made(kind: str, lines: Sequence[str]) -> list[str]:
    """The contacts an activity's outcome brings, by kind ("ally", "ally", "rival")."""
    out: list[str] = []
    for line in lines:
        head, _, rest = line.partition(":")
        if head == "rival":
            out.append("rival")
        if head != "row":
            continue
        for pattern, contact in ((_ALLIES, "ally"), (_HOSTILE, "enemy")):
            found = pattern.search(rest) if kind == "carouse" else None
            if found is not None:
                out += [contact] * (count_word(found[1].lower()) or 1)
        found = _FAVOURS.search(rest) if kind == "service" else None
        if found is not None:
            out += ["favour"] * (count_word(found[1].lower()) or 1)
    return out


class ContactService:
    """Each character's contacts (``library/contacts.json``); new ones and calls logged."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def of(self, character_id: str) -> list[Contact]:
        """A character's contacts, the oldest first."""
        held = [c for c in self._store.contacts.list() if c.character_id == character_id]
        return sorted(held, key=lambda c: c.created_at)

    def from_activity(
        self, pc: PlayerCharacter, kind: str, lines: Sequence[str], by: str, by_name: str
    ) -> list[Contact]:
        """Write down the contacts an activity's outcome brought."""
        out = [
            self._store.contacts.create(
                Contact(
                    character_id=pc.id,
                    world_id=pc.world_id,
                    kind=contact,
                    source=kind,
                )
            )
            for contact in made(kind, lines)
        ]
        if out:
            counts: dict[str, int] = {}
            for contact in out:
                counts[contact.kind] = counts.get(contact.kind, 0) + 1
            self._log(
                pc,
                by,
                by_name,
                [f"contact:new:{k}:{n}" for k, n in counts.items()],
            )
        return out

    def add(self, pc: PlayerCharacter, kind: str, name: str, note: str) -> Contact:
        if kind not in KINDS:
            raise ContactError("contacts.error.kind")
        return self._store.contacts.create(
            Contact(
                character_id=pc.id,
                world_id=pc.world_id,
                kind=kind,
                name=" ".join(name.split())[:120],
                note=note.strip()[:2000],
            )
        )

    def edit(self, character_id: str, contact_id: str, name: str, note: str) -> Contact:
        contact = self._own(character_id, contact_id)
        changed = contact.model_copy(
            update={"name": " ".join(name.split())[:120], "note": note.strip()[:2000]}
        )
        changed.touch()
        return self._store.contacts.upsert(changed)

    def call_on(self, pc: PlayerCharacter, contact_id: str, by: str, by_name: str) -> Contact:
        """Call on a contact (ask the ally, use the favour), or take that back."""
        contact = self._own(pc.id, contact_id)
        changed = contact.model_copy(update={"spent": not contact.spent})
        changed.touch()
        saved = self._store.contacts.upsert(changed)
        if saved.spent:
            name = (saved.name or "").replace(":", " ")
            self._log(pc, by, by_name, [f"contact:called:{saved.kind}:{name}"])
        return saved

    def remove(self, character_id: str, contact_id: str) -> None:
        self._own(character_id, contact_id)
        self._store.contacts.delete(contact_id)

    def _own(self, character_id: str, contact_id: str) -> Contact:
        contact = self._store.contacts.find(contact_id)
        if contact is None or contact.character_id != character_id:
            raise ContactError("contacts.error.unknown")
        return contact

    def _log(self, pc: PlayerCharacter, by: str, by_name: str, lines: Sequence[str]) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}",
                    subject_name=pc.name,
                    kind=EntryKind.CUSTOM,
                    what=line,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=pc.world_id),
                )
                for line in lines
            ]
        )


__all__ = ["KINDS", "ContactError", "ContactService", "made"]
