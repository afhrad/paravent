"""The gamemaster's own picture of a kind of creature (documentation/COMBAT.md §5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.models.gamestate import Role, World
from app.models.pc import PlayerCharacter
from app.services.creature_pictures import CreaturePictures, slug
from app.services.media import MediaError
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from tests.test_monster_attacks import HTMX, PASSWORD, tracker  # noqa: F401 — the fixture

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
WEBP = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 32


class TestStore:
    def test_a_picture_is_set_replaced_and_removed(self, tmp_path: Path) -> None:
        state = GameStateStore(tmp_path)
        world = state.worlds.create(World(name="W"))
        pictures = CreaturePictures(state.prose, tmp_path / "media")
        assert slug("Goblin Boss|MM") == "goblin-boss--mm"
        assert pictures.find(world.id, "Goblin|MM") is None

        first = pictures.set(world.id, "Goblin|MM", "my goblin.PNG", PNG)
        assert first.name == "goblin--mm.png" and first.parent.name == "creatures"
        assert first.parent.parent.name == "media"  # in the world's own media folder
        assert pictures.find(world.id, "Goblin|MM") == first
        second = pictures.set(world.id, "Goblin|MM", "better.webp", WEBP)
        assert pictures.find(world.id, "Goblin|MM") == second and not first.exists()
        assert pictures.version(world.id, "Goblin|MM") is not None

        pictures.remove(world.id, "Goblin|MM")
        assert pictures.find(world.id, "Goblin|MM") is None
        assert pictures.version(world.id, "Goblin|MM") is None

    def test_only_real_images_are_taken(self, tmp_path: Path) -> None:
        state = GameStateStore(tmp_path)
        pictures = CreaturePictures(state.prose, tmp_path / "media")
        with pytest.raises(MediaError):
            pictures.set(None, "Goblin|MM", "goblin.svg", b"<svg/>")
        with pytest.raises(MediaError):
            pictures.set(None, "Goblin|MM", "goblin.png", b"not a png")
        # a fight outside any world keeps its pictures in the library's media folder
        stored = pictures.set(None, "Goblin|MM", "goblin.png", PNG)
        assert stored == tmp_path / "media" / "creatures" / "goblin--mm.png"


class TestRoutes:
    async def test_the_gamemaster_sets_it_and_the_players_see_it(
        self,
        tracker: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        goblin, other = registry.combats.get(combat_id).combatants[:2]
        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Picture" in side and "· none" in side  # no image folder in the tests

        action = f"/gm/combat/{combat_id}/picture/{goblin.id}"
        refused = await ac.post(
            action, data={"sel": goblin.id}, files={"picture": ("x.svg", b"<svg/>")}, headers=HTMX
        )
        assert "Only images can be uploaded" in refused.text  # the media rules
        assert 'role="alert"' in refused.text and "· none" in refused.text
        saved = await ac.post(
            action,
            data={"sel": goblin.id},
            files={"picture": ("goblin.png", PNG, "image/png")},
            headers=HTMX,
        )
        assert (
            "· your own" in saved.text
            and f"/play/combat/{combat_id}/c/{goblin.id}/picture?v=" in saved.text
        )
        world = registry.combats.get(combat_id).world_id
        assert registry.creature_pictures.find(world, "Goblin|MM") is not None

        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Lyra", "player": "alice"})
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        registry.combats.hide(combat_id, other.id, True, "dm")
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        details = (await ac.get(f"/play/combat/{combat_id}/c/{goblin.id}/details")).text
        assert f'src="/play/combat/{combat_id}/c/{goblin.id}/picture?v=' in details
        picture = await ac.get(f"/play/combat/{combat_id}/c/{goblin.id}/picture")
        assert picture.status_code == 200 and picture.content == PNG
        assert picture.headers["content-type"] == "image/png"
        # the same kind, but hidden from the players
        assert (await ac.get(f"/play/combat/{combat_id}/c/{other.id}/picture")).status_code == 404
        assert (await ac.post(action, data={"do": "remove"})).status_code == 403  # players can't

        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        removed = await ac.post(action, data={"sel": goblin.id, "do": "remove"}, headers=HTMX)
        assert "· none" in removed.text
        assert registry.creature_pictures.find(world, "Goblin|MM") is None
