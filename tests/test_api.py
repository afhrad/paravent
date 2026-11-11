"""API tests for the compendium routes.

These run against a temporary data directory rather than the real corpus, so the suite
stays fast and works on a clean checkout.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.store.registry import StoreRegistry


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """A small reference corpus plus an empty library directory."""
    reference = tmp_path / "reference"
    (reference / "bestiary").mkdir(parents=True)
    (reference / "spells").mkdir(parents=True)
    (reference / "bestiary" / "bestiary-mm.json").write_text(
        json.dumps(
            {
                "monster": [
                    {"name": "Goblin", "source": "MM", "cr": "1/4", "size": ["S"]},
                    {"name": "Hobgoblin", "source": "MM", "cr": "1/2", "size": ["M"]},
                ]
            }
        )
    )
    (reference / "spells" / "spells-phb.json").write_text(
        json.dumps({"spell": [{"name": "Fireball", "source": "PHB", "level": 3, "school": "V"}]})
    )
    return tmp_path


@pytest.fixture
async def client(data_root: Path) -> AsyncIterator[AsyncClient]:
    """An app wired to the temporary data directory, with the watcher off."""
    settings = Settings(
        reference_dir=data_root / "reference",
        library_dir=data_root / "library",
        watch_files=False,
    )
    app = create_app()
    registry = StoreRegistry(settings)
    registry.load()
    app.state.stores = registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealth:
    async def test_health_reports_loaded_entries(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["compendium_entries"] == 3
        assert body["compendium_ok"] is True


class TestStatus:
    async def test_status_reports_the_load(self, client: AsyncClient) -> None:
        body = (await client.get("/api/compendium/status")).json()
        assert body["ok"] is True
        assert body["loaded"]["monster"] == 2
        assert body["errors"] == []
        assert body["unknown_copy_modes"] == []


class TestCategories:
    async def test_lists_categories_with_counts(self, client: AsyncClient) -> None:
        body = (await client.get("/api/compendium/categories")).json()
        by_name = {c["name"]: c for c in body}
        assert by_name["monster"]["count"] == 2
        assert by_name["monster"]["sources"] == ["MM"]


class TestSearch:
    async def test_search_within_a_category(self, client: AsyncClient) -> None:
        body = (await client.get("/api/compendium/monster", params={"q": "goblin"})).json()
        assert body["total"] == 2
        assert body["results"][0]["name"] == "Goblin"

    async def test_results_carry_their_source(self, client: AsyncClient) -> None:
        """The same name appears in several books, so a result without source is ambiguous."""
        body = (await client.get("/api/compendium/monster", params={"q": "goblin"})).json()
        assert all(r["source"] for r in body["results"])

    async def test_unknown_category_is_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/compendium/dragonkin")).status_code == 404

    async def test_global_search_spans_categories(self, client: AsyncClient) -> None:
        body = (await client.get("/api/compendium/search", params={"q": "fire"})).json()
        assert [r["category"] for r in body["results"]] == ["spell"]

    async def test_limit_is_validated(self, client: AsyncClient) -> None:
        assert (
            await client.get("/api/compendium/monster", params={"limit": 9999})
        ).status_code == 422


class TestGetEntry:
    async def test_returns_the_full_entry(self, client: AsyncClient) -> None:
        body = (await client.get("/api/compendium/monster/goblin|MM")).json()
        assert body["name"] == "Goblin"
        assert body["cr"] == "1/4"

    async def test_missing_entry_is_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/compendium/monster/beholder|MM")
        assert response.status_code == 404
        assert "beholder" in response.json()["detail"].lower()


class TestSerialisedFieldNames:
    """The API must hand back upstream key names for the aliased fields."""

    async def test_entry_uses_json_ability_keys(self, client: AsyncClient) -> None:
        body = (await client.get("/api/compendium/monster/goblin|MM")).json()
        assert "str_" not in body
        assert "int_" not in body
