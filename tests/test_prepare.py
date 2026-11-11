"""Preparing spells (documentation/CHARGEN-WIZARD.md §10) — against the real corpus."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import PlayerCharacter
from app.services.character import prepared_spells
from app.services.chargen import Catalogue
from app.services.chargen.prepare import PrepareError, preparation
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables
from app.store.registry import StoreRegistry

EXAMPLES = Path(__file__).resolve().parent.parent / "documentation" / "examples"
BOOKS_2014 = ["PHB", "MM", "DMG", "XGE", "TCE"]
PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


def _example(name: str) -> PlayerCharacter:
    data = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    return PlayerCharacter.model_validate(data)


class TestFormula:
    def test_half_a_paladins_level_rounds_down(self) -> None:
        formula = "<$level$> / 2 + <$cha_mod$>"
        assert prepared_spells(formula, 5, {"cha": 3}) == 2 + 3
        assert prepared_spells(formula, 1, {"cha": -1}) == 1  # at least one

    def test_the_full_casters_formula(self) -> None:
        assert prepared_spells("<$level$> + <$wis_mod$>", 10, {"wis": 4}) == 14

    def test_what_cannot_be_read_is_no_number(self) -> None:
        assert prepared_spells("<$spooky$> + 1", 3, {}) is None
        assert prepared_spells("2 +", 3, {}) is None
        assert prepared_spells("<$level$> / 0", 3, {}) is None


class TestSheet:
    def test_oath_spells_are_always_prepared_and_not_counted(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        sheet = build_sheet(
            _example("brunhild-ironvow"),
            loaded_compendium,
            source_set=SourceSet(sources=BOOKS_2014),
        )
        [paladin] = sheet.preparing
        assert (paladin.class_name, paladin.limit, paladin.from_book) == ("Paladin", 8, False)
        always = {s.name for s in sheet.spells if s.always}
        assert always == {
            "Protection from Evil and Good",
            "Sanctuary",
            "Lesser Restoration",
            "Zone of Truth",
        }
        assert sheet.prepared_count == 8  # twelve in the file, four of them the oath's

    def test_a_known_spells_caster_prepares_nothing(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        pc = PlayerCharacter.model_validate(
            {"classes": [{"class": "Sorcerer|PHB", "level": 5}], "abilities": {"cha": 16}}
        )
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=BOOKS_2014))
        assert sheet.preparing == []


class TestPool:
    def test_a_wizard_prepares_from_the_book(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        lyra = _example("lyra-mooncaller")
        prep = preparation(
            lyra, Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups)
        )
        assert prep.from_book and prep.limit == 10 + 5
        book = {o.name.lower() for o in lyra.spells.known}
        assert {o.name.lower() for o in prep.pool} <= book
        assert len(prep.chosen) == len(lyra.spells.prepared)

    def test_a_paladin_prepares_from_the_class_list(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        prep = preparation(
            _example("brunhild-ironvow"),
            Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups),
        )
        assert not prep.from_book
        assert {o.data["level"] for o in prep.pool} == {1, 2}  # Paladin 8 casts up to 2nd
        names = {o.name for o in prep.pool}
        assert "Sanctuary" not in names  # always prepared: not a choice
        assert "Find Steed" in names

    def test_a_wizard_cleric_prepares_twice(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        pc = PlayerCharacter.model_validate(
            {
                "classes": [
                    {"class": "Wizard|PHB", "level": 3},
                    {"class": "Cleric|PHB", "level": 1, "subclass": "Knowledge Domain|PHB"},
                ],
                "abilities": {"int": 16, "wis": 14},
                "spells": {"known": ["Magic Missile|PHB", "Shield|PHB", "Sleep|PHB"]},
            }
        )
        prep = preparation(pc, Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups))
        wizard, cleric = prep.classes
        assert (wizard.class_name, wizard.from_book, wizard.limit) == ("Wizard", True, 3 + 3)
        assert {o.name for o in wizard.pool} == {"Magic Missile", "Shield", "Sleep"}
        assert (cleric.class_name, cleric.from_book, cleric.limit) == ("Cleric", False, 1 + 2)
        assert "Bless" in {o.name for o in cleric.pool}
        assert "Command" not in {o.name for o in cleric.pool}  # the Knowledge Domain's own

    def test_nothing_to_prepare(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        pc = PlayerCharacter.model_validate({"classes": [{"class": "Fighter|PHB", "level": 5}]})
        with pytest.raises(PrepareError):
            preparation(pc, Catalogue(loaded_compendium, SourceSet(sources=BOOKS_2014), lookups))


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="Faerûn", source_set=SourceSet(sources=BOOKS_2014))
    )
    registry.gamestate.characters.create(
        PlayerCharacter.model_validate(
            {
                "id": "mira",
                "name": "Mira",
                "player": "alice",
                "world_id": world.id,
                "status": "ready",
                "classes": [{"class": "Cleric|PHB", "level": 3, "subclass": "Life Domain|PHB"}],
                "abilities": {"str": 10, "dex": 10, "con": 14, "int": 10, "wis": 16, "cha": 12},
                "spells": {"cantrips": ["Sacred Flame|PHB"], "prepared": ["Guiding Bolt|PHB"]},
            }
        )
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry


async def _login(ac: AsyncClient, user: str) -> None:
    await ac.post("/login", data={"username": user, "password": PASSWORD})


class TestPages:
    async def test_the_player_prepares_from_the_sheet(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "alice")
        sheet = (await ac.get("/characters/mira?tab=spells")).text
        assert "1 of 6 prepared" in sheet  # Cleric 3 + WIS +3
        assert "/characters/mira/prepare" in sheet
        page = (await ac.get("/characters/mira/prepare")).text
        assert "Up to 6 spells from your class&#39;s list." in page
        assert "Always prepared" in page and "Spiritual Weapon" in page  # the Life Domain's

        url = "/characters/mira/prepare"
        chosen = ["Guiding Bolt|PHB", "Shield of Faith|PHB", "Aid|PHB"]
        await ac.post(url, data={"prepared.Cleric": chosen})
        pc = registry.gamestate.characters.get("mira")
        assert [s.name for s in pc.spells.prepared] == ["Guiding Bolt", "Shield of Faith", "Aid"]
        done = await ac.post(url, data={"prepared.Cleric": chosen, "go": "done"})
        assert done.headers["location"] == "/characters/mira"
        assert "3 of 6 prepared" in (await ac.get("/characters/mira?tab=spells")).text

    async def test_too_many_or_off_the_list_is_refused(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "alice")
        url = "/characters/mira/prepare"
        many = [
            "Guiding Bolt|PHB",
            "Command|PHB",
            "Detect Magic|PHB",
            "Healing Word|PHB",
            "Sanctuary|PHB",
            "Shield of Faith|PHB",
            "Aid|PHB",
            "Prayer of Healing|PHB",
        ]
        refused = await ac.post(url, data={"prepared.Cleric": many})
        assert "At most 6 spells" in refused.text
        off = await ac.post(url, data={"prepared.Cleric": ["Fireball|PHB"]})
        assert "cannot be prepared" in off.text
        assert [s.name for s in registry.gamestate.characters.get("mira").spells.prepared] == [
            "Guiding Bolt"
        ]

    async def test_a_fighter_has_no_prepare_page(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "id": "bram",
                    "name": "Bram",
                    "player": "alice",
                    "status": "ready",
                    "classes": [{"class": "Fighter|PHB", "level": 2}],
                }
            )
        )
        await _login(ac, "alice")
        got = await ac.get("/characters/bram/prepare")
        assert got.headers["location"] == "/characters/bram"

    async def test_a_wizard_cleric_saves_each_class(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "id": "nell",
                    "name": "Nell",
                    "player": "alice",
                    "status": "ready",
                    "classes": [
                        {"class": "Wizard|PHB", "level": 3},
                        {"class": "Cleric|PHB", "level": 1},
                    ],
                    "abilities": {"int": 16, "wis": 14},
                    "spells": {"known": ["Magic Missile|PHB", "Shield|PHB", "Sleep|PHB"]},
                }
            )
        )
        await _login(ac, "alice")
        page = (await ac.get("/characters/nell/prepare")).text
        assert "Prepared spells · Wizard" in page and "Prepared spells · Cleric" in page
        await ac.post(
            "/characters/nell/prepare",
            data={"prepared.Wizard": ["Shield|PHB"], "prepared.Cleric": ["Bless|PHB"]},
        )
        pc = registry.gamestate.characters.get("nell")
        assert {s.name for s in pc.spells.prepared} == {"Shield", "Bless"}
        assert pc.choices["prepared.Cleric"] == ["Bless|PHB"]
        refused = await ac.post("/characters/nell/prepare", data={"prepared.Wizard": ["Bless|PHB"]})
        assert "cannot be prepared" in refused.text  # not in the book
