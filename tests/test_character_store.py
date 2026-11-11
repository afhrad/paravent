"""The one-file-per-character store: writes, hand edits, errors, notifications."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.pc import PlayerCharacter
from app.store.characters import CharacterStore, slugify
from app.store.errors import DuplicateEntry, EntryNotFound, InvalidEntry


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "characters"


@pytest.fixture
def store(root: Path) -> CharacterStore:
    s = CharacterStore(root)
    s.load()
    return s


@pytest.fixture
def events(store: CharacterStore) -> list[tuple[str, str | None]]:
    seen: list[tuple[str, str | None]] = []
    store.subscribe(lambda cid, rev: seen.append((cid, rev)))
    return seen


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)


class TestWriting:
    def test_create_writes_a_named_file(self, store: CharacterStore, root: Path) -> None:
        pc = store.create(PlayerCharacter(name="Lyra Mooncaller"))
        path = root / f"lyra-mooncaller-{pc.id}.json"
        assert path.is_file()
        assert json.loads(path.read_text())["name"] == "Lyra Mooncaller"
        assert "$schema" not in json.loads(path.read_text())

    def test_no_temp_file_is_left(self, store: CharacterStore, root: Path) -> None:
        store.create(PlayerCharacter(name="X"))
        assert [p.name for p in root.iterdir() if p.name.startswith(".")] == []

    def test_create_refuses_a_duplicate(self, store: CharacterStore) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        with pytest.raises(DuplicateEntry):
            store.create(pc)

    def test_update_validates_first(self, store: CharacterStore) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        with pytest.raises(InvalidEntry):
            store.update(pc.id, overrides={"armour": 3})
        assert store.get(pc.id).overrides == {}

    def test_mutate_writes_through_a_copy(self, store: CharacterStore) -> None:
        pc = store.create(PlayerCharacter(name="X"))

        def breaks(c: PlayerCharacter) -> None:
            c.state.hp = 5
            raise RuntimeError("refused")

        with pytest.raises(RuntimeError):
            store.mutate(pc.id, breaks)
        assert store.get(pc.id).state.hp is None

    def test_mutate_validates_the_result(self, store: CharacterStore) -> None:
        pc = store.create(PlayerCharacter(name="X"))

        def invalid(c: PlayerCharacter) -> None:
            c.state.slots_used = {"12": 1}

        with pytest.raises(InvalidEntry):
            store.mutate(pc.id, invalid)

    def test_writes_keep_the_original_filename(self, store: CharacterStore) -> None:
        pc = store.create(PlayerCharacter(name="Old Name"))
        before = store.path(pc.id)
        store.update(pc.id, name="New Name")
        assert store.path(pc.id) == before

    def test_delete_removes_the_file(
        self, store: CharacterStore, events: list[tuple[str, str | None]]
    ) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        path = store.path(pc.id)
        store.delete(pc.id)
        assert path is not None and not path.exists()
        assert events[-1] == (pc.id, None)
        with pytest.raises(EntryNotFound):
            store.get(pc.id)


class TestNotifications:
    def test_every_change_announces_its_revision(
        self, store: CharacterStore, events: list[tuple[str, str | None]]
    ) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        store.update(pc.id, notes="a")
        assert [cid for cid, _ in events] == [pc.id, pc.id]
        assert events[-1][1] == store.revision(pc.id)
        assert events[0][1] != events[1][1]

    def test_a_listener_failure_does_not_break_a_write(self, store: CharacterStore) -> None:
        def boom(_cid: str, _rev: str | None) -> None:
            raise RuntimeError

        store.subscribe(boom)
        store.create(PlayerCharacter(name="X"))


class TestHandEdits:
    def test_a_dropped_file_without_id_takes_its_filename(self, root: Path) -> None:
        _write(root / "bob.json", {"name": "Bob"})
        store = CharacterStore(root)
        assert store.get("bob").name == "Bob"

    def test_hand_edit_is_picked_up_and_announced(
        self, store: CharacterStore, events: list[tuple[str, str | None]]
    ) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        path = store.path(pc.id)
        assert path is not None
        data = json.loads(path.read_text())
        data["state"]["hp"] = 3
        _write(path, data)
        store.reload_paths({path})
        assert store.get(pc.id).state.hp == 3
        assert events[-1] == (pc.id, store.revision(pc.id))

    def test_an_unchanged_reload_announces_nothing(
        self, store: CharacterStore, events: list[tuple[str, str | None]]
    ) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        count = len(events)
        store.reload_paths({store.path(pc.id)})  # type: ignore[arg-type]
        assert len(events) == count

    def test_a_broken_edit_keeps_the_last_good_version(
        self, store: CharacterStore, events: list[tuple[str, str | None]]
    ) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        path = store.path(pc.id)
        assert path is not None
        _write(path, '{"name": "X", ')
        store.reload_paths({path})
        assert store.get(pc.id).name == "X"
        error = store.error(pc.id)
        assert error is not None and "Not valid JSON" in error and "line 1" in error
        # Fixing the file clears the error and announces the fixed version.
        data = PlayerCharacter(id=pc.id, name="Fixed").model_dump(mode="json")
        _write(path, data)
        store.reload_paths({path})
        assert store.error(pc.id) is None
        assert store.get(pc.id).name == "Fixed"

    def test_a_validation_error_names_the_field(self, store: CharacterStore) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        path = store.path(pc.id)
        assert path is not None
        data = json.loads(path.read_text())
        data["state"]["hp"] = "lots"
        _write(path, data)
        store.reload_paths({path})
        assert "state.hp" in (store.error(pc.id) or "")

    def test_a_deleted_file_drops_the_character(
        self, store: CharacterStore, events: list[tuple[str, str | None]]
    ) -> None:
        pc = store.create(PlayerCharacter(name="X"))
        path = store.path(pc.id)
        assert path is not None
        path.unlink()
        store.reload_paths({path})
        assert store.find(pc.id) is None
        assert events[-1] == (pc.id, None)

    def test_duplicate_ids_are_reported_not_merged(self, root: Path) -> None:
        _write(root / "a.json", {"id": "same", "name": "A"})
        _write(root / "b.json", {"id": "same", "name": "B"})
        store = CharacterStore(root)
        assert store.get("same").name == "A"
        assert "Duplicate id" in store.errors["b"]

    def test_an_unreadable_new_file_is_reported(self, store: CharacterStore, root: Path) -> None:
        _write(root / "broken.json", "nope")
        store.reload_paths({root / "broken.json"})
        assert "broken" in store.errors


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Lyra Mooncaller", "lyra-mooncaller"),
        ("Ærin  the Bold!", "rin-the-bold"),
        ("", "character"),
    ],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


class TestRelativeLibrary:
    """The library may be configured relative while the watcher reports absolute paths."""

    def test_an_edit_reported_absolute_is_not_a_duplicate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        store = CharacterStore(Path("characters"))
        pc = store.create(PlayerCharacter(name="Brunhild"))
        path = store.path(pc.id)
        assert path is not None and path.is_absolute()
        data = json.loads(path.read_text())
        data["state"]["hp"] = 70
        _write(path, data)
        store.reload_paths({Path("characters") / path.name})  # relative form
        store.reload_paths({path})  # absolute form, as the watcher sends it
        assert store.error(pc.id) is None
        assert store.errors == {}
        assert store.get(pc.id).state.hp == 70


class TestConsoleReports:
    """The server console says where a hand edit went wrong."""

    @pytest.fixture
    def file(self, store: CharacterStore) -> Path:
        pc = store.create(
            PlayerCharacter(name="X", hit_points={"method": "rolled", "per_level": [8]})
        )
        path = store.path(pc.id)
        assert path is not None
        return path

    def test_invalid_json_shows_line_and_caret(
        self, store: CharacterStore, file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(file, file.read_text().replace('"name": "X",', '"name": "X"'))
        store.reload_paths({file})
        report = caplog.text
        assert str(file) in report
        assert "Expecting ',' delimiter at line" in report
        assert '"name": "X"' in report and "^" in report

    def test_a_bad_value_names_field_value_and_line(
        self, store: CharacterStore, file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        text = file.read_text()
        _write(file, text.replace('"temp_hp": 0', '"temp_hp": "lots"'))
        store.reload_paths({file})
        report = caplog.text
        assert "state.temp_hp:" in report and "value: 'lots'" in report
        line = next(n for n, row in enumerate(text.splitlines(), 1) if '"temp_hp"' in row)
        assert f"> {line} |" in report

    def test_a_list_item_is_located(
        self, store: CharacterStore, file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        data = json.loads(file.read_text())
        data["inventory"] = [{"item": "Rope"}, {"item": "Torch", "quantity": -2}]
        _write(file, json.dumps(data, indent=2))
        store.reload_paths({file})
        assert "inventory.1.quantity" in caplog.text
        assert '"quantity": -2' in caplog.text

    def test_a_duplicate_names_both_files(
        self, store: CharacterStore, file: Path, root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        copy = root / "copy.json"
        _write(copy, file.read_text())
        store.reload_paths({copy})
        report = caplog.text
        assert f"{copy} ignored" in report and str(file) in report
        assert report.count('"id":') == 2
