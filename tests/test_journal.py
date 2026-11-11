"""The players' journal (documentation/JOURNAL.md): sharing a chapter's boxes and
pictures with chosen characters, as frozen copies, and reading them."""

from __future__ import annotations

import io
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, World
from app.models.pc import DraftStatus, PlayerCharacter
from app.models.protocol import EntryKind, Visibility
from app.store.registry import StoreRegistry
from app.ui.markdown import MarkdownContext, callouts, render_markdown

PASSWORD = "correct horse"
BODY = """Intro.

> [!handout] A torn letter
> Meet me at the mill. {@npc Fenn}
> ![[map.png]]

> [!secret]
> Fenn is a traitor.

> [!read]
> The rain stops.
>
> > [!secret]
> > Only the gamemaster knows.

![[map.png|torn]]
"""


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 40, 40)).save(buffer, "PNG")
    return buffer.getvalue()


class TestBoxes:
    def test_every_box_but_secrets_is_found_with_its_lines(self) -> None:
        found = callouts(BODY)
        assert [c.kind for c in found] == ["handout", "read"]
        assert found[0].title == "A torn letter"
        assert "Meet me at the mill" in found[0].text and "![[map.png]]" in found[0].text
        assert "The rain stops" in found[1].text
        assert "Only the gamemaster" not in found[1].text  # a nested secret is taken out
        assert len({c.key for c in found}) == 2

    def test_share_buttons_sit_on_boxes_and_pictures_never_on_secrets(self) -> None:
        asked: list[tuple[str, str]] = []

        def share(kind: str, key: str) -> str:
            asked.append((kind, key))
            return f"/share/{kind}/{key}"

        ctx = MarkdownContext(find_media=lambda name: f"/m/{name}", share=share)
        out = str(render_markdown(BODY, ctx).html)
        boxes = [key for kind, key in asked if kind == "box"]
        assert boxes == [c.key for c in callouts(BODY)]
        assert ("image", "map.png|torn") in asked
        assert out.count('class="md-share"') == len(asked)
        secret = out.split('class="md-callout md-secret"')[1].split("</aside>")[0]
        assert "md-share" not in secret

    def test_players_read_campaign_links_as_words(self) -> None:
        plain = str(render_markdown("Ask {@npc Fenn}.\n", MarkdownContext(plain=True)).html)
        assert "Fenn" in plain and "missing" not in plain
        marked = str(render_markdown("Ask {@npc Fenn}.\n", MarkdownContext()).html)
        assert "missing" in marked


