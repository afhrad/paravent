"""Whose feature a class's feature reference names (app/services/class_refs.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.gamestate import SourceSet
from app.models.pc import ChosenOption, ClassLevel, PlayerCharacter
from app.services.chargen import Catalogue
from app.services.class_refs import (
    feature_owners,
    feature_refs,
    files_under,
    is_variant,
    subclass_owner,
)
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables

# A class made on the 2024 paladin's frame: its own features beside borrowed ones.
KNIGHT = {
    "name": "Knight",
    "source": "Homebrew",
    "classFeatures": [
        "Vow|Knight|Homebrew|1",
        "Spellcasting|Paladin|XPHB|1",
        {"classFeature": "Knight Subclass|Knight|Homebrew|3", "gainSubclassFeature": True},
        "Steed|Paladin|XPHB|5|Homebrew",
        "Cantrip Formulas|Knight|Homebrew|3|TCE",
    ],
    "includeSubclassesFrom": ["Paladin|XPHB"],
}


class TestFeatureRefs:
    def test_a_borrowed_feature_is_found_under_its_owner(self) -> None:
        refs = feature_refs(KNIGHT)
        spellcasting = refs[1]
        assert (spellcasting.class_name, spellcasting.class_source) == ("paladin", "xphb")
        assert spellcasting.matches(
            {"name": "Spellcasting", "level": 1, "className": "Paladin", "classSource": "XPHB"}
        )
        assert not spellcasting.matches(
            {"name": "Spellcasting", "level": 1, "className": "Knight", "classSource": "Homebrew"}
        )
        assert refs[2].gains_subclass and refs[0].name == "Vow"

    def test_the_book_counts_only_where_written(self) -> None:
        steed = feature_refs(KNIGHT)[3]
        record = {"name": "Steed", "level": 5, "className": "Paladin", "classSource": "XPHB"}
        assert steed.matches({**record, "source": "Homebrew"})
        assert not steed.matches({**record, "source": "XPHB"})
        assert feature_refs(KNIGHT)[1].matches({**record, "name": "Spellcasting", "level": 1})

    def test_variants_are_from_neither_the_class_nor_the_owner(self) -> None:
        refs = feature_refs(KNIGHT)
        assert [is_variant(r, KNIGHT) for r in refs] == [False, False, False, False, True]

    def test_the_owners(self) -> None:
        assert feature_owners(KNIGHT) == {("knight", "homebrew"), ("paladin", "xphb")}


class TestSubclassOwner:
    def test_a_copied_subclass_keeps_the_original_features(self) -> None:
        sub = {
            "name": "Path of the Berserker",
            "shortName": "Berserker",
            "source": "Homebrew",
            "subclassFeatures": ["Path of the Berserker|Barbarian|XPHB|Berserker|XPHB|3"],
        }
        owner = subclass_owner(sub, "Raider", "Homebrew")
        assert owner == ("barbarian", "xphb", "berserker", "xphb")
        assert files_under(
            {
                "className": "Barbarian",
                "classSource": "XPHB",
                "subclassShortName": "Berserker",
                "subclassSource": "XPHB",
            },
            owner,
        )

    def test_an_empty_source_is_the_phb(self) -> None:
        """The 2014 subclasses are filed under the 2024 classes too, their references
        still naming the PHB class's features."""
        sub = {
            "name": "Path of the Berserker",
            "shortName": "Berserker",
            "source": "PHB",
            "subclassFeatures": ["Path of the Berserker|Barbarian||Berserker||3"],
        }
        owner = ("barbarian", "phb", "berserker", "phb")
        assert subclass_owner(sub, "Barbarian", "XPHB") == owner
        assert (
            feature_refs({"name": "Barbarian", "classFeatures": ["Rage|Barbarian||1"]})[
                0
            ].class_source
            == "phb"
        )

    def test_no_references_mean_its_own(self) -> None:
        bare = {"name": "Oath", "source": "Homebrew"}
        owner = ("knight", "homebrew", "oath", "homebrew")
        assert subclass_owner(bare, "Knight", "Homebrew") == owner


