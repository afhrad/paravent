"""The notes a world's party keeps together, on the players' journal page.

One text per world, written by any player with a character in it and by the gamemaster.
A save carries the version of the text it was written over; when someone saved since,
it is refused with the text saved now, and the writer's own text is kept for them.
"""

from __future__ import annotations

from app.models.gamestate import PartyNotes
from app.services.adventures import EditConflict, check_base, text_version
from app.store.gamestate import GameStateStore

LIMIT = 20_000


class PartyNotesService:
    """Reads and writes ``library/party_notes.json``."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def text(self, world_id: str) -> str:
        """The world's party notes, "" when none were written yet."""
        found = self._store.party_notes.find(world_id)
        return found.text if found is not None else ""

    def save(self, world_id: str, text: str, base: str, by: str) -> PartyNotes:
        """Write the notes over the version ``base``; :class:`EditConflict` when they
        changed since (unless they now say the same)."""
        check_base(self.text(world_id), text, base)
        record = self._store.party_notes.find(world_id) or PartyNotes(id=world_id)
        record = record.model_copy(update={"text": text[:LIMIT], "by": by})
        record.touch()
        self._store.party_notes.upsert(record)
        return record


__all__ = ["EditConflict", "PartyNotesService", "text_version"]
