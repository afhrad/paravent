"""Creatures over MCP: Claude drafts in the creature builder (app/mcp/creatures.py)."""

from __future__ import annotations

from typing import Any

import httpx

from app.store.registry import StoreRegistry
from tests.test_mcp import PASSWORD, call, session
from tests.test_mcp import table as table  # noqa: F401 - the fixture

SPELLCASTING = (
    "<$title_short_name$> is a 7th-level spellcaster. Its spellcasting ability is "
    "Intelligence (spell save DC <$dc__int$>, {@hit <$to_hit__int$>} to hit with spell "
    "attacks). It has the following wizard spells prepared:\n\n"
    "Cantrips (at will): {@spell chill touch}, {@spell mage hand}\n\n"
    "1st level (4 slots): {@spell magic missile}, {@spell shield}\n\n"
    "2nd level (3 slots): {@spell misty step}, {@spell Grimbolt of Sorrow}\n\n"
    "3rd level (3 slots): {@spell animate dead}, {@spell counterspell}\n\n"
    "4th level (1 slot): {@spell blight}"
)


async def _refused(mcp: Any, tool: str, **arguments: Any) -> str:
    result = await mcp.call_tool(tool, arguments)
    assert result.is_error, result.content
    return str(result.content[0].text)  # type: ignore[union-attr]


class TestDraftingACreature:
    async def test_a_blank_creature_plays_near_its_rating(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        _raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        async with session(table) as mcp:
            guide = await call(mcp, "creature_guide")
            assert "Spellcasting" in guide and "damage_per_round" in guide
            drafted = await call(
                mcp, "create_creature", world_id=facts["world"].id, name="Moorhulk", cr="5"
            )
        assert drafted["target_cr"] == "5" and drafted["plays_at"] in ("4", "5", "6")
        assert drafted["url"] == f"/gm/creatures/{drafted['id']}" and drafted["statblock"]
        design = registry.creatures.get(drafted["id"])
        assert design.drafted_by and design.world_id == facts["world"].id

    async def test_a_lich_at_cr_5_with_spells_from_the_books(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        world_id = facts["world"].id
        async with session(table) as mcp:
            lich = await call(
                mcp,
                "create_creature",
                world_id=world_id,
                name="Lesser Lich",
                cr="5",
                based_on="Lich",
                abilities={"str": 10, "dex": 14, "con": 14, "int": 18},
                legendary=[],
                traits=[{"name": "Spellcasting", "text": SPELLCASTING}],
                fit=True,
            )
            assert lich["based_on"] == "Lich|MM"
            assert [p["text"] for p in lich["problems"]] == ["{@spell Grimbolt of Sorrow}"]
            assert any("DC 15" in line for line in lich["statblock"])  # 8 + 3 + INT 4
            fixed = await call(
                mcp,
                "update_creature",
                design_id=lich["id"],
                traits=[
                    {
                        "name": "Spellcasting",
                        "text": SPELLCASTING.replace("Grimbolt of Sorrow", "ray of enfeeblement"),
                    }
                ],
                features=[{"name": "magic resistance"}],
                damage_per_round=33,
            )
            assert fixed["problems"] == [] and fixed["damage_per_round"] == 33
            design = registry.creatures.get(lich["id"])
            assert [f.name for f in design.features] == ["Magic Resistance"]
            assert design.abilities["wis"] > 0  # scores not given are kept
            assert "not a monster feature" in await _refused(
                mcp, "update_creature", design_id=lich["id"], features=[{"name": "Lich Aura"}]
            )
            assert "size is one of" in await _refused(
                mcp, "update_creature", design_id=lich["id"], size="X"
            )
            assert "not a creature in this world" in await _refused(
                mcp,
                "create_creature",
                world_id=world_id,
                name="Nope",
                cr="5",
                based_on="Tarrasque Junior",
            )

        # A publish leaves Claude's draft out until the gamemaster accepts it.
        own = registry.creatures.create("Bog Hound", world_id, "1")
        assert registry.creatures.publish().count == 1
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await raw.get(f"/gm/creatures/{lich['id']}")).text
        assert "s draft</span>" in page and "Drafted by Claude" in page
        assert 'value="accept"' in page
        listing = (await raw.get("/gm/creatures")).text
        assert "Claude" in listing
        registry.creatures.accept(lich["id"])
        assert registry.creatures.publish().count == 2
        assert own.drafted_by is None

    async def test_the_gamemasters_designs_are_not_claudes(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        _raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        own = registry.creatures.create("Bog Hound", facts["world"].id, "1")
        async with session(table) as mcp:
            assert "gamemaster's" in await _refused(
                mcp, "update_creature", design_id=own.id, name="Mine now"
            )
            assert "challenge rating" in await _refused(
                mcp, "create_creature", world_id=facts["world"].id, name="X", cr="99"
            )