@pytest.fixture(scope="module")
def lookups(corpus: Path) -> LookupTables:
    tables = LookupTables()
    tables.load(corpus)
    return tables


class TestAgainstTheCorpus:
    def test_every_class_feature_reference_finds_its_record(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """A reference that finds nothing shows its feature without text on the sheet."""
        features = [
            f.model_dump(by_alias=True, exclude_none=True)
            for f in loaded_compendium.all("classFeature")
        ]
        lost = [
            f"{cls.name}|{cls.source}: {ref.name} {ref.level}"
            for cls in loaded_compendium.all("class")
            for ref in feature_refs(cls.model_dump(by_alias=True, exclude_none=True))
            if not any(ref.matches(f) for f in features)
        ]
        assert lost == []

    def test_every_subclass_has_its_features(self, loaded_compendium: CompendiumStore) -> None:
        features = [
            f.model_dump(by_alias=True, exclude_none=True)
            for f in loaded_compendium.all("subclassFeature")
        ]
        empty = []
        for sub in loaded_compendium.all("subclass"):
            data = sub.model_dump(by_alias=True, exclude_none=True)
            if not data.get("subclassFeatures"):
                continue
            owner = subclass_owner(data, data["className"], data.get("classSource", "phb"))
            if not any(files_under(f, owner) for f in features):
                empty.append(f"{sub.name}|{sub.source}")
        assert empty == []

    def test_a_class_offers_the_subclasses_it_takes_from_another(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        cat = Catalogue(loaded_compendium, SourceSet(), lookups)
        borrowers = [o for o in cat.classes() if o.data.get("includeSubclassesFrom")]
        assert borrowers  # every class copied from another's frame has its hit die
        for cls in borrowers:
            offered = {
                (str(o.data["className"]), str(o.data["classSource"])) for o in cat.subclasses(cls)
            }
            for ref in cls.data["includeSubclassesFrom"]:
                name, _, source = ref.partition("|")
                assert (name, source) in offered, (cls.reference, ref)

    def test_a_2024_class_with_a_2014_subclass_has_its_features(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        pc = PlayerCharacter(
            name="Grada",
            classes=[
                ClassLevel(
                    name="Barbarian",
                    source="XPHB",
                    level=6,
                    subclass=ChosenOption(name="Path of the Berserker", source="PHB"),
                )
            ],
        )
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=["XPHB", "PHB"]))
        names = {f.name for f in sheet.features if f.kind == "subclass"}
        assert {"Frenzy", "Mindless Rage"} <= names

    def test_a_class_on_anothers_frame_shows_every_feature_with_its_text(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        borrowers = [
            c
            for c in loaded_compendium.all("class")
            if c.model_dump(by_alias=True).get("includeSubclassesFrom")
        ]
        for cls in borrowers:
            pc = PlayerCharacter(
                name="T", classes=[ClassLevel(name=cls.name, source=cls.source, level=2)]
            )
            sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet())
            bare = [f.name for f in sheet.features if f.kind == "class" and not f.entries]
            assert bare == [], cls.name
        assert borrowers

    def test_a_borrowed_caster_subclass_brings_its_spell_list(
        self, loaded_compendium: CompendiumStore, lookups: LookupTables
    ) -> None:
        """An Arcane Trickster taken by a class on the rogue's frame chooses from the
        list the lookup files under the rogue."""
        cat = Catalogue(loaded_compendium, SourceSet(), lookups)
        checked = 0
        for cls in (o for o in cat.classes() if o.data.get("includeSubclassesFrom")):
            for sub in cat.subclasses(cls):
                home = cat.find("class", f"{sub.data['className']}|{sub.data['classSource']}")
                if not sub.data.get("casterProgression") or home is None or home is cls:
                    continue
                assert len(cat.class_spells(cls, 1, sub)) == len(cat.class_spells(home, 1, sub)), (
                    cls.reference,
                    sub.reference,
                )
                checked += 1
        assert checked
