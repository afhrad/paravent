"""Tests for the auth service: hashing, accounts, sessions, and the account CLI."""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jose import jwt

from app import cli
from app.config import Settings
from app.models.gamestate import Role
from app.services.auth import (
    ALGORITHM,
    MIN_PASSWORD_LENGTH,
    AuthError,
    AuthService,
    hash_password,
    signing_key,
    verify_password,
)
from app.store.gamestate import GameStateStore

PASSWORD = "correct horse"
TOO_SHORT = "x" * (MIN_PASSWORD_LENGTH - 1)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(library_dir=tmp_path / "library", watch_files=False)


@pytest.fixture
def store(settings: Settings) -> GameStateStore:
    return GameStateStore(settings.library_dir)


@pytest.fixture
def auth(store: GameStateStore, settings: Settings) -> AuthService:
    return AuthService(store, settings)


class TestPasswords:
    def test_round_trip(self) -> None:
        hashed = hash_password(PASSWORD)
        assert hashed != PASSWORD
        assert verify_password(PASSWORD, hashed)
        assert not verify_password("wrong password", hashed)

    def test_hash_is_salted(self) -> None:
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_too_short_is_refused(self) -> None:
        with pytest.raises(AuthError, match="at least"):
            hash_password(TOO_SHORT)

    def test_over_72_bytes_is_refused_not_truncated(self) -> None:
        # bcrypt ignores everything past byte 72; accepting it would make two
        # different passwords equal.
        with pytest.raises(AuthError, match="at most"):
            hash_password("x" * 73)

    def test_multibyte_counts_bytes(self) -> None:
        with pytest.raises(AuthError):
            hash_password("ä" * 37)  # 74 bytes, 37 characters

    def test_verify_never_raises(self) -> None:
        assert not verify_password(PASSWORD, "")
        assert not verify_password(PASSWORD, "not-a-bcrypt-hash")


class TestSigningKey:
    def test_configured_key_wins(self, tmp_path: Path) -> None:
        settings = Settings(library_dir=tmp_path, secret_key="pinned")
        assert signing_key(settings) == "pinned"
        assert not settings.secret_key_file.exists()

    def test_generated_once_and_kept(self, settings: Settings) -> None:
        first = signing_key(settings)
        assert len(first) >= 32
        assert signing_key(settings) == first
        mode = stat.S_IMODE(settings.secret_key_file.stat().st_mode)
        assert mode == 0o600

    def test_lives_in_the_library(self, settings: Settings) -> None:
        signing_key(settings)
        assert settings.secret_key_file.parent == settings.library_dir


class TestAccounts:
    def test_first_run_has_no_users(self, auth: AuthService) -> None:
        assert not auth.has_users()

    def test_create_persists_hash_only(self, auth: AuthService, settings: Settings) -> None:
        user = auth.create_user("alice", PASSWORD, display_name="Alice")
        assert auth.has_users()
        assert user.role is Role.PLAYER
        raw = (settings.library_dir / "users.json").read_text()
        assert "alice" in raw
        assert PASSWORD not in raw

    def test_display_name_defaults_to_username(self, auth: AuthService) -> None:
        assert auth.create_user("bob", PASSWORD).display_name == "bob"

    def test_username_is_unique_ignoring_case(self, auth: AuthService) -> None:
        auth.create_user("Alice", PASSWORD)
        with pytest.raises(AuthError, match="taken"):
            auth.create_user("alice", PASSWORD)

    @pytest.mark.parametrize("name", ["", "   ", "two words"])
    def test_bad_usernames(self, auth: AuthService, name: str) -> None:
        with pytest.raises(AuthError):
            auth.create_user(name, PASSWORD)

    def test_find_user_ignores_case_and_whitespace(self, auth: AuthService) -> None:
        user = auth.create_user("Alice", PASSWORD)
        found = auth.find_user("  aLiCe ")
        assert found is not None and found.id == user.id


class TestAuthenticate:
    def test_good_credentials(self, auth: AuthService) -> None:
        user = auth.create_user("alice", PASSWORD)
        found = auth.authenticate("alice", PASSWORD)
        assert found is not None and found.id == user.id

    def test_wrong_password(self, auth: AuthService) -> None:
        auth.create_user("alice", PASSWORD)
        assert auth.authenticate("alice", "wrong password") is None

    def test_unknown_user(self, auth: AuthService) -> None:
        assert auth.authenticate("nobody", PASSWORD) is None

    def test_inactive_user(self, auth: AuthService, store: GameStateStore) -> None:
        user = auth.create_user("alice", PASSWORD)
        store.users.update(user.id, active=False)
        assert auth.authenticate("alice", PASSWORD) is None


