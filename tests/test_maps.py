"""The gamemaster's maps (documentation/MAPS.md, step 1): storing the original, the tile
pyramid, the scale, the remembered view, the trash and an adventure's maps sticking to
it, copies into another world, and the pages."""

from __future__ import annotations

import asyncio
import html
import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from app.models.gamestate import MapPin, Scope, World
from app.services.map_tiles import (
    MapImageError,
    build,
    level_sizes,
    max_zoom,
    needs_tiles,
    store_original,
    tile_count,
)
from app.services.maps import MapError, feet_per_px
from app.services.trash import TrashError
from app.services.world_copy import WorldCopyError
from tests.test_mcp import PASSWORD, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


def picture(width: int, height: int, kind: str = "PNG", mode: str = "RGB") -> bytes:
    """A picture of that size, as a file's bytes."""
    out = io.BytesIO()
    Image.new(mode, (width, height), (90, 120, 60)).save(out, kind)
    return out.getvalue()


async def _login(raw: httpx.AsyncClient) -> None:
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})


def _registry(facts: dict[str, Any]) -> Any:
    return facts["app"].state.stores


class TestPicture:
    def test_sizes_of_the_pyramid(self) -> None:
        assert max_zoom(256, 100) == 0
        assert max_zoom(1000, 600) == 2
        assert level_sizes(1000, 600) == [(250, 150), (500, 300), (1000, 600)]
        # 1 + 2×2 (500×300) + 4×3 (1000×600)
        assert tile_count(1000, 600) == 1 + 4 + 12
        assert not needs_tiles(4096, 3000) and needs_tiles(4097, 10)

    def test_the_original_is_kept_as_sent(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        data = picture(300, 200, "JPEG")
        stored = store_original(
            registry.gamestate.prose, world_id, "Sword Coast.jpg", io.BytesIO(data), 10**6
        )
        assert stored.path == "media/maps/sword-coast.jpg"
        assert (stored.width, stored.height) == (300, 200)
        folder = registry.gamestate.prose.folder(world_id)
        assert (folder / stored.path).read_bytes() == data
        again = store_original(
            registry.gamestate.prose, world_id, "Sword Coast.jpg", io.BytesIO(data), 10**6
        )
        assert again.path == "media/maps/sword-coast-2.jpg"

    def test_what_is_refused_leaves_nothing(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        prose = _registry(facts).gamestate.prose
        world_id = facts["world"].id
        cases = [
            ("map.gif", picture(10, 10, "GIF"), "atlas.error.type"),
            ("map.png", picture(10, 10, "JPEG"), "media.error.content"),
            ("map.png", picture(200, 200), "atlas.error.pixels"),
        ]
        for name, data, key in cases:
            with pytest.raises(MapImageError) as refused:
                store_original(prose, world_id, name, io.BytesIO(data), 30_000)
            assert refused.value.key == key
        maps = prose.folder(world_id) / "media" / "maps"
        assert not [p for p in maps.iterdir()] if maps.is_dir() else True

    def test_the_pyramid(self, tmp_path: Path) -> None:
        source = tmp_path / "big.png"
        source.write_bytes(picture(600, 300))
        target = tmp_path / "tiles"
        seen: list[tuple[int, int]] = []
        build(source, target, 10**6, lambda done, total: seen.append((done, total)))
        assert seen[-1] == (tile_count(600, 300), tile_count(600, 300))
        assert not (tmp_path / "tiles.part").exists()
        assert sorted(p.name for p in target.iterdir()) == ["0", "1", "2", "thumb.webp"]
        # Level 2 is the original: 3 × 2 tiles, the last column and row padded out.
        assert len(list((target / "2").rglob("*.webp"))) == 6
        with Image.open(target / "2" / "2" / "1.webp") as edge:
            assert edge.size == (256, 256)
            assert edge.getpixel((255, 255))[3] == 0  # outside the map: nothing
            assert edge.getpixel((10, 10))[3] == 255
        with Image.open(target / "0" / "0" / "0.webp") as top:
            assert top.size == (256, 256)
        with Image.open(target / "thumb.webp") as thumb:
            assert thumb.size == (150, 75)


class TestScale:
    def test_a_line_or_the_width(self) -> None:
        # 100 miles across 500 pixels.
        assert feet_per_px("line", 100, "mi", 2000, [0, 0, 300, 400]) == pytest.approx(1056)
        assert feet_per_px("width", 2000, "ft", 1000) == 2
        # Paravent's factors: 150 km are 100 miles, 3 m are 10 feet.
        assert feet_per_px("width", 150, "km", 5280) == pytest.approx(100)
        assert feet_per_px("width", 3, "m", 10) == pytest.approx(1)

    def test_refused(self) -> None:
        for args in (
            ("line", 100, "leagues", 10, [0, 0, 5, 5]),
            ("line", 0, "mi", 10, [0, 0, 5, 5]),
            ("line", 100, "mi", 10, [3, 3, 3, 3]),
            ("line", 100, "mi", 10, []),
        ):
            with pytest.raises(MapError):
                feet_per_px(*args)  # type: ignore[arg-type]


class TestService:
    def test_a_map_its_scale_its_view(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        service = registry.world_maps
        made = service.create(world_id, "", "the_sword-coast.png", io.BytesIO(picture(800, 400)))
        assert made.name == "the sword coast"
        assert (made.width, made.height) == (800, 400)
        scaled = service.set_scale(made.id, way="width", amount=400, unit="mi")
        assert scaled.scale is not None and scaled.scale.feet_per_px == pytest.approx(2640)
        # A sharper picture of the same map: the scale follows it.
        sharper = service.replace_image(made.id, "coast.png", io.BytesIO(picture(1600, 800)))
        assert sharper.scale is not None and sharper.scale.feet_per_px == pytest.approx(1320)
        folder = registry.gamestate.prose.folder(world_id)
        assert not (folder / made.image).exists()  # the old picture went
        assert (folder / sharper.image).exists()
        gm = registry.gamestate.users.list()[0]
        service.keep_view(gm.id, made.id, 5000, -3, 2.5)
        view = service.view(gm.id, made.id)
        assert (view.x, view.y, view.zoom) == (1600, 0, 2.5)

    def test_an_adventures_maps_stick_to_it(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        adventure = registry.adventures.create(world_id, "Fog on the River")
        service = registry.world_maps
        own = service.create(
            world_id, "Ferry", "ferry.png", io.BytesIO(picture(300, 300)), adventure.id
        )
        world_map = service.create(world_id, "Coast", "coast.png", io.BytesIO(picture(300, 300)))
        assert [m.id for m in service.of_world(world_id)] == [world_map.id]
        assert [m.id for m in service.of_adventure(adventure.id)] == [own.id]
        assert registry.gamestate.prose.folder(adventure.id) / own.image
        # Deleted, even keeping the NPCs and places: its maps go with it, never up.
        registry.adventures.delete(adventure.id)
        assert registry.gamestate.maps.find(own.id) is None
        entry = registry.trash.entry_of("map", own.id)
        assert entry is not None
        with pytest.raises(TrashError) as refused:
            registry.trash.restore(entry)
        assert refused.value.key == "trash.error.adventure"
        registry.trash.restore(registry.trash.entry_of("adventure", adventure.id))
        back = registry.gamestate.maps.find(own.id)
        assert back is not None and back.scope.adventure_id == adventure.id
        assert service.source(back) is not None  # its picture came back with the folder

    def test_the_trash_forgets_the_picture_last(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        made = registry.world_maps.create(
            world_id, "Coast", "coast.png", io.BytesIO(picture(300, 300))
        )
        path = registry.world_maps.source(made)
        registry.world_maps.delete(made.id)
        assert path.exists()  # kept to restore
        registry.trash.empty(world_id)
        assert not path.exists()


class TestCopies:
    def test_maps_travel(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        other = registry.gamestate.worlds.create(World(name="Ashfall"))
        adventure = registry.adventures.create(world_id, "Fog on the River")
        own = registry.world_maps.create(
            world_id, "Ferry", "ferry.png", io.BytesIO(picture(300, 300)), adventure.id
        )
        coast = registry.world_maps.create(
            world_id, "Coast", "coast.png", io.BytesIO(picture(300, 300))
        )
        done = registry.world_copy.copy("adventure", [adventure.id], other.id)
        copied = registry.world_maps.of_adventure(done.ids[adventure.id])
        assert [m.name for m in copied] == ["Ferry"] and copied[0].id != own.id
        assert copied[0].scope.world_id == other.id
        assert registry.world_maps.source(copied[0]) is not None
        alone = registry.world_copy.copy("map", [coast.id], other.id)
        moved = registry.gamestate.maps.find(alone.ids[coast.id])
        assert moved.scope == Scope(world_id=other.id)
        assert registry.world_maps.source(moved) is not None
        with pytest.raises(WorldCopyError) as refused:
            registry.world_copy.copy("map", [own.id], other.id)
        assert refused.value.key == "copy.error.map_in_adventure"


class TestPages:
    async def test_upload_view_scale_delete(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        await _login(raw)
        empty = await raw.get(f"/gm/worlds/{world_id}/maps")
        assert empty.status_code == 200 and 'data-dialog="tool-map"' in empty.text
        made = await raw.post(
            f"/gm/worlds/{world_id}/maps",
            data={"name": "Sword Coast"},
            files={"picture": ("coast.png", picture(1000, 500), "image/png")},
        )
        assert made.status_code == 303
        (found,) = registry.world_maps.of_world(world_id)
        page = html.unescape((await raw.get(f"/gm/maps/{found.id}")).text)
        assert 'id="map-stage"' in page and 'data-width="1000"' in page
        assert "/static/js/leaflet/leaflet.js" in page
        assert f"/media/{world_id}/media/maps/coast.png" in page  # small: one picture
        thumb = await raw.get(f"/gm/maps/{found.id}/thumb")
        assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")
        scaled = await raw.post(
            f"/gm/maps/{found.id}/scale",
            data={"way": "line", "amount": "12,5", "unit": "mi", "x1": "0", "y1": "0",
                  "x2": "250", "y2": "0"},
        )  # fmt: skip
        assert scaled.status_code == 303 and "saved=1" in scaled.headers["location"]
        assert registry.world_maps.get(found.id).scale.feet_per_px == pytest.approx(264)
        refused = await raw.post(f"/gm/maps/{found.id}/scale", data={"way": "line", "amount": "5"})
        assert "error=atlas.error.line" in refused.headers["location"]
        viewed = await raw.post(
            f"/gm/maps/{found.id}/view", data={"x": "10", "y": "20", "zoom": "1"}
        )
        assert viewed.status_code == 204
        again = html.unescape((await raw.get(f"/gm/maps/{found.id}")).text)
        assert '"x": 10.0' in again and "data-feet=" in again
        menu = await raw.get("/menu/link", params={"href": f"/gm/maps/{found.id}"})
        assert menu.status_code == 200 and f"/gm/maps/{found.id}/delete" in menu.text
        saved = await raw.post(
            f"/gm/maps/{found.id}/edit", data={"name": "The Coast", "notes": "Windy."}
        )
        assert saved.status_code == 303
        assert registry.world_maps.get(found.id).name == "The Coast"
        gone = await raw.post(f"/gm/maps/{found.id}/delete")
        assert gone.status_code == 303 and "undo=" in gone.headers["location"]
        assert (await raw.get(f"/gm/maps/{found.id}")).status_code == 404

    async def test_a_refused_upload_says_why(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        await _login(raw)
        refused = await raw.post(
            f"/gm/worlds/{world_id}/maps",
            files={"picture": ("coast.gif", picture(10, 10, "GIF"), "image/gif")},
        )
        assert refused.status_code == 422 and "WebP" in html.unescape(refused.text)
        adventure = registry.adventures.create(world_id, "Fog on the River")
        on_page = await raw.post(
            f"/gm/worlds/{world_id}/maps",
            data={"adventure": adventure.id},
            files={"picture": ("coast.gif", picture(10, 10, "GIF"), "image/gif")},
        )
        assert on_page.status_code == 422 and 'id="maps"' in on_page.text
        registry.world_maps.create(
            world_id, "Ferry", "ferry.png", io.BytesIO(picture(300, 300)), adventure.id
        )
        shown = html.unescape((await raw.get(f"/gm/adventures/{adventure.id}")).text)
        assert "Ferry" in shown and "/gm/maps/" in shown
        asked = html.unescape((await raw.get(f"/gm/adventures/{adventure.id}/delete")).text)
        assert "1 Karte" in asked or "1 map" in asked

    async def test_a_large_map_is_cut_first(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        await _login(raw)
        big = registry.world_maps.create(
            world_id, "Continent", "continent.png", io.BytesIO(picture(4200, 300))
        )
        first = await raw.get(f"/gm/maps/{big.id}")
        assert 'id="map-tiling"' in first.text and 'id="map-stage"' not in first.text
        for _ in range(200):
            if registry.world_maps.tiles.ready(big.id, big.digest):
                break
            await asyncio.sleep(0.05)
        done = await raw.get(f"/gm/maps/{big.id}/status")
        assert done.headers.get("HX-Refresh") == "true"
        page = await raw.get(f"/gm/maps/{big.id}")
        assert f'data-tiles="/gm/maps/{big.id}/tiles"' in page.text
        tile = await raw.get(f"/gm/maps/{big.id}/tiles/0/0/0.webp")
        assert tile.status_code == 200 and tile.headers["content-type"] == "image/webp"
        assert (await raw.get(f"/gm/maps/{big.id}/tiles/9/0/0.webp")).status_code == 404
        thumb = await raw.get(f"/gm/maps/{big.id}/thumb")
        assert thumb.status_code == 200

    async def test_the_current_worlds_maps_and_the_nav(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        registry.table.select(facts["world"].id)
        await _login(raw)
        page = await raw.get("/gm/maps")
        assert page.status_code == 200 and 'href="/gm/maps"' in page.text


def _map(registry: Any, world_id: str, name: str, size: tuple[int, int] = (1000, 500), **kw: Any):
    return registry.world_maps.create(
        world_id, name, f"{name}.png", io.BytesIO(picture(*size)), kw.get("adventure")
    )


class TestPins:
    def test_what_a_map_may_show(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        service = registry.world_maps
        adventure = registry.adventures.create(world_id, "Fog on the River")
        town = registry.locations.create("Grey Reed", f"w:{world_id}", world_id)
        ferry = registry.locations.create("Ferry", f"a:{adventure.id}", world_id)
        coast = _map(registry, world_id, "coast")
        own = _map(registry, world_id, "ferry-map", adventure=adventure.id)
        pin = service.add_pin(coast.id, x=-5, y=9999, kind="place", target=town.id)
        assert (pin.x, pin.y, pin.name) == (0, 500, "Grey Reed")  # held inside the picture
        # An adventure's place: on its own map, not on the world's.
        with pytest.raises(MapError):
            service.add_pin(coast.id, x=1, y=1, kind="place", target=ferry.id)
        service.add_pin(own.id, x=1, y=1, kind="place", target=ferry.id)
        service.add_pin(coast.id, x=2, y=2, kind="adventure", target=adventure.id)
        service.add_pin(coast.id, x=3, y=3, kind="map", target=own.id)
        with pytest.raises(MapError):
            service.add_pin(coast.id, x=3, y=3, kind="map", target=coast.id)
        with pytest.raises(MapError):
            service.add_pin(coast.id, x=3, y=3, kind="note", label="  ")
        note = service.add_pin(coast.id, x=4, y=4, kind="note", label="Bridge", note="It is out.")
        service.move_pin(coast.id, note.id, 40, 50)
        service.edit_pin(coast.id, note.id, label="The bridge", note="Out since spring.")
        saved = {p.id: p for p in service.get(coast.id).pins}
        assert (saved[note.id].x, saved[note.id].label) == (40, "The bridge")
        assert [m.name for m, _ in service.pins_to("place", town.id)] == ["coast"]
        service.remove_pin(coast.id, note.id)
        assert note.id not in {p.id for p in service.get(coast.id).pins}

    def test_maps_within_maps(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        service = registry.world_maps
        adventure = registry.adventures.create(world_id, "Fog on the River")
        world = _map(registry, world_id, "world", (2000, 1000))
        coast = _map(registry, world_id, "coast", (1000, 500))
        city = _map(registry, world_id, "city", (800, 800))
        dungeon = _map(registry, world_id, "dungeon", (500, 500), adventure=adventure.id)
        service.set_frame(coast.id, world.id, [500, 400, 100, 200])
        child = service.get(coast.id)
        assert (child.frame.x, child.frame.y, child.frame.width) == (100, 200, 400)
        service.set_frame(city.id, coast.id, [10, 10, 60, 60])
        assert [m.name for m in service.path(service.get(city.id))] == ["world", "coast"]
        with pytest.raises(MapError):  # a map never lies in what lies in it
            service.set_frame(world.id, city.id, [1, 1, 50, 50])
        with pytest.raises(MapError):  # nor a world's map on an adventure's
            service.set_frame(coast.id, dungeon.id, [1, 1, 50, 50])
        service.set_frame(dungeon.id, world.id, [1, 1, 50, 50])
        assert [m.name for m in service.frame_candidates(dungeon)] == []
        # 2000 px of world are 400 miles; the coast's frame, 400 px, is 80 miles across,
        # its own scale says 200: they disagree.
        service.set_scale(world.id, way="width", amount=400, unit="mi")
        service.set_scale(coast.id, way="width", amount=200, unit="mi")
        framed, own = service.scales_disagree(service.get(coast.id))
        assert (round(framed / 5280), round(own / 5280)) == (80, 200)
        service.set_scale(coast.id, way="width", amount=82, unit="mi")
        assert service.scales_disagree(service.get(coast.id)) is None
        # The world map at twice the resolution: pins and frames follow.
        service.add_pin(world.id, x=100, y=50, kind="note", label="Here")
        service.replace_image(world.id, "world.png", io.BytesIO(picture(4000, 2000)))
        assert service.get(world.id).pins[0].x == 200
        assert service.get(coast.id).frame.x == 200
        service.clear_frame(city.id)
        assert service.path(service.get(city.id)) == []

    def test_copied_alone_a_map_leaves_its_parent(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        other = registry.gamestate.worlds.create(World(name="Ashfall"))
        world = _map(registry, world_id, "world", (2000, 1000))
        coast = _map(registry, world_id, "coast")
        town = registry.locations.create("Grey Reed", f"w:{world_id}", world_id)
        registry.world_maps.set_frame(coast.id, world.id, [1, 1, 50, 50])
        registry.world_maps.add_pin(coast.id, x=1, y=1, kind="place", target=town.id)
        done = registry.world_copy.copy("map", [coast.id], other.id, bring=False)
        copied = registry.world_maps.get(done.ids[coast.id])
        assert copied.parent is None and copied.frame is None
        # Its pin to a place left behind shows as gone there.
        assert not registry.world_maps.shows(copied, "place", town.id)


class TestPinPages:
    async def test_pin_open_drag_remove(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        await _login(raw)
        coast = _map(registry, world_id, "coast")
        town = registry.locations.create("Grey Reed", f"w:{world_id}", world_id)
        made = await raw.post(
            f"/gm/maps/{coast.id}/pins",
            data={"x": "100,5", "y": "50", "choice": f"place:{town.id}"},
        )
        assert made.status_code == 303 and "pin=" in made.headers["location"]
        (pin,) = registry.world_maps.get(coast.id).pins
        page = html.unescape((await raw.get(made.headers["location"])).text)
        assert f'"id": "{pin.id}"' in page and f'data-focus="{pin.id}"' in page
        assert f'"url": "/gm/places/{town.id}"' in page
        assert 'id="tool-pin"' in page and "Grey Reed" in page
        shown = html.unescape((await raw.get(f"/gm/places/{town.id}")).text)
        assert f"/gm/maps/{coast.id}?pin={pin.id}" in shown
        new = await raw.post(
            f"/gm/maps/{coast.id}/pins",
            data={"x": "10", "y": "10", "choice": "new", "place_name": "Old Mill"},
        )
        assert new.status_code == 303
        mill = next(x for x in registry.locations.list(world_id) if x.name == "Old Mill")
        assert any(p.target == mill.id for p in registry.world_maps.get(coast.id).pins)
        note = await raw.post(
            f"/gm/maps/{coast.id}/pins", data={"x": "5", "y": "5", "choice": "note"}
        )
        assert "error=atlas.error.note" in note.headers["location"]
        moved = await raw.post(
            f"/gm/maps/{coast.id}/pins/{pin.id}/move", data={"x": "300", "y": "200"}
        )
        assert moved.status_code == 204
        edited = await raw.post(f"/gm/maps/{coast.id}/pins/{pin.id}", data={"label": "Reed"})
        assert edited.status_code == 204
        assert registry.world_maps.get(coast.id).pins[0].label == "Reed"
        # The place deleted: the pin stays, grey, with the name it had.
        registry.locations.delete(town.id)
        page = html.unescape((await raw.get(f"/gm/maps/{coast.id}")).text)
        assert '"gone": true' in page and '"name": "Grey Reed"' in page
        gone = await raw.post(f"/gm/maps/{coast.id}/pins/{pin.id}/delete")
        assert gone.status_code == 204
        assert pin.id not in {p.id for p in registry.world_maps.get(coast.id).pins}

    async def test_lay_a_map_on_another(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        await _login(raw)
        world = _map(registry, world_id, "world", (2000, 1000))
        coast = _map(registry, world_id, "coast")
        laid = await raw.post(
            f"/gm/maps/{world.id}/frames",
            data={"child": coast.id, "x1": "10", "y1": "10", "x2": "210", "y2": "110"},
        )
        assert laid.status_code == 303 and "saved=1" in laid.headers["location"]
        page = html.unescape((await raw.get(f"/gm/maps/{world.id}")).text)
        assert f'"url": "/gm/maps/{coast.id}"' in page and "data-frames=" in page
        child = html.unescape((await raw.get(f"/gm/maps/{coast.id}")).text)
        assert 'class="map-path"' in child and f'href="/gm/maps/{world.id}"' in child
        cleared = await raw.post(
            f"/gm/maps/{coast.id}/frame/clear", data={"next": f"/gm/maps/{world.id}"}
        )
        assert cleared.headers["location"] == f"/gm/maps/{world.id}"
        assert registry.world_maps.get(coast.id).parent is None


class TestLayers:
    def test_a_new_map_and_one_from_before(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        service = registry.world_maps
        made = registry.world_maps.create(
            world_id,
            "Coast",
            "coast.png",
            io.BytesIO(picture(1000, 500)),
            layer_names={"places": "Orte", "names": "Namen", "roads": "Wege", "notes": "Notizen"},
        )
        assert [(x.name, x.role) for x in made.layers] == [
            ("Orte", "places"),
            ("Namen", "names"),
            ("Wege", "roads"),
            ("Notizen", "notes"),
        ]
        # A map from before layers: its pins go onto the layer of their kind.
        town = registry.locations.create("Grey Reed", f"w:{world_id}", world_id)
        old = _map(registry, world_id, "old")
        registry.gamestate.maps.update(
            old.id,
            layers=[],
            pins=[
                MapPin(x=1, y=1, kind="place", target=town.id, name="Grey Reed"),
                MapPin(x=2, y=2, kind="note", label="Bridge"),
            ],
        )
        fixed = service.with_layers(old.id)
        roles = {x.id: x.role for x in fixed.layers}
        assert [roles[p.layer] for p in fixed.pins] == ["places", "notes"]
        assert service.with_layers(old.id) == fixed  # once is enough

    def test_change_order_delete_and_show(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        service = registry.world_maps
        coast = _map(registry, world_id, "coast")
        places, names, roads, notes = (x.id for x in coast.layers)
        bandits = service.add_layer(coast.id, "  Bandits  ")
        assert bandits.name == "Bandits" and service.get(coast.id).layers[-1].id == bandits.id
        service.move_layer(coast.id, bandits.id, -1)
        assert service.get(coast.id).layers[-2].id == bandits.id
        service.add_pin(coast.id, x=1, y=1, kind="note", label="Camp", layer=bandits.id)
        label = service.add_label(coast.id, x=500, y=250, text="Grey Sea", style="water", size=40)
        assert label.layer == names
        assert service.counts(service.get(coast.id))[bandits.id] == 1
        # Locked: nothing on it moves, nothing new goes on it.
        service.lock_layer(coast.id, names, True)
        with pytest.raises(MapError):
            service.move_label(coast.id, label.id, 10, 10)
        with pytest.raises(MapError):
            service.add_label(coast.id, x=1, y=1, text="X", size=5, layer=names)
        service.lock_layer(coast.id, names, False)
        service.set_layer_zoom(coast.id, notes, 2.5)
        assert service.get(coast.id).layers[-1].from_zoom == 2.5
        # Shown or hidden per user, the layer's own setting for whoever has not chosen.
        gm = registry.gamestate.users.list()[0]
        service.edit_layer(coast.id, roads, name="Roads", visible=False, locked=False)
        assert service.shown(gm.id, service.get(coast.id))[roads] is False
        service.set_shown(gm.id, coast.id, roads, True)
        assert service.shown(gm.id, service.get(coast.id))[roads] is True
        # Deleted, its contents moved, or deleted with it.
        service.delete_layer(coast.id, bandits.id, move_to=notes)
        assert [p.layer for p in service.get(coast.id).pins] == [notes]
        service.delete_layer(coast.id, names)
        assert service.get(coast.id).labels == []
        for layer in (places, roads):
            service.delete_layer(coast.id, layer)
        with pytest.raises(MapError):
            service.delete_layer(coast.id, notes)  # the last stays

    def test_names_on_the_map(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        service = registry.world_maps
        coast = _map(registry, world_id, "coast")
        made = service.add_label(
            coast.id, x=100, y=50, text="  The   Dragonspine ", style="region", size=60, angle=200
        )
        assert (made.text, made.angle) == ("The Dragonspine", -160)
        with pytest.raises(MapError):
            service.add_label(coast.id, x=1, y=1, text="X", style="gothic", size=5)
        edited = service.edit_label(
            coast.id, made.id, text="Dragonspine", style="feature", size=30, angle=15
        )
        assert (edited.style, edited.size) == ("feature", 30)
        service.move_label(coast.id, made.id, 300, 100)
        service.replace_image(coast.id, "coast.png", io.BytesIO(picture(2000, 1000)))
        stretched = service.get(coast.id).labels[0]
        assert (stretched.x, stretched.size) == (600, 60)
        service.remove_label(coast.id, made.id)
        assert service.get(coast.id).labels == []


class TestLayerPages:
    async def test_panel_and_names(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = _registry(facts)
        world_id = facts["world"].id
        await _login(raw)
        made = await raw.post(
            f"/gm/worlds/{world_id}/maps",
            files={"picture": ("coast.png", picture(1000, 500), "image/png")},
        )
        coast = registry.world_maps.get(made.headers["location"].rsplit("/", 1)[1])
        # Named in the gamemaster's language (the table's user reads German here or not).
        page = html.unescape((await raw.get(f"/gm/maps/{coast.id}")).text)
        assert 'id="layers"' in page and "data-layers=" in page and 'id="tool-label"' in page
        places, names = coast.layers[0].id, coast.layers[1].id
        written = await raw.post(
            f"/gm/maps/{coast.id}/labels",
            data={"x": "10", "y": "10", "text": "Grey Sea", "style": "water", "size": "",
                  "angle": "5"},
        )  # fmt: skip
        assert written.status_code == 303
        (label,) = registry.world_maps.get(coast.id).labels
        assert (label.layer, label.size) == (names, 10)  # 1000 px / 100
        edited = await raw.post(
            f"/gm/maps/{coast.id}/labels/{label.id}",
            data={"text": "Grey Sea", "style": "water", "size": "20", "angle": "0",
                  "layer": places},
        )  # fmt: skip
        assert edited.status_code == 204
        assert registry.world_maps.get(coast.id).labels[0].layer == places
        added = await raw.post(f"/gm/maps/{coast.id}/layers", data={"name": "Bandits"})
        assert added.headers["location"].endswith("#layers")
        bandits = registry.world_maps.get(coast.id).layers[-1]
        hidden_ = await raw.post(
            f"/gm/maps/{coast.id}/layers/{bandits.id}/shown", data={"shown": "0"}
        )
        assert hidden_.status_code == 204
        locked = await raw.post(
            f"/gm/maps/{coast.id}/layers/{bandits.id}/lock", data={"locked": "1"}
        )
        assert locked.status_code == 204 and registry.world_maps.get(coast.id).layers[-1].locked
        refused = await raw.post(
            f"/gm/maps/{coast.id}/pins",
            data={"x": "5", "y": "5", "choice": "note", "label": "Camp", "layer": bandits.id},
        )
        assert "error=atlas.error.locked" in refused.headers["location"]
        page = html.unescape((await raw.get(f"/gm/maps/{coast.id}")).text)
        assert f'"id": "{bandits.id}", "name": "Bandits"' in page and '"shown": false' in page
        zoomed = await raw.post(
            f"/gm/maps/{coast.id}/layers/{bandits.id}/zoom", data={"zoom": "1.5"}
        )
        assert zoomed.status_code == 303
        assert registry.world_maps.get(coast.id).layers[-1].from_zoom == 1.5
        gone = await raw.post(
            f"/gm/maps/{coast.id}/layers/{places}/delete", data={"contents": names}
        )
        assert gone.status_code == 303
        assert registry.world_maps.get(coast.id).labels[0].layer == names
        removed = await raw.post(f"/gm/maps/{coast.id}/labels/{label.id}/delete")
        assert removed.status_code == 204
