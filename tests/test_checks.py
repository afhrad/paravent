"""Checks the gamemaster calls for outside a fight (app/services/checks.py), treasure
shared out to the characters (TreasureService.share), the players' notes, the safety
tools and bastions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Coins, Role, SourceSet, World
from app.models.pc import PlayerCharacter
from app.models.protocol import Visibility
from app.services.adventures import text_version
from app.services.bastions import special_slots
from app.services.checks import CheckError, CheckService
from app.store.gamestate import GameStateStore
from app.store.protocol import log_key
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus

ROOT = Path(__file__).resolve().parent.parent
LYRA = ROOT / "documentation" / "examples" / "lyra-mooncaller.json"
PASSWORD = "correct horse battery"


class TestTheService:
    @pytest.fixture
    def checks(self, tmp_path: Path) -> CheckService:
        rolls = iter([14, 6])
        return CheckService(GameStateStore(tmp_path), roll=lambda *_: next(rolls))

    def test_an_open_call_takes_each_characters_first_roll(self, checks: CheckService) -> None:
        call = checks.call(None, "skill", "Perception", ["a", "b"], "dm", dc=12, group=True)
        assert call.open and call.subject == "perception"
        assert [c.id for c in checks.open_for("a")] == [call.id]
        checks.answer("a", "skill", "perception", 15)
        checks.answer("a", "skill", "perception", 3)  # a second roll does not replace it
        checks.answer("b", "check", "wis", 20)  # another check answers nothing
        now = checks.recent()[0]
        assert now.results == {"a": 15} and now.group_passed is None  # b still to roll
        checks.answer("b", "skill", "perception", 9)
        now = checks.recent()[0]
        assert now.passed("a") and now.passed("b") is False
        assert now.group_passed  # one of two succeeded: half

    def test_a_secret_check_is_rolled_at_once_and_closed(self, checks: CheckService) -> None:
        call = checks.call(None, "save", "dex", ["a", "b"], "dm", dc=10, secret=True)
        assert not call.open and call.results == {"a": 14, "b": 6}
        assert checks.open_for("a") == []

    def test_refused(self, checks: CheckService) -> None:
        with pytest.raises(CheckError):
            checks.call(None, "skill", "Juggling", ["a"], "dm")
        with pytest.raises(CheckError):
            checks.call(None, "check", "wis", [], "dm")

    def test_closing(self, checks: CheckService) -> None:
        call = checks.call(None, "check", "str", ["a"], "dm")
        checks.close(call.id)
        assert checks.open_for("a") == [] and not checks.recent()[0].open


@pytest.fixture
async def table(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(
        World(name="W", source_set=SourceSet(sources=["PHB", "MM"]))
    )
    data = json.loads(LYRA.read_text(encoding="utf-8"))
    data.pop("$schema", None)
    data["world_id"] = world.id
    lyra = registry.gamestate.characters.create(PlayerCharacter.model_validate(data))
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry, lyra.id


@requires_corpus
class TestAtTheTable:
    async def test_a_called_check_reaches_the_sheet_and_its_roll_answers_it(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        party = (await ac.get("/gm")).text
        assert "Call for a check" in party
        await ac.post(
            "/gm/checks", data={"what": "skill:perception", "character": lyra, "dc": "12"}
        )
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert "The gamemaster asks for Perception" in sheet
        await ac.post(
            f"/characters/{lyra}/act/roll", data={"kind": "skill", "subject": "perception"}
        )
        call = registry.checks.recent()[0]
        assert lyra in call.results
        panel = (await ac.get("/gm/checks/panel")).text
        assert f"{call.results[lyra]}" in panel and "waiting" not in panel
        assert "You rolled" in (await ac.get(f"/characters/{lyra}")).text

    async def test_a_secret_check_stays_behind_the_screen(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        await ac.post(
            "/gm/checks", data={"what": "skill:stealth", "character": lyra, "secret": "1"}
        )
        call = registry.checks.recent()[0]
        assert not call.open and lyra in call.results
        world = registry.gamestate.characters.get(lyra).world_id
        newest = registry.protocol.store.newest(log_key(world), 1)
        assert newest and newest[0].visibility is Visibility.PRIVATE
        assert "asks for" not in (await ac.get(f"/characters/{lyra}")).text


@requires_corpus
class TestSharingOutTreasure:
    async def test_items_and_coins_land_in_the_characters_files(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        brunhild = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Brunhild", "world_id": world})
        )
        treasure = registry.treasure.create("Chest", world, world)
        registry.treasure.add_item(treasure.id, "Potion of Healing|DMG", 2)
        registry.treasure.add_item(treasure.id, "Torch|PHB", 1)
        registry.treasure.update(
            treasure.id, name="Chest", scope_value=world, coins=Coins(gp=31, sp=4), notes=""
        )
        page = (await ac.get(f"/gm/treasure/{treasure.id}")).text
        assert "Share out" in page
        held = registry.gamestate.characters.get(lyra).inventory
        before = next((i.quantity for i in held if i.item.name == "Potion of Healing"), 0)
        await ac.post(
            f"/gm/treasure/{treasure.id}/share",
            data={"item.0": lyra, "item.1": "", "coins_to": [lyra, brunhild.id]},
        )
        after = registry.gamestate.characters.get(lyra)
        potions = next(i.quantity for i in after.inventory if i.item.name == "Potion of Healing")
        assert potions == before + 2
        assert registry.gamestate.characters.get(brunhild.id).currency.gp == 15
        left = registry.treasure.get(treasure.id)
        assert [i.name for i in left.items] == ["Torch"]
        assert (left.coins.gp, left.coins.sp) == (1, 0)  # the odd gold piece stays
        newest = registry.protocol.store.newest(log_key(world), 5)
        assert any(e.what.startswith("receive:Potion of Healing|DMG*2") for e in newest)


@requires_corpus
class TestNotes:
    async def test_a_characters_own_notes_are_saved_from_the_sheet(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        await ac.post(f"/characters/{lyra}/act/notes", data={"text": "Owes Brother Varn 5 gp."})
        assert registry.gamestate.characters.get(lyra).notes == "Owes Brother Varn 5 gp."
        assert "Owes Brother Varn 5 gp." in (await ac.get(f"/characters/{lyra}?tab=features")).text

    async def test_party_notes_are_shared_and_a_stale_save_keeps_both_texts(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        journal = (await ac.get("/journal")).text
        assert "Party notes" in journal
        base = text_version("")
        await ac.post(
            f"/journal/notes/{world}", data={"text": "The bell rings at dusk.", "base": base}
        )
        assert registry.party_notes.text(world) == "The bell rings at dusk."
        refused = await ac.post(
            f"/journal/notes/{world}", data={"text": "Ferryman lies.", "base": base}
        )
        assert "Someone saved the notes meanwhile" in refused.text
        assert "Ferryman lies." in refused.text and "The bell rings at dusk." in refused.text
        assert registry.party_notes.text(world) == "The bell rings at dusk."
        missing = await ac.post("/journal/notes/no-such-world", data={"text": "x", "base": ""})
        assert missing.status_code == 404


@requires_corpus
class TestSafetyTools:
    async def test_lines_veils_and_an_anonymous_suggestion(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        await ac.post(
            f"/safety/{world}", data={"lines": "Harm to children\n\n", "veils": "Torture"}
        )
        safety = registry.safety.of(world)
        assert (safety.lines, safety.veils) == (["Harm to children"], ["Torture"])
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        registry.gamestate.characters.update(lyra, player="alice")
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as player:
            await player.post("/login", data={"username": "alice", "password": PASSWORD})
            page = (await player.get("/safety")).text
            assert "Harm to children" in page and "Torture" in page
            await player.post(f"/safety/{world}/suggest", data={"kind": "veil", "text": "Spiders"})
            stored = registry.safety.of(world).suggested
            assert [(s.kind, s.text) for s in stored] == [("veil", "Spiders")]
            assert "alice" not in stored[0].model_dump_json()
        await ac.post(f"/safety/{world}/settle", data={"index": "0", "take": "1"})
        assert registry.safety.of(world).veils == ["Torture", "Spiders"]

    async def test_the_xcard_reaches_the_gamemaster_alone(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        registry.gamestate.characters.update(lyra, player="alice")
        since = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as player:
            await player.post("/login", data={"username": "alice", "password": PASSWORD})
            assert "X-card" in (await player.get(f"/characters/{lyra}")).text
            sent = await player.post(
                "/safety/xcard", data={"world": world or ""}, headers={"HX-Request": "true"}
            )
            assert "The gamemaster knows" in sent.text
            assert "X-card" not in (await player.get("/log/toasts", params={"since": since})).text
        entry = registry.protocol.store.newest(log_key(world), 1)[0]
        assert (entry.what, entry.actor, entry.visibility) == ("xcard", "", Visibility.GM)
        await ac.post("/log/toasts", data={"toasts": "off"})
        toast = (await ac.get("/log/toasts", params={"since": since})).text
        assert "X-card" in toast


class TestBastionRules:
    def test_special_facilities_by_level(self) -> None:
        assert [special_slots(n) for n in (4, 5, 8, 9, 13, 17, 20)] == [0, 2, 2, 4, 5, 6, 6]


@requires_corpus
class TestABastion:
    async def test_build_order_and_a_turn(
        self, table: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.gamestate.worlds.update(world, source_set=SourceSet(sources=["PHB", "MM", "XDMG"]))
        page = (await ac.get(f"/characters/{lyra}/bastion")).text
        assert "Arcane Study" in page and "Demiplane" not in page  # level 17
        url = f"/characters/{lyra}/bastion"
        await ac.post(url, data={"op": "add", "facility": "Arcane Study|XDMG"})
        await ac.post(url, data={"op": "add", "facility": "Arcane Study|XDMG"})  # not twice
        await ac.post(url, data={"op": "add", "facility": "Smithy|XDMG"})
        bastion = registry.bastions.of(lyra)
        assert [f.ref for f in bastion.facilities] == ["Arcane Study|XDMG", "Smithy|XDMG"]
        refused = await ac.post(url, data={"op": "order", "index": "0", "order": "trade"})
        assert "not an order this facility takes" in refused.text
        await ac.post(url, data={"op": "order", "index": "0", "order": "craft"})
        await ac.post(url, data={"op": "turn"})
        bastion = registry.bastions.of(lyra)
        assert bastion.turns == 1 and all(not f.order for f in bastion.facilities)
        recent = [e.what for e in registry.protocol.store.newest(log_key(world), 5)]
        assert "bastion:1:Arcane Study|XDMG=craft" in recent
        # The Arcane Study's first craft option, an Arcane Focus, is in the pack.
        assert bastion.yields == ["Arcane Study|XDMG|item|Arcane Focus|XPHB|1"]
        held = registry.gamestate.characters.get(lyra).inventory
        assert any(i.item.name == "Arcane Focus" for i in held)
        assert "Bastion" in (await ac.get(f"/characters/{lyra}")).text
