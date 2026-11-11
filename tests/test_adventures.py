"""Adventures and chapters: the service and the pages (documentation/ADVENTURES.md)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.services.adventures import (
    AdventureError,
    AdventureNotFound,
    AdventureService,
    EditConflict,
    text_version,
)
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def setup(gamestate: GameStateStore) -> tuple[AdventureService, World]:
    gamestate.prose.load()
    world = gamestate.worlds.create(World(name="Sword Coast"))
    return AdventureService(gamestate), world


class TestService:
    def test_new_adventures_and_chapters_go_last(
        self, setup: tuple[AdventureService, World]
    ) -> None:
        service, world = setup
        first = service.create(world.id, "Lost Mine")
        second = service.create(world.id, "  Dragon   of Icespire  ")
        assert [a.name for a in service.adventures(world.id)] == [
            "Lost Mine",
            "Dragon of Icespire",
        ]
        assert (first.order, second.order) == (1, 2)
        for title in ("Goblin Arrows", "Phandalin", "Spider's Web"):
            service.create_chapter(first.id, title)
        assert [c.order for c in service.chapters(first.id)] == [1, 2, 3]

    def test_a_name_is_required(self, setup: tuple[AdventureService, World]) -> None:
        service, world = setup
        with pytest.raises(AdventureError):
            service.create(world.id, "   ")
        with pytest.raises(AdventureNotFound):
            service.create("nope", "X")

    def test_move_swaps_and_renumbers(self, setup: tuple[AdventureService, World]) -> None:
        service, world = setup
        adventure = service.create(world.id, "A")
        a, b, c = (service.create_chapter(adventure.id, t) for t in "abc")
        service.move("chapter", c.id, -1)
        assert [ch.title for ch in service.chapters(adventure.id)] == ["a", "c", "b"]
        service.move("chapter", a.id, -1)  # already first: nothing happens
        service.move("chapter", b.id, 1)  # already last
        assert [ch.order for ch in service.chapters(adventure.id)] == [1, 2, 3]

    def test_a_view_knows_its_neighbours(self, setup: tuple[AdventureService, World]) -> None:
        service, world = setup
        adventure = service.create(world.id, "A")
        a, b, c = (service.create_chapter(adventure.id, t) for t in "abc")
        view = service.chapter_view(b.id)
        assert (view.previous, view.next) == (a, c)
        assert service.chapter_view(a.id).previous is None

    def test_find_a_chapter_by_title_nearest_first(
        self, setup: tuple[AdventureService, World]
    ) -> None:
        service, world = setup
        one, two = service.create(world.id, "One"), service.create(world.id, "Two")
        mine = service.create_chapter(two.id, "Phandalin")
        service.create_chapter(one.id, "Phandalin")
        assert service.find_chapter(world.id, "phandalin", prefer=two.id) == mine
        assert service.find_chapter(world.id, "Nowhere") is None

    def test_update_keeps_levels_sane(self, setup: tuple[AdventureService, World]) -> None:
        service, world = setup
        adventure = service.create(world.id, "A")
        saved = service.update(
            adventure.id,
            name="A",
            summary="  two\nlines ",
            status="nonsense",
            level_start=7,
            level_end=3,
            body="Text",
        )
        assert saved.level is not None and (saved.level.start, saved.level.end) == (7, 7)
        assert (saved.summary, saved.status) == ("two lines", "planned")

    def test_images_and_cover(self, setup: tuple[AdventureService, World]) -> None:
        service, world = setup
        adventure = service.create(world.id, "A")
        path = service.upload(adventure.id, "Cover.png", PNG, as_cover=True)
        assert service.get(adventure.id).cover == path == "media/cover.png"
        assert service.images(adventure.id) == ["media/cover.png"]
        service.set_cover(adventure.id, None)
        assert service.get(adventure.id).cover is None
        with pytest.raises(AdventureError):
            service.set_cover(adventure.id, "../../../users.json")

    def test_delete_takes_chapters_along(self, setup: tuple[AdventureService, World]) -> None:
        service, world = setup
        adventure = service.create(world.id, "A")
        chapter = service.create_chapter(adventure.id, "a")
        service.delete(adventure.id)
        with pytest.raises(AdventureNotFound):
            service.get_chapter(chapter.id)


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


CHAPTER = """Go to {@chapter Phandalin}. Meet {@npc Sildar}.

