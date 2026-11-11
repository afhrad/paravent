"""Authentication: password hashing, login, and server-side sessions.

A login produces two things that must agree:

* a :class:`~app.models.gamestate.Session` record in ``library/sessions.json`` — the
  server's view, which makes logout real (deleting the record ends the session even if
  the browser keeps the cookie), and
* a signed JWT in an HttpOnly cookie — the browser's view, carrying the session id and a
  per-session token that must match the record.

A token is accepted only when the signature verifies, the session record exists and is
unexpired, its token matches, and the user is still active. Any one failing yields
"not logged in", never an exception the route has to handle.

Passwords are hashed with ``bcrypt`` directly. passlib, named in the original stack, is
unmaintained and breaks on bcrypt >= 5 (its backend self-test hashes a >72-byte secret,
which bcrypt 5 rejects), so it was dropped.
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.config import Settings
from app.models.gamestate import Role, Session, User
from app.services.i18n import Translatable
from app.services.units import units_for_language
from app.store.errors import EntryNotFound
from app.store.gamestate import GameStateStore

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
COOKIE_NAME = "paravent_session"

# bcrypt only reads the first 72 bytes of a secret. Rejecting longer passwords is
# better than silently ignoring their tail, which would make two different passwords
# equal.
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 2


class AuthError(Translatable):
    """A user-facing failure: a weak password, a taken username, an unknown language.

    The message is a UI catalogue key, so it renders in the reader's language.
    """


@dataclass(frozen=True)
class Identity:
    """Who is making a request: the user and the session they logged in with."""

    user: User
    session: Session

    @property
    def role(self) -> Role:
        """The user's current role. Read from the user, so a role change applies at once."""
        return self.user.role

    @property
    def is_gamemaster(self) -> bool:
        """True for the gamemaster role."""
        return self.user.role is Role.GAMEMASTER


# -- passwords -----------------------------------------------------------------------


def validate_password(password: str) -> None:
    """Raise :class:`AuthError` when ``password`` cannot be used."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError("error.password_short", min=MIN_PASSWORD_LENGTH)
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise AuthError("error.password_long", max=MAX_PASSWORD_BYTES)


def hash_password(password: str) -> str:
    """Hash a password for storage. Validates it first."""
    validate_password(password)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """True when ``password`` matches ``password_hash``. Never raises."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # A malformed stored hash, or an over-long candidate, is simply a mismatch.
        return False


# A hash of nothing in particular, checked against when the username is unknown, so a
# failed login takes the same time whether or not the account exists.
_DUMMY_HASH = bcrypt.hashpw(b"paravent-timing-equaliser", bcrypt.gensalt()).decode()


# -- the signing key -----------------------------------------------------------------


def signing_key(settings: Settings) -> str:
    """The JWT signing key: configured, else generated once and kept in the library.

    Generating on first start means a fresh install needs no configuration. The key
    lives in the library because losing it logs everyone out — it belongs with the
    sessions it signs, on the backed-up side.
    """
    if settings.secret_key:
        return settings.secret_key
    path = settings.secret_key_file
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    # Create with owner-only permissions from the start rather than chmod afterwards.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key)
    logger.info("Generated a session signing key at %s", path)
    return key


# -- the service ---------------------------------------------------------------------


