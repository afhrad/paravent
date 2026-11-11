"""What the Markdown editors do besides saving: create an NPC, encounter or treasure a
chapter names, from the drop-down (static/js/complete.js).

* ``POST /gm/editor/create`` — ``kind``, ``name``, ``scope`` (the editor's record,
  ``c:<chapter id>``, ``a:<adventure id>``, ``w:<world id>``); answers 201 with the new
  record's page in ``Location``
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import Response

from app.dependencies import GamemasterDep, RegistryDep
from app.services import scope as scopes
from app.ui.encounter_pages import encounter_url
from app.ui.library_links import location_url, npc_url, treasure_url

router = APIRouter(tags=["editor"])

# What the drop-down may create in place.
CREATABLE = frozenset({"npc", "encounter", "treasure", "place"})


@router.post("/gm/editor/create", status_code=201)
async def create(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()],
    name: Annotated[str, Form()],
    scope: Annotated[str, Form()],
) -> Response:
    """A new NPC, encounter or treasure of that name where the editor's record is, unless
    one of the name is already there."""
    if kind not in CREATABLE or not name.strip():
        raise HTTPException(status_code=400, detail="kind or name")
    try:
        where = scopes.decode(scope, registry.adventures)
    except scopes.ScopeError as exc:
        raise HTTPException(status_code=404, detail="scope") from exc
    world = where.world_id
    value = scopes.encode(where)
    if kind == "npc":
        found = registry.npcs.find(name, where)
        url = npc_url((found or registry.npcs.create(name, value, world)).id)
    elif kind == "place":
        found_place = registry.locations.find(name, where)
        url = location_url((found_place or registry.locations.create(name, value, world)).id)
    elif kind == "treasure":
        found_treasure = registry.treasure.find(name, where)
        made = found_treasure or registry.treasure.create(name, value, world)
        url = treasure_url(made.id)
    else:
        found_encounter = registry.encounters.find(name, where)
        made_encounter = found_encounter or registry.encounters.create(name, world, where)
        url = encounter_url(made_encounter.id)
    return Response(status_code=201, headers={"Location": url})
