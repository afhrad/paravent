"""One piece of a world copied or moved into another (app/services/world_copy.py)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import EncounterMonster, Faction, Quest, Role, SourceSet
from app.services.world_copy import WorldCopyError
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry
from tests.test_world_transfer import PASSWORD, _png, _png_as_webp


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    return registry


def _worlds(registry: StoreRegistry) -> dict[str, str]:
    """Two worlds: Grey Reed with an adventure (a chapter linking an NPC of its own and a
    world-level one, a place by tag, a map), a token and a goblin fight; Ashfall, which has
    a "Landing" of its own and only the 2024 books."""
    store = registry.gamestate
    reed = registry.worlds.create(WorldInput(name="Grey Reed", sources=("PHB", "MM")))
    ash = registry.worlds.create(WorldInput(name="Ashfall", sources=("XPHB", "XMM")))
    bell = registry.adventures.create(reed.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    hob = registry.npcs.create("Hob", f"c:{fog.id}", reed.id)
    mara = registry.npcs.create("Mara", f"w:{reed.id}", reed.id)
    registry.locations.create("Landing", f"w:{reed.id}", reed.id)
    registry.locations.create("Landing", f"w:{ash.id}", ash.id)
    registry.adventures.upload(bell.id, "map.png", _png(), as_cover=False)
    body = f"Meet [Hob](/gm/npcs/{hob.id}) and [Mara](/gm/npcs/{mara.id}) at {{@place Landing}}."
    store.chapters.update(fog.id, body=body)
    fight = registry.encounters.create("Ambush", reed.id)
    store.encounters.update(
        fight.id,
        monsters=[EncounterMonster(name="Goblin", source="MM", count=3)],
        adventure_id=bell.id,
    )
    tokens = registry.tokens.folder(reed.id, "npc")
    assert tokens is not None
    tokens.mkdir(parents=True, exist_ok=True)
    (tokens / f"{mara.id}.webp").write_bytes(_png_as_webp())
    return {
        "reed": reed.id,
        "ash": ash.id,
        "bell": bell.id,
        "fog": fog.id,
        "hob": hob.id,
        "mara": mara.id,
        "fight": fight.id,
    }


def test_the_plan_names_what_goes_and_what_is_outside(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    plan = registry.world_copy.plan(registry.world_copy.bundle("adventure", [t["bell"]]), t["ash"])
    kinds = sorted((p.kind, p.name) for p in plan.pieces)
    assert kinds == [
        ("adventure", "The Drowned Bell"),
        ("chapter", "Fog"),
        ("encounter", "Ambush"),
        ("npc", "Hob"),
    ]
    assert [p.name for p in plan.brought] == ["Mara"]
    assert [p.name for p in plan.reused] == ["Landing"]  # Ashfall has its own
    assert plan.outside == ["Goblin|MM"]
    assert plan.renamed == {}
    with pytest.raises(WorldCopyError):
        registry.world_copy.plan(registry.world_copy.bundle("adventure", [t["bell"]]), t["reed"])


def test_an_adventure_copies_with_new_ids_its_links_and_its_pictures(
    registry: StoreRegistry,
) -> None:
    t = _worlds(registry)
    store = registry.gamestate
    done = registry.world_copy.copy("adventure", [t["bell"]], t["ash"])
    (bell,) = store.adventures.list(lambda a: a.world_id == t["ash"])
    assert bell.id == done.ids[t["bell"]] != t["bell"] and bell.name == "The Drowned Bell"
    (fog,) = store.chapters.list(lambda c: c.adventure_id == bell.id)
    hob = next(n for n in store.npcs.list() if n.scope.chapter_id == fog.id)
    mara = next(n for n in store.npcs.list() if n.scope.world_id == t["ash"] and n.name == "Mara")
    assert f"/gm/npcs/{hob.id}" in fog.body and f"/gm/npcs/{mara.id}" in fog.body
    assert mara.scope.adventure_id is None
    fight = next(e for e in store.encounters.list() if e.world_id == t["ash"])
    assert fight.adventure_id == bell.id and fight.monsters[0].name == "Goblin"
    folder = store.prose.folder(bell.id)
    assert folder is not None and (folder / "media/map.png").is_file()
    world = store.prose.world_folder(t["ash"])
    assert world is not None and (world / f"media/tokens/npc/{mara.id}.webp").is_file()
    # Ashfall's own Landing stays the only one.
    assert len([p for p in store.locations.list() if p.scope.world_id == t["ash"]]) == 1
    # The original is untouched.
    assert store.adventures.get(t["bell"]).world_id == t["reed"]
    # A second copy takes a number.
    again = registry.world_copy.copy("adventure", [t["bell"]], t["ash"])
    assert store.adventures.get(again.ids[t["bell"]]).name == "The Drowned Bell (2)"


def test_without_bringing_named_objects_stay_behind(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    registry.world_copy.copy("adventure", [t["bell"]], t["ash"], bring=False)
    names = [n.name for n in registry.gamestate.npcs.list() if n.scope.world_id == t["ash"]]
    assert names == ["Hob"]


def test_moving_an_adventure_puts_the_original_in_the_trash(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    store = registry.gamestate
    done = registry.world_copy.move("adventure", [t["bell"]], t["ash"])
    assert done.moved and store.adventures.find(t["bell"]) is None
    assert store.npcs.find(t["hob"]) is None  # went with it, restorable
    assert store.npcs.find(t["mara"]) is not None  # came along by name, stays here too
    trashed = [x.kind for x in registry.trash.of_world(t["reed"])]
    assert "adventure" in trashed


def test_an_npc_inside_an_adventure_lands_at_world_level(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    done = registry.world_copy.copy("npc", [t["hob"]], t["ash"])
    hob = registry.gamestate.npcs.get(done.ids[t["hob"]])
    assert hob.scope.world_id == t["ash"] and hob.scope.adventure_id is None


def test_a_name_the_target_has_takes_a_number(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    landing = next(p for p in registry.gamestate.locations.list() if p.scope.world_id == t["reed"])
    done = registry.world_copy.copy("place", [landing.id], t["ash"])
    assert registry.gamestate.locations.get(done.ids[landing.id]).name == "Landing (2)"


def test_a_quest_moves_in_place_and_brings_its_faction(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    store = registry.gamestate
    guild = store.factions.create(
        Faction(world_id=t["reed"], name="Ferry Guild", renown={"pc000000001": 3})
    )
    quest = store.quests.create(
        Quest(world_id=t["reed"], name="Raise the bell", faction_id=guild.id, status="done")
    )
    done = registry.world_copy.move("quest", [quest.id], t["ash"])
    moved = store.quests.get(quest.id)
    assert done.ids[quest.id] == quest.id and moved.world_id == t["ash"] and moved.status == "open"
    copied = store.factions.get(moved.faction_id or "")
    assert copied.world_id == t["ash"] and copied.name == "Ferry Guild" and copied.renown == {}
    assert store.factions.get(guild.id).world_id == t["reed"]


def test_an_unrestricted_target_has_nothing_outside(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    registry.gamestate.worlds.update(t["ash"], source_set=SourceSet())
    assert (
        registry.world_copy.plan(
            registry.world_copy.bundle("encounter", [t["fight"]]), t["ash"]
        ).outside
        == []
    )


def test_targets_are_the_other_worlds(registry: StoreRegistry, tmp_path: Path) -> None:
    t = _worlds(registry)
    assert [w.name for w in registry.world_copy.targets("npc", [t["mara"]])] == ["Ashfall"]


async def test_the_page_plans_and_copies(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    registry.table.select(t["reed"])
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as gm:
        await gm.post("/login", data={"username": "dm", "password": PASSWORD})
        menu = (await gm.get(f"/menu/link?href=/gm/adventures/{t['bell']}")).text
        assert "/gm/copy?kind=adventure" in menu and "mode=move" in menu
        page = (await gm.get(f"/gm/copy?kind=adventure&id={t['bell']}&mode=copy")).text
        assert "Ashfall" in page and "Mara" in page and "Goblin · MM" in page
        unticked = await gm.get(
            f"/gm/copy?kind=adventure&id={t['bell']}&target={t['ash']}&checked=1"
        )
        assert "Named in its texts, left behind" in unticked.text
        done = await gm.post(
            "/gm/copy",
            data={
                "kind": "adventure",
                "id": t["bell"],
                "mode": "copy",
                "target": t["ash"],
                "bring": "1",
            },
        )
        assert "copied to Ashfall" in done.text
        copied = registry.gamestate.adventures.list(lambda a: a.world_id == t["ash"])
        assert len(copied) == 1 and f"/gm/adventures/{copied[0].id}" in done.text
        assert (await gm.get("/gm/copy?kind=adventure&id=nothing")).status_code == 404


def test_a_copy_starts_planned_and_a_move_keeps_its_state(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    store = registry.gamestate
    store.chapters.update(t["fog"], status="running")
    copied = registry.world_copy.copy("adventure", [t["bell"]], t["ash"])
    (fog,) = store.chapters.list(lambda c: c.adventure_id == copied.ids[t["bell"]])
    assert fog.status == "planned"
    moved = registry.world_copy.move("adventure", [t["bell"]], t["ash"])
    (kept,) = store.chapters.list(lambda c: c.adventure_id == moved.ids[t["bell"]])
    assert kept.status == "running"


def test_several_of_a_list_at_once(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    store = registry.gamestate
    landing = next(p for p in store.locations.list() if p.scope.world_id == t["reed"])
    tower = registry.locations.create("Tower", f"w:{t['reed']}", t["reed"])
    done = registry.world_copy.copy("place", [landing.id, tower.id], t["ash"])
    names = sorted(store.locations.get(i).name for i in done.ids.values())
    assert names == ["Landing (2)", "Tower"]
    with pytest.raises(WorldCopyError):  # pieces of two worlds at once
        mine = next(p for p in store.locations.list() if p.scope.world_id == t["ash"])
        registry.world_copy.bundle("place", [tower.id, mine.id])


def test_a_file_carries_an_adventure_to_another_table(
    registry: StoreRegistry, tmp_path: Path
) -> None:
    t = _worlds(registry)
    data = registry.world_copy.saved(registry.world_copy.bundle("adventure", [t["bell"]]))
    other = StoreRegistry(Settings(library_dir=tmp_path / "other", watch_files=False))
    other.load()
    there = other.worlds.create(WorldInput(name="Elsewhere"))
    bundle = other.world_copy.read(data)
    assert bundle.source == "Grey Reed" and [e.name for e in bundle.mains] == ["The Drowned Bell"]
    done = other.world_copy.write(bundle, there.id)
    store = other.gamestate
    (bell,) = store.adventures.list(lambda a: a.world_id == there.id)
    assert bell.id == done.ids[t["bell"]]
    (fog,) = store.chapters.list(lambda c: c.adventure_id == bell.id)
    hob = next(n for n in store.npcs.list() if n.scope.chapter_id == fog.id)
    mara = next(n for n in store.npcs.list() if n.name == "Mara")
    assert f"/gm/npcs/{hob.id}" in fog.body and f"/gm/npcs/{mara.id}" in fog.body
    folder = store.prose.folder(bell.id)
    assert folder is not None and (folder / "media/map.png").is_file()
    world = store.prose.world_folder(there.id)
    assert world is not None and (world / f"media/tokens/npc/{mara.id}.webp").is_file()
    # Without what it names, Mara stays out.
    lean = registry.world_copy.saved(
        registry.world_copy.bundle("adventure", [t["bell"]]), bring=False
    )
    assert all(e.role != "named" for e in other.world_copy.read(lean).entries)


def test_a_file_that_is_not_one(registry: StoreRegistry) -> None:
    with pytest.raises(WorldCopyError):
        registry.world_copy.read(b"not a zip")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as pack:
        pack.writestr("paravent-pieces.json", json.dumps({"paravent": "world"}))
    with pytest.raises(WorldCopyError):
        registry.world_copy.read(out.getvalue())


async def test_select_save_and_import_through_the_pages(registry: StoreRegistry) -> None:
    t = _worlds(registry)
    registry.table.select(t["reed"])
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as gm:
        await gm.post("/login", data={"username": "dm", "password": PASSWORD})
        listing = (await gm.get("/gm/npcs")).text
        assert 'form="pick-npc"' in listing and 'id="pick-npc"' in listing
        nothing = await gm.get("/gm/copy?kind=npc&mode=copy")
        assert "Tick at least one" in nothing.text
        both = f"/gm/copy?kind=npc&id={t['hob']}&id={t['mara']}&mode=file"
        page = (await gm.get(both)).text
        assert "Save as a file" in page and "Hob" in page and "Mara" in page
        saved = await gm.get(f"/gm/copy/file?kind=npc&id={t['hob']}&id={t['mara']}&bring=1")
        assert saved.headers["content-type"] == "application/zip"
        assert ".paravent-pieces.zip" in saved.headers["content-disposition"]
        registry.table.select(t["ash"])
        assert "Import into Ashfall" in (await gm.get("/gm/copy/import")).text
        files = {"file": ("npcs.paravent-pieces.zip", saved.content, "application/zip")}
        done = await gm.post("/gm/copy/import", data={"bring": "1"}, files=files)
        assert "imported into Ashfall" in done.text
        names = sorted(
            n.name for n in registry.gamestate.npcs.list() if n.scope.world_id == t["ash"]
        )
        assert names == ["Hob", "Mara"]
        bad = await gm.post(
            "/gm/copy/import", files={"file": ("x.zip", b"nope", "application/zip")}
        )
        assert "not a file saved" in bad.text
