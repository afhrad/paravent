"""A carried item's mind and artifact properties (app/services/sentient.py), the
gamemaster's alone.

* ``GET  /characters/{id}/mind?index=…&ref=…`` — the item's page
* ``POST /characters/{id}/mind``                — roll a mind (``awaken``), save or reroll a
  part (``save``, ``reroll``), take it away (``forget``), a conflict with the bearer
  (``conflict``), the item taking control (``control``), roll, add or drop an artifact
  property (``roll_property``, ``add_property``, ``drop_property``)
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.routing import APIRouter

from app.dependencies import CharactersDep, GamemasterDep, PageDep, RegistryDep
from app.models.pc import ArtifactProperty, ItemMind, PlayerCharacter
from app.routes.auth import html, redirect
from app.services.dice import Dice
from app.services.play import Actor, PlayError
from app.services.sentient import KINDS, PARTS, SCORES, reroll, roll_mind, roll_part, room
from app.services.sheet import ItemView
from app.ui.names import name_text
from app.ui.sentient_pages import mind_page, mind_url

router = APIRouter(tags=["characters"])


def _item(registry: RegistryDep, pc: PlayerCharacter, index: int, ref: str) -> ItemView:
    """The carried item the page was drawn for, or 404."""
    sheet = registry.characters.sheet(pc)
    item = next((i for i in sheet.inventory if i.index == index), None)
    if item is None or (item.true_name or item.name).casefold() != ref.casefold():
        raise HTTPException(status_code=404)
    return item


def _name(page: PageDep, item: ItemView) -> str:
    return str(name_text(page, ("item", "baseitem"), (item.true_name or item.name).split("|")[0]))


@router.get(mind_url("{character_id}"), response_class=HTMLResponse)
async def mind_form(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
    index: int,
    ref: str,
    error: str = "",
) -> HTMLResponse:
    """The item's mind, the conflict with its bearer, its artifact properties."""
    pc = characters.get_for(gm, character_id)
    item = _item(registry, pc, index, ref)
    query = request.query_params
    result = {k: query[k] for k in ("what", "item", "bearer", "dc", "won") if k in query}
    return html(
        mind_page(
            page,
            pc,
            item,
            _name(page, item),
            registry.sentient_guide(pc.world_id),
            result=result or None,
            error=page.t(error) if error else None,
        )
    )


@router.post(mind_url("{character_id}"))
async def mind_change(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
) -> Response:
    """Whatever the page's buttons ask, then back to it."""
    form = await request.form()
    field = {k: str(v) for k, v in form.items() if isinstance(v, str)}
    op = field.get("op", "")
    try:
        index = int(field.get("index", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400) from exc
    ref = field.get("ref", "")
    pc = characters.get_for(gm, character_id)
    item = _item(registry, pc, index, ref)
    guide = registry.sentient_guide(pc.world_id)
    dice = Dice()
    by = Actor(gm.user.display_name or gm.user.username, username=gm.user.username)
    mind = item.mind
    artifact = list(item.artifact)
    back = {"index": str(index), "ref": ref}
    if op in ("conflict", "control"):
        if mind is None or pc.world_id is None:
            raise HTTPException(status_code=400)
        name = _name(page, item)
        who = pc.name or "?"
        if op == "conflict":
            done = registry.sentient.conflict(
                pc.world_id, pc.id, who, name, mind, guide.contested, by.username, by.name
            )
            back |= {
                "what": "conflict",
                "item": "" if done.item is None else str(done.item),
                "bearer": str(done.bearer),
                "dc": "" if done.dc is None else str(done.dc),
                "won": "1" if done.item_wins else "0",
            }
        else:
            outcome = registry.sentient.control(
                pc.world_id, pc.id, who, name, mind, by.username, by.name
            )
            back |= {
                "what": "control",
                "bearer": str(outcome.roll or 0),
                "dc": str(outcome.dc or 0),
                "won": "1" if outcome.caught else "0",
            }
        return redirect(f"{mind_url(character_id)}?{urlencode(back)}")
    if op == "awaken":
        mind = roll_mind(guide, dice)
    elif op == "save" and mind is not None:
        mind = _typed(mind, field)
        if (part := field.get("reroll", "")) in (*SCORES, *PARTS):
            mind = reroll(mind, part, guide, dice)
    elif op == "forget":
        mind = None
    elif op == "roll_property" and (kind := field.get("kind", "")) in KINDS:
        if room(artifact, kind) and (text := roll_part(guide, kind, dice)):
            artifact.append(ArtifactProperty(kind=kind, text=text))
    elif op == "add_property" and (kind := field.get("kind", "")) in KINDS:
        text = " ".join(field.get("text", "").split())[:2000]
        if text:
            artifact.append(ArtifactProperty(kind=kind, text=text))
    elif op == "drop_property":
        n = field.get("n", "")
        if n.isdigit() and int(n) < len(artifact):
            del artifact[int(n)]
    else:
        raise HTTPException(status_code=400)
    try:
        characters.act_for(
            character_id,
            "item_mind",
            {
                "index": str(index),
                "ref": ref,
                "mind": mind.model_dump_json() if mind is not None else "",
                "artifact": json.dumps([x.model_dump() for x in artifact]),
            },
            by,
        )
    except PlayError as exc:
        back["error"] = exc.key
    return redirect(f"{mind_url(character_id)}?{urlencode(back)}")


def _typed(mind: ItemMind, field: dict[str, str]) -> ItemMind:
    """The mind as the form wrote it; a score that is no number keeps its value."""
    update: dict[str, object] = {}
    for name in SCORES:
        value = field.get(name, "").strip()
        if value.isdigit():
            update[name] = max(1, min(30, int(value)))
    for name in (*PARTS, "languages", "notes"):
        if name in field:
            limit = {"languages": 200, "notes": 2000}.get(name, 1000)
            update[name] = field[name].strip()[:limit]
    return mind.model_copy(update=update)


__all__ = ["router"]
