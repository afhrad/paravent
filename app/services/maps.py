"""The gamemaster's maps of a world and its adventures (documentation/MAPS.md).

A map is a picture with a scale. It belongs to its world or to one adventure, and an
adventure's map sticks to it: it is never moved up to the world, and goes to the trash
and comes back with the adventure. The picture is stored as uploaded in the scope's
``media/maps/`` (app/services/map_tiles.py); everything measured on it is measured in
its pixels, by the scale's feet per pixel.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from app.models.gamestate import MapScale, MapUnit, MapView, Place, Scope, WorldMap
from app.services import media
from app.services.adventures import AdventureService, clean_name
from app.services.i18n import Translatable
from app.services.map_tiles import MapTiles, remove_original, store_original
from app.services.trash import Trash
from app.store.gamestate import GameStateStore

# Feet in one of each unit a scale is typed in, by Paravent's own factors
# (documentation/TRANSLATION.md §6): 1 mile = 1.5 km, 1 ft = 0.3 m.
FEET_PER: dict[str, float] = {"mi": 5280.0, "km": 5280.0 / 1.5, "ft": 1.0, "m": 1 / 0.3}
UNITS: tuple[MapUnit, ...] = ("mi", "km", "ft", "m")
# The units that measure a land rather than a town: a ruler on such a map says miles or
# km, and travel times.
LONG_UNITS = frozenset({"mi", "km"})


class MapError(Translatable):
    """A map, or a change to one, that cannot be made."""


def feet_per_px(
    way: str, amount: float, unit: str, width: int, line: Sequence[float] = ()
) -> float:
    """Feet per pixel of the original: ``amount`` ``unit`` across a line drawn on the map
    (``x1, y1, x2, y2``), or across the map's ``width``."""
    if unit not in FEET_PER:
        raise MapError("atlas.error.unit")
    if not math.isfinite(amount) or amount <= 0:
        raise MapError("atlas.error.amount")
    feet = amount * FEET_PER[unit]
    if way == "width":
        return feet / width
    if way != "line" or len(line) != 4:
        raise MapError("atlas.error.line")
    x1, y1, x2, y2 = line
    pixels = math.hypot(x2 - x1, y2 - y1)
    if not math.isfinite(pixels) or pixels < 1:
        raise MapError("atlas.error.line")
    return feet / pixels


