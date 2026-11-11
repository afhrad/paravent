"""A world's naming styles: only those are rolled from or offered (documentation/NAMES.md §5b)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import EncounterMonster, Npc, Role, Scope, World
from app.models.naming import ListPart, NamingStyle
from app.models.pc import PlayerCharacter
from app.services.naming import Naming, NamingError
from app.services.naming_workshop import raw_text
from app.services.style_drafts import StyleDrafts, Submit
from app.store.gamestate import GameStateStore
from app.store.npc_tables import NpcTables
from app.store.registry import StoreRegistry
from tests.test_mcp import call, session
from tests.test_mcp import table as mcp_table  # noqa: F401 - the fixture

PASSWORD = "correct horse"


def _style(name: str, **fields: object) -> NamingStyle:
    return NamingStyle(
        name=name, pattern="{given}", parts={"given": ListPart(names=[name])}, **fields
    )


def _naming() -> Naming:
    styles = [
        _style("Thalassian", id="wow", race="Elf", culture="Blood Elf"),
        _style("Elrond", id="tolkien", race="Elf", culture="Noldor"),
        _style("Gruk", id="orc", race="Orc"),
    ]
    return Naming(NpcTables(None), lambda: styles)


class TestWithin:
    def test_only_the_chosen_styles_are_offered_and_pooled(self) -> None:
        naming = _naming().within(["own:wow", "own:orc"])
        keys = {e.key for e in naming.entries()}
        assert {"own:wow", "own:orc", "race:Elf", "race:Orc"} <= keys
        assert "own:tolkien" not in keys
        assert not any(k.startswith("starter:") for k in keys)
        assert naming.restricted
        # An elf rolls from the world's elves only.
        assert set(naming.names("race:Elf", count=5)) == {"Thalassian"}

    def test_a_race_the_world_has_no_style_for_falls_back_within_it(self) -> None:
        naming = _naming().within(["own:wow"])
        found = naming.find("Dwarf")
        assert found.fallback and found.key == "own:wow"
        with pytest.raises(NamingError):
            naming.names("own:tolkien")

    def test_the_default_is_the_worlds_humans_found_by_alias(self) -> None:
        styles = [
            _style("Vereesa", id="elf", race="Blutelf"),
            _style("Anduin", id="men", race="Mensch", aliases=["Human"]),
        ]
        naming = Naming(NpcTables(None), lambda: styles).within(["own:elf", "own:men"])
        found = naming.for_creature(["Wolf"], "2014")
        assert found.fallback and found.key == "race:Mensch"

    def test_nothing_chosen_or_only_gone_styles_mean_every_style(self) -> None:
        naming = _naming()
        assert naming.within([]) is naming
        assert naming.within(["own:deleted"]) is naming
        assert not naming.restricted

    def test_the_books_any_style_needs_a_book_style(self) -> None:
        naming = _naming().within(["own:orc"])
        assert all(e.kind != "style" for e in naming.entries())


@pytest.fixture
async def table(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, World, str]]:
    registry = StoreRegistry(
        Settings(
            library_dir=tmp_path / "lib", reference_cache_dir=tmp_path / "cache", watch_files=False
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(World(name="Azeroth"))
    wow = registry.naming_workshop.create(_style("Thrall", race="Orc", culture="Horde"))
    registry.naming_workshop.create(_style("Gorbag", race="Orc", culture="Mordor"))
    encounter = registry.encounters.create("Ambush", world.id)
    registry.gamestate.encounters.mutate(
        encounter.id,
        lambda e: e.monsters.append(EncounterMonster(name="Orc", source="MM", count=1)),
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry, world, f"own:{wow.id}|{encounter.id}"


class TestTheWorldsChoice:
    async def test_ticked_styles_name_the_worlds_creatures(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, ids = table
        key, encounter_id = ids.split("|")
        url = f"/gm/worlds/{world.id}/names"
        page = (await ac.get(url)).text
        assert f'value="{key}"' in page and "Every style is used" in page
        saved = await ac.post(url, data={"style": [key]})
        assert saved.status_code == 303
        assert registry.gamestate.worlds.get(world.id).naming_styles == [key]
        # Kept in the world's file, so a reload (or the file watcher) keeps it too.
        reread = GameStateStore(registry.settings.library_dir).worlds.get(world.id)
        assert reread.naming_styles == [key]
        assert "1 style chosen" in (await ac.get(url)).text
        for _ in range(5):
            registry.encounters.set_label(encounter_id, 0, "")
            await ac.post(f"/gm/encounters/{encounter_id}/monsters/0/label/roll", data={})
            label = registry.gamestate.encounters.get(encounter_id).monsters[0].label
            assert label == "Thrall"
        builder = (await ac.get(f"/gm/encounters/{encounter_id}")).text
        assert "Mordor" not in builder and "starter:" not in builder
        # A style outside the world is refused, not rolled.
        refused = await ac.post(
            f"/gm/encounters/{encounter_id}/monsters/0/label/roll",
            data={"style": "starter:old-norse"},
        )
        assert refused.status_code == 422

    async def test_every_style_clears_the_choice(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, ids = table
        url = f"/gm/worlds/{world.id}/names"
        await ac.post(url, data={"style": [ids.split("|")[0]]})
        await ac.post(url, data={"every": "1", "style": [ids.split("|")[0]]})
        assert registry.gamestate.worlds.get(world.id).naming_styles == []
        assert registry.naming_for(world.id) is registry.naming
        world_page = (await ac.get(f"/gm/worlds/{world.id}")).text
        assert f'href="{url}"' in world_page

    async def test_the_wizard_suggests_from_the_worlds_styles(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, ids = table
        await ac.post(f"/gm/worlds/{world.id}/names", data={"style": [ids.split("|")[0]]})
        pc = registry.gamestate.characters.create(
            PlayerCharacter(name="", player="dm", world_id=world.id)
        )
        naming = registry.drafts._naming_of(pc)
        assert naming is not None and naming.restricted
        assert naming.names(naming.find("Orc").key) == ["Thrall"]


class TestClaudesStylesForAWorld:
    async def test_a_draft_for_a_world_joins_it_when_accepted(
        self,
        mcp_table: tuple[AsyncClient, Any],  # noqa: F811
    ) -> None:
        _raw, facts = mcp_table
        registry: StoreRegistry = facts["app"].state.stores
        world = facts["world"]
        orc = registry.naming_workshop.create(_style("Gruk", race="Orc"))
        async with session(mcp_table) as mcp:
            chosen = await call(
                mcp, "set_world_naming_styles", world_id=world.id, keys=[f"own:{orc.id}"]
            )
            assert chosen == {"world_id": world.id, "every": False, "styles": ["Gruk"]}
            refused = await mcp.call_tool(
                "set_world_naming_styles", {"world_id": world.id, "keys": ["race:Orc"]}
            )
            assert refused.is_error
            drafted = await call(
                mcp,
                "draft_naming_style",
                name="Tauren",
                race="Tauren",
                pattern="{given}",
                parts={"given": {"kind": "list", "names": ["Cairne", "Baine"]}},
                world_id=world.id,
            )
            # A replacement keeps the world it was made for.
            await call(
                mcp,
                "draft_naming_style",
                name="Tauren",
                race="Tauren",
                pattern="{given}",
                parts={"given": {"kind": "list", "names": ["Hamuul", "Baine"]}},
                style_id=drafted["id"],
            )
        draft = registry.naming_workshop.get(drafted["id"])
        assert draft.for_world == world.id and "for_world" not in raw_text(draft)
        key = f"own:{draft.id}"
        assert key not in registry.worlds.get(world.id).naming_styles
        registry.naming_workshop.accept(draft.id)
        assert registry.worlds.get(world.id).naming_styles == [f"own:{orc.id}", key]
        assert registry.naming_workshop.get(draft.id).for_world is None

    async def test_claude_hears_which_peoples_have_no_style_and_which_wait(
        self,
        mcp_table: tuple[AsyncClient, Any],  # noqa: F811
    ) -> None:
        _raw, facts = mcp_table
        registry: StoreRegistry = facts["app"].state.stores
        world = facts["world"]
        orc = registry.naming_workshop.create(_style("Gruk", race="Orc"))
        for name, race in (("Baine", "Tauren"), ("Gnash", "Gnoll"), ("Thrall", "Orc")):
            registry.gamestate.npcs.create(
                Npc(name=name, race=race, scope=Scope(world_id=world.id))
            )
        async with session(mcp_table) as mcp:
            await call(mcp, "set_world_naming_styles", world_id=world.id, keys=[f"own:{orc.id}"])
            peoples = {p["race"]: p for p in await call(mcp, "unserved_peoples", world_id=world.id)}
            assert {"Tauren", "Gnoll"} <= set(peoples) and "Orc" not in peoples
            assert (
                peoples["Tauren"]["met"] == ["Tauren"] and not peoples["Tauren"]["drafts_waiting"]
            )
            await call(
                mcp,
                "draft_naming_style",
                name="Tauren names",
                race="Tauren",
                pattern="{given}",
                parts={"given": {"kind": "list", "names": ["Cairne", "Hamuul"]}},
                world_id=world.id,
            )
            peoples = {p["race"]: p for p in await call(mcp, "unserved_peoples", world_id=world.id)}
            assert peoples["Tauren"]["drafts_waiting"] == ["Tauren names"]  # not yet accepted
            assert peoples["Gnoll"]["drafts_waiting"] == []

    def test_a_world_using_every_style_stays_so(self, tmp_path: Path) -> None:
        registry = StoreRegistry(
            Settings(library_dir=tmp_path / "lib", reference_cache_dir=tmp_path / "c")
        )
        registry.load()
        world = registry.gamestate.worlds.create(World(name="Faerûn"))
        draft = registry.naming_workshop.propose(_style("Ana"), world_id=world.id)
        registry.naming_workshop.accept(draft.id)
        assert registry.worlds.get(world.id).naming_styles == []


class TestTheWorkshopInAWorld:
    async def test_only_the_worlds_styles_and_its_unnamed_peoples(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, ids = table
        key = ids.split("|")[0]
        registry.gamestate.npcs.create(
            Npc(name="Baine", race="Tauren", scope=Scope(world_id=world.id))
        )
        registry.gamestate.npcs.create(Npc(name="Gul", race="Orc", scope=Scope(world_id=world.id)))
        await ac.post(f"/gm/worlds/{world.id}/names", data={"style": [key]})
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "1 style chosen" in page and f'href="/gm/worlds/{world.id}/names"' in page
        assert "/gm/names/new?race=Tauren" in page and "/gm/names/new?race=Orc" not in page
        assert f"race=Orc&amp;world={world.id}" in page
        orcs = (await ac.get(f"/gm/names?race=Orc&world={world.id}")).text
        assert "Thrall" in orcs and "Gorbag" not in orcs
        everywhere = (await ac.get("/gm/names?race=Orc")).text
        assert "Gorbag" in everywhere and "Tauren" not in everywhere


class TestOnePeopleTwoNames:
    async def test_a_german_world_joins_dwarf_and_zwerg_and_offers_the_alias(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, _ids = table
        registry.gamestate.worlds.update(world.id, language="de")
        glossary = registry.settings.i18n_dir / "de" / "_glossary.json"
        glossary.parent.mkdir(parents=True, exist_ok=True)
        glossary.write_text('{"race": {"Dwarf": "Zwerg", "Gnome": "Gnom"}}', encoding="utf-8")
        for name, race in (("Brann", "Dwarf"), ("Gelbin", "Gnom"), ("Fizz", "Gnome")):
            registry.gamestate.npcs.create(
                Npc(name=name, race=race, scope=Scope(world_id=world.id))
            )
        naming = registry.naming_for(world.id)
        peoples = {
            p.race: p
            for p in registry.naming_workshop.unserved(
                world.id, naming, race_names={"Dwarf": "Zwerg", "Gnome": "Gnom"}
            )
        }
        assert peoples["Gnom"].aliases == ["Gnome"] and "Gnome" not in peoples
        assert peoples["Zwerg"].aliases == ["Dwarf"] and peoples["Zwerg"].alias_style is None
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "/gm/names/new?race=Gnom&amp;aliases=Gnome" in page and "also Gnome" in page
        # A style of the table's serves Zwerg: the dwarf of the books is offered as its alias.
        zwerg = registry.naming_workshop.create(_style("Thorgar", race="Zwerg"))
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert f"/gm/names/{zwerg.id}/alias" in page and "Add “Dwarf” to Thorgar" in page
        answer = await ac.post(
            f"/gm/names/{zwerg.id}/alias", data={"alias": "Dwarf", "world": world.id}
        )
        assert answer.headers["location"] == f"/gm/names?world={world.id}"
        assert registry.naming_workshop.get(zwerg.id).aliases == ["Dwarf"]
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "Zwerg" not in page.split('id="naming-world"')[1].split("</section>")[0]


class TestClaudeDraftsFromTheWorkshop:
    async def test_a_draft_is_researched_revised_and_waits_for_the_world(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, _ids = table
        registry.gamestate.npcs.create(
            Npc(name="Baine", race="Tauren", scope=Scope(world_id=world.id))
        )
        asked: list[str] = []
        answers: list[tuple[str, bool]] = []
        release = asyncio.Event()

        async def claude(system: str, prompt: str, submit: Submit) -> None:
            asked.append(prompt)
            assert "web_search" in system and '"kind": "learned"' in system
            await release.wait()
            answers.append(submit({"name": "Tauren", "pattern": "{given}", "parts": {}}))
            answers.append(
                submit(
                    {
                        "name": "Tauren",
                        "race": "Tauren",
                        "pattern": "{given}",
                        "parts": {"given": {"kind": "list", "names": ["Hamuul", "Cairne"]}},
                        "sources": ["general knowledge, not researched"],
                    }
                )
            )

        drafts = StyleDrafts(registry.naming_workshop, registry.naming, claude)
        registry.__dict__["style_drafts"] = drafts
        await ac.post(f"/gm/worlds/{world.id}/names", data={"style": [_ids.split("|")[0]]})
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "Draft with Claude" in page
        started = await ac.post("/gm/names/claude", data={"world": world.id, "race": "Tauren"})
        assert started.headers["location"] == f"/gm/names?world={world.id}"
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "Claude is researching" in page and 'hx-trigger="every 4s"' in page
        release.set()
        run = drafts.runs(world.id)["tauren"]
        assert run._task is not None
        await run._task
        assert "'Tauren'" in asked[0] and "Baine" in asked[0] and "Thrall" in asked[0]
        assert answers[0][1] and answers[0][0].startswith("Not saved")
        assert not answers[1][1] and "Hamuul" in answers[1][0]
        assert run.style_id is not None and not run.error
        draft = registry.naming_workshop.get(run.style_id)
        assert draft.draft and draft.for_world == world.id
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "review it" in page and "every 4s" not in page
        registry.naming_workshop.accept(draft.id)
        assert f"own:{draft.id}" in registry.worlds.get(world.id).naming_styles

    async def test_without_a_key_nothing_is_offered(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, _ids = table
        registry.gamestate.npcs.create(
            Npc(name="Baine", race="Tauren", scope=Scope(world_id=world.id))
        )
        assert not registry.style_drafts.available
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "Tauren" in page and "Draft with Claude" not in page


class TestDraftAllFromTheWorkshop:
    async def test_every_unserved_people_is_drafted_three_at_a_time(
        self, table: tuple[AsyncClient, StoreRegistry, World, str]
    ) -> None:
        ac, registry, world, ids = table
        peoples = ("Tauren", "Gnoll", "Kobold", "Murloc", "Naga")
        for n, race in enumerate(peoples):
            registry.gamestate.npcs.create(
                Npc(name=f"Someone {n}", race=race, scope=Scope(world_id=world.id))
            )
        await ac.post(f"/gm/worlds/{world.id}/names", data={"style": [ids.split("|")[0]]})
        asking: list[str] = []
        release = asyncio.Event()

        async def claude(system: str, prompt: str, submit: Submit) -> None:
            race = re.search(r"`race` '([^']+)'", prompt)
            assert race is not None
            asking.append(race.group(1))
            await release.wait()
            submit(
                {
                    "name": race.group(1),
                    "race": race.group(1),
                    "pattern": "{given}",
                    "parts": {"given": {"kind": "list", "names": ["Ana", "Bor"]}},
                    "sources": ["general knowledge, not researched"],
                }
            )

        drafts = StyleDrafts(registry.naming_workshop, registry.naming, claude)
        registry.__dict__["style_drafts"] = drafts
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert "Draft all 5 with Claude" in page
        started = await ac.post("/gm/names/claude/all", data={"world": world.id})
        assert started.headers["location"] == f"/gm/names?world={world.id}"
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(asking) == 3  # the rest wait for a slot
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert page.count("Claude is researching") == 3
        assert page.count("Waiting for its turn") == 2 and "Draft all" not in page
        release.set()
        runs = drafts.runs(world.id)
        await asyncio.gather(*(run._task for run in runs.values() if run._task is not None))
        assert sorted(asking) == sorted(peoples)
        assert all(run.style_id and not run.error for run in runs.values())
        page = (await ac.get(f"/gm/names?world={world.id}")).text
        assert page.count("review it") == 5 and "Draft all" not in page
        # Asked again, nothing already drafted is asked for twice.
        await ac.post("/gm/names/claude/all", data={"world": world.id})
        assert len(asking) == 5
