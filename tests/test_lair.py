"""Lair actions and turn-start reminders (documentation/COMBAT.md §4) — real corpus."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Combat, Encounter, EncounterMonster, Role, SourceSet, World
from app.models.protocol import ProtocolEntry
from app.services.characters import CharacterService
from app.services.combat import LAIR, CombatService, SheetCharacters
from app.services.dice import Dice
from app.services.difficulty import DifficultyRules
from app.services.encounters import EncounterService
from app.services.i18n import UiCatalogue
from app.services.protocol import ProtocolService
from app.services.references import ReferenceService
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore
from app.store.protocol import ProtocolStore
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.protocol_pages import headline
from tests.conftest import corpus_path
from tests.test_dice_text_events import Scripted

GM = "Thorsten"
PASSWORD = "correct horse battery"


class Lair:
    """A red dragon in its lair with two goblins, a troll elsewhere; scripted dice."""

    def __init__(self, root: Path, compendium: CompendiumStore) -> None:
        self.store = GameStateStore(root)
        world = self.store.worlds.create(World(name="Peaks", source_set=SourceSet(sources=["MM"])))
        rules = DifficultyRules(reference=corpus_path())
        self.encounters = EncounterService(self.store, compendium, rules)
        self.dice = Scripted()
        self.log = ProtocolStore(root / "protocol")
        protocol = ProtocolService(self.log)
        self.combats = CombatService(
            self.store,
            self.encounters.monster,
            SheetCharacters(CharacterService(self.store, compendium, root, protocol)),
            Dice(self.dice),
            legendary_group=ReferenceService(compendium, self.store).legendary_group,
            rules=rules,
            protocol=protocol,
        )
        self.world = world
        self.encounter = self.store.encounters.create(
            Encounter(
                name="The Red's Lair",
                world_id=world.id,
                monsters=[
                    EncounterMonster(name="Adult Red Dragon", source="MM"),
                    EncounterMonster(name="Goblin", source="MM", count=2),
                ],
            )
        )

    def fight_log(self, combat: Combat) -> list[ProtocolEntry]:
        """The fight's entries in the log, newest first."""
        return [e for e in reversed(self.log.entries(self.world.id)) if e.scope.combat == combat.id]

    def start(self, dragon: int, goblins: int) -> Combat:
        combat = self.combats.start(self.encounter_with_rolls(), GM)
        for label, value in (("Adult Red Dragon", dragon), ("Goblin 1", goblins)):
            combat = self.combats.set_initiative(combat.id, self.named(combat, label), value, GM)
        return combat

    def encounter_with_rolls(self) -> Encounter:
        self.dice.values.extend([10, 10])
        return self.encounter

    def named(self, combat: Combat, label: str) -> str:
        return next(c.id for c in combat.combatants if self.combats.label_of(c) == label)

    def order(self, combat: Combat) -> list[str]:
        return [
            t.group if t.is_lair else self.combats.label_of(t.members[0])
            for t in self.combats.order(self.combats.get(combat.id))
        ]


@pytest.fixture
def lair(tmp_path: Path, loaded_compendium: CompendiumStore) -> Lair:
    return Lair(tmp_path / "lib", loaded_compendium)


