"""The Markdown editors' insert pop-up (app/ui/insert.py, app/ui/palette.py): the search,
its results inserting their tag — as short as the world allows — and the world's own
NPCs, encounters, treasure and chapters."""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Npc, Role, Scope, SourceSet, World
from app.services.search import Row
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry
from app.ui.insert import TAG_OF, tag_maker

PASSWORD = "correct horse battery"


def _row(compendium: CompendiumStore, category: str, key: str) -> Row:
    entry = compendium.get(category, key)
    return Row(category, entry.name, entry.source, entry)


class TestTags:
    def test_as_short_as_the_world_allows(self, loaded_compendium: CompendiumStore) -> None:
        resolver = loaded_compendium.resolver()
        goblin = _row(loaded_compendium, "monster", "goblin|mm")
        only_2014 = tag_maker(resolver, SourceSet(sources=["MM"]))
        assert only_2014(goblin) == "{@creature Goblin}"
        # Where the world's books resolve the bare name elsewhere, the book is named.
        fireball = _row(loaded_compendium, "spell", "fireball|phb")
        both = tag_maker(resolver, SourceSet(sources=["PHB", "XPHB"]))
        assert both(fireball) == "{@spell Fireball|PHB}"
        assert both(_row(loaded_compendium, "spell", "fireball|xphb")) == "{@spell Fireball}"

    def test_codes_deities_and_the_campaigns_own(self, loaded_compendium: CompendiumStore) -> None:
        tag = tag_maker(loaded_compendium.resolver(), SourceSet(sources=["XPHB"]))
        assert tag(_row(loaded_compendium, "itemProperty", "v|xphb")) == "{@itemProperty V}"
        assert tag(Row("npc", "Sildar Hallwinter", "", None)) == "{@npc Sildar Hallwinter}"
        assert tag(Row("book", "Grappling", "XPHB", None, url="/books/XPHB/1")) is None
        deity = next(e for e in loaded_compendium.all("deity") if getattr(e, "pantheon", None))
        row = Row("deity", deity.name, deity.source, deity)
        written = tag_maker(loaded_compendium.resolver(), SourceSet(sources=["ZZZ"]))(row)
        assert written == f"{{@deity {deity.name}|{deity.pantheon}|{deity.source}}}"
        assert TAG_OF["monster"] == "creature" and TAG_OF["baseitem"] == "item"


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
    )
    registry.gamestate.npcs.create(Npc(scope=Scope(world_id=world.id), name="Sildar Hallwinter"))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry


def _inserts(text: str) -> list[str]:
    return [html.unescape(x) for x in re.findall(r'data-insert="([^"]+)"', text)]


