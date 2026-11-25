"""Items over MCP: Claude drafts in the item builder (app/mcp/items.py)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.store.registry import StoreRegistry
from tests.test_mcp import PASSWORD, call, session
from tests.test_mcp import table as table  # noqa: F401 - the fixture
from tests.test_mcp_creatures import _refused

pytestmark = pytest.mark.mcp_server


class TestDraftingAnItem:
    async def test_a_draft_waits_for_the_gamemaster(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        _raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        world_id = facts["world"].id
        async with session(table) as mcp:
            drafted = await call(
                mcp,
                "create_item",
                world_id=world_id,
                name="Bell of the Drowned",
                rarity="rare",
                attunement=True,
                bonus_saving_throw=1,
                resist=["cold"],
                charges=3,
                recharge="dawn",
                spells=[{"spell": "Water Breathing", "charges": 1}, {"spell": "Nothing Spell"}],
                text="A bronze bell green with age.",
            )
            assert drafted["url"] == f"/gm/items/{drafted['id']}"
            assert drafted["item"]["bonusSavingThrow"] == "+1" and drafted["item"]["wondrous"]
            assert any("Nothing Spell" in p for p in drafted["problems"])
            assert "needs base_item" in await _refused(
                mcp, "create_item", world_id=world_id, name="Blade", kind="weapon"
            )
            blade = await call(
                mcp,
                "create_item",
                world_id=world_id,
                name="Tide Blade",
                kind="weapon",
                base_item="Longsword",
                bonus_weapon=1,
            )
            assert blade["item"]["dmg1"] == "1d8" and blade["item"]["bonusWeapon"] == "+1"
            changed = await call(mcp, "update_item", design_id=blade["id"], bonus_weapon=2)
            assert changed["item"]["bonusWeapon"] == "+2"
            design = registry.item_builder.get(drafted["id"])
            assert design.drafted_by and design.world_id == world_id
            # Not in the books until accepted; then the gamemaster's, not Claude's.
            registry.item_builder.publish()
            assert "Bell of the Drowned" not in registry.item_builder.homebrew_file.read_text()
            accepted = registry.item_builder.accept(drafted["id"])
            assert accepted.drafted_by is None
            assert "Bell of the Drowned" in registry.item_builder.homebrew_file.read_text()
            assert "Only drafts you created" in await _refused(
                mcp, "update_item", design_id=drafted["id"], bonus_ac=1
            )


async def test_a_revision_of_an_accepted_item(
    table: tuple[httpx.AsyncClient, Any],  # noqa: F811
) -> None:
    raw, facts = table
    registry: StoreRegistry = facts["app"].state.stores
    world_id = facts["world"].id
    builder = registry.item_builder
    ring = builder.create("Reed Ring", world_id, "ring")
    ring.bonus_saving_throw = 1
    builder.save(ring)
    builder.publish()
    async with session(table) as mcp:
        revision = await call(mcp, "start_item_revision", item="Reed Ring")
        assert revision["id"] != ring.id and revision["name"] == "Reed Ring"
        again = await call(mcp, "start_item_revision", item=ring.id)
        assert again["id"] == revision["id"]  # the one already open
        changed = await call(mcp, "update_item", design_id=revision["id"], bonus_ac=1)
        assert changed["item"]["bonusAc"] == "+1"
        assert "keeps its item's name" in await _refused(
            mcp, "update_item", design_id=revision["id"], name="Other Ring"
        )
    # The item itself is untouched until the gamemaster accepts the revision.
    assert builder.get(ring.id).bonus_ac == 0
    assert builder.changes(builder.get(revision["id"])) == ["bonus_ac"]
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})
    page = (await raw.get(f"/gm/items/{revision['id']}")).text
    assert "Changes: Armor Class." in page
    done = await raw.post(
        f"/gm/items/{revision['id']}",
        data={
            "action": "accept",
            "name": "Reed Ring",
            "kind": "ring",
            "rarity": "uncommon",
            "bonus_saving_throw": "1",
            "bonus_ac": "1",
            "world": world_id,
        },
    )
    assert "revised=1" in done.headers["location"]
    assert builder.get(ring.id).bonus_ac == 1 and builder.revision(ring.id) is None
