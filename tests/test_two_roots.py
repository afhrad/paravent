"""Tests for the two-root data split (documentation/TECHSTACK.md §3).

Reference data is somebody else's, read-only, and replaced wholesale on re-import.
Library data is the user's and irreplaceable. The rules below are the ones that make
"delete the corpus and re-import" a safe operation, so they are asserted rather than
trusted to documentation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.models.gamestate import World
from app.store.registry import ReferenceRootViolation, StoreRegistry


class TestDerivedPaths:
    """Homebrew, media and translations are library data, not reference data."""

    def test_homebrew_lives_under_library(self, tmp_path: Path) -> None:
        """Homebrew uses the 5eTools format but is the user's work — so it is backed up."""
        settings = Settings(library_dir=tmp_path / "library")
        assert settings.homebrew_dir == tmp_path / "library" / "homebrew"

    def test_media_and_i18n_live_under_library(self, tmp_path: Path) -> None:
        settings = Settings(library_dir=tmp_path / "library")
        assert settings.media_dir == tmp_path / "library" / "media"
        assert settings.i18n_dir == tmp_path / "library" / "i18n"

    def test_derived_paths_follow_a_relocated_library(self, tmp_path: Path) -> None:
        """Moving the library root moves everything inside it, with no second setting."""
        settings = Settings(library_dir=tmp_path / "elsewhere")
        assert settings.homebrew_dir.is_relative_to(tmp_path / "elsewhere")
        assert settings.media_dir.is_relative_to(tmp_path / "elsewhere")


class TestReleaseRoot:
    """A 5eTools release is the whole site; its corpus is the ``data`` folder in it."""

    def test_a_release_is_read_from_its_data_folder(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "books.json").write_text("{}")
        assert Settings(reference_dir=tmp_path).reference_dir == tmp_path / "data"

    def test_the_corpus_itself_stays_as_named(self, tmp_path: Path) -> None:
        (tmp_path / "books.json").write_text("{}")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "books.json").write_text("{}")
        assert Settings(reference_dir=tmp_path).reference_dir == tmp_path


class TestReferenceConfigured:
    """A fresh checkout has no corpus. That is a reportable state, not an error."""

    def test_unset_reference_is_not_configured(self) -> None:
        assert Settings().reference_configured is False

    def test_a_set_reference_is_configured(self, tmp_path: Path) -> None:
        assert Settings(reference_dir=tmp_path).reference_configured is True

    def test_unconfigured_reference_is_absent_from_load_roots(self, tmp_path: Path) -> None:
        """Without a corpus the tool still runs — homebrew only."""
        settings = Settings(library_dir=tmp_path / "library")
        registry = StoreRegistry(settings)
        assert registry.compendium_roots == [settings.homebrew_dir]

    def test_configured_reference_is_loaded_before_homebrew(self, tmp_path: Path) -> None:
        """Precedence order is what lets homebrew override an imported entry."""
        settings = Settings(
            reference_dir=tmp_path / "reference",
            library_dir=tmp_path / "library",
        )
        registry = StoreRegistry(settings)
        assert registry.compendium_roots == [
            settings.reference_dir,
            settings.homebrew_dir,
        ]


