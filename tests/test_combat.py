"""The combat tracker's rules (documentation/COMBAT.md) — against the real corpus."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    EDITION_CLASSIC,
    Combat,
    Combatant,
    Encounter,
    EncounterMonster,
    Role,
    SourceSet,
    World,
)
from app.models.naming import ListPart, NamingStyle
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, RollRecord, Scope
from app.services.characters import CharacterService
from app.services.combat import (
    CombatError,
    CombatNotFound,
    CombatService,
    SeenCombatant,
    SheetCharacters,
    monster_initiative,
    proficiency_bonus,
)
from app.services.dice import Dice
from app.services.difficulty import DifficultyRules
from app.services.encounters import EncounterService
from app.services.events import PARTY, combat_key
from app.services.i18n import UiCatalogue
from app.services.naming import Naming
from app.services.play import Actor
from app.services.protocol import ProtocolService
from app.services.protocol_view import Filter, ProtocolReader, Viewer
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore
from app.store.npc_tables import NpcTables
from app.store.protocol import ProtocolStore
from app.store.registry import StoreRegistry
from app.ui.combat_pages import combat_log
from app.ui.context import Page
from app.ui.protocol_pages import headline, log_rows
from tests.conftest import corpus_path
from tests.test_dice_text_events import Scripted

GM = "Thorsten"
LYRA = (
    Path(__file__).resolve().parent.parent / "documentation" / "examples" / "lyra-mooncaller.json"
)


class Table:
    """A world with Lyra in it, an encounter of goblins and a bugbear, scripted dice."""

    def __init__(self, root: Path, compendium: CompendiumStore) -> None:
        self.store = GameStateStore(root)
        self.world = self.store.worlds.create(
            World(name="Grey Reed", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        data = json.loads(LYRA.read_text(encoding="utf-8"))
        data["world_id"] = self.world.id
        self.lyra = self.store.characters.create(PlayerCharacter.model_validate(data))
        # The 2014 guide's thresholds are read from the corpus (app/store/rulestables.py).
        rules = DifficultyRules(reference=corpus_path())
        self.encounters = EncounterService(self.store, compendium, rules)
        self.log = ProtocolStore(root / "protocol")
        protocol = ProtocolService(self.log)
        self.characters = CharacterService(self.store, compendium, root, protocol)
        self.dice = Scripted()
        self.combats = CombatService(
            self.store,
            self.encounters.monster,
            SheetCharacters(self.characters),
            Dice(self.dice),
            rules=rules,
            protocol=protocol,
        )
        self.encounter = self.store.encounters.create(
            Encounter(
                name="Goblin Ambush",
                world_id=self.world.id,
                monsters=[
                    EncounterMonster(name="Goblin", source="MM", count=4),
                    EncounterMonster(name="Bugbear", source="MM"),
                ],
            )
        )

    def roll(self, *faces: int) -> Table:
        self.dice.values.extend(faces)
        return self

    def start(self, goblins: int = 15, bugbear: int = 9) -> Combat:
        return self.roll(goblins, bugbear).combats.start(self.encounter, GM)

    def named(self, combat: Combat, label: str) -> str:
        return next(c.id for c in combat.combatants if self.combats.label_of(c) == label)

    def now(self, combat: Combat) -> Combat:
        return self.combats.get(combat.id)

    def fight_log(self, combat: Combat) -> list[ProtocolEntry]:
        """The fight's entries in the log, newest first."""
        return [e for e in reversed(self.log.entries(self.world.id)) if e.scope.combat == combat.id]

    def latest(self, combat: Combat) -> ProtocolEntry:
        return self.fight_log(combat)[0]

    def turn(self, combat: Combat) -> str:
        current = self.combats.current(self.now(combat))
        assert current is not None
        return self.combats.label_of(current.members[0])


@pytest.fixture
def table(tmp_path: Path, loaded_compendium: CompendiumStore) -> Table:
    return Table(tmp_path / "lib", loaded_compendium)


class TestMonsterNumbers:
    @pytest.mark.parametrize(
        ("cr", "bonus"), [(None, 2), ("1/4", 2), ("4", 2), ("5", 3), ("16", 5), ("17", 6)]
    )
    def test_proficiency_by_cr(self, cr: str | None, bonus: int) -> None:
        assert proficiency_bonus(Fraction(cr) if cr else None) == bonus

    def test_2024_initiative_adds_proficiency(self, table: Table) -> None:
        dragon = table.encounters.monster("Adult Red Dragon", "XMM")
        goblin = table.encounters.monster("Goblin", "MM")
        assert dragon is not None and goblin is not None
        assert monster_initiative(dragon)[0] == 12  # DEX +0, twice PB 6, as printed
        assert monster_initiative(goblin)[0] == 2  # DEX only


class TestStart:
    def test_the_plan_becomes_groups(self, table: Table) -> None:
        combat = table.start()
        labels = [table.combats.label_of(c) for c in combat.combatants]
        assert labels == [
            "Goblin 1",
            "Goblin 2",
            "Goblin 3",
            "Goblin 4",
            "Bugbear",
            "Lyra Mooncaller",
        ]
        goblins = [c for c in combat.combatants if c.ref == "Goblin|MM"]
        assert {g.group for g in goblins} == {goblins[0].group}
        assert {(g.initiative, g.hp, g.hp_max, g.ac) for g in goblins} == {(17, 7, 7, 15)}
        lyra = combat.combatants[-1]
        assert lyra.character_id == table.lyra.id and lyra.initiative is None
        assert combat.round == 0 and combat.world_id == table.world.id

    def test_running_again_returns_the_running_combat(self, table: Table) -> None:
        combat = table.start()
        assert table.combats.start(table.encounter, GM).id == combat.id
        table.combats.end(combat.id, GM)
        assert table.roll(1, 1).combats.start(table.encounter, GM).id != combat.id

    def test_the_order_and_the_first_turn(self, table: Table) -> None:
        combat = table.start(goblins=15, bugbear=9)  # goblins 17, bugbear 11
        order = table.combats.order(combat)
        assert [table.combats.label_of(t.members[0]) for t in order] == [
            "Goblin 1",
            "Bugbear",
            "Lyra Mooncaller",  # not rolled yet: last
        ]
        assert len(order[0].members) == 4
        combat = table.combats.begin(combat.id, GM)
        assert combat.round == 1 and table.turn(combat) == "Goblin 1"
        with pytest.raises(CombatError):
            table.combats.begin(combat.id, GM)


