"""The 2014 guide's optional combat rules in the tracker (app/services/combat_rules.py),
against the real corpus: the guide's tables are read from it."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.gamestate import Combat, SourceSet, World
from app.services import combat_rules
from app.services.book_tables import Rows, find_in
from app.services.combat import CombatError, CombatService, SheetCharacters
from app.services.combat_rules import (
    FOE,
    FRIEND,
    available,
    kept,
    lingering_injury,
    massive,
    mob_needs,
    roll_sides,
    system_shock,
)
from app.services.dice import Dice
from app.services.i18n import UiCatalogue
from app.services.monster_attacks import decode
from app.services.worlds import WorldInput, WorldService
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore
from app.ui.context import Page
from app.ui.gm_pages import world_form
from app.ui.protocol_pages import rule_text
from tests.test_combat import GM, Table
from tests.test_dice_text_events import Scripted

ALL = list(combat_rules.RULES)
# Side initiative rolls at the start, so most fights here go without it.
NO_SIDES = [r for r in ALL if r != "side_initiative"]


class RulesTable(Table):
    """The goblin ambush in a world with the 2014 guide, every optional rule on."""

    def __init__(self, root: Path, compendium: CompendiumStore, rules: list[str]) -> None:
        super().__init__(root, compendium)
        self.store.worlds.update(
            self.world.id,
            source_set=SourceSet(sources=["PHB", "MM", "DMG"]),
            combat_rules=rules,
        )
        self.injured: list[tuple[str, str, str]] = []
        tables = compendium.all("table")

        def table(world_id: str | None, caption: str, sources: list[str]) -> Rows | None:
            world = self.store.worlds.find(world_id) if world_id else None
            held = [s for s in sources if world is None or s.lower() in world.source_set.normalized]
            return find_in(tables, caption, held) if held else None

        def injure(world_id: str | None, character_id: str, name: str, note: str) -> None:
            self.injured.append((character_id, name, note))

        self.combats = CombatService(
            self.store,
            self.encounters.monster,
            SheetCharacters(self.characters),
            Dice(self.dice),
            rules=self.combats._rules,
            protocol=self.combats._protocol,
            table=table,
            injure=injure,
        )

    def finder(self) -> combat_rules.Finder:
        return self.combats._finder(self.world.id)

    def lyra_in(self, combat: Combat) -> str:
        return next(c.id for c in combat.combatants if c.character_id == self.lyra.id)


@pytest.fixture
def table(tmp_path: Path, loaded_compendium: CompendiumStore) -> RulesTable:
    return RulesTable(tmp_path / "lib", loaded_compendium, NO_SIDES)


def _begun(table: RulesTable, combat: Combat) -> Combat:
    lyra = table.lyra_in(combat)
    if not table.now(combat).sides:
        table.combats.set_initiative(combat.id, lyra, 5, GM)
    return table.combats.begin(combat.id, GM)


class TestTheRules:
    def test_massive_is_half_the_maximum(self) -> None:
        assert massive(14, 27) and massive(31, 62) and not massive(13, 27)
        assert not massive(50, None) and not massive(50, 0)

    def test_kept_keeps_known_rules_in_order(self) -> None:
        assert kept(["mobs", "nonsense", "morale"]) == ["morale", "mobs"]

    def test_sides_are_rolled_again_on_a_tie(self) -> None:
        assert roll_sides(Dice(Scripted(7, 7, 12, 3))) == {FRIEND: 12, FOE: 3}

    def test_the_guide_tables_read(self, table: RulesTable) -> None:
        find = table.finder()
        assert available(find) == frozenset(ALL)
        shock = system_shock(find("System Shock", ["DMG"]) or [], Dice(Scripted(4)))
        assert shock.conditions == ("stunned",) and not shock.drop
        drop = system_shock(find("System Shock", ["DMG"]) or [], Dice(Scripted(2)))
        assert drop.drop and drop.stable
        injury = lingering_injury(find("Lingering Injuries", ["DMG"]) or [], Dice(Scripted(1)))
        assert injury.name == "Lose an Eye" and "{@" not in injury.note
        rows = find("Mob Attacks", ["DMG"]) or []
        assert mob_needs(rows, 12, 4) == 2  # a d20 of 8: two attackers for one hit
        assert mob_needs(rows, 18, 4) == 3  # a d20 of 14: three attackers
        assert mob_needs(rows, 30, 4) is None

    def test_a_world_without_the_guide_keeps_only_what_needs_no_table(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        bare = RulesTable(tmp_path / "bare", loaded_compendium, ALL)
        bare.store.worlds.update(bare.world.id, source_set=SourceSet(sources=["PHB", "MM"]))
        combat = bare.roll(15, 9, 6, 13).combats.start(bare.encounter, GM)
        assert bare.combats.rules_of(combat) == {"morale", "side_initiative"}


class TestMassiveDamage:
    def test_a_failed_save_stuns_until_its_next_turn_ends(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        bugbear = table.named(combat, "Bugbear")
        table.roll(2, 4)  # CON save 2+2, then the System Shock d10: 4, stunned
        table.combats.damage(combat.id, bugbear, 14, GM)
        member = table.now(combat).combatant(bugbear)
        assert member is not None and member.hp == 13
        assert "stunned" in member.conditions
        assert [(t.condition, t.rounds) for t in member.timers] == [("stunned", 1)]
        whats = [e.what for e in table.fight_log(combat)[:3]]
        assert whats[1].startswith("rule:shock:") and whats[2] == "rule:massive:15"

    def test_a_made_save_or_small_damage_does_nothing(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        bugbear = table.named(combat, "Bugbear")
        table.combats.damage(combat.id, bugbear, 5, GM)  # no dice: not massive
        table.roll(18)
        table.combats.damage(combat.id, bugbear, 14, GM)
        member = table.now(combat).combatant(bugbear)
        assert member is not None and member.conditions == [] and member.hp == 8
        assert table.latest(combat).outcome == "success"

    def test_a_shock_drops_a_character_and_offers_an_injury(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        lyra = table.lyra_in(combat)
        table.roll(1, 1)  # CON save 1+2, System Shock 1: drops to 0 hit points
        table.combats.damage(combat.id, lyra, 31, GM)
        assert table.store.characters.get(table.lyra.id).state.hp == 0
        assert table.now(combat).injuries == {lyra: "down"}


class TestLingeringInjuries:
    def test_a_critical_hit_on_a_character_offers_one(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        goblin = table.named(combat, "Goblin 1")
        table.roll(20, 3, 4)  # a natural 20, the scimitar's dice doubled
        table.combats.attack(combat.id, goblin, "Scimitar", 4, decode("1d6+2:slashing"), GM)
        at = table.now(combat).pending[0].at.isoformat()
        lyra = table.lyra_in(combat)
        table.combats.resolve_attack(combat.id, at, lyra, GM)
        assert table.now(combat).injuries == {lyra: "crit"}
        table.roll(1)
        table.combats.act(combat.id, lyra, "injury", {}, GM)
        assert table.injured == [(table.lyra.id, "Lose an Eye", table.injured[0][2])]
        assert table.now(combat).injuries == {}
        assert table.latest(combat).what == "rule:injury:Lose an Eye"
        with pytest.raises(CombatError):
            table.combats.act(combat.id, lyra, "injury", {}, GM)

    def test_waved_off(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        lyra = table.lyra_in(combat)
        table.roll(1, 1)
        table.combats.damage(combat.id, lyra, 31, GM)
        table.combats.act(combat.id, lyra, "injury", {"skip": "1"}, GM)
        assert table.now(combat).injuries == {} and table.injured == []


class TestMorale:
    def test_half_the_group_down_tests_its_nerve_once(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())  # goblins 17 act first
        for label in ("Goblin 1", "Goblin 2"):
            table.combats.damage(combat.id, table.named(combat, label), 7, GM)
        due = table.combats.reminders(table.now(combat)).morale
        assert len(due) == 1 and due[0][2] == "half"
        table.roll(3)  # WIS save 3−1 against DC 10
        table.combats.act(combat.id, due[0][0], "morale", {}, GM)
        assert table.latest(combat).outcome == "failure"
        assert table.combats.reminders(table.now(combat)).morale == ()
        with pytest.raises(CombatError):
            table.combats.act(combat.id, due[0][0], "morale", {}, GM)

    def test_a_lone_creature_at_half_its_hit_points(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        bugbear = table.named(combat, "Bugbear")
        table.combats.damage(combat.id, bugbear, 13, GM)  # 14 of 27 left: not yet
        while table.turn(combat) != "Bugbear":
            table.combats.next_turn(combat.id, GM)
        assert table.combats.reminders(table.now(combat)).morale == ()
        table.combats.damage(combat.id, bugbear, 1, GM)
        assert table.combats.reminders(table.now(combat)).morale[0][2] == "hurt"


class TestMobs:
    def test_four_goblins_against_lyra(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        goblin = table.named(combat, "Goblin 1")
        lyra = table.lyra_in(combat)
        ac = table.combats.row(table.now(combat).combatant(lyra)).ac  # type: ignore[arg-type]
        assert ac == 14  # a d20 of 10 to hit: two goblins for each hit
        table.roll(3, 5)  # two hits, 1d6+2 each
        table.combats.act(
            combat.id,
            goblin,
            "mob",
            {"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing", "target": lyra, "count": "4"},
            GM,
        )
        assert table.store.characters.get(table.lyra.id).state.hp == 41 - 5 - 7
        mob = next(e for e in table.fight_log(combat) if e.what.startswith("rule:mob:"))
        assert mob.what == "rule:mob:4:2:2:Scimitar→Lyra Mooncaller" and mob.amount == 12


class TestSideInitiative:
    def test_each_side_one_roll(self, tmp_path: Path, loaded_compendium: CompendiumStore) -> None:
        table = RulesTable(tmp_path / "sides", loaded_compendium, ["side_initiative"])
        combat = table.roll(15, 9, 6, 13).combats.start(table.encounter, GM)
        assert combat.sides == {FRIEND: 6, FOE: 13}
        assert {c.initiative for c in combat.combatants if c.is_friend} == {6}
        assert {c.initiative for c in combat.combatants if not c.is_friend} == {13}
        lyra = table.lyra_in(combat)
        table.combats.set_initiative(combat.id, lyra, 18, GM)
        now = table.now(combat)
        assert now.sides[FRIEND] == 18
        assert table.combats.order(now)[0].is_character
        table.combats.add_other(combat.id, "Hired guard", GM, hp=10, ally=True)
        guard = next(c for c in table.now(combat).combatants if c.label == "Hired guard")
        assert guard.initiative == 18


class TestTheTable:
    def test_the_world_keeps_its_rules(self, table: RulesTable) -> None:
        worlds = WorldService(table.store)
        made = worlds.create(WorldInput("Moor", combat_rules=("mobs", "bogus", "morale")))
        assert made.combat_rules == ["morale", "mobs"]

    def test_the_world_file_keeps_them_and_milestones(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path / "files")
        world = store.worlds.create(World(name="Moor", milestone=True, combat_rules=["mobs"]))
        again = GameStateStore(tmp_path / "files").worlds.get(world.id)
        assert again.milestone and again.combat_rules == ["mobs"]

    def test_the_world_form_offers_them(self, table: RulesTable, page: Page) -> None:
        world = table.store.worlds.get(table.world.id)
        html = str(world_form(page, _Books(), world=world, name="x", description="",
                              sources=(), combat_rules=["mobs"]))  # fmt: skip
        assert 'value="mobs" checked' in html and 'value="morale"' in html

    def test_log_lines(self, page: Page) -> None:
        assert "DC 15" in rule_text(page, "Bugbear", "massive:15", 4, "failure")
        assert "they cannot hit" in rule_text(page, "Goblin", "mob:4:0:0:Bite→Lyra", 0, "")
        assert "2 hits" in rule_text(page, "Goblin", "mob:4:2:2:Bite→Lyra", 9, "")


class _Books:
    """An empty book catalogue for the form."""

    def get(self, code: str) -> None:
        return None

    def grouped(self, world_id: str | None = None) -> list[tuple[str, list[object]]]:
        return []


@pytest.fixture
def page() -> Page:
    return Page(UiCatalogue().translator("en"))