class MapService:
    """Maps in the library, their pictures and their tiles."""

    def __init__(
        self,
        store: GameStateStore,
        adventures: AdventureService,
        tiles: MapTiles,
        max_pixels: int,
    ) -> None:
        """Bind to the library, the adventures (for scopes), the tile cache, and the
        largest picture taken."""
        self._store = store
        self._adventures = adventures
        self._tiles = tiles
        self._max_pixels = max_pixels

    @property
    def tiles(self) -> MapTiles:
        """The tile cache and its background cutting."""
        return self._tiles

    def of_world(self, world_id: str) -> list[WorldMap]:
        """A world's own maps by name, none of its adventures'."""
        found = self._store.maps.list(
            lambda m: m.scope.world_id == world_id and m.scope.adventure_id is None
        )
        return sorted(found, key=lambda m: m.name.casefold())

    def of_adventure(self, adventure_id: str) -> list[WorldMap]:
        """An adventure's maps by name."""
        found = self._store.maps.list(lambda m: m.scope.adventure_id == adventure_id)
        return sorted(found, key=lambda m: m.name.casefold())

    def get(self, map_id: str) -> WorldMap:
        """One map, or :class:`MapError`."""
        found = self._store.maps.find(map_id)
        if found is None:
            raise MapError("atlas.error.gone")
        return found

    def source(self, found: WorldMap) -> Path | None:
        """The original's file, or None when it is missing."""
        return media.resolve(self._store.prose, found.scope.record_id, found.image)

    def create(
        self,
        world_id: str,
        name: str,
        filename: str,
        picture: BinaryIO,
        adventure_id: str | None = None,
    ) -> WorldMap:
        """A new map of a world, or of one of its adventures, from an uploaded picture;
        named after the file when no name is given."""
        if adventure_id is not None and self._adventures.get(adventure_id).world_id != world_id:
            raise MapError("atlas.error.gone")
        self._adventures.world(world_id)  # a world that exists
        where = Scope(world_id=world_id, adventure_id=adventure_id)
        title = name.strip() or Path(filename).stem.replace("-", " ").replace("_", " ")
        stored = store_original(
            self._store.prose, where.record_id, filename, picture, self._max_pixels
        )
        return self._store.maps.create(
            WorldMap(
                scope=where,
                name=clean_name(title),
                image=stored.path,
                width=stored.width,
                height=stored.height,
                digest=stored.digest,
            )
        )

    def update(self, map_id: str, *, name: str, notes: str) -> WorldMap:
        """Rename a map, and keep its notes."""
        self.get(map_id)
        return self._store.maps.update(map_id, name=clean_name(name), notes=notes)

    def replace_image(self, map_id: str, filename: str, picture: BinaryIO) -> WorldMap:
        """Put another picture under the map. A picture of another size is taken for the
        same map at another resolution: the scale follows it."""
        before = self.get(map_id)
        stored = store_original(
            self._store.prose, before.scope.record_id, filename, picture, self._max_pixels
        )
        scale = before.scale
        if scale is not None and stored.width != before.width:
            ratio = stored.width / before.width
            scale = scale.model_copy(
                update={
                    "feet_per_px": scale.feet_per_px / ratio,
                    "line": [v * ratio for v in scale.line],
                }
            )
        saved = self._store.maps.update(
            map_id,
            image=stored.path,
            width=stored.width,
            height=stored.height,
            digest=stored.digest,
            scale=scale,
        )
        if stored.path != before.image:
            self._remove_original(before)
        return saved

    def set_scale(
        self,
        map_id: str,
        *,
        way: str,
        amount: float,
        unit: str,
        line: Sequence[float] = (),
    ) -> WorldMap:
        """Give the map its scale: a line drawn on it and its length, or its width."""
        found = self.get(map_id)
        per_px = feet_per_px(way, amount, unit, found.width, line)
        scale = MapScale(
            feet_per_px=per_px,
            way="width" if way == "width" else "line",
            amount=amount,
            unit=unit,  # checked by feet_per_px
            line=[float(v) for v in line] if way == "line" else [],
        )
        return self._store.maps.update(map_id, scale=scale)

    def clear_scale(self, map_id: str) -> WorldMap:
        """Take the map's scale away."""
        self.get(map_id)
        return self._store.maps.update(map_id, scale=None)

    def view(self, user_id: str, map_id: str) -> MapView | None:
        """Where the user left the map, if they have seen it."""
        place = self._store.places.find(user_id)
        return place.maps.get(map_id) if place is not None else None

    def keep_view(self, user_id: str, map_id: str, x: float, y: float, zoom: float) -> None:
        """Remember where the user left the map (its centre in the original's pixels)."""
        found = self.get(map_id)
        if not all(math.isfinite(v) for v in (x, y, zoom)):
            raise MapError("atlas.error.view")
        shown = MapView(
            x=round(min(max(x, 0.0), float(found.width)), 1),
            y=round(min(max(y, 0.0), float(found.height)), 1),
            zoom=round(min(max(zoom, -8.0), 24.0), 2),
        )
        place = self._store.places.find(user_id) or Place(id=user_id)
        if place.maps.get(map_id) == shown:
            return
        self._store.places.upsert(place.model_copy(update={"maps": {**place.maps, map_id: shown}}))

    def delete(self, map_id: str, *, part_of: str | None = None, keep: bool = True) -> WorldMap:
        """Delete a map into the trash (``part_of`` the adventure deleted with it), its
        picture kept until the trash forgets it; its tiles go now, made again if it comes
        back."""
        found = self.get(map_id)
        if keep:
            Trash(self._store).keep("map", found, part_of)
        self._store.maps.delete(map_id)
        self._tiles.forget(map_id)
        if not keep:
            self._remove_original(found)
        return found

    def _remove_original(self, found: WorldMap) -> None:
        """Erase a map's original unless another map shows the same file."""
        remove_original(self._store, found)


__all__ = [
    "FEET_PER",
    "LONG_UNITS",
    "UNITS",
    "MapError",
    "MapService",
    "feet_per_px",
]