class TestOrder:
    def test_ties_go_to_the_bonus_then_characters(self, table: Table) -> None:
        combat = Combat(
            name="Tie",
            combatants=[
                Combatant(label="Slow", initiative=12, initiative_bonus=0),
                Combatant(label="Goblin", ref="Goblin|MM", initiative=12, initiative_bonus=2),
                Combatant(label="L", character_id=table.lyra.id, initiative=12, initiative_bonus=2),
                Combatant(label="Fast", initiative=15),
            ],
        )
        order = [table.combats.label_of(t.members[0]) for t in table.combats.order(combat)]
        assert order == ["Fast", "Lyra Mooncaller", "Goblin", "Slow"]

    def test_the_gamemaster_reorders_a_tie(self, table: Table) -> None:
        combat = table.start(goblins=10, bugbear=10)  # both 12, both DEX +2
        labels = [table.combats.label_of(t.members[0]) for t in table.combats.order(combat)]
        assert labels[:2] == ["Bugbear", "Goblin 1"]
        combat = table.combats.set_order(combat.id, table.named(combat, "Goblin 1"), -1, GM)
        labels = [table.combats.label_of(t.members[0]) for t in table.combats.order(combat)]
        assert labels[:2] == ["Goblin 1", "Bugbear"]

    def test_an_initiative_sets_the_whole_group(self, table: Table) -> None:
        combat = table.start()
        combat = table.combats.set_initiative(combat.id, table.named(combat, "Goblin 3"), 4, GM)
        assert {c.initiative for c in combat.combatants if c.ref == "Goblin|MM"} == {4}
        with pytest.raises(CombatError):
            table.combats.set_initiative(combat.id, table.named(combat, "Goblin 3"), 99, GM)

    def test_rolling_a_group(self, table: Table) -> None:
        combat = table.start()
        combat = table.roll(3).combats.roll_initiative(
            combat.id, table.named(combat, "Bugbear"), GM
        )
        bugbear = combat.combatant(table.named(combat, "Bugbear"))
        assert bugbear is not None and bugbear.initiative == 5
        entry = table.latest(combat)
        assert entry.kind is EntryKind.INITIATIVE and entry.amount == 5