# Goblin Ambush

> [!read]
> Two dead horses lie on the trail.

Four {@creature goblin}s.

::: encounter Cragmaw ambush
They flee.
:::

::: statblock Goblin|MM
:::

| Room | Who |
|---|---|
| 1 | {@creature wolf||Three wolves} |

![Map](media/map.png)
"""


class TestPages:
    async def test_world_adventure_chapter(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Sword Coast", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        made = await ac.post(f"/gm/worlds/{world.id}/adventures", data={"name": "Lost Mine"})
        assert made.status_code == 303
        adventure = registry.adventures.adventures(world.id)[0]
        assert made.headers["location"] == f"/gm/adventures/{adventure.id}"
        assert "Lost Mine" in (await ac.get(f"/gm/worlds/{world.id}")).text

        made = await ac.post(
            f"/gm/adventures/{adventure.id}/chapters", data={"title": "Goblin Arrows"}
        )
        chapter = registry.adventures.chapters(adventure.id)[0]
        assert made.headers["location"] == f"/gm/chapters/{chapter.id}/edit"
        registry.adventures.create_chapter(adventure.id, "Phandalin")
        encounter = registry.encounters.create("Cragmaw ambush", world.id)
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 4)
        registry.adventures.upload(chapter.id, "map.png", PNG, as_cover=False)

        saved = await ac.post(
            f"/gm/chapters/{chapter.id}/edit",
            data={"title": "Goblin Arrows", "status": "running", "body": CHAPTER},
        )
        assert saved.headers["location"] == f"/gm/chapters/{chapter.id}?saved=1"
        page = (await ac.get(saved.headers["location"])).text
        assert "Chapter saved." in page
        assert 'class="md-callout md-read"' in page
        assert 'class="ref lib chapter"' in page  # {@chapter Phandalin}
        assert "“Sildar” is not in this world." in page  # NPCs arrive in step 4
        assert f'href="/gm/encounters/{encounter.id}"' in page and "4× Goblin" in page
        assert 'class="md-fold md-fold-monster"' in page and "Goblin" in page
        assert ">Three wolves</a></td>" in page
        assert f'src="/media/{chapter.id}/media/map.png"' in page
        assert '<a href="#goblin-ambush">Goblin Ambush</a>' not in page  # one heading: no toc
        file = registry.gamestate.prose.path(chapter.id)
        assert file is not None and "status: running" in file.read_text()

    async def test_preview_saves_nothing(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        fragment = await ac.post(f"/gm/chapters/{chapter.id}/preview", data={"body": "# Hi"})
        assert fragment.text.startswith('<h2 id="hi">Hi</h2>')
        no_js = await ac.post(
            f"/gm/chapters/{chapter.id}/edit",
            data={"title": "C", "status": "planned", "body": "# Typed", "action": "preview"},
        )
        assert no_js.status_code == 200 and '<h2 id="typed">Typed</h2>' in no_js.text
        assert registry.adventures.get_chapter(chapter.id).body == ""

    async def test_a_save_from_an_older_text_is_refused(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        registry.adventures.update_chapter(chapter.id, title="C", status="draft", body="First.")
        opened = (await ac.get(f"/gm/chapters/{chapter.id}/edit")).text
        base = re.search(r'name="base" value="(\w+)"', opened)
        assert base is not None and base.group(1) == text_version("First.")

        # Meanwhile the file is edited by hand; a title or order change alone is no clash.
        registry.adventures.update_chapter(chapter.id, title="C", status="draft", body="Hand.")
        mine = {"title": "C", "status": "draft", "body": "Mine.", "base": base.group(1)}
        refused = await ac.post(f"/gm/chapters/{chapter.id}/edit", data=mine)
        assert refused.status_code == 409
        assert "changed elsewhere since you opened it" in refused.text
        assert ">Mine.</textarea>" in refused.text  # the editor keeps the typed text
        assert 'class="panel conflict-panel"' in refused.text and "Hand.</textarea>" in refused.text
        assert registry.adventures.get_chapter(chapter.id).body == "Hand."

        # Saving again from there replaces the hand edit deliberately.
        again = re.search(r'name="base" value="(\w+)"', refused.text)
        assert again is not None and again.group(1) == text_version("Hand.")
        saved = await ac.post(
            f"/gm/chapters/{chapter.id}/edit", data=mine | {"base": again.group(1)}
        )
        assert saved.status_code == 303
        assert registry.adventures.get_chapter(chapter.id).body == "Mine."

    async def test_conflicts_in_the_service(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path)
        service = AdventureService(store)
        world = store.worlds.create(World(name="W"))
        adventure = service.create(world.id, "A")
        chapter = service.create_chapter(adventure.id, "C")
        service.update_chapter(chapter.id, title="C", status="draft", body="Old")
        old = text_version("Old")
        service.update_chapter(chapter.id, title="C", status="draft", body="New")
        with pytest.raises(EditConflict) as raised:
            service.update_chapter(chapter.id, title="C", status="draft", body="Mine", base=old)
        assert raised.value.current == "New"
        # The same text written twice is no clash, and no base means no check.
        service.update_chapter(chapter.id, title="X", status="draft", body="New", base=old)
        service.update_chapter(chapter.id, title="X", status="draft", body="Mine")
        fields = {"summary": "", "status": "planned", "level_start": None, "level_end": None}
        service.update(adventure.id, name="A", body="Hand", **fields)
        with pytest.raises(EditConflict):
            service.update(adventure.id, name="A", body="Mine", base=text_version(""), **fields)

    async def test_live_fragment_and_events(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        revision = registry.gamestate.prose.revision(chapter.id)
        live = (await ac.get(f"/gm/chapters/{chapter.id}/live")).text
        assert live.startswith('<div id="chapter-live"') and f'data-rev="{revision}"' in live

    async def test_order_and_delete(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        a = registry.adventures.create_chapter(adventure.id, "a")
        b = registry.adventures.create_chapter(adventure.id, "b")
        await ac.post(f"/gm/chapters/{b.id}/move", data={"step": "-1"})
        assert [c.title for c in registry.adventures.chapters(adventure.id)] == ["b", "a"]
        assert (await ac.get(f"/gm/chapters/{a.id}/delete")).status_code == 200
        gone = await ac.post(f"/gm/chapters/{a.id}/delete")
        assert gone.headers["location"] == f"/gm/adventures/{adventure.id}?deleted=1"
        assert (await ac.get(f"/gm/chapters/{a.id}")).status_code == 404
        await ac.post(f"/gm/adventures/{adventure.id}/delete")
        assert (await ac.get(f"/gm/adventures/{adventure.id}")).status_code == 404
        assert registry.worlds.delete(world.id) == 0  # nothing left in the way

    async def test_uploads(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        back = f"/gm/worlds/{world.id}/edit"
        done = await ac.post(
            f"/gm/media/{world.id}",
            files={"file": ("Cover.png", PNG, "image/png")},
            data={"next": back, "as_cover": "1"},
        )
        assert done.headers["location"] == f"{back}?uploaded=media%2Fcover.png"
        assert registry.gamestate.worlds.get(world.id).cover == "media/cover.png"
        assert "Uploaded as media/cover.png." in (await ac.get(done.headers["location"])).text
        refused = await ac.post(
            f"/gm/media/{world.id}",
            files={"file": ("x.png", b"not an image", "image/png")},
            data={"next": "//evil.example/"},
        )
        assert refused.headers["location"] == "/gm/worlds?error=media.error.content"
        page = (await ac.get(f"{back}?error=media.error.content")).text
        assert "does not match its extension" in page
        assert "{nope}" not in (await ac.get(f"{back}?error=%7Bnope%7D")).text
        cleared = await ac.post(f"/gm/media/{world.id}/cover", data={"path": "", "next": back})
        assert cleared.status_code == 303
        assert registry.gamestate.worlds.get(world.id).cover is None

    async def test_players_may_not(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        for url in (
            f"/gm/worlds/{world.id}",
            f"/gm/adventures/{adventure.id}",
            f"/gm/chapters/{chapter.id}",
            f"/gm/chapters/{chapter.id}/live",
        ):
            assert (await ac.get(url)).status_code == 403, url
        posted = await ac.post(f"/gm/chapters/{chapter.id}/edit", data={"title": "Mine"})
        assert posted.status_code == 403

    async def test_unknown_ids_are_404(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        for url in ("/gm/worlds/nope", "/gm/adventures/nope", "/gm/chapters/nope"):
            assert (await ac.get(url)).status_code == 404, url
