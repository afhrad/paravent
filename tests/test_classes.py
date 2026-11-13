"""Tests for the class data and the generated lookup tables (corpus Phase 1).

Character generation depends on these entirely (documentation/CHARGEN.md G7), so the
assertions below are about *correctness against the printed rules*, not merely that
something loaded.

Two of these are regression tests for bugs found while building this:

* ``(name, source)`` is not unique for subclasses or their features, and a two-part
  ``_copy`` index made a cross-class copy look like a self-reference — silently dropping
  199 records.
* Spell-list membership lives under ``class`` *or* ``classVariant``; reading only the
  first emptied the class list of 119 spells.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.compendium import ClassEntry, ClassFeature, Subclass, SubclassFeature
from app.store.compendium import CompendiumStore
from app.store.copyresolver import composite_key
from app.store.lookups import LookupTables, SpellSourceLookup, SubclassLookup
from tests.conftest import requires_corpus


class TestCompositeKeys:
    """Keys that must include more than ``(name, source)``."""

    def test_subclass_key_includes_the_class(self) -> None:
        """Subclass names repeat across classes — 124 of 315 records collide without this."""
        sub = Subclass(
            name="Alchemist",
            source="TCE",
            shortName="Alchemist",
            className="Artificer",
            classSource="EFA",
        )
        assert sub.key == "alchemist|tce|artificer|efa"

    def test_class_feature_key_includes_the_level(self) -> None:
        """A feature name repeats at several levels; the level is part of its identity."""
        feature = ClassFeature(
            name="Additional Maneuvers",
            source="PHB",
            className="Fighter",
            classSource="PHB",
            level=7,
        )
        assert feature.key == "additional maneuvers|phb|fighter|7"

    def test_subclass_feature_key_distinguishes_levels(self) -> None:
        """*Additional Maneuvers* (Battle Master, PHB) exists at levels 7, 10 and 15."""
        keys = {
            SubclassFeature(
                name="Additional Maneuvers",
                source="PHB",
                className="Fighter",
                classSource="PHB",
                subclassShortName="Battle Master",
                subclassSource="PHB",
                level=level,
            ).key
            for level in (7, 10, 15)
        }
        assert len(keys) == 3

    def test_composite_key_degrades_for_simple_entries(self) -> None:
        """Categories needing nothing extra keep the two-part key."""
        assert composite_key({"name": "Goblin", "source": "MM"}) == "goblin|mm"

    def test_composite_key_appends_discriminators_in_a_fixed_order(self) -> None:
        """The index and the lookup must agree, so the order cannot depend on dict order."""
        entry = {
            "level": 3,
            "source": "PHB",
            "className": "Fighter",
            "name": "Martial Archetype",
        }
        assert composite_key(entry) == "martial archetype|phb|fighter|3"


class TestSubclassLevelInference:
    """There is no ``subclassLevel`` field; it is inferred (documentation/CHARGEN.md G5)."""

    def test_level_is_read_from_the_feature_flag(self) -> None:
        cls = ClassEntry(
            name="Wizard",
            source="PHB",
            classFeatures=[
                "Spellcasting|Wizard||1",
                {"classFeature": "Arcane Tradition|Wizard||2", "gainSubclassFeature": True},
            ],
        )
        assert cls.subclass_level == 2

    def test_no_flag_means_no_level(self) -> None:
        cls = ClassEntry(name="Sidekick", source="TCE", classFeatures=["Healer|Sidekick||1"])
        assert cls.subclass_level is None

    def test_caster_detection_follows_the_progression_field(self) -> None:
        assert ClassEntry(name="W", source="PHB", casterProgression="full").is_caster
        assert not ClassEntry(name="F", source="PHB").is_caster


class TestSpellSourceLookup:
    """Spell-list membership, the join documentation/CHARGEN.md trap #2 warns about."""

    def test_printed_membership_is_read(self) -> None:
        lookup = SpellSourceLookup(
            {"phb": {"fireball": {"class": {"PHB": {"Wizard": True, "Sorcerer": True}}}}}
        )
        assert lookup.classes_for("Fireball", "PHB") == {"PHB": {"Wizard", "Sorcerer"}}

    def test_class_variant_membership_is_read(self) -> None:
        """119 of 936 records carry only ``classVariant``; ignoring it empties their lists."""
        lookup = SpellSourceLookup(
            {
                "tce": {
                    "mind sliver": {
                        "classVariant": {
                            "PHB": {
                                "Sorcerer": {"definedInSources": ["TCE"]},
                                "Wizard": {"definedInSources": ["TCE"]},
                            }
                        }
                    }
                }
            }
        )
        assert lookup.classes_for("Mind Sliver", "TCE") == {"PHB": {"Sorcerer", "Wizard"}}

    def test_variants_can_be_excluded(self) -> None:
        """A table that has not adopted the expanded lists asks for printed-only."""
        lookup = SpellSourceLookup(
            {
                "phb": {
                    "aid": {
                        "class": {"PHB": {"Cleric": True}},
                        "classVariant": {"PHB": {"Bard": {"definedInSources": ["TCE"]}}},
                    }
                }
            }
        )
        assert lookup.classes_for("Aid", "PHB") == {"PHB": {"Cleric", "Bard"}}
        assert lookup.classes_for("Aid", "PHB", include_variants=False) == {"PHB": {"Cleric"}}

    def test_variant_sources_say_which_book_added_it(self) -> None:
        lookup = SpellSourceLookup(
            {
                "tce": {
                    "mind sliver": {
                        "classVariant": {"PHB": {"Wizard": {"definedInSources": ["TCE"]}}}
                    }
                }
            }
        )
        assert lookup.variant_sources("Mind Sliver", "TCE") == {"Wizard": {"TCE"}}

    def test_membership_is_source_dependent(self) -> None:
        """The whole reason this file exists: the same spell differs by book."""
        lookup = SpellSourceLookup(
            {
                "aag": {
                    "air bubble": {
                        "class": {"PHB": {"Druid": True}, "TCE": {"Artificer": True}}
                    }
                }
            }
        )
        classes = lookup.classes_for("Air Bubble", "AAG")
        assert classes["PHB"] == {"Druid"}
        assert classes["TCE"] == {"Artificer"}

    def test_has_class_is_case_insensitive(self) -> None:
        lookup = SpellSourceLookup({"phb": {"fireball": {"class": {"PHB": {"Wizard": True}}}}})
        assert lookup.has_class("fireball", "phb", "wizard", "PHB")

    def test_unknown_spell_is_empty_not_an_error(self) -> None:
        lookup = SpellSourceLookup({})
        assert lookup.classes_for("Nonesuch", "PHB") == {}
        assert lookup.entry("Nonesuch", "PHB") is None

    def test_explicit_false_excludes_a_class(self) -> None:
        lookup = SpellSourceLookup({"phb": {"x": {"class": {"PHB": {"Bard": False}}}}})
        assert lookup.classes_for("x", "PHB") == {}


