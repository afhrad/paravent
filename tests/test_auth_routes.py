"""End-to-end tests for login, setup, logout and the role-gated screens."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.models.pc import ChosenOption, ClassLevel, PlayerCharacter
from app.routes.auth import safe_next
from app.services.auth import COOKIE_NAME, MIN_PASSWORD_LENGTH
from app.store.registry import StoreRegistry
from app.ui.icons import UnknownIcon, available, icon
from app.ui.layout import asset

PASSWORD = "correct horse"
TOO_SHORT = "x" * (MIN_PASSWORD_LENGTH - 1)


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    """A registry over an empty library and no corpus: the fresh-install state."""
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    return registry


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def gm(registry: StoreRegistry) -> str:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")
    return "dm"


@pytest.fixture
def player(registry: StoreRegistry, gm: str) -> str:
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice Liddell")
    return "alice"


async def login(client: AsyncClient, username: str, next_url: str = "") -> Response:
    data = {"username": username, "password": PASSWORD}
    if next_url:
        data["next"] = next_url
    return await client.post("/login", data=data)


class TestFirstRun:
    async def test_root_goes_to_login_then_setup(self, client: AsyncClient) -> None:
        assert (await client.get("/")).headers["location"] == "/login"
        assert (await client.get("/login")).headers["location"] == "/setup"

    async def test_setup_form_renders(self, client: AsyncClient) -> None:
        response = await client.get("/setup")
        assert response.status_code == 200
        assert "Create gamemaster" in response.text

    async def test_setup_creates_gamemaster_and_logs_in(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        response = await client.post(
            "/setup",
            data={
                "username": "dm",
                "display_name": "Dana",
                "password": PASSWORD,
                "confirm": PASSWORD,
            },
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/gm"
        assert COOKIE_NAME in response.cookies
        user = registry.auth.find_user("dm")
        assert user is not None and user.role is Role.GAMEMASTER
        assert (await client.get("/gm")).status_code == 200

    async def test_setup_rejects_mismatched_passwords(self, client: AsyncClient) -> None:
        response = await client.post(
            "/setup", data={"username": "dm", "password": PASSWORD, "confirm": "other pass"}
        )
        assert response.status_code == 422
        assert "do not match" in response.text
        assert 'value="dm"' in response.text  # the form keeps what was typed

    async def test_setup_rejects_weak_password(self, client: AsyncClient) -> None:
        response = await client.post(
            "/setup", data={"username": "dm", "password": TOO_SHORT, "confirm": TOO_SHORT}
        )
        assert response.status_code == 422
        assert "at least" in response.text

    async def test_setup_closes_once_a_user_exists(
        self, client: AsyncClient, registry: StoreRegistry, gm: str
    ) -> None:
        assert (await client.get("/setup")).headers["location"] == "/login"
        response = await client.post(
            "/setup", data={"username": "evil", "password": PASSWORD, "confirm": PASSWORD}
        )
        assert response.headers["location"] == "/login"
        assert registry.auth.find_user("evil") is None


class TestLogin:
    async def test_form_renders(self, client: AsyncClient, gm: str) -> None:
        response = await client.get("/login")
        assert response.status_code == 200
        assert 'name="username"' in response.text
        assert 'autocomplete="current-password"' in response.text

    async def test_form_has_real_input_elements(self, client: AsyncClient, gm: str) -> None:
        # htpy's ``input_`` emits ``<input->``, which browsers render as nothing.
        text = (await client.get("/login?next=/gm")).text
        assert "<input-" not in text
        assert '<input id="f-username" name="username" type="text"' in text
        assert '<input id="f-password" name="password" type="password"' in text
        assert '<input type="hidden" name="next" value="/gm">' in text

    async def test_wrong_password(self, client: AsyncClient, gm: str) -> None:
        response = await client.post("/login", data={"username": "dm", "password": "nope nope"})
        assert response.status_code == 401
        assert "not correct" in response.text
        assert COOKIE_NAME not in response.cookies

    async def test_unknown_user_gets_the_same_message(self, client: AsyncClient, gm: str) -> None:
        response = await client.post("/login", data={"username": "ghost", "password": PASSWORD})
        assert response.status_code == 401
        assert "not correct" in response.text

    async def test_gamemaster_lands_on_gm_screen(self, client: AsyncClient, gm: str) -> None:
        response = await login(client, gm)
        assert response.status_code == 303
        assert response.headers["location"] == "/gm"

    async def test_player_lands_on_play_screen(self, client: AsyncClient, player: str) -> None:
        assert (await login(client, player)).headers["location"] == "/play"

    async def test_cookie_is_httponly_and_lax(self, client: AsyncClient, gm: str) -> None:
        header = (await login(client, gm)).headers["set-cookie"].lower()
        assert "httponly" in header
        assert "samesite=lax" in header
        assert "secure" not in header  # plain HTTP on the local network by default

    async def test_next_is_honoured(self, client: AsyncClient, gm: str) -> None:
        response = await login(client, gm, next_url="/gm?tab=x")
        assert response.headers["location"] == "/gm?tab=x"

    async def test_offsite_next_is_ignored(self, client: AsyncClient, gm: str) -> None:
        response = await login(client, gm, next_url="//evil.example/")
        assert response.headers["location"] == "/gm"

    async def test_logged_in_user_skips_the_form(self, client: AsyncClient, gm: str) -> None:
        await login(client, gm)
        assert (await client.get("/login")).headers["location"] == "/gm"

    async def test_root_redirects_by_role(self, client: AsyncClient, player: str) -> None:
        await login(client, player)
        assert (await client.get("/")).headers["location"] == "/play"


class TestGuards:
    async def test_anonymous_is_sent_to_login_with_next(self, client: AsyncClient, gm: str) -> None:
        response = await client.get("/gm")
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=%2Fgm"

    async def test_htmx_request_gets_hx_redirect(self, client: AsyncClient, gm: str) -> None:
        response = await client.get("/play", headers={"HX-Request": "true"})
        assert response.status_code == 401
        assert response.headers["HX-Redirect"] == "/login?next=%2Fplay"

    async def test_player_cannot_open_gm_screen(self, client: AsyncClient, player: str) -> None:
        await login(client, player)
        response = await client.get("/gm")
        assert response.status_code == 403
        assert 'href="/play"' in response.text

    async def test_gamemaster_cannot_open_player_screen(self, client: AsyncClient, gm: str) -> None:
        await login(client, gm)
        assert (await client.get("/play")).status_code == 403

    async def test_tampered_cookie_is_anonymous(self, client: AsyncClient, gm: str) -> None:
        client.cookies.set(COOKIE_NAME, "not-a-token")
        assert (await client.get("/gm")).status_code == 303


class TestLogout:
    async def test_logout_ends_the_session(
        self, client: AsyncClient, registry: StoreRegistry, gm: str
    ) -> None:
        await login(client, gm)
        token = client.cookies.get(COOKIE_NAME)
        response = await client.post("/logout")
        assert response.headers["location"] == "/login?out=1"
        assert len(registry.gamestate.sessions) == 0
        # Replaying the old cookie no longer works: logout is server-side.
        client.cookies.set(COOKIE_NAME, token or "")
        assert (await client.get("/gm")).status_code == 303

    async def test_logged_out_notice(self, client: AsyncClient, gm: str) -> None:
        assert "logged out" in (await client.get("/login?out=1")).text


class TestScreens:
    async def test_gm_screen_shows_identity_and_the_party(
        self, client: AsyncClient, player: str
    ) -> None:
        await login(client, "dm")
        text = (await client.get("/gm")).text
        assert "Dana" in text and "@dm" in text
        assert "Gamemaster" in text
        assert 'id="party"' in text and "No characters yet" in text
        assert 'action="/logout"' in text

    async def test_the_user_menu_holds_language_theme_and_logout(
        self, client: AsyncClient, player: str
    ) -> None:
        await login(client, player)
        text = (await client.get("/play")).text
        menu = text[text.index('<details class="usermenu">') : text.index("</header>")]
        assert "Alice Liddell" in menu and "@alice" in menu
        assert 'action="/language"' in menu and 'class="themetoggle icontoggle"' in menu
        assert 'action="/logout"' in menu
        bar = text[text.index("<header") : text.index('<details class="usermenu">')]
        assert "langmenu" not in bar and "themetoggle" not in bar  # only navigation left of it

    async def test_player_screen_empty_state(self, client: AsyncClient, player: str) -> None:
        await login(client, player)
        text = (await client.get("/play")).text
        assert "Alice Liddell" in text
        assert "No characters yet" in text

    async def test_player_sees_only_their_characters(
        self, client: AsyncClient, registry: StoreRegistry, player: str
    ) -> None:
        alice = registry.auth.find_user(player)
        assert alice is not None
        registry.gamestate.characters.create(
            PlayerCharacter(
                name="Torvald",
                player="alice",
                species=ChosenOption(name="Dwarf", source="PHB"),
                classes=[ClassLevel(name="Fighter", source="PHB", level=3)],
            )
        )
        registry.gamestate.characters.create(PlayerCharacter(name="Somebody Else"))
        await login(client, player)
        text = (await client.get("/play")).text
        assert "Torvald" in text and "Dwarf · Fighter 3" in text
        assert "Somebody Else" not in text

    async def test_pages_carry_theme_and_assets(self, client: AsyncClient, gm: str) -> None:
        text = (await client.get("/login")).text
        assert text.startswith("<!doctype html>")
        assert "/static/css/tokens.css" in text
        assert 'class="themetoggle icontoggle"' in text
        assert '<symbol id="i-log-out"' in text  # sprite inlined once

    async def test_assets_are_linked_with_their_version(self, client: AsyncClient, gm: str) -> None:
        """A changed stylesheet must not be served from a browser's cache."""
        text = (await client.get("/login")).text
        url = asset("/static/css/app.css")
        assert url.startswith("/static/css/app.css?v=") and f'href="{url}"' in text
        assert (await client.get(url)).status_code == 200
        assert asset("/static/css/missing.css") == "/static/css/missing.css"

    @pytest.mark.parametrize(
        "path", ["/static/css/tokens.css", "/static/css/app.css", "/static/js/ui.js"]
    )
    async def test_static_assets_are_served(self, client: AsyncClient, path: str) -> None:
        assert (await client.get(path)).status_code == 200

    async def test_installable_without_login(self, client: AsyncClient, gm: str) -> None:
        """The manifest and the icons a home screen needs load before anyone logs in."""
        got = await client.get("/manifest.webmanifest")
        assert got.status_code == 200
        assert got.headers["content-type"].startswith("application/manifest+json")
        manifest = got.json()
        assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
        for icon_ in manifest["icons"]:
            assert (await client.get(icon_["src"])).status_code == 200, icon_["src"]
        assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
        for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/favicon.svg"):
            assert (await client.get(path)).status_code == 200, path
        head = (await client.get("/login")).text
        assert '<link rel="manifest" href="/manifest.webmanifest">' in head
        assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in head
        assert '<meta name="apple-mobile-web-app-capable" content="yes">' in head

    async def test_favicon_at_the_root_without_login(self, client: AsyncClient, gm: str) -> None:
        """Browsers request /favicon.ico on their own; it must not 404 or redirect."""
        got = await client.get("/favicon.ico")
        assert got.status_code == 200
        assert got.headers["content-type"] == "image/x-icon"
        assert '<link rel="icon" href="/favicon.ico"' in (await client.get("/login")).text


