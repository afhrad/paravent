"""FastAPI dependencies.

Routes reach the stores only through these, never through a module-level global —
CLAUDE.md rules out global mutable state, and injection is what makes the stores
replaceable in tests.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.models.gamestate import Role
from app.services.auth import COOKIE_NAME, AuthService, Identity
from app.services.characters import CharacterService
from app.services.i18n import LANG_COOKIE
from app.services.players import PlayerService
from app.services.worlds import WorldService
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.nav import NAV_COOKIE, read_memory


def get_registry(request: Request) -> StoreRegistry:
    """The store registry attached to the app at startup."""
    registry: StoreRegistry = request.app.state.stores
    return registry


def get_compendium(
    registry: Annotated[StoreRegistry, Depends(get_registry)],
) -> CompendiumStore:
    """The in-memory compendium."""
    return registry.compendium


def get_gamestate(
    registry: Annotated[StoreRegistry, Depends(get_registry)],
) -> GameStateStore:
    """The persistent game-state store."""
    return registry.gamestate


def get_auth(registry: Annotated[StoreRegistry, Depends(get_registry)]) -> AuthService:
    """The login and session service."""
    return registry.auth


class LoginRequired(Exception):
    """Raised by a page dependency when nobody is logged in.

    Handled in ``main`` by redirecting to the login page with ``next`` set, so a page
    route never has to know how logging in works.
    """

    def __init__(self, next_url: str) -> None:
        super().__init__(next_url)
        self.next_url = next_url


class RoleForbidden(Exception):
    """Raised when a logged-in user's role may not use a route."""

    def __init__(self, identity: Identity, required: Role) -> None:
        super().__init__(required.value)
        self.identity = identity
        self.required = required


def get_identity(
    request: Request, auth: Annotated[AuthService, Depends(get_auth)]
) -> Identity | None:
    """Who is logged in, or None. Never raises."""
    return auth.identify(request.cookies.get(COOKIE_NAME))


def current_url(request: Request) -> str:
    """The request's path and query, for coming back to it after a redirect."""
    path = request.url.path
    return f"{path}?{request.url.query}" if request.url.query else path


def build_page(request: Request, identity: Identity | None = None) -> Page:
    """The rendering context for ``request``.

    Language preference, strongest first: the logged-in user's stored choice, the
    language cookie (what someone picked before logging in), the browser's
    ``Accept-Language``, then English. Usable outside dependency injection, which is
    what exception handlers need.
    """
    registry: StoreRegistry = request.app.state.stores
    if identity is None:
        identity = registry.auth.identify(request.cookies.get(COOKIE_NAME))
    code = registry.ui.negotiate(
        identity.user.language if identity is not None else None,
        request.cookies.get(LANG_COOKIE),
        accept_language=request.headers.get("accept-language"),
    )
    as_player = (
        identity is not None
        and identity.is_gamemaster
        and request.query_params.get("as") == "player"
    )
    return Page(
        reader=registry.player_reader(identity, as_player=as_player)
        if identity is not None
        else None,
        as_player=as_player,
        t=registry.ui.translator(
            code, registry.glossary, registry.translations, registry.item_names.compose
        ),
        path=current_url(request),
        identity=identity,
        theme=registry.themes.for_user(identity.user if identity is not None else None),
        # Logged in, the memory is the user's own, kept on the server; the cookie is
        # this browser's, for when it has none.
        nav_memory=(registry.places.nav(identity.user.id) if identity is not None else {})
        or read_memory(request.cookies.get(NAV_COOKIE)),
    )


def get_page(request: Request, identity: Annotated[Identity | None, Depends(get_identity)]) -> Page:
    """The rendering context, as a dependency."""
    return build_page(request, identity)


def get_characters(
    registry: Annotated[StoreRegistry, Depends(get_registry)],
) -> CharacterService:
    """Characters as the screens use them."""
    return registry.characters


def get_worlds(registry: Annotated[StoreRegistry, Depends(get_registry)]) -> WorldService:
    """Worlds and their declared books."""
    return registry.worlds


def get_players(registry: Annotated[StoreRegistry, Depends(get_registry)]) -> PlayerService:
    """The gamemaster's player-account management."""
    return registry.players


def require_identity(
    request: Request, identity: Annotated[Identity | None, Depends(get_identity)]
) -> Identity:
    """The logged-in user, or a redirect to the login page."""
    if identity is None:
        raise LoginRequired(current_url(request))
    return identity


def require_role(role: Role) -> object:
    """A dependency admitting only ``role``.

    The role gates what a route *does*, not merely what the UI shows
    (documentation/TECHSTACK.md §7): a player who types ``/gm`` gets a 403, not a page
    with the buttons hidden.
    """

    def check(identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
        if identity.role is not role:
            raise RoleForbidden(identity, role)
        return identity

    return Depends(check)


SettingsDep = Annotated[Settings, Depends(get_settings)]
RegistryDep = Annotated[StoreRegistry, Depends(get_registry)]
CompendiumDep = Annotated[CompendiumStore, Depends(get_compendium)]
GameStateDep = Annotated[GameStateStore, Depends(get_gamestate)]
AuthDep = Annotated[AuthService, Depends(get_auth)]
MaybeIdentityDep = Annotated[Identity | None, Depends(get_identity)]
IdentityDep = Annotated[Identity, Depends(require_identity)]
GamemasterDep = Annotated[Identity, require_role(Role.GAMEMASTER)]
PlayerDep = Annotated[Identity, require_role(Role.PLAYER)]
PageDep = Annotated[Page, Depends(get_page)]
PlayersDep = Annotated[PlayerService, Depends(get_players)]
CharactersDep = Annotated[CharacterService, Depends(get_characters)]
WorldsDep = Annotated[WorldService, Depends(get_worlds)]

__all__ = [
    "AuthDep",
    "CharactersDep",
    "CompendiumDep",
    "GamemasterDep",
    "IdentityDep",
    "LoginRequired",
    "MaybeIdentityDep",
    "PageDep",
    "PlayerDep",
    "PlayersDep",
    "RoleForbidden",
    "WorldsDep",
    "GameStateDep",
    "RegistryDep",
    "SettingsDep",
    "build_page",
    "current_url",
    "get_auth",
    "get_characters",
    "get_compendium",
    "get_gamestate",
    "get_identity",
    "get_page",
    "get_players",
    "get_worlds",
    "get_registry",
    "require_identity",
    "require_role",
]
