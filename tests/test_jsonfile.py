"""Tests for the atomic JSON file layer."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.store.jsonfile import iter_json_files, read_json, update_json, write_json


def test_read_missing_returns_default(tmp_path: Path) -> None:
    assert read_json(tmp_path / "absent.json", default={"a": 1}) == {"a": 1}


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    payload = {"name": "Goblin", "cr": "1/4", "tags": ["small", "humanoid"]}
    write_json(path, payload)
    assert read_json(path) == payload


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "file.json"
    write_json(path, {"ok": True})
    assert path.exists()


def test_write_preserves_non_ascii(tmp_path: Path) -> None:
    """German is a target language, so umlauts must survive a round trip unescaped."""
    path = tmp_path / "de.json"
    write_json(path, {"name": "Rüstung", "desc": "Größe"})
    assert "Rüstung" in path.read_text(encoding="utf-8")
    assert read_json(path)["name"] == "Rüstung"


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "clean.json"
    write_json(path, {"a": 1})
    assert list(tmp_path.iterdir()) == [path]


def test_failed_write_removes_temp_and_keeps_original(tmp_path: Path) -> None:
    """A serialisation failure must not destroy the file that was already there."""
    path = tmp_path / "keep.json"
    write_json(path, {"good": 1})

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        write_json(path, {"bad": Unserialisable()})

    assert read_json(path) == {"good": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_update_json_reads_mutates_writes(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    write_json(path, {"hp": 20})
    with update_json(path) as state:
        state["hp"] -= 7
    assert read_json(path) == {"hp": 13}


def test_update_json_creates_from_default(tmp_path: Path) -> None:
    path = tmp_path / "new.json"
    with update_json(path, default={}) as state:
        state["created"] = True
    assert read_json(path) == {"created": True}


def test_concurrent_updates_do_not_lose_writes(tmp_path: Path) -> None:
    """Two threads incrementing the same counter must both be reflected.

    This is the read-modify-write race the per-path lock exists to prevent.
    """
    path = tmp_path / "counter.json"
    write_json(path, {"n": 0})

    def bump() -> None:
        for _ in range(50):
            with update_json(path) as state:
                state["n"] += 1

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert read_json(path)["n"] == 200


def test_written_file_is_valid_json_from_another_reader(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    write_json(path, {"a": [1, 2, 3]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": [1, 2, 3]}


def test_iter_json_files_skips_dotfiles(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / ".hidden.json").write_text("{}")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text("{}")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "c.json").write_text("{}")

    found = {p.name for p in iter_json_files(tmp_path)}
    assert found == {"a.json", "b.json"}


def test_iter_json_files_tolerates_missing_root(tmp_path: Path) -> None:
    assert list(iter_json_files(tmp_path / "nope")) == []
