"""What Claude is told about a world before it writes for it (documentation/MCP.md §5).

One record per world: the **setting** — which world this is, its era, its cities, its
people, how its races and monsters map onto 5e — and one text per kind of thing Claude
makes (adventure, chapter, NPC, encounter, treasure, place). The MCP server hands them
over in ``world_overview``; the gamemaster writes them on the world's
*Instructions for Claude* page. Claude reads them, never the table.
"""

from __future__ import annotations

from app.models.gamestate import INSTRUCTION_KINDS, WorldInstructions
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore

FIELDS = ("setting", *INSTRUCTION_KINDS)
TEXT_LIMIT = 20000


class InstructionsError(Translatable):
    """Instructions that cannot be saved."""


class WorldInstructionService:
    """Each world's instructions for Claude."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def get(self, world_id: str) -> WorldInstructions:
        """The world's instructions; empty ones (not saved) when none were written."""
        found = self._store.instructions.list(lambda x: x.world_id == world_id)
        return found[0] if found else WorldInstructions(world_id=world_id)

    def save(self, world_id: str, texts: dict[str, str]) -> WorldInstructions:
        """Replace the world's instructions with ``texts`` (field → text); fields left
        out are emptied."""
        if self._store.worlds.find(world_id) is None:
            raise InstructionsError("sessions.error.world")
        values = {key: (texts.get(key) or "").strip()[:TEXT_LIMIT] for key in FIELDS}
        found = self._store.instructions.list(lambda x: x.world_id == world_id)
        if found:
            return self._store.instructions.update(found[0].id, **values)
        return self._store.instructions.create(WorldInstructions(world_id=world_id, **values))

    @staticmethod
    def given(instructions: WorldInstructions) -> dict[str, str]:
        """The fields that say something, for Claude."""
        return {key: text for key in FIELDS if (text := getattr(instructions, key))}


__all__ = ["FIELDS", "InstructionsError", "WorldInstructionService"]
