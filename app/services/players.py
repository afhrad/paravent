"""Player accounts, as the gamemaster manages them.

Scope is deliberately **players only**: a gamemaster account cannot be edited or deleted
through here, so a mistyped id can never lock the table out of its own gamemaster.

Deleting a player **keeps their characters**. A character is someone's work and belongs
in the library whether or not its player still has an account; it is left unassigned
for the gamemaster to hand to someone else.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.gamestate import Role, User
from app.services.auth import AuthError, AuthService, hash_password
from app.services.i18n import UiCatalogue
from app.store.gamestate import GameStateStore


class PlayerNotFound(LookupError):
    """No player with that id — absent, or a gamemaster account."""


@dataclass(frozen=True)
class PlayerInput:
    """What the gamemaster's form submits. ``password`` empty means "unchanged" on update."""

    username: str
    display_name: str = ""
    language: str = "en"
    password: str = ""
    confirm: str = ""
    active: bool = True


@dataclass(frozen=True)
class PlayerSummary:
    """A player plus what the list shows about them."""

    user: User
    characters: int


class PlayerService:
    """Create, read, update and delete player accounts."""

    def __init__(self, store: GameStateStore, auth: AuthService, catalogue: UiCatalogue) -> None:
        """Bind to the store, the auth service, and the languages a player may choose."""
        self._store = store
        self._auth = auth
        self._catalogue = catalogue

    # -- reading -----------------------------------------------------------------

    def find(self, player_id: str) -> User | None:
        """The player with ``player_id``, or None — also for a gamemaster's id."""
        user = self._store.users.find(player_id)
        return user if user is not None and user.role is Role.PLAYER else None

    def get(self, player_id: str) -> User:
        """The player with ``player_id``, or :class:`PlayerNotFound`."""
        user = self.find(player_id)
        if user is None:
            raise PlayerNotFound(player_id)
        return user

    def summaries(self) -> list[PlayerSummary]:
        """Every player with their character count, by display name."""
        counts: dict[str, int] = {}
        for pc in self._store.characters.list():
            if pc.player:
                key = pc.player.casefold()
                counts[key] = counts.get(key, 0) + 1
        players = self._store.users.list(lambda u: u.role is Role.PLAYER)
        players.sort(key=lambda u: (u.display_name or u.username).casefold())
        return [PlayerSummary(u, counts.get(u.username.casefold(), 0)) for u in players]

    def character_count(self, player_id: str) -> int:
        """How many characters belong to a player."""
        username = self.get(player_id).username
        return len(self._store.characters.list(lambda pc: pc.belongs_to(username)))

    # -- writing -----------------------------------------------------------------

    def _check_language(self, code: str) -> str:
        if not self._catalogue.supports(code):
            raise AuthError("error.language_unknown", language=code)
        return code

    @staticmethod
    def _check_confirm(data: PlayerInput) -> None:
        if data.password != data.confirm:
            raise AuthError("error.password_mismatch")

    def create(self, data: PlayerInput) -> User:
        """Create a player. Validation failures raise :class:`AuthError`."""
        self._check_confirm(data)
        return self._auth.create_user(
            data.username,
            data.password,
            Role.PLAYER,
            data.display_name,
            self._check_language(data.language),
        )

    def update(self, player_id: str, data: PlayerInput) -> User:
        """Update a player. An empty password leaves it unchanged.

        Every check runs before anything is written, so a rejected form changes nothing.
        A new password or a deactivation ends the player's sessions: either is the
        gamemaster saying "this person should not be logged in as they are now".
        """
        current = self.get(player_id)
        # Captured now: the users collection mutates its stored record in place, so
        # ``current`` already carries the new values once the update has run.
        previous_username, was_active = current.username, current.active
        username = self._auth.check_username(data.username, exclude_id=player_id)
        language = self._check_language(data.language)
        new_hash: str | None = None
        if data.password or data.confirm:
            self._check_confirm(data)
            new_hash = hash_password(data.password)

        def apply(user: User) -> None:
            user.username = username
            user.display_name = data.display_name.strip() or username
            user.language = language
            user.active = data.active
            if new_hash is not None:
                user.password_hash = new_hash

        updated = self._store.users.mutate(player_id, apply)
        if new_hash is not None or (was_active and not data.active):
            self._auth.end_sessions_for(player_id)
        if username != previous_username:
            # Character files name their player by username; follow the rename.
            for pc in self._store.characters.list(lambda c: c.belongs_to(previous_username)):
                self._store.characters.update(pc.id, player=username)
        return updated

    def delete(self, player_id: str) -> int:
        """Delete a player, returning how many of their characters were unassigned."""
        username = self.get(player_id).username
        orphaned = [pc.id for pc in self._store.characters.list() if pc.belongs_to(username)]
        for pc_id in orphaned:
            self._store.characters.update(pc_id, player=None)
        self._auth.end_sessions_for(player_id)
        if self._store.places.find(player_id) is not None:
            self._store.places.delete(player_id)
        self._store.users.delete(player_id)
        return len(orphaned)


__all__ = ["PlayerInput", "PlayerNotFound", "PlayerService", "PlayerSummary"]
