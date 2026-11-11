"""Statblock facts in the reader's language (app/ui/rules_text.py) — against the real corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.glossary import Glossary
from app.services.i18n import LANG_COOKIE, UiCatalogue
from app.services.sheet import spell_view
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry
from app.ui import rules_text as rt
from app.ui.context import Page

PASSWORD = "correct horse battery"


def _page(lang: str, glossary: Glossary | None = None, units: str = "feet") -> Page:
    page = Page(t=UiCatalogue().translator(lang, glossary))
    if units == "feet":
        return page
    # Page.u reads only the user's unit setting: a stand-in identity carries it.
    reader = SimpleNamespace(user=SimpleNamespace(units=units))
    return Page(t=page.t, identity=reader)  # type: ignore[arg-type]


def _monster(store: CompendiumStore, key: str) -> dict[str, Any]:
    data: dict[str, Any] = store.get("monster", key).model_dump(by_alias=True, exclude_none=True)
    return data


def _spell(store: CompendiumStore, key: str) -> Any:
    return spell_view(store.get("spell", key).model_dump(by_alias=True, exclude_none=True))


class TestCreatures:
    def test_the_type_line(self, loaded_compendium: CompendiumStore) -> None:
        goblin = _monster(loaded_compendium, "goblin|mm")
        assert rt.creature_type(_page("en"), goblin) == "Small humanoid (goblinoid), neutral evil"
        assert (
            rt.creature_type(_page("de"), goblin) == "Kleiner Humanoider (Goblinoid), neutral böse"
        )
        owlbear = _monster(loaded_compendium, "owlbear|mm")
        # the size agrees with the type's gender
        assert rt.creature_type(_page("de"), owlbear) == "Große Monstrosität, gesinnungslos"

    def test_any_evil_is_one_phrase(self, loaded_compendium: CompendiumStore) -> None:
        lich = _monster(loaded_compendium, "lich|mm")
        assert rt.creature_type(_page("en"), lich).endswith("undead, any evil alignment")
        assert rt.creature_type(_page("de"), lich).endswith("Untoter, jede böse Gesinnung")

    def test_speed_senses_and_challenge(self, loaded_compendium: CompendiumStore) -> None:
        dragon = _monster(loaded_compendium, "adult red dragon|mm")
        assert rt.speed(_page("en"), dragon) == "40 ft., climb 40 ft., fly 80 ft."
        metric_de = _page("de", units="metres")
        assert rt.speed(metric_de, dragon) == "12 m, Klettern 12 m, Fliegen 24 m"
        assert rt.senses(metric_de, dragon) == [
            "Blindsicht 18 m",
            "Dunkelsicht 36 m",
            "passive Wahrnehmung 23",
        ]
        assert rt.challenge(_page("en"), dragon) == "17 (18,000 XP)"
        assert rt.challenge(_page("de"), dragon) == "17 (18.000 EP)"
        assert rt.saves(_page("de"), dragon) == "GE +6, KO +13, WE +7, CH +11"
        assert rt.skills(_page("de"), dragon) == "Wahrnehmung +13, Heimlichkeit +6"

    def test_names_from_the_glossary(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        glossary = Glossary(tmp_path)
        glossary.set("de", "language", "Common", "Gemeinsprache")
        glossary.set("de", "race", "Elf", "Elf")
        goblin = _monster(loaded_compendium, "goblin|mm")
        assert rt.languages(_page("de", glossary), goblin["languages"]) == [
            "Gemeinsprache",
            "Goblin",
        ]

    def test_legendary_sentence(self, loaded_compendium: CompendiumStore) -> None:
        dragon = _monster(loaded_compendium, "adult red dragon|mm")
        english = rt.legendary_header(_page("en"), dragon, "Adult Red Dragon")
        assert english and english[0].startswith(
            "The adult red dragon can take 3 legendary actions"
        )
        german = rt.legendary_header(_page("de"), dragon, "Adult Red Dragon")
        assert german and "kann 3 legendäre Aktionen ausführen" in german[0]


class TestSpells:
    @pytest.mark.parametrize(
        ("key", "english", "german"),
        [
            (
                "fireball|phb",
                ("Evocation", "Action", "150 ft.", "Instant"),
                ("Hervorrufung", "Aktion", "150 ft.", "Unmittelbar"),
            ),
            (
                "burning hands|phb",
                ("Evocation", "Action", "Self (15 ft. cone)", "Instant"),
                ("Hervorrufung", "Aktion", "Selbst (Kegel von 15 ft.)", "Unmittelbar"),
            ),
            (
                "contingency|phb",
                ("Evocation", "10 minutes", "Self", "10 days"),
                ("Hervorrufung", "10 Minuten", "Selbst", "10 Tage"),
            ),
            (
                "shield|phb",
                ("Abjuration", "Reaction", "Self", "1 round"),
                ("Bannmagie", "Reaktion", "Selbst", "1 Runde"),
            ),
            (
                "glyph of warding|phb",
                ("Abjuration", "1 hour", "Touch", "Until dispelled or triggered"),
                (
                    "Bannmagie",
                    "1 Stunde",
                    "Berührung",
                    "Bis der Zauber gebannt oder ausgelöst wird",
                ),
            ),
        ],
    )
    def test_facts(
        self,
        loaded_compendium: CompendiumStore,
        key: str,
        english: tuple[str, ...],
        german: tuple[str, ...],
    ) -> None:
        spell = _spell(loaded_compendium, key)
        for lang, expected in (("en", english), ("de", german)):
            page = _page(lang)
            got = (
                rt.school(page, spell.school_code),
                rt.spell_time(page, spell),
                rt.spell_range(page, spell),
                rt.spell_duration(page, spell),
            )
            assert got == expected, lang

    def test_metres_follow_the_unit_setting(self, loaded_compendium: CompendiumStore) -> None:
        spell = _spell(loaded_compendium, "burning hands|phb")
        assert rt.spell_range(_page("de", units="metres"), spell) == "Selbst (Kegel von 4,5 m)"
        assert rt.spell_range(_page("en", units="metres"), spell) == "Self (4.5 m cone)"


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, language="de")
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={LANG_COOKIE: "de"}
    ) as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestPages:
    async def test_a_german_statblock(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _registry = client
        page = (await ac.get("/ref/monster/MM/Lich")).text
        assert "Mittelgroßer Untoter, jede böse Gesinnung" in page
        assert "Wahrsicht" in page and "passive Wahrnehmung 19" in page
        assert "21 (33.000 EP)" in page
        assert "Vergiftet" in page  # a condition immunity, by the catalogue's name
        assert "Gift" in page  # a damage immunity
        assert "17 (natürliche Rüstung)" in page
        # labels the renderer prints itself, around text that may still be English
        assert "Nahkampf-Zauberangriff:" in page and "Treffer:" in page and "SG 18" in page
        assert "Melee Spell Attack:" not in page

    async def test_a_german_spell_and_search(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _registry = client
        page = (await ac.get("/ref/spell/PHB/Fireball")).text
        assert "Grad 3 Hervorrufung" in page and "Unmittelbar" in page and "Aktion" in page
        found = (
            await ac.get("/search", params={"q": "goblin", "group": "bestiary", "partial": "1"})
        ).text
        assert "Kleiner Humanoider (Goblinoid)" in found and "RK 15" in found


class TestDice:
    def test_the_letter_changes_and_words_do_not(self) -> None:
        from app.ui.tags import dice_text

        assert dice_text("2d10 + 8", "W") == "2W10 + 8"
        assert dice_text("d20, 1d6 and 3d8", "W") == "W20, 1W6 and 3W8"
        assert dice_text("hard 12d6", "W") == "hard 12W6"  # "hard" keeps its d
        assert dice_text("bad3", "W") == "bad3"
        assert dice_text("2d10 + 8", "d") == "2d10 + 8"

    async def test_chips_show_w_and_roll_d(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _registry = client
        page = (await ac.get("/ref/monster/MM/Adult%20Red%20Dragon")).text
        assert "19W12 + 133</button>" in page  # shown with W
        assert 'data-roll="19d12+133"' in page  # rolled, and titled, as the formula
        await ac.post("/language", data={"lang": "en", "next": "/"})
        english = (await ac.get("/ref/monster/MM/Adult%20Red%20Dragon")).text
        assert "19d12 + 133" in english and "19W12" not in english


class TestSheetWords:
    """Training, languages, tools and feature origins on the sheet (app/ui/names.py)."""

    def test_training_in_german_and_named_weapons_by_their_item(
        self, loaded_compendium: CompendiumStore, tmp_path: Path
    ) -> None:
        from app.models.pc import PlayerCharacter
        from app.services.sheet import build_sheet
        from app.ui.names import training_text

        rogue = PlayerCharacter.model_validate({"classes": [{"class": "Rogue|PHB", "level": 1}]})
        sheet = build_sheet(rogue, loaded_compendium)
        assert sheet.training_items["hand crossbows"] == "hand crossbow"
        glossary = Glossary(tmp_path)
        glossary.set("de", "baseitem", "Hand Crossbow", "Handarmbrust")
        german = _page("de", glossary)
        words = [training_text(german, w, sheet.training_items) for w in sheet.weapon_proficiencies]
        assert "Einfache Waffen" in words and "Handarmbrust" in words
        assert training_text(german, "light", {}) == "Leichte Rüstung"
        assert training_text(_page("en"), "martial", {}) == "Martial weapons"
        assert training_text(german, "rapiers", {}) == "rapiers"  # unnamed: as written

    def test_feature_origins(self, tmp_path: Path) -> None:
        from app.ui.names import origin_text

        glossary = Glossary(tmp_path)
        glossary.set("de", "class", "Fighter", "Kämpfer")
        glossary.set("de", "background", "Soldier", "Soldat")
        german = _page("de", glossary)
        assert origin_text(german, "Fighter 3") == "Kämpfer 3"
        assert origin_text(german, "Soldier") == "Soldat"
        assert origin_text(german, "Feat") == "Talent"
        assert origin_text(german, "Champion 3") == "Champion 3"
        assert origin_text(_page("en"), "Fighter 3") == "Fighter 3"
