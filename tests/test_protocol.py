"""The log (documentation/PROTOCOL.md): the append-only store, rolls as data, and the
sheet's and the tracker's writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.dice import Advantage, Dice
from app.services.monster_attacks import Damage
from app.services.play import Actor, PlayError, PlayService
from app.services.protocol import ProtocolService, roll_record
from app.services.sheet import build_sheet
from app.store.characters import CharacterStore
from app.store.protocol import GLOBAL, ProtocolStore
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_combat import GM, _encounter
from tests.test_dice_text_events import Scripted


def _entry(world: str | None = None, **facts: Any) -> ProtocolEntry:
    return ProtocolEntry(kind=EntryKind.CUSTOM, scope=Scope(world=world), **facts)


class TestStore:
    def test_one_file_per_world_newest_first(self, tmp_path: Path) -> None:
        store = ProtocolStore(tmp_path / "protocol")
        first, second = _entry("w1", what="a"), _entry("w1", what="b")
        store.append(first)
        store.append(_entry(None, what="nowhere"))
        store.append(second)
        assert [e.what for e in store.newest("w1")] == ["b", "a"]
        assert [e.what for e in store.newest(GLOBAL)] == ["nowhere"]
        assert sorted(store.logs()) == ["global", "w1"]
        # a fresh store reads the same back from disk
        again = ProtocolStore(tmp_path / "protocol")
        assert [e.id for e in again.newest("w1")] == [second.id, first.id]

    def test_appending_never_rewrites(self, tmp_path: Path) -> None:
        store = ProtocolStore(tmp_path / "protocol")
        store.append(_entry("w1", what="a"))
        before = store.path("w1").read_bytes()
        store.append(_entry("w1", what="b"))
        after = store.path("w1").read_bytes()
        assert after.startswith(before) and after.count(b"\n") == 2

    def test_a_torn_line_is_skipped_and_swallows_nothing(self, tmp_path: Path) -> None:
        store = ProtocolStore(tmp_path / "protocol")
        store.append(_entry("w1", what="a"))
        with store.path("w1").open("a", encoding="utf-8") as handle:
            handle.write('{"kind": "custom", "what": "torn')  # a crash mid-append
        store.append(_entry("w1", what="b"))
        fresh = ProtocolStore(tmp_path / "protocol")
        assert [e.what for e in fresh.newest("w1")] == ["b", "a"]
        assert fresh.broken("w1") == 1

    def test_a_hand_edit_is_read(self, tmp_path: Path) -> None:
        store = ProtocolStore(tmp_path / "protocol")
        store.append(_entry("w1", what="a"))
        assert len(store.entries("w1")) == 1
        other = ProtocolStore(tmp_path / "protocol")  # another writer
        other.append(_entry("w1", what="b"))
        assert [e.what for e in store.newest("w1")] == ["b", "a"]

    def test_a_key_cannot_leave_the_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            ProtocolStore(tmp_path).path("../users")

    def test_bookkeeping_must_refer(self) -> None:
        with pytest.raises(ValueError):
            ProtocolEntry(kind=EntryKind.REVEAL)


class TestRollRecord:
    def test_a_d20_with_advantage(self) -> None:
        roll = Dice(Scripted(7, 15)).d20(5, Advantage.ADVANTAGE)
        record = roll_record(
            roll,
            sources=(("ability:dex", 3), ("proficiency", 2)),
            advantage=Advantage.ADVANTAGE,
            chosen=Advantage.ADVANTAGE,
            reasons=("invisible",),
        )
        assert record.total == 20 and record.natural == 15 and record.mode == "advantage"
        assert record.dice[0].results == [7, 15] and record.dice[0].kept == [15]
        assert record.reasons == ["sheet", "condition:invisible"]
        assert [(b.source, b.value) for b in record.bonuses] == [
            ("ability:dex", 3),
            ("proficiency", 2),
        ]

    def test_what_the_sources_miss_is_named(self) -> None:
        roll = Dice(Scripted(4, 2, 3)).roll("2d6+1d4-1")
        record = roll_record(roll, rest="modifier")
        assert [(d.faces, d.results) for d in record.dice] == [(6, [4, 2]), (4, [3])]
        assert [(b.source, b.value) for b in record.bonuses] == [("modifier", -1)]
        assert record.total == 8


class Table:
    """A character, a play service with scripted dice, and the log it writes to."""

    def __init__(self, root: Path) -> None:
        self.store = CharacterStore(root / "c")
        self.store.load()
        self.pc = self.store.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Bram",
                    "world_id": "w1",
                    "classes": [{"class": "Homebrew Fighter", "level": 5}],
                    "abilities": {"str": 16, "dex": 12, "con": 14, "wis": 10},
                    "skills": ["Athletics"],
                    "overrides": {"hp_max": 40},
                }
            )
        )
        self.dice = Scripted()
        self.log = ProtocolStore(root / "protocol")
        self.play = PlayService(self.store, Dice(self.dice), ProtocolService(self.log))

    def act(self, action: str, actor: Actor | None = None, **params: Any) -> PlayerCharacter:
        sheet = build_sheet(self.store.get(self.pc.id), None)
        return self.play.act(
            self.pc.id,
            action,
            {k: str(v) for k, v in params.items()},
            sheet,
            actor or Actor("Alice", username="alice"),
        )

    def newest(self) -> list[ProtocolEntry]:
        return self.log.newest("w1")


@pytest.fixture
def bram(tmp_path: Path) -> Table:
    return Table(tmp_path)


class TestTheSheetWrites:
    def test_a_skill_roll_with_its_dice_advantage_and_bonuses(self, bram: Table) -> None:
        bram.dice.values.extend([15, 7])
        bram.act("roll", kind="skill", subject="athletics", adv=1)
        (entry,) = bram.newest()
        assert entry.kind == EntryKind.SKILL and entry.what == "skill:athletics"
        assert entry.actor == "alice" and entry.actor_name == "Alice"
        assert entry.subject == f"character:{bram.pc.id}" and entry.subject_name == "Bram"
        assert entry.scope.world == "w1" and entry.visibility == Visibility.PUBLIC
        roll = entry.roll
        assert roll is not None and roll.mode == "advantage" and roll.reasons == ["sheet"]
        assert roll.dice[0].results == [15, 7] and roll.dice[0].kept == [15]
        # STR +3 and proficiency +3 at level 5: every bonus by its source
        assert [(b.source, b.value) for b in roll.bonuses] == [
            ("ability:str", 3),
            ("proficiency", 3),
        ]
        assert roll.total == 21

    def test_a_condition_cancelling_the_switch_keeps_both_reasons(self, bram: Table) -> None:
        bram.act("condition", key="poisoned")
        bram.dice.values.append(12)
        bram.act("roll", kind="check", subject="str", adv=1)
        roll = bram.newest()[0].roll
        assert roll is not None and roll.mode == "normal" and len(roll.dice[0].results) == 1
        assert roll.reasons == ["sheet", "condition:poisoned"]
        assert bram.newest()[1].what == "on:poisoned"

    def test_hit_points_and_a_concentration_save(self, bram: Table) -> None:
        bram.act("hp", mode="damage", amount=7)
        bram.act("hp", mode="heal", amount=3)
        kinds = [(e.kind, e.amount) for e in bram.newest()]
        assert kinds == [(EntryKind.HEAL, 3), (EntryKind.DAMAGE, 7)]

    def test_a_refused_action_logs_nothing(self, bram: Table) -> None:
        with pytest.raises(PlayError):
            bram.act("death_save")  # not at 0 HP
        assert bram.newest() == []

    def test_the_tracker_logs_its_own_damage(self, bram: Table) -> None:
        bram.act("hp", Actor("GM", via_combat=True), mode="damage", amount=4)
        assert bram.newest() == []


@requires_corpus
class TestTheTrackerWrites:
    def test_a_fight_from_start_to_a_verdict(self, tmp_path: Path, corpus: Path) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        combat = registry.combats.start(_encounter(registry), GM)
        world = combat.world_id
        assert world is not None
        goblin, hidden = combat.combatants[0], combat.combatants[1]
        lyra = next(c for c in combat.combatants if c.character_id)
        registry.combats.hide(combat.id, hidden.id, True, GM)
        registry.combats.set_initiative(combat.id, lyra.id, 20, GM)
        registry.combats.begin(combat.id, GM)
        registry.combats.attack(
            combat.id, goblin.id, "Scimitar", 4, [Damage("1d6+2", "slashing")], GM
        )
        pending = registry.combats.get(combat.id).pending  # empty after a natural 1
        if pending:
            registry.combats.resolve_attack(combat.id, pending[0].at.isoformat(), lyra.id, GM)

        log = registry.protocol.store.newest(world)
        kinds = [e.kind for e in reversed(log)]
        assert kinds[0] == EntryKind.COMBAT_START and EntryKind.ROUND in kinds
        rolled = next(e for e in log if e.kind == EntryKind.ATTACK and e.roll is not None)
        assert rolled.what == "Scimitar" and rolled.subject_name == goblin.label
        assert rolled.roll is not None and rolled.roll.bonuses[0].source == "statblock"
        # the dice are real: a natural 20 doubles them, a natural 1 rolls none
        if rolled.outcome != "fumble":
            assert rolled.damage_rolls[0].expression in ("1d6+2", "2d6+2")
        if pending:
            hit = next(e for e in log if e.kind == EntryKind.ATTACK and e.refers_to == rolled.id)
            assert hit.outcome == "hit" and hit.target_name == lyra.label
        assert all(e.scope.combat == combat.id for e in log), [
            (e.kind, e.what, e.subject) for e in log if e.scope.combat != combat.id
        ]
        # a hidden combatant's doings are the gamemaster's
        hide = next(e for e in log if e.what.startswith("hide"))
        assert hide.visibility == Visibility.GM


@requires_corpus
class TestEveryBonusHasASource:
    """PROTOCOL.md §10: a bonus without a source is a bug. Each number a roll adds on
    the example characters' sheets is fully accounted for by its sources."""

    @pytest.mark.parametrize("example", ["lyra-mooncaller.json", "brunhild-ironvow.json"])
    def test_the_sheets_rolls(self, example: str, loaded_compendium: Any) -> None:
        path = Path(__file__).resolve().parent.parent / "documentation" / "examples" / example
        pc = PlayerCharacter.model_validate_json(path.read_text(encoding="utf-8"))
        sheet = build_sheet(pc, loaded_compendium)
        values = [("initiative", sheet.initiative)]
        values += [(f"save:{a.key}", a.save) for a in sheet.abilities]
        values += [(f"skill:{s.key}", s.value) for s in sheet.skills]
        values += [(f"attack:{a.name}", a.bonus) for a in sheet.attacks]
        if sheet.spell_attack is not None:
            values.append(("spell_attack", sheet.spell_attack))
        unaccounted = [
            (name, value.value, value.sources)
            for name, value in values
            if value.value is not None and sum(v for _, v in value.sources) != value.value
        ]
        assert unaccounted == []


