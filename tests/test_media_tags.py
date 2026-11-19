"""Tags on a world's pictures, for the image browser (app/services/media_tags.py)."""

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
from app.services.media_tags import TAGS_FILE, clean_tag, clean_tags
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _png(size: tuple[int, int] = (200, 100)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(out, "PNG")
    return out.getvalue()


def test_a_tag_is_kept_tidy() -> None:
    assert clean_tag("  Night   Market! ") == "night market"
    assert clean_tag("Fähre") == "fähre" and clean_tag("!!") == ""
    assert len(clean_tag("x" * 80)) == 40
    assert clean_tags(["Tavern, night", "night", " "]) == ["night", "tavern"]


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


def test_tags_set_changed_and_kept_beside_the_pictures(registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    adventure = registry.adventures.create(world.id, "The Drowned Bell")
    landing = registry.adventures.upload(world.id, "landing.png", _png(), as_cover=False)
    bell = registry.adventures.upload(adventure.id, "bell.png", _png((90, 100)), as_cover=False)
    tags = registry.media_tags
    world_landing = tags.world_path(world.id, landing)
    world_bell = tags.world_path(adventure.id, bell)
    assert world_landing == "media/landing.png" and world_bell and world_bell.endswith("bell.png")
    # The same picture as the adventure writes it maps to the same key.
    from app.services import media

    from_adventure = media.seen_from(registry.gamestate.prose, adventure.id, world_landing)
    assert from_adventure and tags.world_path(adventure.id, from_adventure) == world_landing
    tags.set(world.id, world_landing, ["River, Fog"])
    tags.change(world.id, [world_landing, world_bell], add=["night"], remove=["fog"])
    assert tags.of(world.id, world_landing) == ["night", "river"]
    assert tags.of(adventure.id, world_bell) == ["night"]
    assert tags.vocabulary(world.id) == {"night": 2, "river": 1}
    file = registry.gamestate.prose.world_folder(world.id) / "media" / TAGS_FILE
    assert file.is_file()
    # A picture gone takes its tags with it at the next write.
    (registry.gamestate.prose.world_folder(world.id) / world_landing).unlink()
    tags.set(world.id, world_bell, ["bell"])
    assert world_landing not in tags.all(world.id)
    # A file a hand edit broke is left alone, and reads as no tags.
    file.write_text("{ not json", encoding="utf-8")
    assert tags.all(world.id) == {}
    tags.set(world.id, world_bell, ["x"])
    assert file.read_text(encoding="utf-8") == "{ not json"


async def test_the_browser_finds_by_tag_and_tags_together(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    adventure = registry.adventures.create(world.id, "The Drowned Bell")
    registry.adventures.upload(world.id, "landing.png", _png(), as_cover=False)
    registry.adventures.upload(world.id, "tower.png", _png((80, 200)), as_cover=False)
    url = f"/gm/images/{adventure.id}"
    page = (await gm.get(url)).text
    assert "landscape" in page and "portrait" in page  # the automatic shapes
    paths = [p for p in page.split('data-path="')[1:]]
    landing, tower = (p.split('"', 1)[0] for p in sorted(paths))
    # Tag both from the selection, untag one.
    together = await gm.post(f"{url}/tags", data={"path": [landing, tower], "add": "River"})
    assert together.status_code == 200 and 'data-tag="river"' in together.text
    await gm.post(f"{url}/tags", data={"path": [tower], "remove": "river"})
    # One picture's tags replaced, as the dialog saves them.
    replaced = await gm.post(f"{url}/tags", data={"path": [tower], "tags": "watch, ruin"})
    assert replaced.status_code == 204
    page = (await gm.get(url)).text
    assert 'data-tags="river"' in page and 'data-tags="ruin, watch"' in page
    assert ">river<" in page and " 1</small>" in page  # the strip with its counts
    # The dialog's cover keeps the tags typed for its picture.
    await gm.post(
        f"/gm/media/{adventure.id}/frame",
        data={"path": landing, "tags": "harbour", "next": f"/gm/adventures/{adventure.id}"},
    )
    assert registry.media_tags.of(world.id, "media/landing.png") == ["harbour"]
    assert (await gm.post(f"{url}/tags", data={"path": ["../../../etc"]})).status_code == 404
