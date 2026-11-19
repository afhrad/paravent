"""An adventure's NPCs, places, encounters and treasure: its own, and the world's
ready-made ones it uses (app/services/holdings.py)."""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services import media
from app.services.holdings import HoldingsError
from app.services.worlds import WorldInput
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 40), (40, 80, 120)).save(out, "PNG")
    return out.getvalue()


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


def test_ready_made_used_and_taken_out_without_deleting(registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    other = registry.adventures.create(world.id, "The Salt Road")
    innkeeper = registry.npcs.create("Maren", f"w:{world.id}", world.id)
    holdings = registry.holdings
    assert holdings.ready(bell.id, "npc") == [(innkeeper.id, "Maren")]
    holdings.use(bell.id, "npc", innkeeper.id)
    holdings.use(other.id, "npc", innkeeper.id)  # shared by two adventures
    assert [h.name for h in holdings.used(bell.id, "npc")] == ["Maren"]
    assert holdings.ready(bell.id, "npc") == []
    # Kept in the adventure's file, read back on a fresh start.
    file = registry.gamestate.prose.path(bell.id)
    assert file is not None and innkeeper.id in file.read_text(encoding="utf-8")
    # Taken out: unlinked, the NPC still the world's, the other adventure still uses it.
    holdings.drop(bell.id, "npc", innkeeper.id)
    assert holdings.used(bell.id, "npc") == []
    assert registry.gamestate.npcs.get(innkeeper.id).scope.adventure_id is None
    assert [h.name for h in holdings.used(other.id, "npc")] == ["Maren"]
    # Only the world's own can be used.
    stranger = registry.npcs.create("Odo", f"a:{other.id}", world.id)
    with pytest.raises(HoldingsError):
        holdings.use(bell.id, "npc", stranger.id)


def test_an_own_object_moves_up_to_the_world(registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    ferryman = registry.npcs.create("Hob", f"a:{bell.id}", world.id)
    portrait = registry.adventures.upload(bell.id, "hob.png", _png(), as_cover=False)
    registry.gamestate.npcs.update(ferryman.id, portrait=portrait)
    registry.holdings.drop(bell.id, "npc", ferryman.id)
    moved = registry.gamestate.npcs.get(ferryman.id)
    assert moved.scope.adventure_id is None and moved.scope.world_id == world.id
    # Its portrait is written from the world's folder now, and still found.
    assert moved.portrait != portrait
    assert media.resolve(registry.gamestate.prose, world.id, moved.portrait or "") is not None
    # Now ready-made, for any adventure.
    assert (ferryman.id, "Hob") in registry.holdings.ready(bell.id, "npc")
    with pytest.raises(HoldingsError):  # no longer in the adventure
        registry.holdings.drop(registry.adventures.create(world.id, "X").id, "npc", ferryman.id)


async def test_from_the_adventures_panel(gm: AsyncClient, registry: StoreRegistry) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    landing = registry.locations.create("Grey Reed Landing", f"w:{world.id}", world.id)
    page = (await gm.get(f"/gm/adventures/{bell.id}")).text
    assert 'name="object"' in page and f'value="{landing.id}"' in page  # offered
    added = await gm.post(
        f"/gm/adventures/{bell.id}/holdings",
        data={"kind": "place", "object": landing.id, "op": "use"},
    )
    assert added.status_code == 303 and added.headers["location"].endswith("#holdings")
    page = (await gm.get(f"/gm/adventures/{bell.id}")).text
    assert "Grey Reed Landing" in page and "from the world" in page
    assert f"/gm/places/{landing.id}/delete" not in page  # taken out, never deleted here
    await gm.post(
        f"/gm/adventures/{bell.id}/holdings",
        data={"kind": "place", "object": landing.id, "op": "drop"},
    )
    assert registry.gamestate.locations.find(landing.id) is not None
    assert registry.holdings.used(bell.id, "place") == []
    refused = await gm.post(
        f"/gm/adventures/{bell.id}/holdings",
        data={"kind": "place", "object": "nope", "op": "use"},
    )
    shown = (await gm.get(refused.headers["location"])).text
    assert "Only the world" in shown


async def test_out_of_a_chapter_up_to_its_adventure(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    bell = registry.adventures.create(world.id, "The Drowned Bell")
    fog = registry.adventures.create_chapter(bell.id, "Fog")
    hob = registry.npcs.create("Hob", f"c:{fog.id}", world.id)
    page = (await gm.get(f"/gm/chapters/{fog.id}")).text
    assert f"/gm/npcs/{hob.id}/delete" not in page and f"/gm/chapters/{fog.id}/holdings" in page
    moved = await gm.post(f"/gm/chapters/{fog.id}/holdings", data={"kind": "npc", "object": hob.id})
    assert moved.headers["location"].endswith("#holdings")
    where = registry.gamestate.npcs.get(hob.id).scope
    assert where.adventure_id == bell.id and where.chapter_id is None
    refused = await gm.post(
        f"/gm/chapters/{fog.id}/holdings", data={"kind": "npc", "object": hob.id}
    )
    assert "not here any more" in (await gm.get(refused.headers["location"])).text


async def test_npcs_and_treasure_are_made_in_a_dialog_as_places_are(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    for kind, dialog_id in (
        ("npcs", "new-npc"),
        ("treasure", "new-treasure"),
        ("places", "new-place"),
    ):
        url = f"/gm/worlds/{world.id}/{kind}"
        page = (await gm.get(url)).text
        assert f'data-dialog="{dialog_id}"' in page and f'id="{dialog_id}"' in page
        assert (
            f'<dialog id="{dialog_id}" class="place-dialog" aria-labelledby="{dialog_id}-h">'
            in page
        )
        assert (
            f'id="{dialog_id}" class="place-dialog" aria-labelledby="{dialog_id}-h" open'
            in (await gm.get(f"{url}?new=1")).text
        )
    # A first note is kept; a refused one reopens the dialog with its reason.
    made = await gm.post(
        f"/gm/worlds/{world.id}/npcs", data={"name": "Maren", "notes": "Keeps the inn."}
    )
    assert made.status_code == 303
    maren = next(n for n in registry.gamestate.npcs.list() if n.name == "Maren")
    assert maren.notes == "Keeps the inn."
    made = await gm.post(
        f"/gm/worlds/{world.id}/treasure", data={"name": "Toll chest", "notes": "Under the bridge."}
    )
    assert made.status_code == 303
    chest = next(x for x in registry.gamestate.treasures.list() if x.name == "Toll chest")
    assert chest.notes == "Under the bridge."
    refused = await gm.post(f"/gm/worlds/{world.id}/treasure", data={"name": ""})
    assert refused.status_code == 422 and 'aria-labelledby="new-treasure-h" open' in refused.text


async def test_a_new_npc_is_the_worlds_named_for_its_people(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    page = (await gm.get("/gm/npcs")).text
    dialog = page.split('id="new-npc"', 1)[1].split("</dialog>", 1)[0]
    # No place to choose: the table's world. The people first, then the name and its dice.
    assert (
        'name="place" value="w:' + world.id + '"' in dialog
        and "<select" not in dialog.split('class="name-roll"', 1)[0]
    )
    assert dialog.index('name="race"') < dialog.index('name="name"')
    assert 'hx-post="/gm/npcs/new/name"' in dialog
    rolled = await gm.post(
        "/gm/npcs/new/name", data={"race": "Dwarf", "name": ""}, headers={"HX-Request": "true"}
    )
    assert rolled.status_code == 200 and rolled.text.startswith('<span class="name-field">')
    value = rolled.text.split('value="', 1)[1].split('"', 1)[0]
    assert value  # a name was rolled
    made = await gm.post(
        f"/gm/worlds/{world.id}/npcs",
        data={"name": value, "race": "Dwarf", "place": f"w:{world.id}"},
    )
    assert made.status_code == 303
    npc = next(n for n in registry.gamestate.npcs.list() if n.name == value)
    assert npc.race == "Dwarf" and npc.scope.world_id == world.id and npc.scope.adventure_id is None


async def test_new_treasure_and_places_go_to_the_tables_world(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    world = registry.worlds.create(WorldInput(name="Grey Reed"))
    for address, dialog_id in (("/gm/treasure", "new-treasure"), ("/gm/places", "new-place")):
        page = (await gm.get(address)).text
        dialog = page.split(f'id="{dialog_id}"', 1)[1].split("</dialog>", 1)[0]
        assert '<select id="f-place"' not in dialog, address  # no "belongs to"
        assert f'name="place" value="w:{world.id}"' in dialog, address
    await gm.post(
        f"/gm/worlds/{world.id}/treasure", data={"name": "Toll chest", "place": f"w:{world.id}"}
    )
    await gm.post(
        f"/gm/worlds/{world.id}/places", data={"name": "Landing", "place": f"w:{world.id}"}
    )
    chest = next(x for x in registry.gamestate.treasures.list() if x.name == "Toll chest")
    landing = next(x for x in registry.gamestate.locations.list() if x.name == "Landing")
    assert chest.scope.adventure_id is None and landing.scope.adventure_id is None
