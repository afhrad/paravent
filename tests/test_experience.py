"""Experience points and awarding a fight's XP (documentation/COMBAT.md §6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind
from app.services.combat import CombatError
from app.services.experience import award, level_for, progress, start_of, thresholds
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry
from app.store.rulestables import extract
from tests.test_combat import GM, PASSWORD, Table, _encounter


@pytest.fixture
def table(tmp_path: Path, loaded_compendium: CompendiumStore) -> Table:
    return Table(tmp_path / "lib", loaded_compendium)


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


class TestTable:
    def test_the_thresholds_are_the_books(self, corpus: Path) -> None:
        extracted = extract(corpus)["advancement"]
        assert thresholds() == tuple(extracted[str(level)] for level in range(1, 21))
        assert start_of(5) == 6500 and start_of(20) == 355000

    @pytest.mark.parametrize(
        ("xp", "level"), [(0, 1), (299, 1), (300, 2), (6499, 4), (6500, 5), (400000, 20)]
    )
    def test_the_level_xp_reaches(self, xp: int, level: int) -> None:
        assert level_for(xp) == level


def _pc(level: int, experience: int | None, pending: int = 0) -> PlayerCharacter:
    return PlayerCharacter.model_validate(
        {
            "status": "ready",
            "classes": [{"class": "Fighter|PHB", "level": level}],
            "experience": experience,
            "pending_levels": pending,
        }
    )


class TestProgress:
    def test_untracked_is_none(self) -> None:
        assert progress(_pc(11, None)) is None

    def test_a_level_reached_but_not_granted_is_due(self) -> None:
        xp = progress(_pc(4, 6600))
        assert xp is not None and xp.reached == 5 and xp.due == 1
        assert xp.floor == 2700 and xp.next == 6500 and xp.fraction == 1.0
        granted = progress(_pc(4, 6600, pending=1))
        assert granted is not None and granted.due == 0  # the gamemaster already granted it

    def test_the_first_award_starts_at_the_levels_threshold(self) -> None:
        pc = _pc(4, None)
        award(pc, 300)
        assert pc.experience == 2700 + 300
        award(pc, 100)
        assert pc.experience == 3100


class TestAward:
    def test_once_after_the_end(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        with pytest.raises(CombatError):
            table.combats.award(combat.id, 300, GM)  # still running
        for label in ("Goblin 1", "Goblin 2", "Bugbear"):
            combat = table.combats.damage(combat.id, table.named(combat, label), 100, GM)
        combat = table.combats.end(combat.id, GM)
        combat = table.combats.award(combat.id, 300, GM)
        assert combat.awarded_xp == 300 and combat.awarded_at is not None
        assert table.latest(combat).what == "award" and table.latest(combat).amount == 300
        # each character's award is in the log, with the fight's name as its reason
        [given] = [e for e in table.log.entries(table.world.id) if e.kind is EntryKind.EXPERIENCE]
        assert (given.subject, given.amount, given.note) == (
            f"character:{table.lyra.id}",
            300,
            combat.name,
        )
        lyra = table.store.characters.get(table.lyra.id)
        assert lyra.experience == 64000 + 300  # Wizard 10 starts at 64,000
        with pytest.raises(CombatError):
            table.combats.award(combat.id, 300, GM)  # never twice
        assert table.store.characters.get(table.lyra.id).experience == 64300


class TestPages:
    async def test_award_then_grant_from_the_summary(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        combat = registry.combats.start(_encounter(registry), GM)
        lyra_id = next(c.character_id for c in combat.combatants if c.character_id)
        assert lyra_id is not None
        registry.combats.end(combat.id, GM)
        url = f"/gm/combat/{combat.id}"
        page = (await ac.get(url)).text
        assert "Award to 1 character" in page and 'name="xp"' in page

        # Enough to reach the next level: Wizard 10 → 11 at 85,000
        await ac.post(f"{url}/award", data={"xp": "21000"})
        page = (await ac.get(url)).text
        assert "Awarded: 21,000 XP each." in page and "Award to" not in page
        assert "85,000 / 85,000 XP" in page and "Level 11 reached" in page
        assert f'action="/characters/{lyra_id}/grant-level"' in page

        granted = await ac.post(f"/characters/{lyra_id}/grant-level", data={"next": url})
        assert granted.headers["location"] == url
        assert registry.gamestate.characters.get(lyra_id).pending_levels == 1
        assert "Level 11 reached" not in (await ac.get(url)).text  # granted: no longer due

        sheet = (await ac.get(f"/characters/{lyra_id}")).text
        assert "85,000 / 85,000 XP" in sheet

        refused = await ac.post(f"{url}/award", data={"xp": "100"})
        assert "error=combat.error.awarded" in refused.headers["location"]

    async def test_the_party_card_marks_a_level_reached(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        _encounter(registry)
        lyra = next(iter(registry.gamestate.characters.list()))

        def reached(pc: PlayerCharacter) -> None:
            pc.experience = 90000  # Wizard 10, past 85,000

        registry.gamestate.characters.mutate(lyra.id, reached)
        assert "XP reached level 11" in (await ac.get("/gm")).text
