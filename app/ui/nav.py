"""The shell's navigation: its items, the sections they cover, and where each one was left.

Each item covers a section of the app — Worlds covers its adventures and chapters too —
and remembers the last page read in it, so leaving for the table and coming back lands
where you were, like a tab bar. The memory is a cookie written by
:class:`NavMemoryMiddleware`, which sees every response's status: only pages that
loaded are remembered, and one that has since gone (a 4xx) is forgotten.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from urllib.parse import quote, unquote, urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.models.gamestate import Role
from app.services.auth import COOKIE_NAME as SESSION_COOKIE

NAV_COOKIE = "paravent_nav"
NAV_COOKIE_MAX_AGE = 30 * 24 * 3600

# Pages that are a step in doing something, not a place to come back to.
# Steps in doing something, and fragments a page fetches for itself: never a place.
_TRANSIENT_ENDS = ("/edit", "/delete", "/new", "/live")


@dataclass(frozen=True)
class NavItem:
    """One entry in the shell's navigation.

    ``href`` is the section's root. A root of its own (``/gm``, ``/play``) matches only
    itself, since everything else lives below it; any other root matches its subtree.
    ``covers`` adds further subtrees that belong to the section.
    """

    key: str
    href: str
    icon: str
    covers: tuple[str, ...] = ()

    def contains(self, path: str) -> bool:
        """Whether ``path`` (without its query) lies in this item's section."""
        if path == self.href:
            return True
        prefixes = self.covers if self.href in ("/gm", "/play") else (self.href, *self.covers)
        return any(path == p or path.startswith(p + "/") for p in prefixes)


NAV: dict[Role, tuple[NavItem, ...]] = {
    Role.GAMEMASTER: (
        NavItem("nav.table", "/gm", "swords"),
        NavItem("nav.characters", "/gm/characters", "users-round", ("/characters", "/chargen")),
        NavItem("nav.encounters", "/gm/encounters", "skull"),
        NavItem("nav.combat", "/gm/combat", "zap"),
        NavItem(
            "nav.worlds",
            "/gm/worlds",
            "globe",
            ("/gm/adventures", "/gm/chapters", "/gm/npcs", "/gm/treasure", "/gm/share", "/journal"),
        ),
        NavItem("nav.books", "/books", "book-open", ("/screen",)),
    ),
    Role.PLAYER: (
        NavItem(
            "nav.characters", "/play", "users-round", ("/characters", "/chargen", "/play/combat")
        ),
        NavItem("nav.books", "/books", "book-open", ("/screen",)),
        NavItem("nav.journal", "/journal", "scroll"),
    ),
}

_ALL_ITEMS = tuple({item.href: item for items in NAV.values() for item in items}.values())


def _split(url: str) -> str:
    return url.split("?", 1)[0]


def _is_local(url: str) -> bool:
    return url.startswith("/") and not url.startswith("//") and "\\" not in url


def read_memory(raw: str | None) -> dict[str, str]:
    """The remembered page per section root, from the cookie.

    Anything malformed is dropped, and so is a page outside its section or off this
    site, so the cookie can never send a nav link somewhere else.
    """
    if not raw:
        return {}
    try:
        data = json.loads(unquote(raw))
    except ValueError:
        return {}
    return checked_memory(data)


def checked_memory(data: object) -> dict[str, str]:
    """Of a memory read from anywhere, what is safe to link to: :func:`read_memory`'s
    checks without the cookie's encoding."""
    if not isinstance(data, dict):
        return {}
    items = {item.href: item for item in _ALL_ITEMS}
    return {
        href: url
        for href, url in data.items()
        if isinstance(url, str)
        and href in items
        and _is_local(url)
        and items[href].contains(_split(url))
        and not _split(url).endswith(_TRANSIENT_ENDS)
    }


def remember(memory: Mapping[str, str], url: str, status: int) -> dict[str, str]:
    """``memory`` after a full page at ``url`` answered with ``status``.

    A page that loaded becomes its sections' place to return to; one that failed is
    forgotten wherever it was remembered. Redirects change nothing — the page they
    lead to is what counts.
    """
    path = _split(url)
    updated = dict(memory)
    if 200 <= status < 300:
        if path.endswith(_TRANSIENT_ENDS):
            return updated
        for item in _ALL_ITEMS:
            if item.contains(path):
                updated[item.href] = url
    elif status >= 400:
        updated = {href: kept for href, kept in updated.items() if kept != url}
    return updated


