"""Item groups and the books' tables as entries: their tags resolve, their pages show what
they stand for (TECHSTACK.md §6b) — against the real corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.services.i18n import UiCatalogue
from app.store.published import Section, table_anchor
from app.store.registry import StoreRegistry
from app.ui.tags import localized_context, ref_url, render

PASSWORD = "correct horse battery"


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> StoreRegistry:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    return registry


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac


def _html(registry: StoreRegistry, text: str, *books: str) -> str:
    world = (
        registry.gamestate.worlds.create(World(name="W", source_set=SourceSet(sources=list(books))))
        if books
        else None
    )
    ctx = localized_context(
        UiCatalogue().translator("en"),
        resolver=registry.compendium.resolver(),
        source_set=world.source_set if world else None,
        world_id=world.id if world else None,
    )
    return "".join(str(n) for n in render(text, ctx))


class TestItemGroups:
    def test_a_group_tag_links_to_the_group(self, registry: StoreRegistry) -> None:
        html = _html(registry, "{@item Artisan's Tools|XPHB}")
        assert 'href="/ref/itemGroup/XPHB/Artisan%27s%20Tools"' in html
        # A bare name resolves through the world's books, as any item does.
        assert "/ref/itemGroup/PHB/" in _html(registry, "{@item holy symbol}", "PHB", "MM")

    async def test_its_page_lists_its_items(self, client: AsyncClient) -> None:
        page = await client.get(ref_url("itemGroup", "Artisan's Tools", "XPHB"))
        assert page.status_code == 200
        assert "Item group" in page.text and "17 items" in page.text
        assert "/ref/item/XPHB/Alchemist%27s%20Supplies" in page.text or (
            "Alchemist%27s%20Supplies" in page.text
        )

    async def test_a_group_with_its_own_table_lists_nothing_twice(
        self, client: AsyncClient
    ) -> None:
        page = await client.get(ref_url("itemGroup", "Belt of Giant Strength", "XDMG"))
        assert page.status_code == 200 and "item-group-list" not in page.text


class TestBookTables:
    def test_the_dmg_artifact_tables_resolve(self, registry: StoreRegistry) -> None:
        html = _html(registry, "{@table Artifact Properties; Minor Beneficial Properties|dmg}")
        assert "/ref/table/DMG/" in html and "missing" not in html

    def test_where_it_is_printed(self, registry: StoreRegistry) -> None:
        entry = registry.references.get(
            "table", "DMG", "Artifact Properties; Minor Beneficial Properties", None
        )
        assert entry.printed_in is not None
        url, book, chapter = entry.printed_in
        assert url.startswith("/books/DMG/7#h-") and book == "Dungeon Master's Guide (2014)"
        assert chapter == "Treasure"

    async def test_the_page_shows_the_table_and_the_chapter_has_its_anchor(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        name = "Artifact Properties; Minor Beneficial Properties"
        page = await client.get(ref_url("table", name, "DMG"))
        assert page.status_code == 200
        assert "<table" in page.text and "From Dungeon Master" in page.text
        url = registry.references.get("table", "DMG", name, None).printed_in
        assert url is not None
        path, anchor = url[0].split("#")
        chapter = await client.get(path)
        assert f'id="{anchor}"' in chapter.text

        preview = await client.get(ref_url("table", name, "DMG") + "/preview")
        assert preview.status_code == 200 and "<table" in preview.text

    def test_an_adventure_table_has_no_book_link(self, registry: StoreRegistry) -> None:
        entry = registry.references.get("table", "SKT", "Items in a Giant's Bag", None)
        assert entry.printed_in is None and entry.data["rows"]


class TestTableAnchor:
    def test_the_heading_above_or_the_chapter_top(self) -> None:
        table = {"type": "table", "caption": "Loot", "rows": [["1", "x"]]}
        section = Section(
            "0",
            "Chapter",
            {
                "entries": [
                    {"type": "table", "caption": "Top", "rows": []},
                    {
                        "type": "entries",
                        "name": "Treasure",
                        "entries": [
                            {"type": "entries", "name": "Treasure", "entries": ["text", table]},
                        ],
                    },
                ]
            },
        )
        assert table_anchor(section, "Loot") == "h-treasure-2"
        assert table_anchor(section, "Top") == ""
        assert table_anchor(section, "Absent") is None
