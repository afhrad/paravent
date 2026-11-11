"""Reading the log (documentation/PROTOCOL.md §4, §5): who may see what, the filters,
the panel and its routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, World
from app.models.pc import PlayerCharacter
from app.models.protocol import (
    Bonus,
    Die,
    EntryKind,
    ProtocolEntry,
    RollRecord,
    Scope,
    Visibility,
)
from app.services.protocol_view import Filter, ProtocolReader, Viewer
from app.store.protocol import ProtocolStore
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"
T0 = datetime(2026, 10, 25, 19, 0, tzinfo=UTC)
GM = Viewer("dm", gamemaster=True)
ALICE = Viewer("alice", gamemaster=False, characters=frozenset({"lyra"}), worlds=frozenset({"w1"}))


def _roll(total: int = 17) -> RollRecord:
    return RollRecord(
        expression="1d20+4",
        dice=[Die(faces=20, results=[13], kept=[13])],
        natural=13,
        total=total,
        bonuses=[Bonus(source="statblock", value=4)],
    )


def _entry(minutes: int, **facts: Any) -> ProtocolEntry:
    facts.setdefault("scope", Scope(world="w1"))
    facts.setdefault("kind", EntryKind.CUSTOM)
    return ProtocolEntry(at=T0 + timedelta(minutes=minutes), **facts)


@pytest.fixture
def reader(tmp_path: Path) -> tuple[ProtocolStore, ProtocolReader]:
    store = ProtocolStore(tmp_path / "protocol")
    return store, ProtocolReader(store)


class TestWhoSeesWhat:
    def test_the_gamemaster_reads_everything(self, reader: Any) -> None:
        store, log = reader
        store.append(_entry(1, what="secret", visibility=Visibility.GM))
        assert [s.entry.what for s in log.page(GM).shown] == ["secret"]

    def test_a_player_reads_their_worlds_only(self, reader: Any) -> None:
        store, log = reader
        store.append(_entry(1, what="here"))
        store.append(_entry(2, what="elsewhere", scope=Scope(world="w2")))
        store.append(_entry(3, what="behind the screen", visibility=Visibility.GM))
        assert [s.entry.what for s in log.page(ALICE).shown] == ["here"]

    def test_private_rolls_reach_the_roller_and_their_character(self, reader: Any) -> None:
        store, log = reader
        store.append(_entry(1, what="mine", actor="alice", visibility=Visibility.PRIVATE))
        store.append(
            _entry(2, what="about lyra", subject="character:lyra", visibility=Visibility.PRIVATE)
        )
        store.append(_entry(3, what="bob's", actor="bob", visibility=Visibility.PRIVATE))
        assert [s.entry.what for s in log.page(ALICE).shown] == ["about lyra", "mine"]

    def test_a_monster_shows_its_results_only(self, reader: Any) -> None:
        store, log = reader
        rolled = _entry(
            1,
            kind=EntryKind.ATTACK,
            what="Scimitar",
            subject="combatant:c1/g1",
            subject_name="Goblin 1",
            roll=_roll(),
        )
        store.append(rolled)
        store.append(
            _entry(
                2,
                kind=EntryKind.ATTACK,
                what="Scimitar",
                subject="combatant:c1/g1",
                subject_name="Goblin 1",
                target="character:lyra",
                target_name="Lyra",
                outcome="hit",
                amount=5,
                types={"slashing": 5},
                refers_to=rolled.id,
            )
        )
        store.append(
            _entry(
                3,
                kind=EntryKind.SAVE,
                subject="combatant:c1/g1",
                what="save:dex",
                roll=_roll(9),
                outcome="failure",
                against=15,
            )
        )
        seen = [s.entry for s in log.page(ALICE).shown]
        # the roll is behind the screen; the verdict and the save show without numbers
        assert [(e.kind, e.outcome) for e in seen] == [
            (EntryKind.SAVE, "failure"),
            (EntryKind.ATTACK, "hit"),
        ]
        assert all(e.roll is None and e.against is None for e in seen)
        assert seen[1].amount == 5
        # the gamemaster sees all three, numbers and all
        full = [s.entry for s in log.page(GM).shown]
        assert len(full) == 3 and full[0].against == 15 and full[2].roll is not None

    def test_the_fights_bookkeeping_and_dcs_stay_with_the_gamemaster(self, reader: Any) -> None:
        store, log = reader
        store.append(_entry(1, kind=EntryKind.COMBAT, what="hide"))
        store.append(
            _entry(
                2,
                kind=EntryKind.SAVE,
                subject="character:lyra",
                what="save:dex",
                roll=_roll(20),
                outcome="success",
                against=15,
            )
        )
        (shown,) = log.page(ALICE).shown
        assert shown.entry.roll is not None and shown.entry.against is None

    def test_a_reveal_makes_a_hidden_roll_public(self, reader: Any) -> None:
        store, log = reader
        hidden = _entry(1, what="behind the screen", visibility=Visibility.GM, roll=_roll())
        store.append(hidden)
        assert log.page(ALICE).shown == []
        store.append(_entry(2, kind=EntryKind.REVEAL, refers_to=hidden.id))
        (shown,) = log.page(ALICE).shown
        assert shown.revealed and shown.entry.roll is not None

    def test_correct_and_clear(self, reader: Any) -> None:
        store, log = reader
        old = _entry(1, what="old", subject="character:lyra")
        wrong = _entry(3, what="wrong", subject="character:lyra")
        store.append(old)
        store.append(_entry(2, kind=EntryKind.CLEAR, subject="character:lyra"))
        store.append(wrong)
        store.append(_entry(4, kind=EntryKind.CORRECT, refers_to=wrong.id))
        shown = log.page(GM).shown
        assert [(s.entry.what, s.corrected) for s in shown] == [("wrong", True)]


class TestFiltersAndPaging:
    def test_me_fight_character_monsters(self, reader: Any) -> None:
        store, log = reader
        store.append(_entry(1, what="lyra rolls", subject="character:lyra"))
        store.append(
            _entry(
                2,
                what="goblin",
                subject="combatant:c1/g1",
                scope=Scope(world="w1", combat="c1", round=1),
            )
        )
        store.append(_entry(3, what="hits lyra", target="character:lyra"))
        mine = log.page(ALICE, Filter("me")).shown
        assert [s.entry.what for s in mine] == ["hits lyra", "lyra rolls"]
        fight = log.page(GM, Filter("fight", combat="c1")).shown
        assert [s.entry.what for s in fight] == ["goblin"]
        theirs = log.page(GM, Filter("character", character="lyra")).shown
        assert len(theirs) == 2
        assert [s.entry.what for s in log.page(GM, Filter("monsters")).shown] == ["goblin"]

    def test_older_pages_and_the_badge(self, reader: Any) -> None:
        store, log = reader
        for minute in range(5):
            store.append(_entry(minute, what=str(minute)))
        first = log.page(GM, limit=2)
        assert [s.entry.what for s in first.shown] == ["4", "3"] and first.more
        second = log.page(GM, before=first.shown[-1].entry.id, limit=2)
        assert [s.entry.what for s in second.shown] == ["2", "1"]
        assert log.since(GM, T0 + timedelta(minutes=2)) == 2
        assert log.since(ALICE, None) == 5


# -- the panel on screen ----------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice")
    return registry


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _login(client: AsyncClient, username: str) -> None:
    await client.post("/login", data={"username": username, "password": PASSWORD})


def _thorin(registry: StoreRegistry) -> PlayerCharacter:
    world = registry.gamestate.worlds.create(World(name="Grey Reed"))
    return registry.gamestate.characters.create(
        PlayerCharacter.model_validate(
            {
                "name": "Thorin",
                "player": "alice",
                "world_id": world.id,
                "classes": [{"class": "Homebrew Fighter", "level": 5}],
                "abilities": {"str": 16, "dex": 12},
                "skills": ["Athletics"],
                "overrides": {"hp_max": 40},
            }
        )
    )


class TestThePanel:
    async def test_every_page_has_the_button_and_the_sheet_opens_on_its_character(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        thorin = _thorin(registry)
        await _login(client, "alice")
        home = (await client.get("/play")).text
        assert 'class="btn btn-ghost shell-log"' in home and 'id="log-panel"' in home
        assert "/static/js/protocol.js" in home
        sheet = (await client.get(f"/characters/{thorin.id}")).text
        assert f'data-query="filter=character&amp;character={thorin.id}"' in sheet

    async def test_a_roll_reads_back_in_german(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        thorin = _thorin(registry)
        await _login(client, "alice")
        await client.post(
            f"/characters/{thorin.id}/act/roll",
            data={"kind": "skill", "subject": "athletics", "adv": "1"},
        )
        panel = (await client.get("/log/panel")).text
        assert "Athletics" in panel and "ADV" in panel and "+ STR 3 + Prof 3" in panel
        await client.post("/language", data={"lang": "de", "next": "/"})
        german = (await client.get("/log/panel")).text
        assert "Athletik" in german and "VORT" in german and "W20" in german
        assert "+ ST 3 + ÜB 3" in german  # the catalogue's abbreviations
        assert (await client.get("/log/badge")).text == "1"
        page = await client.get("/log")
        assert page.status_code == 200 and "Athletik" in page.text

    async def test_a_dice_chip_is_logged_privately(
        self, client: AsyncClient, registry: StoreRegistry
    ) -> None:
        thorin = _thorin(registry)
        await _login(client, "alice")
        rolled = await client.post(
            "/log/roll",
            data={"expr": "1d20+4", "label": "Goblin · Scimitar", "world": "elsewhere"},
        )
        body = rolled.json()
        assert rolled.status_code == 200 and isinstance(body["total"], int)
        entry = registry.protocol.store.newest("global")[0]  # not a world of hers
        assert entry.visibility == Visibility.PRIVATE and entry.actor == "alice"
        assert entry.subject_name == "Goblin · Scimitar" and entry.what == "custom:1d20+4"
        assert (await client.post("/log/roll", data={"expr": "rm -rf"})).status_code == 422
        assert thorin.world_id is not None
        await client.post("/log/roll", data={"expr": "2d6", "world": thorin.world_id})
        assert registry.protocol.store.newest(thorin.world_id)[0].what == "custom:2d6"

    async def test_signed_out_gets_nothing(self, client: AsyncClient) -> None:
        response = await client.get("/log/panel")
        assert response.status_code in (303, 401) or "login" in response.headers.get("location", "")
