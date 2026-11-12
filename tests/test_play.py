"""The rules behind the sheet's buttons."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry
from app.services.dice import Dice
from app.services.play import Actor, PlayError, PlayService
from app.services.protocol import ProtocolService
from app.services.sheet import build_sheet
from app.store.characters import CharacterStore
from app.store.compendium import CompendiumStore
from app.store.protocol import ProtocolStore, log_key
from tests.conftest import requires_corpus
from tests.test_dice_text_events import Scripted

ACTOR = Actor("Alice")
EXAMPLE = (
    Path(__file__).resolve().parent.parent / "documentation" / "examples" / "lyra-mooncaller.json"
)


class Table:
    """A character in a store, a play service with scripted dice and the log it writes
    to, and a way to act."""

    def __init__(self, root: Path, compendium: CompendiumStore | None = None, **data: Any) -> None:
        self.store = CharacterStore(root)
        self.store.load()
        self.compendium = compendium
        self.pc = self.store.create(PlayerCharacter.model_validate(data))
        self.dice = Scripted()
        self.log = ProtocolStore(root.parent / f"{root.name}-protocol")
        self.play = PlayService(self.store, Dice(self.dice), ProtocolService(self.log))

    def roll(self, *faces: int) -> Table:
        self.dice.values.extend(faces)
        return self

    def act(self, action: str, **params: Any) -> PlayerCharacter:
        current = self.store.get(self.pc.id)
        sheet = build_sheet(current, self.compendium)
        return self.play.act(
            self.pc.id, action, {k: str(v) for k, v in params.items()}, sheet, ACTOR
        )

    @property
    def now(self) -> PlayerCharacter:
        return self.store.get(self.pc.id)

    @property
    def latest(self) -> ProtocolEntry:
        """The newest entry the character's actions put in the log."""
        return self.log.newest(log_key(self.pc.world_id), limit=1)[0]


def _total(table: Table) -> int:
    roll = table.latest.roll
    assert roll is not None
    return roll.total


@pytest.fixture
def fighter(tmp_path: Path) -> Table:
    return Table(
        tmp_path / "c",
        name="Bram",
        classes=[{"class": "Homebrew Fighter", "level": 5}],
        abilities={"str": 16, "dex": 12, "con": 14, "wis": 10},
        skills=["Athletics"],
        overrides={"hp_max": 40, "slots.1": 2},
        resources=[
            {"name": "Second Wind", "max": 1, "recharge": "short"},
            {"name": "Indomitable", "max": 1, "recharge": "long"},
        ],
    )


class TestHitPoints:
    def test_damage_takes_temp_hp_first(self, fighter: Table) -> None:
        fighter.act("hp", mode="temp", amount=5)
        fighter.act("hp", mode="damage", amount=8)
        assert (fighter.now.state.temp_hp, fighter.now.state.hp) == (0, 37)

    def test_temp_hp_does_not_stack(self, fighter: Table) -> None:
        fighter.act("hp", mode="temp", amount=5)
        fighter.act("hp", mode="temp", amount=3)
        assert fighter.now.state.temp_hp == 5

    def test_damage_stops_at_zero(self, fighter: Table) -> None:
        fighter.act("hp", mode="damage", amount=99)
        assert fighter.now.state.hp == 0

    def test_healing_stops_at_the_maximum(self, fighter: Table) -> None:
        fighter.act("hp", mode="damage", amount=10)
        fighter.act("hp", mode="heal", amount=50)
        assert (fighter.now.state.hp, fighter.now.state.hp_max) == (40, 40)

    def test_every_change_records_the_maximum(self, fighter: Table) -> None:
        assert fighter.now.state.hp_max is None  # nothing written yet
        fighter.act("inspiration")
        assert (fighter.now.state.hp, fighter.now.state.hp_max) == (40, 40)

    def test_damage_at_zero_is_a_failed_death_save(self, fighter: Table) -> None:
        fighter.act("hp", mode="damage", amount=40)
        fighter.act("hp", mode="damage", amount=1)
        assert fighter.now.state.death_saves.failures == 1

    def test_healing_from_zero_resets_death_saves(self, fighter: Table) -> None:
        fighter.act("hp", mode="damage", amount=40)
        fighter.act("hp", mode="damage", amount=1)
        fighter.act("hp", mode="heal", amount=3)
        assert fighter.now.state.death_saves.failures == 0
        assert fighter.now.state.hp == 3

    def test_bad_amount_is_refused(self, fighter: Table) -> None:
        with pytest.raises(PlayError):
            fighter.act("hp", mode="damage", amount="lots")


