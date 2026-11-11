"""NPCs, treasure and scope (documentation/ADVENTURES.md §4)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Coins, Encounter, Role, Scope, SourceSet, World
from app.models.pc import PlayerCharacter
from app.services import scope
from app.services.adventures import AdventureService, text_version
from app.services.npcs import NpcService, ObjectError, TreasureService, clean_roles
from app.services.scope import ScopeError
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry
from app.ui.markdown import embeds_to_tags

PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32

CREATURES = {("bugbear", "mm"): "Bugbear", ("drow mage", "mm"): "Drow Mage"}
ITEMS = {("potion of healing", "dmg"): "Potion of Healing", ("rope, hempen (50 feet)", "phb"): ""}


def creature(name: str, source: str) -> Any:
    found = CREATURES.get((name.lower(), source.lower()))
    return SimpleNamespace(name=found, source=source.upper()) if found else None


def item(name: str, source: str) -> Any:
    key = (name.lower(), source.lower())
    if key not in ITEMS:
        return None
    return SimpleNamespace(name=ITEMS[key] or name, source=source.upper())


class World3:
    """A world with two adventures; the first has two chapters."""

    def __init__(self, store: GameStateStore) -> None:
        store.prose.load()
        self.store = store
        self.adventures = AdventureService(store)
        self.world = store.worlds.create(World(name="Sunken Marches"))
        self.bell = self.adventures.create(self.world.id, "The Drowned Bell")
        self.other = self.adventures.create(self.world.id, "Icespire")
        self.ch1 = self.adventures.create_chapter(self.bell.id, "Fog on the River")
        self.ch2 = self.adventures.create_chapter(self.bell.id, "Grey Reed")
        self.npcs = NpcService(store, self.adventures, creature)
        self.treasure = TreasureService(store, self.adventures, item)

    def at(self, record: str) -> Scope:
        return {
            "world": Scope(world_id=self.world.id),
            "bell": Scope(world_id=self.world.id, adventure_id=self.bell.id),
            "ch1": Scope(world_id=self.world.id, adventure_id=self.bell.id, chapter_id=self.ch1.id),
            "ch2": Scope(world_id=self.world.id, adventure_id=self.bell.id, chapter_id=self.ch2.id),
            "other": Scope(world_id=self.world.id, adventure_id=self.other.id),
        }[record]


@pytest.fixture
def w(gamestate: GameStateStore) -> World3:
    return World3(gamestate)


class TestScope:
    def test_nearest_first(self, w: World3) -> None:
        world_captain = w.npcs.create("Guard Captain", f"w:{w.world.id}", w.world.id)
        local = w.npcs.create("Guard Captain", f"c:{w.ch1.id}", w.world.id)
        assert w.npcs.find("guard captain", w.at("ch1")) == local
        assert w.npcs.find("Guard Captain", w.at("ch2")) == world_captain
        assert w.npcs.find("Guard Captain", w.at("other")) == world_captain

    def test_a_chapters_npc_is_still_found_from_elsewhere(self, w: World3) -> None:
        gnasher = w.npcs.create("Gnasher", f"c:{w.ch1.id}", w.world.id)
        assert w.npcs.find("Gnasher", w.at("ch2")) == gnasher
        assert w.npcs.find("Gnasher", w.at("other")) == gnasher
        assert w.npcs.find("Nobody", w.at("ch1")) is None

    def test_another_worlds_npc_is_never_found(self, w: World3) -> None:
        elsewhere = w.store.worlds.create(World(name="Kestria"))
        w.npcs.create("Gnasher", f"w:{elsewhere.id}", elsewhere.id)
        assert w.npcs.find("Gnasher", w.at("ch1")) is None

    def test_decode_fills_in_parents(self, w: World3) -> None:
        assert scope.decode(f"c:{w.ch1.id}", w.adventures) == w.at("ch1")
        assert scope.decode(f"a:{w.bell.id}", w.adventures) == w.at("bell")
        assert scope.decode(w.world.id, w.adventures) == w.at("world")  # a bare world id
        for bad in ("c:nope", "a:", "x:y"):
            with pytest.raises(ScopeError):
                scope.decode(bad, w.adventures)

    def test_decode_keeps_to_its_world(self, w: World3) -> None:
        elsewhere = w.store.worlds.create(World(name="Kestria"))
        with pytest.raises(ScopeError):
            scope.decode(f"c:{w.ch1.id}", w.adventures, elsewhere.id)

    def test_choices_in_reading_order(self, w: World3) -> None:
        values = [
            (v.split(":")[0], label, depth)
            for v, label, depth in scope.choices(w.adventures, w.world.id)
        ]
        assert values == [
            ("w", "Sunken Marches", 0),
            ("a", "The Drowned Bell", 1),
            ("c", "The Drowned Bell › Fog on the River", 2),
            ("c", "The Drowned Bell › Grey Reed", 2),
            ("a", "Icespire", 1),
        ]

    def test_within(self, w: World3) -> None:
        assert scope.within(w.at("ch1"), w.at("bell"))
        assert not scope.within(w.at("other"), w.at("bell"))
        assert scope.within(w.at("bell"), w.at("world"))
        assert not scope.within(w.at("bell"), w.at("ch1"))


class TestNpcs:
    def test_roles_are_cleaned(self) -> None:
        assert clean_roles(["ally", " Pale  Weaver, BOSS", "boss", ""]) == [
            "boss",
            "ally",
            "pale weaver",
        ]

    def test_update_checks_the_statblock(self, w: World3) -> None:
        npc = w.npcs.create("Vessk", f"w:{w.world.id}", w.world.id)
        saved = w.npcs.update(
            npc.id,
            name="Vessk",
            scope_value=f"w:{w.world.id}",
            roles=["boss"],
            statblock="drow mage|mm",
            notes="",
        )
        assert saved.statblock == "Drow Mage|MM"  # the corpus's own spelling
        with pytest.raises(ObjectError):
            w.npcs.update(
                npc.id,
                name="Vessk",
                scope_value=f"w:{w.world.id}",
                roles=[],
                statblock="Tarrasque|XX",
                notes="",
            )
        assert w.npcs.bosses(w.world.id) == [saved]

    def test_a_rename_follows_into_the_texts(self, w: World3) -> None:
        npc = w.npcs.create("Gnasher", f"c:{w.ch1.id}", w.world.id)
        w.store.chapters.update(
            w.ch2.id,
            body="{@npc Gnasher} and {@npc Gnasher|the bugbear}.\n\n::: npc Gnasher\n:::\n",
        )
        w.npcs.update(
            npc.id,
            name="Gnasher the Bugbear",
            scope_value=f"c:{w.ch1.id}",
            roles=[],
            statblock="",
            notes="",
        )
        assert w.store.chapters.get(w.ch2.id).body == (
            "{@npc Gnasher the Bugbear} and {@npc Gnasher the Bugbear|the bugbear}.\n\n"
            "::: npc Gnasher the Bugbear\n:::"
        )

    def test_portrait_lives_beside_its_scope(self, w: World3) -> None:
        npc = w.npcs.create("Gnasher", f"c:{w.ch1.id}", w.world.id)
        path = w.npcs.upload_portrait(npc.id, "Gnasher.png", PNG)
        assert path == "media/gnasher.png"
        assert w.adventures.media_path(w.ch1.id, path) is not None
        # Moving the NPC elsewhere drops a portrait that would no longer resolve.
        moved = w.npcs.update(
            npc.id,
            name="Gnasher",
            scope_value=f"w:{w.world.id}",
            roles=[],
            statblock="",
            notes="",
        )
        assert moved.portrait is None


class TestTreasure:
    def test_items_merge_count_and_go(self, w: World3) -> None:
        chest = w.treasure.create("Gnasher's chest", f"c:{w.ch1.id}", w.world.id)
        w.treasure.add_item(chest.id, "potion of healing|dmg")
        chest = w.treasure.add_item(chest.id, "Potion of Healing|DMG", 2)
        assert [(i.name, i.count) for i in chest.items] == [("Potion of Healing", 3)]
        chest = w.treasure.set_count(chest.id, 0, 0)
        assert chest.items == []
        with pytest.raises(ObjectError):
            w.treasure.add_item(chest.id, "Vorpal Sword|XX")
        with pytest.raises(ObjectError):
            w.treasure.set_count(chest.id, 3, 1)

    def test_coins(self, w: World3) -> None:
        chest = w.treasure.create("Chest", f"w:{w.world.id}", w.world.id)
        saved = w.treasure.update(
            chest.id,
            name="Chest",
            scope_value=f"w:{w.world.id}",
            coins=Coins(gp=600, sp=110),
            notes="",
        )
        assert saved.coins.copper == 61_100

    def test_deleting_an_adventure_lifts_what_it_held(self, w: World3) -> None:
        npc = w.npcs.create("Gnasher", f"c:{w.ch1.id}", w.world.id)
        chest = w.treasure.create("Chest", f"a:{w.bell.id}", w.world.id)
        enc = w.store.encounters.create(
            Encounter(
                name="Ambush", world_id=w.world.id, adventure_id=w.bell.id, chapter_id=w.ch1.id
            )
        )
        w.adventures.delete_chapter(w.ch1.id)
        assert w.npcs.get(npc.id).scope == w.at("bell")
        w.adventures.delete(w.bell.id)
        assert w.npcs.get(npc.id).scope == w.at("world")
        assert w.treasure.get(chest.id).scope == w.at("world")
        lifted = w.store.encounters.get(enc.id)
        assert (lifted.adventure_id, lifted.chapter_id) == (None, None)


class TestEncounterScope:
    def test_old_files_with_module_id_still_load(self) -> None:
        enc = Encounter.model_validate({"name": "Old", "world_id": "w", "module_id": None})
        assert enc.scope == Scope(world_id="w")

    def test_no_world_no_scope(self) -> None:
        assert Encounter(name="Loose").scope is None


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
    async def test_from_a_chapter_to_its_text(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Sunken Marches", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
        )
        adventure = registry.adventures.create(world.id, "The Drowned Bell")
        chapter = registry.adventures.create_chapter(adventure.id, "Fog on the River")
        place = f"c:{chapter.id}"

        made = await ac.post(
            f"/gm/worlds/{world.id}/npcs", data={"name": "Gnasher", "place": place}
        )
        npc = registry.npcs.list(world.id)[0]
        assert made.headers["location"] == f"/gm/npcs/{npc.id}/edit"
        saved = await ac.post(
            f"/gm/npcs/{npc.id}/edit",
            data={"name": "Gnasher", "place": place, "roles": ["boss"], "statblock": "Bugbear|MM"},
        )
        assert saved.status_code == 303
        refused = await ac.post(
            f"/gm/npcs/{npc.id}/edit",
            data={"name": "Gnasher", "place": place, "statblock": "Nothing|XX"},
        )
        assert refused.status_code == 422 and "is not a creature" in refused.text

        made = await ac.post(
            f"/gm/worlds/{world.id}/treasure", data={"name": "Gnasher's chest", "place": place}
        )
        chest = registry.treasure.list(world.id)[0]
        assert made.headers["location"] == f"/gm/treasure/{chest.id}"
        await ac.post(f"/gm/treasure/{chest.id}/items", data={"ref": "Potion of Healing|DMG"})
        await ac.post(
            f"/gm/treasure/{chest.id}",
            data={"name": "Gnasher's chest", "place": place, "gp": "600", "sp": "x"},
        )
        assert registry.treasure.get(chest.id).coins == Coins(gp=600)

        made = await ac.post(
            "/gm/encounters", data={"name": "Cave ambush", "place": place, "world": world.id}
        )
        assert made.status_code == 303
        encounter = registry.encounters.within(
            Scope(world_id=world.id, chapter_id=chapter.id, adventure_id=adventure.id)
        )[0]
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 3)

        registry.adventures.update_chapter(
            chapter.id,
            title="Fog on the River",
            status="running",
            body="{@npc Gnasher} waits.\n\n{@npc Gnasher}\n\n{@treasure Gnasher's chest}\n\n"
            "{@encounter Cave ambush}\n",
        )
        page = (await ac.get(f"/gm/chapters/{chapter.id}")).text
        assert f'href="/gm/npcs/{npc.id}" class="ref lib npc"' in page
        assert 'class="npc-embed"' in page and "Bugbear" in page
        assert "Potion of Healing" in page and "600 gp" in page
        assert "3× Goblin" in page
        assert "{@npc Gnasher}</code>" in page  # the holdings panel offers the line to paste

        world_page = (await ac.get(f"/gm/worlds/{world.id}")).text
        assert "Bosses" in world_page and "Gnasher" in world_page

    async def test_a_character_in_the_text(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        pc = registry.gamestate.characters.create(
            PlayerCharacter(name="Brunhild", world_id=world.id)
        )
        registry.gamestate.characters.create(PlayerCharacter(name="Elsewhere"))
        registry.adventures.update_chapter(
            chapter.id,
            title="C",
            status="running",
            body="Ask {@character brunhild|her}.\n\n{@character Brunhild}\n\n"
            "{@character Elsewhere}\n",
        )
        page = (await ac.get(f"/gm/chapters/{chapter.id}")).text
        assert f'href="/characters/{pc.id}" class="ref lib character"' in page
        assert "md-embed-character" in page and "pc-hp-row" in page
        # A character of another world is not this world's.
        assert "“Elsewhere” is not in this world." in page

    async def test_notes_changed_elsewhere_are_not_overwritten(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        npc = registry.npcs.create("Gnasher", f"w:{world.id}", world.id)
        chest = registry.treasure.create("Chest", f"w:{world.id}", world.id)
        for url, record in ((f"/gm/npcs/{npc.id}/edit", npc), (f"/gm/treasure/{chest.id}", chest)):
            opened = (await ac.get(url)).text
            base = re.search(r'name="base" value="(\w+)"', opened)
            assert base is not None and base.group(1) == text_version("")
            collection = registry.gamestate.npcs if record is npc else registry.gamestate.treasures
            collection.update(record.id, notes="From another tab.")
            mine = {"name": record.name, "place": f"w:{world.id}", "notes": "Mine."}
            refused = await ac.post(url, data=mine | {"base": base.group(1)})
            assert refused.status_code == 409, url
            assert ">Mine.</textarea>" in refused.text
            assert "From another tab.</textarea>" in refused.text
            assert collection.get(record.id).notes == "From another tab."
            again = re.search(r'name="base" value="(\w+)"', refused.text)
            assert again is not None
            saved = await ac.post(url, data=mine | {"base": again.group(1)})
            assert saved.status_code == 303 and collection.get(record.id).notes == "Mine."

    async def test_a_character_card_in_a_chapter_stays_live(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        pc = registry.gamestate.characters.create(
            PlayerCharacter(name="Brunhild", world_id=world.id)
        )
        registry.adventures.update_chapter(
            chapter.id, title="C", status="running", body="{@character Brunhild}"
        )
        page = (await ac.get(f"/gm/chapters/{chapter.id}")).text
        assert f'data-party-world="{world.id}"' in page and f'id="pc-{pc.id}"' in page
        assert "/static/js/party.js" in page and "/static/js/htmx.min.js" in page
        # party.js refetches the card as the chapter shows it.
        card = (await ac.get(f"/gm/party/{pc.id}/card", params={"world": world.id})).text
        assert f'id="pc-{pc.id}"' in card and f'name="world" value="{world.id}"' in card

    async def test_old_blocks_are_rewritten_as_tags(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        _ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        chapter = registry.adventures.create_chapter(adventure.id, "C")
        registry.adventures.update_chapter(
            chapter.id, title="C", status="draft", body="::: npc Gnasher\nHe hides.\n:::\n"
        )
        npc = registry.npcs.create("Gnasher", f"w:{world.id}", world.id)
        registry.gamestate.npcs.update(npc.id, notes="::: creature Goblin\n:::")
        assert registry.adventures.rewrite_all_texts(embeds_to_tags) == 2
        assert registry.adventures.get_chapter(chapter.id).body == "{@npc Gnasher}\n\nHe hides."
        assert registry.gamestate.npcs.get(npc.id).notes == "{@creature Goblin}"
        assert registry.adventures.rewrite_all_texts(embeds_to_tags) == 0

    async def test_the_item_picker(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        chest = registry.treasure.create("Chest", f"w:{world.id}", world.id)
        fragment = (
            await ac.get(
                "/search", params={"q": "potion of heal", "pick": chest.id, "partial": "1"}
            )
        ).text
        assert f'formaction="/gm/treasure/{chest.id}/items"' in fragment
        assert "Potion of Healing" in fragment
        empty = (await ac.get(f"/gm/treasure/{chest.id}")).text
        assert "Search the items in this world" in empty

    async def test_encounter_scope_setting(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        adventure = registry.adventures.create(world.id, "A")
        enc = registry.encounters.create("Ambush", world.id)
        await ac.post(
            f"/gm/encounters/{enc.id}",
            data={"name": "Ambush", "world": f"a:{adventure.id}", "rules": ""},
        )
        saved = registry.encounters.get(enc.id)
        assert (saved.world_id, saved.adventure_id) == (world.id, adventure.id)
        page = (await ac.get(f"/gm/encounters/{enc.id}")).text
        assert "W › A" in page
        bad = await ac.post(f"/gm/encounters/{enc.id}", data={"name": "Ambush", "world": "c:nope"})
        assert bad.status_code == 422

    async def test_players_may_not(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        npc = registry.npcs.create("Gnasher", f"w:{world.id}", world.id)
        chest = registry.treasure.create("Chest", f"w:{world.id}", world.id)
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        for url in (
            f"/gm/worlds/{world.id}/npcs",
            f"/gm/npcs/{npc.id}",
            f"/gm/treasure/{chest.id}",
        ):
            assert (await ac.get(url)).status_code == 403, url
        fragment = (
            await ac.get("/search", params={"q": "potion", "pick": chest.id, "partial": "1"})
        ).text
        assert "res-pick" not in fragment

    async def test_unknown_ids(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        for url in ("/gm/npcs/nope", "/gm/treasure/nope", "/gm/worlds/nope/npcs"):
            assert (await ac.get(url)).status_code == 404, url
