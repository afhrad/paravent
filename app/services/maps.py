"""The gamemaster's maps of a world and its adventures (documentation/MAPS.md).

A map is a picture with a scale. It belongs to its world or to one adventure, and an
adventure's map sticks to it: it is never moved up to the world, and goes to the trash
and comes back with the adventure. The picture is stored as uploaded in the scope's
``media/maps/`` (app/services/map_tiles.py); everything measured on it is measured in
its pixels, by the scale's feet per pixel.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.models.gamestate import (
    MapFrame,
    MapGrid,
    MapLabel,
    MapLayer,
    MapPin,
    MapRoute,
    MapScale,
    MapUnit,
    MapView,
    Place,
    RouteStretch,
    Scope,
    WorldMap,
)
from app.services import media
from app.services.adventures import AdventureService, clean_name
from app.services.i18n import Translatable
from app.services.map_tiles import MapTiles, remove_original, store_original
from app.services.trash import Trash
from app.services.travel import TERRAINS
from app.store.gamestate import GameStateStore

# Feet in one of each unit a scale is typed in, by Paravent's own factors
# (documentation/TRANSLATION.md §6): 1 mile = 1.5 km, 1 ft = 0.3 m.
FEET_PER: dict[str, float] = {"mi": 5280.0, "km": 5280.0 / 1.5, "ft": 1.0, "m": 1 / 0.3}
UNITS: tuple[MapUnit, ...] = ("mi", "km", "ft", "m")
# The units that measure a land rather than a town: a ruler on such a map says miles or
# km, and travel times.
LONG_UNITS = frozenset({"mi", "km"})
# The layers a new map starts with, by role, in drawing order (documentation/MAPS.md §7),
# and their names where the gamemaster's language gives none.
LAYER_ROLES: tuple[str, ...] = ("places", "names", "roads", "notes")
LAYER_NAMES = {"places": "Places", "names": "Names", "roads": "Roads", "notes": "Notes"}
LABEL_STYLES: tuple[str, ...] = ("settlement", "region", "water", "feature")
# What a route may be (documentation/MAPS.md §8); all but a way across country ("") are
# paths nobody has to find.
ROUTE_KINDS: tuple[str, ...] = ("road", "trail", "river", "sea", "")
FEET_PER_MILE = 5280.0
# What a pin may point at (documentation/MAPS.md §6).
PIN_KINDS = (
    "place",
    "npc",
    "encounter",
    "treasure",
    "adventure",
    "chapter",
    "map",
    "character",
    "quest",
    "faction",
    "handout",
    "session",
    "reference",
    "note",
)
# What a pin may point at that belongs to no world: a character plays in the table's
# world, an entry of the books is in every world that declares them.
UNPLACED_KINDS = frozenset({"character", "reference"})
# The layers players see once a map is shown to them, unless the gamemaster chooses
# (documentation/MAPS.md §9): the names and the roads, never the notes.
PLAYER_ROLES = frozenset({"names", "roads"})
# What the hex grid is called among a user's shown and hidden layers.
GRID = "grid"
# The smallest hex the grid draws, in the original's pixels.
MIN_HEX_PX = 8.0


class MapError(Translatable):
    """A map, or a change to one, that cannot be made."""


@dataclass(frozen=True)
class Stop:
    """A place on a route a journey may start or end at: how far along the route (in
    miles from its first point), which point it is, and what is there (a pin's name)."""

    at: float
    index: int
    name: str = ""


def stretches_of(terrains: Sequence[str], difficult: Sequence[bool]) -> list[RouteStretch]:
    """A route's stretches from each segment's terrain and difficulty: consecutive
    segments alike make one stretch."""
    out: list[RouteStretch] = []
    for index, terrain in enumerate(terrains):
        hard = bool(difficult[index]) if index < len(difficult) else False
        name = terrain if terrain in TERRAINS else ""
        if out and out[-1].terrain == name and out[-1].difficult == hard:
            continue
        out.append(RouteStretch(start=index, terrain=name, difficult=hard))
    return out or [RouteStretch(start=0)]


def _merged(stretches: Sequence[RouteStretch]) -> list[RouteStretch]:
    """Stretches with a neighbour alike folded into it."""
    out: list[RouteStretch] = []
    for st in sorted(stretches, key=lambda x: x.start):
        if out and (out[-1].terrain, out[-1].difficult) == (st.terrain, st.difficult):
            continue
        out.append(st)
    return out


def default_layers(names: Mapping[str, str] | None = None) -> list[MapLayer]:
    """The four layers a new map starts with, named in ``names`` by role."""
    given = names or {}
    return [
        MapLayer(
            name=(given.get(role) or LAYER_NAMES[role])[:60],
            role=role,
            players=role in PLAYER_ROLES,
        )
        for role in LAYER_ROLES
    ]


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
        entry_name: Callable[[str], str | None] | None = None,
    ) -> None:
        """Bind to the library, the adventures (for scopes), the tile cache, and the
        largest picture taken; ``entry_name`` names an entry of the books from its
        ``category|name|source``, or None when there is none."""
        self._store = store
        self._adventures = adventures
        self._tiles = tiles
        self._max_pixels = max_pixels
        self._entry_name = entry_name

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
        layer_names: Mapping[str, str] | None = None,
    ) -> WorldMap:
        """A new map of a world, or of one of its adventures, from an uploaded picture;
        named after the file when no name is given; its four layers named in
        ``layer_names`` (the gamemaster's language), by role."""
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
                layers=default_layers(layer_names),
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
        labels = [
            lab.model_copy(update={"x": lab.x * rx, "y": lab.y * ry, "size": lab.size * rx})
            for lab in before.labels
        ]
        routes = [
            r.model_copy(update={"points": [[x * rx, y * ry] for x, y in r.points]})
            for r in before.routes
        ]
        grid = before.grid
        if grid is not None:
            grid = grid.model_copy(update={"x": grid.x * rx, "y": grid.y * ry})
        saved = self._store.maps.update(
            map_id,
            image=stored.path,
            width=stored.width,
            height=stored.height,
            digest=stored.digest,
            scale=scale,
            pins=pins,
            labels=labels,
            routes=routes,
            grid=grid,
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

    # -- the hex grid (documentation/MAPS.md §8b) -------------------------------------

    @staticmethod
    def hex_px(found: WorldMap) -> float | None:
        """How far a hex of the map's grid is across, flat side to flat side, in the
        original's pixels; None without a grid or a scale."""
        if found.grid is None or found.scale is None:
            return None
        return found.grid.amount * FEET_PER[found.grid.unit] / found.scale.feet_per_px

    def set_grid(
        self,
        map_id: str,
        *,
        amount: float,
        unit: str,
        orientation: str = "flat",
        x: float | None = None,
        y: float | None = None,
    ) -> WorldMap:
        """Lay a hex grid over the map: ``amount`` ``unit`` across a hex, the hexes
        standing on a flat side or a corner, one centred at ``x``, ``y`` (kept as they
        were when not given). The map needs its scale first."""
        found = self.get(map_id)
        if found.scale is None:
            raise MapError("atlas.error.grid_scale")
        if unit not in FEET_PER:
            raise MapError("atlas.error.unit")
        if not math.isfinite(amount) or amount <= 0:
            raise MapError("atlas.error.amount")
        across = amount * FEET_PER[unit] / found.scale.feet_per_px
        if across < MIN_HEX_PX or across > max(found.width, found.height):
            raise MapError("atlas.error.grid_size")
        before = found.grid
        cx = x if x is not None and math.isfinite(x) else (before.x if before else 0.0)
        cy = y if y is not None and math.isfinite(y) else (before.y if before else 0.0)
        grid = MapGrid(
            amount=amount,
            unit=unit,  # checked above
            orientation="pointy" if orientation == "pointy" else "flat",
            x=round(min(max(cx, 0.0), float(found.width)), 1),
            y=round(min(max(cy, 0.0), float(found.height)), 1),
        )
        return self._store.maps.update(map_id, grid=grid)

    def clear_grid(self, map_id: str) -> WorldMap:
        """Take the hex grid away."""
        self.get(map_id)
        return self._store.maps.update(map_id, grid=None)

    # -- the players (documentation/MAPS.md §9) ---------------------------------------

    def set_players(self, map_id: str, shown: bool) -> WorldMap:
        """Show the map to the players of its world, or hide it from them again."""
        self.get(map_id)
        return self._store.maps.update(map_id, players=shown)

    def for_players(self, world_id: str | None) -> list[WorldMap]:
        """The maps of a world shown to its players, the world's own first, by name."""
        if not world_id:
            return []
        found = self._store.maps.list(lambda m: m.scope.world_id == world_id and m.players)
        return sorted(found, key=lambda m: (m.scope.adventure_id is not None, m.name.casefold()))

    @staticmethod
    def player_layers(found: WorldMap) -> set[str]:
        """The ids of the layers players see on a map."""
        return {layer.id for layer in found.layers if layer.players}

    def players_see(self, found: WorldMap, kind: str, target: str) -> bool:
        """Whether a pin's target means something to players: a note always, a map only
        when it is shown to them too, anything else while it exists."""
        if kind == "note":
            return True
        if kind == "map":
            other = self._store.maps.find(target)
            return other is not None and other.players and self.shows(found, kind, target)
        if kind in ("handout", "session"):
            # Whom a handout went to, what the gamemaster noted of a day: theirs alone.
            return False
        if kind == "quest":
            quest = self._store.quests.find(target)
            if quest is None or not quest.shown:
                return False
        return self.shows(found, kind, target)

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
        if kind == "character":
            pc = store.characters.find(target)
            return (pc.name or pc.id, None) if pc else None
        if kind == "quest":
            quest = store.quests.find(target)
            return (quest.name, Scope(world_id=quest.world_id)) if quest else None
        if kind == "faction":
            faction = store.factions.find(target)
            return (faction.name, Scope(world_id=faction.world_id)) if faction else None
        if kind == "handout":
            handout = store.handouts.find(target)
            return (handout.title, Scope(world_id=handout.world_id)) if handout else None
        if kind == "session":
            note = store.session_notes.find(target)
            return (note.day.isoformat(), Scope(world_id=note.world_id)) if note else None
        if kind == "reference" and self._entry_name is not None:
            name = self._entry_name(target)
            return (name, None) if name else None
        return None

    def shows(self, found: WorldMap, kind: str, target: str) -> bool:
        """Whether ``found`` may point at that: something of its world; on an adventure's
        map, an NPC, place, encounter or treasure only of the world itself or of that
        adventure (it travels with the adventure), on a world map one of any adventure."""
        if kind == "note":
            return True
        if kind == "map" and target == found.id:
            return False
        if kind in UNPLACED_KINDS:
            return self.target(kind, target) is not None
        hit = self.target(kind, target)
        if hit is None or hit[1] is None or hit[1].world_id != found.scope.world_id:
            return False
        where = hit[1]
        if kind in ("place", "npc", "encounter", "treasure") and found.scope.adventure_id:
            return where.adventure_id in (None, found.scope.adventure_id)
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
        layer: str = "",
    ) -> MapPin:
        """Pin something of the map's world to a spot on it, or a note, on ``layer``
        (else the layer of its kind: *Notes* for a note, *Places* for the rest)."""
        found = self.with_layers(map_id)
        if kind not in PIN_KINDS:
            raise MapError("atlas.error.pin")
        if kind != "note" and not self.shows(found, kind, target):
            raise MapError("atlas.error.pin")
        hit = self.target(kind, target) if kind != "note" else None
        label = " ".join(label.split())[:120]
        if kind == "note" and not label:
            raise MapError("atlas.error.note")
        px, py = self._clamped(found, x, y)
        on = self._open_layer(found, layer, "notes" if kind == "note" else "places")
        pin = MapPin(
            layer=on.id,
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
        found = self.with_layers(map_id)
        pin = self._pin(found, pin_id)
        self._unlocked(found, pin.layer)
        px, py = self._clamped(found, x, y)
        moved = pin.model_copy(update={"x": px, "y": py})
        self._store.maps.update(map_id, pins=[moved if p.id == pin_id else p for p in found.pins])
        return moved

    def edit_pin(
        self, map_id: str, pin_id: str, *, label: str, note: str = "", layer: str = ""
    ) -> MapPin:
        """Change what a pin is called, a note's text, and the layer it sits on."""
        found = self.with_layers(map_id)
        pin = self._pin(found, pin_id)
        label = " ".join(label.split())[:120]
        if pin.kind == "note" and not label:
            raise MapError("atlas.error.note")
        on = self._layer(found, layer).id if layer else pin.layer
        edited = pin.model_copy(
            update={
                "label": label,
                "note": note.strip()[:4000] if pin.kind == "note" else "",
                "layer": on,
            }
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

    # -- layers and names (documentation/MAPS.md §7) -------------------------------

    def with_layers(self, map_id: str, names: Mapping[str, str] | None = None) -> WorldMap:
        """The map with its layers: a map from before them gets the four it would start
        with, and whatever sits on no layer of it (a pin from before) goes onto the
        layer of its kind. Writes only when something changed."""
        found = self.get(map_id)
        layers = found.layers or default_layers(names)
        ids = {layer.id for layer in layers}
        by_role: dict[str, str] = {layer.role: layer.id for layer in layers if layer.role}
        first = layers[0].id

        def home(role: str) -> str:
            return by_role.get(role, first)

        pins = [
            p
            if p.layer in ids
            else p.model_copy(update={"layer": home("notes" if p.kind == "note" else "places")})
            for p in found.pins
        ]
        labels = [
            lab if lab.layer in ids else lab.model_copy(update={"layer": home("names")})
            for lab in found.labels
        ]
        routes = [
            r if r.layer in ids else r.model_copy(update={"layer": home("roads")})
            for r in found.routes
        ]
        if (layers, pins, labels, routes) == (found.layers, found.pins, found.labels, found.routes):
            return found
        return self._store.maps.update(
            map_id, layers=layers, pins=pins, labels=labels, routes=routes
        )

    def _layer(self, found: WorldMap, layer_id: str) -> MapLayer:
        layer = next((x for x in found.layers if x.id == layer_id), None)
        if layer is None:
            raise MapError("atlas.error.layer")
        return layer

    def _unlocked(self, found: WorldMap, layer_id: str) -> None:
        layer = next((x for x in found.layers if x.id == layer_id), None)
        if layer is not None and layer.locked:
            raise MapError("atlas.error.locked")

    def _open_layer(self, found: WorldMap, layer_id: str, role: str) -> MapLayer:
        """The layer something new goes on: the one chosen, else the one of its role,
        else the first; never a locked one."""
        if layer_id:
            layer = self._layer(found, layer_id)
        else:
            layer = next((x for x in found.layers if x.role == role), found.layers[0])
        if layer.locked:
            raise MapError("atlas.error.locked")
        return layer

    def counts(self, found: WorldMap) -> dict[str, int]:
        """How many things sit on each layer."""
        counted = {layer.id: 0 for layer in found.layers}
        on = [p.layer for p in found.pins] + [x.layer for x in found.labels]
        for layer_id in [*on, *(r.layer for r in found.routes)]:
            if layer_id in counted:
                counted[layer_id] += 1
        return counted

    def _layers(self, map_id: str, layers: list[MapLayer]) -> None:
        self._store.maps.update(map_id, layers=layers)

    def add_layer(self, map_id: str, name: str) -> MapLayer:
        """A new layer on top."""
        found = self.with_layers(map_id)
        title = " ".join(name.split())[:60]
        if not title:
            raise MapError("atlas.error.layer_name")
        layer = MapLayer(name=title)
        self._layers(map_id, [*found.layers, layer])
        return layer

    def edit_layer(
        self,
        map_id: str,
        layer_id: str,
        *,
        name: str,
        visible: bool,
        locked: bool,
        players: bool | None = None,
    ) -> MapLayer:
        """A layer's name, whether a newcomer sees it, whether it is locked, and whether
        the players see it (kept as it was when not given)."""
        found = self.with_layers(map_id)
        layer = self._layer(found, layer_id)
        title = " ".join(name.split())[:60]
        if not title:
            raise MapError("atlas.error.layer_name")
        edited = layer.model_copy(
            update={
                "name": title,
                "visible": visible,
                "locked": locked,
                "players": layer.players if players is None else players,
            }
        )
        self._layers(map_id, [edited if x.id == layer_id else x for x in found.layers])
        return edited

    def lock_layer(self, map_id: str, layer_id: str, locked: bool) -> MapLayer:
        """Lock a layer against moving what is on it, or unlock it."""
        found = self.with_layers(map_id)
        layer = self._layer(found, layer_id)
        return self.edit_layer(
            map_id, layer_id, name=layer.name, visible=layer.visible, locked=locked
        )

    def set_layer_zoom(self, map_id: str, layer_id: str, zoom: float | None) -> MapLayer:
        """Show a layer only from ``zoom`` in, or at every zoom (None)."""
        found = self.with_layers(map_id)
        layer = self._layer(found, layer_id)
        if zoom is not None and not math.isfinite(zoom):
            raise MapError("atlas.error.view")
        edited = layer.model_copy(
            update={"from_zoom": None if zoom is None else round(min(max(zoom, -8), 24), 2)}
        )
        self._layers(map_id, [edited if x.id == layer_id else x for x in found.layers])
        return edited

    def move_layer(self, map_id: str, layer_id: str, step: int) -> None:
        """Move a layer one up (drawn later, on top) or one down."""
        found = self.with_layers(map_id)
        layers = list(found.layers)
        index = next(i for i, x in enumerate(layers) if x.id == self._layer(found, layer_id).id)
        other = index + (1 if step > 0 else -1)
        if 0 <= other < len(layers):
            layers[index], layers[other] = layers[other], layers[index]
            self._layers(map_id, layers)

    def delete_layer(self, map_id: str, layer_id: str, move_to: str = "") -> None:
        """Delete a layer; what is on it moves to ``move_to``, or goes with it. The last
        layer stays."""
        found = self.with_layers(map_id)
        self._layer(found, layer_id)
        if len(found.layers) < 2:
            raise MapError("atlas.error.last_layer")
        if move_to:
            if move_to == layer_id:
                raise MapError("atlas.error.layer")
            self._layer(found, move_to)
            pins = [
                p.model_copy(update={"layer": move_to}) if p.layer == layer_id else p
                for p in found.pins
            ]
            labels = [
                x.model_copy(update={"layer": move_to}) if x.layer == layer_id else x
                for x in found.labels
            ]
            routes = [
                r.model_copy(update={"layer": move_to}) if r.layer == layer_id else r
                for r in found.routes
            ]
        else:
            pins = [p for p in found.pins if p.layer != layer_id]
            labels = [x for x in found.labels if x.layer != layer_id]
            routes = [r for r in found.routes if r.layer != layer_id]
        layers = [x for x in found.layers if x.id != layer_id]
        self._store.maps.update(map_id, layers=layers, pins=pins, labels=labels, routes=routes)

    def shown(self, user_id: str, found: WorldMap) -> dict[str, bool]:
        """Whether the user sees each layer: as they chose, else the layer's default;
        and the hex grid, under ``GRID``, shown unless they hid it."""
        place = self._store.places.find(user_id)
        chosen = place.map_layers.get(found.id, {}) if place is not None else {}
        out = {layer.id: chosen.get(layer.id, layer.visible) for layer in found.layers}
        out[GRID] = chosen.get(GRID, True)
        return out

    def set_shown(self, user_id: str, map_id: str, layer_id: str, shown: bool) -> None:
        """Remember that the user showed or hid a layer of the map, or its grid."""
        found = self.with_layers(map_id)
        if layer_id != GRID:
            self._layer(found, layer_id)
        place = self._store.places.find(user_id) or Place(id=user_id)
        mine = {**place.map_layers.get(map_id, {}), layer_id: shown}
        self._store.places.upsert(
            place.model_copy(update={"map_layers": {**place.map_layers, map_id: mine}})
        )

    def _label(self, found: WorldMap, label_id: str) -> MapLabel:
        found_label = next((x for x in found.labels if x.id == label_id), None)
        if found_label is None:
            raise MapError("atlas.error.label_gone")
        return found_label

    @staticmethod
    def _label_fields(text: str, style: str, size: float, angle: float) -> dict[str, object]:
        words = " ".join(text.split())[:80]
        if not words:
            raise MapError("atlas.error.label")
        if style not in LABEL_STYLES:
            raise MapError("atlas.error.label")
        if not (math.isfinite(size) and size > 0 and math.isfinite(angle)):
            raise MapError("atlas.error.label")
        turned = ((angle + 180) % 360) - 180
        return {"text": words, "style": style, "size": round(size, 1), "angle": round(turned, 1)}

    def add_label(
        self,
        map_id: str,
        *,
        x: float,
        y: float,
        text: str,
        style: str = "settlement",
        size: float,
        angle: float = 0,
        layer: str = "",
    ) -> MapLabel:
        """Write a name on the map, on ``layer`` (else *Names*)."""
        found = self.with_layers(map_id)
        px, py = self._clamped(found, x, y)
        on = self._open_layer(found, layer, "names")
        fields = self._label_fields(
            text, style, min(size, float(max(found.width, found.height))), angle
        )
        made = MapLabel.model_validate({"layer": on.id, "x": px, "y": py, **fields})
        self._store.maps.update(map_id, labels=[*found.labels, made])
        return made

    def edit_label(
        self,
        map_id: str,
        label_id: str,
        *,
        text: str,
        style: str,
        size: float,
        angle: float,
        layer: str = "",
    ) -> MapLabel:
        """Change a name's words, look, size, turn or layer."""
        found = self.with_layers(map_id)
        current = self._label(found, label_id)
        fields = self._label_fields(
            text, style, min(size, float(max(found.width, found.height))), angle
        )
        fields["layer"] = self._layer(found, layer).id if layer else current.layer
        edited = current.model_copy(update=fields)
        self._store.maps.update(
            map_id, labels=[edited if x.id == label_id else x for x in found.labels]
        )
        return edited

    def move_label(self, map_id: str, label_id: str, x: float, y: float) -> MapLabel:
        """Move a name to another spot."""
        found = self.with_layers(map_id)
        current = self._label(found, label_id)
        self._unlocked(found, current.layer)
        px, py = self._clamped(found, x, y)
        moved = current.model_copy(update={"x": px, "y": py})
        self._store.maps.update(
            map_id, labels=[moved if x.id == label_id else x for x in found.labels]
        )
        return moved

    def remove_label(self, map_id: str, label_id: str) -> None:
        """Take a name off the map."""
        found = self.with_layers(map_id)
        self._label(found, label_id)
        self._store.maps.update(map_id, labels=[x for x in found.labels if x.id != label_id])

    # -- roads (documentation/MAPS.md §8) -----------------------------------------------

    def _route(self, found: WorldMap, route_id: str) -> MapRoute:
        route = next((r for r in found.routes if r.id == route_id), None)
        if route is None:
            raise MapError("atlas.error.route_gone")
        return route

    def _put_route(self, map_id: str, found: WorldMap, route: MapRoute) -> MapRoute:
        self._store.maps.update(
            map_id, routes=[route if r.id == route.id else r for r in found.routes]
        )
        return route

    def add_route(
        self,
        map_id: str,
        *,
        name: str,
        kind: str,
        points: Sequence[Sequence[float]],
        terrains: Sequence[str] = (),
        difficult: Sequence[bool] = (),
        layer: str = "",
    ) -> MapRoute:
        """A road, trail, river or sea route drawn on the map: its points, and each
        segment's terrain, made into stretches; on ``layer`` (else *Roads*)."""
        found = self.with_layers(map_id)
        if kind not in ROUTE_KINDS:
            raise MapError("atlas.error.route")
        drawn = [list(self._clamped(found, float(x), float(y))) for x, y in points]
        if len(drawn) < 2:
            raise MapError("atlas.error.route_points")
        on = self._open_layer(found, layer, "roads")
        segments = len(drawn) - 1
        route = MapRoute(
            layer=on.id,
            name=" ".join(name.split())[:80],
            kind=kind,  # checked against ROUTE_KINDS
            points=drawn,
            stretches=stretches_of(
                [*terrains, *[""] * segments][:segments], [*difficult, *[False] * segments]
            ),
        )
        self._store.maps.update(map_id, routes=[*found.routes, route])
        return route

    def edit_route(
        self,
        map_id: str,
        route_id: str,
        *,
        name: str,
        kind: str,
        layer: str = "",
        stretches: Sequence[tuple[str, bool]] = (),
    ) -> MapRoute:
        """A route's name, kind and layer, and each stretch's terrain and difficulty."""
        found = self.with_layers(map_id)
        route = self._route(found, route_id)
        if kind not in ROUTE_KINDS:
            raise MapError("atlas.error.route")
        changed = list(route.stretches)
        for index, (terrain, hard) in enumerate(stretches[: len(changed)]):
            changed[index] = changed[index].model_copy(
                update={"terrain": terrain if terrain in TERRAINS else "", "difficult": hard}
            )
        edited = route.model_copy(
            update={
                "name": " ".join(name.split())[:80],
                "kind": kind,
                "layer": self._layer(found, layer).id if layer else route.layer,
                "stretches": _merged(changed),
            }
        )
        return self._put_route(map_id, found, edited)

    def move_route_point(
        self, map_id: str, route_id: str, index: int, x: float, y: float
    ) -> MapRoute:
        """Move one of a route's points."""
        found = self.with_layers(map_id)
        route = self._route(found, route_id)
        self._unlocked(found, route.layer)
        if not 0 <= index < len(route.points):
            raise MapError("atlas.error.route_points")
        points = [list(p) for p in route.points]
        points[index] = list(self._clamped(found, x, y))
        return self._put_route(map_id, found, route.model_copy(update={"points": points}))

    def split_route(self, map_id: str, route_id: str, index: int) -> MapRoute:
        """Begin a new stretch at a point, through the same terrain until changed."""
        found = self.with_layers(map_id)
        route = self._route(found, route_id)
        if not 0 < index < len(route.points) - 1:
            raise MapError("atlas.error.route_points")
        if any(st.start == index for st in route.stretches):
            return route
        current = [st for st in route.stretches if st.start < index][-1]
        added = current.model_copy(update={"start": index})
        stretches = sorted([*route.stretches, added], key=lambda st: st.start)
        return self._put_route(map_id, found, route.model_copy(update={"stretches": stretches}))

    def remove_route_point(self, map_id: str, route_id: str, index: int) -> MapRoute:
        """Take a point out of a route; a route keeps two."""
        found = self.with_layers(map_id)
        route = self._route(found, route_id)
        if not 0 <= index < len(route.points) or len(route.points) <= 2:
            raise MapError("atlas.error.route_points")
        points = [list(p) for i, p in enumerate(route.points) if i != index]
        stretches: list[RouteStretch] = []
        for st in route.stretches:
            start = st.start - 1 if st.start > index else st.start
            if start < len(points) - 1 and not any(x.start == start for x in stretches):
                stretches.append(st.model_copy(update={"start": start}))
        if not stretches or stretches[0].start != 0:
            first = route.stretches[0].model_copy(update={"start": 0})
            stretches = [first, *[x for x in stretches if x.start != 0]]
        edited = route.model_copy(update={"points": points, "stretches": _merged(stretches)})
        return self._put_route(map_id, found, edited)

    def remove_route(self, map_id: str, route_id: str) -> None:
        """Take a route off the map."""
        found = self.with_layers(map_id)
        self._route(found, route_id)
        self._store.maps.update(map_id, routes=[r for r in found.routes if r.id != route_id])

    @staticmethod
    def segment_miles(found: WorldMap, route: MapRoute) -> list[float] | None:
        """Each segment's length in miles, by the map's scale; None without one."""
        if found.scale is None:
            return None
        per = found.scale.feet_per_px / FEET_PER_MILE
        return [
            math.hypot(b[0] - a[0], b[1] - a[1]) * per
            for a, b in zip(route.points, route.points[1:], strict=False)
        ]

    def stretch_miles(self, found: WorldMap, route: MapRoute) -> list[float] | None:
        """Each stretch's length in miles; None without a scale."""
        segments = self.segment_miles(found, route)
        if segments is None:
            return None
        bounds = [st.start for st in route.stretches] + [len(segments)]
        return [sum(segments[a:b]) for a, b in zip(bounds, bounds[1:], strict=False)]

    def stops(self, found: WorldMap, route: MapRoute) -> list[Stop]:
        """Where a journey along the route may start or end: each of its points, named
        by a pin standing on it (within a hundred and fiftieth of the map)."""
        segments = self.segment_miles(found, route) or [0.0] * (len(route.points) - 1)
        near = max(found.width, found.height) / 150
        out = []
        at = 0.0
        for index, (x, y) in enumerate(route.points):
            if index:
                at += segments[index - 1]
            pin = min(
                (p for p in found.pins if math.hypot(p.x - x, p.y - y) <= near),
                key=lambda p: math.hypot(p.x - x, p.y - y),
                default=None,
            )
            out.append(Stop(round(at, 3), index, (pin.label or pin.name) if pin else ""))
        return out

    def legs(
        self, found: WorldMap, route: MapRoute, start: float, end: float
    ) -> list[tuple[float, str, bool]]:
        """The way from ``start`` to ``end`` (miles along the route, either way round)
        as ``(miles, terrain, difficult)`` in the order walked, a stretch of terrain
        each."""
        segments = self.segment_miles(found, route)
        if segments is None:
            raise MapError("atlas.ruler.needs_scale")
        total = sum(segments)
        a, b = sorted((min(max(start, 0.0), total), min(max(end, 0.0), total)))
        if b - a < 1e-6:
            raise MapError("atlas.error.route_same")
        terrain_of = {}
        for st in route.stretches:
            terrain_of[st.start] = (st.terrain, st.difficult)
        current = ("", False)
        pieces: list[tuple[float, str, bool]] = []
        at = 0.0
        for index, miles in enumerate(segments):
            current = terrain_of.get(index, current)
            lo, hi = max(a, at), min(b, at + miles)
            if hi > lo:
                pieces.append((hi - lo, *current))
            at += miles
        if end < start:
            pieces.reverse()
        joined: list[tuple[float, str, bool]] = []
        for miles, terrain, hard in pieces:
            if joined and joined[-1][1:] == (terrain, hard):
                joined[-1] = (joined[-1][0] + miles, terrain, hard)
            else:
                joined.append((miles, terrain, hard))
        return joined

    def position(self, found: WorldMap, route: MapRoute, at: float) -> tuple[float, float]:
        """The spot ``at`` miles along the route, in the picture's pixels."""
        segments = self.segment_miles(found, route) or []
        walked = 0.0
        for index, miles in enumerate(segments):
            if walked + miles >= at and miles > 0:
                part = (at - walked) / miles
                (x1, y1), (x2, y2) = route.points[index], route.points[index + 1]
                return (x1 + (x2 - x1) * part, y1 + (y2 - y1) * part)
            walked += miles
        x, y = route.points[-1]
        return (x, y)

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
    "GRID",
    "PLAYER_ROLES",
    "LONG_UNITS",
    "UNITS",
    "LABEL_STYLES",
    "LAYER_NAMES",
    "LAYER_ROLES",
    "PIN_KINDS",
    "ROUTE_KINDS",
    "Stop",
    "MapError",
    "MapService",
    "default_layers",
    "feet_per_px",
]
