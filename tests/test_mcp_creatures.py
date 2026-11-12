"""Creatures over MCP: Claude drafts in the creature builder (app/mcp/creatures.py)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models.gamestate import EncounterMonster
from app.services.creature_pictures import design_ref
from app.services.media import MediaError
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


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


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
            touch = await call(
                mcp,
                "update_creature",
                design_id=lich["id"],
                actions=[
                    {
                        "name": "Paralyzing Touch",
                        "text": "{@atk ms} {@hit <$to_hit__int$>} to hit. {@h}<$damage_avg__10.5$>"
                        " ({@damage 3d6}) cold damage, <$power_level$> times.",
                    }
                ],
            )
            assert any("10 ({@damage 3d6})" in line for line in touch["statblock"])
            assert [(p["kind"], p["text"]) for p in touch["problems"]] == [
                ("placeholder", "<$power_level$>")
            ]
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


class TestItsPicture:
    async def test_claude_gives_a_picture_by_its_address(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        world_id = facts["world"].id
        fetched: list[str] = []

        async def fake_fetch(url: str) -> tuple[str, bytes]:
            fetched.append(url)
            if "broken" in url:
                raise MediaError("media.error.content")
            return "picture.png", PNG

        monkeypatch.setattr("app.mcp.creatures.fetch", fake_fetch)
        pictures = registry.creature_pictures
        async with session(table) as mcp:
            drafted = await call(
                mcp,
                "create_creature",
                world_id=world_id,
                name="Moorhulk",
                cr="2",
                image_url="https://example.org/moorhulk.png",
            )
            assert drafted["picture"] == f"/gm/creatures/{drafted['id']}/picture"
            assert pictures.find(world_id, design_ref("Moorhulk")) is not None
            # a refused address changes nothing, the design included
            assert "does not match" in await _refused(
                mcp,
                "update_creature",
                design_id=drafted["id"],
                name="Changed",
                image_url="https://example.org/broken",
            )
            assert registry.creatures.get(drafted["id"]).name == "Moorhulk"
            renamed = await call(mcp, "update_creature", design_id=drafted["id"], name="Bog Hulk")
            assert renamed["picture"] and pictures.find(world_id, design_ref("Moorhulk")) is None
            gone = await call(mcp, "update_creature", design_id=drafted["id"], image_url="")
            assert gone["picture"] is None
        assert fetched == ["https://example.org/moorhulk.png", "https://example.org/broken"]

    async def test_the_gamemaster_uploads_and_removes_it_in_the_builder(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        design = registry.creatures.create("Bog Hound", facts["world"].id, "1")
        url = f"/gm/creatures/{design.id}"
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        assert 'id="cb-picture"' in (await raw.get(url)).text
        assert (await raw.get(f"{url}/picture")).status_code == 404
        refused = await raw.post(f"{url}/picture", files={"picture": ("x.png", b"nope")})
        assert refused.status_code == 422
        done = await raw.post(f"{url}/picture", files={"picture": ("hound.png", PNG)})
        assert done.status_code == 303
        page = (await raw.get(url)).text
        assert f'src="{url}/picture?v=' in page
        served = await raw.get(f"{url}/picture")
        assert served.status_code == 200 and served.content == PNG
        # renaming in the builder keeps the picture with the creature
        form = {"action": "save", "name": "Bog Wolf", "world": facts["world"].id, "cr": "1"}
        await raw.post(url, data=form)
        assert registry.creatures.get(design.id).name == "Bog Wolf"
        assert registry.creature_pictures.find(facts["world"].id, design_ref("Bog Wolf"))
        await raw.post(f"{url}/picture", data={"do": "remove"})
        assert (await raw.get(f"{url}/picture")).status_code == 404


class TestInAnAdventure:
    async def test_claudes_creature_fights_in_its_draft_and_is_accepted_with_it(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        world_id = facts["world"].id
        async with session(table) as mcp:
            hulk = await call(mcp, "create_creature", world_id=world_id, name="Moorhulk", cr="3")
            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Bog",
                summary="",
                character_ids=[facts["ids"][1]],
            )
            fight = await call(
                mcp,
                "create_encounter",
                adventure_id=draft["id"],
                name="Mire",
                monsters=[{"name": "moorhulk"}, {"name": "Zombie", "count": 2}],
            )
            rated = {m["name"]: m for m in fight["rating"]["monsters"]}
            assert rated["Moorhulk"]["source"] == "Homebrew" and rated["Moorhulk"]["xp_each"] == 700
            assert "Not in this world's books: Bogwight" in await _refused(
                mcp,
                "create_encounter",
                adventure_id=draft["id"],
                name="X",
                monsters=[{"name": "Bogwight"}],
            )
        assert registry.compendium.find("monster", "Moorhulk", "Homebrew") is None
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await raw.get(f"/gm/adventures/{draft['id']}")).text
        assert "Claude&#39;s creatures, accepted" in page and f"/gm/creatures/{hulk['id']}" in page
        await raw.post(f"/gm/adventures/{draft['id']}/accept")
        assert registry.creatures.get(hulk["id"]).drafted_by is None
        assert registry.compendium.find("monster", "Moorhulk", "Homebrew") is not None
        assert registry.worlds.find(world_id).source_set.allows("Homebrew")


class TestForAGermanTable:
    async def test_distances_in_metres_and_no_offer_to_translate(
        self,
        table: tuple[httpx.AsyncClient, Any],  # noqa: F811
    ) -> None:
        raw, facts = table
        registry: StoreRegistry = facts["app"].state.stores
        world_id = facts["world"].id
        design = registry.creatures.create("Moorhulk", world_id, "3")
        design = registry.creatures.save(
            design.model_copy(update={"speed": {"walk": 30}, "senses": "darkvision 60 ft."})
        )
        url = f"/gm/creatures/{design.id}"
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        await raw.post("/language", data={"lang": "de", "next": "/"})
        page = (await raw.get(url)).text
        assert 'name="speed_walk"' in page and 'step="1.5" value="9"' in page
        assert 'value="Dunkelsicht 18 m"' in page
        form = {
            "action": "save",
            "name": "Moorhulk",
            "world": world_id,
            "cr": "3",
            "speed_walk": "12",
            "speed_swim": "4,5",
            "senses": "Dunkelsicht 36 m, Erschütterungssinn 3 m",
        }
        await raw.post(url, data=form)
        saved = registry.creatures.get(design.id)
        assert saved.speed == {"walk": 40, "swim": 15} and saved.senses == (
            "darkvision 120 ft., tremorsense 10 ft."
        )

        # The builder's own templates write German and metres.
        form["action"] = "attack"
        form["attack_name"] = "Klaue"
        form["attack_dice"] = "2d6"
        form["attack_type"] = "slashing"
        await raw.post(url, data=form)
        claw = registry.creatures.get(design.id).actions[-1]
        assert claw.name == "Klaue" and "Reichweite 1,5 m" in claw.text
        assert claw.text.endswith("Hiebschaden.")

        # In an encounter, the creature opens in the builder, draft or published.
        encounter = registry.encounters.create("Mire", world_id)
        registry.encounters.set_monsters(
            encounter.id, [EncounterMonster(name="Moorhulk", source="Homebrew")]
        )
        plan = (await raw.get(f"/gm/encounters/{encounter.id}")).text
        assert f'href="{url}"' in plan and "Kreaturenbaukasten" in plan

        # Written in the table's language: no name to give, nothing to translate.
        registry.creatures.publish()
        entry = (await raw.get("/ref/monster/Homebrew/Moorhulk")).text
        assert "Moorhulk" in entry and "ref-names" not in entry and "tr-note" not in entry
