"""The rest of the item model: shared item text ({#itemEntry}), weapon properties and
item types as entries (TECHSTACK.md §6b phase 2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.i18n import LANG_COOKIE, UiCatalogue
from app.services.sheet import property_codes
from app.store.compendium import CompendiumStore
from app.store.item_entries import ItemEntries
from app.store.registry import StoreRegistry
from app.ui.tags import localized_context, ref_url, render

PASSWORD = "correct horse battery"

TATTOO = ["Designs in one color ({{item.detail1}}).", "Resistance to {{item.resist}} damage."]


class TestItemEntries:
    def test_a_reference_becomes_the_text_with_its_gaps_filled(self) -> None:
        shared = ItemEntries({"absorbing tattoo|tce": TATTOO})
        item = {"name": "Acid Tattoo", "resist": ["acid"], "detail1": "green"}
        item["entries"] = ["Before.", "{#itemEntry Absorbing Tattoo|TCE}", "After."]
        assert shared.expand(item)["entries"] == [
            "Before.",
            "Designs in one color (green).",
            "Resistance to acid damage.",
            "After.",
        ]
        assert not shared.missing and not shared.unfilled

    def test_no_source_means_the_dmg_and_a_variant_fills_from_inherits(self) -> None:
        shared = ItemEntries(
            {"armor of resistance|dmg": ["Resistance to {{getFullImmRes item.resist}}."]}
        )
        variant: dict[str, Any] = {
            "name": "Armor of Resistance",
            "inherits": {
                "resist": ["acid", "cold", "fire"],
                "entries": ["{#itemEntry Armor of Resistance}"],
            },
        }
        expanded = shared.expand(variant)
        assert expanded["inherits"]["entries"] == ["Resistance to acid, cold, and fire."]

    def test_what_cannot_be_filled_stays_visible(self) -> None:
        shared = ItemEntries({"absorbing tattoo|tce": TATTOO})
        item = {
            "name": "Odd",
            "entries": ["{#itemEntry Absorbing Tattoo|TCE}", "{#itemEntry Absent|ZZ}"],
        }
        entries = shared.expand(item)["entries"]
        assert "{{item.detail1}}" in entries[0] and entries[-1] == "{#itemEntry Absent|ZZ}"
        assert shared.missing == {"Absent|ZZ"}
        assert shared.unfilled == {"Odd: detail1", "Odd: resist"}

    def test_an_item_without_a_reference_is_the_same_dict(self) -> None:
        item = {"name": "Rope", "entries": ["Hempen."]}
        assert ItemEntries({}).expand(item) is item


class TestCorpus:
    def test_every_reference_is_expanded(self, loaded_compendium: CompendiumStore) -> None:
        report = loaded_compendium.report
        assert not report.missing_item_entries and not report.unfilled_item_entries
        assert not any("{#itemEntry" in m.model_dump_json() for _c, m in loaded_compendium)
        tattoo = loaded_compendium.get("item", "acid absorbing tattoo|tce")
        assert "one color (green)" in str(tattoo.entries[0])

    def test_properties_and_types_are_keyed_by_their_code(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        versatile = loaded_compendium.get("itemProperty", "v|xphb")
        assert versatile.name == "Versatile" and versatile.template
        assert loaded_compendium.get("itemProperty", "s|phb").name == "special"
        # A type copying another by its code: Vehicle (Air) takes the ship's rules.
        air = loaded_compendium.get("itemType", "air|dmg")
        assert air.name == "Vehicle (Air)" and air.entries
        resolved = loaded_compendium.resolver().resolve_parts("itemProperty", "V", None)
        assert resolved.entry is not None and resolved.entry.source == "XPHB"

    def test_the_lance_keeps_two_handed(self) -> None:
        lance = {"property": [{"uid": "2H|XPHB", "note": "unless mounted"}, "H", "R|XPHB"]}
        assert property_codes(lance) == [("2H", "XPHB"), ("H", "PHB"), ("R", "XPHB")]


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> StoreRegistry:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    return registry


def _html(registry: StoreRegistry, text: str) -> str:
    ctx = localized_context(
        UiCatalogue().translator("en"), resolver=registry.compendium.resolver(), source_set=None
    )
    return "".join(str(n) for n in render(text, ctx))


class TestPages:
    def test_property_tags_link(self, registry: StoreRegistry) -> None:
        html = _html(registry, "{@itemProperty V|XPHB|Versatile} and {@itemProperty F}")
        assert 'href="/ref/itemProperty/XPHB/Versatile"' in html
        assert ">Finesse</a>" in html  # a code with no display text shows the name

    @pytest.fixture
    async def client(self, registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
        registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
        anna = registry.auth.create_user("anna", PASSWORD, Role.GAMEMASTER)
        registry.auth.set_language(anna.id, "de")  # as the switch does: metres with it
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    async def test_property_and_type_pages(self, client: AsyncClient) -> None:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        page = await client.get(ref_url("itemProperty", "Versatile", "XPHB"))
        assert page.status_code == 200 and "Weapon property" in page.text
        assert (await client.get(ref_url("itemType", "Ammunition", "XPHB"))).status_code == 200

    async def test_an_item_prints_its_properties_through_their_templates(
        self, client: AsyncClient
    ) -> None:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        sword = (await client.get(ref_url("baseitem", "Longsword", "XPHB"))).text
        assert '/ref/itemProperty/XPHB/Versatile"' in sword and "(1d10)" in sword
        bow = (await client.get(ref_url("baseitem", "Longbow", "XPHB"))).text
        assert "Range 150/600" in bow and "/ref/baseitem/XPHB/Arrow" in bow
        # A type links where it has rules of its own: Ranged Weapon does, Ammunition not.
        assert '/ref/itemType/XPHB/Ranged%20Weapon"' in bow
        arrows = (await client.get(ref_url("baseitem", "Arrows (20)", "XPHB"))).text
        assert "/ref/itemType/" not in arrows

    async def test_in_german_and_metres(self, client: AsyncClient) -> None:
        client.cookies.set(LANG_COOKIE, "de")
        await client.post("/login", data={"username": "anna", "password": PASSWORD})
        bow = (await client.get(ref_url("baseitem", "Longbow", "XPHB"))).text
        assert "Geschosse" in bow and "Reichweite 45/180" in bow