class TestSessions:
    def test_token_identifies_the_user(self, auth: AuthService) -> None:
        user = auth.create_user("gm", PASSWORD, Role.GAMEMASTER)
        identity = auth.identify(auth.start_session(user))
        assert identity is not None
        assert identity.user.id == user.id
        assert identity.is_gamemaster

    def test_session_is_persisted(self, auth: AuthService, store: GameStateStore) -> None:
        user = auth.create_user("alice", PASSWORD)
        auth.start_session(user)
        assert len(store.sessions) == 1

    @pytest.mark.parametrize("token", [None, "", "garbage", "a.b.c"])
    def test_junk_tokens_are_anonymous(self, auth: AuthService, token: str | None) -> None:
        assert auth.identify(token) is None

    def test_logout_ends_the_session(self, auth: AuthService, store: GameStateStore) -> None:
        user = auth.create_user("alice", PASSWORD)
        token = auth.start_session(user)
        auth.end_session(token)
        # The cookie may survive in the browser; the server no longer honours it.
        assert auth.identify(token) is None
        assert len(store.sessions) == 0

    def test_logout_of_nothing_is_harmless(self, auth: AuthService) -> None:
        auth.end_session(None)
        auth.end_session("garbage")

    def test_token_signed_with_another_key_is_refused(
        self, auth: AuthService, store: GameStateStore, settings: Settings
    ) -> None:
        user = auth.create_user("alice", PASSWORD)
        token = auth.start_session(user)
        other = AuthService(store, settings.model_copy(update={"secret_key": "different"}))
        assert other.identify(token) is None

    def test_forged_session_token_is_refused(self, auth: AuthService, settings: Settings) -> None:
        user = auth.create_user("alice", PASSWORD)
        claims = jwt.get_unverified_claims(auth.start_session(user))
        claims["tok"] = "guessed"
        forged = jwt.encode(claims, signing_key(settings), algorithm=ALGORITHM)
        assert auth.identify(forged) is None

    def test_expired_session_is_refused(self, auth: AuthService, store: GameStateStore) -> None:
        user = auth.create_user("alice", PASSWORD)
        token = auth.start_session(user)
        session = store.sessions.list()[0]
        store.sessions.update(session.id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert auth.identify(token) is None

    def test_deactivation_ends_access(self, auth: AuthService, store: GameStateStore) -> None:
        user = auth.create_user("alice", PASSWORD)
        token = auth.start_session(user)
        store.users.update(user.id, active=False)
        assert auth.identify(token) is None

    def test_role_is_read_live_from_the_user(
        self, auth: AuthService, store: GameStateStore
    ) -> None:
        user = auth.create_user("alice", PASSWORD)
        token = auth.start_session(user)
        store.users.update(user.id, role=Role.GAMEMASTER)
        identity = auth.identify(token)
        assert identity is not None and identity.role is Role.GAMEMASTER

    def test_password_change_ends_all_sessions(self, auth: AuthService) -> None:
        user = auth.create_user("alice", PASSWORD)
        tokens = [auth.start_session(user), auth.start_session(user)]
        auth.set_password(user.id, "a new password")
        assert all(auth.identify(t) is None for t in tokens)
        assert auth.authenticate("alice", "a new password") is not None


class TestCli:
    @pytest.fixture(autouse=True)
    def library(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PARAVENT_LIBRARY_DIR", str(settings.library_dir))

    @pytest.fixture
    def passwords(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        answers: list[str] = []
        monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt="": answers.pop(0))
        return answers

    def test_add_and_list(
        self,
        passwords: list[str],
        auth: AuthService,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        passwords.extend([PASSWORD, PASSWORD])
        assert cli.main(["user", "add", "alice", "--display-name", "Alice"]) == 0
        assert cli.main(["user", "list"]) == 0
        out = capsys.readouterr().out
        assert "player" in out and "alice" in out and "Alice" in out

    def test_add_gamemaster(self, passwords: list[str], store: GameStateStore) -> None:
        passwords.extend([PASSWORD, PASSWORD])
        assert cli.main(["user", "add", "dm", "--role", "gamemaster"]) == 0
        store.users.load()
        assert store.users.list()[0].role is Role.GAMEMASTER

    def test_mismatched_confirmation(
        self, passwords: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        passwords.extend([PASSWORD, "something else"])
        assert cli.main(["user", "add", "alice"]) == 1
        assert "do not match" in capsys.readouterr().err

    def test_passwd(self, passwords: list[str], auth: AuthService, settings: Settings) -> None:
        auth.create_user("alice", PASSWORD)
        passwords.extend(["a new password", "a new password"])
        assert cli.main(["user", "passwd", "alice"]) == 0
        fresh = AuthService(GameStateStore(settings.library_dir), settings)
        assert fresh.authenticate("alice", "a new password") is not None

    def test_passwd_unknown_user(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["user", "passwd", "nobody"]) == 1
        assert "no user named nobody" in capsys.readouterr().err

    def test_list_when_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["user", "list"]) == 0
        assert "No users yet" in capsys.readouterr().out