class TestReferenceIsNeverWritten:
    """The guard that makes the read-only promise enforceable."""

    def test_library_inside_the_corpus_is_refused(self, tmp_path: Path) -> None:
        """A combat-tracker write must never land in data the next re-import erases."""
        reference = tmp_path / "reference"
        reference.mkdir()
        settings = Settings(
            reference_dir=reference,
            library_dir=reference / "library",
        )
        registry = StoreRegistry(settings)
        with pytest.raises(ReferenceRootViolation, match="read-only reference root"):
            registry.assert_reference_readonly()

    def test_cache_inside_the_corpus_is_refused(self, tmp_path: Path) -> None:
        reference = tmp_path / "reference"
        reference.mkdir()
        settings = Settings(
            reference_dir=reference,
            library_dir=tmp_path / "library",
            reference_cache_dir=reference / "cache",
        )
        registry = StoreRegistry(settings)
        with pytest.raises(ReferenceRootViolation):
            registry.assert_reference_readonly()

    def test_library_equal_to_the_corpus_is_refused(self, tmp_path: Path) -> None:
        """The degenerate case: both roots pointing at the same directory."""
        reference = tmp_path / "shared"
        reference.mkdir()
        settings = Settings(reference_dir=reference, library_dir=reference)
        registry = StoreRegistry(settings)
        with pytest.raises(ReferenceRootViolation):
            registry.assert_reference_readonly()

    def test_separate_roots_are_accepted(self, tmp_path: Path) -> None:
        settings = Settings(
            reference_dir=tmp_path / "reference",
            library_dir=tmp_path / "library",
            reference_cache_dir=tmp_path / "cache",
        )
        StoreRegistry(settings).assert_reference_readonly()  # must not raise

    def test_load_enforces_the_guard(self, tmp_path: Path) -> None:
        """The check runs on the real path a caller takes, not only when asked."""
        reference = tmp_path / "reference"
        reference.mkdir()
        settings = Settings(reference_dir=reference, library_dir=reference / "lib")
        with pytest.raises(ReferenceRootViolation):
            StoreRegistry(settings).load()

    def test_unconfigured_reference_cannot_be_violated(self, tmp_path: Path) -> None:
        """With no corpus there is no read-only root to protect."""
        StoreRegistry(Settings(library_dir=tmp_path / "library")).assert_reference_readonly()

    def test_game_state_writes_land_under_library(self, tmp_path: Path) -> None:
        """The end-to-end check: a real write, and where the file turns up."""
        reference = tmp_path / "reference"
        reference.mkdir()
        library = tmp_path / "library"
        settings = Settings(reference_dir=reference, library_dir=library)
        registry = StoreRegistry(settings)
        registry.load()

        registry.gamestate.worlds.create(World(name="Faerûn"))

        assert (library / "worlds" / "faer-n" / "world.md").exists()
        assert not list(reference.rglob("*"))


class TestChangeRouting:
    """Homebrew sits under the library root but belongs to the compendium."""

    def test_homebrew_change_reloads_the_compendium(self, tmp_path: Path) -> None:
        reference = tmp_path / "reference"
        reference.mkdir()
        library = tmp_path / "library"
        (library / "homebrew" / "bestiary").mkdir(parents=True)
        path = library / "homebrew" / "bestiary" / "bestiary-hb.json"
        path.write_text(json.dumps({"monster": [{"name": "Beast", "source": "HB", "cr": "3"}]}))

        settings = Settings(reference_dir=reference, library_dir=library, watch_files=False)
        registry = StoreRegistry(settings)
        registry.load()
        assert registry.compendium.get("monster", "beast|hb").cr_value == "3"

        path.write_text(json.dumps({"monster": [{"name": "Beast", "source": "HB", "cr": "9"}]}))
        registry._on_change({path})

        assert registry.compendium.get("monster", "beast|hb").cr_value == "9"

    def test_library_record_change_reloads_game_state(self, tmp_path: Path) -> None:
        reference = tmp_path / "reference"
        reference.mkdir()
        library = tmp_path / "library"
        library.mkdir()

        settings = Settings(reference_dir=reference, library_dir=library, watch_files=False)
        registry = StoreRegistry(settings)
        registry.load()

        users = library / "users.json"
        users.write_text(json.dumps({"u1": {"id": "u1", "username": "eberron"}}))
        registry._on_change({users})

        assert registry.gamestate.users.get("u1").username == "eberron"

    def test_world_file_change_reloads_the_world(self, tmp_path: Path) -> None:
        reference = tmp_path / "reference"
        reference.mkdir()
        library = tmp_path / "library"
        settings = Settings(reference_dir=reference, library_dir=library, watch_files=False)
        registry = StoreRegistry(settings)
        registry.load()

        world = library / "worlds" / "eberron" / "world.md"
        world.parent.mkdir(parents=True)
        world.write_text("---\nid: w1\nname: Eberron\n---\nKhorvaire.\n")
        registry._on_change({world})

        assert registry.gamestate.worlds.get("w1").description == "Khorvaire."
