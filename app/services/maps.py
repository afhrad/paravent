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

from app.models.gamestate import (
    MapFrame,
    MapPin,
    MapScale,
    MapUnit,
    MapView,
    Place,
    Scope,
    WorldMap,
)
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
# What a pin may point at (documentation/MAPS.md §6).
PIN_KINDS = ("place", "npc", "encounter", "treasure", "adventure", "chapter", "map", "note")


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
        same map at another resolution: the scale, the pins and the frames of the maps
        lying on it follow it."""
        before = self.get(map_id)
        stored = store_original(
            self._store.prose, before.scope.record_id, filename, picture, self._max_pixels
        )
        rx, ry = stored.width / before.width, stored.height / before.height
        scale = before.scale
        if scale is not None and stored.width != before.width:
            scale = scale.model_copy(
                update={
                    "feet_per_px": scale.feet_per_px / rx,
                    "line": [v * (rx if i % 2 == 0 else ry) for i, v in enumerate(scale.line)],
                }
            )
        pins = [p.model_copy(update={"x": p.x * rx, "y": p.y * ry}) for p in before.pins]
        saved = self._store.maps.update(
            map_id,
            image=stored.path,
            width=stored.width,
            height=stored.height,
            digest=stored.digest,
            scale=scale,
            pins=pins,
        )
        if (rx, ry) != (1, 1):
            for child in self.framed_on(map_id):
                frame = child.frame
                assert frame is not None
                moved = MapFrame(
                    x=frame.x * rx, y=frame.y * ry, width=frame.width * rx, height=frame.height * ry
                )
                self._store.maps.update(child.id, frame=moved)
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

    # -- pins (documentation/MAPS.md §6) ---------------------------------------------

    def target(self, kind: str, target: str) -> tuple[str, Scope | None] | None:
        """What a pin of ``kind`` points at: its name, and where it is placed (None for an
        adventure, chapter or map, which any map of their world may show); None when
        there is no such thing."""
        store = self._store
        if kind == "place":
            place = store.locations.find(target)
            return (place.name, place.scope) if place else None
        if kind == "npc":
            npc = store.npcs.find(target)
            return (npc.name, npc.scope) if npc else None
        if kind == "treasure":
            treasure = store.treasures.find(target)
            return (treasure.name, treasure.scope) if treasure else None
        if kind == "encounter":
            encounter = store.encounters.find(target)
            return (encounter.name, encounter.scope) if encounter and encounter.scope else None
        if kind == "adventure":
            adventure = store.adventures.find(target)
            return (adventure.name, Scope(world_id=adventure.world_id)) if adventure else None
        if kind == "chapter":
            chapter = store.chapters.find(target)
            return (chapter.title, Scope(world_id=chapter.world_id)) if chapter else None
        if kind == "map":
            other = store.maps.find(target)
            return (other.name, Scope(world_id=other.scope.world_id)) if other else None
        return None

    def shows(self, found: WorldMap, kind: str, target: str) -> bool:
        """Whether ``found`` may point at that: something of its world, and of an NPC,
        place, encounter or treasure one placed in the world itself or, on an adventure's
        map, in that adventure."""
        if kind == "note":
            return True
        if kind == "map" and target == found.id:
            return False
        hit = self.target(kind, target)
        if hit is None or hit[1] is None or hit[1].world_id != found.scope.world_id:
            return False
        where = hit[1]
        if kind in ("place", "npc", "encounter", "treasure"):
            return where.adventure_id is None or where.adventure_id == found.scope.adventure_id
        return True

    def _clamped(self, found: WorldMap, x: float, y: float) -> tuple[float, float]:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise MapError("atlas.error.view")
        return (
            round(min(max(x, 0.0), float(found.width)), 1),
            round(min(max(y, 0.0), float(found.height)), 1),
        )

    def add_pin(
        self,
        map_id: str,
        *,
        x: float,
        y: float,
        kind: str,
        target: str = "",
        label: str = "",
        note: str = "",
    ) -> MapPin:
        """Pin something of the map's world to a spot on it, or a note."""
        found = self.get(map_id)
        if kind not in PIN_KINDS:
            raise MapError("atlas.error.pin")
        if kind != "note" and not self.shows(found, kind, target):
            raise MapError("atlas.error.pin")
        hit = self.target(kind, target) if kind != "note" else None
        label = " ".join(label.split())[:120]
        if kind == "note" and not label:
            raise MapError("atlas.error.note")
        px, py = self._clamped(found, x, y)
        pin = MapPin(
            x=px,
            y=py,
            kind=kind,  # checked against PIN_KINDS
            target=target if kind != "note" else "",
            name=hit[0] if hit else "",
            label=label,
            note=note.strip()[:4000] if kind == "note" else "",
        )
        self._store.maps.update(map_id, pins=[*found.pins, pin])
        return pin

    def _pin(self, found: WorldMap, pin_id: str) -> MapPin:
        pin = next((p for p in found.pins if p.id == pin_id), None)
        if pin is None:
            raise MapError("atlas.error.pin_gone")
        return pin

    def move_pin(self, map_id: str, pin_id: str, x: float, y: float) -> MapPin:
        """Move a pin to another spot."""
        found = self.get(map_id)
        pin = self._pin(found, pin_id)
        px, py = self._clamped(found, x, y)
        moved = pin.model_copy(update={"x": px, "y": py})
        self._store.maps.update(map_id, pins=[moved if p.id == pin_id else p for p in found.pins])
        return moved

    def edit_pin(self, map_id: str, pin_id: str, *, label: str, note: str = "") -> MapPin:
        """Change what a pin is called, and a note's text."""
        found = self.get(map_id)
        pin = self._pin(found, pin_id)
        label = " ".join(label.split())[:120]
        if pin.kind == "note" and not label:
            raise MapError("atlas.error.note")
        edited = pin.model_copy(
            update={"label": label, "note": note.strip()[:4000] if pin.kind == "note" else ""}
        )
        self._store.maps.update(map_id, pins=[edited if p.id == pin_id else p for p in found.pins])
        return edited

    def remove_pin(self, map_id: str, pin_id: str) -> None:
        """Take a pin off the map."""
        found = self.get(map_id)
        self._pin(found, pin_id)
        self._store.maps.update(map_id, pins=[p for p in found.pins if p.id != pin_id])

    def pins_to(self, kind: str, target: str) -> list[tuple[WorldMap, MapPin]]:
        """Every pin pointing at that, with its map, for "On the map"."""
        out = []
        for found in sorted(self._store.maps.list(), key=lambda m: m.name.casefold()):
            out += [(found, p) for p in found.pins if p.kind == kind and p.target == target]
        return out

    # -- maps within maps -------------------------------------------------------------

    def parent_of(self, found: WorldMap) -> WorldMap | None:
        """The map ``found`` lies on, while it exists in the same world."""
        if not found.parent or found.frame is None:
            return None
        parent = self._store.maps.find(found.parent)
        if parent is None or parent.scope.world_id != found.scope.world_id:
            return None
        return parent

    def path(self, found: WorldMap) -> list[WorldMap]:
        """The maps ``found`` lies in, the widest first."""
        out: list[WorldMap] = []
        seen = {found.id}
        current = self.parent_of(found)
        while current is not None and current.id not in seen:
            out.insert(0, current)
            seen.add(current.id)
            current = self.parent_of(current)
        return out

    def framed_on(self, map_id: str) -> list[WorldMap]:
        """The maps lying on this one, by name."""
        found = self._store.maps.list(lambda m: m.parent == map_id and m.frame is not None)
        return sorted(found, key=lambda m: m.name.casefold())

    def may_lie_on(self, child: WorldMap, parent: WorldMap) -> bool:
        """Whether ``child`` may lie on ``parent``: the same world, never on itself or on
        a map lying in it, and a world's map never on an adventure's; an adventure's map
        on a world map or one of its own adventure's."""
        if child.id == parent.id or child.scope.world_id != parent.scope.world_id:
            return False
        if parent.scope.adventure_id and parent.scope.adventure_id != child.scope.adventure_id:
            return False
        return child.id not in {m.id for m in self.path(parent)}

    def frame_candidates(self, parent: WorldMap) -> list[WorldMap]:
        """The maps that may lie on ``parent``."""
        world = self._store.maps.list(lambda m: m.scope.world_id == parent.scope.world_id)
        found = [m for m in world if self.may_lie_on(m, parent)]
        return sorted(found, key=lambda m: m.name.casefold())

    def set_frame(self, child_id: str, parent_id: str, corners: Sequence[float]) -> WorldMap:
        """Lay a map on another, in the rectangle of two corners (``x1, y1, x2, y2``) in
        the other's pixels."""
        child, parent = self.get(child_id), self.get(parent_id)
        if not self.may_lie_on(child, parent):
            raise MapError("atlas.error.frame")
        if len(corners) != 4 or not all(math.isfinite(v) for v in corners):
            raise MapError("atlas.error.frame_corners")
        x1, y1 = self._clamped(parent, corners[0], corners[1])
        x2, y2 = self._clamped(parent, corners[2], corners[3])
        width, height = abs(x2 - x1), abs(y2 - y1)
        if width < 2 or height < 2:
            raise MapError("atlas.error.frame_corners")
        frame = MapFrame(x=min(x1, x2), y=min(y1, y2), width=width, height=height)
        return self._store.maps.update(child_id, parent=parent_id, frame=frame)

    def clear_frame(self, child_id: str) -> WorldMap:
        """Take a map off the map it lies on."""
        self.get(child_id)
        return self._store.maps.update(child_id, parent=None, frame=None)

    def scales_disagree(self, child: WorldMap) -> tuple[float, float] | None:
        """``(by the frame, by its own scale)``, how far the map reaches across in feet,
        when both maps have a scale and the two differ by more than a tenth."""
        parent = self.parent_of(child)
        if parent is None or parent.scale is None or child.scale is None:
            return None
        assert child.frame is not None
        framed = child.frame.width * parent.scale.feet_per_px
        own = child.width * child.scale.feet_per_px
        if abs(framed - own) <= 0.1 * max(framed, own):
            return None
        return framed, own

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
    "PIN_KINDS",
    "MapError",
    "MapService",
    "feet_per_px",
]
