"""Tests for the fluff keyspace and its join onto entries (corpus Phase 1, pass 2).

A statblock alone cannot fill a tooltip (documentation/DESIGN.md §7), so what matters
here is that the join *lands* — a loaded record nobody can reach is no better than an
unloaded one.

One regression test worth naming: ``subclassFluff`` is keyed by class as well as by
``(name, source)``, because "Alchemist" does not say whose Alchemist. A two-part key
joined **0 of 148** records while reporting a clean load.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.compendium import Fluff, Subclass
from app.store.compendium import CompendiumStore
from app.store.fluff import FluffStore, category_for
from tests.conftest import requires_corpus


class TestKeyConvention:
    """Fluff keys name their category by convention, so new books need no code."""

    def test_category_is_the_prefix(self) -> None:
        assert category_for("monsterFluff") == "monster"
        assert category_for("subclassFluff") == "subclass"
        assert category_for("optionalfeatureFluff") == "optionalfeature"


class TestFluffModel:
    """The record itself."""

    def test_explicit_null_images_is_tolerated(self) -> None:
        """One corpus record carries ``"images": null``; intent is plainly "none"."""
        fluff = Fluff.model_validate({"name": "X", "source": "MM", "images": None})
        assert fluff.images == []

    def test_has_content_distinguishes_empty_from_absent(self) -> None:
        assert not Fluff(name="X", source="MM").has_content
        assert Fluff(name="X", source="MM", entries=["text"]).has_content
        assert Fluff(name="X", source="MM", images=[{"type": "image"}]).has_content

    def test_fluff_supports_copy(self) -> None:
        """1,310 of 5,984 records are shells, so the model must accept ``_copy``."""
        fluff = Fluff.model_validate(
            {"name": "Child", "source": "MM", "_copy": {"name": "Parent", "source": "MM"}}
        )
        assert fluff.is_unresolved_copy


class TestLoadingAndJoining:
    """Discovery, resolution and the lookup, on synthetic data."""

    def _corpus(self, tmp_path: Path) -> Path:
        bestiary = tmp_path / "bestiary"
        bestiary.mkdir()
        (bestiary / "bestiary-hb.json").write_text(
            json.dumps({"monster": [{"name": "Beast", "source": "HB", "cr": "3"}]})
        )
        (bestiary / "fluff-bestiary-hb.json").write_text(
            json.dumps(
                {
                    "monsterFluff": [
                        {"name": "Beast", "source": "HB", "entries": ["A description."]},
                        {
                            "name": "Copycat",
                            "source": "HB",
                            "_copy": {"name": "Beast", "source": "HB"},
                        },
                    ]
                }
            )
        )
        return tmp_path

    def test_fluff_is_discovered_by_suffix(self, tmp_path: Path) -> None:
        store = FluffStore()
        report = store.load(self._corpus(tmp_path))
        assert report.ok
        assert store.count("monster") == 2

    def test_lookup_by_name_and_source(self, tmp_path: Path) -> None:
        store = FluffStore()
        store.load(self._corpus(tmp_path))
        found = store.get("monster", "Beast", "HB")
        assert found is not None
        assert found.entries == ["A description."]

    def test_lookup_is_case_insensitive(self, tmp_path: Path) -> None:
        store = FluffStore()
        store.load(self._corpus(tmp_path))
        assert store.get("monster", "beast", "hb") is not None

    def test_copy_within_the_fluff_keyspace_resolves(self, tmp_path: Path) -> None:
        """Fluff shells copy from other *fluff* records, not from entries."""
        store = FluffStore()
        store.load(self._corpus(tmp_path))
        copycat = store.get("monster", "Copycat", "HB")
        assert copycat is not None
        assert copycat.entries == ["A description."]

    def test_join_onto_a_loaded_entry(self, tmp_path: Path) -> None:
        root = self._corpus(tmp_path)
        compendium = CompendiumStore()
        compendium.load(root)
        fluff = FluffStore()
        fluff.load(root)
        monster = compendium.find("monster", "Beast")
        assert monster is not None
        assert fluff.for_entry("monster", monster) is not None

    def test_entry_without_fluff_returns_none(self, tmp_path: Path) -> None:
        store = FluffStore()
        store.load(self._corpus(tmp_path))
        assert store.get("monster", "Nonesuch", "HB") is None
        assert not store.has_fluff("monster", "Nonesuch", "HB")

    def test_missing_root_is_tolerated(self, tmp_path: Path) -> None:
        report = FluffStore().load(tmp_path / "absent")
        assert report.total == 0
        assert report.ok

    def test_foundry_files_are_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "foundry-bestiary.json").write_text(
            json.dumps({"monsterFluff": [{"name": "Fake", "source": "HB"}]})
        )
        store = FluffStore()
        store.load(tmp_path)
        assert store.count("monster") == 0

    def test_later_roots_override_earlier_ones(self, tmp_path: Path) -> None:
        """Homebrew fluff shadows imported fluff, as homebrew entries do."""
        reference, homebrew = tmp_path / "reference", tmp_path / "homebrew"
        reference.mkdir()
        homebrew.mkdir()
        (reference / "fluff-x.json").write_text(
            json.dumps({"monsterFluff": [{"name": "Beast", "source": "HB", "entries": ["old"]}]})
        )
        (homebrew / "fluff-x.json").write_text(
            json.dumps({"monsterFluff": [{"name": "Beast", "source": "HB", "entries": ["new"]}]})
        )
        store = FluffStore()
        store.load(reference, homebrew)
        found = store.get("monster", "Beast", "HB")
        assert found is not None and found.entries == ["new"]


class TestSubclassFluffNeedsTheClass:
    """The regression: a subclass name alone does not identify a subclass."""

    def _corpus(self, tmp_path: Path) -> Path:
        klass = tmp_path / "class"
        klass.mkdir()
        (klass / "class-artificer.json").write_text(
            json.dumps(
                {
                    "subclass": [
                        {
                            "name": "Alchemist",
                            "shortName": "Alchemist",
                            "source": "TCE",
                            "className": "Artificer",
                            "classSource": "TCE",
                        }
                    ]
                }
            )
        )
        (klass / "fluff-class-artificer.json").write_text(
            json.dumps(
                {
                    "subclassFluff": [
                        {
                            "name": "Alchemist",
                            "source": "TCE",
                            "className": "Artificer",
                            "classSource": "TCE",
                            "entries": ["Alchemist art."],
                        }
                    ]
                }
            )
        )
        return tmp_path

    def test_subclass_fluff_joins_via_the_class(self, tmp_path: Path) -> None:
        root = self._corpus(tmp_path)
        compendium = CompendiumStore()
        compendium.load(root)
        fluff = FluffStore()
        fluff.load(root)
        subclass = compendium.find("subclass", "Alchemist")
        assert subclass is not None
        found = fluff.for_entry("subclass", subclass)
        assert found is not None, "subclass fluff must join on className/classSource"
        assert found.entries == ["Alchemist art."]

    def test_a_two_part_key_does_not_reach_subclass_fluff(self, tmp_path: Path) -> None:
        """Documents why the discriminators are needed, not merely nice."""
        store = FluffStore()
        store.load(self._corpus(tmp_path))
        assert store.get("subclass", "Alchemist", "TCE") is None
        assert (
            store.get(
                "subclass", "Alchemist", "TCE", className="Artificer", classSource="TCE"
            )
            is not None
        )

    def test_same_name_in_two_classes_stays_distinct(self, tmp_path: Path) -> None:
        klass = tmp_path / "class"
        klass.mkdir()
        (klass / "fluff-class-x.json").write_text(
            json.dumps(
                {
                    "subclassFluff": [
                        {
                            "name": "Alchemist",
                            "source": "TCE",
                            "className": "Artificer",
                            "classSource": "TCE",
                            "entries": ["artificer"],
                        },
                        {
                            "name": "Alchemist",
                            "source": "TCE",
                            "className": "Wizard",
                            "classSource": "PHB",
                            "entries": ["wizard"],
                        },
                    ]
                }
            )
        )
        store = FluffStore()
        store.load(tmp_path)
        assert store.count("subclass") == 2
        artificer = Subclass(
            name="Alchemist",
            source="TCE",
            shortName="Alchemist",
            className="Artificer",
            classSource="TCE",
        )
        found = store.for_entry("subclass", artificer)
        assert found is not None and found.entries == ["artificer"]


class TestOrphansAreReportedNotDropped:
    """Fluff with no entry is normal, and sometimes load-bearing."""

    def test_orphan_count_is_available(self, tmp_path: Path) -> None:
        (tmp_path / "fluff-x.json").write_text(
            json.dumps(
                {
                    "monsterFluff": [
                        {"name": "Real", "source": "HB", "entries": ["x"]},
                        {"name": "GroupOnly", "source": "HB", "entries": ["y"]},
                    ]
                }
            )
        )
        store = FluffStore()
        store.load(tmp_path)
        assert store.count_orphans("monster", ["real|hb"]) == 1

    def test_an_orphan_may_be_a_copy_parent(self, tmp_path: Path) -> None:
        """Pruning orphans at load would break the shells that copy from them."""
        (tmp_path / "fluff-x.json").write_text(
            json.dumps(
                {
                    "monsterFluff": [
                        {"name": "Parent", "source": "HB", "entries": ["shared"]},
                        {
                            "name": "Child",
                            "source": "HB",
                            "_copy": {"name": "Parent", "source": "HB"},
                        },
                    ]
                }
            )
        )
        store = FluffStore()
        store.load(tmp_path)
        child = store.get("monster", "Child", "HB")
        assert child is not None and child.entries == ["shared"]


@requires_corpus
class TestAgainstTheRealCorpus:
    """Claims only the real data can settle."""

    def test_every_fluff_record_loads(self, corpus: Path) -> None:
        store = FluffStore()
        report = store.load(corpus)
        assert report.total == 6725
        assert report.errors == []
        assert report.unresolved_copies == []

    def test_all_twenty_fluff_keys_are_found(self, corpus: Path) -> None:
        """19 categories carry records; ``rewardFluff`` is an empty array upstream."""
        store = FluffStore()
        store.load(corpus)
        assert len(store.categories) == 19
        assert store.count("monster") == 4300
        assert store.count("item") == 1212
        assert store.count("race") == 240

    def test_prose_reaches_the_entry(self, corpus: Path) -> None:
        """The point of the whole exercise: a tooltip can show a description."""
        compendium = CompendiumStore()
        compendium.load(corpus)
        fluff = FluffStore()
        fluff.load(corpus)

        goblin = compendium.get("monster", "goblin|mm")
        found = fluff.for_entry("monster", goblin)
        assert found is not None
        assert found.has_content
        assert "Goblins" in json.dumps(found.entries)

    def test_subclass_fluff_mostly_joins(self, corpus: Path) -> None:
        """141 of 148 join; the 7 left over are XGE class art filed under subclassFluff."""
        compendium = CompendiumStore()
        compendium.load(corpus)
        fluff = FluffStore()
        fluff.load(corpus)
        joined = sum(
            1 for s in compendium.all("subclass") if fluff.for_entry("subclass", s)
        )
        assert joined >= 140

    def test_a_copy_shell_resolves_against_the_real_corpus(self, corpus: Path) -> None:
        store = FluffStore()
        store.load(corpus)
        found = store.get("monster", "Solar Dragon", "BAM")
        assert found is not None
        assert found.has_content