class TestSubclassLookup:
    """Which subclasses a class has, per source."""

    def test_subclasses_are_grouped_by_source(self) -> None:
        lookup = SubclassLookup(
            {"TCE": {"Artificer": {"TCE": {"Alchemist": {"name": "Alchemist"}}}}}
        )
        assert lookup.subclasses_for("Artificer", "TCE") == {"TCE": ["Alchemist"]}

    def test_unknown_class_is_empty(self) -> None:
        assert SubclassLookup({}).subclasses_for("Wizard", "PHB") == {}


class TestMissingLookupsAreTolerated:
    """The corpus is user-supplied and may be partial (documentation/TECHSTACK.md §8)."""

    def test_absent_files_are_reported_not_raised(self, tmp_path: Path) -> None:
        tables = LookupTables()
        tables.load(tmp_path)
        assert len(tables.missing) == 2
        assert tables.spell_sources.classes_for("Fireball", "PHB") == {}

    def test_a_non_object_file_is_ignored(self, tmp_path: Path) -> None:
        generated = tmp_path / "generated"
        generated.mkdir()
        (generated / "gendata-spell-source-lookup.json").write_text("[1, 2, 3]")
        (generated / "gendata-subclass-lookup.json").write_text("{}")
        tables = LookupTables()
        tables.load(tmp_path)
        assert tables.spell_sources.spell_count == 0


