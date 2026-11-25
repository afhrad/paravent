"""Spells over MCP: Claude drafts in the spell builder (app/mcp/spells.py)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.store.registry import StoreRegistry
from tests.test_mcp import PASSWORD, call, session
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


async def test_revisions_of_a_spell_and_a_background(
    table: tuple[httpx.AsyncClient, Any],  # noqa: F811
) -> None:
    raw, facts = table
    registry: StoreRegistry = facts["app"].state.stores
    world_id = facts["world"].id
    spell = registry.spell_builder.create("Mist Step", world_id)
    background = registry.background_builder.create("Tollkeeper", world_id)
    registry.spell_builder.publish()
    registry.background_builder.publish()
    async with session(table) as mcp:
        copy = await call(mcp, "start_spell_revision", spell="Mist Step")
        assert copy["id"] != spell.id and copy["name"] == "Mist Step"
        await call(mcp, "update_spell", design_id=copy["id"], level=2)
        assert "keeps its spell's name" in await _refused(
            mcp, "update_spell", design_id=copy["id"], name="Fog Step"
        )
        bg_copy = await call(mcp, "start_background_revision", background=background.id)
        await call(mcp, "update_background", design_id=bg_copy["id"], gold=20)
    assert registry.spell_builder.get(spell.id).level == 1  # untouched until accepted
    assert registry.spell_builder.changes(registry.spell_builder.get(copy["id"])) == ["level"]
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})
    page = (await raw.get(f"/gm/spells/{copy['id']}")).text
    assert "Changes: Level." in page
    assert "Claude has proposed changes" in (await raw.get(f"/gm/spells/{spell.id}")).text
    accepted = registry.spell_builder.accept_revision(copy["id"])
    assert accepted.id == spell.id and accepted.level == 2
    assert registry.spell_builder.revision(spell.id) is None
    done = registry.background_builder.accept_revision(bg_copy["id"])
    assert done.id == background.id and done.gold == 20
