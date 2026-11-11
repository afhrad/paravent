"""Specific magic items from generic variants (TECHSTACK.md §6b, phase 2)."""

from __future__ import annotations

from collections import Counter

from app.models.gamestate import SourceSet
from app.models.pc import PlayerCharacter
from app.services.sheet import build_sheet
from app.store.compendium import CompendiumStore
from app.store.magicvariants import applies, specific, variant_edition

PLUS_ONE = {
    "name": "+1 Weapon",
    "edition": "classic",
    "requires": [{"weapon": True}],
    "excludes": {"net": True},
    "inherits": {
        "namePrefix": "+1 ",
        "source": "DMG",
        "rarity": "uncommon",
        "bonusWeapon": "+1",
        "reprintedAs": ["+1 Weapon|XDMG"],
        "entries": ["You have a {=bonusWeapon} bonus to attack and damage rolls."],
    },
}
LONGSWORD = {
    "name": "Longsword",
    "source": "PHB",
    "edition": "classic",
    "type": "M",
    "weapon": True,
    "sword": True,
    "dmg1": "1d8",
    "dmgType": "S",
    "value": 1500,
    "srd": True,
    "page": 149,
}


class TestRules:
    def test_requires_excludes_and_edition(self) -> None:
        assert applies(PLUS_ONE, LONGSWORD)
        assert not applies(PLUS_ONE, {**LONGSWORD, "net": True})  # excluded
        assert not applies(PLUS_ONE, {**LONGSWORD, "weapon": None})  # not required kind
        assert not applies(PLUS_ONE, {**LONGSWORD, "edition": "one"})  # the other edition
        assert applies(PLUS_ONE, {k: v for k, v in LONGSWORD.items() if k != "edition"})

    def test_a_variant_without_an_edition_takes_its_books(self) -> None:
        assert variant_edition({"inherits": {"source": "XDMG"}}) == "one"
        assert variant_edition({"inherits": {"source": "EGW"}}) is None

    def test_list_values_match_any(self) -> None:
        variant = {"requires": [{"type": ["HA", "MA"]}], "inherits": {"source": "DMG"}}
        assert applies(variant, {"type": "HA"}) and not applies(variant, {"type": "LA"})

    def test_the_specific_item(self) -> None:
        item = specific(PLUS_ONE, LONGSWORD)
        assert item["name"] == "+1 Longsword" and item["source"] == "DMG"
        assert item["rarity"] == "uncommon" and item["bonusWeapon"] == "+1"
        assert item["dmg1"] == "1d8" and item["baseItem"] == "Longsword|PHB"
        assert item["entries"][0] == "You have a +1 bonus to attack and damage rolls."
        assert item["reprintedAs"] == ["+1 Longsword|XDMG"]  # the specific reprint
        assert "value" not in item and "srd" not in item and "page" not in item

    def test_placeholders_and_prices(self) -> None:
        variant = {
            "name": "Vicious Weapon",
            "requires": [{"weapon": True}],
            "inherits": {
                "nameSuffix": " of Doom",
                "source": "DMG",
                "valueExpression": "[[baseItem.value]] + 50000",
                "entries": ["An extra 7 {=dmgType} damage from this {=baseName/l}."],
            },
        }
        item = specific(variant, LONGSWORD)
        assert item["name"] == "Longsword of Doom" and item["value"] == 51500
        assert item["entries"][0] == "An extra 7 slashing damage from this longsword."


class TestCorpus:
    def test_the_items_are_made_once_each(self, loaded_compendium: CompendiumStore) -> None:
        report = loaded_compendium.report
        assert report is not None and report.variants_made > 3000 and not report.errors
        keys = Counter(
            f"{getattr(i, 'name', '')}|{getattr(i, 'source', '')}".lower()
            for i in loaded_compendium.all("item")
        )
        assert max(keys.values()) == 1
        assert loaded_compendium.count("magicvariant") == 230

    def test_each_world_gets_its_printing(self, loaded_compendium: CompendiumStore) -> None:
        resolver = loaded_compendium.resolver()

        def printing(books: list[str]) -> str:
            found = resolver.resolve_parts("item", "+1 Longsword", None, SourceSet(sources=books))
            return str(getattr(found.entry, "source", ""))

        assert printing(["PHB", "DMG"]) == "DMG"
        assert printing(["XPHB", "XDMG"]) == "XDMG"
        assert printing(["PHB", "DMG", "XPHB", "XDMG"]) == "XDMG"  # the reprint wins

    def test_a_variant_from_neither_rules_set_uses_the_2014_base(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        blade = loaded_compendium.find("item", "Acheron Blade Greatsword", "EGW")
        assert blade is not None and getattr(blade, "baseItem", "") == "Greatsword|PHB"

    def test_the_sheet_counts_the_bonus(self, loaded_compendium: CompendiumStore) -> None:
        pc = PlayerCharacter.model_validate(
            {
                "classes": [{"class": "Fighter|PHB", "level": 5}],
                "abilities": {"str": 16, "dex": 12, "con": 14},
                "inventory": [
                    {"item": "+1 Longsword|DMG", "equipped": True},
                    {"item": "+1 Plate Armor|DMG", "equipped": True},
                ],
            }
        )
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=["PHB", "DMG"]))
        sword = next(a for a in sheet.attacks if a.name == "+1 Longsword")
        assert sword.bonus.value == 3 + 3 + 1 and sword.damage == "1d8+4"
        assert sheet.armor_class.value == 18 + 1

    def test_a_generic_variant_is_an_entry_of_its_own(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        found = loaded_compendium.resolver().resolve_parts("magicvariant", "+1 Weapon", "DMG", None)
        assert found.entry is not None and getattr(found.entry, "rarity", None) == "uncommon"
