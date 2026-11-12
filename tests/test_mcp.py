"""Paravent's MCP server (documentation/MCP.md): tokens, the door, the reading tools.

Driven with the MCP SDK's own client over the app's ASGI transport, as Claude Code
would talk to it over HTTP.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.main import create_app
from app.mcp.server import level_gap_warning
from app.models.gamestate import Npc, Role, Scope, Sound, SourceSet, World
from app.models.pc import PlayerCharacter
from app.services.mcp_tokens import McpTokenError, McpTokenService
from app.store.gamestate import GameStateStore
from app.store.registry import StoreRegistry

# Its app builds the MCP server, which the other tests leave out (tests/conftest.py).
pytestmark = pytest.mark.mcp_server

PASSWORD = "correct horse battery"
EXAMPLES = Path(__file__).resolve().parent.parent / "documentation" / "examples"


class TestTokens:
    def test_made_once_kept_as_a_hash(self, gamestate: GameStateStore) -> None:
        gm = gamestate.users.create(_user("dm", Role.GAMEMASTER))
        service = McpTokenService(gamestate)
        made = service.create("  Claude Code,   laptop ", gm)
        assert made.token.startswith("pv_") and made.record.name == "Claude Code, laptop"
        assert made.token not in json.dumps(gamestate.mcp_tokens.list()[0].model_dump(mode="json"))
        found = service.verify(made.token)
        assert found is not None and found[1].id == gm.id
        assert found[0].last_used_at is not None
        assert service.verify(made.token + "x") is None and service.verify("") is None
        service.revoke(made.record.id)
        assert service.verify(made.token) is None

    def test_only_an_active_gamemaster(self, gamestate: GameStateStore) -> None:
        service = McpTokenService(gamestate)
        with pytest.raises(McpTokenError):
            service.create("x", gamestate.users.create(_user("alice", Role.PLAYER)))
        gm = gamestate.users.create(_user("dm", Role.GAMEMASTER))
        made = service.create("x", gm)
        gamestate.users.update(gm.id, active=False)
        assert service.verify(made.token) is None
        with pytest.raises(McpTokenError):
            service.create("   ", gm)


def _user(name: str, role: Role) -> Any:
    from app.models.gamestate import User

    return User(username=name, role=role)


@pytest.fixture
async def table(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    gm = registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(
        World(name="Grey Reed", language="de", source_set=SourceSet(sources=["PHB", "MM", "DMG"]))
    )
    registry.gamestate.npcs.create(
        Npc(scope=Scope(world_id=world.id), name="Hob Wade", roles=["ally"])
    )
    ids = []
    for file in ("lyra-mooncaller.json", "brunhild-ironvow.json"):
        data = json.loads((EXAMPLES / file).read_text(encoding="utf-8"))
        data["world_id"] = world.id
        ids.append(registry.gamestate.characters.create(PlayerCharacter.model_validate(data)).id)
    token = registry.mcp_tokens.create("tests", gm).token
    app = create_app()
    app.state.stores = registry
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        yield raw, {"world": world, "ids": ids, "token": token, "app": app}


@asynccontextmanager
async def session(table: tuple[httpx.AsyncClient, Any]) -> AsyncIterator[ClientSession]:
    _raw, facts = table
    headers = {"Authorization": f"Bearer {facts['token']}"}
    transport = httpx.ASGITransport(app=facts["app"])
    # The session manager runs once per app, and inside one task: here, not the fixture.
    async with (
        facts["app"].state.mcp.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client,
        streamable_http_client("http://test/mcp", http_client=client) as streams,
        ClientSession(streams[0], streams[1]) as mcp,
    ):
        await mcp.initialize()
        yield mcp


async def call(mcp: ClientSession, tool: str, **arguments: Any) -> Any:
    result = await mcp.call_tool(tool, arguments)
    assert not result.is_error, result.content
    data = result.structured_content
    assert data is not None
    return data.get("result", data)


class TestTheDoor:
    async def test_no_token_no_entry(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        raw, _facts = table
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert (await raw.post("/mcp", json=body)).status_code == 401
        wrong = {"Authorization": "Bearer pv_nothing"}
        refused = await raw.post("/mcp", json=body, headers=wrong)
        assert (
            refused.status_code == 401 and "bearer" in refused.headers["www-authenticate"].lower()
        )

    async def test_guessing_slows_down(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        raw, _facts = table
        codes = [
            (await raw.post("/mcp", json={}, headers={"Authorization": "Bearer pv_x"})).status_code
            for _ in range(7)
        ]
        assert codes[:5] == [401] * 5 and 429 in codes

    async def test_tools_are_listed(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        async with session(table) as mcp:
            names = {t.name for t in (await mcp.list_tools()).tools}
        assert {
            "list_worlds",
            "world_overview",
            "party",
            "search_reference",
            "find_monsters",
            "get_reference",
            "rate_encounter",
            "markdown_guide",
        } <= names


class TestReading:
    async def test_worlds_and_overview(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        async with session(table) as mcp:
            worlds = await call(mcp, "list_worlds")
            overview = await call(mcp, "world_overview", world_id=facts["world"].id)
        (world,) = worlds
        assert world["language"] == "de" and world["characters"] == 2
        assert [b["code"] for b in world["books"]] == ["PHB", "MM", "DMG"]
        assert overview["rules"] == "classic"
        assert overview["npcs"][0]["name"] == "Hob Wade"
        assert len(overview["party"]) == 2

    async def test_party(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        async with session(table) as mcp:
            answer = await call(
                mcp, "party", world_id=facts["world"].id, character_ids=[facts["ids"][1], "nope"]
            )
        (brunhild,) = answer["characters"]
        assert brunhild["name"] == "Brunhild Ironvow" and brunhild["level"] == 8
        assert brunhild["armor_class"] and brunhild["hit_points"]
        assert "Paladin" in brunhild["classes"] and answer["missing"] == ["nope"]

    async def test_search_and_reference(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        world_id = facts["world"].id
        async with session(table) as mcp:
            found = await call(mcp, "search_reference", world_id=world_id, query="green hag")
            hag = await call(
                mcp, "get_reference", world_id=world_id, kind="creature", name="Green Hag"
            )
            outside = await call(
                mcp, "get_reference", world_id=world_id, kind="spell", name="Mind Sliver"
            )
        assert found["matches"][0]["tag"] == "{@creature Green Hag}"
        assert hag["found"] and hag["source"] == "MM" and hag["entry"]["cr"]["cr"] == "3"
        assert not outside["found"]  # Tasha's is not among this world's books

    async def test_find_monsters(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        async with session(table) as mcp:
            answer = await call(
                mcp,
                "find_monsters",
                world_id=facts["world"].id,
                cr_min="2",
                cr_max="3",
                environment="swamp",
            )
        names = {m["name"] for m in answer["monsters"]}
        assert "Green Hag" in names
        assert all(m["source"] in ("MM", "PHB", "DMG") for m in answer["monsters"])
        assert all(m["cr"] in ("2", "3") for m in answer["monsters"])

    async def test_rate_encounter(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        async with session(table) as mcp:
            rated = await call(
                mcp,
                "rate_encounter",
                world_id=facts["world"].id,
                monsters=[{"name": "Zombie", "count": 5}, {"name": "Nobody Known"}],
                party_size=2,
                party_level=5,
            )
        # 5 × 50 XP, ×2 for 3–6 monsters and one step up for a party under three.
        assert rated["base_xp"] == 250 and rated["multiplier"] == 2.5
        assert rated["adjusted_xp"] == 625 and rated["difficulty"] == "easy"
        assert rated["unresolved"] == ["Nobody Known"] and rated["party_levels"] == [5, 5]

    async def test_markdown_guide(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        async with session(table) as mcp:
            result = await mcp.call_tool("markdown_guide", {})
        assert "{@encounter" in result.content[0].text  # type: ignore[union-attr]


class TestTokenPage:
    async def test_make_show_once_revoke(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        raw, facts = table
        registry = facts["app"].state.stores
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        await raw.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await raw.get("/gm/mcp")).status_code == 403
        await raw.post("/logout")
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        made = await raw.post("/gm/mcp", data={"name": "Laptop"})
        assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
        token = re.search(r"Bearer (pv_[A-Za-z0-9_-]+)", made.text).group(1)  # type: ignore[union-attr]
        assert token.startswith("pv_") and "http://test/mcp" in made.text
        assert "mcp-remote" in made.text and "--allow-http" in made.text
        assert f"Bearer {token}" in made.text.replace("&quot;", '"')  # the Desktop config too
        assert token not in (await raw.get("/gm/mcp")).text  # shown that once only
        laptop = next(x for x in registry.mcp_tokens.list() if x.name == "Laptop")
        assert registry.mcp_tokens.verify(token) is not None
        await raw.post(f"/gm/mcp/{laptop.id}/revoke")
        assert registry.mcp_tokens.verify(token) is None
        assert (await raw.post("/gm/mcp", data={"name": " "})).status_code == 422


async def refused(mcp: ClientSession, tool: str, **arguments: Any) -> str:
    result = await mcp.call_tool(tool, arguments)
    assert result.is_error, result
    return result.content[0].text  # type: ignore[union-attr]


class TestDrafts:
    async def test_a_draft_written_through_mcp(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Die Nesselmuhme",
                summary="Folk horror in the fens.",
                character_ids=[brunhild],
                level_start=8,
            )
            adventure_id = draft["id"]
            assert draft["status"] == "draft" and draft["writable"]
            assert draft["party"] == [brunhild] and draft["level_start"] == 8

            hag = await call(
                mcp,
                "create_npc",
                adventure_id=adventure_id,
                name="Nesselmuhme",
                notes="**Wants:** the village.",
                roles=["villain"],
                statblock="Green Hag",
            )
            assert hag["statblock"] == "Green Hag|MM"
            assert "not a creature" in await refused(
                mcp, "create_npc", adventure_id=adventure_id, name="X", notes="", statblock="Nope"
            )

            fight = await call(
                mcp,
                "create_encounter",
                adventure_id=adventure_id,
                name="Moorleichen",
                monsters=[{"name": "Zombie", "count": 5}],
                notes="At the willow cross.",
            )
            # Rated for the adventure's party — Brunhild alone — not the whole world.
            assert fight["rating"]["party_levels"] == [8]
            assert fight["rating"]["base_xp"] == 250
            assert "Not in this world's books: Owlbear Ghost" in await refused(
                mcp,
                "create_encounter",
                adventure_id=adventure_id,
                name="X",
                monsters=[{"name": "Owlbear Ghost"}],
            )

            hoard = await call(
                mcp,
                "create_treasure",
                adventure_id=adventure_id,
                name="Hort der Muhme",
                items=[{"name": "Potion of Healing", "count": 2}, {"name": "Moon Cheese"}],
                coins={"gp": 85, "sp": 140},
            )
            assert hoard["items"] == [{"name": "Potion of Healing", "source": "DMG", "count": 2}]
            assert hoard["unresolved"] == ["Moon Cheese"] and hoard["coins"]["gp"] == 85
            hinted = await call(
                mcp,
                "create_treasure",
                adventure_id=adventure_id,
                name="Fast richtig",
                items=[{"name": "Potion of Healing (Rot)"}],
            )
            assert hinted["unresolved"] == [
                "Potion of Healing (Rot) (the books call it Potion of Healing)"
            ]
            await call(mcp, "delete", kind="treasure", id=hinted["id"])

            body = (
                "# Ankunft\n\n> [!read]\n> Nebel.\n\n{@npc Nesselmuhme}\n\n"
                "{@encounter Moorleichen}\n\nSie wirkt {@spell Firebal}.\n"
            )
            chapter = await call(
                mcp, "write_chapter", adventure_id=adventure_id, title="Ankunft", body=body
            )
            assert [(p["kind"], p["line"]) for p in chapter["problems"]] == [("reference", 10)]
            assert chapter["problems"][0]["instead"][0] == "{@spell Fireball}"
            fixed = await call(
                mcp,
                "write_chapter",
                adventure_id=adventure_id,
                title="Ankunft",
                body=body.replace("Firebal}", "Fireball}"),
                chapter_id=chapter["id"],
                base=chapter["version"],
            )
            assert fixed["problems"] == [] and fixed["id"] == chapter["id"]
            # Written from an older text: refused, the current text handed back.
            stale = await refused(
                mcp,
                "write_chapter",
                adventure_id=adventure_id,
                title="Ankunft",
                body="x",
                chapter_id=chapter["id"],
                base=chapter["version"],
            )
            assert "current text" in stale and "Fireball" in stale

            whole = await call(mcp, "read_adventure", adventure_id=adventure_id)
            assert [c["title"] for c in whole["chapters"]] == ["Ankunft"]
            assert {n["name"] for n in whole["npcs"]} == {"Nesselmuhme"}
            assert [e["name"] for e in whole["encounters"]] == ["Moorleichen"]
            assert [t["name"] for t in whole["treasures"]] == ["Hort der Muhme"]

            assert "Deleted" in await call(mcp, "delete", kind="npc", id=hag["id"])

            # The gamemaster takes it over: from now on it is read only.
            registry.gamestate.adventures.update(adventure_id, status="planned")
            assert "not a draft" in await refused(
                mcp, "create_npc", adventure_id=adventure_id, name="Y", notes=""
            )
            assert not (await call(mcp, "read_adventure", adventure_id=adventure_id))["writable"]
        assert lyra  # the other character stayed out of the party

    async def test_nothing_else_is_written(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        mine = registry.adventures.create(world_id, "Hand-made")
        # Even a draft the gamemaster made by hand stays theirs.
        registry.gamestate.adventures.update(mine.id, status="draft")
        hob = registry.npcs.list(world_id)[0]
        async with session(table) as mcp:
            assert "not a draft" in await refused(
                mcp, "write_chapter", adventure_id=mine.id, title="X", body="y"
            )
            assert "belongs to the world" in await refused(
                mcp, "update_npc", npc_id=hob.id, base="", notes="changed"
            )
            assert "belongs to the world" in await refused(mcp, "delete", kind="npc", id=hob.id)
            assert "not a draft" in await refused(mcp, "delete", kind="adventure", id=mine.id)
        assert registry.npcs.get(hob.id).notes == ""
        assert registry.adventures.chapters(mine.id) == []


class TestAdventureParty:
    async def test_chosen_in_the_form(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        raw, facts = table
        registry = facts["app"].state.stores
        lyra, brunhild = facts["ids"]
        adventure = registry.adventures.create(facts["world"].id, "Two groups")
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        form = await raw.get(f"/gm/adventures/{adventure.id}/edit")
        assert f'value="{lyra}"' in form.text and f'value="{brunhild}"' in form.text
        saved = await raw.post(
            f"/gm/adventures/{adventure.id}/edit",
            data={"name": "Two groups", "status": "planned", "party": [lyra, "stranger"]},
        )
        assert saved.status_code == 303
        assert registry.adventures.get(adventure.id).party == [lyra]
        await raw.post(
            f"/gm/adventures/{adventure.id}/edit", data={"name": "Two groups", "status": "planned"}
        )
        assert registry.adventures.get(adventure.id).party is None


class TestNames:
    async def test_styles_and_names_for_a_world(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        world_id = facts["world"].id
        async with session(table) as mcp:
            styles = await call(mcp, "list_naming_styles")
            norse = next(s for s in styles if s["key"] == "starter:old-norse")
            assert norse["kind"] == "starter" and len(norse["examples"]) == 3
            answer = await call(
                mcp,
                "generate_names",
                world_id=world_id,
                style="starter:old-norse",
                gender="female",
                count=5,
            )
            assert len(answer["names"]) == 5
            assert all(n.endswith("dottir") for n in answer["names"])
            default = await call(mcp, "generate_names", world_id=world_id, count=2)
            assert default["style"] and len(default["names"]) == 2
            assert "list_naming_styles" in await refused(
                mcp, "generate_names", world_id=world_id, style="starter:nobody"
            )
            assert "female" in await refused(
                mcp, "generate_names", world_id=world_id, gender="other"
            )


class TestCraft:
    async def test_budget_random_encounters_and_treasure(
        self, table: tuple[httpx.AsyncClient, Any]
    ) -> None:
        _raw, facts = table
        world_id = facts["world"].id
        lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            budget = await call(mcp, "party_budget", world_id=world_id, character_ids=[brunhild])
            assert budget["rules"] == "classic" and budget["day_xp"] == 6000  # DMG, level 8
            assert budget["encounter_bands"]["deadly"] == 2100

            tables = await refused(
                mcp, "random_encounter", world_id=world_id, environment="Nowhere|X"
            )
            assert "Swamp|XGE" in tables
            rolled = await call(
                mcp,
                "random_encounter",
                world_id=world_id,
                environment="Swamp|XGE",
                character_ids=[brunhild],
            )
            assert rolled["table"] == "Swamp (XGE)" and rolled["text"]
            if rolled["monsters"]:
                assert rolled["rating"]["party_levels"] == [8]

            built = await call(
                mcp,
                "build_encounter",
                world_id=world_id,
                difficulty="hard",
                shape="boss with minions",
                habitat="swamp",
                character_ids=[brunhild],
            )
            assert built["shape"] == "Boss with Minions" and built["monsters"]
            assert built["rating"]["party_levels"] == [8]
            assert built["rating"]["difficulty"] in ("medium", "hard", "deadly")
            assert "easy, medium, hard, deadly" in await refused(
                mcp, "build_encounter", world_id=world_id, difficulty="moderate"
            )
            assert "Horde" in await refused(
                mcp, "build_encounter", world_id=world_id, difficulty="hard", shape="Swarm"
            )
            assert "forest" in await refused(
                mcp, "build_encounter", world_id=world_id, difficulty="hard", habitat="moon"
            )

            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Hoard test",
                summary="",
                character_ids=[lyra],
            )
            hoard = await call(mcp, "create_treasure", adventure_id=draft["id"], name="Hort")
            filled = await call(mcp, "roll_treasure", treasure_id=hoard["id"], kind="hoard", cr=3)
            assert sum(filled["coins"].values()) > 0

    async def test_the_prompt(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        async with session(table) as mcp:
            prompts = {p.name for p in (await mcp.list_prompts()).prompts}
            assert prompts == {"new_adventure", "new_naming_style"}
            got = await mcp.get_prompt("new_adventure", {"world": "Grey Reed", "idea": "a hag"})
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert '"Grey Reed"' in text and "a hag" in text and "{@creature …}" in text
        assert "world's instructions name" in text

    async def test_the_prompt_takes_a_number_of_sessions(
        self, table: tuple[httpx.AsyncClient, Any]
    ) -> None:
        async with session(table) as mcp:
            got = await mcp.get_prompt("new_adventure", {"world": "X", "sessions": "3"})
        assert "3 session(s) of play" in got.messages[0].content.text  # type: ignore[union-attr]


class TestDraftReview:
    async def test_review_then_accept(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        _lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Bell",
                summary="",
                character_ids=[brunhild],
            )
            await call(
                mcp,
                "create_encounter",
                adventure_id=draft["id"],
                name="Cross",
                monsters=[{"name": "Zombie", "count": 5}],
            )
            await call(
                mcp,
                "write_chapter",
                adventure_id=draft["id"],
                title="Road",
                body="{@encounter Cross}\n\n{@spell Firebal}\n",
            )
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await raw.get(f"/gm/adventures/{draft['id']}")).text
        assert 'class="panel draft-review"' in page and "“tests”" in page
        assert "1 problem" in page and "Brunhild Ironvow" in page
        assert "6,000 XP" in page  # Brunhild's adventuring day at level 8 (2014)
        accepted = await raw.post(f"/gm/adventures/{draft['id']}/accept")
        assert accepted.status_code == 303
        assert registry.adventures.get(draft["id"]).status == "planned"
        after = (await raw.get(accepted.headers["location"])).text
        assert "draft-review" not in after and "Claude can only read it now" in after


class TestCampaignHistory:
    def test_progress_lines(self) -> None:
        from app.mcp.history import progress_line
        from app.models.protocol import EntryKind, ProtocolEntry

        level = ProtocolEntry(kind=EntryKind.LEVEL_UP, subject_name="Varis", what="level:Fighter:9")
        xp = ProtocolEntry(kind=EntryKind.EXPERIENCE, subject_name="Varis", amount=450, note="Bog")
        assert progress_line(level) == "Varis reached Fighter level 9"
        assert progress_line(xp) == "Varis gained 450 XP (Bog)"
        assert progress_line(ProtocolEntry(kind=EntryKind.ROUND)) is None

    async def test_what_happened(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        lyra, brunhild = facts["ids"]
        adventure = registry.adventures.create(world_id, "Fog on the River")
        chapter = registry.adventures.create_chapter(adventure.id, "Ambush")
        registry.adventures.update_chapter(chapter.id, title="Ambush", status="finished", body="")
        encounter = registry.encounters.create(
            "Goblin ambush", world_id, Scope(world_id=world_id, adventure_id=adventure.id)
        )
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 4)
        fight = registry.combats.start(encounter, by="dm", characters=[brunhild])
        registry.combats.end(fight.id, by="dm")
        registry.characters.award_xp(brunhild, 200, "Goblin ambush", by="dm")
        async with session(table) as mcp:
            history = await call(mcp, "campaign_history", world_id=world_id)
        assert any("Brunhild Ironvow, level 8" in c for c in history["characters"])
        goblins = next(a for a in history["adventures"] if a["name"] == "Fog on the River")
        assert goblins["chapters"] == [{"title": "Ambush", "status": "finished"}]
        (today,) = history["sessions"]
        (ran,) = today["fights"]
        assert ran["encounter"] == "Goblin ambush" and ran["adventure"] == "Fog on the River"
        assert ran["finished"] and ran["party"] == ["Brunhild Ironvow"]
        assert today["progress"] == ["Brunhild Ironvow gained 200 XP (Goblin ambush)"]
        assert lyra and history["older_sessions"] == 0


class TestDraftOnDisk:
    async def test_party_and_key_survive_a_restart(
        self, table: tuple[httpx.AsyncClient, Any]
    ) -> None:
        _raw, facts = table
        registry = facts["app"].state.stores
        _lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=facts["world"].id,
                name="Kept",
                summary="",
                character_ids=[brunhild],
            )
        path = registry.gamestate.prose.path(draft["id"])
        assert path is not None and "drafted_by:" in path.read_text(encoding="utf-8")
        registry.gamestate.prose.load()  # what a restart reads
        again = registry.adventures.get(draft["id"])
        assert again.status == "draft" and again.party == [brunhild]
        assert again.drafted_by is not None


class TestHelp:
    async def test_the_guide_in_both_languages(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        raw, _facts = table
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        assert 'href="/help/mcp?back=/gm/mcp"' in (await raw.get("/gm/mcp")).text
        english = await raw.get("/help/mcp")
        assert english.status_code == 200 and "mcp-remote" in english.text
        german = await raw.get("/help/mcp", headers={"Accept-Language": "de"})
        assert "Abenteuer mit Claude schreiben" in german.text or "mcp-remote" in german.text


def test_the_desktop_config_is_valid_json() -> None:
    from app.ui.mcp_pages import desktop_config

    entry = json.loads(desktop_config("http://host:8000/mcp", "pv_x"))["mcpServers"]["paravent"]
    assert entry["args"][2] == "http://host:8000/mcp" and " " not in entry["args"][-1]
    assert entry["env"]["PARAVENT_KEY"] == "Bearer pv_x"


class TestWholeAdventure:
    async def test_no_stream_stays_open(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        """An open GET would keep the server from reloading or shutting down."""
        raw, facts = table
        headers = {"Authorization": f"Bearer {facts['token']}", "Accept": "text/event-stream"}
        assert (await raw.get("/mcp", headers=headers)).status_code == 405

    def test_a_party_far_apart_is_warned_about(self) -> None:
        assert "from 1 to 8" in level_gap_warning([1, 8, 8])
        assert level_gap_warning([5, 8]) == "" and level_gap_warning([]) == ""

    async def test_the_check_reads_the_notes_and_later_names(
        self, table: tuple[httpx.AsyncClient, Any]
    ) -> None:
        _raw, facts = table
        _lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=facts["world"].id,
                name="Checked",
                summary="",
                character_ids=[brunhild],
            )
            await call(
                mcp,
                "create_npc",
                adventure_id=draft["id"],
                name="Herald",
                notes="Knows {@spell Fireblast} and {@place Later}.",
            )
            found = await call(mcp, "check_adventure", adventure_id=draft["id"])
            assert {(p["where"], p["name"]) for p in found} == {
                ("npc Herald", "Fireblast"),
                ("npc Herald", "Later"),
            }
            await call(mcp, "create_place", adventure_id=draft["id"], name="Later", notes="")
            found = await call(mcp, "check_adventure", adventure_id=draft["id"])
            assert [p["name"] for p in found] == ["Fireblast"]

    async def test_sounds_can_be_listed(self, table: tuple[httpx.AsyncClient, Any]) -> None:
        _raw, facts = table
        registry = facts["app"].state.stores
        registry.gamestate.sounds.create(Sound(name="Rain", kind="ambience", file="rain.wav"))
        async with session(table) as mcp:
            assert await call(mcp, "list_sounds") == [{"name": "Rain", "kind": "ambience"}]
            guide = await call(mcp, "markdown_guide")
            assert "{@sound Name}" in str(guide)
