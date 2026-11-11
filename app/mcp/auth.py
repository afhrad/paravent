"""The MCP server's door: a bearer token, or nothing (documentation/MCP.md §3).

Pure ASGI around the SDK's endpoint. A request without a token, or with one that opens
nothing, is a 401 before the MCP layer sees it; failures count in the login throttle per
client address, so guessing slows down exactly as it does on the login form.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry


def bearer(headers: list[tuple[bytes, bytes]]) -> str:
    """The token of an ``Authorization: Bearer …`` header, or ""."""
    for key, value in headers:
        if key.lower() == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" else ""
    return ""


class BearerGuard:
    """Let a request through to ``app`` only with a token the library knows."""

    def __init__(self, app: ASGIApp, stores: Callable[[], StoreRegistry]) -> None:
        self.app = app
        self.stores = stores

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        registry = self.stores()
        client = scope.get("client")
        keys = [f"mcp@{client[0] if client else '?'}"]
        throttle = registry.login_throttle
        wait = throttle.wait(keys)
        if wait:
            await _refuse(send, 429, "too many failed attempts", {"retry-after": str(wait)})
            return
        token = bearer(scope.get("headers", []))
        if not token or registry.mcp_tokens.verify(token) is None:
            throttle.failed(keys)
            await _refuse(
                send,
                401,
                "a Paravent MCP token is needed",
                {"www-authenticate": 'Bearer realm="paravent"'},
            )
            return
        throttle.succeeded(keys)
        if scope.get("method") == "GET":
            # The stream a client opens for messages from the server. Paravent sends none,
            # and one left open keeps uvicorn from finishing a reload or a shutdown.
            await _refuse(send, 405, "this server only answers POST", {"allow": "POST, DELETE"})
            return
        await self.app(scope, receive, send)


async def _refuse(send: Send, status: int, message: str, headers: dict[str, str]) -> None:
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *((k.encode(), v.encode()) for k, v in headers.items()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = ["BearerGuard", "bearer"]
