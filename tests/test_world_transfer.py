"""A world exported, imported and duplicated with new ids (app/services/world_transfer.py)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Faction, Quest, QuestStep, Role
from app.services.world_transfer import WorldTransferError
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 40), (90, 30, 30)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    return registry


def _campaign(registry: StoreRegistry) -> dict[str, str]:
    """A world with an adventure, a chapter naming an NPC by link, a place, a quest for a
    faction, renown, a picture and a token, and a line in its log."""
    store = registry.gamestate
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    hob = registry.npcs.create("Hob", f"c:{fog.id}", world.id)
    registry.locations.create("Landing", f"w:{world.id}", world.id)
    registry.adventures.upload(world.id, "map.png", _png(), as_cover=False)
    store.chapters.update(fog.id, body=f"Meet [Hob](/gm/npcs/{hob.id}).\n\n![[map.png]]\n")
    guild = store.factions.create(
        Faction(world_id=world.id, name="Ferry Guild", renown={"pc000000001": 4})
    )
    store.quests.create(
        Quest(
            world_id=world.id,
            name="Raise the bell",
            faction_id=guild.id,
            steps=[QuestStep(text="Find the diver", done=True)],
            characters=["pc000000001"],
            status="done",
        )
    )
    tokens = registry.tokens.folder(world.id, "npc")
    assert tokens is not None
    tokens.mkdir(parents=True, exist_ok=True)
    (tokens / f"{hob.id}.webp").write_bytes(_png_as_webp())
    registry.protocol.free_roll(
        "1d20", username="dm", name="GM", label="Luck", world_id=world.id, gamemaster=True
    )
    return {"world": world.id, "chapter": fog.id, "npc": hob.id, "faction": guild.id}


def _png_as_webp() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (20, 20), (10, 10, 10)).save(out, "WEBP")
    return out.getvalue()


def test_a_duplicate_is_the_campaign_with_new_ids(registry: StoreRegistry) -> None:
    t = _campaign(registry)
    store = registry.gamestate
    made = registry.world_transfer.duplicate(t["world"], name="Grey Reed, second table")
    world = made.world
    assert world.id != t["world"] and world.name == "Grey Reed, second table"
    # Its adventure and chapter, new ids, the chapter's link following its NPC.
    (bell,) = store.adventures.list(lambda a: a.world_id == world.id)
    (fog,) = store.chapters.list(lambda c: c.world_id == world.id)
    assert fog.id != t["chapter"] and fog.adventure_id == bell.id
    (hob,) = [n for n in store.npcs.list() if n.scope.chapter_id == fog.id]
    assert hob.id != t["npc"] and f"/gm/npcs/{hob.id}" in fog.body
    assert [p.name for p in store.locations.list() if p.scope.world_id == world.id] == ["Landing"]
    folder = store.prose.world_folder(world.id)
    assert folder is not None and (folder / "media/map.png").is_file()
    assert (folder / f"media/tokens/npc/{hob.id}.webp").is_file()
    # Campaign only: the quest open again with nobody on it, no renown, no log.
    (guild,) = store.factions.list(lambda f: f.world_id == world.id)
    assert guild.id != t["faction"] and guild.renown == {}
    (quest,) = store.quests.list(lambda q: q.world_id == world.id)
    assert quest.faction_id == guild.id and quest.status == "open" and quest.characters == []
    assert not quest.steps[0].done
    assert registry.protocol.store.entries(world.id) == []
    # The original is untouched.
    assert store.factions.get(t["faction"]).renown == {"pc000000001": 4}
    assert len(store.npcs.list(lambda n: n.id == t["npc"])) == 1


def test_an_export_comes_back_with_its_play(registry: StoreRegistry, tmp_path: Path) -> None:
    t = _campaign(registry)
    data = registry.world_transfer.export(t["world"])
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert {"paravent-world.json", "records.json", "protocol.jsonl", "world/world.md"} <= set(names)
    # Into another table altogether.
    other = StoreRegistry(Settings(library_dir=tmp_path / "other", watch_files=False))
    other.load()
    made = other.world_transfer.import_world(data, play=True)
    world = made.world
    assert world.name == "Grey Reed" and made.records == 4  # NPC, place, faction, quest
    (guild,) = other.gamestate.factions.list()
    assert guild.renown == {"pc000000001": 4}  # the play so far came along
    (quest,) = other.gamestate.quests.list()
    assert quest.status == "done" and quest.faction_id == guild.id
    (entry,) = other.protocol.store.entries(world.id)
    assert entry.scope.world == world.id
    assert other.table.world_id() == world.id  # its only world


def test_what_is_not_a_world_package_is_refused(registry: StoreRegistry) -> None:
    transfer = registry.world_transfer
    with pytest.raises(WorldTransferError):
        transfer.import_world(b"not a zip")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as pack:
        pack.writestr("paravent-world.json", json.dumps({"paravent": "world", "format": 1}))
        pack.writestr("../escape.md", "x")
    with pytest.raises(WorldTransferError):
        transfer.import_world(out.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as pack:
        pack.writestr("paravent-world.json", json.dumps({"paravent": "something else"}))
    with pytest.raises(WorldTransferError):
        transfer.import_world(out.getvalue())
    assert registry.gamestate.worlds.list() == []


async def test_the_world_menu_exports_imports_and_duplicates(registry: StoreRegistry) -> None:
    t = _campaign(registry)
    registry.table.select(t["world"])
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as gm:
        await gm.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await gm.get("/gm/adventures")).text
        assert 'href="/gm/world/export"' in page and 'id="import-world"' in page
        assert 'value="Grey Reed (copy)"' in page
        got = await gm.get("/gm/world/export")
        assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
        assert "grey-reed.paravent.zip" in got.headers["content-disposition"]
        # Imported under a name, the table plays in it.
        sent = await gm.post(
            "/gm/world/import",
            data={"name": "Grey Reed restored", "play": "1", "next": "/gm/adventures"},
            files={"file": ("grey-reed.paravent.zip", got.content, "application/zip")},
        )
        assert sent.status_code == 303 and sent.headers["location"] == "/gm/adventures"
        restored = registry.table.world()
        assert restored is not None and restored.name == "Grey Reed restored"
        # Duplicated: a copy, played in at once.
        await gm.post("/gm/world/duplicate", data={"name": "Second table", "next": "/gm"})
        copy = registry.table.world()
        assert copy is not None and copy.name == "Second table"
        assert len(registry.gamestate.worlds.list()) == 3
        # A file that is no world comes back to the dialog, open, saying so.
        bad = await gm.post(
            "/gm/world/import",
            data={"next": "/gm/npcs"},
            files={"file": ("notes.zip", b"nope", "application/zip")},
        )
        assert bad.headers["location"] == "/gm/npcs?transfer_error=transfer.error.not_zip"
        again = (await gm.get(bad.headers["location"])).text
        assert "That file is not a zip file." in again
        opened = 'aria-labelledby="import-world-h" data-open-now="1"'
        assert f'id="import-world" class="place-dialog" {opened}' in again
