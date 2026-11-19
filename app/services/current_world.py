"""The world the gamemaster is working in, for what is made without naming one.

Paravent keeps no "current world" setting; it is read from where the gamemaster has been.
The navigation already remembers, per user, the last page read in each section
(app/services/places.py), so the world of the last page of the Worlds section — a world,
an adventure, a chapter, an NPC, a place, a treasure — is the one being worked in; then
that of the last page read anywhere; and a table with one world needs no memory at all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.models.gamestate import World
from app.services.places import PlaceService
from app.store.gamestate import GameStateStore

# A page's address and the kind of record its id names.
_PAGE = re.compile(
    r"^/gm/(worlds|adventures|chapters|npcs|places|treasure|encounters)/([A-Za-z0-9_-]+)"
)


def world_of_page(store: GameStateStore, url: str) -> str | None:
    """The world a page of the library belongs to, or None (a page of no world)."""
    found = _PAGE.match(url.split("?", 1)[0])
    if found is None:
        return None
    kind, ident = found.groups()
    if kind == "worlds":
        return ident
    if kind in ("adventures", "chapters", "encounters"):
        collection: Any = {
            "adventures": store.adventures,
            "chapters": store.chapters,
            "encounters": store.encounters,
        }[kind]
        record = collection.find(ident)
        return getattr(record, "world_id", None) if record is not None else None
    owned: Any = {"npcs": store.npcs, "places": store.locations, "treasure": store.treasures}[kind]
    record = owned.find(ident)
    return record.scope.world_id if record is not None else None


def current_world(
    store: GameStateStore, places: PlaceService, user_id: str, worlds: Sequence[World]
) -> str | None:
    """The world ``user_id`` is working in, one of ``worlds``: that of the last page read in
    the Worlds section, else of the last page read, else the only world; None when there
    is no world, or several and nothing says which."""
    known = {w.id for w in worlds}
    place = places.get(user_id)
    for url in (places.nav(user_id).get("/gm/worlds", ""), place.last):
        world = world_of_page(store, url) if url else None
        if world in known:
            return world
    return worlds[0].id if len(worlds) == 1 else None


__all__ = ["current_world", "world_of_page"]
