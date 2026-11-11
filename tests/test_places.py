"""Logging in again carries on where the user left off (app/services/places.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, User
from app.models.pc import PlayerCharacter
from app.routes.auth import RESUME_COOKIE
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    return registry


@pytest.fixture
def alice(registry: StoreRegistry) -> User:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")
    return registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice")


@pytest.fixture
def thorin(registry: StoreRegistry, alice: User) -> PlayerCharacter:
    return registry.gamestate.characters.create(
        PlayerCharacter.model_validate(
            {
                "name": "Thorin",
                "player": "alice",
                "classes": [{"class": "Homebrew Fighter", "level": 3}],
                "abilities": {"str": 16, "con": 14},
                "overrides": {"hp_max": 28},
            }
        )
    )


def client_for(registry: StoreRegistry) -> AsyncClient:
    app = create_app()
    app.state.stores = registry
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    async with client_for(registry) as ac:
        yield ac


async def login(client: AsyncClient, username: str = "alice", **data: str) -> str:
    response = await client.post(
        "/login", data={"username": username, "password": PASSWORD, **data}
    )
    assert response.status_code == 303
    return response.headers["location"]


class TestResume:
    async def test_a_new_login_lands_on_the_page_left(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        assert await login(client) == "/play"
        assert (await client.get(f"/characters/{thorin.id}")).status_code == 200
        await client.post("/logout")
        assert await login(client) == f"/characters/{thorin.id}"
        assert client.cookies.get(RESUME_COOKIE) == "1"

    async def test_another_device_carries_on_too(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client)
        await client.get("/books")
        async with client_for(registry) as phone:
            assert await login(phone) == "/books"
            # The nav on the new device knows where each section was left.
            assert f'href="/characters/{thorin.id}"' not in (await phone.get("/books")).text
            await phone.get(f"/characters/{thorin.id}")
        await client.post("/logout")
        assert await login(client) == f"/characters/{thorin.id}"

    async def test_the_installed_app_starts_where_it_was_left(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client)
        await client.get(f"/characters/{thorin.id}")
        response = await client.get("/")
        assert response.headers["location"] == f"/characters/{thorin.id}"

    async def test_a_page_asked_for_wins_over_the_page_left(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client)
        await client.get(f"/characters/{thorin.id}")
        await client.post("/logout")
        assert await login(client, next="/books") == "/books"

    async def test_forms_and_pages_that_have_gone_are_not_returned_to(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client)
        await client.get(f"/characters/{thorin.id}")
        await client.get(f"/characters/{thorin.id}/delete")
        await client.post("/logout")
        assert await login(client) == f"/characters/{thorin.id}"
        registry.gamestate.characters.delete(thorin.id)
        assert (await client.get(f"/characters/{thorin.id}")).status_code == 404
        await client.post("/logout")
        client.cookies.delete(RESUME_COOKIE)  # ui.js clears it on the page it marks
        assert await login(client) == "/play"
        assert RESUME_COOKIE not in client.cookies

    async def test_a_new_user_goes_to_their_screen(self, client: AsyncClient, alice: User) -> None:
        assert await login(client) == "/play"
        assert await login(client, "dm") == "/gm"


class TestSheetView:
    async def test_the_sheet_opens_on_the_view_it_was_left_on(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client)
        response = await client.post(
            f"/characters/{thorin.id}/view", data={"tab": "skills", "view": "gear", "filter": "2"}
        )
        assert response.status_code == 204
        html = (await client.get(f"/characters/{thorin.id}")).text
        assert 'data-tab="skills"' in html and 'data-m="gear"' in html
        assert 'data-filter="2"' in html
        # An address that names a view still wins.
        html = (await client.get(f"/characters/{thorin.id}?view=log")).text
        assert 'data-m="log"' in html
        # the log is the top bar's (the log panel, PROTOCOL.md §5), not a bottom tab
        bar = html[html.index("<header") : html.index("</header>")]
        assert 'class="btn btn-ghost shell-log"' in bar
        tabs = html[html.index('class="cs-mtabs"') :]
        assert 'data-view-link="log"' not in tabs

    async def test_only_the_owner_keeps_a_view(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        registry.auth.create_user("bob", PASSWORD, Role.PLAYER, "Bob")
        await login(client, "bob")
        response = await client.post(f"/characters/{thorin.id}/view", data={"tab": "skills"})
        assert response.status_code == 404


class TestStorage:
    async def test_kept_in_the_library_and_dropped_with_the_player(
        self, client: AsyncClient, registry: StoreRegistry, alice: User, thorin: PlayerCharacter
    ) -> None:
        await login(client)
        await client.get(f"/characters/{thorin.id}")
        assert (registry.settings.library_dir / "places.json").exists()
        assert registry.places.get(alice.id).last == f"/characters/{thorin.id}"
        registry.players.delete(alice.id)
        assert registry.gamestate.places.find(alice.id) is None

    def test_a_place_outside_the_roles_sections_is_not_resumed(
        self, registry: StoreRegistry, alice: User
    ) -> None:
        registry.places.visited(alice.id, Role.PLAYER, "/gm/worlds", 200)
        assert registry.places.resume(alice.id, Role.PLAYER) is None

    def test_its_own_writes_reload_nothing_else(
        self, registry: StoreRegistry, alice: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry.places.visited(alice.id, Role.PLAYER, "/books", 200)

        def unexpected() -> None:
            raise AssertionError("users reloaded")

        monkeypatch.setattr(registry.gamestate.users, "load", unexpected)
        registry.reload_gamestate({registry.gamestate.places.path})
        assert registry.places.resume(alice.id, Role.PLAYER) == "/books"
