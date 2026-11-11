"""Tests for the JSON file watcher.

documentation/PROGRESS.md requires that a hand-edit to any JSON file is picked up without a restart.
The tests use real files and a real event loop, because the failure modes worth
catching — our own atomic writes triggering reloads, editor save bursts causing
reload storms — only appear against a real filesystem.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.config import Settings
from app.store.jsonfile import write_json
from app.store.registry import StoreRegistry
from app.store.watcher import JsonWatcher, _is_relevant


class TestRelevanceFilter:
    """Which paths the watcher reacts to at all."""

    def test_json_files_are_watched(self) -> None:
        assert _is_relevant("/data/compendium/bestiary/bestiary-mm.json")

    def test_markdown_is_watched(self) -> None:
        """Worlds, adventures and chapters are Markdown files (ADVENTURES.md §3)."""
        assert _is_relevant("/data/library/worlds/eberron/world.md")

    def test_other_files_are_ignored(self) -> None:
        assert not _is_relevant("/data/media/token.png")
        assert not _is_relevant("/data/notes.txt")

    def test_our_own_atomic_write_temp_file_is_ignored(self) -> None:
        """``write_json`` writes ``.name.json.tmp`` beside the target.

        Watching it would make every game-state save trigger a compendium reload.
        """
        assert not _is_relevant("/data/gamestate/.encounters.json.tmp")

    def test_dot_directories_are_ignored(self) -> None:
        assert not _is_relevant("/project/.git/index.json")

    def test_extension_match_is_case_insensitive(self) -> None:
        assert _is_relevant("/data/Bestiary.JSON")


class TestWatching:
    async def test_callback_fires_on_a_new_file(self, tmp_path: Path) -> None:
        seen: list[set[Path]] = []
        watcher = JsonWatcher([tmp_path], seen.append, debounce_ms=50)
        watcher.start()
        try:
            await asyncio.sleep(0.3)
            write_json(tmp_path / "new.json", {"a": 1})
            await asyncio.wait_for(_until(lambda: bool(seen)), timeout=5)
        finally:
            await watcher.stop()

        assert seen
        assert any(p.name == "new.json" for paths in seen for p in paths)

    async def test_callback_fires_on_an_edit(self, tmp_path: Path) -> None:
        path = tmp_path / "edit.json"
        write_json(path, {"cr": "1"})

        seen: list[set[Path]] = []
        watcher = JsonWatcher([tmp_path], seen.append, debounce_ms=50)
        watcher.start()
        try:
            await asyncio.sleep(0.3)
            write_json(path, {"cr": "9"})
            await asyncio.wait_for(_until(lambda: bool(seen)), timeout=5)
        finally:
            await watcher.stop()

        assert seen

    async def test_a_failing_callback_does_not_stop_the_watcher(self, tmp_path: Path) -> None:
        """A broken reload must not silently disable live updating."""
        calls: list[int] = []

        def explode(_paths: set[Path]) -> None:
            calls.append(1)
            raise RuntimeError("reload failed")

        watcher = JsonWatcher([tmp_path], explode, debounce_ms=50)
        watcher.start()
        try:
            await asyncio.sleep(0.3)
            write_json(tmp_path / "a.json", {"n": 1})
            await asyncio.wait_for(_until(lambda: len(calls) >= 1), timeout=5)
            write_json(tmp_path / "b.json", {"n": 2})
            await asyncio.wait_for(_until(lambda: len(calls) >= 2), timeout=5)
        finally:
            await watcher.stop()

        assert len(calls) >= 2

    async def test_non_json_changes_do_not_fire(self, tmp_path: Path) -> None:
        seen: list[set[Path]] = []
        watcher = JsonWatcher([tmp_path], seen.append, debounce_ms=50)
        watcher.start()
        try:
            await asyncio.sleep(0.3)
            (tmp_path / "token.png").write_bytes(b"\x89PNG")
            await asyncio.sleep(0.6)
        finally:
            await watcher.stop()

        assert seen == []

    async def test_start_is_idempotent_and_stop_is_safe(self, tmp_path: Path) -> None:
        watcher = JsonWatcher([tmp_path], lambda _p: None)
        watcher.start()
        watcher.start()
        await watcher.stop()
        await watcher.stop()

    def test_missing_roots_are_dropped(self, tmp_path: Path) -> None:
        watcher = JsonWatcher([tmp_path / "absent"], lambda _p: None)
        watcher.start()
        assert watcher._task is None


class TestRegistryReload:
    async def test_editing_homebrew_updates_the_compendium(self, tmp_path: Path) -> None:
        """The end-to-end requirement: edit a file, see the change without a restart."""
        reference = tmp_path / "reference"
        library = tmp_path / "library"
        (library / "homebrew" / "bestiary").mkdir(parents=True)
        reference.mkdir()

        path = library / "homebrew" / "bestiary" / "bestiary-hb.json"
        path.write_text(json.dumps({"monster": [{"name": "Beast", "source": "HB", "cr": "3"}]}))

        settings = Settings(
            reference_dir=reference,
            library_dir=library,
            watch_files=True,
        )
        registry = StoreRegistry(settings)
        registry.load()
        assert registry.compendium.get("monster", "beast|hb").cr_value == "3"

        registry.start_watching()
        try:
            await asyncio.sleep(0.3)
            path.write_text(json.dumps({"monster": [{"name": "Beast", "source": "HB", "cr": "9"}]}))
            await asyncio.wait_for(
                _until(lambda: registry.compendium.get("monster", "beast|hb").cr_value == "9"),
                timeout=5,
            )
        finally:
            await registry.stop_watching()

        assert registry.compendium.get("monster", "beast|hb").cr_value == "9"

    def test_homebrew_is_loaded_after_compendium(self, tmp_path: Path) -> None:
        """Precedence order is what lets homebrew override an imported entry."""
        settings = Settings(
            reference_dir=tmp_path / "reference",
            library_dir=tmp_path / "library",
        )
        registry = StoreRegistry(settings)
        assert registry.compendium_roots == [settings.reference_dir, settings.homebrew_dir]


async def _until(predicate: object, interval: float = 0.05) -> None:
    """Poll ``predicate`` until it returns True."""
    assert callable(predicate)
    while not predicate():
        await asyncio.sleep(interval)