class TestOrder:
    def test_the_lair_acts_on_20_losing_ties(self, lair: Lair) -> None:
        combat = lair.start(dragon=12, goblins=20)
        assert lair.order(combat) == ["Goblin 1", LAIR, "Adult Red Dragon"]
        combat = lair.start(dragon=22, goblins=5)
        assert lair.order(combat) == ["Adult Red Dragon", LAIR, "Goblin 1"]

    def test_the_turn_passes_through_the_lair(self, lair: Lair) -> None:
        combat = lair.start(dragon=12, goblins=20)
        combat = lair.combats.begin(combat.id, GM)
        combat = lair.combats.next_turn(combat.id, GM)
        assert combat.turn == LAIR
        latest = lair.fight_log(combat)[0]
        assert latest.what == "lair" and latest.subject_name == "Red Dragon"
        due = lair.combats.reminders(combat)
        [(name, entries)] = due.lair
        assert name == "Red Dragon" and entries  # its lair actions, to read out

    def test_switched_off_the_lair_leaves_the_order(self, lair: Lair) -> None:
        combat = lair.combats.begin(lair.start(dragon=12, goblins=20).id, GM)
        combat = lair.combats.next_turn(combat.id, GM)
        combat = lair.combats.toggle_lair(combat.id, GM)
        assert not combat.lair and LAIR not in lair.order(combat)
        assert combat.turn == lair.combats.order(combat)[1].group  # the dragon acts next
        combat = lair.combats.toggle_lair(combat.id, GM)
        assert combat.lair and LAIR in lair.order(combat)

    def test_no_lair_actions_once_its_creature_is_down(self, lair: Lair) -> None:
        combat = lair.combats.begin(lair.start(dragon=12, goblins=20).id, GM)
        combat = lair.combats.damage(combat.id, lair.named(combat, "Adult Red Dragon"), 999, GM)
        combat = lair.combats.next_turn(combat.id, GM)
        assert combat.turn != LAIR  # skipped, as a down group's turn is

    def test_a_2024_dragon_has_no_lair_turn(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        table = Lair(tmp_path / "lib", loaded_compendium)
        table.encounter = table.store.encounters.create(
            Encounter(
                name="2024", monsters=[EncounterMonster(name="Adult Red Dragon", source="XMM")]
            )
        )
        combat = table.combats.start(table.encounter_with_rolls(), GM)
        assert not any(t.is_lair for t in table.combats.order(combat))


class TestReminders:
    def test_the_dragons_turn(self, lair: Lair) -> None:
        combat = lair.combats.begin(lair.start(dragon=22, goblins=5).id, GM)
        due = lair.combats.reminders(combat)
        assert [(label, n) for _id, label, n in due.legendary] == [("Adult Red Dragon", 3)]
        [breath] = due.recharge
        assert (breath.ability, breath.at) == ("Fire Breath", 5)

    def test_a_recharge_roll_is_logged(self, lair: Lair) -> None:
        combat = lair.combats.begin(lair.start(dragon=22, goblins=5).id, GM)
        dragon = lair.named(combat, "Adult Red Dragon")
        lair.dice.values.extend([4, 6])
        combat = lair.combats.act(
            combat.id, dragon, "recharge", {"ability": "Fire Breath", "at": "5"}, GM
        )
        latest = lair.fight_log(combat)[0]
        assert (latest.what, latest.amount, latest.outcome) == (
            "recharge:Fire Breath",
            4,
            "failure",
        )
        combat = lair.combats.act(
            combat.id, dragon, "recharge", {"ability": "Fire Breath", "at": "5"}, GM
        )
        latest = lair.fight_log(combat)[0]
        assert (latest.amount, latest.outcome) == (6, "success")
        page = Page(UiCatalogue().translator("en"))
        assert headline(page, latest) == "Adult Red Dragon: Fire Breath recharged (6)"

    def test_a_hurt_troll_regenerates(self, lair: Lair) -> None:
        lair.encounter = lair.store.encounters.create(
            Encounter(name="Bridge", monsters=[EncounterMonster(name="Troll", source="MM")])
        )
        lair.dice.values.extend([10])
        combat = lair.combats.begin(lair.combats.start(lair.encounter, GM).id, GM)
        assert lair.combats.reminders(combat).regeneration == ()  # unhurt: nothing to regain
        troll = combat.combatants[0].id
        combat = lair.combats.damage(combat.id, troll, 20, GM)
        [regen] = lair.combats.reminders(combat).regeneration
        assert regen.amount == 10

    @pytest.mark.parametrize("lang", ["en", "de"])
    def test_the_new_log_entries_read_in_both_languages(self, lair: Lair, lang: str) -> None:
        combat = lair.combats.begin(lair.start(dragon=12, goblins=20).id, GM)
        combat = lair.combats.next_turn(combat.id, GM)
        combat = lair.combats.toggle_lair(combat.id, GM)
        combat = lair.combats.toggle_lair(combat.id, GM)
        page = Page(UiCatalogue().translator(lang))
        entries = lair.fight_log(combat)
        assert {"lair", "lair_on", "lair_off"} <= {e.what for e in entries}
        for entry in entries:
            text = headline(page, entry)
            assert "{" not in text and not text.startswith(("combat.", "protocol.")), (
                entry.kind,
                text,
            )


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


class TestPages:
    async def test_the_statblock_has_the_lair(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _registry = client
        page = (await ac.get("/ref/monster/MM/Adult Red Dragon")).text
        assert "Lair actions" in page and "Regional effects" in page
        assert "Lair actions" not in (await ac.get("/ref/monster/MM/Goblin")).text

    async def test_the_tracker_shows_the_lair_and_what_is_due(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        encounter = registry.gamestate.encounters.create(
            Encounter(
                name="The Red's Lair",
                monsters=[EncounterMonster(name="Adult Red Dragon", source="MM")],
            )
        )
        combat = registry.combats.start(encounter, GM)
        dragon = combat.combatants[0].id
        registry.combats.set_initiative(combat.id, dragon, 22, GM)
        registry.combats.begin(combat.id, GM)
        url = f"/gm/combat/{combat.id}"
        page = (await ac.get(url)).text
        assert "Lair actions on" in page and "Lair actions of Adult Red Dragon" in page
        assert "Turn start" in page and "regains 3 legendary actions" in page
        assert "Fire Breath recharges on 5–6" in page
        await ac.post(f"{url}/next")
        page = (await ac.get(url)).text
        assert "Lair actions · Red Dragon" in page and "geyser" in page  # its first action
        await ac.post(f"{url}/lair")
        assert "Lair actions off" in (await ac.get(url)).text
