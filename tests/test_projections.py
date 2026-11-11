"""Typed projections over polymorphic corpus fields, and the filter API over them."""

from __future__ import annotations

from fractions import Fraction

import pytest

from app.models.gamestate import SourceSet
from app.store.compendium import CompendiumStore
from app.store.projections import (
    coerce_codes,
    coerce_cr,
    coerce_type,
    first_code,
    project_item,
    project_monster,
    project_spell,
)
from app.store.query import MonsterQuery


class TestChallengeRating:
    """``cr`` is a string, a dict with variants, ``"Unknown"``, or absent."""

    @pytest.mark.parametrize(
        ("raw", "value", "xp"),
        [
            ("1/4", Fraction(1, 4), 50),
            ("1/8", Fraction(1, 8), 25),
            ("0", Fraction(0), 10),
            ("17", Fraction(17), 18000),
        ],
    )
    def test_a_plain_string(self, raw: str, value: Fraction, xp: int) -> None:
        cr = coerce_cr(raw)
        assert cr is not None and cr.value == value and cr.text == raw and cr.xp == xp

    def test_lair_and_coven_variants(self) -> None:
        lair = coerce_cr({"cr": "5", "lair": "6"})
        coven = coerce_cr({"cr": "5", "coven": "7"})
        assert lair is not None and lair.value == 5 and lair.lair == 6
        assert coven is not None and coven.coven == 7

    def test_an_xp_override_beats_the_table(self) -> None:
        cr = coerce_cr({"cr": "1", "xp": 100})
        assert cr is not None and cr.xp == 100

    @pytest.mark.parametrize("raw", [None, "Unknown", "—", {"cr": "Unknown"}])
    def test_no_usable_rating(self, raw: object) -> None:
        assert coerce_cr(raw) is None


class TestCreatureType:
    def test_a_string(self) -> None:
        assert coerce_type("Dragon").types == ("dragon",)

    def test_tags_mix_strings_and_objects(self) -> None:
        kind = coerce_type(
            {"type": "humanoid", "tags": ["goblinoid", {"tag": "elf", "prefix": "high"}]}
        )
        assert kind.types == ("humanoid",)
        assert kind.tags == ("goblinoid", "high elf")
        assert kind.text == "humanoid (goblinoid, high elf)"

    def test_a_choice_of_types(self) -> None:
        kind = coerce_type({"type": {"choose": ["celestial", "fiend"]}, "tags": []})
        assert kind.types == ("celestial", "fiend")
        assert kind.text == "celestial or fiend"

    def test_a_swarm(self) -> None:
        assert coerce_type({"type": "beast", "swarmSize": "T"}).swarm_size == "T"

    def test_absent(self) -> None:
        assert coerce_type(None).types == ()


class TestSmallCoercions:
    def test_codes(self) -> None:
        assert coerce_codes(["M", "S"]) == ("M", "S")
        assert coerce_codes("L") == ("L",)
        assert coerce_codes(None) == ()

    def test_first_code_drops_the_source(self) -> None:
        assert first_code("M|XPHB") == "M"
        assert first_code("$G|DMG") == "$G"
        assert first_code(None) == ""


class TestProjectFromDicts:
    def test_monster(self) -> None:
        m = project_monster(
            {
                "name": "Goblin",
                "source": "MM",
                "cr": "1/4",
                "type": {"type": "humanoid", "tags": ["goblinoid"]},
                "size": ["S"],
                "environment": ["Forest", "hill"],
                "ac": [{"ac": 15, "from": ["leather armor", "shield"]}],
                "hp": {"average": 7, "formula": "2d6"},
            }
        )
        assert (m.cr and m.cr.value) == Fraction(1, 4)
        assert m.sizes == ("S",) and m.environments == ("forest", "hill")
        assert (m.ac, m.hp, m.legendary) == (15, 7, False)

    def test_summon_ac_is_prose(self) -> None:
        m = project_monster({"name": "X", "source": "T", "ac": [{"special": "11 + level"}]})
        assert m.ac is None and m.cr is None

    def test_spell(self) -> None:
        s = project_spell(
            {
                "name": "Fly",
                "source": "PHB",
                "level": 3,
                "school": "T",
                "duration": [{"type": "timed", "concentration": True}],
                "meta": {"ritual": False},
            }
        )
        assert (s.level, s.school, s.concentration, s.ritual) == (3, "T", True, False)

    def test_item_magic_follows_rarity(self) -> None:
        mundane = project_item({"name": "Rope", "source": "PHB", "rarity": "none"}, "item")
        wand = project_item(
            {"name": "Wand", "source": "DMG", "type": "WD|DMG", "rarity": "rare"}, "item"
        )
        assert not mundane.magic
        assert wand.magic and wand.kind == "WD"


