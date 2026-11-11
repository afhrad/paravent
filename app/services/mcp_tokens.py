"""Keys for Paravent's MCP server (documentation/MCP.md §3).

A token is a random string shown once when it is made; the library keeps only its
SHA-256. It is long and random, so a fast hash is enough — unlike a password, there is
nothing to guess. A token acts as the gamemaster who made it, while that account is
an active gamemaster.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.models.gamestate import McpToken, Role, User
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore

PREFIX = "pv_"
NAME_LIMIT = 60
# How stale ``last_used_at`` may grow before a request writes it again: every tool
# call would otherwise rewrite the file.
USED_EVERY = timedelta(minutes=5)


class McpTokenError(Translatable):
    """A token that cannot be made."""


@dataclass(frozen=True)
class Made:
    """A new token: its record and, this once, the token itself."""

    record: McpToken
    token: str


def token_hash(token: str) -> str:
    """The fingerprint a token is stored and looked up by."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class McpTokenService:
    """Making, listing, checking and revoking MCP tokens."""

    def __init__(self, store: GameStateStore) -> None:
        self._store = store

    def list(self) -> list[McpToken]:
        """Every token, newest first."""
        return sorted(self._store.mcp_tokens.list(), key=lambda t: t.created_at, reverse=True)

    def create(self, name: str, user: User) -> Made:
        """A new token for ``user``, who must be a gamemaster."""
        cleaned = " ".join(name.split())[:NAME_LIMIT]
        if not cleaned:
            raise McpTokenError("mcp.error.name")
        if user.role is not Role.GAMEMASTER:
            raise McpTokenError("mcp.error.role")
        token = PREFIX + secrets.token_urlsafe(32)
        record = self._store.mcp_tokens.create(
            McpToken(name=cleaned, token_hash=token_hash(token), user_id=user.id)
        )
        return Made(record, token)

    def revoke(self, token_id: str) -> None:
        """Remove a token; Claude sessions using it fail from the next request."""
        if self._store.mcp_tokens.find(token_id) is not None:
            self._store.mcp_tokens.delete(token_id)

    def verify(self, token: str) -> tuple[McpToken, User] | None:
        """The token's record and gamemaster, or None when it opens nothing."""
        if not token.startswith(PREFIX):
            return None
        wanted = token_hash(token)
        found = next(
            (
                t
                for t in self._store.mcp_tokens.list()
                if secrets.compare_digest(t.token_hash, wanted)
            ),
            None,
        )
        if found is None:
            return None
        user = self._store.users.find(found.user_id)
        if user is None or not user.active or user.role is not Role.GAMEMASTER:
            return None
        now = datetime.now(UTC)
        if found.last_used_at is None or now - found.last_used_at > USED_EVERY:
            found = self._store.mcp_tokens.update(found.id, last_used_at=now)
        return found, user


__all__ = ["PREFIX", "Made", "McpTokenError", "McpTokenService", "token_hash"]
