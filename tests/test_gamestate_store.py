"""Tests for the game-state CRUD store."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.gamestate import Combat, Combatant, Role, Session, User, World
from app.store.errors import DuplicateEntry, EntryNotFound, InvalidEntry
from app.store.gamestate import GameStateStore
from app.store.jsonfile import read_json


class TestCrud:
    def test_create_and_get(self, gamestate: GameStateStore) -> None:
        world = gamestate.users.create(User(username="Faerûn"))
        assert gamestate.users.get(world.id).username == "Faerûn"

    def test_create_persists_to_disk(self, gamestate: GameStateStore) -> None:
        world = gamestate.users.create(User(username="Eberron"))
        raw = read_json(gamestate.users.path)
        assert raw[world.id]["username"] == "Eberron"

    def test_create_rejects_duplicate_id(self, gamestate: GameStateStore) -> None:
        world = gamestate.users.create(User(username="A"))
        with pytest.raises(DuplicateEntry):
            gamestate.users.create(User(id=world.id, username="B"))

    def test_get_missing_raises(self, gamestate: GameStateStore) -> None:
        with pytest.raises(EntryNotFound):
            gamestate.users.get("nope")

    def test_find_missing_returns_none(self, gamestate: GameStateStore) -> None:
        assert gamestate.users.find("nope") is None

    def test_update_changes_field_and_touches_timestamp(self, gamestate: GameStateStore) -> None:
        world = gamestate.users.create(User(username="Old"))
        original = world.updated_at
        updated = gamestate.users.update(world.id, username="New")
        assert updated.username == "New"
        assert updated.updated_at >= original

    def test_update_validates_before_writing(self, gamestate: GameStateStore) -> None:
        """An invalid change must be rejected rather than persisted."""
        world = gamestate.users.create(User(username="Valid"))
        with pytest.raises(InvalidEntry):
            gamestate.users.update(world.id, username=["not", "a", "string"])
        assert gamestate.users.get(world.id).username == "Valid"

    def test_update_missing_raises(self, gamestate: GameStateStore) -> None:
        with pytest.raises(EntryNotFound):
            gamestate.users.update("nope", username="X")

    def test_delete_removes_from_memory_and_disk(self, gamestate: GameStateStore) -> None:
        world = gamestate.users.create(User(username="Gone"))
        gamestate.users.delete(world.id)
        assert world.id not in gamestate.users
        assert read_json(gamestate.users.path) == {}

    def test_delete_missing_raises(self, gamestate: GameStateStore) -> None:
        with pytest.raises(EntryNotFound):
            gamestate.users.delete("nope")

    def test_upsert_inserts_then_overwrites(self, gamestate: GameStateStore) -> None:
        world = User(username="First")
        gamestate.users.upsert(world)
        world.username = "Second"
        gamestate.users.upsert(world)
        assert len(gamestate.users) == 1
        assert gamestate.users.get(world.id).username == "Second"

    def test_replace_requires_existing(self, gamestate: GameStateStore) -> None:
        with pytest.raises(EntryNotFound):
            gamestate.users.replace(User(username="Ghost"))

    def test_list_with_predicate(self, gamestate: GameStateStore) -> None:
        gamestate.users.create(User(username="A", language="en"))
        gamestate.users.create(User(username="B", language="de"))
        german = gamestate.users.list(lambda w: w.language == "de")
        assert [w.username for w in german] == ["B"]

    def test_clear_empties_collection(self, gamestate: GameStateStore) -> None:
        gamestate.users.create(User(username="A"))
        gamestate.users.clear()
        assert len(gamestate.users) == 0


class TestPersistence:
    def test_data_survives_a_new_store_instance(self, tmp_path: Path) -> None:
        """A restart must not lose state — the whole point of writing on mutation."""
        root = tmp_path / "gs"
        first = GameStateStore(root)
        world = first.users.create(User(username="Persisted"))

        second = GameStateStore(root)
        assert second.users.get(world.id).username == "Persisted"

    def test_invalid_record_on_disk_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """One corrupt record must not make the rest of the collection unreachable."""
        root = tmp_path / "gs"
        store = GameStateStore(root)
        good = store.users.create(User(username="Good"))

        raw = read_json(store.users.path)
        raw["broken"] = {"id": "broken", "nonsense": True}
        from app.store.jsonfile import write_json

        write_json(store.users.path, raw)

        reloaded = GameStateStore(root)
        assert len(reloaded.users) == 1
        assert reloaded.users.get(good.id).username == "Good"

    def test_missing_file_loads_as_empty(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path / "fresh")
        assert store.users.list() == []


class TestConcurrency:
    def test_concurrent_mutations_all_land(self, gamestate: GameStateStore) -> None:
        """Ten threads damaging one combatant must apply all ten hits.

        Through get/replace this would lose updates; ``mutate`` holds the lock across
        the read-modify-write cycle.
        """
        combat = gamestate.combats.create(
            Combat(name="Brawl", combatants=[Combatant(label="Ogre", hp=100, hp_max=100)])
        )

        def strike(c: Combat) -> None:
            c.combatants[0].hp -= 1

        def hit() -> None:
            for _ in range(10):
                gamestate.combats.mutate(combat.id, strike)

        threads = [threading.Thread(target=hit) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert gamestate.combats.get(combat.id).combatants[0].hp == 0

    def test_concurrent_creates_all_persist(self, gamestate: GameStateStore) -> None:
        def make(n: int) -> None:
            gamestate.users.create(User(username=f"u{n}"))

        threads = [threading.Thread(target=make, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(gamestate.users) == 20
        assert len(read_json(gamestate.users.path)) == 20


class TestUserRecords:
    def test_public_representation_hides_password_hash(self, gamestate: GameStateStore) -> None:
        """A password hash must never reach a client."""
        user = gamestate.users.create(
            User(username="dm", role=Role.GAMEMASTER, password_hash="$2b$secret")
        )
        public = user.public()
        assert "password_hash" not in public
        assert public["username"] == "dm"

    def test_role_round_trips_through_json(self, tmp_path: Path) -> None:
        root = tmp_path / "gs"
        store = GameStateStore(root)
        user = store.users.create(User(username="dm", role=Role.GAMEMASTER))
        assert GameStateStore(root).users.get(user.id).role is Role.GAMEMASTER

    def test_purge_expired_sessions(self, gamestate: GameStateStore) -> None:
        now = datetime.now(UTC)
        gamestate.sessions.create(
            Session(user_id="u", token="live", expires_at=now + timedelta(hours=1))
        )
        dead = gamestate.sessions.create(
            Session(user_id="u", token="dead", expires_at=now - timedelta(hours=1))
        )
        assert dead.is_expired
        assert gamestate.purge_expired_sessions() == 1
        assert len(gamestate.sessions) == 1


class TestUnknownFieldsRejected:
    """Our own schemas are strict, unlike the imported ones."""

    def test_extra_field_is_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            World.model_validate({"name": "X", "typoField": 1})
