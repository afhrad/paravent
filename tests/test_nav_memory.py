"""The shell's navigation remembers where each section was left (app/ui/nav.py)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from app.ui.nav import NAV, NAV_COOKIE, nav_href, read_memory, remember

PASSWORD = "correct horse"
WORLDS = next(item for item in NAV[Role.GAMEMASTER] if item.href == "/gm/worlds")
TABLE = next(item for item in NAV[Role.GAMEMASTER] if item.href == "/gm")


class TestSections:
    def test_a_section_covers_its_subtree_and_what_belongs_to_it(self) -> None:
        assert WORLDS.contains("/gm/worlds")
        assert WORLDS.contains("/gm/worlds/w1")
        assert WORLDS.contains("/gm/adventures/a1")
        assert WORLDS.contains("/gm/chapters/c1")
        assert not WORLDS.contains("/gm/worldsx")
        assert not WORLDS.contains("/gm")

    def test_the_table_is_only_its_own_page(self) -> None:
        assert TABLE.contains("/gm")
        assert not TABLE.contains("/gm/worlds")


class TestMemory:
    def test_a_loaded_page_is_remembered_for_its_section(self) -> None:
        assert remember({}, "/gm/adventures/a1", 200) == {"/gm/worlds": "/gm/adventures/a1"}

    def test_the_query_is_kept(self) -> None:
        assert remember({}, "/gm?world=w1", 200) == {"/gm": "/gm?world=w1"}

    def test_forms_and_confirmations_are_not_places_to_return_to(self) -> None:
        for url in ("/gm/worlds/w1/edit", "/gm/chapters/c1/delete", "/gm/worlds/new"):
            assert remember({}, url, 200) == {}

    def test_a_fragment_a_page_fetches_is_not_a_place(self) -> None:
        assert remember({}, "/gm/chapters/c1/live", 200) == {}
        # One remembered before this rule existed is dropped when read.
        raw = quote(json.dumps({"/gm/worlds": "/gm/chapters/c1/live"}))
        assert read_memory(raw) == {}

    def test_a_page_that_has_gone_is_forgotten(self) -> None:
        memory = {"/gm/worlds": "/gm/adventures/gone", "/books": "/books/phb"}
        assert remember(memory, "/gm/adventures/gone", 404) == {"/books": "/books/phb"}

    def test_redirects_change_nothing(self) -> None:
        memory = {"/gm/worlds": "/gm/adventures/a1"}
        assert remember(memory, "/gm/worlds/w1", 303) == memory

    def test_a_page_outside_every_section_is_not_remembered(self) -> None:
        assert remember({}, "/ref/spell/PHB/Fireball", 200) == {}

    def test_the_cookie_cannot_point_elsewhere(self) -> None:
        raw = quote(
            json.dumps(
                {
                    "/gm/worlds": "//evil.example/gm/worlds",
                    "/books": "/gm/players",  # outside its section
                    "/gm/encounters": "/gm/encounters/e1",
                    "/nowhere": "/nowhere",
                }
            )
        )
        assert read_memory(raw) == {"/gm/encounters": "/gm/encounters/e1"}
        assert read_memory("not json") == {}
        assert read_memory(quote("[1]")) == {}

    def test_inside_its_section_an_item_goes_home(self) -> None:
        memory = {"/gm/worlds": "/gm/adventures/a1"}
        assert nav_href(WORLDS, "/gm/chapters/c1", memory) == "/gm/worlds"
        assert nav_href(WORLDS, "/gm", memory) == "/gm/adventures/a1"
        assert nav_href(WORLDS, "/gm", {}) == "/gm/worlds"


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")
    return registry


@pytest.fixture
async def gm(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client


def nav_link(html: str, key_text: str) -> str:
    match = re.search(rf'<a href="([^"]*)"[^>]*>(?:(?!</a>).)*{key_text}', html, re.S)
    assert match, key_text
    return match.group(1)


class TestInTheApp:
    async def test_coming_back_lands_where_you_left(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.worlds.create(WorldInput(name="Mirefall"))
        assert (await gm.get(f"/gm/worlds/{world.id}")).status_code == 200
        table = (await gm.get("/gm")).text
        assert nav_link(table, "Worlds") == f"/gm/worlds/{world.id}"
        # In the section, the item goes back to its root.
        here = (await gm.get(f"/gm/worlds/{world.id}")).text
        assert nav_link(here, "Worlds") == "/gm/worlds"

    async def test_fragments_and_failures_are_not_remembered(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.worlds.create(WorldInput(name="Mirefall"))
        await gm.get(f"/gm/worlds/{world.id}")
        await gm.get("/gm/worlds", headers={"HX-Request": "true"})
        assert read_memory(gm.cookies.get(NAV_COOKIE)) == {"/gm/worlds": f"/gm/worlds/{world.id}"}
        registry.worlds.delete(world.id)
        assert (await gm.get(f"/gm/worlds/{world.id}")).status_code == 404
        assert read_memory(gm.cookies.get(NAV_COOKIE)) == {}
        assert nav_link((await gm.get("/gm")).text, "Worlds") == "/gm/worlds"

    async def test_a_scripts_fetch_is_not_a_page_read(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        # The chapter page refetches its text with fetch(), which sends no HX-Request;
        # remembering it sent the Worlds link and the login to a bare fragment.
        world = registry.worlds.create(WorldInput(name="Mirefall"))
        other = registry.worlds.create(WorldInput(name="Aldmere"))
        await gm.get(f"/gm/worlds/{world.id}", headers={"Sec-Fetch-Mode": "navigate"})
        await gm.get(f"/gm/worlds/{other.id}", headers={"Sec-Fetch-Mode": "cors"})
        assert read_memory(gm.cookies.get(NAV_COOKIE)) == {"/gm/worlds": f"/gm/worlds/{world.id}"}
        user = registry.auth.find_user("dm")
        assert user is not None
        assert registry.places.get(user.id).last == f"/gm/worlds/{world.id}"


class TestSettingsMenu:
    async def test_players_moved_from_the_bar_to_the_gear(self, gm: AsyncClient) -> None:
        html = (await gm.get("/gm")).text
        bar = html.split('class="shell-nav"', 1)[1].split("</nav>", 1)[0]
        assert "/gm/players" not in bar
        gear = html.split('class="usermenu settingsmenu"', 1)[1].split("</details>", 1)[0]
        assert 'aria-label="Settings"' in gear and 'href="/gm/players"' in gear
        assert 'href="/gm/glossary"' in gear
        user = html.split('class="usermenu"', 1)[1].split("</details>", 1)[0]
        assert "/gm/glossary" not in user
        # The gear sits between search and the user menu.
        assert (
            html.index("shell-search") < html.index("settingsmenu") < html.index('class="usermenu"')
        )

    async def test_the_gear_marks_the_page_it_holds(self, gm: AsyncClient) -> None:
        html = (await gm.get("/gm/players")).text
        gear = html.split('class="usermenu settingsmenu"', 1)[1].split("</details>", 1)[0]
        assert "is-current" in gear and 'aria-current="page"' in gear

    async def test_a_player_has_no_gear(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice")
        await gm.post("/logout")
        await gm.post("/login", data={"username": "alice", "password": PASSWORD})
        assert "settingsmenu" not in (await gm.get("/play")).text


class TestPageChangedInPlace:
    async def test_an_htmx_request_remembers_the_address_shown(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.worlds.create(WorldInput(name="Mirefall"))
        shown = f"http://test/gm/worlds/{world.id}?sel=abc"
        await gm.get("/gm/worlds", headers={"HX-Request": "true", "HX-Current-URL": shown})
        assert read_memory(gm.cookies.get(NAV_COOKIE)) == {
            "/gm/worlds": f"/gm/worlds/{world.id}?sel=abc"
        }
        assert nav_link((await gm.get("/gm")).text, "Worlds") == (f"/gm/worlds/{world.id}?sel=abc")

    async def test_a_failing_fragment_forgets_nothing(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.worlds.create(WorldInput(name="Mirefall"))
        await gm.get(f"/gm/worlds/{world.id}")
        await gm.get(
            "/gm/worlds/nope",
            headers={"HX-Request": "true", "HX-Current-URL": f"http://test/gm/worlds/{world.id}"},
        )
        assert read_memory(gm.cookies.get(NAV_COOKIE)) == {"/gm/worlds": f"/gm/worlds/{world.id}"}