def is_place(items: tuple[NavItem, ...], url: str) -> bool:
    """Whether ``url`` is a page to come back to in one of ``items``' sections: on this
    site, inside a section, and not a step in doing something."""
    path = _split(url)
    return (
        _is_local(url)
        and not path.endswith(_TRANSIENT_ENDS)
        and any(item.contains(path) for item in items)
    )


def nav_href(item: NavItem, path: str, memory: Mapping[str, str]) -> str:
    """Where ``item`` links from ``path``: its root while you are in its section, so
    pressing it again goes home, and otherwise where you left it."""
    if item.contains(path):
        return item.href
    return memory.get(item.href, item.href)


def _cookie_value(memory: Mapping[str, str]) -> str:
    return quote(json.dumps(memory, separators=(",", ":"), sort_keys=True), safe="")


def _record_for_user(scope: Scope, cookies: SimpleCookie, url: str, status: int) -> None:
    """Keep the visit in the logged-in user's place on the server as well
    (app/services/places.py), so it outlives this browser and follows the user."""
    app = scope.get("app")
    registry = getattr(getattr(app, "state", None), "stores", None)
    if registry is None:
        return
    session = cookies.get(SESSION_COOKIE)
    registry.places.visited_by_token(session.value if session else None, url, status)


class NavMemoryMiddleware:
    """Records the page each navigation section was left on, in :data:`NAV_COOKIE`.

    Pure ASGI rather than ``BaseHTTPMiddleware``, so the live sheets' event streams
    pass through untouched. Full-page GETs count, and so does the page behind an HTMX
    GET: its ``HX-Current-URL`` is the address the browser shows, which a page may have
    changed in place (the tracker's ``?sel=`` when a combatant is selected). A fragment
    that fails forgets nothing — it says nothing about the page. No event stream, no
    preview, no script's fetch.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        query = scope.get("query_string", b"").decode("latin-1")
        fragment = "hx-request" in headers
        # A script's own fetch (the chapter refetching its text) is not a page read:
        # browsers mark a navigation ``Sec-Fetch-Mode: navigate`` and a fetch otherwise.
        mode = headers.get("sec-fetch-mode")
        if not fragment and mode is not None and mode != "navigate":
            await self.app(scope, receive, send)
            return
        if fragment:
            shown = urlsplit(headers.get("hx-current-url", ""))
            if not shown.path:
                await self.app(scope, receive, send)
                return
            url = f"{shown.path}?{shown.query}" if shown.query else shown.path
        else:
            url = f"{scope['path']}?{query}" if query else scope["path"]
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
        except CookieError:  # a malformed header means no memory, not an error
            cookies = SimpleCookie()
        morsel = cookies.get(NAV_COOKIE)
        memory = read_memory(morsel.value if morsel else None)

        async def send_with_memory(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = message.get("headers", [])
                content_type = next(
                    (
                        v.decode("latin-1")
                        for k, v in response_headers
                        if k.lower() == b"content-type"
                    ),
                    "",
                )
                status = message["status"]
                counts = not fragment or 200 <= status < 300
                if content_type.startswith("text/html") and "partial=1" not in query and counts:
                    _record_for_user(scope, cookies, url, status)
                    updated = remember(memory, url, status)
                    if updated != memory:
                        cookie = (
                            f"{NAV_COOKIE}={_cookie_value(updated)}; Path=/; "
                            f"Max-Age={NAV_COOKIE_MAX_AGE}; SameSite=Lax; HttpOnly"
                        )
                        message["headers"] = [
                            *response_headers,
                            (b"set-cookie", cookie.encode("latin-1")),
                        ]
            await send(message)

        await self.app(scope, receive, send_with_memory)


__all__ = [
    "NAV",
    "NAV_COOKIE",
    "NavItem",
    "NavMemoryMiddleware",
    "checked_memory",
    "is_place",
    "nav_href",
    "read_memory",
    "remember",
]
