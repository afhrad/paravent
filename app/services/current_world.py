"""Which world a page of the library belongs to, read from its address.

The gamemaster's banner on another world's page (documentation/CURRENT-WORLD.md §5) and the
world menu's switch use it: a world's own pages, its adventures, chapters, NPCs, places,
treasure, encounters and session notes each name their world.
"""

from __future__ import annotations

import re
from typing import Any

from app.store.gamestate import GameStateStore

# A page's address and the kind of record its id names.
_PAGE = re.compile(
    r"^/gm/(worlds|adventures|chapters|npcs|places|treasure|encounters|sessions)/([A-Za-z0-9_-]+)"
)


def world_of_page(store: GameStateStore, url: str) -> str | None:
    """The world a page of the library belongs to, or None (a page of no world)."""
    found = _PAGE.match(url.split("?", 1)[0])
    if found is None:
        return None
    kind, ident = found.groups()
    if kind == "worlds":
        return ident if store.worlds.find(ident) is not None else None
    if kind in ("adventures", "chapters", "encounters", "sessions"):
        collection: Any = {
            "adventures": store.adventures,
            "chapters": store.chapters,
            "encounters": store.encounters,
            "sessions": store.session_notes,
        }[kind]
        record = collection.find(ident)
        return getattr(record, "world_id", None) if record is not None else None
    owned: Any = {"npcs": store.npcs, "places": store.locations, "treasure": store.treasures}[kind]
    record = owned.find(ident)
    return record.scope.world_id if record is not None else None


__all__ = ["world_of_page"]