class TestAgainstTheCorpus:
    """Every entry projects; the filters agree with the fields they filter on."""

    def test_every_monster_spell_and_item_projects(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        p = loaded_compendium.projections
        assert len(p.monsters) == loaded_compendium.count("monster")
        assert len(p.spells) == loaded_compendium.count("spell")
        assert len(p.items) == loaded_compendium.count("item") + loaded_compendium.count("baseitem")

    def test_only_unrated_creatures_lack_a_cr(self, loaded_compendium: CompendiumStore) -> None:
        """Summons and sidekicks carry no CR, plus one written "Unknown" and one "None"."""
        unrated = loaded_compendium.monsters().where(lambda m: m.cr is None).all()
        assert 90 <= len(unrated) <= 120
        for m in unrated:
            raw = m.entry.model_dump(by_alias=True).get("cr")
            text = raw.get("cr") if isinstance(raw, dict) else raw
            assert text in (None, "Unknown", "None")

    def test_every_creature_has_a_type_and_a_size(self, loaded_compendium: CompendiumStore) -> None:
        for m in loaded_compendium.monsters():
            assert m.kind.types, m.name
            assert m.sizes, m.name

    def test_cr_between_and_type(self, loaded_compendium: CompendiumStore) -> None:
        hits = loaded_compendium.monsters().cr_between(3, 7).of_type("dragon").all()
        assert hits
        for m in hits:
            assert m.cr is not None and 3 <= m.cr.value <= 7
            assert "dragon" in m.kind.types

    def test_fractional_bounds(self, loaded_compendium: CompendiumStore) -> None:
        weak = loaded_compendium.monsters().cr_between("1/8", "1/4").from_source("MM").all()
        assert {m.name for m in weak} >= {"Goblin", "Kobold"}

    def test_a_choice_type_matches_either(self, loaded_compendium: CompendiumStore) -> None:
        either = loaded_compendium.monsters().where(lambda m: len(m.kind.types) > 1).all()
        assert either
        assert all(
            loaded_compendium.monsters()
            .of_type(m.kind.types[1])
            .where(lambda x, name=m.name: x.name == name)
            .count()
            for m in either
        )

    def test_spell_filters(self, loaded_compendium: CompendiumStore) -> None:
        hits = loaded_compendium.spells().level(3).school("V").from_source("PHB").all()
        assert "Fireball" in {s.name for s in hits}
        assert all(s.level == 3 and s.school == "V" for s in hits)
        assert all(s.ritual for s in loaded_compendium.spells().ritual())

    def test_item_filters(self, loaded_compendium: CompendiumStore) -> None:
        wands = loaded_compendium.items().kind("WD").magic().rarity("rare").all()
        assert wands and all(i.kind == "WD" and i.rarity == "rare" for i in wands)


class TestInBooks:
    """The source set applies to browsing exactly as it does to resolving a tag."""

    def test_both_editions_get_the_reprint(self, loaded_compendium: CompendiumStore) -> None:
        both = SourceSet(sources=["PHB", "XPHB"])
        fireball = loaded_compendium.spells().named("fireball").in_books(both).all()
        assert [(s.name, s.source) for s in fireball if s.name == "Fireball"] == [
            ("Fireball", "XPHB")
        ]

    def test_one_edition_keeps_its_own(self, loaded_compendium: CompendiumStore) -> None:
        phb = SourceSet(sources=["PHB"])
        assert {s.source for s in loaded_compendium.spells().in_books(phb)} == {"PHB"}

    def test_a_book_outside_the_set_cannot_supersede(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """MTF's Allip was reprinted in MPMM; a table without MPMM keeps MTF's."""
        mtf = loaded_compendium.monsters().named("allip").in_books(SourceSet(sources=["MTF"]))
        both = (
            loaded_compendium.monsters().named("allip").in_books(SourceSet(sources=["MTF", "MPMM"]))
        )
        assert [m.source for m in mtf] == ["MTF"]
        assert [m.source for m in both] == ["MPMM"]

    def test_queries_are_immutable(self, loaded_compendium: CompendiumStore) -> None:
        base: MonsterQuery = loaded_compendium.monsters()
        dragons = base.of_type("dragon")
        assert base.count() > dragons.count() > 0

    def test_projections_follow_a_reload(self, corpus: object) -> None:
        store = CompendiumStore()
        store.load(corpus)  # type: ignore[arg-type]
        first = store.projections
        store.load(corpus)  # type: ignore[arg-type]
        assert store.projections is not first
