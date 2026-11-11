"""Tests for reference resolution (documentation/TECHSTACK.md §6c).

The hard problem is not finding an entry but knowing **which** entry a bare reference
means: 87% of spell tags name no source, and 379 of 557 spell names exist in more than
one book. These tests pin the four-step resolution and the two traps found building it —
``{@deity}`` putting its pantheon where every other tag puts the source, and feature
categories whose names legitimately repeat.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.models.gamestate import Adventure, SourceSet, World
from app.store.compendium import CompendiumStore
from app.store.reference import (
    COMPOSITE_KEY_CATEGORIES,
    Outcome,
    ReferenceIndex,
    ReferenceResolver,
    parse_reference,
    reprint_targets,
)
from tests.conftest import requires_corpus


@dataclass
class FakeEntry:
    """Minimal stand-in: resolution only reads name, source and reprintedAs."""

    name: str
    source: str
    reprintedAs: list[object] | None = None


def build(*entries: FakeEntry, category: str = "monster") -> ReferenceResolver:
    """A resolver over the given entries."""
    exact = {category: {f"{e.name.lower()}|{e.source.lower()}": e for e in entries}}
    index = ReferenceIndex()
    index.build(exact)
    return ReferenceResolver(index, exact)


class TestParseReference:
    """Tag payloads are positional, and one tag shifts the positions."""

    def test_bare_name(self) -> None:
        assert parse_reference("fireball") == ("fireball", None, None)

    def test_name_and_source(self) -> None:
        assert parse_reference("fireball|PHB") == ("fireball", "PHB", None)

    def test_empty_source_slot_means_unspecified(self) -> None:
        """``{@creature troll||trolls}`` — plural display, source left open."""
        assert parse_reference("troll||trolls") == ("troll", None, "trolls")

    def test_display_override(self) -> None:
        assert parse_reference("longbow|XPHB|long") == ("longbow", "XPHB", "long")

    def test_deity_puts_the_pantheon_in_slot_two(self) -> None:
        """``{@deity Bahamut|Nonhuman|PHB}`` — reading slot 2 as a source looks for a
        book called "Nonhuman"."""
        assert parse_reference("Bahamut|Nonhuman|PHB", "deity") == ("Bahamut", "PHB", None)

    def test_deity_display_is_slot_four(self) -> None:
        name, source, display = parse_reference("Bahamut|Nonhuman|PHB|the Platinum", "deity")
        assert (name, source, display) == ("Bahamut", "PHB", "the Platinum")

    def test_whitespace_is_stripped(self) -> None:
        assert parse_reference(" fireball | PHB ") == ("fireball", "PHB", None)


class TestReprintTargets:
    """The reprint graph, as the corpus actually encodes it."""

    def test_string_form(self) -> None:
        entry = FakeEntry("Allip", "MTF", reprintedAs=["Allip|MPMM"])
        assert reprint_targets(entry) == {"allip|mpmm"}

    def test_dict_form_with_uid(self) -> None:
        """69 cross-category reprints use ``{"tag": …, "uid": "Name|SRC"}``."""
        entry = FakeEntry(
            "Cult of Zariel", "MTF", reprintedAs=[{"tag": "boon", "uid": "Cult of Zariel|MPMM"}]
        )
        assert reprint_targets(entry) == {"cult of zariel|mpmm"}

    def test_absent_and_empty_are_empty(self) -> None:
        assert reprint_targets(FakeEntry("X", "MM")) == set()
        assert reprint_targets(FakeEntry("X", "MM", reprintedAs=[])) == set()

    def test_a_target_without_a_source_is_skipped(self) -> None:
        """A sourceless target cannot identify a specific printing."""
        assert reprint_targets(FakeEntry("X", "MM", reprintedAs=["Y"])) == set()


class TestStepOneExplicitSource:
    """An explicit source short-circuits the whole thing."""

    def test_exact_match(self) -> None:
        resolver = build(FakeEntry("Goblin", "MM"), FakeEntry("Goblin", "XMM"))
        result = resolver.resolve("monster", "Goblin|MM")
        assert result.outcome is Outcome.EXACT
        assert result.entry is not None and result.entry.source == "MM"

    def test_unknown_source_is_not_imported(self) -> None:
        resolver = build(FakeEntry("Goblin", "MM"))
        assert resolver.resolve("monster", "Goblin|VGM").outcome is Outcome.NOT_IMPORTED

    def test_an_explicit_source_still_honours_the_declaration(self) -> None:
        """Otherwise the set stops being a correctness boundary."""
        resolver = build(FakeEntry("Goblin", "MM"), FakeEntry("Goblin", "VGM"))
        result = resolver.resolve("monster", "Goblin|VGM", SourceSet(sources=["PHB", "MM"]))
        assert result.outcome is Outcome.NOT_IN_SOURCE_SET
        assert not result


class TestStepTwoSourceSetFilter:
    """Narrowing to the declared books."""

    def test_a_single_survivor_resolves(self) -> None:
        resolver = build(FakeEntry("Mind Sliver", "TCE"), FakeEntry("Mind Sliver", "XPHB"))
        result = resolver.resolve("monster", "Mind Sliver", SourceSet(sources=["TCE"]))
        assert result.outcome is Outcome.RESOLVED
        assert result.entry is not None and result.entry.source == "TCE"

    def test_nothing_declared_means_everything(self) -> None:
        resolver = build(FakeEntry("Goblin", "MM"))
        assert resolver.resolve("monster", "Goblin").outcome is Outcome.RESOLVED

    def test_excluded_candidates_give_the_not_in_books_state(self) -> None:
        """A correct outcome, not an error — DESIGN.md §6 renders it as inert text."""
        resolver = build(FakeEntry("Mind Flayer", "VGM"))
        result = resolver.resolve("monster", "Mind Flayer", SourceSet(sources=["PHB", "MM"]))
        assert result.outcome is Outcome.NOT_IN_SOURCE_SET
        assert result.candidates  # kept, so the UI can offer "add this book"

    def test_an_unknown_name_is_not_imported(self) -> None:
        resolver = build(FakeEntry("Goblin", "MM"))
        assert resolver.resolve("monster", "Nonesuch").outcome is Outcome.NOT_IMPORTED


class TestStepThreeSupersession:
    """``reprintedAs`` breaks ties, from data the corpus already carries."""

    def test_the_reprint_wins(self) -> None:
        resolver = build(
            FakeEntry("Allip", "MTF", reprintedAs=["Allip|MPMM"]),
            FakeEntry("Allip", "MPMM"),
        )
        result = resolver.resolve("monster", "Allip", SourceSet(sources=["MTF", "MPMM"]))
        assert result.outcome is Outcome.RESOLVED
        assert result.entry is not None and result.entry.source == "MPMM"

    def test_the_superseded_printing_stays_available(self) -> None:
        """Never silently drop a book the user declared."""
        resolver = build(
            FakeEntry("Fireball", "PHB", reprintedAs=["Fireball|XPHB"]),
            FakeEntry("Fireball", "XPHB"),
        )
        result = resolver.resolve("monster", "Fireball", SourceSet(sources=["PHB", "XPHB"]))
        assert [e.source for e in result.alternatives] == ["PHB"]

    def test_supersession_only_counts_inside_the_set(self) -> None:
        """A book the table lacks cannot supersede one it has."""
        resolver = build(
            FakeEntry("Allip", "MTF", reprintedAs=["Allip|MPMM"]),
            FakeEntry("Allip", "MPMM"),
        )
        result = resolver.resolve("monster", "Allip", SourceSet(sources=["MTF"]))
        assert result.outcome is Outcome.RESOLVED
        assert result.entry is not None and result.entry.source == "MTF"

    def test_a_cyclic_reprint_graph_does_not_resolve_to_nothing(self) -> None:
        resolver = build(
            FakeEntry("Loop", "A", reprintedAs=["Loop|B"]),
            FakeEntry("Loop", "B", reprintedAs=["Loop|A"]),
        )
        result = resolver.resolve("monster", "Loop", SourceSet(sources=["A", "B"]))
        assert result.outcome is Outcome.AMBIGUOUS
        assert len(result.candidates) == 2


class TestStepFourTiebreak:
    """Where the corpus records no reprint link, declaration order decides."""

    def test_declaration_order_breaks_the_tie(self) -> None:
        """136 names need this under a 2014 set, mostly deities and legendary groups."""
        resolver = build(FakeEntry("Angharradh", "SCAG"), FakeEntry("Angharradh", "MTF"))
        result = resolver.resolve("monster", "Angharradh", SourceSet(sources=["MTF", "SCAG"]))
        assert result.outcome is Outcome.AMBIGUOUS
        assert result.entry is not None and result.entry.source == "MTF"

    def test_an_entry_is_still_returned(self) -> None:
        """A corpus gap must surface as a choice, never as a dead reference."""
        resolver = build(FakeEntry("X", "A"), FakeEntry("X", "B"))
        result = resolver.resolve("monster", "X", SourceSet(sources=["A", "B"]))
        assert result.entry is not None
        assert result.is_unexpected, "flagged so the UI can offer the alternatives"

    def test_every_candidate_is_reported(self) -> None:
        resolver = build(FakeEntry("X", "A"), FakeEntry("X", "B"))
        result = resolver.resolve("monster", "X", SourceSet(sources=["A", "B"]))
        assert {e.source for e in result.candidates} == {"A", "B"}


class TestSourceSet:
    """The declaration itself."""

    def test_empty_is_unrestricted(self) -> None:
        assert SourceSet().is_unrestricted
        assert SourceSet().allows("anything")

    def test_identity_is_order_independent(self) -> None:
        """Caches key on this, so two spellings of one set must not make two keys."""
        a = SourceSet(sources=["PHB", "MM"])
        b = SourceSet(sources=["MM", "PHB"])
        assert a.identity == b.identity

    def test_unrestricted_identity_is_distinct(self) -> None:
        assert SourceSet().identity == "*"

    def test_a_missing_source_is_allowed(self) -> None:
        """Some corpus records carry none; excluding them would hide content."""
        assert SourceSet(sources=["PHB"]).allows(None)

    def test_matching_is_case_insensitive(self) -> None:
        assert SourceSet(sources=["phb"]).allows("PHB")

    @pytest.mark.parametrize(
        ("sources", "expected"),
        [
            (["PHB", "MM"], {"classic"}),
            (["XPHB", "XMM"], {"one"}),
            (["PHB", "XPHB"], {"classic", "one"}),
        ],
    )
    def test_edition_follows_from_the_books(
        self, sources: list[str], expected: set[str]
    ) -> None:
        """Edition is a consequence of the declaration, not a separate mode."""
        assert SourceSet(sources=sources).editions == expected

    def test_single_edition_detection(self) -> None:
        assert SourceSet(sources=["PHB", "MM"]).is_single_edition
        assert not SourceSet(sources=["PHB", "XPHB"]).is_single_edition

    def test_with_sources_deduplicates(self) -> None:
        assert SourceSet(sources=["PHB"]).with_sources("phb", "MM").sources == ["PHB", "MM"]


class TestSourceSetOnRecords:
    """Where the declaration lives: the world, with an adventure override."""

    def test_a_world_defaults_to_unrestricted(self) -> None:
        assert World(name="Aldmere").source_set.is_unrestricted

    def test_an_adventure_inherits_the_world(self) -> None:
        world = World(name="Aldmere", source_set=SourceSet(sources=["PHB", "MM"]))
        adventure = Adventure(world_id=world.id, name="LMoP")
        assert adventure.effective_source_set(world).sources == ["PHB", "MM"]

    def test_an_adventure_can_narrow(self) -> None:
        world = World(name="Aldmere", source_set=SourceSet(sources=["PHB", "MM", "VGM"]))
        adventure = Adventure(
            world_id=world.id, name="LMoP", source_set=SourceSet(sources=["PHB"])
        )
        assert adventure.effective_source_set(world).sources == ["PHB"]


class TestIndexScope:
    """Composite-key categories are not addressable by bare name."""

    def test_feature_categories_are_not_indexed(self) -> None:
        """"Potent Spellcasting" exists for several classes at several levels; nothing
        references one by name alone, and indexing them invents ambiguity."""
        entries = {
            "classFeature": {
                "potent spellcasting|xphb|cleric|8": FakeEntry("Potent Spellcasting", "XPHB")
            },
            "monster": {"goblin|mm": FakeEntry("Goblin", "MM")},
        }
        index = ReferenceIndex()
        index.build(entries)
        assert index.candidates("classFeature", "Potent Spellcasting") == []
        assert len(index.candidates("monster", "Goblin")) == 1

    def test_name_lookup_is_case_insensitive(self) -> None:
        index = ReferenceIndex()
        index.build({"monster": {"goblin|mm": FakeEntry("Goblin", "MM")}})
        assert index.candidates("monster", "GOBLIN")


@requires_corpus
class TestAgainstTheRealCorpus:
    """What only the real data can settle."""

    def test_the_index_covers_the_corpus(self, loaded_compendium: CompendiumStore) -> None:
        """8,239 distinct names, excluding the composite-key feature categories."""
        assert loaded_compendium.index.name_count() > 8000

    def test_a_bare_spell_reference_resolves(self, loaded_compendium: CompendiumStore) -> None:
        resolver = loaded_compendium.resolver()
        result = resolver.resolve("spell", "fireball", SourceSet(sources=["PHB"]))
        assert result.outcome is Outcome.RESOLVED
        assert result.entry is not None and result.entry.source == "PHB"

    def test_edition_split_resolves_per_declaration(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """The same bare tag, two tables, two correct answers."""
        resolver = loaded_compendium.resolver()
        classic = resolver.resolve("spell", "fireball", SourceSet(sources=["PHB"]))
        modern = resolver.resolve("spell", "fireball", SourceSet(sources=["XPHB"]))
        assert classic.entry is not None and classic.entry.source == "PHB"
        assert modern.entry is not None and modern.entry.source == "XPHB"

    def test_both_editions_prefers_the_reprint_and_keeps_the_original(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """5eTools models 2024 as reprinting 2014, so the newer entry wins — but the
        declared older printing stays offerable."""
        resolver = loaded_compendium.resolver()
        result = resolver.resolve("spell", "fireball", SourceSet(sources=["PHB", "XPHB"]))
        assert result.entry is not None and result.entry.source == "XPHB"
        assert "PHB" in {e.source for e in result.alternatives}

    def test_a_deity_reference_parses_and_resolves(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """The pantheon-in-slot-2 trap, end to end."""
        resolver = loaded_compendium.resolver()
        result = resolver.resolve("deity", "Bahamut|Nonhuman|PHB")
        assert result.entry is not None
        assert result.entry.source == "PHB"

    def test_a_book_outside_the_set_stays_inert(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """Running PHB+MM must not quietly pull a monster from Volo's."""
        resolver = loaded_compendium.resolver()
        result = resolver.resolve(
            "monster", "Alhoon|VGM", SourceSet(sources=["PHB", "MM"])
        )
        assert result.outcome is Outcome.NOT_IN_SOURCE_SET
        assert not result

    def test_every_reference_yields_an_entry(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """The invariant that matters: no dead links, whatever the declaration.

        Where the corpus records no reprint link the result is flagged AMBIGUOUS with
        every candidate attached, but it still carries an entry.
        """
        resolver = loaded_compendium.resolver()
        declared = SourceSet(
            sources=["PHB", "MM", "DMG", "XGE", "TCE", "MPMM", "VGM", "MTF", "SCAG"]
        )
        dead = []
        for category in loaded_compendium.categories():
            if category in COMPOSITE_KEY_CATEGORIES:
                # Not addressable by bare name, by design — a feature is reached
                # through its parent class, never by a name that repeats per level.
                continue
            for model in loaded_compendium.all(category):
                # What a tag names it by: the name, or the code of an item property.
                name = getattr(model, "ref_name", None) or getattr(model, "name", None)
                if not name or not declared.allows(getattr(model, "source", None)):
                    continue
                if not resolver.resolve_parts(category, name, None, declared):
                    dead.append((category, name))
        assert dead == [], f"{len(dead)} references resolved to nothing"
