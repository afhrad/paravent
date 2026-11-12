"""Building a fight to a difficulty from the corpus's encounter shapes."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    EDITION_ONE,
    Encounter,
    EncounterMonster,
    PartySpec,
    Role,
    SourceSet,
    World,
)
from app.services.difficulty import DifficultyRules
from app.services.encounter_gen import (
    Candidate,
    EncounterGenerator,
    _count,
    _formula,
    _ratio,
)
from app.store.encounter_tables import EncounterShape, EncounterTables, ShapeGroup
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"

BOSS_WITH_MINIONS = EncounterShape(
    "Boss with Minions",
    "Generic",
    (
        (
            ShapeGroup({"exact": 1}, {"exact": 0.6}),
            ShapeGroup({"formulaMin": "players", "formulaMax": "players * 2"}, None),
        ),
    ),
)
# XP at CR 1/8 … 10, as the 2024 rules count it.
POOL = [
    Candidate(f"Creature {xp}", "ZZ", xp)
    for xp in (25, 50, 100, 200, 450, 700, 1100, 1800, 2300, 2900, 3900, 5000, 5900)
]


class TestTheParts:
    def test_formulas_count_players(self) -> None:
        assert _formula("players * 2", 4) == 8
        assert _formula("players + 3", 4) == 7
        assert _formula("2", 4) == 2
        assert _formula("__import__('os')", 4) == 1  # nothing else is evaluated

    def test_counts_and_ratios(self) -> None:
        rng = random.Random(1)
        assert _count({"exact": 1}, 4, rng) == 1
        assert 1 <= _count({"min": 1, "max": 3}, 4, rng) <= 3
        assert 4 <= _count({"formulaMin": "players", "formulaMax": "players * 2"}, 4, rng) <= 8
        assert _ratio({"exact": {"num": 1, "denom": 3}}, rng) == pytest.approx(1 / 3)
        assert 0.5 <= (_ratio({"min": 0.5, "max": 0.6}, rng) or 0) <= 0.6
        assert _ratio(None, rng) is None


class TestTheGenerator:
    @pytest.mark.parametrize("band", ["low", "moderate", "high"])
    def test_it_lands_on_the_difficulty(self, band: str) -> None:
        generator = EncounterGenerator(DifficultyRules(), random.Random(7))
        made = generator.generate(POOL, [5, 5, 5, 5], EDITION_ONE, band, [BOSS_WITH_MINIONS])
        assert made is not None and made.shape == "Boss with Minions"
        assert made.rating.level == band
        # A boss and its minions: two kinds, the minions four to eight.
        assert len(made.monsters) == 2 and 4 <= made.monsters[1][2] <= 8
        boss, minions = made.monsters
        assert boss[2] == 1 and boss[0] != minions[0]

    def test_nothing_to_build_from(self) -> None:
        generator = EncounterGenerator(DifficultyRules())
        shapes = [BOSS_WITH_MINIONS]
        assert generator.generate([], [5], EDITION_ONE, "low", shapes) is None
        assert generator.generate(POOL, [], EDITION_ONE, "low", shapes) is None
        assert generator.generate(POOL, [5], EDITION_ONE, "deadly", shapes) is None
        assert generator.generate(POOL, [5], EDITION_ONE, "low", []) is None


class TestTheCorpus:
    def test_the_five_shapes_are_read(self, corpus: Path) -> None:
        shapes = EncounterTables(corpus).shapes()
        assert [s.name for s in shapes] == ["Boss", "Boss with Minions", "Duo", "Horde", "Trio"]
        horde = shapes[3]
        assert len(horde.templates) == 3 and horde.templates[2][0].ratio == {"exact": 1}


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestTheBuilder:
    async def test_a_built_fight_replaces_the_plan(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Woods", source_set=SourceSet(sources=["XPHB", "XMM"]))
        )
        encounter = registry.gamestate.encounters.create(
            Encounter(
                name="In the woods",
                world_id=world.id,
                party=PartySpec(size=4, level=5),
                monsters=[EncounterMonster(name="Tarrasque", source="MM", count=1)],
            )
        )
        url = f"/gm/encounters/{encounter.id}"
        page = (await ac.get(url)).text
        assert "Build a fight" in page and '<option value="forest">Forest</option>' in page
        answer = await ac.post(
            f"{url}/build", data={"difficulty": "high", "shape": "Trio", "habitat": "forest"}
        )
        assert answer.status_code == 200 and "Built: Trio" in answer.text
        after = registry.gamestate.encounters.get(encounter.id)
        assert after.notes.startswith("Built as Trio for High")
        assert len(after.monsters) == 3 and all(m.source == "XMM" for m in after.monsters)
        # The choices stay chosen for the next one.
        assert '<option value="high" selected>' in answer.text
        assert '<option value="Trio" selected>' in answer.text

    async def test_no_party_no_fight(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        encounter = registry.gamestate.encounters.create(Encounter(name="Alone"))
        answer = await ac.post(f"/gm/encounters/{encounter.id}/build", data={})
        assert answer.status_code == 200 and "A fight is built for a party" in answer.text
