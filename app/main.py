"""FastAPI entry point.

Startup loads the compendium into memory and starts the JSON watcher; shutdown stops
it. The stores live on ``app.state`` and reach routes through dependency injection.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import MutableHeaders
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import STATIC_DIR, get_settings
from app.dependencies import LoginRequired, RoleForbidden, build_page
from app.logs import configure_logging
from app.mcp.auth import BearerGuard
from app.mcp.server import build_server
from app.routes import adventures as adventure_routes
from app.routes import auth as auth_routes
from app.routes import backups as backup_routes
from app.routes import books as books_routes
from app.routes import cast as cast_routes
from app.routes import characters as character_routes
from app.routes import chargen as chargen_routes
from app.routes import combat as combat_routes
from app.routes import compendium as compendium_routes
from app.routes import corpus as corpus_routes
from app.routes import creatures as creature_routes
from app.routes import editor as editor_routes
from app.routes import encounters as encounter_routes
from app.routes import glossary as glossary_routes
from app.routes import gm as gm_routes
from app.routes import help as help_routes
from app.routes import home as home_routes
from app.routes import journal as journal_routes
from app.routes import levelup as levelup_routes
from app.routes import library_preview as library_preview_routes
from app.routes import locations as location_routes
from app.routes import mcp_tokens as mcp_routes
from app.routes import media as media_routes
from app.routes import naming as naming_routes
from app.routes import npc_gen as npc_gen_routes
from app.routes import play_combat as play_combat_routes
from app.routes import players as players_routes
from app.routes import prepare as prepare_routes
from app.routes import protocol as protocol_routes
from app.routes import ref as ref_routes
from app.routes import search as search_routes
from app.routes import session_notes as session_routes
from app.routes import sound as sound_routes
from app.routes import themes as theme_routes
from app.routes import tokens as token_routes
from app.routes import translate as translate_routes
from app.routes import world_instructions as instruction_routes
from app.services.adventures import AdventureNotFound
from app.services.backup import BackupError
from app.services.characters import CharacterNotFound
from app.services.combat import CombatNotFound
from app.services.creature_builder import CreatureNotFound
from app.services.encounters import EncounterNotFound
from app.services.events import close_on_shutdown_signals
from app.services.npcs import ObjectNotFound
from app.services.players import PlayerNotFound
from app.services.references import ReferenceNotFound
from app.services.worlds import WorldNotFound
from app.store.errors import DuplicateEntry, EntryNotFound, InvalidEntry, StoreError
from app.store.registry import StoreRegistry
from app.ui.adventure_pages import adventure_not_found_page
from app.ui.auth_pages import forbidden_page
from app.ui.cast_pages import object_not_found_page
from app.ui.combat_pages import combat_not_found_page
from app.ui.creature_pages import creature_not_found_page
from app.ui.encounter_pages import encounter_not_found_page
from app.ui.gm_pages import world_not_found_page
from app.ui.markdown import embeds_to_tags
from app.ui.nav import NavMemoryMiddleware
from app.ui.players_pages import player_not_found_page
from app.ui.ref_pages import reference_missing_page
from app.ui.sheet_pages import character_missing_page

ICONS = "icons/app-icon"
# (path, file under static/, media type) — the root paths browsers and iOS request.
ROOT_ICONS = (
    ("/favicon.ico", "icons/favicon/favicon.ico", "image/x-icon"),
    ("/favicon.svg", "icons/favicon/favicon.svg", "image/svg+xml"),
    ("/apple-touch-icon.png", f"{ICONS}/apple-touch-icon.png", "image/png"),
    ("/apple-touch-icon-precomposed.png", f"{ICONS}/apple-touch-icon.png", "image/png"),
)
# The app as a home-screen app (documentation/TECHSTACK.md §7: a PWA, no offline mode).
THEME_COLOR = "#4560DC"
MANIFEST = {
    "name": "Paravent",
    "short_name": "Paravent",
    "description": "A D&D 5e tool for players and dungeon masters",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "theme_color": THEME_COLOR,
    "background_color": "#FFFFFF",
    "icons": [
        {"src": f"/static/{ICONS}/android-chrome-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": f"/static/{ICONS}/android-chrome-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": f"/static/{ICONS}/maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
}

configure_logging()  # one layout for Paravent, uvicorn and the libraries (app/logs.py)
logger = logging.getLogger(__name__)


def _warn_if_library_readonly(library_dir: Path) -> None:
    """Log loudly when the library cannot be written to.

    Every save creates a temporary file next to its target, so the directory itself must
    be writable. In Docker a bind mount owned by another user than PUID:PGID reads fine
    and fails only at the first write — the login — so say it at start instead.
    """
    library_dir.mkdir(parents=True, exist_ok=True)
    if os.access(library_dir, os.W_OK | os.X_OK):
        return
    logger.error(
        "The library %s is not writable by uid %d / gid %d: logins and every save will "
        "fail. Give that user the folder (chown -R) or run as its owner (PUID/PGID).",
        library_dir,
        os.getuid(),
        os.getgid(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the stores, watch for changes, and clean up on shutdown."""
    settings = get_settings()
    registry = StoreRegistry(settings)
    if not settings.reference_configured:
        # Paravent ships no 5e content (documentation/TECHSTACK.md section 8), so this
        # is the state a fresh checkout starts in. Say so plainly rather than reporting
        # an empty load.
        logger.warning(
            "No reference corpus configured. Set PARAVENT_REFERENCE_DIR in .env to "
            "point at your 5eTools data; homebrew under %s still loads.",
            settings.homebrew_dir,
        )
    _warn_if_library_readonly(settings.library_dir)
    report = registry.load()
    logger.info("Compendium ready: %s", report.summary())
    registry.copy_old_logs()
    # ``::: npc Maren`` … ``:::`` embeds from before a tag alone on its line was one.
    rewritten = registry.adventures.rewrite_all_texts(embeds_to_tags)
    if rewritten:
        logger.info("Rewrote the ::: embeds of %d text(s) as tags on their own line", rewritten)
    if not report.ok:
        # Not fatal: the corpus is user-supplied, and a partial load is more useful
        # than a refused start. The status endpoint surfaces the detail.
        logger.warning(
            "Loaded with %d validation error(s) and %d unresolved copies",
            len(report.errors),
            len(report.unresolved_copies),
        )

    registry.start_watching()
    backups = _schedule_backups(registry)
    app.state.stores = registry
    # Open sheets hold a streaming connection that uvicorn would wait on forever before
    # running the shutdown below; end those streams the moment a stop signal arrives.
    restore_signals = close_on_shutdown_signals(registry.events)
    try:
        # The MCP endpoint's session manager (documentation/MCP.md §3).
        async with app.state.mcp.session_manager.run():
            yield
    finally:
        registry.events.close()
        restore_signals()
        if backups is not None:
            backups.cancel()
        await registry.stop_watching()


