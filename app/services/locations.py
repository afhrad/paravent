"""Places of the campaign, ``{@place Name}`` (documentation/ADVENTURES.md §4).

A place is placed like an NPC — in the world, an adventure or one chapter — and found by
name nearest first from where a text names it. What is there is written in its notes,
in Paravent Markdown: the NPCs, encounters and treasure they name are the place's own
"here" list, so nothing is kept twice. Renaming a place rewrites ``{@place Old}`` in
every text of the world.
"""

from __future__ import annotations

import builtins
import re

from app.models.gamestate import Location, Scope
from app.services import media, scope
from app.services.adventures import AdventureService, check_base, clean_name
from app.services.naming import SUBJECT_PREFIX, Naming, NamingError, pool_key
from app.services.npcs import ObjectNotFound, Place, clean_roles
from app.store.gamestate import GameStateStore

# ``{@npc Name}``, ``{@encounter Name|label}``… — the campaign's objects a text names.
_NAMED = re.compile(r"\{@(npc|encounter|treasure)\s+([^}|]+)")


def named_in(text: str) -> list[tuple[str, str]]:
    """``(kind, name)`` of every NPC, encounter and treasure ``text`` names, once each,
    in the order written."""
    seen: list[tuple[str, str]] = []
    for kind, name in _NAMED.findall(text or ""):
        found = (kind, name.strip())
        if found[1] and found not in seen:
            seen.append(found)
    return seen


class LocationService:
    """Places in the library."""

    def __init__(self, store: GameStateStore, adventures: AdventureService) -> None:
        """Bind to the library and the adventures (for scopes and renames)."""
        self._store = store
        self._adventures = adventures

    def list(self, world_id: str, within: Scope | None = None) -> list[Location]:
        """A world's places by name, or only those inside ``within``."""
        found = self._store.locations.list(lambda x: x.scope.world_id == world_id)
        if within is not None:
            found = [x for x in found if scope.within(x.scope, within)]
        return sorted(found, key=lambda x: x.name.casefold())

    def get(self, location_id: str) -> Location:
        """One place, or :class:`~app.services.npcs.ObjectNotFound`."""
        found = self._store.locations.find(location_id)
        if found is None:
            raise ObjectNotFound(location_id)
        return found

    def find(self, name: str, here: Scope) -> Location | None:
        """The place called ``name`` nearest to ``here``."""
        return scope.nearest(
            self._store.locations.list(), name, here, lambda x: x.scope, lambda x: x.name
        )

    def place(self, location: Location) -> Place:
        """Where the place sits — world, adventure, chapter — by name."""
        return Place(location.scope, tuple(scope.label(self._adventures, location.scope)))

    def roll_name(self, location_id: str, naming: Naming, style: str, language: str) -> str:
        """A place name in ``style`` (a kind of place, or one style of it), preferring the
        reader's language; none of the world's places is named so already. Empty when
        the style gave nothing."""
        location = self.get(location_id)
        key = style or pool_key(SUBJECT_PREFIX, "settlement")
        taken = [x.name for x in self.list(location.scope.world_id)]
        try:
            names = naming.names(key, count=1, avoid=taken, language=language)
        except NamingError:
            return ""
        return names[0] if names else ""

    def create(self, name: str, scope_value: str, world_id: str) -> Location:
        """A new place in a scope of this world."""
        where = scope.decode(scope_value, self._adventures, world_id)
        return self._store.locations.create(Location(scope=where, name=clean_name(name)))

    def update(
        self,
        location_id: str,
        *,
        name: str,
        scope_value: str,
        tags: builtins.list[str],
        notes: str,
        base: str | None = None,
    ) -> Location:
        """Save a place; a new name follows into the world's texts. ``base`` is the
        version of the notes the form opened; notes changed since raise
        :class:`~app.services.adventures.EditConflict`."""
        before = self.get(location_id)
        check_base(before.notes, notes, base)
        where = scope.decode(scope_value, self._adventures, before.scope.world_id)
        picture = before.picture if where.record_id == before.scope.record_id else None
        saved = self._store.locations.update(
            location_id,
            name=clean_name(name),
            scope=where,
            tags=clean_roles(tags),
            notes=notes,
            picture=picture,
        )
        if saved.name != before.name:
            self._adventures.rewrite_references(
                where.world_id, "place", before.name, saved.name, where.adventure_id
            )
        return saved

    def upload_picture(self, location_id: str, filename: str, data: bytes) -> str:
        """Store a picture beside the place's scope and use it."""
        found = self.get(location_id)
        relative = media.store_upload(self._store.prose, found.scope.record_id, filename, data)
        self._store.locations.update(location_id, picture=relative)
        return relative

    def clear_picture(self, location_id: str) -> None:
        """Show the place without a picture (the image file stays)."""
        self.get(location_id)
        self._store.locations.update(location_id, picture=None)

    def delete(self, location_id: str) -> Location:
        """Delete a place; returns what it was."""
        found = self.get(location_id)
        self._store.locations.delete(location_id)
        return found


__all__ = ["LocationService", "named_in"]
