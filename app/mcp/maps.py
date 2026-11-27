"""The MCP server's maps: reading a world's and an adventure's maps, and pinning the
places, people and stories of a draft on the draft's own maps (documentation/MAPS.md
§9c, MCP.md §4).

Claude cannot see a map's picture. What it reads is the map's size, its scale, the
names the gamemaster wrote on it and its pins; a pin goes at a spot in the picture's
pixels or on a name written there. It writes only on a map of an adventure that is a
draft drafted through MCP, as every writing tool; the gamemaster uploads the map.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.mcp.common import current_or, tool_errors
from app.mcp.models import MapInfo, MapLabelInfo, MapPinInfo
from app.models.gamestate import MapPin, WorldMap
from app.services.maps import FEET_PER_MILE, PIN_KINDS, MapError

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry


def register_maps(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add the map tools to ``server``."""

    def writable(found: WorldMap) -> bool:
        ident = found.scope.adventure_id
        if not ident:
            return False
        adventure = stores().gamestate.adventures.find(ident)
        return adventure is not None and adventure.status == "draft" and bool(adventure.drafted_by)

    def the_map(map_id: str) -> WorldMap:
        with tool_errors():
            return stores().world_maps.with_layers(map_id)

    def mine(map_id: str) -> WorldMap:
        found = the_map(map_id)
        if not writable(found):
            raise ToolError(
                f"“{found.name}” is not a map of a draft written through Paravent's MCP "
                "server: it can be read, not changed."
            )
        return found

    def pin_info(found: WorldMap, pin: MapPin) -> MapPinInfo:
        hit = stores().world_maps.target(pin.kind, pin.target) if pin.kind != "note" else None
        layers = {layer.id: layer.name for layer in found.layers}
        return MapPinInfo(
            id=pin.id,
            kind=pin.kind,
            name=pin.label or (hit[0] if hit else pin.name),
            target_id=pin.target,
            x=round(pin.x, 1),
            y=round(pin.y, 1),
            layer=layers.get(pin.layer, ""),
        )

    def map_info(found: WorldMap) -> MapInfo:
        scale = found.scale
        return MapInfo(
            id=found.id,
            name=found.name,
            adventure_id=found.scope.adventure_id,
            width=found.width,
            height=found.height,
            miles_per_px=round(scale.feet_per_px / FEET_PER_MILE, 6) if scale else None,
            writable=writable(found),
            layers=[layer.name for layer in found.layers],
            labels=[
                MapLabelInfo(text=x.text, style=x.style, x=round(x.x, 1), y=round(x.y, 1))
                for x in found.labels
            ],
            pins=[pin_info(found, p) for p in found.pins],
            roads=[r.name for r in found.routes if r.name],
        )

    @server.tool()
    def list_maps(
        world_id: Annotated[
            str, Field(description="The world whose own maps to list; empty for the current")
        ] = "",
        adventure_id: Annotated[
            str | None, Field(description="List this adventure's maps instead")
        ] = None,
    ) -> list[MapInfo]:
        """The gamemaster's maps of a world, or of one adventure: each map's size in
        pixels, its scale, the names written on it (where its towns, rivers and regions
        are) and its pins. Pin on the maps of your own draft with `pin_on_map`."""
        registry = stores()
        with tool_errors():
            if adventure_id:
                registry.adventures.get(adventure_id)
                found = registry.world_maps.of_adventure(adventure_id)
            else:
                world = registry.worlds.get(current_or(registry, world_id))
                found = registry.world_maps.of_world(world.id)
        return [map_info(registry.world_maps.with_layers(m.id)) for m in found]

    @server.tool()
    def pin_on_map(
        map_id: str,
        kind: Annotated[
            str,
            Field(
                description="place, npc, encounter, treasure, adventure, chapter, map, "
                "character, quest, faction, handout, session, reference (an entry of the "
                "world's books, its target_id `category|name|source`) or note"
            ),
        ],
        name: Annotated[
            str,
            Field(
                description="What to pin, by its name: a place, NPC, encounter or treasure "
                "of the draft or the world, a chapter, another map, a character, a quest, "
                "a faction. For a note, its title"
            ),
        ] = "",
        target_id: Annotated[str, Field(description="Or what to pin, by its id")] = "",
        at_label: Annotated[
            str,
            Field(
                description="Pin it on a name the gamemaster wrote on the map (see "
                "`list_maps`), such as the town's own name"
            ),
        ] = "",
        x: Annotated[float | None, Field(description="Or where: pixels from the left")] = None,
        y: Annotated[float | None, Field(description="and pixels from the top")] = None,
        note: Annotated[str, Field(description="A note's text, Paravent Markdown")] = "",
    ) -> MapPinInfo:
        """Pin something of your draft on one of the draft's maps: on a name written on
        the map, or at a spot in its pixels. A place pinned shows on the map and the
        place's page names the map. Notes are for what deserves no record of its own."""
        registry = stores()
        found = mine(map_id)
        if kind not in PIN_KINDS:
            raise ToolError(f"No pin of kind “{kind}”: one of {', '.join(PIN_KINDS)}.")
        service = registry.world_maps
        if at_label:
            wanted = at_label.strip().casefold()
            label = next((lab for lab in found.labels if lab.text.casefold() == wanted), None)
            if label is None:
                written = ", ".join(sorted({lab.text for lab in found.labels})) or "none"
                raise ToolError(f"No name “{at_label}” is written on the map. Written: {written}.")
            x, y = label.x, label.y
        if x is None or y is None:
            raise ToolError("Say where: `at_label`, or `x` and `y` in the map's pixels.")
        target = target_id
        if kind != "note" and not target:
            target = find(found, kind, name)
        with tool_errors():
            pin = service.add_pin(
                found.id,
                x=x,
                y=y,
                kind=kind,
                target=target,
                label=name if kind == "note" else "",
                note=note if kind == "note" else "",
            )
        return pin_info(service.get(found.id), pin)

    def find(found: WorldMap, kind: str, name: str) -> str:
        """The id of what ``name`` names that the map may show, or a refusal naming
        what it may show of that kind."""
        registry = stores()
        world_id = found.scope.world_id
        wanted = name.strip().casefold()
        if kind == "place":
            candidates = [(x.id, x.name) for x in registry.locations.list(world_id)]
        elif kind == "npc":
            candidates = [(x.id, x.name) for x in registry.npcs.list(world_id)]
        elif kind == "encounter":
            candidates = [(x.id, x.name) for x in registry.encounters.list(world_id)]
        elif kind == "treasure":
            candidates = [(x.id, x.name) for x in registry.treasure.list(world_id)]
        elif kind == "adventure":
            candidates = [(x.id, x.name) for x in registry.adventures.adventures(world_id)]
        elif kind == "chapter":
            ident = found.scope.adventure_id or ""
            candidates = [(x.id, x.title) for x in registry.adventures.chapters(ident)]
        elif kind == "map":
            candidates = [
                (m.id, m.name)
                for m in registry.gamestate.maps.list(lambda m: m.scope.world_id == world_id)
            ]
        elif kind == "character":
            candidates = [(c.id, c.name) for c in registry.gamestate.characters.list() if c.name]
        elif kind == "quest":
            candidates = [
                (q.id, q.name)
                for q in registry.gamestate.quests.list(lambda q: q.world_id == world_id)
            ]
        elif kind == "faction":
            candidates = [
                (f.id, f.name)
                for f in registry.gamestate.factions.list(lambda f: f.world_id == world_id)
            ]
        else:
            raise ToolError(
                f"A {kind} is pinned by its `target_id`"
                + (" (category|name|source of a book's entry)" if kind == "reference" else "")
                + "."
            )
        allowed = [(i, n) for i, n in candidates if registry.world_maps.shows(found, kind, i)]
        hit = next((i for i, n in allowed if n.casefold() == wanted), None)
        if hit is None:
            names = ", ".join(sorted(n for _i, n in allowed)[:40]) or "none"
            raise ToolError(f"No {kind} called “{name}” this map may show. It may show: {names}.")
        return hit

    @server.tool()
    def remove_map_pin(map_id: str, pin_id: str) -> str:
        """Take a pin off a map of your draft."""
        found = mine(map_id)
        try:
            stores().world_maps.remove_pin(found.id, pin_id)
        except MapError as exc:
            raise ToolError(str(exc)) from exc
        return "Removed."