class TestConcentration:
    def test_damage_rolls_a_con_save_and_keeps_on_success(self, fighter: Table) -> None:
        fighter.store.update(fighter.pc.id, state={"concentration": "Bless|PHB"})
        fighter.roll(12).act("hp", mode="damage", amount=6)  # DC 10, CON +2 → 14
        assert fighter.now.state.concentration is not None
        entry = fighter.latest
        assert entry.roll is not None
        assert (entry.kind, entry.outcome, entry.roll.total) == (
            EntryKind.CONCENTRATION,
            "success",
            14,
        )

    def test_failure_drops_it(self, fighter: Table) -> None:
        fighter.store.update(fighter.pc.id, state={"concentration": "Bless|PHB"})
        fighter.roll(3).act("hp", mode="damage", amount=30)  # DC 15
        assert fighter.now.state.concentration is None
        assert fighter.latest.against == 15

    def test_drop(self, fighter: Table) -> None:
        fighter.store.update(fighter.pc.id, state={"concentration": "Bless|PHB"})
        fighter.act("concentration", op="drop")
        assert fighter.now.state.concentration is None


class TestSlotsAndResources:
    def test_spend_and_recover(self, fighter: Table) -> None:
        fighter.act("slot", level=1, op="spend")
        fighter.act("slot", level=1, op="spend")
        assert fighter.now.state.slots_used == {"1": 2}
        with pytest.raises(PlayError) as caught:
            fighter.act("slot", level=1, op="spend")
        assert caught.value.key == "play.error.no_slot"
        fighter.act("slot", level=1, op="recover")
        assert fighter.now.state.slots_used == {"1": 1}

    def test_no_slot_of_a_level_the_character_lacks(self, fighter: Table) -> None:
        with pytest.raises(PlayError):
            fighter.act("slot", level=3, op="spend")

    def test_resource_use_and_exhaust(self, fighter: Table) -> None:
        fighter.act("resource", name="Second Wind", op="use")
        with pytest.raises(PlayError) as caught:
            fighter.act("resource", name="Second Wind", op="use")
        assert caught.value.key == "play.error.resource_spent"

    def test_a_refusal_changes_nothing(self, fighter: Table) -> None:
        before = fighter.store.revision(fighter.pc.id)
        with pytest.raises(PlayError):
            fighter.act("slot", level=9, op="spend")
        assert fighter.store.revision(fighter.pc.id) == before


class TestRests:
    def test_short_rest_restores_short_resources_only(self, fighter: Table) -> None:
        fighter.act("resource", name="Second Wind", op="use")
        fighter.act("resource", name="Indomitable", op="use")
        fighter.act("hp", mode="damage", amount=10)
        fighter.act("rest", kind="short")
        state = fighter.now.state
        assert state.resources_used == {"Indomitable": 1}
        assert state.hp == 30  # a short rest heals only through hit dice

    def test_long_rest_restores_everything(self, fighter: Table) -> None:
        fighter.store.update(
            fighter.pc.id,
            state={
                "hp": 3,
                "temp_hp": 2,
                "slots_used": {"1": 2},
                "exhaustion": 2,
                "resources_used": {"Indomitable": 1},
            },
        )
        fighter.act("rest", kind="long")
        state = fighter.now.state
        assert (state.hp, state.hp_max, state.temp_hp) == (40, 40, 0)
        assert (state.slots_used, state.resources_used) == ({}, {})
        assert state.exhaustion == 1
        assert fighter.latest.kind is EntryKind.REST


