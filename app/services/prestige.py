"""Marks of prestige: what the guides list beside treasure as rewards of standing.

A title, a parcel of land, a stronghold (the 2024 guide's fortification), a medal, a
letter of recommendation, a special favor, a special right, or training, granted to a
character by someone of standing — a monarch, a temple, a guild — with what it is
called and a note of what it allows. The guides give none of them a rule of its own:
each is worth what its giver's sway is worth, so Paravent keeps them on the
character's story, told in the log, and leaves their weight to the table. A favor is
called in once; land and strongholds can be taken back, as the guides warn. A quest may
carry one as its reward, granted to each character on it when it is done.
"""

from __future__ import annotations

from typing import get_args

from app.models.gamestate import Mark, MarkKind
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

KINDS: tuple[str, ...] = get_args(MarkKind)
# Where the guides describe each kind, the 2024 guide first.
PRINTED: dict[str, tuple[tuple[str, str], ...]] = {
    "title": (("XDMG", "Titles"), ("DMG", "Titles")),
    "land": (("XDMG", "Parcels of Land"), ("DMG", "Parcels of Land")),
    "stronghold": (("XDMG", "Fortifications"), ("DMG", "Strongholds")),
    "medal": (("XDMG", "Medals"), ("DMG", "Medals")),
    "letter": (("XDMG", "Letters of Recommendation"), ("DMG", "Letters of Recommendation")),
    "favor": (("XDMG", "Special Favors"), ("DMG", "Special Favors")),
    "right": (("XDMG", "Special Rights"), ("DMG", "Special Rights")),
    "training": (("XDMG", "Training"), ("DMG", "Training")),
}
assert set(PRINTED) == set(KINDS)


class PrestigeError(Translatable):
    """A mark of prestige the rules refuse."""


class PrestigeService:
    """Characters' marks of prestige (``library/marks.json``), each grant, favor called
    in and mark taken back told in the log for the table."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def of(self, character_id: str) -> list[Mark]:
        """A character's marks, oldest first."""
        found = self._store.marks.list(lambda m: m.character_id == character_id)
        return sorted(found, key=lambda m: m.created_at)

    def get(self, character_id: str, mark_id: str) -> Mark:
        found = self._store.marks.find(mark_id)
        if found is None or found.character_id != character_id:
            raise PrestigeError("prestige.error.unknown")
        return found

    def grant(
        self,
        pc: PlayerCharacter,
        kind: str,
        name: str,
        granted_by: str = "",
        note: str = "",
        *,
        by: str,
        by_name: str,
    ) -> Mark:
        """Grant a character a mark, logged ``mark:<kind>:<name>`` for everyone."""
        if kind not in KINDS:
            raise PrestigeError("prestige.error.kind")
        named = _line(name, 160)
        if not named:
            raise PrestigeError("prestige.error.name")
        mark = self._store.marks.create(
            Mark(
                character_id=pc.id,
                world_id=pc.world_id,
                kind=kind,
                name=named,
                granted_by=_line(granted_by, 160),
                note=note.strip()[:2000],
            )
        )
        self._log(pc, f"mark:{kind}:{named}", by, by_name)
        return mark

    def call_in(self, pc: PlayerCharacter, mark_id: str, *, by: str, by_name: str) -> Mark:
        """A special favor called in: it is spent, logged ``mark_spent:<name>``."""
        mark = self.get(pc.id, mark_id)
        if mark.kind != "favor" or mark.spent:
            raise PrestigeError("prestige.error.spent")
        spent = mark.model_copy(update={"spent": True})
        spent.touch()
        self._log(pc, f"mark_spent:{mark.name}", by, by_name)
        return self._store.marks.upsert(spent)

    def take_back(self, pc: PlayerCharacter, mark_id: str, *, by: str, by_name: str) -> None:
        """A mark taken away (land reclaimed, a title stripped), logged
        ``mark_gone:<kind>:<name>``."""
        mark = self.get(pc.id, mark_id)
        self._store.marks.delete(mark.id)
        self._log(pc, f"mark_gone:{mark.kind}:{mark.name}", by, by_name)

    def _log(self, pc: PlayerCharacter, what: str, by: str, by_name: str) -> None:
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
                    what=what,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=pc.world_id),
                )
            ]
        )


def _line(text: str, most: int) -> str:
    return " ".join(text.split())[:most]


__all__ = ["KINDS", "PRINTED", "PrestigeError", "PrestigeService"]