@pytest.fixture
async def setup(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, dict[str, str]]]:
    registry = StoreRegistry(
        Settings(
            library_dir=tmp_path / "lib",
            reference_cache_dir=tmp_path / "cache",
            watch_files=False,
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    registry.auth.create_user("bob", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(World(name="W"))
    adventure = registry.adventures.create(world.id, "A")
    chapter = registry.adventures.create_chapter(adventure.id, "The Mill")
    registry.adventures.upload(chapter.id, "map.png", _png(), as_cover=False)
    registry.adventures.update_chapter(chapter.id, title="The Mill", status="planned", body=BODY)
    thorin = registry.gamestate.characters.create(
        PlayerCharacter(name="Thorin", player="alice", world_id=world.id, status=DraftStatus.READY)
    )
    mira = registry.gamestate.characters.create(
        PlayerCharacter(name="Mira", player="bob", world_id=world.id, status=DraftStatus.READY)
    )
    app = create_app()
    app.state.stores = registry
    ids = {"world": world.id, "chapter": chapter.id, "thorin": thorin.id, "mira": mira.id}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry, ids


async def _login(ac: AsyncClient, name: str) -> None:
    await ac.post("/logout")
    await ac.post("/login", data={"username": name, "password": PASSWORD})


class TestSharing:
    async def test_a_box_goes_to_the_characters_ticked_as_a_frozen_copy(
        self, setup: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = setup
        await _login(ac, "dm")
        page = (await ac.get(f"/gm/chapters/{ids['chapter']}")).text
        share = re.search(r'href="(/gm/share\?[^"]*box=[^"]*)"', page)
        assert share is not None
        form = await ac.get(share.group(1).replace("&amp;", "&"))
        assert "Thorin" in form.text and "Mira" in form.text and "Meet me at the mill" in form.text
        key = callouts(BODY)[0].key
        done = await ac.post(
            "/gm/share",
            data={
                "chapter": ids["chapter"],
                "box": key,
                "title": "A torn letter",
                "characters": [ids["thorin"]],
            },
        )
        assert done.status_code == 303 and done.headers["location"].endswith("?shared=1")
        handout = registry.gamestate.handouts.list()[0]
        assert handout.characters == [ids["thorin"]] and handout.callout == "handout"
        assert (Path(registry.settings.library_dir) / "handouts" / handout.id / "map.png").is_file()
        # The log tells Thorin's player, privately.
        entries = [
            e for e in registry.protocol.store.entries(ids["world"]) if e.kind is EntryKind.HANDOUT
        ]
        assert [(e.subject, e.visibility) for e in entries] == [
            (f"character:{ids['thorin']}", Visibility.PRIVATE)
        ]
        # A later edit of the chapter does not reach the copy.
        registry.adventures.update_chapter(
            ids["chapter"], title="The Mill", status="planned", body=BODY.replace("mill", "MILL")
        )
        assert "Meet me at the mill" in registry.gamestate.handouts.get(handout.id).text

        await _login(ac, "alice")
        journal = (await ac.get("/journal")).text
        assert "A torn letter" in journal and "is-unread" in journal
        read = await ac.get(f"/journal/{handout.id}")
        assert "Meet me at the mill" in read.text and "Fenn" in read.text
        assert f"/journal/{handout.id}/img/map.png" in read.text
        assert "md-share" not in read.text and "/gm/" not in read.text.split("<main")[1]
        assert "is-unread" not in (await ac.get("/journal")).text
        picture = await ac.get(f"/journal/{handout.id}/img/map.png")
        assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"

        await _login(ac, "bob")
        assert "A torn letter" not in (await ac.get("/journal")).text
        assert (await ac.get(f"/journal/{handout.id}")).status_code == 404
        assert (await ac.get(f"/journal/{handout.id}/img/map.png")).status_code == 404

    async def test_a_picture_with_its_effects_and_withdrawing(
        self, setup: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = setup
        await _login(ac, "dm")
        refused = await ac.post(
            "/gm/share",
            data={"chapter": ids["chapter"], "image": "map.png|torn", "characters": []},
        )
        assert "error=journal.error.nobody" in refused.headers["location"]
        await ac.post(
            "/gm/share",
            data={
                "chapter": ids["chapter"],
                "image": "map.png|torn",
                "title": "The map",
                "characters": [ids["thorin"], ids["mira"]],
            },
        )
        handout = registry.gamestate.handouts.list()[0]
        assert handout.kind == "image" and handout.image == "map.png|torn"
        await _login(ac, "bob")
        page = (await ac.get(f"/journal/{handout.id}")).text
        assert f"/journal/{handout.id}/img/map.png?fx=torn" in page
        rendered = await ac.get(f"/journal/{handout.id}/img/map.png?fx=torn")
        assert rendered.headers["content-type"] == "image/webp"
        assert (await ac.post(f"/journal/{handout.id}/delete")).status_code == 403
        await _login(ac, "dm")
        gm_view = (await ac.get(f"/journal/{handout.id}")).text
        assert "Thorin" in gm_view and "Mira" in gm_view and "Withdraw" in gm_view
        await ac.post(f"/journal/{handout.id}/delete")
        assert registry.gamestate.handouts.list() == []
        assert not (Path(registry.settings.library_dir) / "handouts" / handout.id).exists()

    async def test_drafts_are_not_offered(
        self, setup: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        _ac, registry, ids = setup
        registry.gamestate.characters.create(
            PlayerCharacter(name="Halfway", player="alice", world_id=ids["world"])
        )
        assert [name for _, name in registry.handouts.recipients(ids["world"])] == [
            "Mira",
            "Thorin",
        ]

    async def test_players_may_not_share(
        self, setup: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, _registry, ids = setup
        await _login(ac, "alice")
        page = await ac.get(f"/gm/share?chapter={ids['chapter']}&image=map.png")
        assert page.status_code == 403


@pytest.fixture
async def book_setup(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, dict[str, str]]]:
    registry = StoreRegistry(
        Settings(
            reference_dir=corpus,
            library_dir=tmp_path / "lib",
            reference_cache_dir=tmp_path / "cache",
            watch_files=False,
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(World(name="W"))
    adventure = registry.adventures.create_from_book(world.id, "LMoP")
    chapter = registry.adventures.chapters(adventure.id)[1]
    thorin = registry.gamestate.characters.create(
        PlayerCharacter(name="Thorin", player="alice", world_id=world.id, status=DraftStatus.READY)
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry, {"chapter": chapter.id, "thorin": thorin.id}


class TestTheBooksBoxes:
    async def test_a_read_aloud_box_of_the_book_is_shared_as_it_reads(
        self, book_setup: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = book_setup
        await _login(ac, "dm")
        page = (await ac.get(f"/gm/chapters/{ids['chapter']}")).text
        links = re.findall(r'href="(/gm/share\?[^"]*book=[^"]*)"', page)
        assert links, "the book's boxes carry share buttons"
        form = await ac.get(links[0].replace("&amp;", "&"))
        assert form.status_code == 200 and "Thorin" in form.text
        key = re.search(r'name="book" value="([^"]+)"', form.text)
        assert key is not None
        await ac.post(
            "/gm/share",
            data={"chapter": ids["chapter"], "book": key.group(1), "characters": [ids["thorin"]]},
        )
        (handout,) = registry.gamestate.handouts.list()
        assert handout.entry is not None and handout.entry["type"] in (
            "insetReadaloud",
            "inset",
            "quote",
        )
        await _login(ac, "alice")
        read = (await ac.get(f"/journal/{handout.id}")).text
        assert 'class="book-text"' in read and "md-share" not in read
        missing = await ac.get(f"/gm/share?chapter={ids['chapter']}&book=nonsense")
        assert missing.status_code == 403  # a player may not even look

    async def test_a_box_the_book_does_not_have_is_not_found(
        self, book_setup: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, _registry, ids = book_setup
        await _login(ac, "dm")
        assert (
            await ac.get(f"/gm/share?chapter={ids['chapter']}&book=nonsense")
        ).status_code == 404