class TestTurns:
    def test_a_round_wraps(self, table: Table) -> None:
        combat = table.start()
        combat = table.combats.set_initiative(
            combat.id, table.named(combat, "Lyra Mooncaller"), 1, GM
        )
        combat = table.combats.begin(combat.id, GM)
        seen = [table.turn(combat)]
        for _ in range(3):
            combat = table.combats.next_turn(combat.id, GM)
            seen.append(table.turn(combat))
        assert seen == ["Goblin 1", "Bugbear", "Lyra Mooncaller", "Goblin 1"]
        assert combat.round == 2
        combat = table.combats.previous_turn(combat.id, GM)
        assert combat.round == 1 and table.turn(combat) == "Lyra Mooncaller"

    def test_a_fallen_group_is_skipped_a_fallen_character_is_not(self, table: Table) -> None:
        combat = table.start()
        lyra = table.named(combat, "Lyra Mooncaller")
        combat = table.combats.set_initiative(combat.id, lyra, 1, GM)
        combat = table.combats.begin(combat.id, GM)
        combat = table.combats.damage(combat.id, table.named(combat, "Bugbear"), 50, GM)
        combat = table.combats.damage(combat.id, lyra, 500, GM)
        combat = table.combats.next_turn(combat.id, GM)
        assert table.turn(combat) == "Lyra Mooncaller"  # the bugbear's turn skipped

    def test_the_turn_stays_with_its_creature_when_initiative_changes(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        combat = table.combats.next_turn(combat.id, GM)
        assert table.turn(combat) == "Bugbear"
        combat = table.combats.set_initiative(combat.id, table.named(combat, "Bugbear"), 30, GM)
        assert table.turn(combat) == "Bugbear"

    def test_turns_need_a_begun_combat(self, table: Table) -> None:
        with pytest.raises(CombatError):
            table.combats.next_turn(table.start().id, GM)


class TestHitPoints:
    def test_temporary_hp_first_and_never_stacking(self, table: Table) -> None:
        combat = table.start()
        bugbear = table.named(combat, "Bugbear")
        table.combats.temp(combat.id, bugbear, 5, GM)
        table.combats.temp(combat.id, bugbear, 3, GM)
        combat = table.combats.damage(combat.id, bugbear, 8, GM)
        hit = combat.combatant(bugbear)
        assert hit is not None and (hit.hp_temp, hit.hp) == (0, 24)
        combat = table.combats.heal(combat.id, bugbear, 100, GM)
        assert combat.combatant(bugbear).hp == 27  # type: ignore[union-attr]

    def test_health_words(self, table: Table) -> None:
        combat = table.start()
        bugbear = table.named(combat, "Bugbear")  # 27 HP
        words = []
        for damage in (1, 13, 8, 5):  # 26, 13, 5, 0 of 27
            combat = table.combats.damage(combat.id, bugbear, damage, GM)
            words.append(combat.combatant(bugbear).health)  # type: ignore[union-attr]
        assert words == ["hurt", "bloodied", "critical", "down"]

    def test_a_zero_amount_is_refused(self, table: Table) -> None:
        combat = table.start()
        with pytest.raises(CombatError):
            table.combats.damage(combat.id, table.named(combat, "Bugbear"), 0, GM)

    def test_characters_take_damage_in_their_file(self, table: Table) -> None:
        combat = table.start()
        lyra = table.named(combat, "Lyra Mooncaller")
        before = table.characters.sheet(table.store.characters.get(table.lyra.id)).hp_current
        table.combats.damage(combat.id, lyra, 3, GM)
        pc = table.store.characters.get(table.lyra.id)
        assert table.characters.sheet(pc).hp_current == before - 3
        # the log names who did it, once: the tracker's entry, not the sheet's as well
        damage = [e for e in table.fight_log(combat) if e.kind is EntryKind.DAMAGE]
        assert [(e.actor_name, e.subject, e.amount) for e in damage] == [
            (GM, f"character:{table.lyra.id}", 3)
        ]


class TestConcentration:
    def test_damage_owes_a_save_and_a_roll_settles_it(self, table: Table) -> None:
        combat = table.start()
        bugbear = table.named(combat, "Bugbear")
        table.combats.concentrate(combat.id, bugbear, "Hex", GM)
        combat = table.combats.damage(combat.id, bugbear, 24, GM)
        assert combat.combatant(bugbear).save_due == 12  # type: ignore[union-attr]
        combat = table.roll(10).combats.concentration_save(combat.id, bugbear, GM)
        assert combat.combatant(bugbear).concentration is None  # type: ignore[union-attr]
        entry = table.latest(combat)
        assert entry.kind is EntryKind.CONCENTRATION
        assert entry.outcome == "failure" and entry.against == 12

    def test_small_damage_is_dc_10_and_the_gm_may_settle_it(self, table: Table) -> None:
        combat = table.start()
        bugbear = table.named(combat, "Bugbear")
        table.combats.concentrate(combat.id, bugbear, "Hex", GM)
        combat = table.combats.damage(combat.id, bugbear, 3, GM)
        assert combat.combatant(bugbear).save_due == 10  # type: ignore[union-attr]
        combat = table.combats.concentration_save(combat.id, bugbear, GM, kept=True)
        assert combat.combatant(bugbear).concentration == "Hex"  # type: ignore[union-attr]
        with pytest.raises(CombatError):
            table.combats.concentration_save(combat.id, bugbear, GM)

    def test_characters_concentrate_on_their_sheet(self, table: Table) -> None:
        combat = table.start()
        with pytest.raises(CombatError):
            table.combats.concentrate(combat.id, table.named(combat, "Lyra Mooncaller"), "x", GM)


class TestConditions:
    def test_a_timed_condition_ends_after_the_creatures_turn(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        goblin = table.named(combat, "Goblin 2")
        combat = table.combats.toggle_condition(combat.id, goblin, "Frightened", GM, rounds=1)
        assert combat.combatant(goblin).conditions == ["frightened"]  # type: ignore[union-attr]
        combat = table.combats.next_turn(combat.id, GM)  # the goblins' turn ends
        assert combat.combatant(goblin).conditions == []  # type: ignore[union-attr]
        assert any(e.what == "expired:frightened" for e in table.fight_log(combat))

    def test_toggling_off_and_an_unknown_condition(self, table: Table) -> None:
        combat = table.start()
        goblin = table.named(combat, "Goblin 1")
        table.combats.toggle_condition(combat.id, goblin, "prone", GM)
        combat = table.combats.toggle_condition(combat.id, goblin, "prone", GM)
        assert combat.combatant(goblin).conditions == []  # type: ignore[union-attr]
        with pytest.raises(CombatError):
            table.combats.toggle_condition(combat.id, goblin, "sleepy", GM)

    def test_a_characters_condition_is_in_the_file(self, table: Table) -> None:
        combat = table.start()
        table.combats.toggle_condition(
            combat.id, table.named(combat, "Lyra Mooncaller"), "poisoned", GM, rounds=2
        )
        assert "poisoned" in table.store.characters.get(table.lyra.id).state.conditions


class TestRoster:
    def test_more_goblins_continue_the_numbering(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        combat = table.roll(2).combats.add_monster(combat.id, "Goblin|MM", 2, GM)
        labels = [c.label for c in combat.combatants if c.ref == "Goblin|MM"]
        assert labels[-2:] == ["Goblin 5", "Goblin 6"]
        assert len({c.group for c in combat.combatants if c.ref == "Goblin|MM"}) == 2
        with pytest.raises(CombatError):
            table.combats.add_monster(combat.id, "Nothing|XX", 1, GM)

    def test_split_off_one_goblin(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        goblin = table.named(combat, "Goblin 4")
        combat = table.roll(1).combats.split(combat.id, goblin, GM)
        split = combat.combatant(goblin)
        assert split is not None and split.initiative == 3
        assert table.turn(combat) == "Goblin 1"  # the rest kept the turn
        with pytest.raises(CombatError):
            table.roll(12).combats.split(combat.id, goblin, GM)

    def test_someone_ad_hoc_and_hidden(self, table: Table) -> None:
        combat = table.start()
        combat = table.combats.add_other(combat.id, "Wolf", GM, ac=13, hp=11, initiative=14)
        wolf = table.named(combat, "Wolf")
        combat = table.combats.hide(combat.id, wolf, True, GM)
        assert combat.combatant(wolf).hidden  # type: ignore[union-attr]
        with pytest.raises(CombatError):
            table.combats.add_other(combat.id, "  ", GM)

    def test_a_character_once(self, table: Table) -> None:
        combat = table.start()
        with pytest.raises(CombatError):
            table.combats.add_character(combat.id, table.lyra.id, GM)

    def test_removing_the_active_creature_passes_the_turn(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        combat = table.combats.next_turn(combat.id, GM)
        combat = table.combats.remove(combat.id, table.named(combat, "Bugbear"), GM)
        assert table.turn(combat) == "Lyra Mooncaller"

    def test_editing_numbers(self, table: Table) -> None:
        combat = table.start()
        goblin = table.named(combat, "Goblin 1")
        combat = table.combats.edit(combat.id, goblin, GM, label="Boss Goblin", hp_max=12, hp=12)
        edited = combat.combatant(goblin)
        assert edited is not None and (edited.label, edited.hp, edited.hp_max) == (
            "Boss Goblin",
            12,
            12,
        )
        with pytest.raises(CombatError):
            table.combats.edit(combat.id, table.named(combat, "Lyra Mooncaller"), GM, ac=3)


class TestEnding:
    def test_summary_and_no_changes_after(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        for label in ("Goblin 1", "Goblin 2", "Bugbear"):
            combat = table.combats.damage(combat.id, table.named(combat, label), 100, GM)
        combat = table.combats.end(combat.id, GM)
        summary = table.combats.summary(combat)
        assert summary.xp == 50 + 50 + 200 and summary.characters == 1 and summary.share == 300
        assert len(summary.fallen) == 3 and not combat.running
        assert summary.defeated == (("Goblin 1", 50), ("Goblin 2", 50), ("Bugbear", 200))
        assert summary.party == ("Lyra Mooncaller",) and summary.party_down == ()
        assert table.combats.finished(table.encounter.id) == [combat]
        with pytest.raises(CombatError):
            table.combats.damage(combat.id, table.named(combat, "Goblin 3"), 1, GM)

    @pytest.mark.parametrize("lang", ["en", "de"])
    def test_every_log_entry_reads_in_both_languages(self, table: Table, lang: str) -> None:
        combat = table.combats.begin(table.start().id, GM)
        goblin = table.named(combat, "Goblin 1")
        table.combats.damage(combat.id, goblin, 2, GM)
        table.combats.heal(combat.id, goblin, 1, GM)
        table.combats.temp(combat.id, goblin, 3, GM)
        table.combats.toggle_condition(combat.id, goblin, "prone", GM, rounds=2)
        table.combats.toggle_condition(combat.id, goblin, "prone", GM)
        table.combats.concentrate(combat.id, goblin, "Bless", GM)
        table.combats.concentrate(combat.id, goblin, "", GM)
        table.combats.hide(combat.id, goblin, True, GM)
        table.combats.hide(combat.id, goblin, False, GM)
        table.combats.edit(combat.id, goblin, GM, ac=12)
        table.roll(12).combats.split(combat.id, goblin, GM)
        table.roll(10).combats.add_monster(combat.id, "Wolf|MM", 2, GM)
        combat = table.combats.end(combat.id, GM)
        page = Page(UiCatalogue().translator(lang))
        entries = table.fight_log(combat)
        kinds = {e.kind for e in entries}
        assert {
            EntryKind.COMBAT_START,
            EntryKind.ROUND,
            EntryKind.TURN,
            EntryKind.DAMAGE,
            EntryKind.CONDITION,
            EntryKind.COMBAT_END,
        } <= kinds
        assert any(e.kind is EntryKind.TURN and e.subject_name == "Goblin ×4" for e in entries)
        for entry in entries:
            text = headline(page, entry)
            assert "{" not in text and not text.startswith(("combat.", "protocol.")), (
                entry.kind,
                text,
            )
        # the board's fold is a slice of the log, fetched by the page
        assert "log-embed" in str(combat_log(page, combat))
        gm = Viewer("dm", gamemaster=True)
        chosen = Filter("fight", combat=combat.id)
        rows = str(log_rows(page, ProtocolReader(table.log).page(gm, chosen), chosen))
        assert page.t("protocol.round", n=1) in rows

    def test_a_refused_change_leaves_the_record_alone(self, table: Table) -> None:
        combat = table.start()
        before = table.now(combat).model_dump()
        with pytest.raises(CombatNotFound):
            table.combats.damage(combat.id, "nobody", 3, GM)
        assert table.now(combat).model_dump() == before

    def test_combats_persist(self, table: Table) -> None:
        combat = table.start()
        again = GameStateStore(table.store.root)
        assert again.combats.get(combat.id).combatants[0].label == "Goblin 1"


class TestOldEncounters:
    def test_the_unused_run_fields_are_dropped(self) -> None:
        old = {
            "name": "Old",
            "module_id": None,
            "combatants": [],
            "round": 0,
            "turn_index": 0,
            "active": False,
        }
        assert Encounter.model_validate(old).name == "Old"


# -- the tracker's pages (step 2) ----------------------------------------------------

PASSWORD = "correct horse battery"


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


def _roll_on_sheet(table: Table, player: str = "alice") -> int:
    """Lyra rolls initiative on her sheet, as the player's phone does; returns the total."""
    pc = table.store.characters.get(table.lyra.id)
    table.characters.play.act(
        pc.id, "roll", {"kind": "initiative"}, table.characters.sheet(pc), Actor(player)
    )
    entry = table.log.newest(table.world.id, limit=1)[0]
    assert entry.kind is EntryKind.INITIATIVE and entry.roll is not None
    return entry.roll.total


def _rolled_ago(table: Table, total: int, minutes: float) -> None:
    """An initiative Lyra rolled on her sheet ``minutes`` ago, in the log."""
    at = datetime.now(UTC) - timedelta(minutes=minutes)
    table.log.append(
        ProtocolEntry(
            at=at,
            kind=EntryKind.INITIATIVE,
            actor_name="alice",
            subject=f"character:{table.lyra.id}",
            roll=RollRecord(expression="1d20+3", total=total),
            scope=Scope(world=table.world.id),
            origin="sheet",
        )
    )


class TestSheetInitiative:
    def test_a_roll_on_the_sheet_lands_in_the_tracker(self, table: Table) -> None:
        combat = table.start()
        total = _roll_on_sheet(table)
        [changed] = table.combats.take_initiative(table.lyra.id)
        lyra = changed.combatant(table.named(changed, "Lyra Mooncaller"))
        assert lyra is not None and lyra.initiative == total
        taken = table.latest(changed)
        assert taken.kind is EntryKind.INITIATIVE and taken.actor_name == "alice"
        # taken once: the same roll does not land again
        assert table.combats.take_initiative(table.lyra.id) == []
        assert table.combats.get(combat.id).updated_at == changed.updated_at

    def test_the_newest_wins_between_sheet_and_gamemaster(self, table: Table) -> None:
        combat = table.start()
        lyra = table.named(combat, "Lyra Mooncaller")
        _roll_on_sheet(table)
        table.combats.take_initiative(table.lyra.id)
        table.combats.set_initiative(combat.id, lyra, 3, GM)
        # the gamemaster's value stands against the older roll
        assert table.combats.take_initiative(table.lyra.id) == []
        total = _roll_on_sheet(table)
        table.combats.take_initiative(table.lyra.id)
        assert table.now(combat).combatant(lyra).initiative == total  # type: ignore[union-attr]

    def test_a_roll_just_before_the_start_counts(self, table: Table) -> None:
        _rolled_ago(table, 16, minutes=2)
        combat = table.start()
        lyra = combat.combatant(table.named(combat, "Lyra Mooncaller"))
        assert lyra is not None and lyra.initiative == 16

    def test_an_old_roll_does_not(self, table: Table) -> None:
        _rolled_ago(table, 16, minutes=30)
        combat = table.start()
        lyra = combat.combatant(table.named(combat, "Lyra Mooncaller"))
        assert lyra is not None and lyra.initiative is None

    def test_nor_one_from_the_last_fight(self, table: Table) -> None:
        first = table.start()
        _rolled_ago(table, 16, minutes=1)
        table.combats.end(first.id, GM)
        second = table.roll(1, 1).combats.start(table.encounter, GM)
        lyra = second.combatant(table.named(second, "Lyra Mooncaller"))
        assert lyra is not None and lyra.initiative is None

    def test_an_ended_combat_takes_no_roll(self, table: Table) -> None:
        combat = table.start()
        table.combats.end(combat.id, GM)
        _roll_on_sheet(table)
        assert table.combats.take_initiative(table.lyra.id) == []


def _encounter(registry: StoreRegistry) -> Encounter:
    world = registry.gamestate.worlds.create(
        World(name="Grey Reed", source_set=SourceSet(sources=["PHB", "MM"]))
    )
    data = json.loads(LYRA.read_text(encoding="utf-8"))
    data["world_id"] = world.id
    registry.gamestate.characters.create(PlayerCharacter.model_validate(data))
    return registry.gamestate.encounters.create(
        Encounter(
            name="Goblin Ambush",
            world_id=world.id,
            monsters=[EncounterMonster(name="Goblin", source="MM", count=2)],
        )
    )


HTMX = {"HX-Request": "true"}


class TestPages:
    async def test_the_party_overview_links_the_fight(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        assert 'id="party-combats"' in (await ac.get("/gm")).text
        combat = registry.combats.start(_encounter(registry), GM)
        page = (await ac.get("/gm")).text
        assert f'href="/gm/combat/{combat.id}"' in page
        assert "Waiting for initiative: Lyra Mooncaller" in page
        lyra = next(c for c in combat.combatants if c.character_id)
        assert lyra.character_id is not None
        pc = registry.characters.get_any(lyra.character_id)
        registry.characters.play.act(
            pc.id, "roll", {"kind": "initiative"}, registry.characters.sheet(pc), Actor("alice")
        )
        registry.combats.begin(combat.id, GM)
        strip = (await ac.get("/gm/party/combats")).text
        assert "Round 1" in strip and "Waiting" not in strip
        registry.combats.end(combat.id, GM)
        assert combat.id not in (await ac.get("/gm/party/combats")).text

    async def test_run_an_encounter_and_fight(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        encounter = _encounter(registry)
        started = await ac.post(f"/gm/encounters/{encounter.id}/run")
        assert started.status_code == 303
        url = started.headers["location"]
        combat_id = url.rsplit("/", 1)[1]
        page = (await ac.get(url)).text
        assert "Goblin 1" in page and "Lyra Mooncaller" in page
        assert "Waiting for initiative: Lyra Mooncaller" in page
        # running it again finds the same fight; the builder links it
        assert (await ac.post(f"/gm/encounters/{encounter.id}/run")).headers["location"] == url
        assert (
            f'href="/gm/combat/{combat_id}"'
            in (await ac.get(f"/gm/encounters/{encounter.id}")).text
        )

        begun = await ac.post(f"{url}/begin", headers=HTMX)
        assert begun.status_code == 200 and begun.text.startswith('<div id="combat-board"')
        assert "Goblin ×2&#39;s turn" in begun.text

        goblin = registry.combats.get(combat_id).combatants[0]
        hit = await ac.post(
            f"{url}/c/{goblin.id}/hp", data={"mode": "damage", "amount": "3"}, headers=HTMX
        )
        assert "4<small>/7</small>" in hit.text
        # the row's background is its hit point bar, coloured by how serious it is
        row = f'id="c-{goblin.id}" class="combatant'
        assert row in hit.text and 'has-hp" style="--hp:57%"' in hit.text
        hit = await ac.post(
            f"{url}/c/{goblin.id}/hp", data={"mode": "damage", "amount": "1"}, headers=HTMX
        )
        assert "is-bloodied" in hit.text and 'style="--hp:43%"' in hit.text
        assert "HX-Trigger" not in hit.headers  # damage is no roll: no dice sound
        rolled = await ac.post(f"{url}/c/{goblin.id}/roll", headers=HTMX)
        assert "paravent-roll" in rolled.headers["HX-Trigger"]  # re-rolled initiative rattles
        refused = await ac.post(
            f"{url}/c/{goblin.id}/condition", data={"key": "sleepy"}, headers=HTMX
        )
        assert "is not a condition" in refused.text
        # without JavaScript: a redirect back, carrying the error
        plain = await ac.post(f"{url}/c/{goblin.id}/hp", data={"mode": "damage", "amount": "x"})
        assert (
            plain.status_code == 303 and "error=combat.error.bad_input" in plain.headers["location"]
        )
        assert "That did not make sense" in (await ac.get(plain.headers["location"])).text

        side = await ac.get(f"{url}/side", params={"sel": goblin.id})
        assert "Nimble Escape" in side.text

    async def test_adding_and_ending(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        url = (await ac.post(f"/gm/encounters/{_encounter(registry).id}/run")).headers["location"]
        combat_id = url.rsplit("/", 1)[1]
        await ac.post(f"{url}/monsters", data={"ref": "Wolf|MM"})
        await ac.post(f"{url}/others", data={"label": "Hob", "ac": "16", "hp": "27"})
        labels = [c.label for c in registry.combats.get(combat_id).combatants]
        assert "Wolf" in labels and "Hob" in labels
        bad = await ac.post(f"{url}/monsters", data={"ref": "Nothing|XX"})
        assert "error=combat.error.monster" in bad.headers["location"]
        picker = await ac.get("/search", params={"q": "wolf", "pick": combat_id, "partial": "1"})
        assert f'formaction="{url}/monsters"' in picker.text

        assert "End “Goblin Ambush”?" in (await ac.get(f"{url}/end")).text
        await ac.post(f"{url}/end")
        ended = (await ac.get(url)).text
        assert "Summary" in ended and "Next turn" not in ended
        assert "log-embed" in ended  # the log's slice, fetched by the page
        fold = (
            await ac.get(
                "/log/panel", params={"filter": "fight", "combat": combat_id, "embed": "1"}
            )
        ).text
        assert "Combat ended" in fold
        listing = (await ac.get("/gm/combat")).text
        assert "Finished" in listing and "Goblin Ambush" in listing
        encounter_id = registry.combats.get(combat_id).encounter_id
        builder = (await ac.get(f"/gm/encounters/{encounter_id}")).text
        assert "Fought before" in builder and f'href="{url}"' in builder

        assert "Delete “Goblin Ambush”?" in (await ac.get(f"{url}/delete")).text
        gone = await ac.post(f"{url}/delete")
        assert gone.headers["location"] == f"/gm/encounters/{encounter_id}"
        assert (await ac.get(url)).status_code == 404

    async def test_a_running_combat_is_not_deleted(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        url = (await ac.post(f"/gm/encounters/{_encounter(registry).id}/run")).headers["location"]
        refused = await ac.post(f"{url}/delete")
        assert "error=combat.error.still_running" in refused.headers["location"]
        assert (await ac.get(url)).status_code == 200

    async def test_the_chapter_embed_runs_it(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        encounter = _encounter(registry)
        assert encounter.world_id is not None
        adventure = registry.adventures.create(encounter.world_id, "The Drowned Bell")
        chapter = registry.adventures.create_chapter(adventure.id, "Fog on the River")
        registry.adventures.update_chapter(
            chapter.id,
            title=chapter.title,
            status="planned",
            body="::: encounter Goblin Ambush\n:::\n",
        )
        page = (await ac.get(f"/gm/chapters/{chapter.id}")).text
        assert f'action="/gm/encounters/{encounter.id}/run"' in page

    async def test_gamemaster_only_and_unknown(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        url = (await ac.post(f"/gm/encounters/{_encounter(registry).id}/run")).headers["location"]
        assert (await ac.get("/gm/combat/nope")).status_code == 404
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await ac.get(url)).status_code == 403
        assert (await ac.post(f"{url}/begin")).status_code == 403


class TestLiveEvents:
    async def test_changes_reach_the_combat_key(self, tmp_path: Path, corpus: Path) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        encounter = _encounter(registry)
        combat = registry.combats.start(encounter, GM)
        queue = registry.events.subscribe(combat.id)
        party = registry.events.subscribe(PARTY)
        registry.combats.begin(combat.id, GM)
        assert queue.get_nowait() == registry.combats.get(combat.id).updated_at.isoformat()
        assert str(party.get_nowait()).startswith(f"*combats {combat.id} ")
        # a character's own change reaches the tracker too
        lyra = next(c for c in combat.combatants if c.character_id)
        assert lyra.character_id is not None
        registry.characters.play.act(
            lyra.character_id,
            "hp",
            {"mode": "damage", "amount": "1"},
            registry.characters.sheet(registry.characters.get_any(lyra.character_id)),
            Actor("Alice"),
        )
        assert "|" in str(queue.get_nowait())

    async def test_a_sheet_roll_reaches_the_tracker(self, tmp_path: Path, corpus: Path) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        combat = registry.combats.start(_encounter(registry), GM)
        lyra = next(c for c in combat.combatants if c.character_id)
        assert lyra.character_id is not None
        pc = registry.characters.get_any(lyra.character_id)
        registry.characters.play.act(
            pc.id, "roll", {"kind": "initiative"}, registry.characters.sheet(pc), Actor("alice")
        )
        landed = registry.combats.get(combat.id).combatant(lyra.id)
        assert landed is not None and landed.initiative is not None
        # the tracker's own change to the character writes once, without waiting on itself
        registry.combats.damage(combat.id, lyra.id, 1, GM)
        assert combat.world_id is not None
        latest = registry.protocol.store.newest(combat.world_id, limit=1)[0]
        assert latest.kind is EntryKind.DAMAGE and latest.scope.combat == combat.id


class TestPlayerView:
    def test_the_projection_carries_no_numbers(self) -> None:
        # A field added here reaches the players' phones: keep it to these.
        assert set(SeenCombatant.__dataclass_fields__) == {
            "label",
            "health",
            "conditions",
            "character_id",
            "monster",  # the creature's name, to show it in the reader's language
            "friend",  # on the characters' side
            "fill",  # hit points left in percent — friends only, never a foe's
            "id",  # the combatant's id, to pick it as a target — not a number
        }

    def test_hidden_combatants_are_left_out(self, table: Table) -> None:
        combat = table.start()
        table.combats.hide(combat.id, table.named(combat, "Bugbear"), True, GM)
        goblin = table.named(combat, "Goblin 2")
        combat = table.combats.damage(combat.id, goblin, 4, GM)
        view = table.combats.player_view(combat)
        labels = [m.label for t in view.turns for m in t.members]
        assert "Bugbear" not in labels and "Lyra Mooncaller" in labels
        seen = next(m for t in view.turns for m in t.members if m.label == "Goblin 2")
        assert seen.health == "bloodied"
        assert view.waiting == {table.lyra.id}

    def test_your_turn(self, table: Table) -> None:
        combat = table.start()
        lyra = table.named(combat, "Lyra Mooncaller")
        table.combats.set_initiative(combat.id, lyra, 25, GM)
        view = table.combats.player_view(table.combats.begin(combat.id, GM))
        assert view.acting([table.lyra.id]) and not view.acting(["someone-else"])
        assert table.combats.joined_by([table.lyra.id])[0].id == combat.id

    async def test_a_player_follows_the_fight(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        combat = registry.combats.start(_encounter(registry), GM)
        lyra = next(c for c in combat.combatants if c.character_id)
        assert lyra.character_id is not None
        registry.gamestate.characters.mutate(
            lyra.character_id, lambda pc: setattr(pc, "player", "alice")
        )
        goblin = combat.combatants[1]
        registry.combats.hide(combat.id, goblin.id, True, GM)
        registry.auth.create_user("bob", PASSWORD, Role.PLAYER)

        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        url = f"/play/combat/{combat.id}"
        page = (await ac.get(url)).text
        assert "Lyra Mooncaller" in page and "Goblin 1" in page
        assert goblin.label not in page  # hidden
        assert "Roll your initiative on your sheet" in page
        assert "<small>/7</small>" not in page and "Nimble Escape" not in page  # no HP, statblock
        # no heading: the round leads, with the switch to the character beside it
        assert 'class="page-head"' not in page and 'class="pv-switch"' in page
        sheet_url = f"/characters/{lyra.character_id}"
        assert f'href="{sheet_url}"' in page and f'href="{url}"' in page
        sheet = (await ac.get(sheet_url)).text
        assert f'href="{url}"' in sheet and 'id="combat-strip"' in sheet
        assert 'class="pv-switch"' in sheet
        assert "Roll initiative" in sheet
        assert f'href="{url}"' in (await ac.get("/play")).text

        registry.combats.set_initiative(combat.id, lyra.id, 25, GM)
        registry.combats.begin(combat.id, GM)
        assert "Your turn!" in (await ac.get(f"{url}/board")).text
        strip = (await ac.get(f"/characters/{lyra.character_id}/combat")).text
        assert "Your turn!" in strip and "Roll initiative" not in strip

        # Hit point bars: a friend's real share, a foe's bar full in its state's colour
        foe = combat.combatants[0]
        registry.combats.damage(combat.id, foe.id, 6, GM)  # 1 of 7 left: critical
        registry.combats.damage(combat.id, lyra.id, 1, GM)
        view = registry.combats.player_view(registry.combats.get(combat.id))
        seen = {m.label: m for turn in view.turns for m in turn.members}
        assert seen[foe.label].fill is None and seen[foe.label].health == "critical"
        hurt = seen["Lyra Mooncaller"].fill
        assert hurt is not None and 0 < hurt < 100
        board = (await ac.get(f"{url}/board")).text
        assert 'class="pv-member critical is-foe" style="--hp:100%"' in board
        assert f'is-friend" style="--hp:{hurt}%"' in board

        await ac.post("/login", data={"username": "bob", "password": PASSWORD})
        assert (await ac.get(url)).status_code == 404  # not bob's fight

    async def test_a_change_reaches_the_characters_sheets(
        self, tmp_path: Path, corpus: Path
    ) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        combat = registry.combats.start(_encounter(registry), GM)
        lyra = next(c.character_id for c in combat.combatants if c.character_id)
        assert lyra is not None
        queue = registry.events.subscribe(combat_key(lyra))
        begun = registry.combats.begin(combat.id, GM)
        assert queue.get_nowait() == begun.updated_at.isoformat()


class TestFriendAndFoe:
    """Sides, the live rating, and the dead (COMBAT.md §3)."""

    def _rated(self, table: Table, levels: list[int], foes: list[tuple[int, int]]) -> int:
        return table.encounters.difficulty.rate(EDITION_CLASSIC, levels, foes).adjusted_xp

    def test_the_rating_falls_as_foes_fall(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        level = table.lyra.total_level
        rating = table.combats.view(combat).rating
        assert rating is not None and rating.rules == EDITION_CLASSIC
        assert rating.adjusted_xp == self._rated(table, [level], [(50, 4), (200, 1)])
        combat = table.combats.damage(combat.id, table.named(combat, "Bugbear"), 100, GM)
        rating = table.combats.view(combat).rating
        assert rating is not None and rating.monster_count == 4
        assert rating.adjusted_xp == self._rated(table, [level], [(50, 4)])

    def test_switching_a_group_to_friend_joins_the_party(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        combat = table.combats.switch_side(combat.id, table.named(combat, "Goblin 2"), GM)
        goblins = [c for c in combat.combatants if c.ref == "Goblin|MM"]
        assert all(g.ally and g.is_friend for g in goblins)  # the whole group turned
        assert table.latest(combat).what == "side:friend"
        rating = table.combats.view(combat).rating
        assert rating is not None and (rating.party_size, rating.monster_count) == (5, 1)
        assert rating.adjusted_xp == self._rated(
            table, [table.lyra.total_level, 1, 1, 1, 1], [(200, 1)]
        )
        combat = table.combats.switch_side(combat.id, goblins[0].id, GM)
        assert not any(c.ally for c in combat.combatants)
        with pytest.raises(CombatError):
            table.combats.switch_side(combat.id, table.named(combat, "Lyra Mooncaller"), GM)

    def test_an_ally_added_mid_fight(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        combat = table.roll(10).combats.add_monster(combat.id, "Wolf|MM", 1, GM, ally=True)
        combat = table.combats.add_other(combat.id, "Hob", GM, hp=27, ally=True)
        assert table.now(combat).combatant(table.named(combat, "Wolf")).ally  # type: ignore[union-attr]
        rating = table.combats.view(combat).rating
        # the wolf (CR 1/4) counts as a level 1 member; Hob has no CR and no weight
        assert rating is not None and rating.party_size == 2

    def test_fallen_allies_earn_no_xp(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        combat = table.combats.switch_side(combat.id, table.named(combat, "Bugbear"), GM)
        for label in ("Goblin 1", "Bugbear"):
            combat = table.combats.damage(combat.id, table.named(combat, label), 100, GM)
        summary = table.combats.summary(table.combats.end(combat.id, GM))
        assert summary.defeated == (("Goblin 1", 50),)

    def test_a_monster_at_zero_is_dead_and_leaves_the_rating(self, table: Table) -> None:
        combat = table.combats.begin(table.start().id, GM)
        for label in ("Goblin 1", "Goblin 2", "Goblin 3", "Goblin 4", "Bugbear"):
            combat = table.combats.damage(combat.id, table.named(combat, label), 100, GM)
        view = table.combats.view(combat)
        goblin = view.row(table.named(combat, "Goblin 1"))
        lyra = view.row(table.named(combat, "Lyra Mooncaller"))
        assert goblin is not None and goblin.dead
        assert lyra is not None and not lyra.dead
        assert view.rating is not None and view.rating.monster_count == 0

    async def test_the_tracker_shows_sides_dead_and_difficulty(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        url = (await ac.post(f"/gm/encounters/{_encounter(registry).id}/run")).headers["location"]
        combat_id = url.rsplit("/", 1)[1]
        page = (await ac.get(url)).text
        assert "Difficulty" in page and "side-foe" in page and "side-friend" in page
        # the order scrolls in its own box, kept by the nav's scroll memory
        assert 'class="cb-scroll" data-scroll-keep="combat-order"' in page
        # the damage controls sit below the row's body
        row = page.split('class="combatant', 1)[1].split("</li>", 1)[0]
        assert row.index("c-actions") < row.index("c-damage") and "cb-amount" in row
        bottom = row.split('class="c-damage"', 1)[1]
        assert 'value="temp"' in bottom and 'class="cb-menu"' in bottom
        # adding as a friend through the picker's form
        assert 'name="side"' in page
        added = await ac.post(f"{url}/monsters", data={"ref": "Wolf|MM", "side": "friend"})
        assert "side=friend" in added.headers["location"]
        wolf = next(c for c in registry.combats.get(combat_id).combatants if c.label == "Wolf")
        assert wolf.ally
        switched = await ac.post(f"{url}/c/{wolf.id}/side", headers=HTMX)
        assert switched.status_code == 200
        fold = await ac.get(
            "/log/panel", params={"filter": "fight", "combat": combat_id, "embed": "1"}
        )
        assert "now fights as Foe" in fold.text
        goblin = registry.combats.get(combat_id).combatants[0]
        dead = await ac.post(
            f"{url}/c/{goblin.id}/hp", data={"mode": "damage", "amount": "50"}, headers=HTMX
        )
        assert "cb-dead" in dead.text and "Dead" in dead.text


class TestCharacterPanel:
    async def test_a_character_opens_its_fighting_stats(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        url = (await ac.post(f"/gm/encounters/{_encounter(registry).id}/run")).headers["location"]
        combat = registry.combats.get(url.rsplit("/", 1)[1])
        lyra = next(c for c in combat.combatants if c.character_id)
        side = (await ac.get(f"{url}/side", params={"sel": lyra.id})).text
        assert "Player character" in side and "Lyra Mooncaller" in side
        assert f'href="/characters/{lyra.character_id}"' in side  # the full sheet
        assert f'data-live="{url}/side?sel={lyra.id}"' in side  # follows the fight
        for heading in ("Armor class", "Speed", "Initiative", "Saving throws", "Spellcasting"):
            assert heading in side, heading
        assert "/ref/spell/" in side  # spells link to their entries, with previews
        assert "{name}" not in side
        # damage shows in the panel on its next load
        await ac.post(f"{url}/c/{lyra.id}/hp", data={"mode": "damage", "amount": "5"})
        assert "/62" in (await ac.get(f"{url}/side", params={"sel": lyra.id})).text
        # a monster's panel is its statblock, and not live
        goblin = next(c for c in combat.combatants if c.ref)
        monster = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Nimble Escape" in monster and "data-live" not in monster


class TestNames:
    """A group named one by one (NAMES.md §5)."""

    @staticmethod
    def _naming() -> Naming:
        names = ["Snik", "Grub", "Mog", "Yark", "Bree", "Tusk"]
        style = NamingStyle(
            name="Goblins",
            race="Goblin",
            pattern="{given}",
            parts={"given": ListPart(names=names)},
        )
        return Naming(NpcTables(None), lambda: [style])

    def test_a_planned_list_of_names_names_the_members(self, table: Table) -> None:
        table.store.encounters.mutate(
            table.encounter.id, lambda e: setattr(e.monsters[0], "label", "Snik, Grub")
        )
        table.encounter = table.store.encounters.get(table.encounter.id)
        labels = [c.label for c in table.start().combatants if c.ref == "Goblin|MM"]
        assert labels == ["Snik", "Grub", "Goblin 1", "Goblin 2"]

    def test_the_dice_name_one_or_every_member(self, table: Table) -> None:
        combat = table.start()
        first = table.named(combat, "Goblin 1")
        named = table.combats.roll_names(combat.id, first, self._naming(), "2014", GM)
        goblins = [c.label for c in named.combatants if c.ref == "Goblin|MM"]
        assert goblins[0] in {"Snik", "Grub", "Mog", "Yark", "Bree", "Tusk"}
        assert goblins[1:] == ["Goblin 2", "Goblin 3", "Goblin 4"]
        each = table.combats.roll_names(combat.id, first, self._naming(), "2014", GM, group=True)
        goblins = [c.label for c in each.combatants if c.ref == "Goblin|MM"]
        assert len(set(goblins)) == 4 and not any(g.startswith("Goblin") for g in goblins)
        lyra = next(c.id for c in combat.combatants if c.character_id)
        with pytest.raises(CombatError):
            table.combats.roll_names(combat.id, lyra, self._naming(), "2014", GM)
