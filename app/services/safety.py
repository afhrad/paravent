"""Safety tools for the table: the X-card, and lines and veils.

The X-card is a button on a player's phone. Pressed, it tells the gamemaster, and no one
else, that someone wants the scene to change: an entry in the gamemaster's log with no
name on it, which always pops up for the gamemaster. Lines (what does not happen in the
game) and veils (what happens off screen) are a world's agreements, kept by the
gamemaster and readable by its players, who may suggest more without a name.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.gamestate import SafetySuggestion, TableSafety
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

XCARD = "xcard"


class SafetyService:
    """Reads and writes ``library/safety.json``; the X-card goes to the log."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def xcard(self, world_id: str | None) -> None:
        """Someone tapped the X-card: the gamemaster is told, with no name."""
        if self._protocol is None:
            return
        entry = ProtocolEntry(
            kind=EntryKind.CUSTOM,
            what=XCARD,
            visibility=Visibility.GM,
            scope=Scope(world=world_id),
        )
        self._protocol.append([entry])

    def of(self, world_id: str) -> TableSafety:
        """A world's agreements (empty when none were written)."""
        return self._store.safety.find(world_id) or TableSafety(id=world_id)

    def set_lists(self, world_id: str, lines: Sequence[str], veils: Sequence[str]) -> TableSafety:
        """The gamemaster's lines and veils, one per entry, blanks left out."""
        record = self.of(world_id).model_copy(
            update={"lines": _clean(lines), "veils": _clean(veils)}
        )
        record.touch()
        return self._store.safety.upsert(record)

    def suggest(self, world_id: str, kind: str, text: str) -> TableSafety:
        """A player puts a line or veil forward; no name is kept."""
        cleaned = " ".join(text.split())[:300]
        if kind not in ("line", "veil") or not cleaned:
            return self.of(world_id)
        record = self.of(world_id)
        suggestion = SafetySuggestion.model_validate({"kind": kind, "text": cleaned})
        record = record.model_copy(update={"suggested": [*record.suggested, suggestion]})
        record.touch()
        return self._store.safety.upsert(record)

    def settle(self, world_id: str, index: int, take: bool) -> TableSafety:
        """The gamemaster takes a suggestion into its list, or drops it."""
        record = self.of(world_id)
        if not 0 <= index < len(record.suggested):
            return record
        chosen = record.suggested[index]
        rest = [s for n, s in enumerate(record.suggested) if n != index]
        lines, veils = list(record.lines), list(record.veils)
        if take:
            (lines if chosen.kind == "line" else veils).append(chosen.text)
        record = record.model_copy(update={"suggested": rest, "lines": lines, "veils": veils})
        record.touch()
        return self._store.safety.upsert(record)


def _clean(values: Sequence[str]) -> list[str]:
    return [" ".join(v.split())[:300] for v in values if v.strip()]


__all__ = ["XCARD", "SafetyService"]