class TestVttPayloadsAreExcluded:
    """Foundry files carry content keys but hold VTT automation, not content."""

    @pytest.mark.parametrize(
        "name",
        ["foundry.json", "foundry-items.json", "foundry-spells.json"],
    )
    def test_foundry_files_are_not_read(self, tmp_path: Path, name: str) -> None:
        from app.store.compendium import is_vtt_payload

        assert is_vtt_payload(tmp_path / name)

    @pytest.mark.parametrize("name", ["spells-phb.json", "items.json", "bestiary-mm.json"])
    def test_real_content_is_read(self, tmp_path: Path, name: str) -> None:
        from app.store.compendium import is_vtt_payload

        assert not is_vtt_payload(tmp_path / name)

    def test_a_foundry_spell_file_contributes_nothing(self, tmp_path: Path) -> None:
        """The end-to-end check: a file whose key matches, excluded by name."""
        spells = tmp_path / "spells"
        spells.mkdir()
        (spells / "spells-hb.json").write_text(
            json.dumps({"spell": [{"name": "Real", "source": "HB", "level": 1, "school": "V"}]})
        )
        (spells / "foundry.json").write_text(
            json.dumps({"spell": [{"name": "Fake", "source": "HB", "level": 1, "school": "V"}]})
        )
        store = CompendiumStore()
        store.load(tmp_path)
        assert store.count("spell") == 1
        assert store.find("spell", "Real") is not None
        assert store.find("spell", "Fake") is None


@requires_corpus
class TestAgainstTheRealCorpus:
    """The claims that only real data can settle."""

    def test_every_class_record_loads(self, loaded_compendium: CompendiumStore) -> None:
        assert loaded_compendium.count("class") == 43
        assert loaded_compendium.count("subclass") == 367
        assert loaded_compendium.count("classFeature") == 691
        assert loaded_compendium.count("subclassFeature") == 1564
        assert loaded_compendium.count("legendaryGroup") == 191

    def test_no_validation_failures_and_no_unresolved_copies(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """The regression guard: a two-part copy index dropped 199 records."""
        report = loaded_compendium.report
        assert report.errors == []
        assert report.unresolved_copies == []
        assert report.unknown_copy_modes == set()

    def test_a_cross_class_copy_resolves(self, loaded_compendium: CompendiumStore) -> None:
        """The Artificer's Alchemist copies the one whose classSource differs.

        Identical ``(name, source)``, different record — this read as a cycle before the
        index learned about discriminators.
        """
        alchemist = loaded_compendium.get("subclass", "alchemist|tce|artificer|efa")
        assert alchemist.shortName == "Alchemist"
        assert alchemist.subclassFeatures

    def test_wizard_progression_matches_the_phb(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        wizard = loaded_compendium.get("class", "wizard|phb")
        assert wizard.hd == {"number": 1, "faces": 6}
        assert wizard.casterProgression == "full"
        assert wizard.spellcastingAbility == "int"
        assert wizard.preparedSpells == "<$level$> + <$int_mod$>"
        assert wizard.cantripProgression[:10] == [3, 3, 3, 4, 4, 4, 4, 4, 4, 5]
        assert wizard.subclass_level == 2

    @pytest.mark.parametrize(
        ("key", "level"),
        [
            ("cleric|phb", 1),
            ("sorcerer|phb", 1),
            ("warlock|phb", 1),
            ("druid|phb", 2),
            ("wizard|phb", 2),
            ("fighter|phb", 3),
            ("rogue|phb", 3),
        ],
    )
    def test_subclass_levels_are_inferred_correctly(
        self, loaded_compendium: CompendiumStore, key: str, level: int
    ) -> None:
        """Inferred from ``gainSubclassFeature``, never hardcoded per class."""
        assert loaded_compendium.get("class", key).subclass_level == level

    def test_spell_lists_resolve_for_almost_every_spell(self, corpus: Path) -> None:
        """Only subclass-only spells should lack a base class list."""
        tables = LookupTables()
        tables.load(corpus)
        raw = json.loads(
            (corpus / "generated/gendata-spell-source-lookup.json").read_text()
        )
        empty = [
            (name, source)
            for source, spells in raw.items()
            for name in spells
            if not tables.spell_sources.classes_for(name, source)
        ]
        # Those without a class list come some other way: a subclass (Graviturgy and
        # Chronurgy in 2.39, more in 2.41), a feat, a background or a species. None is lost.
        unreached = [
            (name, source)
            for name, source in empty
            if not set(raw[source][name]) - {"class", "classVariant"}
        ]
        assert unreached == []
        assert len(empty) <= 39

    def test_fireball_is_a_wizard_spell_and_not_a_cleric_one(self, corpus: Path) -> None:
        tables = LookupTables()
        tables.load(corpus)
        assert tables.spell_sources.has_class("Fireball", "phb", "Wizard", "PHB")
        assert not tables.spell_sources.has_class("Fireball", "phb", "Cleric", "PHB")