class TestHelpers:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (None, ""),
            ("", ""),
            ("/gm", "/gm"),
            ("/play?x=1", "/play?x=1"),
            ("//evil.example", ""),
            ("/\\evil.example", ""),
            ("https://evil.example", ""),
            ("gm", ""),
        ],
    )
    def test_safe_next(self, url: str | None, expected: str) -> None:
        assert safe_next(url) == expected

    def test_icons_used_by_the_ui_exist(self) -> None:
        used = {"book-open", "log-out", "arrow-right", "arrow-left", "triangle-alert"}
        used |= {"check", "shield", "shield-check", "swords", "users-round"}
        assert used <= available()

    def test_unknown_icon_raises(self) -> None:
        with pytest.raises(UnknownIcon):
            icon("d20")  # does not exist in Lucide (documentation/DESIGN.md §5)


async def test_the_version_is_on_screen(tmp_path: Path) -> None:
    from httpx import ASGITransport, AsyncClient

    from app import __version__
    from app.config import Settings
    from app.main import create_app
    from app.models.gamestate import Role
    from app.store.registry import StoreRegistry

    registry = StoreRegistry(Settings(library_dir=tmp_path / "lib", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", "correct horse battery", Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        assert f"Paravent {__version__}" in (await ac.get("/login")).text
        await ac.post("/login", data={"username": "dm", "password": "correct horse battery"})
        assert f"Paravent {__version__}" in (await ac.get("/gm")).text  # the user menu
