"""Unidentified and cursed magic items (app/services/identify.py)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Coins, Role, Scope, SourceSet, Treasure, TreasureItem, World
from app.models.pc import ChosenOption, InventoryItem, PlayerCharacter
from app.services.identify import as_seen, is_bound, is_cursed, kind_of
from app.store.registry import StoreRegistry
from tests.conftest import log_about

PASSWORD = "correct horse"
LYRA = Path(__file__).resolve().parent.parent / "documentation/examples/lyra-mooncaller.json"
SWORD = "+1 Longsword|DMG"
VENGEANCE = "Longsword of Vengeance|DMG"
RING = 2  # Lyra's Ring of Protection


def test_what_an_unidentified_item_looks_like() -> None:
    assert kind_of({"type": "RG|XDMG"}) == "ring" and kind_of({"type": "P"}) == "potion"
    assert kind_of({"wondrous": True}) == "wondrous" and kind_of(None) == "item"
    books = {
        "Ring of Protection": {"type": "RG"},
        "+1 Longsword": {"baseItem": "longsword|phb", "type": "M"},
        "longsword": {"type": "M"},
    }
    pc = PlayerCharacter.model_validate(
        {
            "name": "Wren",
            "inventory": [
                {"item": "Ring of Protection|DMG", "attuned": True, "unidentified": True},
                {"item": "+1 Longsword|DMG", "unidentified": True, "charges_used": 2},
                {"item": "Rope|PHB"},
            ],
        }
    )
    seen = as_seen(pc, lambda o: books.get(o.name))
    assert [i.item.name for i in seen.inventory] == ["?ring", "longsword", "Rope"]
    assert not seen.inventory[0].attuned and seen.inventory[1].charges_used == 0
    assert pc.inventory[0].item.name == "Ring of Protection"  # the file is untouched
    # Written to the file only while it says something.
    plain = InventoryItem(item=ChosenOption(name="Rope"))
    assert set(plain.model_dump()) == {"item", "quantity", "equipped", "attuned", "notes"}


def test_a_curse_binds_once_attuned() -> None:
    cursed = {"curse": True}
    carried = InventoryItem(item=ChosenOption(name="Longsword of Vengeance"))
    assert is_cursed(carried, cursed) and not is_bound(carried, cursed)
    carried.attuned = True
    assert is_bound(carried, cursed)
    carried.cursed = False  # the gamemaster's word over the book's
    assert not is_bound(carried, cursed)
    assert is_cursed(InventoryItem(item=ChosenOption(name="Rope"), cursed=True), None)


@pytest.fixture
async def table(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[StoreRegistry, str, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("wren", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="Grey Reed", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
    )
    registry.table.select(world.id)
    data = json.loads(LYRA.read_text(encoding="utf-8"))
    data.pop("$schema", None)
    data["player"] = "wren"
    data["world_id"] = world.id
    data["inventory"][RING]["unidentified"] = True
    data["inventory"][RING]["shown_as"] = "a ring of black iron"
    data["inventory"] += [
        {"item": SWORD, "unidentified": True, "equipped": True},
        {"item": VENGEANCE},
    ]
    lyra = registry.gamestate.characters.create(PlayerCharacter.model_validate(data))
    yield registry, world.id, lyra.id


@asynccontextmanager
async def _client(registry: StoreRegistry, user: str) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": user, "password": PASSWORD})
        yield client


async def test_the_player_sees_only_its_looks(table: tuple[StoreRegistry, str, str]) -> None:
    registry, _world, lyra = table
    sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
    ring, sword = sheet.inventory[RING], sheet.inventory[-2]
    assert ring.unidentified and ring.seen_kind == "ring" and ring.true_name == "Ring of Protection"
    assert not ring.attunable and not ring.found  # its magic waits to be identified
    assert sword.seen_kind == "Longsword" and sword.true_name == "+1 Longsword"
    attack = next(a for a in sheet.attacks if "longsword" in a.name.lower())
    assert attack.name.lower() == "longsword"  # a plain longsword, no +1 to give it away
    async with _client(registry, "wren") as player:
        page = (await player.get(f"/characters/{lyra}?view=gear")).text
        assert "a ring of black iron" in page and "Longsword (unidentified)" in page
        assert "Ring of Protection" not in page and "+1 Longsword" not in page
        assert f"/characters/{lyra}/item" not in page  # the gamemaster's panel
        # Nor may its player read the real entry by having it.
        assert (await player.get("/ref/item/DMG/Ring%20of%20Protection")).status_code == 404
        # Attuning waits for it to be identified.
        refused = await player.post(
            f"/characters/{lyra}/act/attune", data={"index": str(RING)}, follow_redirects=True
        )
        assert "not identified yet" in refused.text
    async with _client(registry, "dm") as gm:
        page = (await gm.get(f"/characters/{lyra}?view=gear")).text
        assert "a ring of black iron" in page and "Ring of Protection" in page
        assert "Unidentified" in page
        done = await gm.post(
            f"/characters/{lyra}/item",
            data={"op": "identify", "index": str(RING), "ref": "Ring of Protection"},
        )
        assert done.status_code == 303
    pc = registry.gamestate.characters.get(lyra)
    assert not pc.inventory[RING].unidentified and not pc.inventory[RING].shown_as
    assert any(e.what == "identify:Ring of Protection|DMG" for e in log_about(registry, lyra))


async def test_a_cursed_item_holds_on_until_lifted(table: tuple[StoreRegistry, str, str]) -> None:
    registry, _world, lyra = table
    vengeance = len(registry.gamestate.characters.get(lyra).inventory) - 1
    async with _client(registry, "wren") as player:
        url = f"/characters/{lyra}/act"
        await player.post(f"{url}/equip", data={"index": str(vengeance)})
        await player.post(f"{url}/attune", data={"index": str(vengeance)})
        pc = registry.gamestate.characters.get(lyra)
        assert pc.inventory[vengeance].attuned
        page = (await player.get(f"/characters/{lyra}?view=gear")).text
        assert "Cursed: bound" in page
        for action in ("attune", "equip"):
            refused = await player.post(
                f"{url}/{action}", data={"index": str(vengeance)}, follow_redirects=True
            )
            assert "curse holds on" in refused.text
        pc = registry.gamestate.characters.get(lyra)
        assert pc.inventory[vengeance].attuned and pc.inventory[vengeance].equipped
        # The player cannot lift it.
        lifted = await player.post(
            f"/characters/{lyra}/item", data={"op": "lift_curse", "index": str(vengeance)}
        )
        assert lifted.status_code == 403
    async with _client(registry, "dm") as gm:
        await gm.post(
            f"/characters/{lyra}/item",
            data={"op": "lift_curse", "index": str(vengeance), "ref": "Longsword of Vengeance"},
        )
    pc = registry.gamestate.characters.get(lyra)
    assert not pc.inventory[vengeance].attuned and pc.inventory[vengeance].equipped


async def test_the_gamemaster_marks_an_item(table: tuple[StoreRegistry, str, str]) -> None:
    registry, _world, lyra = table
    dagger = 1
    async with _client(registry, "dm") as gm:
        url = f"/characters/{lyra}/item"
        await gm.post(
            url,
            data={
                "op": "item_secret",
                "index": str(dagger),
                "ref": "Dagger",
                "unidentified": "1",
                "shown_as": "  a   bone knife ",
                "cursed": "yes",
            },
        )
        pc = registry.gamestate.characters.get(lyra)
        carried = pc.inventory[dagger]
        assert (carried.unidentified, carried.shown_as, carried.cursed) == (
            True, "a bone knife", True,
        )  # fmt: skip
        # A page drawn for another item acts on nothing.
        await gm.post(url, data={"op": "identify", "index": str(dagger), "ref": "Quarterstaff"})
        assert registry.gamestate.characters.get(lyra).inventory[dagger].unidentified
        await gm.post(
            url, data={"op": "item_secret", "index": str(dagger), "ref": "Dagger", "cursed": "book"}
        )
    carried = registry.gamestate.characters.get(lyra).inventory[dagger]
    assert not carried.unidentified and carried.cursed is None


async def test_treasure_handed_out_unidentified(table: tuple[StoreRegistry, str, str]) -> None:
    registry, world, lyra = table
    hoard = registry.gamestate.treasures.create(
        Treasure(
            scope=Scope(world_id=world),
            name="Bog hoard",
            items=[
                TreasureItem(name="Ring of Free Action", source="DMG"),
                TreasureItem(name="Rope, Hempen (50 feet)", source="PHB"),
            ],
            coins=Coins(gp=10),
        )
    )
    async with _client(registry, "dm") as gm:
        page = (await gm.get(f"/gm/treasure/{hoard.id}")).text
        # Magic goes out unidentified unless unticked; mundane gear as it is.
        assert 'name="unid.0" value="1" checked' in page
        assert 'name="unid.1" value="1">' in page
        shared = await gm.post(
            f"/gm/treasure/{hoard.id}/share",
            data={"item.0": lyra, "item.1": lyra, "unid.0": "1", "coins_to": lyra},
        )
        assert shared.status_code == 200, shared.text[:300]
    pc = registry.gamestate.characters.get(lyra)
    ring = next(i for i in pc.inventory if i.item.name == "Ring of Free Action")
    rope = next(i for i in pc.inventory if i.item.name.startswith("Rope"))
    assert ring.unidentified and not rope.unidentified and pc.currency.gp >= 10
    received = next(e for e in log_about(registry, lyra) if e.what.startswith("receive:"))
    assert "Ring of Free Action" not in received.what and "?*1" in received.what
