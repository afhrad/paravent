"""The role-specific landing screens and the root redirect between them."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import CharactersDep, MaybeIdentityDep, PageDep, PlayerDep, RegistryDep
from app.routes.auth import html, resume
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
    return html(player_home(page, mine, fights_list(page, views, ids)))


__all__ = ["router"]
