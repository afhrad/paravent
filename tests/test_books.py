"""The rulebooks (TECHSTACK.md §6b, phase 3) — against the real corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.store.published import (
    PublishedBooks,
    ReferenceScreens,
    chapter_headings,
    heading_anchor,
)
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def books(corpus: Path) -> PublishedBooks:
    return PublishedBooks(corpus)


def _combat(books: PublishedBooks) -> int:
    phb = books.get("PHB")
    assert phb is not None
    return next(i for i, c in enumerate(phb.contents) if c.name == "Combat")


class TestStore:
    def test_the_index_and_a_body(self, books: PublishedBooks) -> None:
        phb = books.get("phb")
        assert phb is not None and phb.group == "core" and phb.source == "PHB"
        combat = phb.contents[_combat(books)]
        assert combat.ordinal == "Chapter 9" and "Making an Attack" in combat.headers
        body = books.body("PHB")
        assert body is not None and body.section(_combat(books)).name == "Combat"  # type: ignore[union-attr]
        assert body.section(999) is None

    def test_a_chapters_headings_go_deeper_than_the_index(self, books: PublishedBooks) -> None:
        body = books.body("PHB")
        assert body is not None
        chapter = body.section(_combat(books))
        assert chapter is not None
        found = {name: (depth, anchor) for depth, name, anchor in chapter_headings(chapter)}
        assert found["Making an Attack"][0] == 1
        assert found["Grappling"] == (2, "h-grappling")

    def test_the_reference_screens(self, corpus: Path) -> None:
        screens = ReferenceScreens(corpus)
        dm = screens.body("dm")
        quick = screens.body("quick")
        assert dm is not None and [c.name for c in dm.sections][:2] == [
            "Running the Game",
            "Combat",
        ]
        assert quick is not None and "Playing the Game" in [c.name for c in quick.sections]
        assert screens.body("nope") is None

    def test_anchors(self) -> None:
        assert heading_anchor("Grappling") == "h-grappling"
        assert heading_anchor("Moving around Other Creatures") == "h-moving-around-other-creatures"


@pytest.fixture
async def client(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="Grey Reed", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry, world.id


async def _login(ac: AsyncClient, user: str) -> None:
    await ac.post("/login", data={"username": user, "password": PASSWORD})


class TestPages:
    async def test_a_player_reads_the_combat_chapter(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world_id = client
        await _login(ac, "alice")
        assert 'href="/books"' in (await ac.get("/play")).text  # in the navigation
        listing = (await ac.get("/books", params={"world": world_id})).text
        assert "Player&#39;s Handbook (2014)" in listing
        assert "Player&#39;s Handbook (2024)" not in listing  # not among this world's books
        contents = (await ac.get("/books/PHB", params={"world": world_id})).text
        index = _combat(registry.rulebooks)
        assert f"/books/PHB/{index}?world={world_id}#h-making-an-attack" in contents
        chapter = (await ac.get(f"/books/PHB/{index}", params={"world": world_id})).text
        assert "<h1>Combat</h1>" in chapter and 'id="h-grappling"' in chapter
        assert 'class="ref' in chapter  # its links resolve
        assert f"/books/PHB/{index + 1}" in chapter  # the next chapter

    async def test_missing_books_and_chapters(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, _registry, _world_id = client
        await _login(ac, "alice")
        assert (await ac.get("/books/NOPE")).status_code == 404
        assert (await ac.get("/books/PHB/999")).status_code == 404

    async def test_search_finds_a_heading(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world_id = client
        result = registry.search.search(
            "grappling", SourceSet(sources=["PHB", "MM", "DMG"]), group="books"
        )
        urls = [hit.row.url for g in result.groups for hit in g.hits]
        assert f"/books/PHB/{_combat(registry.rulebooks)}#h-grappling" in urls
        await _login(ac, "dm")
        page = (
            await ac.get("/search", params={"q": "grappling", "group": "books", "world": world_id})
        ).text
        assert "#h-grappling" in page and "Player&#39;s Handbook (2014) · Combat" in page

    async def test_the_screens_for_everyone(
        self, client: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, world_id = client
        await _login(ac, "alice")
        listing = (await ac.get("/books", params={"world": world_id})).text
        assert f"/screen?ref=quick&amp;world={world_id}" in listing
        quick = (await ac.get("/screen", params={"ref": "quick", "world": world_id})).text
        assert "Quick reference" in quick and 'id="h-conditions"' in quick
        assert 'aria-current="page"' in quick and 'class="ref' in quick
        assert "/screen?ref=dm" not in listing  # the DM screen is the gamemaster's
        assert (await ac.get("/screen", params={"ref": "dm"})).status_code == 404
        await ac.post("/logout")
        await _login(ac, "dm")
        dm = (await ac.get("/screen", params={"ref": "dm"})).text
        assert "DM screen" in dm and 'id="h-combat"' in dm
        assert "2014 books" not in dm  # no world: nothing to warn about
        assert (await ac.get("/screen", params={"ref": "nope"})).status_code == 404
        newer = registry.gamestate.worlds.create(
            World(name="2024", source_set=SourceSet(sources=["XPHB", "XMM", "XDMG"]))
        )
        dm2024 = (await ac.get("/screen", params={"ref": "dm", "world": newer.id})).text
        assert "made from the 2014 books" in dm2024