class TestRoute:
    async def test_results_insert_and_the_campaign_comes_first(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        found = await ac.get(
            "/search", params={"q": "sil", "insert": "1", "world": world.id, "partial": "1"}
        )
        tags = _inserts(found.text)
        assert tags[0] == "{@npc Sildar Hallwinter}"
        goblin = await ac.get(
            "/search", params={"q": "goblin", "insert": "1", "world": world.id, "partial": "1"}
        )
        assert "{@creature Goblin}" in _inserts(goblin.text)
        assert 'data-label-slot="2"' in goblin.text
        only = await ac.get(
            "/search",
            params={
                "q": "s",
                "insert": "1",
                "world": world.id,
                "group": "campaign",
                "partial": "1",
            },
        )
        assert _inserts(only.text) == ["{@npc Sildar Hallwinter}"]

    async def test_the_editors_carry_the_pop_up_and_players_get_links(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        npc = registry.gamestate.npcs.list()[0]
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        page = await ac.get(f"/gm/npcs/{npc.id}/edit")
        assert page.status_code == 200 and 'id="insert-palette"' in page.text
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        played = await ac.get(
            "/search", params={"q": "goblin", "insert": "1", "world": world.id, "partial": "1"}
        )
        # a player gets links, and only to what they may read: not the goblin creature
        assert not _inserts(played.text) and 'href="/ref/language/' in played.text
        assert 'href="/ref/monster/' not in played.text


class TestSuggest:
    async def test_the_drop_down_completes_references(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})

        async def complete(kind: str, q: str) -> list[str]:
            found = await ac.get(
                "/search/suggest", params={"kind": kind, "q": q, "world": world.id}
            )
            return [html.unescape(x) for x in re.findall(r'data-complete="([^"]+)"', found.text)]

        spells = await complete("spell", "fire")
        assert set(spells[:2]) == {"Fireball", "Fire Bolt"}  # names starting so come first
        assert all(not s.endswith("|XPHB") for s in spells)  # the world's books only
        goblins = await complete("statblock", "goblin")
        assert goblins[0] == "Goblin" and "Goblin Boss" in goblins
        assert await complete("npc", "sil") == ["Sildar Hallwinter"]
        assert await complete("nonsense", "a") == []
        registry.gamestate.worlds.update(
            world.id, source_set=SourceSet(sources=["PHB", "XPHB", "MM", "DMG"])
        )
        both = await complete("spell", "fireball")
        # One printing per entry, the world's: the bare name, which now means 2024's.
        assert both[0] == "Fireball" and "Fireball|PHB" not in both

    async def test_asked_with_no_name_it_lists_the_kind(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})

        async def browse(kind: str, **extra: str) -> list[str]:
            params = {"kind": kind, "q": "", "world": world.id} | extra
            found = await ac.get("/search/suggest", params=params)
            return [html.unescape(x) for x in re.findall(r'data-complete="([^"]+)"', found.text)]

        assert await browse("spell") == []  # typing nothing opens nothing by itself
        spells = await browse("spell", browse="1")
        assert spells and all(not s.endswith("|XPHB") for s in spells)
        links = registry.link_counts.counts()
        weights = [links.get(("spell", s.split("|")[0].lower()), 0) for s in spells]
        assert weights == sorted(weights, reverse=True) and weights[0] > 0  # most linked first
        assert "Sildar Hallwinter" in await browse("npc", browse="1")
        assert await browse("nonsense", browse="1") == []

    async def test_players_get_nothing(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        found = await ac.get("/search/suggest", params={"kind": "spell", "q": "fire"})
        assert "data-complete" not in found.text


class TestFolds:
    async def test_a_reference_folds_into_the_text(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Goblin Arrows")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        body = (
            "::: spell Fireball\n:::\n\n"
            "::: item Potion of Healing open\n:::\n\n"
            "::: spell Nope\n:::\n"
        )
        shown = (await ac.post(f"/gm/chapters/{chapter.id}/preview", data={"body": body})).text
        assert 'class="md-fold md-fold-spell"' in shown and "8d6" in shown
        assert '<details class="md-fold md-fold-item" open>' in shown
        assert "Nope is not in this world&#39;s books." in shown


class TestKindsAndCreating:
    async def test_the_kinds_before_a_name(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _registry = client
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})

        async def kinds(context: str, q: str = "") -> list[str]:
            found = await ac.get("/search/kinds", params={"context": context, "q": q})
            return [html.unescape(x) for x in re.findall(r'data-complete="([^"]+)"', found.text)]

        inline = await kinds("inline")
        assert inline[:4] == ["creature ", "spell ", "item ", "condition "]
        assert {"npc ", "encounter ", "treasure ", "character ", "chapter "} <= set(inline)
        assert await kinds("inline", "sp") == ["spell "]
        # The editor writes tags only: ``::: `` offers nothing any more.
        assert await kinds("block") == []
        callouts = await kinds("callout")
        assert callouts[:4] == ["read]", "secret]", "sidebar]", "variant]"]
        assert {"handout]", "pullquote]", "flowchart]", "gallery]"} <= set(callouts)
        assert {"abstract]", "danger]", "quote]"} <= set(callouts)
        assert await kinds("callout", "da") == ["danger]"]

    async def test_a_name_nothing_has_is_offered_and_created_where_you_write(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Phandalin")
        here = f"c:{chapter.id}"
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})

        async def suggest(kind: str, q: str) -> str:
            params = {"kind": kind, "q": q, "world": world.id, "scope": here}
            return (await ac.get("/search/suggest", params=params)).text

        offered = await suggest("npc", "Garaele")
        assert 'data-create="npc"' in offered and "New, in this chapter" in offered
        assert "data-create" not in await suggest("npc", "Sildar Hallwinter")  # it exists

        made = await ac.post(
            "/gm/editor/create", data={"kind": "npc", "name": "Garaele", "scope": here}
        )
        assert made.status_code == 201 and made.headers["location"].startswith("/gm/npcs/")
        garaele = [n for n in registry.gamestate.npcs.list() if n.name == "Garaele"]
        assert len(garaele) == 1 and garaele[0].scope.chapter_id == chapter.id
        again = await ac.post(
            "/gm/editor/create", data={"kind": "npc", "name": "Garaele", "scope": here}
        )
        assert again.headers["location"] == made.headers["location"]  # not twice
        encounter = await ac.post(
            "/gm/editor/create", data={"kind": "encounter", "name": "Redbrands", "scope": here}
        )
        assert encounter.status_code == 201
        assert registry.encounters.list(world.id)[0].chapter_id == chapter.id
        page = await ac.get(f"/gm/chapters/{chapter.id}/edit")
        assert f'data-scope="c:{chapter.id}"' in page.text

    async def test_players_create_nothing(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        refused = await ac.post(
            "/gm/editor/create", data={"kind": "npc", "name": "X", "scope": f"w:{world.id}"}
        )
        assert refused.status_code == 403


CHECKED = """# Phandalin

Cast {@spell Firebal} at the door, or {@spell Fireball|XPHB}.
Meet {@npc Garaele} and {@npc Sildar Hallwinter}.

```
{@spell Nope} is code and left alone.
```

::: encounter Empty camp
:::

::: spel Fireball
:::

![Map](media/none.png)

::: spell Fireball
"""


class TestChapterCheck:
    async def test_everything_that_leads_nowhere(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Phandalin")
        registry.encounters.create("Empty camp", world.id)
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        panel = html.unescape(
            (await ac.post(f"/gm/chapters/{chapter.id}/check", data={"body": CHECKED})).text
        )
        found = re.findall(r'class="check-item check-(\w+)">.*?data-line="(\d+)"', panel)
        assert found == [
            ("reference", "3"),  # Firebal
            ("excluded", "3"),  # the 2024 book this world leaves out
            ("library", "4"),  # Garaele; Sildar is there
            ("empty", "10"),
            ("block", "13"),
            ("image", "16"),
            ("unclosed", "18"),
        ]
        assert 'data-to="{@spell Fireball}"' in panel  # the nearest name
        assert 'data-kind="npc" data-name="Garaele"' in panel
        assert "Nope" not in panel  # code is left alone

    async def test_callouts_and_heading_links(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Phandalin")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        body = (
            "> [!danger]- Hag\n> x\n\n> [!lore]\n> y\n\n"
            "[[#The Inn]] [[#Nowhere]] [[Phandalin]] [[Neverwinter]]\n\n![[gone.png]]\n\n"
            "## The Inn\n"
        )
        panel = html.unescape(
            (await ac.post(f"/gm/chapters/{chapter.id}/check", data={"body": body})).text
        )
        found = re.findall(r'class="check-item check-(\w+)">.*?data-line="(\d+)"', panel)
        assert found == [("callout", "4"), ("heading", "7"), ("library", "7"), ("image", "9")]
        assert "[[#Nowhere]]" in panel and "Neverwinter" in panel

    async def test_tags_alone_on_their_line(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Phandalin")
        registry.encounters.create("Empty camp", world.id)
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        body = (
            "{@encounter Empty camp}\n\n{@character Nobody}\n\nWe reach {@encounter Empty camp}.\n"
        )
        panel = html.unescape(
            (await ac.post(f"/gm/chapters/{chapter.id}/check", data={"body": body})).text
        )
        found = re.findall(r'class="check-item check-(\w+)">.*?data-line="(\d+)"', panel)
        # Only the card has nothing to run; a character is not made from the text.
        assert found == [("empty", "1"), ("library", "3")]
        assert 'data-kind="character"' not in panel

    async def test_a_clean_chapter_says_so(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Phandalin")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        await ac.post(
            f"/gm/chapters/{chapter.id}/edit",
            data={"title": "Phandalin", "status": "draft", "body": "Meet {@creature goblin}."},
        )
        page = await ac.get(f"/gm/chapters/{chapter.id}/edit")
        assert "Every reference leads somewhere." in page.text
        assert f'data-check="/gm/chapters/{chapter.id}/check"' in page.text


class TestImagesInTheText:
    async def test_a_dropped_image_is_stored_and_its_path_returned(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        from tests.test_adventures import PNG

        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        adventure = registry.adventures.create(world.id, "Lost Mine")
        chapter = registry.adventures.create_chapter(adventure.id, "Cragmaw")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        stored = await ac.post(
            f"/gm/media/{chapter.id}/inline", files={"file": ("cave map.png", PNG, "image/png")}
        )
        assert stored.status_code == 201
        path = stored.headers["x-media-path"]
        assert path.startswith("media/") and path in registry.adventures.images(chapter.id)
        refused = await ac.post(
            f"/gm/media/{chapter.id}/inline", files={"file": ("notes.txt", b"hello", "text/plain")}
        )
        assert refused.status_code == 422 and refused.text  # the reason, to show
        page = await ac.get(f"/gm/chapters/{chapter.id}/edit")
        assert f'data-upload="/gm/media/{chapter.id}/inline"' in page.text
