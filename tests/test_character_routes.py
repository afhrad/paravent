"""The sheet's routes: who may see what, actions with and without HTMX, the GM list."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, User
from app.models.pc import PlayerCharacter
from app.store.registry import StoreRegistry
from tests.conftest import log_about

PASSWORD = "correct horse"
HX = {"HX-Request": "true"}


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    return registry


@pytest.fixture
def people(registry: StoreRegistry) -> dict[str, User]:
    auth = registry.auth
    return {
        "dm": auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana"),
        "alice": auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice"),
        "bob": auth.create_user("bob", PASSWORD, Role.PLAYER, "Bob"),
    }


@pytest.fixture
def thorin(registry: StoreRegistry, people: dict[str, User]) -> PlayerCharacter:
    return registry.gamestate.characters.create(
        PlayerCharacter.model_validate(
            {
                "name": "Thorin",
                "player": "alice",
                "classes": [{"class": "Homebrew Fighter", "level": 3}],
                "abilities": {"str": 16, "con": 14},
                "overrides": {"hp_max": 28, "slots.1": 2},
            }
        )
    )


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def login(client: AsyncClient, username: str) -> None:
    await client.post("/login", data={"username": username, "password": PASSWORD})


class TestAccess:
    async def test_owner_sees_the_sheet(self, client: AsyncClient, thorin: PlayerCharacter) -> None:
        await login(client, "alice")
        response = await client.get(f"/characters/{thorin.id}")
        assert response.status_code == 200
        assert 'id="sheet"' in response.text
        assert "/static/js/htmx.min.js" in response.text and "/static/js/sheet.js" in response.text

    async def test_another_player_gets_not_found(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "bob")
        for path in ("", "/sheet", "/events"):
            assert (await client.get(f"/characters/{thorin.id}{path}")).status_code == 404
        response = await client.post(
            f"/characters/{thorin.id}/act/hp", data={"mode": "damage", "amount": "5"}
        )
        assert response.status_code == 404

    async def test_gamemaster_sees_every_sheet_and_the_file(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "dm")
        text = (await client.get(f"/characters/{thorin.id}")).text
        assert f"characters/thorin-{thorin.id}.json" in text

    async def test_player_does_not_see_the_file_path(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        assert "cs-file" not in (await client.get(f"/characters/{thorin.id}")).text

    async def test_anonymous_is_sent_to_login(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        response = await client.get(f"/characters/{thorin.id}")
        assert response.headers["location"].startswith("/login?next=")

    async def test_player_list_links_to_the_sheet(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        assert f'href="/characters/{thorin.id}"' in (await client.get("/play")).text


class TestActions:
    async def test_htmx_action_returns_the_new_sheet(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        response = await client.post(
            f"/characters/{thorin.id}/act/hp",
            data={"mode": "damage", "amount": "5", "tab": "skills"},
            headers=HX,
        )
        assert response.status_code == 200
        assert response.text.startswith('<div id="sheet"')
        assert 'data-tab="skills"' in response.text
        assert registry.gamestate.characters.get(thorin.id).state.hp == 23
        assert f'data-rev="{registry.gamestate.characters.revision(thorin.id)}"' in response.text

    async def test_plain_form_post_redirects_back(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        response = await client.post(
            f"/characters/{thorin.id}/act/condition", data={"key": "prone"}
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/characters/{thorin.id}"
        assert registry.gamestate.characters.get(thorin.id).state.conditions == ["prone"]

    async def test_a_refusal_is_explained_in_the_sheet(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        response = await client.post(
            f"/characters/{thorin.id}/act/slot", data={"level": "5", "op": "spend"}, headers=HX
        )
        assert response.status_code == 200
        assert "No spell slot of that level left." in response.text

    async def test_a_refusal_without_js_carries_the_message(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        response = await client.post(
            f"/characters/{thorin.id}/act/slot", data={"level": "5", "op": "spend"}
        )
        assert "flash=play.error.no_slot" in response.headers["location"]
        page = await client.get(response.headers["location"])
        assert "No spell slot of that level left." in page.text

    async def test_rolls_are_shared_and_name_the_roller(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "dm")
        await client.post(
            f"/characters/{thorin.id}/act/roll",
            data={"kind": "check", "subject": "str"},
            headers=HX,
        )
        await client.post("/logout")
        await login(client, "alice")
        text = (await client.get(f"/characters/{thorin.id}/sheet")).text
        assert "log-embed" in text  # the sheet's dice log is a slice of the log
        # It fills itself, never the sheet whose root sets hx-target="this".
        slot = re.search(r'<div class="log-embed"[^>]*>', text)
        assert slot is not None and 'hx-target="this"' in slot.group(0)
        slice_ = await client.get(
            "/log/panel", params={"filter": "character", "character": thorin.id, "embed": "1"}
        )
        assert "Strength check" in slice_.text and "Dana" in slice_.text

    async def test_the_sheet_follows_the_viewers_language(
        self,
        client: AsyncClient,
        registry: StoreRegistry,
        thorin: PlayerCharacter,
        people: dict[str, User],
    ) -> None:
        registry.auth.set_language(people["alice"].id, "de")
        await login(client, "alice")
        text = (await client.get(f"/characters/{thorin.id}")).text
        assert "Rüstungsklasse" in text and "Rettungswürfe" in text and "Liegend" in text

    async def test_tabs_and_views_come_from_the_url(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        text = (await client.get(f"/characters/{thorin.id}?tab=gear&view=track")).text
        assert 'data-tab="gear"' in text and 'data-m="track"' in text

    async def test_a_file_error_is_shown_on_the_sheet(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        store = registry.gamestate.characters
        path = store.path(thorin.id)
        assert path is not None
        path.write_text("{ broken")
        store.reload_paths({path})
        await login(client, "alice")
        text = (await client.get(f"/characters/{thorin.id}")).text
        assert "has an error" in text and "Thorin" in text


class TestGamemasterList:
    async def test_list_create_and_assign(
        self,
        client: AsyncClient,
        registry: StoreRegistry,
        thorin: PlayerCharacter,
        people: dict[str, User],
    ) -> None:
        await login(client, "dm")
        listing = (await client.get("/gm/characters")).text
        assert "Thorin" in listing and "Alice" in listing
        created = await client.post("/gm/characters", data={"name": "Mira", "player": "bob"})
        assert created.status_code == 303
        mira = next(pc for pc in registry.gamestate.characters.list() if pc.name == "Mira")
        assert mira.player == "bob"
        await client.post(f"/gm/characters/{thorin.id}/assign", data={"player": ""})
        assert registry.gamestate.characters.get(thorin.id).player is None

    async def test_assigning_to_a_gamemaster_leaves_it_unassigned(
        self,
        client: AsyncClient,
        registry: StoreRegistry,
        thorin: PlayerCharacter,
        people: dict[str, User],
    ) -> None:
        await login(client, "dm")
        await client.post(f"/gm/characters/{thorin.id}/assign", data={"player": "dm"})
        assert registry.gamestate.characters.get(thorin.id).player is None

    async def test_unknown_character_is_404(
        self, client: AsyncClient, people: dict[str, User]
    ) -> None:
        await login(client, "dm")
        assert (await client.post("/gm/characters/nope/assign", data={})).status_code == 404

    async def test_players_cannot_use_it(
        self, client: AsyncClient, people: dict[str, User]
    ) -> None:
        await login(client, "alice")
        assert (await client.get("/gm/characters")).status_code == 403
        assert (await client.post("/gm/characters", data={"name": "X"})).status_code == 403


class TestRendering:
    async def test_every_state_renders(
        self, client: AsyncClient, registry: StoreRegistry, people: dict[str, User]
    ) -> None:
        """A character in every unusual state at once: dying, pact magic, gear, a log."""
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Edge Case",
                    "player": "alice",
                    "classes": [{"class": "Homebrew Hexblade", "level": 5}],
                    "spells": {"cantrips": ["Eldritch Blast|PHB"], "known": ["Hex|PHB"]},
                    "inventory": [
                        {"item": "Lucky coin", "quantity": 3, "attuned": True, "notes": "warm"}
                    ],
                    "resources": [{"name": "Hexblade's Curse", "max": 1, "recharge": "short"}],
                    "overrides": {
                        "hp_max": 30,
                        "pact_slots": 2,
                        "pact_slot_level": 3,
                        "spell_save_dc": 14,
                        "spell_attack": 6,
                    },
                    "state": {
                        "hp": 0,
                        "death_saves": {"successes": 1, "failures": 2},
                        "conditions": ["unconscious"],
                        "exhaustion": 1,
                        "inspiration": True,
                        "concentration": "Hex|PHB",
                    },
                    "log": [
                        {"kind": "death_save", "total": 1, "outcome": "fumble", "by": "Alice"},
                        {"kind": "rest", "subject": "short"},
                    ],
                    "pronouns": "they/them",
                    "notes": "Owes the patron a favour.",
                }
            )
        )
        registry.copy_old_logs()  # the file's old log, brought into the log on start
        await login(client, "alice")
        text = (await client.get(f"/characters/{pc.id}?tab=gear")).text
        logged = (
            await client.get(
                "/log/panel", params={"filter": "character", "character": pc.id, "embed": "1"}
            )
        ).text
        assert "Death saves" in text and "Roll death save" in text
        assert "Pact" in text and "Concentrating: Hex" in text
        assert "Eldritch Blast|PHB is not in your books" in text
        assert "Lucky coin" in text and "×3" in text and "attuned" in text
        assert "Hexblade&#39;s Curse" in text or "Hexblade's Curse" in text
        assert "Natural 1" in logged and "Short rest" in logged
        assert "Owes the patron a favour." in text
        assert 'class="cbtn on"' in text
        response = await client.post(f"/characters/{pc.id}/act/death_save", headers=HX)
        assert response.status_code == 200


class TestOwnership:
    """Characters name their player by username, so the file says who plays it."""

    def test_ownership_ignores_case(self) -> None:
        pc = PlayerCharacter.model_validate({"player": "Tenewaja"})
        assert pc.belongs_to("tenewaja") and not pc.belongs_to("alice")

    def test_legacy_player_id_is_read(self) -> None:
        assert PlayerCharacter.model_validate({"player_id": "abc123"}).player == "abc123"

    def test_load_rewrites_legacy_ids_to_usernames(
        self, registry: StoreRegistry, people: dict[str, User]
    ) -> None:
        store = registry.gamestate.characters
        path = store.root / "old.json"
        legacy = {"id": "old", "name": "Old", "player_id": people["alice"].id}
        path.write_text(json.dumps(legacy))
        registry.load()
        assert registry.gamestate.characters.get("old").player == "alice"
        assert '"player": "alice"' in path.read_text()
        assert "player_id" not in path.read_text()

    async def test_renaming_a_player_follows_into_the_file(
        self,
        client: AsyncClient,
        registry: StoreRegistry,
        thorin: PlayerCharacter,
        people: dict[str, User],
    ) -> None:
        await login(client, "dm")
        await client.post(
            f"/gm/players/{people['alice'].id}",
            data={"username": "alicia", "language": "en", "active": "on"},
        )
        assert registry.gamestate.characters.get(thorin.id).player == "alicia"
        await client.post("/logout")
        await client.post("/login", data={"username": "alicia", "password": PASSWORD})
        assert (await client.get(f"/characters/{thorin.id}")).status_code == 200

    async def test_a_named_player_without_account_waits(
        self, client: AsyncClient, registry: StoreRegistry, people: dict[str, User]
    ) -> None:
        pc = registry.gamestate.characters.create(PlayerCharacter(name="Waiting", player="carol"))
        await login(client, "dm")
        listing = (await client.get("/gm/characters")).text
        assert "@carol" in listing
        registry.auth.create_user("carol", PASSWORD, Role.PLAYER)
        await client.post("/logout")
        await login(client, "carol")
        assert f'href="/characters/{pc.id}"' in (await client.get("/play")).text


class TestDelete:
    async def test_a_player_deletes_their_own_character(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        assert f'href="/characters/{thorin.id}/delete"' in (await client.get("/play")).text
        confirm = await client.get(f"/characters/{thorin.id}/delete")
        assert confirm.status_code == 200 and "Delete Thorin?" in confirm.text
        path = registry.gamestate.characters.path(thorin.id)
        assert path is not None and path.exists()
        response = await client.post(f"/characters/{thorin.id}/delete")
        assert response.status_code == 303 and response.headers["location"] == "/play"
        assert registry.gamestate.characters.find(thorin.id) is None and not path.exists()
        assert f"/characters/{thorin.id}" not in (await client.get("/play")).text

    async def test_another_player_cannot(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client, "bob")
        assert (await client.get(f"/characters/{thorin.id}/delete")).status_code == 404
        assert (await client.post(f"/characters/{thorin.id}/delete")).status_code == 404
        assert registry.gamestate.characters.find(thorin.id) is not None

    async def test_the_gamemaster_deletes_any_and_returns_to_the_list(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        await login(client, "dm")
        assert f'href="/characters/{thorin.id}/delete"' in (await client.get("/gm/characters")).text
        response = await client.post(f"/characters/{thorin.id}/delete")
        assert response.headers["location"] == "/gm/characters"
        assert registry.gamestate.characters.find(thorin.id) is None

    async def test_the_confirmation_is_german_for_a_german_reader(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        await client.post("/language", data={"lang": "de", "next": "/"})
        assert "Thorin löschen?" in (await client.get(f"/characters/{thorin.id}/delete")).text


class TestRollSounds:
    """A roll tells the browser to play its sound (app/routes/rolls.py, static/js/sound.js)."""

    async def test_a_roll_announces_itself_and_other_actions_do_not(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        rolled = await client.post(
            f"/characters/{thorin.id}/act/roll",
            data={"kind": "custom", "expr": "1d20+2"},
            headers=HX,
        )
        event = json.loads(rolled.headers["HX-Trigger"])["paravent-roll"]
        assert event["outcome"] in ("", "crit", "fumble")
        hurt = await client.post(
            f"/characters/{thorin.id}/act/hp", data={"mode": "damage", "amount": "3"}, headers=HX
        )
        assert "HX-Trigger" not in hurt.headers

    async def test_the_gamemasters_card_rolls_too(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "dm")
        rolled = await client.post(
            f"/gm/party/{thorin.id}/act/roll", data={"kind": "custom", "expr": "2d6"}, headers=HX
        )
        assert json.loads(rolled.headers["HX-Trigger"]) == {"paravent-roll": {"outcome": ""}}

    def test_a_natural_20_among_new_rolls_wins(self) -> None:
        from app.models.protocol import EntryKind, ProtocolEntry, RollRecord
        from app.routes.rolls import new_roll

        damage = ProtocolEntry(kind=EntryKind.DAMAGE, roll=RollRecord(expression="2d8", total=9))
        crit = ProtocolEntry(
            kind=EntryKind.ATTACK, roll=RollRecord(expression="1d20+5", total=25), outcome="crit"
        )
        assert new_roll([damage, crit]) is crit  # the attack's sound, not the damage's
        assert new_roll([damage]) is damage
        rest = ProtocolEntry(kind=EntryKind.REST, what="rest:short")
        assert new_roll([rest]) is None  # not dice
        assert new_roll([]) is None


class TestResilientSave:
    async def test_a_missing_save_is_asked_once_and_recorded(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        store = registry.gamestate.characters
        store.update(thorin.id, feats=[{"name": "Resilient", "source": "PHB"}])
        await login(client, "alice")
        page = (await client.get(f"/characters/{thorin.id}")).text
        assert "Resilient: which saving throw" in page and 'value="con"' in page
        answer = await client.post(
            f"/characters/{thorin.id}/act/saving_throw", data={"ability": "con"}, headers=HX
        )
        assert store.get(thorin.id).saving_throws == ["con"]
        assert "which saving throw" not in answer.text
        again = await client.post(
            f"/characters/{thorin.id}/act/saving_throw", data={"ability": "wis"}, headers=HX
        )
        assert store.get(thorin.id).saving_throws == ["con"]  # nothing more is owed
        assert "cs-flash" in again.text


class TestHealingOthers:
    async def test_a_potion_heals_and_is_used_up(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        store = registry.gamestate.characters
        store.update(
            thorin.id,
            inventory=[{"item": "Potion of Healing|DMG", "quantity": 2}],
            state={"hp": 5},
        )
        await login(client, "alice")
        # without a corpus the potion is unknown: no Drink, and the action is refused
        page = (await client.get(f"/characters/{thorin.id}")).text
        assert "/act/drink" not in page
        refused = await client.post(
            f"/characters/{thorin.id}/act/drink", data={"index": "0"}, headers=HX
        )
        assert refused.status_code == 200 and store.get(thorin.id).inventory[0].quantity == 2


class TestConditionsBendRolls:
    async def test_a_poisoned_check_rolls_twice_and_says_why(
        self, client: AsyncClient, registry: StoreRegistry, thorin: PlayerCharacter
    ) -> None:
        store = registry.gamestate.characters
        store.update(thorin.id, state={"conditions": ["poisoned"]})
        await login(client, "alice")
        page = (await client.get(f"/characters/{thorin.id}")).text
        assert "Poisoned: Disadvantage on attack rolls, Disadvantage on ability checks" in page
        await client.post(
            f"/characters/{thorin.id}/act/roll",
            data={"kind": "check", "subject": "str"},
            headers=HX,
        )
        roll = log_about(registry, thorin.id)[0].roll
        assert roll is not None and len(roll.dice[0].results) == 2  # two dice, and why
        assert (roll.mode, roll.reasons) == ("disadvantage", ["condition:poisoned"])
        await client.post("/language", data={"lang": "de", "next": "/"})
        panel = await client.get(
            "/log/panel", params={"filter": "character", "character": thorin.id}
        )
        assert 'title="Vergiftet"' in panel.text


class TestPlayerBar:
    async def test_the_players_phone_bar_and_where_it_stays_away(
        self, client: AsyncClient, thorin: PlayerCharacter
    ) -> None:
        await login(client, "alice")
        home = (await client.get("/play")).text
        assert 'class="player-bar"' in home and "player-bar-new" not in home
        # "New character" is in the user menu, with the icon switches
        assert 'href="/chargen/new" class="usermenu-link"' in home
        assert 'class="usermenu-section usermenu-switches"' in home
        assert 'data-set="system"' not in home and 'data-set="dark"' in home
        # the Paravent mark on the left of the top row, without the name
        top = home[home.index('class="shell-home"') :]
        assert top.index('href="#i-paravent"') < top.index("</a>")
        assert "Paravent</span>" not in top[: top.index("</a>")]
        sheet = (await client.get(f"/characters/{thorin.id}")).text
        assert 'class="player-bar"' not in sheet  # the sheet has bottom tabs of its own
        await client.post("/logout")
        await login(client, "dm")
        assert 'class="player-bar"' not in (await client.get("/gm")).text
