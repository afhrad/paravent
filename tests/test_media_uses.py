"""Where a world's pictures are used; renaming one with every use, deleting an unused one
(app/services/media_uses.py)."""

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
from app.services import media
from app.services.media_uses import MediaUseError
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), (40, 80, 120)).save(out, "PNG")
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


def _table(registry: StoreRegistry) -> dict[str, str]:
    """A world with a picture used five ways, and one nobody uses."""
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    adventure = registry.adventures.create(world.id, "The Drowned Bell")
    chapter = registry.adventures.create_chapter(adventure.id, "Fog")
    map_ = registry.adventures.upload(world.id, "map.png", _png(), as_cover=False)
    registry.adventures.upload(world.id, "spare.png", _png(), as_cover=False)
    store = registry.gamestate
    store.worlds.update(world.id, cover=f"{map_}|torn")  # a field, with an effect
    from_chapter = media.seen_from(store.prose, chapter.id, map_)
    assert from_chapter is not None
    store.chapters.update(
        chapter.id,
        body=f"Fog.\n\n![[map.png|300 sepia]]\n\nAgain: ![The map]({from_chapter})\n",
    )
    npc = registry.npcs.create("Maren", f"w:{world.id}", world.id)
    store.npcs.update(npc.id, portrait=map_, notes="Her map: ![[map.png]]")
    registry.media_tags.set(world.id, "media/map.png", ["river"])
    return {"world": world.id, "chapter": chapter.id, "npc": npc.id, "map": "media/map.png"}


def test_every_use_is_found_and_follows_a_rename(registry: StoreRegistry) -> None:
    t = _table(registry)
    uses = registry.media_uses
    found = uses.uses(t["world"])
    kinds = sorted((u.owner, u.kind) for u in found[t["map"]])
    assert kinds == [("chapter", "text"), ("npc", "portrait"), ("npc", "text"), ("world", "cover")]
    assert "media/spare.png" not in found  # nobody uses it
    # Renamed: the file, its tags and every use, frames and effects kept.
    new = uses.rename(t["world"], t["map"], "Old Ferry Map")
    assert new == "media/Old Ferry Map.png"
    store = registry.gamestate
    folder = store.prose.world_folder(t["world"])
    assert folder is not None and (folder / new).is_file() and not (folder / t["map"]).exists()
    assert store.worlds.get(t["world"]).cover == "media/Old Ferry Map.png|torn"
    body = store.chapters.get(t["chapter"]).body
    assert "![[Old Ferry Map.png|300 sepia]]" in body and "Old Ferry Map.png)" in body
    assert "map.png" not in body.replace("Old Ferry Map.png", "")
    npc = store.npcs.get(t["npc"])
    assert (
        npc.portrait == "media/Old Ferry Map.png" and npc.notes == "Her map: ![[Old Ferry Map.png]]"
    )
    assert registry.media_tags.of(t["world"], new) == ["river"]
    assert len(uses.uses(t["world"])[new]) == 4  # still every use
    # A name taken, or not a name, is refused.
    with pytest.raises(MediaUseError):
        uses.rename(t["world"], new, "spare")
    with pytest.raises(MediaUseError):
        uses.rename(t["world"], new, "../escape")


def test_only_an_unused_picture_is_deleted(registry: StoreRegistry) -> None:
    t = _table(registry)
    uses = registry.media_uses
    with pytest.raises(MediaUseError):
        uses.delete(t["world"], t["map"])
    folder = registry.gamestate.prose.world_folder(t["world"])
    assert folder is not None and (folder / t["map"]).is_file()
    uses.delete(t["world"], "media/spare.png")
    assert not (folder / "media/spare.png").exists()
    with pytest.raises(MediaUseError):
        uses.delete(t["world"], "media/spare.png")  # gone


async def test_the_browser_shows_uses_and_renames_and_deletes(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    t = _table(registry)
    url = f"/gm/images/{t['chapter']}"
    page = (await gm.get(url)).text
    assert 'class="img-pick-used"' in page and "4×" in page  # the map, used four times
    assert 'class="img-pick-used is-unused"' in page  # the spare
    assert 'data-rename-url="/gm/images/' in page and "Chapter “Fog” · in the text" in page
    # Paths as the chapter writes them.
    from_chapter = media.seen_from(registry.gamestate.prose, t["chapter"], "media/spare.png")
    refused = await gm.post(
        f"{url}/delete",
        data={"path": media.seen_from(registry.gamestate.prose, t["chapter"], t["map"])},
    )
    assert "still in use" in refused.text
    gone = await gm.post(f"{url}/delete", data={"path": from_chapter})
    assert gone.status_code == 200 and "spare.png" not in gone.text
    renamed = await gm.post(
        f"{url}/rename",
        data={
            "path": media.seen_from(registry.gamestate.prose, t["chapter"], t["map"]),
            "name": "ferry",
        },
    )
    assert "ferry.png" in renamed.text
    assert registry.gamestate.npcs.get(t["npc"]).portrait == "media/ferry.png"


def test_a_tag_is_renamed_merged_and_removed_everywhere(registry: StoreRegistry) -> None:
    t = _table(registry)
    tags = registry.media_tags
    tags.set(t["world"], "media/spare.png", ["river", "ruin"])
    assert tags.rename_tag(t["world"], "river", "Water") == 2
    assert tags.vocabulary(t["world"]) == {"water": 2, "ruin": 1}
    tags.rename_tag(t["world"], "ruin", "water")  # merged: one tag, once
    assert tags.of(t["world"], "media/spare.png") == ["water"]
    assert tags.rename_tag(t["world"], "water", "") == 2  # removed, the pictures stay
    assert tags.all(t["world"]) == {}
    assert registry.gamestate.prose.world_folder(t["world"]) is not None


async def test_the_browser_lists_and_renames_tags(gm: AsyncClient, registry: StoreRegistry) -> None:
    t = _table(registry)
    url = f"/gm/images/{t['chapter']}"
    page = (await gm.get(url)).text
    assert 'class="img-tagrow" data-tag="river"' in page and "1 picture" in page
    renamed = (await gm.post(f"{url}/tags/rename", data={"old": "river", "new": "ford"})).text
    assert 'data-tag="ford"' in renamed and 'data-tag="river"' not in renamed
