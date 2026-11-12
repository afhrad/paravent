"""The parsed corpus kept for the next start (app/store/snapshot.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.store.compendium import CompendiumStore
from app.store.fluff import FluffStore
from app.store.registry import StoreRegistry
from app.store.snapshot import PREFIX, Snapshot, fingerprint


def _registry(corpus: Path, tmp_path: Path) -> StoreRegistry:
    return StoreRegistry(
        Settings(
            reference_dir=corpus,
            library_dir=tmp_path / "lib",
            reference_cache_dir=tmp_path / "cache",
            reference_snapshot=True,
            watch_files=False,
        )
    )


def _snapshots(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "cache").glob(f"{PREFIX}*.pickle"))


def _no_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading the corpus fails from here on: whatever loads now came from the snapshot."""

    def refuse(self: object, *roots: Path) -> None:
        raise AssertionError("the corpus was read")

    monkeypatch.setattr(CompendiumStore, "load", refuse)
    monkeypatch.setattr(FluffStore, "load", refuse)


class TestSnapshot:
    def test_the_next_start_takes_it(
        self, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = _registry(corpus, tmp_path)
        first.load()
        assert len(_snapshots(tmp_path)) == 1
        _no_reading(monkeypatch)
        again = _registry(corpus, tmp_path)
        report = again.load()
        assert report.total == first.compendium.report.total
        assert again.compendium.get("monster", "Goblin|MM").name == "Goblin"
        goblin = again.compendium.get("monster", "Goblin|MM")
        assert again.fluff.for_entry("monster", goblin) is not None
        assert again.compendium.resolver() is not None  # the name index is built again
        assert again.compendium.generation == 1

    def test_a_changed_file_makes_a_new_one(self, corpus: Path, tmp_path: Path) -> None:
        _registry(corpus, tmp_path).load()
        old = _snapshots(tmp_path)
        homebrew = tmp_path / "lib" / "homebrew" / "bestiary"
        homebrew.mkdir(parents=True)
        lurker = {
            "name": "Mire Lurker",
            "source": "Homebrew",
            "_copy": {"name": "Goblin", "source": "MM"},
        }
        (homebrew / "bestiary-mine.json").write_text(json.dumps({"monster": [lurker]}))
        changed = _registry(corpus, tmp_path)
        changed.load()
        new = _snapshots(tmp_path)
        assert len(new) == 1 and new != old  # the old one is gone
        assert changed.compendium.get("monster", "Mire Lurker|Homebrew").name == "Mire Lurker"

    def test_an_unreadable_one_is_read_past(self, corpus: Path, tmp_path: Path) -> None:
        _registry(corpus, tmp_path).load()
        (path,) = _snapshots(tmp_path)
        path.write_bytes(b"not a pickle")
        again = _registry(corpus, tmp_path)
        assert again.load().total > 0
        assert path.read_bytes() != b"not a pickle"  # made anew

    def test_off_when_switched_off(self, corpus: Path, tmp_path: Path) -> None:
        registry = StoreRegistry(
            Settings(
                reference_dir=corpus,
                library_dir=tmp_path / "lib",
                reference_cache_dir=tmp_path / "cache",
                reference_snapshot=False,
                watch_files=False,
            )
        )
        registry.load()
        assert _snapshots(tmp_path) == []

    def test_the_key_follows_the_files_and_the_version(self, tmp_path: Path) -> None:
        (tmp_path / "a.json").write_text("{}")
        key = fingerprint([tmp_path], "1.0")
        assert fingerprint([tmp_path], "1.0") == key
        assert fingerprint([tmp_path], "1.1") != key
        (tmp_path / "a.json").write_text('{"x": 1}')
        assert fingerprint([tmp_path], "1.0") != key
        assert Snapshot(tmp_path / "none", key).read() is None