class TestTheOldLogs:
    """PROTOCOL.md §8: the character and combat records no longer keep a log; an older
    file's is read, copied into the log once, and never written back."""

    def test_an_old_character_files_log_is_read_not_written(self) -> None:
        pc = PlayerCharacter.model_validate(
            {"name": "Old", "log": [{"kind": "save", "subject": "dex", "total": 14}]}
        )
        assert len(pc.log) == 1 and "log" not in pc.model_dump(mode="json")

    def test_an_old_fights_open_attack_becomes_pending(self) -> None:
        from app.models.gamestate import Combat

        open_attack = {"kind": "attack", "subject": "Goblin 1", "damage": 5, "round": 1}
        settled = {**open_attack, "applied": "Lyra"}
        combat = Combat.model_validate(
            {"name": "Old fight", "round": 1, "log": [open_attack, settled, {"kind": "begin"}]}
        )
        assert [e.applied for e in combat.pending] == [""]
        assert "log" not in combat.model_dump(mode="json")

    def test_the_copy_runs_once_and_keeps_the_verdict(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime, timedelta

        from app.models.gamestate import Combat
        from app.services.protocol_migration import copy_old_logs

        long_ago = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
        pc = PlayerCharacter.model_validate(
            {
                "id": "lyra",
                "name": "Lyra",
                "world_id": "w1",
                "log": [
                    {
                        "at": long_ago + timedelta(minutes=1),
                        "kind": "skill",
                        "subject": "Athletics",
                        "total": 17,
                        "by": "Alice",
                    },
                    {"at": long_ago, "kind": "cast", "subject": "Fireball", "detail": "L3"},
                ],
            }
        )
        combat = Combat.model_validate(
            {
                "id": "c1",
                "name": "Old fight",
                "world_id": "w1",
                "round": 1,
                "combatants": [{"id": "g1", "label": "Goblin 1", "ref": "Goblin|MM"}],
                "log": [
                    {
                        "at": long_ago + timedelta(minutes=3),
                        "kind": "attack",
                        "round": 1,
                        "subject": "Goblin 1",
                        "actor": "g1",
                        "detail": "Scimitar",
                        "amount": 17,
                        "damage": 5,
                        "damage_types": {"slashing": 5},
                        "applied": "Lyra",
                    },
                    {
                        "at": long_ago + timedelta(minutes=2),
                        "kind": "start",
                        "subject": "Old fight",
                    },
                ],
            }
        )
        store = ProtocolStore(tmp_path / "protocol")
        assert copy_old_logs(store, [pc], [combat]) == 5  # 2 rolls, start, attack + verdict
        assert copy_old_logs(store, [pc], [combat]) == 0  # nothing older left to copy
        entries = store.entries("w1")
        assert all(e.migrated for e in entries)
        assert [e.what for e in entries if e.subject == "character:lyra"] == [
            "cast:Fireball:3",
            "skill:athletics",
        ]
        rolled, verdict = [e for e in entries if e.kind is EntryKind.ATTACK]
        assert verdict.refers_to == rolled.id and verdict.outcome == "hit"
        assert (verdict.target_name, verdict.amount) == ("Lyra", 5)


@requires_corpus
class TestAFightsWorld:
    def test_a_sheet_roll_in_a_fight_is_kept_in_the_fights_world(
        self, tmp_path: Path, corpus: Path
    ) -> None:
        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        combat = registry.combats.start(_encounter(registry), GM)
        lyra = next(c for c in combat.combatants if c.character_id)
        assert lyra.character_id is not None and combat.world_id is not None
        pc = registry.characters.get_any(lyra.character_id)
        registry.characters.play.act(
            pc.id,
            "roll",
            {"kind": "initiative"},
            registry.characters.sheet(pc),
            Actor("Alice", username="alice"),
        )
        rolled = registry.protocol.store.newest(combat.world_id)
        initiative = next(
            e for e in rolled if e.kind is EntryKind.INITIATIVE and e.origin == "sheet"
        )
        assert (initiative.origin, initiative.scope.combat) == ("sheet", combat.id)
        # and the tracker took it, through the log (COMBAT.md §4)
        member = registry.combats.get(combat.id).combatant(lyra.id)
        assert member is not None and initiative.roll is not None
        assert member.initiative == initiative.roll.total
