"""Tests for the gamemaster's player management and the language selector."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, User
from app.models.pc import PlayerCharacter
from app.services.auth import MIN_PASSWORD_LENGTH, AuthError
from app.services.i18n import LANG_COOKIE
from app.services.players import PlayerInput, PlayerNotFound, PlayerService
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"
TOO_SHORT = "x" * (MIN_PASSWORD_LENGTH - 1)


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    return registry


@pytest.fixture
def service(registry: StoreRegistry) -> PlayerService:
    return registry.players


@pytest.fixture
def gm(registry: StoreRegistry) -> User:
    return registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")


@pytest.fixture
def alice(registry: StoreRegistry, gm: User) -> User:
    return registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice", "de")


def _input(**overrides: object) -> PlayerInput:
    base: dict[str, object] = {
        "username": "bob",
        "display_name": "Bob",
        "language": "en",
        "password": PASSWORD,
        "confirm": PASSWORD,
    }
    base.update(overrides)
    return PlayerInput(**base)  # type: ignore[arg-type]


# -- service ---------------------------------------------------------------------------


class TestService:
    def test_create(self, service: PlayerService) -> None:
        user = service.create(_input(language="de"))
        assert user.role is Role.PLAYER
        assert user.language == "de"

    def test_create_rejects_unknown_language(self, service: PlayerService) -> None:
        with pytest.raises(AuthError) as caught:
            service.create(_input(language="xx"))
        assert caught.value.key == "error.language_unknown"

    def test_create_rejects_mismatch(self, service: PlayerService) -> None:
        with pytest.raises(AuthError, match="do not match"):
            service.create(_input(confirm="something else"))

    def test_gamemaster_is_not_a_player(self, service: PlayerService, gm: User) -> None:
        assert service.find(gm.id) is None
        with pytest.raises(PlayerNotFound):
            service.get(gm.id)
        with pytest.raises(PlayerNotFound):
            service.delete(gm.id)

    def test_summaries_count_characters(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        registry.gamestate.characters.create(PlayerCharacter(name="A", player="alice"))
        registry.gamestate.characters.create(PlayerCharacter(name="B", player="alice"))
        [summary] = service.summaries()
        assert summary.user.id == alice.id
        assert summary.characters == 2

    def test_update_without_password_keeps_it(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        token = registry.auth.start_session(alice)
        service.update(alice.id, _input(username="alice2", password="", confirm=""))
        assert registry.auth.authenticate("alice2", PASSWORD) is not None
        assert registry.auth.identify(token) is not None  # a rename does not log out

    def test_update_password_ends_sessions(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        token = registry.auth.start_session(alice)
        service.update(
            alice.id, _input(username="alice", password="new password", confirm="new password")
        )
        assert registry.auth.identify(token) is None
        assert registry.auth.authenticate("alice", "new password") is not None

    def test_deactivate_ends_sessions(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        token = registry.auth.start_session(alice)
        service.update(alice.id, _input(username="alice", password="", confirm="", active=False))
        assert registry.auth.identify(token) is None
        assert registry.auth.authenticate("alice", PASSWORD) is None

    def test_username_may_stay_but_not_collide(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        service.update(alice.id, _input(username="ALICE", password="", confirm=""))
        with pytest.raises(AuthError) as caught:
            service.update(alice.id, _input(username="dm", password="", confirm=""))
        assert caught.value.key == "error.username_taken"

    def test_rejected_update_writes_nothing(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        with pytest.raises(AuthError):
            service.update(
                alice.id,
                _input(username="renamed", display_name="X", password=TOO_SHORT, confirm=TOO_SHORT),
            )
        assert service.get(alice.id).username == "alice"

    def test_delete_keeps_characters_unassigned(
        self, service: PlayerService, registry: StoreRegistry, alice: User
    ) -> None:
        pc = registry.gamestate.characters.create(PlayerCharacter(name="A", player="alice"))
        token = registry.auth.start_session(alice)
        assert service.delete(alice.id) == 1
        assert service.find(alice.id) is None
        assert registry.gamestate.characters.get(pc.id).player is None
        assert registry.auth.identify(token) is None


# -- routes ----------------------------------------------------------------------------


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def login(client: AsyncClient, username: str) -> Response:
    return await client.post("/login", data={"username": username, "password": PASSWORD})


@pytest.fixture
async def as_gm(client: AsyncClient, gm: User) -> AsyncClient:
    await login(client, "dm")
    return client


def _form(**overrides: str) -> dict[str, str]:
    data = {
        "username": "bob",
        "display_name": "Bob",
        "language": "en",
        "password": PASSWORD,
        "confirm": PASSWORD,
    }
    data.update(overrides)
    return data


class TestPlayerRoutes:
    async def test_anonymous_is_sent_to_login(self, client: AsyncClient, gm: User) -> None:
        response = await client.get("/gm/players")
        assert response.headers["location"] == "/login?next=%2Fgm%2Fplayers"

    async def test_player_is_refused(self, client: AsyncClient, alice: User) -> None:
        await login(client, "alice")
        assert (await client.get("/gm/players")).status_code == 403
        response = await client.post("/gm/players", data=_form())
        assert response.status_code == 403

    async def test_empty_list(self, as_gm: AsyncClient) -> None:
        text = (await as_gm.get("/gm/players")).text
        assert "No players yet" in text
        assert 'href="/gm/players/new"' in text
        assert 'aria-current="page"' in text and 'href="/gm/players"' in text

    async def test_new_form_defaults_to_gm_language(
        self, as_gm: AsyncClient, registry: StoreRegistry, gm: User
    ) -> None:
        registry.auth.set_language(gm.id, "de")
        text = (await as_gm.get("/gm/players/new")).text
        assert '<option value="de" selected>Deutsch</option>' in text

    async def test_create(self, as_gm: AsyncClient, registry: StoreRegistry) -> None:
        response = await as_gm.post("/gm/players", data=_form(language="de"))
        assert response.status_code == 303
        bob = registry.auth.find_user("bob")
        assert bob is not None and bob.role is Role.PLAYER and bob.language == "de"
        listing = await as_gm.get(response.headers["location"])
        assert "Player Bob created." in listing.text
        assert "@bob" in listing.text and "Deutsch" in listing.text

    async def test_create_error_keeps_input(self, as_gm: AsyncClient) -> None:
        response = await as_gm.post("/gm/players", data=_form(username="dm"))
        assert response.status_code == 422
        assert "already taken" in response.text
        assert 'value="Bob"' in response.text
        assert PASSWORD not in response.text  # passwords are never echoed

    async def test_edit_form_is_prefilled(self, as_gm: AsyncClient, alice: User) -> None:
        text = (await as_gm.get(f"/gm/players/{alice.id}")).text
        assert 'value="alice"' in text
        assert '<option value="de" selected>' in text
        assert 'name="active" type="checkbox" checked' in text
        assert f'href="/gm/players/{alice.id}/delete"' in text

    async def test_update(self, as_gm: AsyncClient, registry: StoreRegistry, alice: User) -> None:
        response = await as_gm.post(
            f"/gm/players/{alice.id}",
            data={
                "username": "alice",
                "display_name": "Alice L.",
                "language": "en",
                "active": "on",
            },
        )
        assert response.headers["location"] == f"/gm/players?updated={alice.id}"
        updated = registry.players.get(alice.id)
        assert updated.display_name == "Alice L." and updated.language == "en"
        assert "Changes to Alice L. saved." in (await as_gm.get(response.headers["location"])).text

    async def test_unticked_checkbox_deactivates(
        self, as_gm: AsyncClient, registry: StoreRegistry, alice: User
    ) -> None:
        await as_gm.post(f"/gm/players/{alice.id}", data={"username": "alice", "language": "de"})
        assert registry.players.get(alice.id).active is False
        assert "Inactive" in (await as_gm.get("/gm/players")).text

    async def test_update_error(self, as_gm: AsyncClient, alice: User) -> None:
        response = await as_gm.post(
            f"/gm/players/{alice.id}",
            data={"username": "alice", "language": "de", "password": "x", "confirm": "y"},
        )
        assert response.status_code == 422
        assert "do not match" in response.text

    async def test_delete_flow(
        self, as_gm: AsyncClient, registry: StoreRegistry, alice: User
    ) -> None:
        registry.gamestate.characters.create(PlayerCharacter(name="Thorin", player="alice"))
        confirm = await as_gm.get(f"/gm/players/{alice.id}/delete")
        assert "Delete Alice?" in confirm.text
        assert "Their character is kept and becomes unassigned." in confirm.text
        response = await as_gm.post(f"/gm/players/{alice.id}/delete")
        assert response.headers["location"] == "/gm/players?deleted=1"
        assert registry.players.find(alice.id) is None
        listing = await as_gm.get(response.headers["location"])
        assert "Their character is kept and now unassigned." in listing.text

    @pytest.mark.parametrize("suffix", ["", "/delete"])
    async def test_unknown_player_is_404(self, as_gm: AsyncClient, suffix: str) -> None:
        response = await as_gm.get(f"/gm/players/nope{suffix}")
        assert response.status_code == 404
        assert "Player not found" in response.text

    async def test_gamemaster_cannot_be_edited_here(self, as_gm: AsyncClient, gm: User) -> None:
        assert (await as_gm.get(f"/gm/players/{gm.id}")).status_code == 404
        assert (await as_gm.post(f"/gm/players/{gm.id}/delete")).status_code == 404


# -- language ----------------------------------------------------------------------------


class TestLanguage:
    async def test_selector_is_on_every_page(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        assert 'action="/language"' in (await client.get("/setup")).text
        registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
        assert 'action="/language"' in (await client.get("/login")).text
        await login(client, "dm")
        assert 'action="/language"' in (await client.get("/gm")).text
        assert 'action="/language"' in (await client.get("/gm/players")).text

    async def test_browser_language_is_used_before_any_choice(self, client: AsyncClient) -> None:
        text = (await client.get("/setup", headers={"Accept-Language": "de-DE,de;q=0.9"})).text
        assert '<html lang="de">' in text
        assert "Willkommen bei Paravent" in text

    async def test_anonymous_switch_sets_cookie_and_returns(self, client: AsyncClient) -> None:
        response = await client.post("/language", data={"lang": "de", "next": "/setup"})
        assert response.status_code == 303
        assert response.headers["location"] == "/setup"
        assert response.cookies[LANG_COOKIE] == "de"
        assert "Spielleitung anlegen" in (await client.get("/setup")).text

    async def test_unknown_language_changes_nothing(self, client: AsyncClient) -> None:
        response = await client.post("/language", data={"lang": "xx", "next": "/setup"})
        assert LANG_COOKIE not in response.cookies

    async def test_offsite_next_is_ignored(self, client: AsyncClient) -> None:
        response = await client.post("/language", data={"lang": "de", "next": "//evil.example"})
        assert response.headers["location"] == "/"

    async def test_setup_creates_gamemaster_in_page_language(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        await client.post("/language", data={"lang": "de", "next": "/setup"})
        await client.post(
            "/setup", data={"username": "dm", "password": PASSWORD, "confirm": PASSWORD}
        )
        gm = registry.auth.find_user("dm")
        assert gm is not None and gm.language == "de"
        assert "Dein Spieltisch" in (await client.get("/gm")).text

    async def test_setup_errors_are_translated(self, client: AsyncClient) -> None:
        client.cookies.set(LANG_COOKIE, "de")
        response = await client.post(
            "/setup", data={"username": "dm", "password": TOO_SHORT, "confirm": TOO_SHORT}
        )
        assert f"mindestens {MIN_PASSWORD_LENGTH} Zeichen" in response.text

    async def test_logged_in_switch_is_saved_on_the_account(
        self, client: AsyncClient, registry: StoreRegistry, gm: User
    ) -> None:
        await login(client, "dm")
        await client.post("/language", data={"lang": "de", "next": "/gm"})
        assert registry.auth.find_user("dm").language == "de"  # type: ignore[union-attr]
        text = (await client.get("/gm")).text
        assert "Abmelden" in text and "Spieler" in text

    async def test_account_language_beats_the_cookie(
        self, client: AsyncClient, alice: User
    ) -> None:
        client.cookies.set(LANG_COOKIE, "en")
        response = await login(client, "alice")
        # Login also re-points the cookie, so a later logout stays in German.
        assert response.cookies[LANG_COOKIE] == "de"
        assert "Deine Charaktere" in (await client.get("/play")).text

    async def test_login_failure_is_translated(self, client: AsyncClient, gm: User) -> None:
        client.cookies.set(LANG_COOKIE, "de")
        response = await client.post("/login", data={"username": "dm", "password": "nope nope"})
        assert "Benutzername oder Passwort ist nicht korrekt." in response.text

    async def test_menu_marks_the_current_language(self, client: AsyncClient, gm: User) -> None:
        client.cookies.set(LANG_COOKIE, "de")
        text = (await client.get("/login")).text
        assert 'value="de" lang="de" aria-current="true"' in text
        assert 'name="next" value="/login"' in text
