"""Image effects (documentation/ADVENTURES.md §5.5): the notation, the rendering, the
cache, and where images ask for them."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, World
from app.services import image_effects
from app.services.image_effects import ImageEffects, apply, join, parse, split, with_effects
from app.store.registry import StoreRegistry
from app.ui.library_links import cover, media_url
from app.ui.markdown import MarkdownContext, embedded_image, render_markdown
from tests.test_markdown import html

PASSWORD = "correct horse"


def _picture(width: int = 120, height: int = 80) -> Image.Image:
    image = Image.new("RGB", (width, height), (40, 120, 200))
    for x in range(width):
        image.putpixel((x, height // 2), (250, 250, 250))
    return image


def _png(width: int = 120, height: int = 80) -> bytes:
    buffer = io.BytesIO()
    _picture(width, height).save(buffer, "PNG")
    return buffer.getvalue()


class TestNotation:
    def test_a_part_made_only_of_effect_words_names_effects(self) -> None:
        assert parse("torn sepia") == ("sepia", "torn")
        assert parse("Torn, fade+OVAL") == ("torn", "fade", "oval")
        assert parse("ripped old") == ("aged", "torn")  # aliases
        assert parse("The torn map") is None  # alt text
        assert parse("300") is None and parse("") is None

    def test_a_spec_carries_its_effects_after_a_bar(self) -> None:
        assert split("media/tavern.jpg|torn fade") == ("media/tavern.jpg", ("torn", "fade"))
        assert split("media/tavern.jpg") == ("media/tavern.jpg", ())
        assert split("media/tavern.jpg|nonsense") == ("media/tavern.jpg", ())
        assert split(None) == ("", ())
        assert join("media/x.png", ["fade", "torn"]) == "media/x.png|torn fade"
        assert join("media/x.png", []) == "media/x.png"

    def test_urls_ask_for_the_rendering(self) -> None:
        assert with_effects("/media/w/a.png", ("torn",)) == "/media/w/a.png?fx=torn"
        assert with_effects("/media/w/a.png?v=1", ("torn", "fade")).endswith("&fx=torn,fade")
        assert media_url("w", "media/a.png|sepia") == "/media/w/media/a.png?fx=sepia"
        assert media_url("w", "media/a.png") == "/media/w/media/a.png"
        assert 'class="cover fx"' in str(cover("w", "media/a.png|oval", "A"))
        assert "?fx=banner,oval" in str(cover("w", "media/a.png|oval", "A"))


class TestRendering:
    @pytest.mark.parametrize("effect", ["torn", "fade", "oval"])
    def test_edges_fall_away_into_transparency(self, effect: str) -> None:
        out = apply(_picture(), (effect,), "seed")
        assert out.mode == "RGBA" and out.size == (120, 80)
        assert out.getpixel((0, 0))[3] < 40  # a corner falls away
        assert out.getpixel((60, 40))[3] == 255  # the middle stays

    def test_a_fade_vanishes_completely_at_every_edge(self) -> None:
        out = apply(_picture(300, 200), ("fade",), "seed")
        for x, y in ((0, 100), (299, 100), (150, 0), (150, 199), (0, 0), (299, 199)):
            assert out.getpixel((x, y))[3] == 0, (x, y)
        assert 0 < out.getpixel((10, 100))[3] < 255  # rising, not cut

    def test_a_transparent_margin_is_cut_before_the_effects(self) -> None:
        framed = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
        framed.paste(Image.new("RGBA", (120, 60), (40, 120, 200, 255)), (40, 20))
        out = apply(framed, ("fade",), "seed")
        assert out.size == (120, 60)
        assert out.getpixel((0, 30))[3] == 0 and out.getpixel((60, 30))[3] == 255

    def test_colour_effects_keep_the_edges(self) -> None:
        for effect in ("sepia", "aged", "mono"):
            out = apply(_picture(), (effect,), "seed")
            assert out.getpixel((0, 0))[3] == 255, effect
            assert out.getpixel((5, 5))[:3] != (40, 120, 200), effect
        mono = apply(_picture(), ("mono",), "seed").getpixel((5, 5))
        assert mono[0] == mono[1] == mono[2]

    def test_a_tear_is_the_same_for_the_same_picture(self) -> None:
        a = apply(_picture(), ("torn",), "one").tobytes()
        assert a == apply(_picture(), ("torn",), "one").tobytes()
        assert a != apply(_picture(), ("torn",), "two").tobytes()

    def test_large_pictures_are_made_smaller(self) -> None:
        out = apply(_picture(3200, 1600), ("fade",), "seed")
        assert out.size == (image_effects.MAX_SIDE, 800)

    def test_a_rendering_is_kept_and_made_again_when_the_picture_changes(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "map.png"
        source.write_bytes(_png())
        effects = ImageEffects(tmp_path / "cache")
        first = effects.render(source, ("torn",))
        assert first.suffix == ".webp" and first.is_relative_to(tmp_path / "cache")
        with Image.open(first) as made:
            assert made.mode == "RGBA"
        stamp = first.stat().st_mtime_ns
        assert effects.render(source, ("torn",)) == first and first.stat().st_mtime_ns == stamp
        assert effects.render(source, ("fade",)) != first
        source.write_bytes(_png(100, 60))
        assert effects.render(source, ("torn",)) != first


class TestMarkdown:
    def test_an_embed_names_its_effects_beside_size_and_alt(self) -> None:
        assert embedded_image("map.png|The map|torn sepia|300") == (
            "map.png",
            "The map",
            300,
            None,
            ("sepia", "torn"),
        )
        assert embedded_image("map.png|300") == ("map.png", "", 300, None, ())

    def test_images_ask_for_their_rendering(self) -> None:
        ctx = MarkdownContext(
            media=lambda path: f"/media/w/{path}", find_media=lambda name: f"/media/w/media/{name}"
        )
        out = str(render_markdown("![[map.png|torn|300]]\n", ctx).html)
        assert 'src="/media/w/media/map.png?fx=torn"' in out
        assert 'class="fx fx-torn"' in out and 'width="300"' in out
        out = str(render_markdown("![The map|fade](media/map.png)\n", ctx).html)
        assert 'src="/media/w/media/map.png?fx=fade"' in out and 'alt="The map"' in out
        plain = str(render_markdown("![The map](media/map.png)\n", ctx).html)
        assert "fx" not in plain and 'alt="The map"' in plain

    def test_without_media_nothing_changes(self) -> None:
        assert "md-image missing" in html("![[map.png|torn]]\n")


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(
            library_dir=tmp_path / "lib",
            reference_cache_dir=tmp_path / "cache",
            watch_files=False,
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestRoutes:
    async def test_the_cover_wears_its_effects(
        self, client: tuple[AsyncClient, StoreRegistry], tmp_path: Path
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        back = f"/gm/worlds/{world.id}/edit"
        await ac.post(
            f"/gm/media/{world.id}",
            files={"file": ("Cover.png", _png(), "image/png")},
            data={"next": back, "as_cover": "1"},
        )
        done = await ac.post(
            f"/gm/media/{world.id}/cover",
            data={"path": "media/cover.png", "effects": ["fade", "torn"], "next": back},
        )
        assert done.status_code == 303
        assert registry.gamestate.worlds.get(world.id).cover == "media/cover.png|torn fade"
        page = (await ac.get(f"/gm/worlds/{world.id}")).text
        assert f'src="/media/{world.id}/media/cover.png?fx=banner,torn,fade"' in page
        rendered = await ac.get(f"/media/{world.id}/media/cover.png?fx=torn,fade")
        assert rendered.status_code == 200 and rendered.headers["content-type"] == "image/webp"
        assert rendered.content[8:12] == b"WEBP"
        assert list((tmp_path / "cache" / "images").rglob("*.webp"))
        original = await ac.get(f"/media/{world.id}/media/cover.png?fx=nonsense")
        assert original.headers["content-type"] == "image/png"
        refused = await ac.post(
            f"/gm/media/{world.id}/cover",
            data={"path": "media/missing.png", "effects": ["torn"], "next": back},
        )
        assert "error=adventures.error.cover" in refused.headers["location"]
