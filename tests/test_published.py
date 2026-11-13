"""Published adventures as a base (documentation/ADVENTURES.md §7) — against the real corpus."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Adventure, Role, SourceSet, World
from app.services.adventures import AdventureError
from app.services.i18n import english
from app.store.compendium import CompendiumStore
from app.store.maps import MapIndex
from app.store.published import PublishedAdventures, body_sections
from app.store.registry import StoreRegistry
from app.ui import tags
from app.ui.book_pages import book_chapter, creatures_in
from app.ui.markdown import MarkdownContext, split_anchors

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def published(corpus: Path) -> PublishedAdventures:
    return PublishedAdventures(corpus)


class Reader:
    lang = "en"
    t = english()
    path = "/gm/chapters/c1"
    identity = None


class TestStore:
    def test_the_index(self, published: PublishedAdventures) -> None:
        books = published.all()
        assert len(books) == 104
        lmop = published.get("lmop")
        assert lmop is not None and (lmop.name, lmop.source) == ("Lost Mine of Phandelver", "LMoP")
        assert (lmop.level_start, lmop.level_end) == (1, 5)
        assert [c.ordinal for c in lmop.contents][:2] == [None, "Part 1"]
        # id and source differ for two adventures
        nss = published.get("MOT-NSS")
        assert nss is not None and nss.source == "MOT"

    def test_a_body_is_read_once_and_kept(self, published: PublishedAdventures) -> None:
        body = published.body("LMoP")
        assert body is not None and published.body("lmop") is body
        assert [s.id for s in body.sections][:2] == ["000", "00c"]
        assert body.section_of("00e") == "00c"
        assert body.section_of("nope") is None
        assert body.places("00c")[:3] == [
            ("00e", "Goblin Ambush", 0),
            ("011", "Goblin Trail", 1),
            ("012", "Cragmaw Hideout", 0),
        ]

    def test_no_corpus_no_adventures(self) -> None:
        none = PublishedAdventures(None)
        assert none.all() == [] and none.body("LMoP") is None


class TestSplitAnchors:
    def test_blocks_come_out_in_order(self) -> None:
        rest, anchored = split_anchors(
            "Top.\n\n:::: at 00e\n::: npc Klarg\n:::\nTwo.\n::::\n\nMore.\n\n:::: at 012\nK\n::::\n"
        )
        assert rest.split() == ["Top.", "More."]
        assert anchored == [("00e", "::: npc Klarg\n:::\nTwo."), ("012", "K")]

    def test_an_unclosed_block_runs_to_the_end(self) -> None:
        assert split_anchors("Top\n:::: at 1\nlast") == ("Top", [("1", "last")])

    def test_nested_and_bare_blocks_stay(self) -> None:
        text = "::: npc Klarg\n:::\n"
        assert split_anchors(text) == (text, [])


@pytest.fixture(scope="module")
def rendered(
    published: PublishedAdventures, loaded_compendium: CompendiumStore
) -> tuple[str, list[str]]:
    body = published.body("LMoP")
    assert body is not None
    part = body.section("00c")
    assert part is not None
    ctx = MarkdownContext(
        tags=tags.RenderContext(resolver=loaded_compendium.resolver()), t=english()
    )
    overlay = (
        "Top note.\n\n:::: at 00e\nSix goblins.\n::::\n\n"
        ":::: at zzz\nLost.\n::::\n\n:::: at 00c\nEnd of section.\n::::\n"
    )
    result = book_chapter(Reader(), body, part, ctx, overlay, lambda i: f"/edit?at={i}")  # type: ignore[arg-type]
    return str(result.html), [h.text for h in result.toc]


class TestBookChapter:
    def test_notes_sit_after_their_entry(self, rendered: tuple[str, list[str]]) -> None:
        html, _ = rendered
        assert html.index("Top note.") < html.index("The adventure begins")
        ambush_end = html.index('id="e-012"')  # Cragmaw Hideout follows Goblin Ambush
        assert html.index("Six goblins.") < ambush_end
        assert html.index("End of section.") > html.index('id="e-012"')

    def test_a_lost_anchor_is_kept(self, rendered: tuple[str, list[str]]) -> None:
        html, _ = rendered
        assert "Notes whose place in the book was not found" in html
        assert "Your note at zzz" in html and "Lost." in html

    def test_the_book_becomes_headings_with_note_links(
        self, rendered: tuple[str, list[str]]
    ) -> None:
        html, toc = rendered
        assert '<section class="book-entry" id="e-00e"><h2 class="book-heading">' in html
        assert 'href="/edit?at=00e"' in html
        assert toc[:3] == ["Goblin Ambush", "Goblin Trail", "Cragmaw Hideout"]
        assert "Ceilings" not in toc  # three levels down: in the text, not the contents

    def test_images_become_placeholders_and_no_tag_is_left(
        self, rendered: tuple[str, list[str]]
    ) -> None:
        html, _ = rendered
        assert 'class="book-image"' in html and "Image not in your corpus" in html
        assert "{@" not in html

    def test_creatures_mentioned(
        self, published: PublishedAdventures, loaded_compendium: CompendiumStore
    ) -> None:
        body = published.body("LMoP")
        assert body is not None
        part = body.section("00c")
        assert part is not None
        found = creatures_in(part, loaded_compendium.resolver(), SourceSet(sources=["MM", "LMoP"]))
        names = {(m.name, m.source): m.count for m in found}
        assert names[("Goblin", "MM")] >= 4 and ("Bugbear", "MM") in names
        assert ("Wolf", "MM") in names  # the world's books, not the 2024 reprint


class TestSourceSet:
    def test_the_book_source_joins_the_worlds(self) -> None:
        world = World(name="W", source_set=SourceSet(sources=["PHB", "MM"]))
        adventure = Adventure(world_id=world.id, name="A", based_on="MOT-NSS")
        assert adventure.effective_source_set(world, "MOT").sources == ["PHB", "MM", "MOT"]


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


class TestPages:
    async def test_start_from_the_book_and_run_a_chapter(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Sword Coast", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
        )
        assert (
            "Lost Mine of Phandelver (LMoP, Levels 1–5)"
            in (await ac.get(f"/gm/worlds/{world.id}")).text
        )
        made = await ac.post(f"/gm/worlds/{world.id}/adventures/from-book", data={"book": "LMoP"})
        assert made.status_code == 303
        adventure = registry.adventures.adventures(world.id)[0]
        assert adventure.based_on == "LMoP" and adventure.level is not None
        chapters = registry.adventures.chapters(adventure.id)
        assert [c.section for c in chapters][:2] == ["000", "00c"]
        assert "Based on Lost Mine of Phandelver" in (await ac.get(made.headers["location"])).text

        goblins = chapters[1]
        registry.adventures.update_chapter(
            goblins.id,
            title=goblins.title,
            status="running",
            body=":::: at 00e\nSix goblins tonight.\n::::\n",
        )
        page = (await ac.get(f"/gm/chapters/{goblins.id}")).text
        assert "Six goblins tonight." in page and "Cragmaw Hideout" in page
        # LMoP's own creatures resolve though the world never declared the book
        assert 'href="/ref/monster/LMoP/Gundren%20Rockseeker' in page
        assert 'id="creatures"' in page
        live = (await ac.get(f"/gm/chapters/{goblins.id}/live")).text
        assert "Six goblins tonight." in live

        edit = (await ac.get(f"/gm/chapters/{goblins.id}/edit", params={"at": "012"})).text
        assert ":::: at 012\n\n::::" in edit and "Places in the book" in edit

    async def test_creatures_to_an_encounter(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create_from_book(world.id, "LMoP")
        chapter = registry.adventures.chapters(adventure.id)[1]
        bad = await ac.post(
            f"/gm/chapters/{chapter.id}/creatures", data={"ref": "Nothing|XX", "encounter": ""}
        )
        assert "error=encounters.error.monster" in bad.headers["location"]
        assert registry.encounters.list(world.id) == []  # nothing left behind
        await ac.post(f"/gm/chapters/{chapter.id}/creatures", data={"ref": "Goblin|MM"})
        (encounter,) = registry.encounters.list(world.id)
        assert (encounter.name, encounter.chapter_id) == (chapter.title, chapter.id)
        await ac.post(
            f"/gm/chapters/{chapter.id}/creatures",
            data={"ref": "Goblin|MM", "encounter": encounter.id},
        )
        assert registry.encounters.get(encounter.id).monsters[0].count == 2

    async def test_an_unknown_book(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        refused = await ac.post(
            f"/gm/worlds/{world.id}/adventures/from-book", data={"book": "NOPE"}
        )
        assert refused.status_code == 422 and "is not a published adventure" in refused.text
        with pytest.raises(AdventureError):
            registry.adventures.create_from_book(world.id, "")

    async def test_players_may_not(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        refused = await ac.post(
            f"/gm/worlds/{world.id}/adventures/from-book", data={"book": "LMoP"}
        )
        assert refused.status_code == 403


class TestBodyLayouts:
    SECTION = {"type": "section", "name": "One", "id": "000"}

    def test_the_older_layout(self) -> None:
        assert body_sections({"data": [self.SECTION]}) == [self.SECTION]

    @pytest.mark.parametrize("key", ["bookData", "adventureData"])
    def test_the_wrapped_layout_of_newer_releases(self, key: str) -> None:
        raw = {key: [{"id": "X", "source": "X", "data": [self.SECTION]}]}
        assert body_sections(raw) == [self.SECTION]

    def test_anything_else_is_empty(self) -> None:
        assert body_sections([]) == []


def _image_folder(registry: StoreRegistry, tmp_path: Path, *paths: str) -> None:
    """An image folder holding these (fake) pictures, as the user's 5eTools images would."""
    images = tmp_path / "img"
    for path in paths:
        (images / path).parent.mkdir(parents=True, exist_ok=True)
        (images / path).write_bytes(b"RIFF....WEBP")
    registry.settings.image_dir = images


