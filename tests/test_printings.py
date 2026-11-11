"""A declared printing that supersession set aside is offered, never dropped (TECHSTACK.md §6c)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.services.i18n import LANG_COOKIE
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> StoreRegistry:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    return registry


def _world(registry: StoreRegistry, *books: str) -> str:
    world = registry.gamestate.worlds.create(
        World(name="W", source_set=SourceSet(sources=list(books)))
    )
    return world.id


class TestService:
    def test_both_editions_offer_the_other(self, registry: StoreRegistry) -> None:
        both = _world(registry, "PHB", "XPHB")
        refs = registry.references
        chosen = refs.other_printings(refs.get("spell", "XPHB", "Fireball", both))
        assert [(p.source, p.used) for p in chosen] == [("PHB", False)]
        aside = refs.other_printings(refs.get("spell", "PHB", "Fireball", both))
        assert [(p.source, p.used) for p in aside] == [("XPHB", True)]

    def test_one_edition_has_nothing_to_offer(self, registry: StoreRegistry) -> None:
        only = _world(registry, "PHB")
        refs = registry.references
        assert refs.other_printings(refs.get("spell", "PHB", "Fireball", only)) == []


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac


class TestPages:
    async def test_preview_and_page_offer_the_other_printing(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        both = _world(registry, "PHB", "XPHB")
        preview = (
            await client.get("/ref/spell/XPHB/Fireball/preview", params={"world": both})
        ).text
        assert "PHB version" in preview and f"/ref/spell/PHB/Fireball?world={both}" in preview
        page = (await client.get("/ref/spell/PHB/Fireball", params={"world": both})).text
        assert "Your world uses:" in page and f"/ref/spell/XPHB/Fireball?world={both}" in page
        client.cookies.set(LANG_COOKIE, "de")
        await client.post("/language", data={"lang": "de", "next": "/"})
        german = (await client.get("/ref/spell/XPHB/Fireball/preview", params={"world": both})).text
        assert "PHB-Fassung" in german
        only = _world(registry, "PHB")
        plain = (await client.get("/ref/spell/PHB/Fireball", params={"world": only})).text
        assert "ref-printings" not in plain
