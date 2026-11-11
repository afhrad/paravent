"""Styles made for one language, styles of places, and creatures finding their style
(documentation/NAMES.md §2, §5)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import EncounterMonster, Role, World
from app.models.naming import ListPart, NamingStyle
from app.services.naming import FoundStyle, Naming, creature_races
from app.services.naming_workshop import fields_of, search, style_from, subjects
from app.store.npc_tables import NpcTables
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _style(name: str, **fields: object) -> NamingStyle:
    return NamingStyle(
        name=name, pattern="{given}", parts={"given": ListPart(names=[name])}, **fields
    )


def _dwarves() -> Naming:
    styles = [
        _style("Thorgrim", id="de", race="Dwarf", language="de"),
        _style("Torvin", id="any", race="Dwarf"),
        _style("Gruk", id="orc", race="Orc"),
        _style("Ugrash", id="orcde", race="Orc", language="de"),
    ]
    return Naming(NpcTables(None), lambda: styles)


class TestLanguage:
    def test_a_style_made_for_the_readers_language_comes_first(self) -> None:
        naming = _dwarves()
        key = naming.find("Dwarf", language="de").key
        assert key == "race:Dwarf"
        assert set(naming.names(key, count=5, language="de")) == {"Thorgrim"}

    def test_it_is_never_picked_by_itself_for_another_language(self) -> None:
        naming = _dwarves()
        assert set(naming.names("race:Dwarf", count=5, language="en")) == {"Torvin"}
        assert set(naming.names("race:Dwarf", count=5)) == {"Torvin"}

    def test_a_race_with_only_another_languages_style_falls_back(self) -> None:
        naming = Naming(
            NpcTables(None), lambda: [_style("Thorgrim", id="de", race="Zwerg", language="de")]
        )
        assert naming.find("Zwerg", language="en").fallback
        assert naming.find("Zwerg", language="de") == FoundStyle("race:Zwerg")
        # Chosen by hand it still serves.
        assert naming.names("own:de", language="en") == ["Thorgrim"]

    def test_a_regional_code_counts_as_its_language(self) -> None:
        naming = _dwarves()
        assert naming.names("race:Dwarf", language="de-AT") == ["Thorgrim"]


class TestPlaces:
    @pytest.fixture
    def naming(self) -> Naming:
        return Naming(NpcTables(None))

    def test_the_starters_name_every_kind(self, naming: Naming) -> None:
        kinds = {e.subject for e in naming.entries() if e.style is not None}
        assert kinds == {"person", "settlement", "water", "landscape", "building"}
        assert [g.subject for g in subjects(naming.entries())] == [
            "settlement",
            "water",
            "landscape",
            "building",
        ]

    def test_a_kind_rolls_in_the_readers_language(self, naming: Naming) -> None:
        found = naming.find(language="de", subject="settlement")
        assert found == FoundStyle("subject:settlement")
        german = naming.names(found.key, count=30, language="de")
        endings = ("dorf", "hausen", "heim", "bach", "berg", "burg", "feld", "au", "stadt")
        assert len(german) == 30 and any(n.endswith(endings) for n in german)
        english = naming.names(found.key, count=40, language="en")
        assert not any(n.endswith(("dorf", "hausen")) for n in english)

    def test_a_german_tavern_agrees(self, naming: Naming) -> None:
        for name in naming.names("starter:german-taverns", count=40):
            article, _, sign = name.partition(" ")
            assert article in ("Zum", "Zur") and sign.split()[0].endswith("en")
        assert naming.name("starter:english-taverns").startswith("The ")

    def test_places_stay_out_of_peoples_lookups(self, naming: Naming) -> None:
        assert naming.find("", "German").key.startswith(("starter:", "Human", "race:Human"))
        assert not naming.find("", "German").key.startswith("starter:german-")
        assert all(
            e.subject == "settlement" for e in search(naming.entries(), subject="settlement")
        )
        assert all(e.subject == "person" for e in search(naming.entries(), race=""))

    def test_the_workshop_keeps_kind_and_language(self) -> None:
        style = _style("Eichendorf", subject="settlement", language="de")
        again = style_from(fields_of(style))
        assert (again.subject, again.language) == ("settlement", "de")
        fields = fields_of(style)
        fields.subject, fields.language = "nonsense", "DE-de"
        assert (style_from(fields).subject, style_from(fields).language) == ("person", "de")


class TestCreatures:
    def test_race_names_of_a_creature(self) -> None:
        assert creature_races("Orc War Chief", ("orc",)) == ["orc", "Orc War Chief", "War", "Chief"]
        assert creature_races("Kuo-toa", (), "Mire Lurker")[0] == "Mire Lurker"

    def test_a_creature_finds_its_style_by_tag_or_name(self) -> None:
        naming = _dwarves()
        assert naming.for_creature(creature_races("Orc War Chief", ("orc",)), "2014") == FoundStyle(
            "race:Orc"
        )
        assert naming.for_creature(creature_races("Orc Eye of Gruumsh", ()), "2014") == FoundStyle(
            "race:Orc"
        )
        found = naming.for_creature(creature_races("Wolf", ()), "2014")
        assert found.fallback and found.key == naming.default_key("2014")

    def test_an_alias_names_a_creature(self) -> None:
        naming = Naming(
            NpcTables(None), lambda: [_style("Grak", id="g", race="Gnoll", aliases=["Flind"])]
        )
        assert naming.for_creature(["Flind"], "2014") == FoundStyle("race:Gnoll")


@pytest.fixture
async def table(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, dict[str, str]]]:
    registry = StoreRegistry(
        Settings(
            library_dir=tmp_path / "lib", reference_cache_dir=tmp_path / "cache", watch_files=False
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(World(name="W"))
    registry.naming_workshop.create(_style("Gruk", race="Orc"))
    registry.naming_workshop.create(_style("Ugrash", race="Orc", language="de"))
    encounter = registry.encounters.create("Ambush", world.id)
    registry.gamestate.encounters.mutate(
        encounter.id,
        lambda e: e.monsters.append(EncounterMonster(name="Orc", source="MM", count=1)),
    )
    location = registry.locations.create("Somewhere", f"w:{world.id}", world.id)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry, {"encounter": encounter.id, "location": location.id}


class TestAtTheTable:
    async def test_the_dice_name_a_planned_orc_in_the_readers_language(
        self, table: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = table
        url = f"/gm/encounters/{ids['encounter']}"
        page = (await ac.get(url)).text
        assert f'formaction="{url}/monsters/0/label/roll"' in page
        await ac.post(f"{url}/monsters/0/label/roll", data={"label": "", "style": ""})
        assert registry.gamestate.encounters.get(ids["encounter"]).monsters[0].label == "Gruk"
        registry.encounters.set_label(ids["encounter"], 0, "")
        registry.encounters.roll_label(ids["encounter"], 0, registry.naming, "2014", language="de")
        assert registry.gamestate.encounters.get(ids["encounter"]).monsters[0].label == "Ugrash"
        registry.encounters.set_count(ids["encounter"], 0, 3)
        assert f'formaction="{url}/monsters/0/label/roll"' in (await ac.get(url)).text
        await ac.post(f"{url}/monsters/0/label/roll", data={"style": "starter:old-norse"})
        group = registry.gamestate.encounters.get(ids["encounter"]).monsters[0]
        assert len(group.names) == 3 and len(set(group.names)) == 3 and group.reskin is None

    def test_a_label_reads_as_a_reskin_or_a_list(self) -> None:
        line = EncounterMonster(name="Kuo-toa", source="MM", count=3, label="Mire Lurker")
        assert (line.names, line.reskin) == ([], "Mire Lurker")
        line.label = "Gruk,  Ugrash ,"
        assert (line.names, line.reskin) == (["Gruk", "Ugrash"], None)

    async def test_another_style_from_the_menu(
        self, table: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = table
        url = f"/gm/encounters/{ids['encounter']}"
        await ac.post(
            f"{url}/monsters/0/label/roll",
            data={"label": "", "style": "starter:english-taverns"},
        )
        assert (
            registry.gamestate.encounters.get(ids["encounter"]).monsters[0].label.startswith("The ")
        )

    async def test_a_place_name_is_rolled_into_the_form_unsaved(
        self, table: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = table
        url = f"/gm/places/{ids['location']}"
        form = (await ac.get(f"{url}/edit")).text
        assert f'formaction="{url}/edit/name"' in form and "subject:water" in form
        answer = await ac.post(
            f"{url}/edit/name",
            data={"name": "Somewhere", "notes": "Kept as typed.", "style": "starter:german-waters"},
        )
        assert answer.status_code == 200 and "Kept as typed." in answer.text
        assert 'value="Somewhere"' not in answer.text
        assert registry.locations.get(ids["location"]).name == "Somewhere"

    async def test_with_htmx_only_the_line_or_field_comes_back(
        self, table: tuple[AsyncClient, StoreRegistry, dict[str, str]]
    ) -> None:
        ac, registry, ids = table
        hx = {"HX-Request": "true"}
        url = f"/gm/encounters/{ids['encounter']}"
        line = await ac.post(f"{url}/monsters/0/label/roll", data={"style": ""}, headers=hx)
        assert line.status_code == 200 and line.text.startswith("<li")
        assert 'value="Gruk"' in line.text and "<html" not in line.text
        # The label form selects #plan for its own saves; the dice must not inherit it.
        assert 'hx-select="unset"' in line.text
        again = await ac.post(
            f"{url}/monsters/0/label/roll", data={"style": "starter:old-norse"}, headers=hx
        )
        assert '<option value="starter:old-norse" selected>' in again.text  # kept for the dice
        place = f"/gm/places/{ids['location']}"
        field = await ac.post(
            f"{place}/edit/name",
            data={"name": "Somewhere", "style": "starter:german-waters"},
            headers=hx,
        )
        assert field.text.startswith('<span class="name-field">') and "<form" not in field.text
        assert registry.locations.get(ids["location"]).name == "Somewhere"
