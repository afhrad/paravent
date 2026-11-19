"""The world the whole table plays in (documentation/CURRENT-WORLD.md).

The gamemaster selects it; every list, page, character and player then plays in it. Kept
on the table's one settings record (``table.json``, id ``table``) beside its theme. A table
that never chose one, or whose world was deleted, plays in its first world by name; one
with no world plays in none.
"""

from __future__ import annotations

from app.models.gamestate import TableSettings, World
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore

TABLE_ID = "table"


class TableError(Translatable):
    """A world that is not there to be chosen."""


class TableService:
    """Reads and sets the table's current world."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def _worlds(self) -> list[World]:
        return sorted(self._store.worlds.list(), key=lambda w: w.name.casefold())

    def world(self) -> World | None:
        """The world the table plays in: the one chosen while it exists, else the first
        by name; None without any world."""
        record = self._store.table.find(TABLE_ID)
        chosen = self._store.worlds.find(record.world_id) if record and record.world_id else None
        if chosen is not None:
            return chosen  # the common case, asked for every character served
        worlds = self._worlds()
        return worlds[0] if worlds else None

    def world_id(self) -> str | None:
        """The current world's id, or None."""
        world = self.world()
        return world.id if world is not None else None

    def select(self, world_id: str) -> World:
        """Make ``world_id`` the table's world."""
        world = self._store.worlds.find(world_id)
        if world is None:
            raise TableError("table.error.no_world")
        record = self._store.table.find(TABLE_ID) or TableSettings(id=TABLE_ID)
        self._store.table.upsert(record.model_copy(update={"world_id": world.id}))
        return world


__all__ = ["TABLE_ID", "TableError", "TableService"]
