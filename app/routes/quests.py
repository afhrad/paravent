"""A world's quests (app/services/quests.py), for the gamemaster.

* ``GET  /gm/quests?world=…``         — the quests, open first, and a new one
* ``POST /gm/quests/{world_id}/{op}`` — ``save``, ``step``, ``finish``, ``fail``,
  ``reopen``, ``delete``
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from app.dependencies import GamemasterDep, PageDep, RegistryDep, WorldsDep
from app.models.gamestate import QuestItem, QuestStep, Scope, World
from app.routes.auth import html, redirect
from app.routes.map_links import on_maps
from app.routes.paravent_markdown import paravent_markdown
from app.services.factions import FactionError
from app.services.play import Actor
from app.services.quests import Handout, QuestError
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.quest_pages import QUESTS_URL, quests_page, quests_url

router = APIRouter(tags=["quests"])

# "2 Potion of Healing", "Bag of Holding|DMG", "3× Arrow"
_ITEM_LINE = re.compile(r"^\s*(?:(\d+)\s*[×x]?\s+)?(.+?)\s*$")


def _members(registry: StoreRegistry, world_id: str) -> list[tuple[str, str]]:
    found = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.of_world(world_id, drafts=False)
    ]
    return sorted(found, key=lambda m: m[1].lower())


def _page(
    page: Page, registry: StoreRegistry, world: World, error: str | None = None
) -> HTMLResponse:
    quests = registry.quests.of(world.id)
    return html(
        quests_page(
            page,
            world,
            quests,
            _members(registry, world.id),
            [(f.id, f.name) for f in registry.factions.of(world.id)],
            {q.id: registry.quests.party(q) for q in quests},
            milestone=world.milestone,
            error=error,
            on_maps={q.id: on_maps(registry, "quest", q.id, world.id) for q in quests},
            render=lambda text: (
                paravent_markdown(page, registry, Scope(world_id=world.id), text).html
            ),
        ),
        status_code=200 if error is None else 422,
    )


def _items(registry: StoreRegistry, world: World, text: str) -> list[QuestItem]:
    """The reward's items, one a line, each found in the world's books."""
    out = []
    resolver = registry.references.resolver()
    for line in text.splitlines()[:20]:
        found = _ITEM_LINE.match(line)
        if found is None or not found[2].strip():
            continue
        name, _, source = found[2].partition("|")
        entry = resolver.resolve_parts(
            "item", name.strip(), source.strip() or None, world.source_set
        ).entry
        if entry is None:
            raise QuestError("quests.error.item", name=name.strip())
        count = int(found[1]) if found[1] else 1
        out.append(QuestItem(ref=f"{entry.name}|{entry.source}", count=max(1, min(999, count))))
    return out


def _steps(text: str) -> list[QuestStep]:
    """The steps, one a line; ``[x]`` before one marks it done."""
    out = []
    for line in text.splitlines():
        line = line.strip().removeprefix("-").strip()
        done = line.lower().startswith("[x]")
        line = line[3:].strip() if done or line.startswith("[ ]") else line
        if line:
            out.append(QuestStep(text=line[:500], done=done))
    return out


def _values(registry: StoreRegistry, world: World, form: FormData) -> dict[str, object]:
    gold = str(form.get("gold", "")).strip()
    return {
        "name": str(form.get("name", "")),
        "giver": str(form.get("giver", "")),
        "faction_id": str(form.get("faction_id", "")),
        "text": str(form.get("text", "")),
        "steps": _steps(str(form.get("steps", ""))),
        "characters": [str(c) for c in form.getlist("character")],
        "shown": bool(form.get("shown")),
        "xp": str(form.get("xp", "")),
        "coins": str(int(gold) * 100) if gold.isdigit() else "",
        "items": _items(registry, world, str(form.get("items", ""))),
        "renown": str(form.get("renown", "")),
        "mark_kind": str(form.get("mark_kind", "")),
        "mark_name": str(form.get("mark_name", "")),
    }


@router.get(QUESTS_URL, response_class=HTMLResponse)
async def show_quests(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, registry: RegistryDep, world: str = ""
) -> Response:
    """A world's quests."""
    chosen = worlds.find_or_current(world)
    if chosen is None:
        return redirect("/gm/worlds")
    return _page(page, registry, chosen)


@router.post(f"{QUESTS_URL}/{{world_id}}/{{op}}")
async def change_quests(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world_id: str,
    op: str,
) -> Response:
    """Write a quest, tick a step, finish, fail, reopen or delete one."""
    world = worlds.find(world_id)
    if world is None:
        return redirect("/gm/worlds")
    form = await request.form()
    quests = registry.quests
    quest_id = str(form.get("quest", ""))
    user = gm.user
    name = user.display_name or user.username
    undo = ""  # the trash entry of something deleted, for its Undo
    try:
        if op == "save":
            quests.save(world.id, _values(registry, world, form), quest_id, user.username, name)
        elif op == "step":
            index = str(form.get("index", ""))
            quests.step(quest_id, world.id, int(index) if index.isdigit() else -1)
        elif op == "finish":
            by = Actor(name, username=user.username)
            characters = registry.characters

            def renown(faction_id: str, character_id: str, delta: int) -> None:
                pc = registry.gamestate.characters.find(character_id)
                if pc is not None:
                    registry.factions.adjust(faction_id, world.id, pc, delta, user.username, name)

            def experience(character_id: str, xp: int, why: str) -> None:
                if not world.milestone:  # no XP where the world levels by milestone
                    characters.award_xp(character_id, xp, why, name)

            def receive(
                character_id: str, items: list[tuple[str, int]], coins: dict[str, int]
            ) -> None:
                characters.receive(character_id, items, coins, by)

            def mark(character_id: str, kind: str, mark_name: str, giver: str) -> None:
                pc = registry.gamestate.characters.find(character_id)
                if pc is not None:
                    registry.prestige.grant(
                        pc, kind, mark_name, giver, by=user.username, by_name=name
                    )

            handout = Handout(xp=experience, receive=receive, renown=renown, mark=mark)
            given = {
                int(key[5:]): str(value)
                for key, value in form.items()
                if key.startswith("give_") and key[5:].isdigit()
            }
            quests.finish(quest_id, world.id, handout, given=given, by=user.username, by_name=name)
        elif op == "fail":
            quests.fail(quest_id, world.id, user.username, name)
        elif op == "reopen":
            quests.reopen(quest_id, world.id)
        elif op == "delete":
            undo = quests.delete(quest_id, world.id) or ""
        else:
            raise QuestError("quests.error.unknown")
    except (QuestError, FactionError) as exc:
        return _page(page, registry, world, page.t.error(exc))
    # After a delete, the page offers Undo (app/ui/world_menu.py, undo_notice).
    return redirect(quests_url(world.id) + (f"&undo={undo}" if undo else ""))


__all__ = ["router"]