class AuthService:
    """Users, logins and sessions over the game-state store."""

    def __init__(self, store: GameStateStore, settings: Settings) -> None:
        """Bind to a store and the settings that hold the key and session lifetime."""
        self._store = store
        self._settings = settings
        self._key = signing_key(settings)

    # users

    def has_users(self) -> bool:
        """True once any account exists. False is the first-run state."""
        return len(self._store.users) > 0

    def users(self) -> list[User]:
        """Every account."""
        return self._store.users.list()

    def find_user(self, username: str) -> User | None:
        """Look a user up by name, ignoring case."""
        wanted = username.strip().casefold()
        for user in self._store.users.list():
            if user.username.casefold() == wanted:
                return user
        return None

    def check_username(self, username: str, *, exclude_id: str | None = None) -> str:
        """The cleaned username, or :class:`AuthError` when it is unusable or taken."""
        username = username.strip()
        if not username:
            raise AuthError("error.username_required")
        if any(ch.isspace() for ch in username):
            raise AuthError("error.username_spaces")
        existing = self.find_user(username)
        if existing is not None and existing.id != exclude_id:
            raise AuthError("error.username_taken", username=username)
        return username

    def create_user(
        self,
        username: str,
        password: str,
        role: Role = Role.PLAYER,
        display_name: str = "",
        language: str = "en",
    ) -> User:
        """Create an account. Raises :class:`AuthError` on a bad name or password."""
        username = self.check_username(username)
        user = User(
            username=username,
            display_name=display_name.strip() or username,
            role=role,
            password_hash=hash_password(password),
            language=language,
        )
        return self._store.users.create(user)

    def set_password(self, user_id: str, password: str) -> User:
        """Replace a user's password and end every session they hold."""
        new_hash = hash_password(password)

        def apply(user: User) -> None:
            user.password_hash = new_hash

        user = self._store.users.mutate(user_id, apply)
        self.end_sessions_for(user_id)
        return user

    def set_language(self, user_id: str, language: str) -> User:
        """Store a user's UI language, and the units it reads in (feet for English,
        metres otherwise). The caller checks the code is supported."""
        return self._store.users.update(
            user_id, language=language, units=units_for_language(language)
        )

    def set_toasts(self, user_id: str, toasts: str) -> User:
        """Store which log entries pop up for a user ("mine", "all" or "off")."""
        return self._store.users.update(user_id, toasts=toasts)

    def authenticate(self, username: str, password: str) -> User | None:
        """The user these credentials belong to, or None.

        Unknown user, wrong password and deactivated account all return None — the
        login page must not reveal which accounts exist.
        """
        user = self.find_user(username)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user.password_hash) or not user.active:
            return None
        return user

    # sessions

    def start_session(self, user: User) -> str:
        """Open a session for ``user`` and return the signed token for the cookie."""
        expires = datetime.now(UTC) + timedelta(days=self._settings.session_days)
        session = Session(
            user_id=user.id,
            token=secrets.token_urlsafe(24),
            expires_at=expires,
            role=user.role,
        )
        self._store.sessions.create(session)
        claims = {
            "sub": user.id,
            "sid": session.id,
            "tok": session.token,
            "exp": int(expires.timestamp()),
        }
        return str(jwt.encode(claims, self._key, algorithm=ALGORITHM))

    def identify(self, token: str | None) -> Identity | None:
        """Resolve a cookie token to who is logged in, or None for anyone else."""
        if not token:
            return None
        try:
            claims = jwt.decode(token, self._key, algorithms=[ALGORITHM])
        except JWTError:
            return None
        session = self._store.sessions.find(str(claims.get("sid", "")))
        user = self._store.users.find(str(claims.get("sub", "")))
        if (
            session is None
            or user is None
            or session.is_expired
            or session.user_id != user.id
            or not secrets.compare_digest(session.token, str(claims.get("tok", "")))
            or not user.active
        ):
            return None
        return Identity(user=user, session=session)

    def end_session(self, token: str | None) -> None:
        """Log out: delete the session behind ``token``. Unknown tokens are ignored."""
        identity = self.identify(token)
        if identity is None:
            return
        # A concurrent logout may have removed it first; either way it is gone.
        with suppress(EntryNotFound):
            self._store.sessions.delete(identity.session.id)

    def end_sessions_for(self, user_id: str) -> int:
        """Delete every session a user holds, returning how many were removed."""
        ids = [s.id for s in self._store.sessions.list() if s.user_id == user_id]
        for session_id in ids:
            self._store.sessions.delete(session_id)
        return len(ids)

    @property
    def max_age_seconds(self) -> int:
        """Cookie lifetime, matching the session's."""
        return self._settings.session_days * 24 * 3600


__all__ = [
    "COOKIE_NAME",
    "AuthError",
    "AuthService",
    "Identity",
    "hash_password",
    "signing_key",
    "validate_password",
    "verify_password",
]
