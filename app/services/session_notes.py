"""Session notes: what happened at the table, one note per day of play
(documentation/ADVENTURES.md §4b).

The gamemaster's own words beside what the log already knows — the fights, experience
and levels. Claude reads them through the MCP server's campaign memory, so a new
adventure follows the story, not only the numbers. Notes are the gamemaster's: the MCP
tools read them and never write them.
"""

from __future__ import annotations

import builtins
from datetime import date

from app.models.gamestate import SessionNote
from app.services.adventures import check_base
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore


class SessionNoteNotFound(LookupError):
    """No session note with that id."""


class SessionNoteError(Translatable):
    """A note that cannot be saved."""


class SessionNoteService:
    """A world's session notes."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def list(self, world_id: str) -> list[SessionNote]:
        """A world's notes, newest day first."""
        found = self._store.session_notes.list(lambda n: n.world_id == world_id)
        return sorted(found, key=lambda n: (n.day, n.created_at), reverse=True)

    def get(self, note_id: str) -> SessionNote:
        """One note, or :class:`SessionNoteNotFound`."""
        note = self._store.session_notes.find(note_id)
        if note is None:
            raise SessionNoteNotFound(note_id)
        return note

    def _adventure(self, world_id: str, adventure_id: str | None) -> str | None:
        if not adventure_id:
            return None
        adventure = self._store.adventures.find(adventure_id)
        if adventure is None or adventure.world_id != world_id:
            raise SessionNoteError("sessions.error.adventure")
        return adventure.id

    def create(
        self, world_id: str, day: date, adventure_id: str | None = None, text: str = ""
    ) -> SessionNote:
        """A note for a day of play in a world."""
        if self._store.worlds.find(world_id) is None:
            raise SessionNoteError("sessions.error.world")
        return self._store.session_notes.create(
            SessionNote(
                world_id=world_id,
                day=day,
                adventure_id=self._adventure(world_id, adventure_id),
                text=text,
            )
        )

    def update(
        self,
        note_id: str,
        *,
        day: date,
        adventure_id: str | None,
        text: str,
        base: str | None = None,
    ) -> SessionNote:
        """Save a note; ``base`` is the text version the form opened, so an edit made
        meanwhile raises :class:`~app.services.adventures.EditConflict`."""
        note = self.get(note_id)
        check_base(note.text, text, base)
        return self._store.session_notes.update(
            note_id,
            day=day,
            adventure_id=self._adventure(note.world_id, adventure_id),
            text=text,
        )

    def delete(self, note_id: str) -> SessionNote:
        """Delete a note; returns what it was."""
        note = self.get(note_id)
        self._store.session_notes.delete(note_id)
        return note

    def on(self, world_id: str, day: date) -> builtins.list[SessionNote]:
        """The notes of one day."""
        return [n for n in self.list(world_id) if n.day == day]


__all__ = ["SessionNoteError", "SessionNoteNotFound", "SessionNoteService"]
