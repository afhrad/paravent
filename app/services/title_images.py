"""Title images of the elements that are not set on a table (decided 2026-11-18):
places, encounters, quests and factions. A token is for what stands on a table — an NPC,
a creature, a character, treasure (documentation/TOKENS.md); these have a picture at the
head of their page instead, as worlds, adventures and chapters have their cover.

A place keeps its picture beside its scope's record, as before. An encounter, quest or
faction keeps its image in its world's ``media/`` folder, the path in its ``cover``. The
file is never deleted: taking the image away only stops showing it.
"""

from __future__ import annotations

from typing import Literal

from app.services import media
from app.services.i18n import Translatable
from app.services.locations import LocationService
from app.store.gamestate import GameStateStore

KINDS = ("place", "encounter", "quest", "faction")
Kind = Literal["place", "encounter", "quest", "faction"]


class TitleImageError(Translatable):
    """An element that cannot take a title image (gone, or of no world)."""


class TitleImages:
    """Sets, clears and finds the title images of places, encounters, quests, factions."""

    def __init__(self, store: GameStateStore, locations: LocationService) -> None:
        self._store = store
        self._locations = locations

    def owner(self, kind: str, ident: str) -> str | None:
        """The record whose folder holds the image: a place's scope record, else the
        element's world; None for one that is gone or belongs to no world."""
        store = self._store
        if kind == "place":
            place = store.locations.find(ident)
            return place.scope.record_id if place is not None else None
        if kind == "encounter":
            encounter = store.encounters.find(ident)
            return encounter.world_id if encounter is not None else None
        if kind == "quest":
            quest = store.quests.find(ident)
            return quest.world_id if quest is not None else None
        if kind == "faction":
            faction = store.factions.find(ident)
            return faction.world_id if faction is not None else None
        return None

    def of(self, kind: str, ident: str) -> tuple[str, str] | None:
        """``(record, path)`` of the element's title image, for ``media_url``; None
        without one."""
        owner = self.owner(kind, ident)
        store = self._store
        path: str | None = None
        if kind == "place":
            place = store.locations.find(ident)
            path = place.picture if place is not None else None
        elif kind == "encounter":
            encounter = store.encounters.find(ident)
            path = encounter.cover if encounter is not None else None
        elif kind == "quest":
            quest = store.quests.find(ident)
            path = quest.cover if quest is not None else None
        elif kind == "faction":
            faction = store.factions.find(ident)
            path = faction.cover if faction is not None else None
        return (owner, path) if owner and path else None

    def set(self, kind: str, ident: str, filename: str, data: bytes) -> str:
        """Store an upload and make it the element's title image; the path written.
        Raises :class:`TitleImageError` or :class:`~app.services.media.MediaError`."""
        owner = self.owner(kind, ident)
        if owner is None:
            raise TitleImageError("title_image.error.owner")
        if kind == "place":
            return self._locations.upload_picture(ident, filename, data)
        relative = media.store_upload(self._store.prose, owner, filename, data)
        self._update(kind, ident, relative)
        return relative

    def clear(self, kind: str, ident: str) -> None:
        """Show the element without a title image (the file stays)."""
        if self.owner(kind, ident) is None:
            raise TitleImageError("title_image.error.owner")
        if kind == "place":
            self._locations.clear_picture(ident)
        else:
            self._update(kind, ident, None)

    def _update(self, kind: str, ident: str, cover: str | None) -> None:
        if kind == "encounter":
            self._store.encounters.update(ident, cover=cover)
        elif kind == "quest":
            self._store.quests.update(ident, cover=cover)
        elif kind == "faction":
            self._store.factions.update(ident, cover=cover)


__all__ = ["KINDS", "TitleImageError", "TitleImages"]
