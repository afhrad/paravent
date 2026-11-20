"""Deleted NPCs, places, treasure and encounters kept a while to restore
(app/services/trash.py, Check links)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Quest, Role, Scope
from app.services.trash import KEEP_DAYS, TrashError
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


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


def _world(registry: StoreRegistry) -> tuple[str, str, str]:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    registry.table.select(world.id)
    return world.id, bell.id, fog.id


def test_what_is_deleted_comes_back_where_it_was(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    hob = registry.npcs.create("Hob", f"c:{fog}", world)
    registry.gamestate.npcs.update(hob.id, notes="Rows the ferry.")
    ambush = registry.encounters.create(
        "Ambush", world, Scope(world_id=world, adventure_id=bell, chapter_id=fog)
    )
    registry.npcs.delete(hob.id)
    registry.encounters.delete(ambush.id)
    trash = registry.trash
    assert [t.name for t in trash.of_world(world)] == ["Ambush", "Hob"]  # newest first
    kept = next(t for t in trash.of_world(world) if t.name == "Hob")
    kind, back = trash.restore(kept.id)
    npc = registry.gamestate.npcs.get(hob.id)
    assert kind == "npc" and back.id == hob.id
    assert npc.scope.chapter_id == fog and npc.notes == "Rows the ferry."
    assert [t.name for t in trash.of_world(world)] == ["Ambush"]
    # Its chapter gone meanwhile: the encounter comes back into its adventure.
    registry.adventures.delete_chapter(fog)
    kind, again = trash.restore(next(t for t in trash.of_world(world) if t.name == "Ambush").id)
    encounter = registry.gamestate.encounters.get(ambush.id)
    assert encounter.adventure_id == bell and encounter.chapter_id is None
    with pytest.raises(TrashError):
        trash.restore("gone")


def test_old_deletions_are_forgotten_and_the_trash_empties(registry: StoreRegistry) -> None:
    world, _bell, _fog = _world(registry)
    for name in ("One", "Two"):
        registry.npcs.delete(registry.npcs.create(name, f"w:{world}", world).id)
    trash = registry.trash
    old = trash.of_world(world)[1]
    registry.gamestate.trash.update(
        old.id, deleted_at=datetime.now(UTC) - timedelta(days=KEEP_DAYS + 1)
    )
    trash.clear_old()
    assert [t.name for t in trash.of_world(world)] == ["Two"]
    assert trash.empty(world) == 1 and trash.of_world(world) == []


async def test_check_links_lists_and_restores(gm: AsyncClient, registry: StoreRegistry) -> None:
    world, _bell, _fog = _world(registry)
    mill = registry.locations.create("Old Mill", f"w:{world}", world)
    registry.locations.delete(mill.id)
    page = (await gm.get("/gm/world/links")).text
    assert "Recently deleted" in page and "Old Mill" in page
    kept = registry.trash.of_world(world)[0]
    back = await gm.post("/gm/world/links/restore", data={"id": kept.id})
    assert back.headers["location"] == f"/gm/places/{mill.id}"
    assert registry.gamestate.locations.get(mill.id).name == "Old Mill"
    registry.locations.delete(mill.id)
    emptied = await gm.post("/gm/world/links/empty-trash")
    assert emptied.headers["location"] == "/gm/world/links?fixed=emptied"
    assert registry.trash.of_world(world) == []


async def test_the_trash_says_which_texts_still_name_it(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world, _bell, fog = _world(registry)
    hob = registry.npcs.create("Hob", f"w:{world}", world)
    registry.gamestate.chapters.update(fog, body="{@npc Hob} rows, {@npc hob|he} waits.")
    registry.npcs.delete(hob.id)
    page = (await gm.get("/gm/world/links")).text
    assert "named in 1 text, whose link works again once it is back" in page


def test_a_deleted_chapter_comes_back_with_what_went_with_it(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    registry.gamestate.chapters.update(fog, body="The ferry waits.")
    hob = registry.npcs.create("Hob", f"c:{fog}", world)
    registry.adventures.delete_chapter(fog, contents=True)
    assert registry.gamestate.chapters.find(fog) is None
    assert registry.gamestate.npcs.find(hob.id) is None
    trash = registry.trash
    kept = next(t for t in trash.of_world(world) if t.kind == "chapter")
    assert kept.name == "Fog"
    kind, back = trash.restore(kept.id)
    chapter = registry.gamestate.chapters.get(fog)
    assert kind == "chapter" and back.id == fog
    assert chapter.adventure_id == bell and chapter.body.strip() == "The ferry waits."
    # The NPC deleted with it is back in it.
    assert registry.gamestate.npcs.get(hob.id).scope.chapter_id == fog
    assert trash.of_world(world) == []


def test_a_deleted_adventure_comes_back_into_its_world(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    registry.adventures.delete(bell)
    kept = registry.trash.of_world(world)[0]
    assert (kept.kind, kept.name) == ("adventure", "The Drowned Bell")
    registry.trash.restore(kept.id)
    assert registry.gamestate.adventures.get(bell).world_id == world
    assert registry.gamestate.chapters.get(fog).adventure_id == bell


def test_a_chapter_whose_adventure_is_gone_stays(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    registry.adventures.delete_chapter(fog)
    registry.adventures.delete(bell)
    chapter = next(t for t in registry.trash.of_world(world) if t.kind == "chapter")
    with pytest.raises(TrashError):
        registry.trash.restore(chapter.id)


async def test_check_links_restores_a_chapter(gm: AsyncClient, registry: StoreRegistry) -> None:
    world, _bell, fog = _world(registry)
    registry.adventures.delete_chapter(fog)
    page = (await gm.get("/gm/world/links")).text
    assert "Recently deleted" in page and "Chapter" in page and "Fog" in page
    kept = registry.trash.of_world(world)[0]
    back = await gm.post("/gm/world/links/restore", data={"id": kept.id})
    assert back.headers["location"] == f"/gm/chapters/{fog}"


async def test_a_delete_offers_undo(gm: AsyncClient, registry: StoreRegistry) -> None:
    world, _bell, fog = _world(registry)
    hob = registry.npcs.create("Hob", f"c:{fog}", world)
    gone = await gm.post(f"/gm/npcs/{hob.id}/delete")
    landed = gone.headers["location"]
    kept = registry.trash.entry_of("npc", hob.id)
    assert kept and f"undo={kept}" in landed
    page = (await gm.get(landed)).text
    assert "undo-notice" in page and f'name="id" value="{kept}"' in page
    back = await gm.post("/gm/world/links/restore", data={"id": kept})
    assert back.headers["location"] == f"/gm/npcs/{hob.id}"
    # A chapter too, back on its own page.
    gone = await gm.post(f"/gm/chapters/{fog}/delete", data={"contents": "keep"})
    kept = registry.trash.entry_of("chapter", fog)
    assert f"undo={kept}" in gone.headers["location"]
    back = await gm.post("/gm/world/links/restore", data={"id": kept})
    assert back.headers["location"] == f"/gm/chapters/{fog}"


def test_the_nav_forgets_a_used_undo() -> None:
    from app.ui.nav import _without_one_offs

    assert _without_one_offs("/gm/npcs?world=w1&deleted=1&undo=abc") == "/gm/npcs?world=w1"
    assert _without_one_offs("/gm/npcs?undo=abc") == "/gm/npcs"


def test_the_trash_folder_is_erased_after_its_days(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    registry.adventures.delete_chapter(fog)
    folder = registry.gamestate.prose.trash
    (kept,) = [p for p in folder.iterdir()]
    old = folder / "20200101-120000-old-world"
    (old / "media").mkdir(parents=True)
    (old / "media" / "map.png").write_bytes(b"x")
    stray = folder / "notes.txt"  # not named by a delete: never touched
    stray.write_text("mine")
    assert registry.trash.clear_old() == 1
    assert not old.exists() and kept.exists() and stray.exists()
    # Thirty days on, the chapter's file and its entry go too.
    later = datetime.now(UTC) + timedelta(days=KEEP_DAYS + 1)
    assert registry.trash.clear_old(later) == 1
    assert not kept.exists() and registry.trash.of_world(world) == []


def test_quests_and_factions_come_back_with_their_links(registry: StoreRegistry) -> None:
    world, _bell, _fog = _world(registry)
    wardens = registry.factions.create(world, "Reed Wardens")
    quest = registry.gamestate.quests.create(
        Quest(world_id=world, name="Ring the bell", faction_id=wardens.id)
    )
    kept = registry.factions.delete(wardens.id, world)
    assert kept and registry.gamestate.quests.get(quest.id).faction_id is None
    registry.trash.restore(kept)
    assert registry.gamestate.factions.get(wardens.id).name == "Reed Wardens"
    assert registry.gamestate.quests.get(quest.id).faction_id == wardens.id  # linked again
    gone = registry.quests.delete(quest.id, world)
    assert gone and registry.gamestate.quests.find(quest.id) is None
    kind, back = registry.trash.restore(gone)
    assert kind == "quest" and registry.gamestate.quests.get(quest.id).name == "Ring the bell"


async def test_deleting_a_quest_offers_undo(gm: AsyncClient, registry: StoreRegistry) -> None:
    world, _bell, _fog = _world(registry)
    quest = registry.gamestate.quests.create(Quest(world_id=world, name="Ring the bell"))
    gone = await gm.post(f"/gm/quests/{world}/delete", data={"quest": quest.id})
    kept = registry.trash.entry_of("quest", quest.id)
    assert kept and f"undo={kept}" in gone.headers["location"]
    back = await gm.post("/gm/world/links/restore", data={"id": kept})
    assert back.headers["location"] == f"/gm/quests?world={world}"
    assert registry.gamestate.quests.find(quest.id) is not None


async def test_a_deleted_world_comes_back(gm: AsyncClient, registry: StoreRegistry) -> None:
    moor = registry.worlds.create(WorldInput(name="Grey Moor"))
    registry.table.select(moor.id)
    hob = registry.npcs.create("Hob", f"w:{moor.id}", moor.id)
    gone = await gm.post(f"/gm/worlds/{moor.id}/delete")
    kept = registry.trash.worlds()[0]
    assert gone.headers["location"] == f"/gm/adventures?undo={kept.id}"
    assert (kept.kind, kept.name) == ("world", "Grey Moor")
    assert registry.gamestate.worlds.find(moor.id) is None
    # Listed under Deleted worlds, reached from the world menu.
    menu = (await gm.get("/gm/adventures")).text
    assert "/gm/world/deleted" in menu and "Deleted worlds: 1" in menu
    listed = (await gm.get("/gm/world/deleted")).text
    assert "Grey Moor" in listed and f'value="{kept.id}"' in listed
    back = await gm.post("/gm/world/links/restore", data={"id": kept.id})
    assert back.headers["location"] == f"/gm/worlds/{moor.id}"
    assert registry.gamestate.worlds.get(moor.id).name == "Grey Moor"
    assert registry.gamestate.npcs.get(hob.id).scope.world_id == moor.id  # its records too
    assert registry.table.world_id() == moor.id  # the table had no world: it plays here
    assert registry.trash.worlds() == []


def test_what_moved_up_goes_back_with_its_adventure(registry: StoreRegistry) -> None:
    world, bell, fog = _world(registry)
    hob = registry.npcs.create("Hob", f"c:{fog}", world)
    tam = registry.npcs.create("Tam", f"a:{bell}", world)
    registry.adventures.delete(bell)  # its contents move up to the world
    assert registry.gamestate.npcs.get(hob.id).scope == Scope(world_id=world)
    # Tam is moved on meanwhile: it stays where it was put.
    registry.gamestate.npcs.update(tam.id, scope=Scope(world_id=world, adventure_id="x"))
    kept = next(t for t in registry.trash.of_world(world) if t.kind == "adventure")
    registry.trash.restore(kept.id)
    assert registry.gamestate.npcs.get(hob.id).scope == Scope(
        world_id=world, adventure_id=bell, chapter_id=fog
    )
    assert registry.gamestate.npcs.get(tam.id).scope.adventure_id == "x"
