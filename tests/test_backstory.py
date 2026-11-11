"""Rolling a backstory in the wizard: *Xanathar's* "This Is Your Life" tables for the
class and background chosen (app/services/chargen/backstory.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.services.auth import Identity
from app.services.chargen.backstory import Physique, roll_physique
from app.services.chargen.drafts import DraftError
from app.store.npc_tables import NpcTables
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


class TestTables:
    def test_class_and_background_tables(self, corpus: Path) -> None:
        tables = NpcTables(corpus)
        barbarian = tables.life_class("Barbarian")
        assert [t.caption for t in barbarian] == ["", "Personal Totems", "Tattoo", "Superstitions"]
        assert barbarian[0].faces == 6
        assert tables.life_background("Acolyte")[0].faces == 6
        # "Guild Artisan (Guild Merchant)" answers to the background's name
        assert tables.life_background("Guild Artisan")
        assert tables.life_class("Artificer") == []


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    registry.gamestate.worlds.create(
        World(name="With XGE", source_set=SourceSet(sources=["XPHB", "XGE"]))
    )
    registry.gamestate.worlds.create(World(name="Core", source_set=SourceSet(sources=["XPHB"])))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        yield ac, registry


def _alice(registry: StoreRegistry) -> Identity:
    user = next(u for u in registry.gamestate.users.list() if u.username == "alice")
    return Identity(user=user, session=None)  # type: ignore[arg-type]


def _draft(registry: StoreRegistry, world: str) -> str:
    alice = _alice(registry)
    found = next(w for w in registry.gamestate.worlds.list() if w.name == world)
    drafts = registry.drafts
    draft = drafts.start(alice, found.id)
    drafts.save(alice, draft.id, "class", {"class": ["Fighter|XPHB"]})
    drafts.save(alice, draft.id, "background", {"background": ["Soldier|XPHB"]})
    return draft.id


class TestWizard:
    async def test_a_roll_keeps_what_was_typed_and_adds_a_line(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        draft = _draft(registry, "With XGE")
        page = (await ac.get(f"/chargen/{draft}/details")).text
        assert "Roll your backstory" in page and "Why Fighter?" in page
        assert "Heraldic Sign" in page and "Why Soldier?" in page

        response = await ac.post(
            f"/chargen/{draft}/details/backstory",
            data={"name": "Bryn", "backstory": "Raised by wolves.", "table": "class:"},
        )
        assert response.status_code == 303
        pc = registry.characters.get_for(_alice(registry), draft)
        assert pc.name == "Bryn"
        first, line = pc.backstory.split("\n\n")
        assert first == "Raised by wolves."
        reasons = registry.npc_tables.life_class("Fighter")[0]
        assert line in {r.cells[0] for r in reasons.rows}

        await ac.post(
            f"/chargen/{draft}/details/backstory",
            data={"backstory": pc.backstory, "table": "all"},
        )
        pc = registry.characters.get_for(_alice(registry), draft)
        lines = pc.backstory.split("\n\n")
        assert len(lines) == 2 + 4 + 1  # typed, the class line, then 4 fighter + 1 soldier
        assert lines[3].startswith("Heraldic Sign: ")

    async def test_a_2014_background_rolls_its_characteristics_without_xanathars(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        alice = _alice(registry)
        registry.gamestate.worlds.create(World(name="2014", source_set=SourceSet(sources=["PHB"])))
        found = next(w for w in registry.gamestate.worlds.list() if w.name == "2014")
        draft = registry.drafts.start(alice, found.id).id
        registry.drafts.save(alice, draft, "class", {"class": ["Fighter|PHB"]})
        registry.drafts.save(alice, draft, "background", {"background": ["Acolyte|PHB"]})
        page = (await ac.get(f"/chargen/{draft}/details")).text
        assert "Personality trait" in page and "Flaw" in page and "Why Fighter?" not in page

        await ac.post(
            f"/chargen/{draft}/details/backstory",
            data={"backstory": "", "table": "characteristic:flaw"},
        )
        pc = registry.characters.get_for(alice, draft)
        assert pc.backstory.startswith("Flaw: ") and "{@" not in pc.backstory

        pc = registry.drafts.roll_backstory(alice, draft, "all", {"flaw": "Makel"})
        lines = pc.backstory.split("\n\n")[1:]
        assert [line.split(": ")[0] for line in lines] == [
            "personality",
            "personality",
            "ideal",
            "bond",
            "Makel",
        ]
        assert lines[0] != lines[1]

    async def test_rolled_characteristics_read_in_the_translation(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        alice = _alice(registry)
        registry.gamestate.worlds.create(World(name="2014", source_set=SourceSet(sources=["PHB"])))
        found = next(w for w in registry.gamestate.worlds.list() if w.name == "2014")
        draft = registry.drafts.start(alice, found.id).id
        registry.drafts.save(alice, draft, "class", {"class": ["Fighter|PHB"]})
        registry.drafts.save(alice, draft, "background", {"background": ["Acolyte|PHB"]})
        planner = registry.drafts.planner(registry.characters.get_for(alice, draft))
        record = planner.catalogue.find("background", "Acolyte|PHB")
        assert record is not None
        translations = registry.translations
        rows = translations.rows("de", "background", "Acolyte|PHB", dict(record.data))
        flaw = next(r for r in rows if r.english.startswith("I judge others"))
        table = flaw.path.split("/rows/")[0]
        passages = {f"{table}/rows/{n}/1": "Hart im Urteil." for n in range(6)}
        translations.save("de", "background", "Acolyte|PHB", dict(record.data), passages, by="dm")
        await ac.post("/language", data={"lang": "de"})
        await ac.post(
            f"/chargen/{draft}/details/backstory",
            data={"backstory": "", "table": "characteristic:flaw"},
        )
        pc = registry.characters.get_for(alice, draft)
        assert pc.backstory == "Makel: Hart im Urteil."

    async def test_height_and_weight_join_the_appearance(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        alice = _alice(registry)
        registry.gamestate.worlds.create(World(name="2014", source_set=SourceSet(sources=["PHB"])))
        found = next(w for w in registry.gamestate.worlds.list() if w.name == "2014")
        draft = registry.drafts.start(alice, found.id).id
        registry.drafts.save(alice, draft, "class", {"class": ["Fighter|PHB"]})
        registry.drafts.save(alice, draft, "species", {"species": ["Human|PHB"]})
        assert "Roll height and weight" in (await ac.get(f"/chargen/{draft}/details")).text
        await ac.post(
            f"/chargen/{draft}/details/physique",
            data={"appearance": "A scar."},
        )
        pc = registry.characters.get_for(alice, draft)
        first, line = pc.appearance.split("\n")
        assert first == "A scar." and line.startswith("Height ") and " lb." in line

    def test_a_physique_is_the_table_s_sum(self) -> None:
        class Fixed:
            def roll(self, expression: str) -> Any:
                return SimpleNamespace(total=10 if expression == "2d10" else 3)

        table = {"baseHeight": 56, "heightMod": "2d10", "baseWeight": 110, "weightMod": "2d4"}
        assert roll_physique(table, Fixed()) == Physique(66, 140)  # type: ignore[arg-type]
        assert roll_physique({"baseHeight": 60, "baseWeight": 100}, Fixed()) == Physique(60, 100)  # type: ignore[arg-type]

    async def test_not_offered_without_xanathars(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        draft = _draft(registry, "Core")
        assert "Roll your backstory" not in (await ac.get(f"/chargen/{draft}/details")).text
        with pytest.raises(DraftError):
            registry.drafts.roll_backstory(_alice(registry), draft, "all")
