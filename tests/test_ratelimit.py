"""Failed logins slow down (app/services/ratelimit.py)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.ratelimit import FREE, MAX_WAIT, WINDOW, LoginThrottle, login_keys
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestThrottle:
    def test_the_first_failures_are_free_then_the_wait_doubles(self) -> None:
        clock = Clock()
        throttle = LoginThrottle(clock)
        keys = login_keys("Dana", "10.0.0.2")
        for _ in range(FREE):
            throttle.failed(keys)
            assert throttle.wait(keys) == 0
        throttle.failed(keys)
        assert throttle.wait(keys) == 2
        throttle.failed(keys)
        assert throttle.wait(keys) == 4
        clock.now += 4
        assert throttle.wait(keys) == 0

    def test_the_wait_has_a_ceiling(self) -> None:
        throttle = LoginThrottle(Clock())
        for _ in range(FREE + 30):
            throttle.failed(["user:x"])
        assert throttle.wait(["user:x"]) == MAX_WAIT

    def test_the_window_forgets_and_success_resets(self) -> None:
        clock = Clock()
        throttle = LoginThrottle(clock)
        for _ in range(FREE):
            throttle.failed(["user:x"])
        clock.now += WINDOW + 1
        throttle.failed(["user:x"])
        assert throttle.wait(["user:x"]) == 0  # the old failures ran out
        for _ in range(FREE + 2):
            throttle.failed(["user:x"])
        throttle.succeeded(["user:x"])
        assert throttle.wait(["user:x"]) == 0

    def test_keys_are_by_name_and_address(self) -> None:
        assert login_keys(" Dana ", "10.0.0.2") == ["user:dana", "addr:10.0.0.2"]
        assert login_keys("dana", None) == ["user:dana"]


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, Clock]]:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "lib", watch_files=False))
    registry.load()
    registry.auth.create_user("dana", PASSWORD, Role.GAMEMASTER)
    clock = Clock()
    registry.login_throttle.clock = clock
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry, clock


class TestLogin:
    async def test_guessing_is_slowed_and_the_right_password_waits_too(
        self, client: tuple[AsyncClient, StoreRegistry, Clock]
    ) -> None:
        ac, _registry, clock = client
        for _ in range(FREE + 1):
            wrong = await ac.post("/login", data={"username": "dana", "password": "nope"})
            assert wrong.status_code == 401
        refused = await ac.post("/login", data={"username": "dana", "password": PASSWORD})
        assert refused.status_code == 429 and refused.headers["Retry-After"] == "2"
        assert "Try again in 2 seconds" in refused.text
        assert "paravent_session" not in refused.cookies
        clock.now += 2
        allowed = await ac.post("/login", data={"username": "dana", "password": PASSWORD})
        assert allowed.status_code == 303

    async def test_an_unknown_name_is_slowed_the_same_way(
        self, client: tuple[AsyncClient, StoreRegistry, Clock]
    ) -> None:
        ac, _registry, _clock = client
        answers = [
            (await ac.post("/login", data={"username": "nobody", "password": "x"})).status_code
            for _ in range(FREE + 2)
        ]
        assert answers == [401] * (FREE + 1) + [429]  # no hint whether "nobody" exists

    async def test_one_address_trying_many_names(
        self, client: tuple[AsyncClient, StoreRegistry, Clock]
    ) -> None:
        ac, _registry, _clock = client
        for n in range(FREE + 1):
            await ac.post("/login", data={"username": f"guess{n}", "password": "x"})
        refused = await ac.post("/login", data={"username": "dana", "password": PASSWORD})
        assert refused.status_code == 429