def _schedule_backups(registry: StoreRegistry) -> asyncio.Task[None] | None:
    """Back the library up at ``PARAVENT_BACKUP_AT``; a bad setting is logged, not fatal."""
    try:
        service = registry.backups
    except BackupError as exc:
        logger.error("Backups are off: %s", exc)
        return None
    if not service.enabled:
        return None
    if not service.times:
        logger.info("Backups to %s on demand only (PARAVENT_BACKUP_AT is empty)", service.target)
        return None
    logger.info(
        "Backups of %s to %s daily at %s, keeping %d",
        service.library,
        service.target,
        ", ".join(t.strftime("%H:%M") for t in service.times),
        service.keep,
    )
    return asyncio.create_task(service.schedule(), name="backups")


def mount_mcp(app: FastAPI) -> None:
    """Serve the MCP server at ``/mcp``, behind a bearer token (documentation/MCP.md §3).

    The SDK's own route is taken out of its Starlette app and added here, so ``/mcp``
    answers without a redirect to ``/mcp/``. Stateless with JSON answers: every call
    stands alone, so a restart loses nothing. The SDK's DNS-rebinding guard expects
    localhost; the token is what guards this endpoint, reached by the server's LAN name.
    """
    server = build_server(lambda: app.state.stores)
    # Stateless: the SDK would log "Terminating session" for every single call.
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    starlette_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    endpoint = next(r for r in starlette_app.routes if isinstance(r, Route) and r.path == "/mcp")
    app.router.routes.append(
        Route("/mcp", endpoint=BearerGuard(endpoint.app, lambda: app.state.stores))
    )
    app.state.mcp = server


