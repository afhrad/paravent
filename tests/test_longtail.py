"""The long tail of corpus phase 2: psionics, vehicles, facilities, options, cults, decks.

Loaded as compendium categories (TECHSTACK.md §6b), searchable, linkable, and each
shown on the reference page. Cards are the trap: ``(name, source)`` does not name one,
so they are keyed with their deck and reached through it.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet
from app.services.search import SearchService, flatten
from app.services.text import strip_tags
from app.store.compendium import CompendiumStore
from app.store.reference import parse_reference
from app.store.registry import StoreRegistry
from app.ui.tags import RenderContext, render

PASSWORD = "correct horse battery"

FILES = {
    "psionic": ("psionics.json", "psionic"),
    "vehicle": ("vehicles.json", "vehicle"),
    "vehicleUpgrade": ("vehicles.json", "vehicleUpgrade"),
    "facility": ("bastions.json", "facility"),
    "charoption": ("charcreationoptions.json", "charoption"),
    "cult": ("cultsboons.json", "cult"),
    "boon": ("cultsboons.json", "boon"),
    "deck": ("decks.json", "deck"),
    "card": ("decks.json", "card"),
}


def _html_text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


class TestLoad:
    @pytest.mark.parametrize("category", sorted(FILES))
    def test_every_row_loads(
        self, category: str, corpus: Path, loaded_compendium: CompendiumStore
    ) -> None:
        file, key = FILES[category]
        rows = json.loads((corpus / file).read_text(encoding="utf-8"))[key]
        assert len(loaded_compendium.all(category)) == len(rows)

    def test_cards_need_their_deck_in_the_key(self, corpus: Path) -> None:
        """32 (name, source) pairs repeat; with the deck, none do."""
        cards = json.loads((corpus / "decks.json").read_text(encoding="utf-8"))["card"]
        pairs = [(c["name"], c["source"]) for c in cards]
        triples = [(c["name"], c["source"], c["set"]) for c in cards]
        assert len(set(pairs)) < len(pairs)
        assert len(set(triples)) == len(triples)

    def test_a_card_tag_names_its_deck_not_a_source(self) -> None:
        assert parse_reference("Sun|Deck of Many Things|DMG|the Sun", "card") == (
            "Sun",
            "DMG",
            "the Sun",
        )
        assert strip_tags("Draw {@card Sun|Deck of Many Things|DMG|the Sun}.") == "Draw the Sun."


class TestTags:
    def test_a_card_links_to_its_place_on_the_deck(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        ctx = RenderContext(resolver=loaded_compendium.resolver())
        html = str(render("{@card Abjurer|Tarokka Deck|CoS}", ctx)[0])
        assert 'href="/ref/deck/CoS/Tarokka%20Deck?card=Abjurer#card-abjurer"' in html

    def test_a_card_without_a_source_takes_the_world_s_deck(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        resolver = loaded_compendium.resolver()
        ctx = RenderContext(resolver=resolver, source_set=SourceSet(sources=["XDMG"]))
        html = str(render("{@card Sun|Deck of Many Things}", ctx)[0])
        assert "/ref/deck/XDMG/Deck%20of%20Many%20Things" in html

    def test_a_deck_outside_the_world_s_books_is_not_linked(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        ctx = RenderContext(
            resolver=loaded_compendium.resolver(), source_set=SourceSet(sources=["PHB"])
        )
        html = str(render("{@card Abjurer|Tarokka Deck|CoS}", ctx)[0])
        assert "ref missing" in html and "href" not in html

    @pytest.mark.parametrize(
        ("tag", "category"),
        [
            ("{@vehicle Infernal War Machine|BGDIA}", None),
            ("{@vehicle Demon Grinder|BGDIA}", "vehicle"),
            ("{@vehupgrade Acidic Bile Sprayer|BGDIA}", "vehicleUpgrade"),
            ("{@facility Arcane Study|XDMG}", "facility"),
            ("{@cult Cult of Baalzebul|MTF}", "cult"),
            ("{@deck Tarokka Deck|CoS}", "deck"),
        ],
    )
    def test_entity_tags_resolve(
        self, tag: str, category: str | None, loaded_compendium: CompendiumStore
    ) -> None:
        html = str(render(tag, RenderContext(resolver=loaded_compendium.resolver()))[0])
        if category is None:
            assert "href" not in html  # not a vehicle: no such entry
        else:
            assert f'href="/ref/{category}/' in html


class TestSearch:
    def test_the_new_categories_are_found(self, loaded_compendium: CompendiumStore) -> None:
        service = SearchService(loaded_compendium)
        found = {
            (hit.row.category, hit.row.name)
            for query in ("demon grinder", "arcane study", "tarokka", "cult of baalzebul")
            for hit in flatten(service.search(query).groups)
        }
        assert ("vehicle", "Demon Grinder") in found
        assert ("facility", "Arcane Study") in found
        assert ("deck", "Tarokka Deck") in found
        assert ("cult", "Cult of Baalzebul") in found

    def test_cards_are_not_searched(self, loaded_compendium: CompendiumStore) -> None:
        hits = flatten(SearchService(loaded_compendium).search("abjurer").groups)
        assert all(hit.row.category != "card" for hit in hits)


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


def _url(category: str, entry: Any) -> str:
    return f"/ref/{category}/{entry.source}/{entry.name}"


class TestPages:
    async def test_every_entry_renders(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        """Every page answers, with no raw tag and no missing label."""
        ac, registry = client
        for category in sorted(FILES):
            if category == "card":
                continue
            for entry in registry.compendium.all(category):
                response = await ac.get(_url(category, entry))
                assert response.status_code == 200, (category, entry.name)
                text = _html_text(response.text)
                assert "{@" not in text, (category, entry.name)
                assert not re.search(r"\bref\.[a-z_]+\b", text), (category, entry.name)

    async def test_a_ship_shows_its_hull_and_parts(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _registry = client
        text = _html_text((await ac.get("/ref/vehicle/AI/Battle Balloon")).text)
        for words in ("Hull", "damage threshold 15", "Control", "Helm", "Weapons", "Harpoon Gun"):
            assert words in text

    async def test_a_deck_lists_its_cards_with_anchors(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _registry = client
        page = (await ac.get("/ref/deck/CoS/Tarokka Deck")).text
        assert 'id="card-abjurer"' in page and "54 cards" in page

    async def test_the_13_card_deck_draws_from_the_full_one(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _registry = client
        page = (await ac.get("/ref/deck/DMG/Deck of Many Things (13 Cards)")).text
        assert "13 cards" in page and 'id="card-jester"' in page

    async def test_a_card_preview_shows_the_card(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _registry = client
        preview = (await ac.get("/ref/deck/CoS/Tarokka Deck/preview?card=Abjurer")).text
        assert "Abjurer" in preview and "card=Abjurer#card-abjurer" in preview
        deck = (await ac.get("/ref/deck/CoS/Tarokka Deck/preview")).text
        assert "Abjurer" not in deck
