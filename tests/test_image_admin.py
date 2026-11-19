"""The image dialog: frames kept beside the path, never cut into the picture, and the
world's pictures to pick from (app/services/image_effects.py, media.py, image_pages.py)."""

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
from app.services import image_effects, media
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from app.ui.library_links import cover

PASSWORD = "correct horse"


def _png(size: tuple[int, int] = (200, 100)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(out, "PNG")
    return out.getvalue()


def test_a_frame_is_written_beside_the_path_and_rendered_from_the_original() -> None:
    frame = image_effects.frame_word(0.5, 0.0, 0.5, 1.0)
    assert frame == "crop:0.5000_0.0000_0.5000_1.0000"
    reference = image_effects.spec("media/inn.png", frame, ["torn"])
    assert reference == "media/inn.png|crop:0.5000_0.0000_0.5000_1.0000 torn"
    path, effects = image_effects.split(reference)
    assert path == "media/inn.png" and image_effects.frame_of(effects) == (0.5, 0.0, 0.5, 1.0)
    # The right half of a 200×100 picture: 100×100, cut from the original.
    with Image.open(io.BytesIO(_png())) as original:
        shown = image_effects.apply(original, (frame,), "seed")
    assert shown.size == (100, 100)
    # A frame the form sent is made canonical; anything else is no frame.
    assert image_effects.framed("crop:0.1_0.2_0.3_0.4") == "crop:0.1000_0.2000_0.3000_0.4000"
    assert image_effects.framed("crop:2_0_1_1") == "" and image_effects.framed("torn") == ""
    # A framed cover takes the frame's shape instead of the banner.
    framed_cover = str(cover("w1", reference, ""))
    assert (
        "banner" not in framed_cover and "fx=crop:0.5000_0.0000_0.5000_1.0000,torn" in framed_cover
    )


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


def test_the_worlds_pictures_as_a_record_writes_them(registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    adventure = registry.adventures.create(world.id, "The Drowned Bell")
    registry.adventures.upload(world.id, "map.png", _png(), as_cover=False)
    registry.adventures.upload(adventure.id, "bell.png", _png(), as_cover=False)
    store = registry.gamestate.prose
    pictures = media.world_images(store, adventure.id)
    assert sorted(p.name for p in pictures) == ["bell.png", "map.png"]
    world_map = next(p for p in pictures if p.name == "map.png")
    seen = media.seen_from(store, adventure.id, world_map.path)
    assert seen is not None and seen.endswith("media/map.png")
    assert media.resolve(store, adventure.id, seen) is not None


async def test_the_dialog_sets_a_framed_cover_and_title_image(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    adventure = registry.adventures.create(world.id, "The Drowned Bell")
    registry.adventures.upload(world.id, "map.png", _png(), as_cover=False)
    # The browser lists the world's pictures, as the adventure writes them.
    browser = (await gm.get(f"/gm/images/{adventure.id}")).text
    assert "map.png" in browser and 'class="img-pick"' in browser
    picked = media.seen_from(
        registry.gamestate.prose,
        adventure.id,
        media.world_images(registry.gamestate.prose, adventure.id)[0].path,
    )
    assert picked is not None and f'data-path="{picked}"' in browser
    # A picture of the world as the adventure's cover, framed and torn.
    sent = await gm.post(
        f"/gm/media/{adventure.id}/frame",
        data={
            "path": picked,
            "crop": "crop:0.25_0_0.5_1",
            "effects": ["torn"],
            "next": f"/gm/adventures/{adventure.id}",
        },
    )
    assert sent.status_code == 303
    stored = registry.gamestate.adventures.get(adventure.id).cover
    assert stored == f"{picked}|crop:0.2500_0.0000_0.5000_1.0000 torn"
    page = (await gm.get(f"/gm/adventures/{adventure.id}/edit")).text
    assert 'data-frame="0.2500,0.0000,0.5000,1.0000"' in page  # to be framed again
    # An upload with a frame as an encounter's title image.
    encounter = registry.encounters.create("Ambush", world.id)
    await gm.post(
        f"/gm/title-image/encounter/{encounter.id}",
        data={"crop": "crop:0_0_1_0.3", "next": "/gm/encounters"},
        files={"image": ("ambush.png", _png(), "image/png")},
    )
    title = registry.gamestate.encounters.get(encounter.id).cover
    assert title is not None and title.endswith("|crop:0.0000_0.0000_1.0000_0.3000")
    # Or a picture the world has, picked in the browser.
    await gm.post(
        f"/gm/title-image/encounter/{encounter.id}",
        data={"path": "media/map.png", "next": "/gm/encounters"},
    )
    assert registry.gamestate.encounters.get(encounter.id).cover == "media/map.png"
    # The original is never touched; the framed rendering is served from the cache.
    served = await gm.get(f"/media/{world.id}/media/map.png?fx=crop:0.5_0_0.5_1")
    assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(served.content)) as shown:
        assert shown.size == (100, 100)
    # The browser's grid shows previews, shrunk; a width it does not offer is the original.
    assert f"/media/{world.id}/media/map.png?w=320" in browser
    big = registry.adventures.upload(world.id, "big.png", _png((1600, 800)), as_cover=False)
    preview = await gm.get(f"/media/{world.id}/{big}?w=320")
    with Image.open(io.BytesIO(preview.content)) as shown:
        assert preview.headers["content-type"] == "image/webp" and shown.size == (320, 160)
    other = await gm.get(f"/media/{world.id}/{big}?w=50")
    assert other.headers["content-type"] == "image/png"


async def test_the_editor_gets_the_dialog(gm: AsyncClient, registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    adventure = registry.adventures.create(world.id, "The Drowned Bell")
    chapter = registry.adventures.create_chapter(adventure.id, "Fog")
    page = (await gm.get(f"/gm/chapters/{chapter.id}/edit")).text
    assert 'data-mode="insert"' in page and 'data-for="f-body"' in page
    assert f'data-browse="/gm/images/{chapter.id}"' in page
    # Outside the chapter's form: its fields are not saved with the text.
    form = page.split('<form method="post" action="/gm/chapters/', 1)[1].split("</form>", 1)[0]
    assert "img-insert" not in form
