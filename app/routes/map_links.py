"""Where something lies on the maps, for its page (documentation/MAPS.md §6)."""

from __future__ import annotations

from urllib.parse import urlencode

from app.store.registry import StoreRegistry
from app.ui.map_links import OnMaps
from app.ui.map_pages import map_url


def on_maps(registry: StoreRegistry, kind: str, target: str, world_id: str | None) -> OnMaps:
    """The pins of ``world_id``'s maps pointing at that, and its maps that may take one
    more (opening ready to pin it, ``?pin_for=``); empty without a world."""
    if not world_id:
        return OnMaps()
    service = registry.world_maps
    return OnMaps(
        pinned=[
            (found.name, f"{map_url(found.id)}?{urlencode({'pin': pin.id})}")
            for found, pin in service.pins_to(kind, target)
            if found.scope.world_id == world_id
        ],
        pinnable=[
            (found.name, f"{map_url(found.id)}?{urlencode({'pin_for': f'{kind}:{target}'})}")
            for found in service.pinnable(kind, target, world_id)
        ],
    )
