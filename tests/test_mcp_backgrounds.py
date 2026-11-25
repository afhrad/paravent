"""Backgrounds over MCP: Claude drafts in the background builder (app/mcp/backgrounds.py)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.store.registry import StoreRegistry
from tests.test_mcp import call, session
from tests.test_mcp import table as table  # noqa: F401 - the fixture
from tests.test_mcp_creatures import _refused

pytestmark = pytest.mark.mcp_server


async def test_a_background_draft_waits_for_the_gamemaster(
    table: tuple[httpx.AsyncClient, Any],  # noqa: F811
) -> None:
    _raw, facts = table
    registry: StoreRegistry = facts["app"].state.stores
    world_id = facts["world"].id
    async with session(table) as mcp:
        drafted = await call(
            mcp,
            "create_background",
            world_id=world_id,
            name="Reed Cutter",
            edition="one",
            abilities=["str", "dex", "wis"],
            feat="Nowhere Feat",
            skills=["survival", "nature"],
            equipment=[{"item": "Sickle"}, {"special": "a bundle of reeds", "quantity": 3}],
            gold=8,
        )
        assert drafted["url"] == f"/gm/backgrounds/{drafted['id']}"
        assert drafted["background"]["edition"] == "one"
        assert any("Nowhere Feat" in p for p in drafted["problems"])
        changed = await call(mcp, "update_background", design_id=drafted["id"], gold=12)
        assert changed["background"]["startingEquipment"][0]["A"][-1] == {"value": 1200}
        registry.background_builder.publish()
        assert "Reed Cutter" not in registry.background_builder.homebrew_file.read_text()
        registry.background_builder.accept(drafted["id"])
        assert "Reed Cutter" in registry.background_builder.homebrew_file.read_text()
        assert "Only drafts you created" in await _refused(
            mcp, "update_background", design_id=drafted["id"], gold=1
        )