CRAGMAW = "adventure/LMoP/Cragmaw Hideout.webp"


class TestMaps:
    """The maps each book prints (generated/gendata-maps.json): listed by chapter, each
    leading to where it sits; a map's numbered areas lead to their text. For reading."""

    def test_the_index(self, corpus: Path) -> None:
        maps = MapIndex(corpus).of("lmop")
        assert len(maps) == 8 and all(m.player is not None for m in maps)
        cragmaw = maps[1]
        assert (cragmaw.title, cragmaw.chapter, cragmaw.id) == ("Cragmaw Hideout", 1, "1ab")
        assert cragmaw.thumbnail == "adventure/LMoP/thumbnail/Cragmaw Hideout.webp"
        assert cragmaw.regions == 8 and cragmaw.player is not None
        assert cragmaw.player.path == "adventure/LMoP/Cragmaw Hideout (Player).webp"
        assert MapIndex(corpus).of("nothing") == ()
        assert MapIndex(None).of("LMoP") == ()

    async def test_an_adventure_from_the_book_lists_them_and_its_areas_lead_on(
        self, client: tuple[AsyncClient, StoreRegistry], tmp_path: Path
    ) -> None:
        ac, registry = client
        _image_folder(
            registry,
            tmp_path,
            CRAGMAW,
            "adventure/LMoP/thumbnail/Cragmaw Hideout.webp",
            "adventure/LMoP/Cragmaw Hideout (Player).webp",
        )
        world = registry.gamestate.worlds.create(
            World(name="Sword Coast", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
        )
        made = await ac.post(f"/gm/worlds/{world.id}/adventures/from-book", data={"book": "LMoP"})
        adventure = registry.adventures.adventures(world.id)[0]
        goblins = registry.adventures.chapters(adventure.id)[1]
        page = (await ac.get(made.headers["location"])).text
        assert 'class="maps-panel' in page and "8 maps" in page
        assert f'href="/gm/chapters/{goblins.id}#e-1ab"' in page  # where the map sits
        assert 'src="/play/img/adventure/LMoP/thumbnail/Cragmaw%20Hideout.webp"' in page
        assert 'href="/play/img/adventure/LMoP/Cragmaw%20Hideout%20%28Player%29.webp"' in page
        assert "Phandalin" in page and "Image not in your corpus" in page  # no file for it
        # The player version goes to the players' journal from the chapter showing the map.
        share = re.search(r'href="(/gm/share\?[^"]+)" class="maps-share"', page)
        assert share is not None
        url = html.unescape(share.group(1))
        assert f"chapter={goblins.id}" in url and "Cragmaw+Hideout+%28Player%29" in url
        form = await ac.get(url)
        assert form.status_code == 200
        assert 'src="/play/img/adventure/LMoP/Cragmaw%20Hideout%20%28Player%29.webp"' in form.text

        chapter = (await ac.get(f"/gm/chapters/{goblins.id}")).text
        assert 'id="e-1ab"' in chapter and 'class="map-areas"' in chapter
        areas = chapter[chapter.index('class="map-areas"') :]
        areas = areas[: areas.index("</svg>")]
        assert areas.count("<polygon") == 8  # every numbered area of the hideout
        assert 'href="#e-' in areas and "<title>" in areas

    async def test_a_rulebook_lists_its_maps_and_shows_its_pictures(
        self, client: tuple[AsyncClient, StoreRegistry], tmp_path: Path
    ) -> None:
        ac, registry = client
        _image_folder(registry, tmp_path, "book/DMG/app-c-1.webp")
        contents = (await ac.get("/books/DMG")).text
        assert 'class="maps-panel' in contents and "Map 1" in contents
        assert 'href="/books/DMG/12#e-233"' in contents
        chapter = (await ac.get("/books/DMG/12")).text
        assert 'id="e-233"' in chapter and 'src="/play/img/book/DMG/app-c-1.webp"' in chapter

    async def test_search_finds_maps_where_they_lead(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        books = SourceSet(sources=["PHB", "MM", "DMG"])
        world = registry.gamestate.worlds.create(World(name="Sword Coast", source_set=books))
        other = registry.gamestate.worlds.create(World(name="Elsewhere", source_set=books))
        await ac.post(f"/gm/worlds/{world.id}/adventures/from-book", data={"book": "LMoP"})
        adventure = registry.adventures.adventures(world.id)[0]
        goblins = registry.adventures.chapters(adventure.id)[1]

        found = (
            await ac.get(
                "/search", params={"q": "Cragmaw Hideout", "world": world.id, "partial": "1"}
            )
        ).text
        assert f"/gm/chapters/{goblins.id}?world={world.id}#e-1ab" in found  # at the map
        assert "Lost Mine of Phandelver · Part 1" in found
        elsewhere = (
            await ac.get(
                "/search", params={"q": "Cragmaw Hideout", "world": other.id, "partial": "1"}
            )
        ).text
        assert "#e-1ab" not in elsewhere  # no adventure of that world is based on the book
        rulebook = (
            await ac.get("/search", params={"q": "Map 1", "world": world.id, "partial": "1"})
        ).text
        assert f"/books/DMG/12?world={world.id}#e-233" in rulebook

        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        player = (
            await ac.get(
                "/search", params={"q": "Cragmaw Hideout", "world": world.id, "partial": "1"}
            )
        ).text
        assert "#e-1ab" not in player and "/gm/chapters" not in player
