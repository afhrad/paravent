"""A whole evening at the table, through the routes: the gamemaster and two players.

Each piece has its own tests; this one plays them one after another on the same world,
so a seam between them shows: a page that breaks once something else has happened, a
step that leaves the next one nothing to work with. Every page the players and the
gamemaster read along the way must come back whole.
"""

from __future__ import annotations

import html
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import PlayerCharacter
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus

PASSWORD = "evening-password-1"
EXAMPLES = Path(__file__).resolve().parent.parent / "documentation/examples"


@asynccontextmanager
async def _client(registry: StoreRegistry, username: str) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": username, "password": PASSWORD})
        yield client


async def _reads(client: AsyncClient, *urls: str) -> None:
    """Every page comes back whole."""
    for url in urls:
        response = await client.get(url)
        assert response.status_code == 200, f"{url}: {response.status_code}"
        assert "Internal Server Error" not in response.text, url


@pytest.fixture
def table(tmp_path: Path, corpus: Path) -> tuple[StoreRegistry, str, str, str]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    registry.auth.create_user("bob", PASSWORD, Role.PLAYER)
    world = registry.gamestate.worlds.create(
        World(name="W", source_set=SourceSet(sources=["PHB", "MM", "DMG", "XGE", "XDMG"]))
    )
    ids = []
    for name, player in (("lyra-mooncaller", "alice"), ("brunhild-ironvow", "bob")):
        data = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
        data.pop("$schema", None)
        data.update(world_id=world.id, player=player, currency={"gp": 300})
        ids.append(registry.gamestate.characters.create(PlayerCharacter.model_validate(data)).id)
    return registry, world.id, ids[0], ids[1]


