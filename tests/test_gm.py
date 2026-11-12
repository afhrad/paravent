"""The gamemaster's table: the live party overview, and worlds with their books."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Adventure, Role, SourceSet, World
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, RollRecord, Scope
from app.services.events import PARTY
from app.services.worlds import BookCatalog
from app.store.registry import StoreRegistry
from tests.conftest import log_about, requires_corpus

PASSWORD = "correct horse"


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice")
    return registry


@pytest.fixture
async def gm(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client


def _character(registry: StoreRegistry, name: str, **data: object) -> PlayerCharacter:
    return registry.gamestate.characters.create(
        PlayerCharacter.model_validate({"name": name, "overrides": {"hp_max": 30}, **data})
    )


class TestBookCatalog:
    def test_titles_and_groups_from_the_index_files(self, tmp_path: Path) -> None:
        (tmp_path / "books.json").write_text(
            json.dumps(
                {
                    "book": [
                        {
                            "name": "Player's Handbook (2014)",
                            "source": "PHB",
                            "group": "core",
                            "published": "2014-08-19",
                        },
                        {
                            "name": "Player's Handbook (2024)",
                            "source": "XPHB",
                            "group": "core",
                            "published": "2024-09-17",
                        },
                        {
                            "name": "Xanathar's Guide",
                            "source": "XGE",
                            "group": "supplement",
                            "published": "2017-11-21",
                        },
                    ]
                }
            )
        )
        (tmp_path / "adventures.json").write_text(
            json.dumps(
                {
                    "adventure": [
                        {
                            "name": "Curse of Strahd",
                            "source": "CoS",
                            "group": "supplement",
                            "published": "2016-03-15",
                        },
                    ]
                }
            )
        )
        catalog = BookCatalog(tmp_path, {"PHB", "XPHB", "CoS", "HOMEBREW"})
        groups = dict(catalog.grouped())
        assert [b.code for b in groups["core"]] == ["PHB", "XPHB"]
        assert [b.name for b in groups["adventure"]] == ["Curse of Strahd"]
        assert "supplement" not in groups  # XGE has no loaded content
        assert groups["other"][0].name == "HOMEBREW"  # loaded but not indexed: its code
        assert catalog.get("xphb") is not None and catalog.get("xphb").is_2024  # type: ignore[union-attr]

    def test_no_corpus_no_books(self) -> None:
        assert BookCatalog(None, set()).books == []


class TestWorlds:
    async def test_create_edit_and_list(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        response = await gm.post(
            "/gm/worlds",
            data={
                "name": "Mirefall",
                "description": "Mists.",
                "sources": ["PHB", "MM", "CoS", "phb"],
            },
        )
        assert response.status_code == 303
        world = registry.worlds.list()[0]
        assert world.source_set.sources == ["PHB", "MM", "CoS"]  # duplicates dropped
        listing = await gm.get(response.headers["location"])
        assert "World Mirefall created." in listing.text and "3 books" in listing.text
        await gm.post(f"/gm/worlds/{world.id}", data={"name": "Mirefall", "sources": []})
        assert registry.worlds.get(world.id).source_set.is_unrestricted
        assert "all imported books" in (await gm.get("/gm/worlds")).text

    async def test_the_form_buttons_say_what_they_do(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        new = (await gm.get("/gm/worlds/new")).text
        assert "Create world" in new and "Create player" not in new
        world = registry.gamestate.worlds.create(World(name="W"))
        edit = (await gm.get(f"/gm/worlds/{world.id}/edit")).text
        assert "Save changes" in edit

    async def test_the_language_its_texts_are_written_in(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        new = (await gm.get("/gm/worlds/new")).text
        assert 'name="language"' in new and 'value="de"' in new
        await gm.post("/gm/worlds", data={"name": "Ashen Vale", "language": "de"})
        (world,) = registry.gamestate.worlds.list()
        assert world.language == "de"
        assert '<option value="de" selected>' in (await gm.get(f"/gm/worlds/{world.id}/edit")).text
        await gm.post(f"/gm/worlds/{world.id}", data={"name": "Ashen Vale", "language": "xx"})
        assert registry.gamestate.worlds.find(world.id).language == "en"  # type: ignore[union-attr]

    async def test_variant_encumbrance_is_a_world_s_choice(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        await gm.post("/gm/worlds", data={"name": "Heavy Hills", "encumbrance": "1"})
        world = next(w for w in registry.gamestate.worlds.list() if w.name == "Heavy Hills")
        assert world.variant_encumbrance
        form = (await gm.get(f"/gm/worlds/{world.id}/edit")).text
        assert 'name="encumbrance"' in form and "Variant encumbrance" in form
        await gm.post(f"/gm/worlds/{world.id}", data={"name": "Heavy Hills"})
        assert not registry.gamestate.worlds.get(world.id).variant_encumbrance

    async def test_a_name_is_required(self, gm: AsyncClient) -> None:
        response = await gm.post("/gm/worlds", data={"name": "  ", "sources": ["PHB"]})
        assert response.status_code == 422
        assert "A world needs a name." in response.text
        assert 'value="PHB" checked' in response.text  # the choice survives the error

    async def test_declared_books_without_content_stay_visible(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.gamestate.worlds.create(
            World(name="W", source_set=SourceSet(sources=["ZZZ"]))
        )
        text = (await gm.get(f"/gm/worlds/{world.id}/edit")).text
        assert "ZZZ — not in your imported data" in text

    async def test_delete_keeps_characters(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        world = registry.gamestate.worlds.create(World(name="W"))
        pc = _character(registry, "Torvald", world_id=world.id)
        confirm = await gm.get(f"/gm/worlds/{world.id}/delete")
        assert "Its character is kept" in confirm.text
        await gm.post(f"/gm/worlds/{world.id}/delete")
        assert registry.worlds.find(world.id) is None
        assert registry.gamestate.characters.get(pc.id).world_id is None

    async def test_delete_refused_while_adventures_exist(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.gamestate.worlds.create(World(name="W"))
        registry.gamestate.adventures.create(Adventure(world_id=world.id, name="A"))
        response = await gm.post(f"/gm/worlds/{world.id}/delete")
        assert response.status_code == 409
        assert "still has 1 adventures" in response.text

    async def test_unknown_world_is_404(self, gm: AsyncClient) -> None:
        assert (await gm.get("/gm/worlds/nope")).status_code == 404

    async def test_place_a_character(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        world = registry.gamestate.worlds.create(World(name="W"))
        pc = _character(registry, "Torvald")
        assert "Move" in (await gm.get("/gm/characters")).text
        await gm.post(f"/gm/characters/{pc.id}/world", data={"world": world.id})
        assert registry.gamestate.characters.get(pc.id).world_id == world.id
        await gm.post(f"/gm/characters/{pc.id}/world", data={"world": ""})
        assert registry.gamestate.characters.get(pc.id).world_id is None

    async def test_players_cannot_manage_worlds(self, registry: StoreRegistry) -> None:
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": PASSWORD})
            assert (await client.get("/gm/worlds")).status_code == 403
            assert (await client.get("/gm")).status_code == 403
            assert (await client.get("/gm/events")).status_code == 403


class TestParty:
    async def test_every_character_has_a_card(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        _character(
            registry,
            "Torvald",
            player="alice",
            state={"hp": 12, "conditions": ["prone"], "inspiration": True},
        )
        text = (await gm.get("/gm")).text
        assert "Torvald" in text and "Alice" in text
        assert ">12<" in text and "/ 30" in text
        assert "Prone" in text and "Inspiration" in text
        assert "/static/js/party.js" in text

    async def test_a_card_shows_state_at_a_glance(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        pc = _character(
            registry,
            "Down",
            state={"hp": 0, "death_saves": {"successes": 1, "failures": 2}},
        )
        # the card's last roll is the character's newest in the log
        registry.protocol.store.append(
            ProtocolEntry(
                kind=EntryKind.SAVE,
                what="save:dex",
                actor_name="Alice",
                subject=f"character:{pc.id}",
                roll=RollRecord(expression="1d20-1", total=4),
                scope=Scope(world=pc.world_id),
                origin="sheet",
            )
        )
        card = (await gm.get(f"/gm/party/{pc.id}/card")).text
        assert card.startswith(f'<li id="pc-{pc.id}" class="pc-card down"')
        assert "✓ 1" in card and "✗ 2" in card
        assert "Alice · Dexterity save" in card and ">4<" in card

    async def test_filter_by_world(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        world = registry.gamestate.worlds.create(
            World(name="Mirefall", source_set=SourceSet(sources=["CoS"]))
        )
        _character(registry, "Isolde", world_id=world.id)
        _character(registry, "Elsewhere")
        everyone = (await gm.get("/gm")).text
        assert "Isolde" in everyone and "Elsewhere" in everyone and "Mirefall" in everyone
        only = (await gm.get(f"/gm?world={world.id}")).text
        assert "Isolde" in only and "Elsewhere" not in only
        assert "Mirefall · books: CoS" in only
        grid = (await gm.get(f"/gm/party?world={world.id}")).text
        assert grid.startswith('<div id="party"') and f"world={world.id}" in grid

    async def test_an_empty_character_is_not_drawn_as_dying(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        pc = registry.gamestate.characters.create(PlayerCharacter(name="Blank"))
        card = (await gm.get(f"/gm/party/{pc.id}/card")).text
        assert card.startswith(f'<li id="pc-{pc.id}" class="pc-card nohp"')
        assert "crit" not in card and "down" not in card
        assert 'class="pc-hp-now mono ">—<' in card  # not a false "0"

    async def test_unknown_card_is_404(self, gm: AsyncClient) -> None:
        assert (await gm.get("/gm/party/nope/card")).status_code == 404

    async def test_the_overview_is_german_for_a_german_gamemaster(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        dm = registry.auth.find_user("dm")
        assert dm is not None
        registry.auth.set_language(dm.id, "de")
        _character(registry, "Torvald", state={"conditions": ["prone"]})
        text = (await gm.get("/gm")).text
        assert "Liegend" in text and "RK" in text and "Alle Welten" in text


class TestLive:
    async def test_every_character_change_reaches_the_party_stream(
        self, registry: StoreRegistry
    ) -> None:
        queue = registry.events.subscribe(PARTY)
        pc = _character(registry, "Torvald")
        created = await asyncio.wait_for(queue.get(), 1)
        assert created == f"{pc.id} {registry.gamestate.characters.revision(pc.id)}"
        registry.gamestate.characters.update(pc.id, notes="changed")
        changed = await asyncio.wait_for(queue.get(), 1)
        assert changed == f"{pc.id} {registry.gamestate.characters.revision(pc.id)}"
        registry.gamestate.characters.delete(pc.id)
        assert await asyncio.wait_for(queue.get(), 1) == f"{pc.id} "


@requires_corpus
class TestWorldBooksGovernResolution:
    def test_a_world_without_the_book_leaves_the_spell_unresolved(
        self, tmp_path: Path, corpus: Path
    ) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        strict = registry.gamestate.worlds.create(
            World(name="Strict", source_set=SourceSet(sources=["XPHB"]))
        )
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "X",
                    "world_id": strict.id,
                    "spells": {"prepared": ["Fireball|PHB", "Fireball"]},
                }
            )
        )
        sheet = registry.characters.sheet(pc)
        found = {s.reference: (s.found, s.source) for s in sheet.spells}
        assert found["Fireball|PHB"] == (False, "PHB")  # PHB is not among this world's books
        assert found["Fireball"] == (True, "XPHB")  # the bare name resolves within the world
        assert registry.books.get("PHB") is not None
        assert registry.books.get("PHB").name == "Player's Handbook (2014)"  # type: ignore[union-attr]


HX = {"HX-Request": "true"}


class TestCardActions:
    """Damage, healing and conditions straight from the overview."""

    async def test_damage_returns_the_new_card(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        pc = _character(registry, "Torvald", state={"temp_hp": 3})
        response = await gm.post(
            f"/gm/party/{pc.id}/act/hp", data={"mode": "damage", "amount": "8"}, headers=HX
        )
        assert response.status_code == 200
        assert response.text.startswith(f'<li id="pc-{pc.id}"')
        state = registry.gamestate.characters.get(pc.id).state
        assert (state.hp, state.temp_hp) == (25, 0)  # temporary HP first, as on the sheet
        assert ">25<" in response.text

    async def test_heal_and_temp(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        pc = _character(registry, "Torvald", state={"hp": 10})
        await gm.post(f"/gm/party/{pc.id}/act/hp", data={"mode": "heal", "amount": "5"}, headers=HX)
        await gm.post(f"/gm/party/{pc.id}/act/hp", data={"mode": "temp", "amount": "4"}, headers=HX)
        state = registry.gamestate.characters.get(pc.id).state
        assert (state.hp, state.temp_hp) == (15, 4)

    async def test_conditions_toggle(self, gm: AsyncClient, registry: StoreRegistry) -> None:
        pc = _character(registry, "Torvald")
        card = (
            await gm.post(f"/gm/party/{pc.id}/act/condition", data={"key": "prone"}, headers=HX)
        ).text
        assert registry.gamestate.characters.get(pc.id).state.conditions == ["prone"]
        assert 'class="cbtn on" aria-pressed="true">Prone' in card
        await gm.post(f"/gm/party/{pc.id}/act/condition", data={"key": "prone"}, headers=HX)
        assert registry.gamestate.characters.get(pc.id).state.conditions == []

    async def test_the_gamemaster_is_named_in_the_log(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        pc = _character(registry, "Torvald", state={"concentration": "Bless|PHB"})
        await gm.post(
            f"/gm/party/{pc.id}/act/hp", data={"mode": "damage", "amount": "4"}, headers=HX
        )
        save = log_about(registry, pc.id)[0]  # the concentration save, after the damage
        assert save.kind.value == "concentration" and save.actor_name == "Dana"

    async def test_a_refusal_is_explained_on_the_card(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        pc = _character(registry, "Torvald")
        response = await gm.post(
            f"/gm/party/{pc.id}/act/hp", data={"mode": "damage", "amount": "x"}, headers=HX
        )
        assert response.status_code == 200
        assert "That action could not be read." in response.text

    async def test_without_javascript_it_returns_to_the_same_view(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.gamestate.worlds.create(World(name="W"))
        pc = _character(registry, "Torvald", world_id=world.id)
        response = await gm.post(
            f"/gm/party/{pc.id}/act/hp", data={"mode": "damage", "amount": "1", "world": world.id}
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/gm?world={world.id}"
        assert registry.gamestate.characters.get(pc.id).state.hp == 29

    async def test_cards_carry_the_world_filter(
        self, gm: AsyncClient, registry: StoreRegistry
    ) -> None:
        world = registry.gamestate.worlds.create(World(name="W"))
        _character(registry, "Torvald", world_id=world.id)
        page = (await gm.get(f"/gm?world={world.id}")).text
        assert f'name="world" value="{world.id}"' in page

    async def test_unknown_character_is_404(self, gm: AsyncClient) -> None:
        response = await gm.post(
            "/gm/party/nope/act/hp", data={"mode": "damage", "amount": "1"}, headers=HX
        )
        assert response.status_code == 404

    async def test_players_cannot_use_it(self, registry: StoreRegistry) -> None:
        pc = _character(registry, "Torvald", player="alice")
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": PASSWORD})
            response = await client.post(
                f"/gm/party/{pc.id}/act/hp", data={"mode": "damage", "amount": "5"}
            )
            assert response.status_code == 403
        assert registry.gamestate.characters.get(pc.id).state.hp is None
