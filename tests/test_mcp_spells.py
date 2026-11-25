"""Spells over MCP: Claude drafts in the spell builder (app/mcp/spells.py)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.store.registry import StoreRegistry
from tests.test_mcp import call, session
from tests.test_mcp import table as table  # noqa: F401 - the fixture
from tests.test_mcp_creatures import _refused

pytestmark = pytest.mark.mcp_server


async def test_a_spell_draft_waits_for_the_gamemaster(
    table: tuple[httpx.AsyncClient, Any],  # noqa: F811
) -> None:
    _raw, facts = table
    registry: StoreRegistry = facts["app"].state.stores
    world_id = facts["world"].id
    async with session(table) as mcp:
        drafted = await call(
            mcp,
            "create_spell",
            world_id=world_id,
            name="Fen Lantern",
            classes=["Wizard", "Nobody"],
            level=1,
            school="V",
            effect="attack",
            attack="R",
            dice="2d8",
            damage_types=["radiant"],
            upcast="1d8",
            text="A green light bobs over the water.",
        )
        assert drafted["url"] == f"/gm/spells/{drafted['id']}"
        assert drafted["spell"]["spellAttack"] == ["R"] and "{@damage 2d8}" in str(
            drafted["spell"]["entries"]
        )
        assert drafted["classes"] and drafted["classes"][0].startswith("Wizard|")
        assert any("Nobody" in p for p in drafted["problems"])
        changed = await call(mcp, "update_spell", design_id=drafted["id"], dice="3d8")
        assert "{@damage 3d8}" in str(changed["spell"]["entries"])
        registry.spell_builder.publish()
        assert "Fen Lantern" not in registry.spell_builder.spell_file.read_text()
        registry.spell_builder.accept(drafted["id"])
        assert "Fen Lantern" in registry.spell_builder.spell_file.read_text()
        assert "Only drafts you created" in await _refused(
            mcp, "update_spell", design_id=drafted["id"], level=2
        )
