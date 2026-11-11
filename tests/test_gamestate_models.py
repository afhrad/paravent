"""Tests for the game-state models' own rules: a combatant's identity and health word.

The combat rules themselves (damage, turns, conditions) live in the combat service and
are tested in ``test_combat.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.gamestate import Combat, Combatant


def make(**kwargs: object) -> Combatant:
    """A monster combatant with sensible defaults, overridable per test."""
    defaults = {"label": "Ogre", "ref": "Ogre|MM", "hp": 30, "hp_max": 30}
    return Combatant(**{**defaults, **kwargs})  # type: ignore[arg-type]


class TestHealth:
    @pytest.mark.parametrize(
        ("hp", "word"),
        [
            (30, "unhurt"),
            (16, "hurt"),
            (15, "bloodied"),
            (8, "bloodied"),
            (7, "critical"),
            (0, "down"),
        ],
    )
    def test_the_state_word(self, hp: int, word: str) -> None:
        assert make(hp=hp).health == word

    def test_without_a_maximum_nothing_is_known(self) -> None:
        unknown = make(hp=0, hp_max=0)
        assert unknown.health == "unhurt" and not unknown.is_down

    def test_a_character_is_never_down_by_its_own_fields(self) -> None:
        pc = Combatant(label="Lyra", character_id="abc")
        assert pc.is_character and not pc.is_down


class TestIdentity:
    def test_a_monster_or_a_character_not_both(self) -> None:
        with pytest.raises(ValidationError):
            make(character_id="abc")

    def test_hit_points_are_never_negative(self) -> None:
        with pytest.raises(ValidationError):
            make(hp=-1)

    def test_a_combat_finds_its_combatants(self) -> None:
        ogre = make()
        combat = Combat(name="Fight", combatants=[ogre])
        assert combat.combatant(ogre.id) is ogre and combat.combatant("x") is None
        assert combat.running