@requires_corpus
async def test_an_evening_at_the_table(table: tuple[StoreRegistry, str, str, str]) -> None:
    registry, world, lyra, brunhild = table
    async with (
        _client(registry, "dm") as gm,
        _client(registry, "alice") as alice,
        _client(registry, "bob") as bob,
    ):

        async def everyone_reads() -> None:
            await _reads(
                gm, "/gm", f"/gm/session?world={world}", f"/gm/quests?world={world}", "/log"
            )
            for client, pc in ((alice, lyra), (bob, brunhild)):
                await _reads(
                    client,
                    f"/characters/{pc}",
                    f"/characters/{pc}/story",
                    f"/characters/{pc}/downtime",
                    "/journal",
                    "/log",
                )

        # Before play: the session page, then the session starts.
        await everyone_reads()
        await gm.post(f"/gm/session/{world}/start")
        assert registry.play_sessions.of(world) is not None

        # On the road until something comes up, then it becomes a fight.
        journey = None
        for _ in range(30):
            await gm.post(
                f"/gm/clock/{world}/travel",
                data={"amount": "1", "unit": "day", "environment": "Forest|XGE", "checks": "3"},
            )
            journey = registry.clock.of(world).journey
            if journey is not None and journey.monsters:
                break
        assert journey is not None and journey.monsters and journey.miles > 0
        await everyone_reads()
        made = await gm.post(f"/gm/clock/{world}/travel_encounter")
        encounter_id = made.headers["location"].rsplit("/", 1)[1]
        started = await gm.post(f"/gm/encounters/{encounter_id}/run")
        combat_url = started.headers["location"]
        combat_id = combat_url.rsplit("/", 1)[1]
        combat = registry.combats.get(combat_id)
        assert {lyra, brunhild} <= {c.character_id for c in combat.combatants}
        await _reads(alice, f"/play/combat/{combat_id}")
        await _reads(bob, f"/play/combat/{combat_id}")
        await everyone_reads()

        # The foes fall, the fight ends, the experience is handed out once.
        for foe in [c for c in combat.combatants if c.character_id is None]:
            await gm.post(f"{combat_url}/c/{foe.id}/hp", data={"mode": "damage", "amount": "999"})
        await gm.post(f"{combat_url}/end")
        await gm.post(f"{combat_url}/award", data={"xp": "150"})
        earned = registry.gamestate.characters.get(lyra).experience
        await gm.post(f"{combat_url}/award", data={"xp": "150"})
        assert earned and registry.gamestate.characters.get(lyra).experience == earned
        await _reads(gm, combat_url, f"/gm/combat/{combat_id}/recap")
        await everyone_reads()

        # What they found is shared out.
        found = await gm.post(f"{combat_url}/treasure")
        treasure = registry.treasure.get(found.headers["location"].split("?")[0].rsplit("/", 1)[1])
        await gm.post(f"/gm/treasure/{treasure.id}/items", data={"ref": "Potion of Healing|DMG"})
        saved = await gm.post(
            f"/gm/treasure/{treasure.id}",
            data={"name": "Bandits' purse", "place": f"w:{world}", "gp": "40"},
        )
        assert saved.status_code == 303, [
            m for m in html.unescape(saved.text).split("<") if "alert" in m
        ][:3]
        purse = registry.gamestate.characters.get(brunhild).currency.gp
        await gm.post(
            f"/gm/treasure/{treasure.id}/share",
            data={"item.0": lyra, "coins_to": [lyra, brunhild]},
        )
        assert any(
            i.item.name == "Potion of Healing"
            for i in registry.gamestate.characters.get(lyra).inventory
        )
        assert registry.gamestate.characters.get(brunhild).currency.gp == purse + 20
        await everyone_reads()

        # A chase through the town: a round, then the quarry is caught.
        await gm.post(
            f"/gm/chase/{world}/start",
            data={f"side_{lyra}": "quarry", "pursuer": "2 Goblin", "distance": "30"},
        )
        chase = registry.chases.of(world)
        assert chase is not None
        await _reads(alice, f"/characters/{lyra}")
        for _ in chase.runners:
            await gm.post(f"/gm/chase/{world}/turn")
        await gm.post(f"/gm/chase/{world}/round")
        assert registry.chases.of(world).round == 2  # type: ignore[union-attr]
        await gm.post(f"/gm/chase/{world}/end", data={"outcome": "caught"})
        assert registry.chases.of(world) is None
        await everyone_reads()

        # Between adventures: downtime, the shop, the bastion.
        await gm.post(f"/gm/clock/{world}/downtime", data={"days": "10"})
        await alice.post(
            f"/characters/{lyra}/downtime",
            data={"op": "start", "kind": "craft", "item": "Dagger|PHB"},
        )
        project = registry.downtime.of(lyra).projects[0]
        await alice.post(
            f"/characters/{lyra}/downtime", data={"op": "spend", "project": project.id, "days": "1"}
        )
        assert registry.downtime.of(lyra).days == 9
        await gm.post(f"/gm/shop/{world}/open", data={"name": "The Anvil", "markup": "100"})
        await bob.post(
            f"/characters/{brunhild}/shop", data={"op": "buy", "item": "Torch|PHB", "count": "2"}
        )
        assert any(
            i.item.name == "Torch" for i in registry.gamestate.characters.get(brunhild).inventory
        )
        bastion = f"/characters/{brunhild}/bastion"
        await bob.post(bastion, data={"op": "add", "facility": "Arcane Study|XDMG"})
        await bob.post(bastion, data={"op": "order", "index": "0", "order": "craft"})
        await gm.post(bastion, data={"op": "turn", "turns": "0"})
        await gm.post(bastion, data={"op": "turn", "turns": "0"})  # pressed twice: once
        assert registry.bastions.of(brunhild).turns == 1
        await _reads(bob, bastion, f"/characters/{brunhild}/followers")
        await everyone_reads()

        # A level for Lyra, answered by her player.
        await gm.post(f"/characters/{lyra}/grant-level")
        assert registry.gamestate.characters.get(lyra).pending_levels == 1
        await _reads(alice, f"/characters/{lyra}/level-up")

        # The session ends: its note tells the evening.
        ended = await gm.post(f"/gm/session/{world}/end")
        assert ended.status_code == 303
        (note,) = registry.session_notes.list(world)
        text = html.unescape(note.text)
        for said in (
            "Travelled",
            "A chase:",
            "Lyra Mooncaller: 150 XP",
            "Lyra Mooncaller received Potion of Healing",
            "Brunhild Ironvow received",
            "A bastion turn for Brunhild Ironvow",
            "10 days of downtime granted",
        ):
            assert said in text, text.replace("\n", " / ")
        await everyone_reads()