# What a page may load (documentation/THEMES.md §4): stylesheets, fonts and pictures only
# from this server, so a theme's CSS cannot reach anywhere else. Scripts are not held
# here yet: the pages' own inline scripts and htmx attributes would need nonces first.
CONTENT_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'"
)


class ContentPolicyMiddleware:
    """Adds :data:`CONTENT_POLICY` to every response that sets none of its own.

    Pure ASGI, like the navigation memory, so event streams pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = CONTENT_POLICY
            await send(message)

        await self.app(scope, receive, send_with_policy)


def create_app() -> FastAPI:
    """Build the application. A factory, so tests can construct isolated instances."""
    app = FastAPI(
        title="Paravent",
        description="A D&D 5e tool for players and dungeon masters",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    mount_mcp(app)
    app.add_middleware(NavMemoryMiddleware)
    app.add_middleware(ContentPolicyMiddleware)

    # Icons browsers and iOS ask for at the root, whatever the page says; public, like
    # the login page, so a home-screen icon appears before anyone has logged in.
    for path, file, media_type in ROOT_ICONS:

        def serve(file: str = file, media_type: str = media_type) -> FileResponse:
            return FileResponse(
                STATIC_DIR / file,
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )

        app.add_api_route(path, serve, methods=["GET"], include_in_schema=False)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest(request: Request) -> JSONResponse:
        """The web app manifest: installable, full screen, with the app icons, in the
        table's theme colour."""
        theme = request.app.state.stores.themes.for_user(None)
        body = dict(MANIFEST)
        if theme is not None:
            body["theme_color"] = theme.manifest.theme_color.light
        return JSONResponse(
            body,
            media_type="application/manifest+json",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    app.include_router(auth_routes.router)
    app.include_router(home_routes.router)
    app.include_router(players_routes.router)
    app.include_router(character_routes.router)
    app.include_router(chargen_routes.router)
    app.include_router(levelup_routes.router)
    app.include_router(prepare_routes.router)
    app.include_router(glossary_routes.router)
    app.include_router(corpus_routes.router)
    app.include_router(backup_routes.router)
    app.include_router(mcp_routes.router)
    app.include_router(session_routes.router)
    app.include_router(instruction_routes.router)
    app.include_router(naming_routes.router)
    app.include_router(translate_routes.router)
    app.include_router(books_routes.router)
    app.include_router(gm_routes.router)
    app.include_router(encounter_routes.router)
    app.include_router(combat_routes.router)
    app.include_router(play_combat_routes.router)
    app.include_router(adventure_routes.router)
    app.include_router(cast_routes.router)
    app.include_router(location_routes.router)
    app.include_router(library_preview_routes.router)
    app.include_router(token_routes.router)
    app.include_router(npc_gen_routes.router)
    app.include_router(creature_routes.router)
    app.include_router(sound_routes.router)
    app.include_router(theme_routes.router)
    app.include_router(journal_routes.router)
    app.include_router(ref_routes.router)
    app.include_router(search_routes.router)
    app.include_router(editor_routes.router)
    app.include_router(media_routes.router)
    app.include_router(compendium_routes.router)
    app.include_router(help_routes.router)
    app.include_router(protocol_routes.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired) -> Response:
        """A page that needs a login sends the browser to the login form, and back after.

        An HTMX request cannot follow a redirect into a full page swap, so it gets the
        ``HX-Redirect`` header instead and HTMX navigates the whole window.
        """
        target = f"/login?{urlencode({'next': exc.next_url})}"
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(RoleForbidden)
    async def _role_forbidden(request: Request, exc: RoleForbidden) -> HTMLResponse:
        """Wrong role: say so plainly and offer the way back to the user's own screen."""
        page = build_page(request, exc.identity)
        home = auth_routes.home_for(exc.identity.role)
        return HTMLResponse(str(forbidden_page(page, home)), status_code=403)

    @app.exception_handler(CharacterNotFound)
    async def _character_not_found(request: Request, _exc: CharacterNotFound) -> HTMLResponse:
        """Absent, or not this player's: the same answer, so nothing leaks."""
        return HTMLResponse(str(character_missing_page(build_page(request))), status_code=404)

    @app.exception_handler(ReferenceNotFound)
    async def _reference_not_found(request: Request, _exc: ReferenceNotFound) -> HTMLResponse:
        """No such entry in the loaded data, or no data at all yet (a fresh install)."""
        empty = not request.app.state.stores.settings.reference_configured
        return HTMLResponse(
            str(reference_missing_page(build_page(request), no_corpus=empty)), status_code=404
        )

    @app.exception_handler(WorldNotFound)
    async def _world_not_found(request: Request, _exc: WorldNotFound) -> HTMLResponse:
        """An id that names no world."""
        return HTMLResponse(str(world_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(AdventureNotFound)
    async def _adventure_not_found(request: Request, _exc: AdventureNotFound) -> HTMLResponse:
        """An id that names no world, adventure or chapter (any more)."""
        return HTMLResponse(str(adventure_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(ObjectNotFound)
    async def _object_not_found(request: Request, _exc: ObjectNotFound) -> HTMLResponse:
        """An id that names no NPC or treasure (any more)."""
        return HTMLResponse(str(object_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(CreatureNotFound)
    async def _creature_not_found(request: Request, _exc: CreatureNotFound) -> HTMLResponse:
        """An id that names no creature design."""
        return HTMLResponse(str(creature_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(EncounterNotFound)
    async def _encounter_not_found(request: Request, _exc: EncounterNotFound) -> HTMLResponse:
        """An id that names no encounter."""
        return HTMLResponse(str(encounter_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(CombatNotFound)
    async def _combat_not_found(request: Request, _exc: CombatNotFound) -> HTMLResponse:
        """An id that names no combat, or no combatant in it."""
        return HTMLResponse(str(combat_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(PlayerNotFound)
    async def _player_not_found(request: Request, _exc: PlayerNotFound) -> HTMLResponse:
        """An id that names no player: deleted meanwhile, or a gamemaster's."""
        return HTMLResponse(str(player_not_found_page(build_page(request))), status_code=404)

    @app.exception_handler(EntryNotFound)
    async def _not_found(_request: Request, exc: EntryNotFound) -> JSONResponse:
        """Domain 'not found' becomes a 404 without the routes knowing about HTTP."""
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DuplicateEntry)
    async def _conflict(_request: Request, exc: DuplicateEntry) -> JSONResponse:
        """A create that would overwrite is a conflict, not a server error."""
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InvalidEntry)
    async def _invalid(_request: Request, exc: InvalidEntry) -> JSONResponse:
        """Domain validation failures use the same status as Pydantic's."""
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(StoreError)
    async def _store_error(_request: Request, exc: StoreError) -> JSONResponse:
        """Any other storage failure is a server-side problem."""
        logger.exception("Unhandled store error")
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.get("/health", tags=["meta"])
    async def health(request: Request) -> dict[str, object]:
        """Liveness probe that also reports how much data is loaded."""
        registry: StoreRegistry = request.app.state.stores
        return {
            "status": "ok",
            "compendium_entries": registry.compendium.report.total,
            "compendium_ok": registry.compendium.report.ok,
            "fluff_records": registry.fluff.report.total,
            "fluff_ok": registry.fluff.report.ok,
            # Empty when a corpus has not been configured, which is a normal state for
            # a fresh checkout rather than a failure (documentation/TECHSTACK.md section 8).
            "reference_configured": registry.settings.reference_configured,
            "lookups_missing": registry.lookups.missing,
        }

    return app


app = create_app()
