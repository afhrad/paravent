"""One search across the reference set, and the ⌘K palette and page that show it."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.services.search import SearchService, flatten, match_tier
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def service(loaded_compendium: CompendiumStore) -> SearchService:
    return SearchService(loaded_compendium)


class TestMatchTier:
    @pytest.mark.parametrize(
        ("name", "needle", "tier"),
        [
            ("Fireball", "fireball", 0),
            ("Fireball", "fire", 1),
            ("Wall of Fire", "fire", 2),
            ("Delayed Blast Fireball", "fire", 2),
            ("Hobgoblin", "goblin", 3),
            ("Goblin", "orc", None),
        ],
    )
    def test_tiers(self, name: str, needle: str, tier: int | None) -> None:
        assert match_tier(name, needle) == tier


class TestSearch:
    def test_an_empty_query_finds_nothing(self, service: SearchService) -> None:
        assert service.search("   ").groups == ()

    def test_no_category_is_chosen_first(self, service: SearchService) -> None:
        result = service.search("fire")
        keys = {g.key for g in result.groups}
        assert {"spells", "bestiary", "items"} <= keys

    def test_the_best_group_comes_first(self, service: SearchService) -> None:
        goblin = service.search("goblin", SourceSet(sources=["PHB", "MM"]))
        assert goblin.groups[0].key == "bestiary"
        assert goblin.groups[0].hits[0].row.name == "Goblin"

    def test_exact_beats_prefix_beats_inside(self, service: SearchService) -> None:
        spells = next(g for g in service.search("fireball").groups if g.key == "spells")
        names = [h.row.name for h in spells.hits]
        assert names[0] == "Fireball"
        assert names.index("Fireball") < names.index("Delayed Blast Fireball")

    def test_a_group_narrows_and_shows_more(self, service: SearchService) -> None:
        everything = service.search("wand")
        items_only = service.search("wand", group="items")
        assert [g.key for g in items_only.groups] == ["items"]
        shown = len(items_only.groups[0].hits)
        assert shown > len(next(g for g in everything.groups if g.key == "items").hits)

    def test_an_unknown_group_means_all(self, service: SearchService) -> None:
        assert service.search("fire", group="nonsense").group is None

    def test_features_and_subclasses_are_not_searched(self, service: SearchService) -> None:
        """They repeat across classes and levels; they are reached through their class."""
        hits = flatten(service.search("action surge").groups)
        hits += flatten(service.search("battle master").groups)
        assert not {h.row.category for h in hits} & {
            "classFeature",
            "subclassFeature",
            "subclass",
        }


class TestSourceSet:
    def test_only_declared_books(self, service: SearchService) -> None:
        books = SourceSet(sources=["PHB", "MM"])
        hits = flatten(service.search("goblin", books).groups)
        assert hits and {h.row.source for h in hits} <= {"PHB", "MM"}

    def test_what_is_hidden_is_counted(self, service: SearchService) -> None:
        result = service.search("goblin", SourceSet(sources=["PHB", "MM"]))
        assert result.restricted and result.outside > 0

    def test_nothing_is_hidden_without_a_declaration(self, service: SearchService) -> None:
        result = service.search("goblin")
        assert not result.restricted and result.outside == 0

    def test_one_printing_per_entry(self, service: SearchService) -> None:
        both = SourceSet(sources=["PHB", "XPHB"])
        spells = next(g for g in service.search("fireball", both).groups if g.key == "spells")
        assert [h.row.source for h in spells.hits if h.row.name == "Fireball"] == ["XPHB"]

    def test_all_books_shows_every_printing(self, service: SearchService) -> None:
        """The escape from the world's books skips supersession too."""
        result = service.search("fireball", SourceSet(sources=["PHB"]), all_books=True)
        spells = next(g for g in result.groups if g.key == "spells")
        assert {h.row.source for h in spells.hits if h.row.name == "Fireball"} >= {
            "PHB",
            "XPHB",
        }
        assert result.outside == 0


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestRoutes:
    async def test_search_page_and_fragment(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Grey Reed", source_set=SourceSet(sources=["PHB", "MM"]))
        )

        page = await ac.get("/search", params={"q": "goblin", "world": world.id})
        assert page.status_code == 200
        text = page.text
        assert "<html" in text and 'class="palette-form dialog"' in text
        assert 'id="palette"' not in text  # the page is the palette; no second layer
        assert f'href="/ref/monster/MM/Goblin?world={world.id}"' in text
        assert "all=1" in text  # the escape to every imported book

        fragment = await ac.get(
            "/search", params={"q": "goblin", "world": world.id, "partial": "1"}
        )
        assert "<html" not in fragment.text
        assert fragment.text.startswith('<div id="palette-results"')
        assert 'role="option"' in fragment.text

        everything = await ac.get(
            "/search", params={"q": "goblin", "world": world.id, "all": "1", "partial": "1"}
        )
        assert "/ref/monster/XMM/" in everything.text

    async def test_every_signed_in_page_has_the_palette(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        text = (await ac.get("/gm")).text
        assert 'id="palette"' in text and "data-palette-open" in text
        assert "/static/js/palette.js" in text

    async def test_an_empty_query_shows_the_hint(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        text = (await ac.get("/search", params={"partial": "1"})).text
        assert 'class="res-empty"' in text and "res-0" not in text

    async def test_a_stale_world_is_ignored(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        text = (await ac.get("/search", params={"q": "goblin", "world": "gone"})).text
        assert 'href="/ref/monster/XMM/Goblin%20Boss"' in text  # no stale world on links
        assert "?world=gone" not in text.split('id="palette-results"', 1)[1]

    async def test_search_needs_a_login(self, tmp_path: Path, corpus: Path) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/search", params={"q": "goblin"})
        assert response.status_code in (302, 303, 307)
