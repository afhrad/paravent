"""Round tokens for every element (documentation/TOKENS.md)."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    Encounter,
    EncounterMonster,
    Npc,
    PartySpec,
    Role,
    Scope,
    SourceSet,
    World,
)
from app.models.pc import PlayerCharacter
from app.services.tokens import Placement, Token, compose, crop_round, token_name
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


def _picture(size: tuple[int, int] = (300, 200), fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, fmt)
    return out.getvalue()


class TestTheParts:
    def test_a_name_as_5etools_writes_its_token(self) -> None:
        assert token_name('Drow "Mage"') == "Drow Mage"
        assert token_name("Jarlaxle Baenre") == "Jarlaxle Baenre"
        assert token_name("Zhentarim Thug (Kültür)") == "Zhentarim Thug (Kultur)"

    def test_any_picture_is_cropped_round(self) -> None:
        token = Image.open(io.BytesIO(crop_round(_picture())))
        assert token.format == "WEBP" and token.size == (512, 512)
        assert token.getpixel((2, 2))[3] == 0  # a corner is see-through
        assert token.getpixel((256, 256))[3] == 255  # the middle is the picture

    def test_a_picture_is_placed_in_its_kind_s_frame(self) -> None:
        """Left half red, right half blue: placed with its blue at the centre."""
        picture = Image.new("RGB", (400, 200), (220, 20, 20))
        picture.paste((20, 20, 220), (200, 0, 400, 200))
        out = io.BytesIO()
        picture.save(out, "PNG")
        made = Image.open(io.BytesIO(compose(out.getvalue(), "npc", Placement(0.75, 0.5, 1))))
        assert made.size == (512, 512) and made.getpixel((2, 2))[3] == 0
        r, g, b, a = made.getpixel((256, 256))
        assert b > 150 and r < 80 and a == 255  # the chosen point is in the middle
        ring = made.getpixel((256, 18))  # on the ring: the frame's own colour, opaque
        assert ring[3] > 200 and (ring[0], ring[1], ring[2]) != (r, g, b)

    def test_a_placement_from_a_form_stays_in_bounds(self) -> None:
        assert Placement.parse("0.3", "0.7", "2") == Placement(0.3, 0.7, 2.0)
        assert Placement.parse("-1", "9", "0.2") == Placement(0.0, 1.0, 1.0)
        assert Placement.parse("x", "nan", "") == Placement()


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> StoreRegistry:
    images = tmp_path / "img"
    (images / "bestiary" / "tokens" / "MM").mkdir(parents=True)
    Image.new("RGBA", (64, 64)).save(images / "bestiary" / "tokens" / "MM" / "Goblin.webp")
    store = StoreRegistry(
        Settings(
            reference_dir=corpus,
            library_dir=tmp_path / "lib",
            image_dir=images,
            watch_files=False,
        )
    )
    store.load()
    return store


class TestFinding:
    def test_5etools_tokens_else_the_kind_s_disc(self, registry: StoreRegistry) -> None:
        goblin = registry.monster_token("Goblin", "MM")
        assert goblin == Token("creature", "/play/img/bestiary/tokens/MM/Goblin.webp")
        assert registry.monster_token("Bugbear", "MM") == Token("creature")
        # A record's own ``token`` names another creature's.
        named = registry.monster_token(
            "Goblin Boss", "MM", token={"name": "Goblin", "source": "MM"}
        )
        assert named.src == goblin.src
        assert registry.tokens.of_entry("spell", "Fireball", "PHB") == Token("spell")

    def test_an_uploaded_token_where_there_is_no_picture(self, registry: StoreRegistry) -> None:
        world = registry.gamestate.worlds.create(World(name="Moor", source_set=SourceSet()))
        treasure = registry.treasure.create("Hoard", f"world:{world.id}", world.id)
        assert registry.tokens.of_record("treasure", world.id, treasure.id) == Token("treasure")
        registry.tokens.set(world.id, "treasure", treasure.id, "hoard.png", _picture())
        own = registry.tokens.of_record("treasure", world.id, treasure.id)
        assert own.src is not None and own.src.startswith(f"/tokens/{world.id}/treasure/")
        registry.tokens.remove(world.id, "treasure", treasure.id)
        assert registry.tokens.of_record("treasure", world.id, treasure.id) == Token("treasure")

    def test_a_portrait_is_its_token_too(self, registry: StoreRegistry) -> None:
        """One picture for both: the token is made from the portrait, follows it, and
        keeps where it was placed."""
        world = registry.gamestate.worlds.create(World(name="Moor", source_set=SourceSet()))
        npc = registry.gamestate.npcs.create(Npc(name="Maren", scope=Scope(world_id=world.id)))
        assert registry.tokens.of_record("npc", world.id, npc.id) == Token("npc")
        registry.npcs.upload_portrait(npc.id, "maren.png", _picture())
        made = registry.tokens.of_record("npc", world.id, npc.id)
        assert made.src is not None and made.src.startswith(f"/tokens/{world.id}/npc/{npc.id}?v=")
        registry.tokens.place(world.id, "npc", npc.id, Placement(0.3, 0.5, 2))
        assert registry.tokens.placement(world.id, "npc", npc.id) == Placement(0.3, 0.5, 2)
        # A new portrait makes the token again, where it was placed.
        registry.npcs.upload_portrait(npc.id, "maren2.png", _picture((500, 500)))
        again = registry.tokens.of_record("npc", world.id, npc.id)
        assert again.src != made.src
        assert registry.tokens.placement(world.id, "npc", npc.id) == Placement(0.3, 0.5, 2)
        registry.npcs.clear_portrait(npc.id)
        assert registry.tokens.of_record("npc", world.id, npc.id) == Token("npc")

    def test_the_table_s_picture_of_a_book_creature(self, registry: StoreRegistry) -> None:
        registry.creature_pictures.set(None, "Goblin|MM", "goblin.png", _picture())
        made = registry.monster_token("Goblin", "MM")
        assert made.src is not None and made.src.startswith("/tokens/-/creature/book-goblin--mm")


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("pia", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry


async def _login(ac: AsyncClient, name: str) -> None:
    await ac.post("/login", data={"username": name, "password": PASSWORD})


class TestThePages:
    async def test_upload_show_and_remove(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        await _login(ac, "dm")
        world = registry.gamestate.worlds.create(World(name="Moor", source_set=SourceSet()))
        npc = registry.gamestate.npcs.create(Npc(name="Maren", scope=Scope(world_id=world.id)))
        page = (await ac.get(f"/gm/npcs/{npc.id}")).text
        assert 'id="token"' in page and "tok-npc tok-disc" in page
        assert "/static/tokens/frame-npc.png" in page and "token_editor.js" in page
        sent = await ac.post(
            f"/tokens/npc/{npc.id}",
            data={"next": f"/gm/npcs/{npc.id}#token", "x": "0.4", "y": "0.5", "zoom": "1.5"},
            files={"token": ("maren.png", _picture(), "image/png")},
        )
        assert sent.status_code == 303
        page = (await ac.get(f"/gm/npcs/{npc.id}")).text
        assert f"/tokens/{world.id}/npc/{npc.id}?v=" in page
        # The upload is its portrait too, and the panel offers to place it anew.
        assert registry.gamestate.npcs.get(npc.id).portrait
        assert "tok-adjust" in page and f"/tokens/{world.id}/npc/{npc.id}/picture" in page
        picture = await ac.get(f"/tokens/{world.id}/npc/{npc.id}/picture")
        assert picture.status_code == 200
        assert registry.tokens.placement(world.id, "npc", npc.id) == Placement(0.4, 0.5, 1.5)
        moved = await ac.post(
            f"/tokens/npc/{npc.id}", data={"next": "/", "x": "0.6", "y": "0.5", "zoom": "1"}
        )
        assert moved.status_code == 303 and "token_error" not in moved.headers["location"]
        assert registry.tokens.placement(world.id, "npc", npc.id) == Placement(0.6, 0.5, 1.0)
        listed = (await ac.get(f"/gm/worlds/{world.id}/npcs")).text
        assert f"/tokens/{world.id}/npc/{npc.id}" in listed
        served = await ac.get(f"/tokens/{world.id}/npc/{npc.id}")
        assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
        # Not a picture: said so, nothing stored.
        refused = await ac.post(
            f"/tokens/npc/{npc.id}",
            data={"next": f"/gm/npcs/{npc.id}#token"},
            files={"token": ("x.png", b"not an image", "image/png")},
        )
        assert "token_error=image" in refused.headers["location"]

    async def test_a_player_sets_only_their_own_character_s(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="Moor", source_set=SourceSet()))
        npc = registry.gamestate.npcs.create(Npc(name="Maren", scope=Scope(world_id=world.id)))
        pc = registry.gamestate.characters.create(
            PlayerCharacter(name="Lyra", player="pia", world_id=world.id)
        )
        await _login(ac, "pia")
        picture = {"token": ("lyra.png", _picture(), "image/png")}
        assert (await ac.post(f"/tokens/character/{pc.id}", files=picture)).status_code == 303
        assert registry.tokens.file(world.id, "character", pc.id) is not None
        assert (await ac.post(f"/tokens/npc/{npc.id}", files=picture)).status_code == 403
        assert (await ac.post("/tokens/npc/nobody", files=picture)).status_code == 404

    async def test_the_tracker_and_the_builder_show_creature_tokens(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        encounter = registry.gamestate.encounters.create(
            Encounter(
                name="Ambush",
                party=PartySpec(size=4, level=2),
                monsters=[
                    EncounterMonster(name="Goblin", source="MM", count=2),
                    EncounterMonster(name="Bugbear", source="MM", count=1),
                ],
            )
        )
        plan = (await ac.get(f"/gm/encounters/{encounter.id}")).text
        assert (
            "/play/img/bestiary/tokens/MM/Goblin.webp" in plan and "tok-creature tok-disc" in plan
        )
        combat = registry.combats.start(encounter, "dm")
        board = (await ac.get(f"/gm/combat/{combat.id}")).text
        assert "/play/img/bestiary/tokens/MM/Goblin.webp" in board
        assert '<span class="tok-badge">2</span>' in board  # "Goblin 2" keeps its number
        reference = (await ac.get("/ref/monster/MM/Goblin")).text
        assert "sb-head has-token" in reference and "Goblin.webp" in reference