class TestRolls:
    def test_skill_with_advantage_logs_both_dice(self, fighter: Table) -> None:
        fighter.roll(5, 16).act("roll", kind="skill", subject="athletics", adv=1)
        entry = fighter.latest
        assert entry.roll is not None
        assert (entry.kind, entry.what, entry.roll.total, entry.actor_name) == (
            EntryKind.SKILL,
            "skill:athletics",
            16 + 6,
            "Alice",
        )
        assert entry.roll.dice[0].results == [5, 16] and entry.roll.mode == "advantage"

    def test_save_uses_the_save_bonus(self, fighter: Table) -> None:
        fighter.roll(10).act("roll", kind="save", subject="con")
        assert _total(fighter) == 12  # no proficiency known for a homebrew class

    def test_a_heavy_load_bends_strength_dexterity_and_constitution(self, fighter: Table) -> None:
        """Variant encumbrance: 180 lb. of coins on STR 16 is past × 10 (160 lb.)."""
        fighter.store.update(fighter.pc.id, currency={"gp": 9000})

        def rolled(**params: Any) -> Any:
            current = fighter.store.get(fighter.pc.id)
            sheet = build_sheet(current, None, variant_encumbrance=True)
            fighter.roll(15, 5)
            fighter.play.act(
                fighter.pc.id, "roll", {k: str(v) for k, v in params.items()}, sheet, ACTOR
            )
            return fighter.latest.roll

        save = rolled(kind="save", subject="str")
        assert save.mode == "disadvantage" and "condition:encumbered" in save.reasons
        skill = rolled(kind="skill", subject="athletics")
        assert skill.mode == "disadvantage"
        wisdom = rolled(kind="save", subject="wis")
        assert wisdom.mode != "disadvantage" and not wisdom.reasons

    def test_natural_twenty_is_marked(self, fighter: Table) -> None:
        fighter.roll(20).act("roll", kind="check", subject="str")
        assert fighter.latest.outcome == "crit"

    def test_unarmed_attack_and_damage(self, fighter: Table) -> None:
        fighter.roll(11).act("roll", kind="attack", subject="Unarmed Strike")
        assert _total(fighter) == 11 + 3 + 3
        fighter.act("roll", kind="damage", subject="Unarmed Strike")
        assert _total(fighter) == 4  # 1 + STR

    def test_custom_dice(self, fighter: Table) -> None:
        fighter.roll(2, 6).act("roll", kind="custom", expr="2d6 + 1")
        assert _total(fighter) == 9

    def test_bad_custom_dice(self, fighter: Table) -> None:
        with pytest.raises(PlayError) as caught:
            fighter.act("roll", kind="custom", expr="fireball")
        assert caught.value.key == "play.error.bad_dice"

    def test_clear_log(self, fighter: Table) -> None:
        fighter.roll(10).act("roll", kind="initiative")
        fighter.act("clear_log")
        assert fighter.latest.kind is EntryKind.CLEAR  # views start after it; nothing deleted


