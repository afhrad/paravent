"""Tests for the in-memory compendium store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.store.compendium import CATEGORIES_BY_NAME, CompendiumStore
from app.store.errors import EntryNotFound
from tests.conftest import requires_corpus


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    """A minimal corpus laid out like the real one, for tests that need no real data."""
    (tmp_path / "bestiary").mkdir()
    (tmp_path / "spells").mkdir()

    (tmp_path / "bestiary" / "bestiary-mm.json").write_text(
        json.dumps(
            {
                "monster": [
                    {"name": "Goblin", "source": "MM", "cr": "1/4", "size": ["S"], "ac": [15]},
                    {"name": "Hobgoblin", "source": "MM", "cr": "1/2", "size": ["M"]},
                    {
                        "name": "Goblin Boss",
                        "source": "MM",
                        "_copy": {
                            "name": "Goblin",
                            "source": "MM",
                            "_mod": {"cr": {"mode": "setProp", "value": "1"}},
                        },
                    },
                ]
            }
        )
    )
    (tmp_path / "spells" / "spells-phb.json").write_text(
        json.dumps(
            {
                "spell": [
                    {"name": "Fireball", "source": "PHB", "level": 3, "school": "V"},
                    {"name": "Fire Bolt", "source": "PHB", "level": 0, "school": "V"},
                ]
            }
        )
    )
    return tmp_path


class TestLoading:
    def test_loads_entries_by_category(self, tiny_corpus: Path) -> None:
        store = CompendiumStore()
        report = store.load(tiny_corpus)
        assert report.loaded["monster"] == 3
        assert report.loaded["spell"] == 2
        assert report.ok

    def test_copy_entries_are_resolved_at_load(self, tiny_corpus: Path) -> None:
        """An unresolved copy would render as a blank statblock."""
        store = CompendiumStore()
        store.load(tiny_corpus)
        boss = store.get("monster", "goblin boss|mm")
        assert boss.cr_value == "1"
        assert boss.ac == [15]
        assert not boss.is_unresolved_copy

    def test_load_is_idempotent(self, tiny_corpus: Path) -> None:
        store = CompendiumStore()
        store.load(tiny_corpus)
        store.load(tiny_corpus)
        assert store.count("monster") == 3

    def test_later_root_overrides_earlier(self, tiny_corpus: Path, tmp_path: Path) -> None:
        """This is how homebrew/ shadows compendium/."""
        homebrew = tmp_path / "homebrew"
        (homebrew / "bestiary").mkdir(parents=True)
        (homebrew / "bestiary" / "bestiary-mm.json").write_text(
            json.dumps({"monster": [{"name": "Goblin", "source": "MM", "cr": "5"}]})
        )
        store = CompendiumStore()
        store.load(tiny_corpus, homebrew)
        assert store.get("monster", "goblin|mm").cr_value == "5"

    def test_missing_root_is_tolerated(self, tmp_path: Path) -> None:
        store = CompendiumStore()
        report = store.load(tmp_path / "absent")
        assert report.total == 0
        assert report.ok

    def test_malformed_json_is_skipped_not_fatal(self, tiny_corpus: Path) -> None:
        (tiny_corpus / "spells" / "spells-broken.json").write_text("{not json")
        store = CompendiumStore()
        store.load(tiny_corpus)
        assert store.count("spell") == 2

    def test_invalid_entry_is_collected_in_lenient_mode(self, tmp_path: Path) -> None:
        (tmp_path / "spells").mkdir()
        (tmp_path / "spells" / "spells-x.json").write_text(
            json.dumps({"spell": [{"name": "Broken", "source": "X"}]})  # missing level/school
        )
        store = CompendiumStore()
        report = store.load(tmp_path)
        assert report.errors
        assert not report.ok

    def test_strict_mode_raises_on_invalid_entry(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        (tmp_path / "spells").mkdir()
        (tmp_path / "spells" / "spells-x.json").write_text(
            json.dumps({"spell": [{"name": "Broken", "source": "X"}]})
        )
        with pytest.raises(ValidationError):
            CompendiumStore(strict=True).load(tmp_path)


class TestQueries:
    @pytest.fixture
    def store(self, tiny_corpus: Path) -> CompendiumStore:
        store = CompendiumStore()
        store.load(tiny_corpus)
        return store

    def test_get_is_case_insensitive(self, store: CompendiumStore) -> None:
        assert store.get("monster", "GOBLIN|MM").name == "Goblin"

    def test_get_missing_raises(self, store: CompendiumStore) -> None:
        with pytest.raises(EntryNotFound):
            store.get("monster", "beholder|mm")

    def test_unknown_category_raises(self, store: CompendiumStore) -> None:
        with pytest.raises(EntryNotFound):
            store.get("dragonkin", "x|y")

    def test_find_without_source(self, store: CompendiumStore) -> None:
        """A source-less tag reference resolves to whichever edition is loaded."""
        assert store.find("monster", "goblin") is not None

    def test_find_with_source_pins_the_edition(self, store: CompendiumStore) -> None:
        assert store.find("monster", "Goblin", "MM") is not None
        assert store.find("monster", "Goblin", "XMM") is None

    def test_search_ranks_exact_then_prefix_then_substring(self, store: CompendiumStore) -> None:
        names = [m.name for m in store.search("monster", "goblin")]
        assert names[0] == "Goblin"
        assert names[1] == "Goblin Boss"
        assert names[2] == "Hobgoblin"

    def test_search_is_case_insensitive(self, store: CompendiumStore) -> None:
        assert store.search("monster", "GOBLIN")

    def test_empty_query_returns_everything(self, store: CompendiumStore) -> None:
        assert len(store.search("monster", "")) == 3

    def test_search_respects_limit(self, store: CompendiumStore) -> None:
        assert len(store.search("monster", "", limit=2)) == 2

    def test_search_with_predicate(self, store: CompendiumStore) -> None:
        cantrips = store.search("spell", "", predicate=lambda s: s.level == 0)
        assert [s.name for s in cantrips] == ["Fire Bolt"]

    def test_search_all_spans_categories(self, store: CompendiumStore) -> None:
        results = store.search_all("fire")
        assert "spell" in results
        assert "monster" not in results

    def test_sources_lists_distinct_books(self, store: CompendiumStore) -> None:
        assert store.sources("monster") == ["MM"]

    def test_iteration_yields_category_and_model(self, store: CompendiumStore) -> None:
        pairs = list(store)
        assert len(pairs) == 5
        assert {c for c, _ in pairs} == {"monster", "spell"}


@requires_corpus
class TestAgainstRealCorpus:
    def test_expected_scale(self, loaded_compendium: CompendiumStore) -> None:
        """Sanity-check the load actually pulled the full corpus, not a fragment."""
        assert loaded_compendium.count("monster") > 4000
        assert loaded_compendium.count("spell") > 900
        assert loaded_compendium.count("item") > 2000

    def test_known_monster_resolves_correctly(self, loaded_compendium: CompendiumStore) -> None:
        goblin = loaded_compendium.find("monster", "Goblin", "MM")
        assert goblin is not None
        assert goblin.cr_value == "1/4"
        assert goblin.size_label == "Small"
        assert goblin.abilities["dex"] == 14

    def test_search_is_fast_enough_for_typeahead(self, loaded_compendium: CompendiumStore) -> None:
        """Search backs a live search popup, so it has to stay interactive."""
        import time

        start = time.perf_counter()
        for _ in range(20):
            loaded_compendium.search("monster", "dragon", limit=20)
        elapsed_ms = (time.perf_counter() - start) * 1000 / 20
        assert elapsed_ms < 50, f"search took {elapsed_ms:.1f}ms per query"

    def test_every_category_in_the_table_is_declared_consistently(self) -> None:
        for name, category in CATEGORIES_BY_NAME.items():
            assert category.name == name
            assert category.globs
