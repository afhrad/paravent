"""Title images of places, encounters, quests and factions (app/services/title_images.py),
and tokens kept for what stands on a table (documentation/TOKENS.md)."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.title_images import TitleImageError
from app.services.tokens import OWN_KINDS, PICTURE_KINDS
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 30), (40, 80, 120)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    return registry


@pytest.fixture
async def gm(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client


def test_tokens_are_for_what_stands_on_a_table() -> None:
    assert {"npc", "character", "creature", "treasure"} == OWN_KINDS
    assert {"npc", "creature"} == PICTURE_KINDS


def test_every_kind_takes_and_drops_a_title_image(registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    place = registry.locations.create("Landing", f"w:{world.id}", world.id)
    encounter = registry.encounters.create("Ambush", world.id)
    quest = registry.quests.save(world.id, {"name": "Find the bell"})
    faction = registry.factions.create(world.id, "Ferrymen")
    images = registry.title_images
    for kind, ident in (
        ("place", place.id),
        ("encounter", encounter.id),
        ("quest", quest.id),
        ("faction", faction.id),
    ):
        path = images.set(kind, ident, "banner.png", _png())
        assert path.startswith("media/") and images.of(kind, ident) == (
            images.owner(kind, ident),
            path,
        )
        images.clear(kind, ident)
        assert images.of(kind, ident) is None
    # An encounter planned ahead of any world has no folder for one.
    loose = registry.encounters.create("Someday", None)
    with pytest.raises(TitleImageError):
        images.set("encounter", loose.id, "banner.png", _png())


async def test_dropped_on_its_panel_and_drawn(gm: AsyncClient, registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    encounter = registry.encounters.create("Ambush", world.id)
    page = (await gm.get(f"/gm/encounters/{encounter.id}")).text
    assert 'class="cover' not in page
    # Changed in its own panel beside the plan, the builder being its edit mode.
    side = page.split('class="enc-side"', 1)[1]
    assert f'id="title-encounter-{encounter.id}"' in side
    assert f'action="/tokens/encounter/{encounter.id}"' not in page  # no token panel
    sent = await gm.post(
        f"/gm/title-image/encounter/{encounter.id}",
        data={"next": f"/gm/encounters/{encounter.id}#title"},
        files={"image": ("banner.png", _png(), "image/png")},
    )
    assert sent.status_code == 303 and "title_error" not in sent.headers["location"]
    page = (await gm.get(f"/gm/encounters/{encounter.id}")).text
    assert 'class="cover' in page and "img-current" in page  # the panel shows it as set
    listed = (await gm.get("/gm/encounters")).text
    assert "title-thumb" in listed
    # Not a picture: back with the reason; nothing chosen likewise.
    bad = await gm.post(
        f"/gm/title-image/encounter/{encounter.id}",
        data={"next": "/gm/encounters"},
        files={"image": ("notes.png", b"not an image", "image/png")},
    )
    assert "title_error=image" in bad.headers["location"]
    none = await gm.post(f"/gm/title-image/quest/{encounter.id}", data={})
    assert none.status_code == 404  # no such quest
    # The token route no longer takes a place or an encounter.
    refused = await gm.post(
        f"/tokens/encounter/{encounter.id}", files={"token": ("t.png", _png(), "image/png")}
    )
    assert refused.status_code == 404


async def test_pictures_are_changed_only_in_an_edit_mode(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    landing = registry.locations.create("Grey Reed Landing", f"w:{world.id}", world.id)
    shown = (await gm.get(f"/gm/places/{landing.id}")).text
    assert "img-editor" not in shown and f"/gm/title-image/place/{landing.id}" not in shown
    edited = (await gm.get(f"/gm/places/{landing.id}/edit")).text
    assert "img-editor" in edited and f"/gm/title-image/place/{landing.id}" in edited
    # A treasure's page is its own builder: its token has a panel beside it.
    hoard = registry.treasure.create("Hoard", f"w:{world.id}", world.id)
    page = (await gm.get(f"/gm/treasure/{hoard.id}")).text
    side = page.split('class="tr-side"', 1)[1]
    assert f'action="/tokens/treasure/{hoard.id}"' in side
