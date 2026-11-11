"""Units as a per-user setting (app/services/units.py, TRANSLATION.md §6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.units import UnitFormat, feet_from, text_in_feet
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


class TestFormat:
    @pytest.mark.parametrize(
        ("feet", "metres"),
        [(5, "1,5 m"), (10, "3 m"), (30, "9 m"), (60, "18 m"), (90, "27 m"), (120, "36 m")],
    )
    def test_the_published_german_values(self, feet: int, metres: str) -> None:
        # 1 ft = 0.3 m, as the German books print it — not 1/3.28 (60 ft would be 18.3 m)
        assert UnitFormat("metres", "de").distance(feet) == metres

    def test_separators_follow_the_language(self) -> None:
        assert UnitFormat("metres", "en").distance(5) == "1.5 m"
        assert UnitFormat("metres", "de").distance(35) == "10,5 m"

    def test_weights(self) -> None:
        assert UnitFormat("metres", "de").weight(3) == "1,5 kg"
        assert UnitFormat("feet", "en").weight(3.0) == "3 lb."
        assert UnitFormat("feet", "de").weight(0.25) == "0,25 lb."

    def test_feet_are_left_as_they_are(self) -> None:
        feet = UnitFormat("feet", "en")
        assert feet.distance(30) == "30 ft."
        assert feet.text("Self (15 ft cone)") == "Self (15 ft cone)"

    def test_structured_text(self) -> None:
        metric = UnitFormat("metres", "de")
        assert metric.text("Self (15 ft cone)") == "Self (4,5 m cone)"
        assert metric.text("{@sense darkvision} 60 ft.") == "{@sense darkvision} 18 m"
        assert metric.text("20/60 ft.") == "6/18 m"
        assert metric.text("1 mi") == "1,5 km"
        assert metric.text("blindsight 30 ft. (blind beyond this radius)") == (
            "blindsight 9 m (blind beyond this radius)"
        )
        assert metric.text("") == ""

    def test_typed_back_into_feet(self) -> None:
        assert feet_from("9", metric=True) == 30 and feet_from("1,5", metric=True) == 5
        assert feet_from("10.5", metric=True) == 35 and feet_from("1", metric=True) == 3
        assert feet_from("30", metric=False) == 30 and feet_from("schnell", True) is None
        assert text_in_feet("darkvision 18 m, blindsight 3 m") == (
            "darkvision 60 ft., blindsight 10 ft."
        )
        assert text_in_feet("tremorsense 1,5 km") == "tremorsense 1 mi."
        assert text_in_feet("darkvision 60 ft.") == "darkvision 60 ft."


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


class TestSetting:
    async def test_the_language_sets_the_units(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        goblin = "/ref/monster/MM/Goblin"
        assert "30 ft." in (await ac.get(goblin)).text
        switched = await ac.post("/language", data={"lang": "de", "next": goblin})
        assert switched.status_code == 303 and switched.headers["location"] == goblin
        assert registry.auth.find_user("dm").units == "metres"  # type: ignore[union-attr]
        page = (await ac.get(goblin)).text
        assert "9 m" in page and "30 ft." not in page
        assert 'value="de" lang="de"' in page and "/units" not in page
        fireball = (await ac.get("/ref/spell/PHB/Fireball")).text
        assert "45 m" in fireball and "150 ft" not in fireball
        # prose keeps its units
        assert "20-foot-radius" in fireball
        await ac.post("/language", data={"lang": "en"})
        assert registry.auth.find_user("dm").units == "feet"  # type: ignore[union-attr]
        assert "30 ft." in (await ac.get(goblin)).text

    async def test_an_unknown_language_changes_nothing(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await ac.post("/language", data={"lang": "xx"})
        user = registry.auth.find_user("dm")
        assert user is not None and (user.language, user.units) == ("en", "feet")