class TestOther:
    def test_conditions_toggle(self, fighter: Table) -> None:
        fighter.act("condition", key="prone")
        assert fighter.now.state.conditions == ["prone"]
        fighter.act("condition", key="prone")
        assert fighter.now.state.conditions == []

    def test_unknown_condition(self, fighter: Table) -> None:
        with pytest.raises(PlayError):
            fighter.act("condition", key="sleepy")

    def test_exhaustion_is_bounded(self, fighter: Table) -> None:
        fighter.act("exhaustion", delta=-1)
        assert fighter.now.state.exhaustion == 0
        for _ in range(8):
            fighter.act("exhaustion", delta=1)
        assert fighter.now.state.exhaustion == 6

    def test_inspiration_toggles(self, fighter: Table) -> None:
        fighter.act("inspiration")
        assert fighter.now.state.inspiration

    def test_death_save_only_at_zero(self, fighter: Table) -> None:
        with pytest.raises(PlayError):
            fighter.act("death_save")

    @pytest.mark.parametrize(
        ("face", "successes", "failures", "hp"),
        [(20, 0, 0, 1), (1, 0, 2, 0), (10, 1, 0, 0), (9, 0, 1, 0)],
    )
    def test_death_saves(
        self, fighter: Table, face: int, successes: int, failures: int, hp: int
    ) -> None:
        fighter.act("hp", mode="damage", amount=40)
        fighter.roll(face).act("death_save")
        saves = fighter.now.state.death_saves
        assert (saves.successes, saves.failures, fighter.now.state.hp) == (successes, failures, hp)

    def test_unknown_action(self, fighter: Table) -> None:
        with pytest.raises(PlayError):
            fighter.act("teleport")


@requires_corpus
class TestCastingAgainstTheCorpus:
    @pytest.fixture
    def lyra(self, tmp_path: Path, loaded_compendium: CompendiumStore) -> Table:
        data = json.loads(EXAMPLE.read_text())
        data.pop("id")
        return Table(tmp_path / "c", loaded_compendium, **data)

    def test_cast_spends_a_slot_of_the_spell_level(self, lyra: Table) -> None:
        lyra.act("cast", spell="Fireball|PHB")
        assert lyra.now.state.slots_used["3"] == 2
        assert lyra.latest.kind is EntryKind.CAST and lyra.latest.what == "cast:Fireball:3"

    def test_casting_higher(self, lyra: Table) -> None:
        lyra.act("cast", spell="Fireball|PHB", level=5)
        assert lyra.now.state.slots_used.get("5") == 1

    def test_concentration_spell_replaces_concentration(self, lyra: Table) -> None:
        lyra.act("cast", spell="Web|PHB")
        assert lyra.now.state.concentration is not None
        assert lyra.now.state.concentration.name == "Web"

    def test_cantrips_cost_nothing(self, lyra: Table) -> None:
        before = dict(lyra.now.state.slots_used)
        lyra.act("cast", spell="Fire Bolt|PHB")
        assert lyra.now.state.slots_used == before

    def test_no_slot_left(self, lyra: Table) -> None:
        lyra.act("cast", spell="Cone of Cold|PHB")
        lyra.act("cast", spell="Cone of Cold|PHB")
        with pytest.raises(PlayError) as caught:
            lyra.act("cast", spell="Cone of Cold|PHB")
        assert caught.value.key == "play.error.no_slot_for"

    def test_spell_damage_roll_uses_scaled_cantrip_dice(self, lyra: Table) -> None:
        lyra.roll(4, 9).act("roll", kind="damage", subject="Fire Bolt|PHB")
        assert _total(lyra) == 13  # 2d10 at level 10
        assert lyra.latest.what == "damage:Fire Bolt"  # the name, not the reference

    def test_hit_die_heals_and_is_spent(self, lyra: Table) -> None:
        lyra.roll(5).act("hit_die", faces=6)
        assert lyra.now.state.hp == 41 + 5 + 2
        assert lyra.now.state.hit_dice_used == {"d6": 3}

    def test_long_rest_returns_half_the_hit_dice(self, lyra: Table) -> None:
        lyra.store.update(lyra.pc.id, state={"hit_dice_used": {"d6": 8}})
        lyra.act("rest", kind="long")
        assert lyra.now.state.hit_dice_used == {"d6": 3}  # 10 dice → 5 back

    def test_equipping_changes_the_attacks(self, lyra: Table) -> None:
        lyra.act("equip", index=1)  # the daggers
        sheet = build_sheet(lyra.now, lyra.compendium)
        assert "Dagger" in [a.name for a in sheet.attacks]
