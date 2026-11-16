"""The role-specific landing screens and the root redirect between them."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import CharactersDep, MaybeIdentityDep, PageDep, PlayerDep, RegistryDep
from app.models.pc import PlayerCharacter
from app.routes.auth import html, resume
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.home_pages import player_home
from app.ui.play_combat_pages import fights_list

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def root(identity: MaybeIdentityDep, registry: RegistryDep) -> Response:
    """Send each visitor where they belong: the page they left (the installed app
    starts here), their role's screen, or the login."""
    if identity is None:
        return RedirectResponse("/login", status_code=303)
    return resume(registry, identity.user)


@router.get("/play", response_class=HTMLResponse)
async def play(
    identity: PlayerDep, page: PageDep, characters: CharactersDep, registry: RegistryDep
) -> HTMLResponse:
    """The player's screen: their characters, and the fights they are in now."""
    mine = characters.for_player(identity)
    combats = registry.combats
    views = [combats.player_view(c) for c in combats.joined_by(pc.id for pc in mine)]
    ids = frozenset(pc.id for pc in mine)
    return html(player_home(page, mine, fights_list(page, views, ids), _news(page, registry, mine)))


def _news(
    page: Page, registry: StoreRegistry, mine: list[PlayerCharacter]
) -> list[tuple[str, str, str]]:
    """What is going on in the player's worlds: a session running, a chase one of their
    characters runs in (its turn named), quests open to them."""
    t = page.t
    out: list[tuple[str, str, str]] = []
    worlds = list(dict.fromkeys(pc.world_id for pc in mine if pc.world_id))
    for world_id in worlds:
        world = registry.gamestate.worlds.find(world_id)
        if world is None:
            continue
        if registry.play_sessions.of(world_id) is not None:
            out.append(("play", t("play.news_session", world=world.name), "/journal"))
        chase = registry.chases.of(world_id)
        for pc in mine:
            if pc.world_id != world_id or chase is None or chase.over:
                continue
            index = next((n for n, r in enumerate(chase.runners) if r.character_id == pc.id), None)
            if index is None:
                continue
            key = "play.news_chase_turn" if chase.turn == index else "play.news_chase"
            out.append(("footprints", t(key, name=pc.name or "?"), f"/characters/{pc.id}"))
        quests = registry.quests.shown_to(world_id, [pc.id for pc in mine])
        open_count = sum(1 for q in quests if q.status == "open")
        if open_count:
            out.append(
                ("scroll", t.plural("play.news_quests", open_count, world=world.name), "/journal")
            )
    return out


__all__ = ["router"]
