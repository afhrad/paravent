"""A creature's conditions bend its own rolls (app/services/condition_rolls.py)."""

from __future__ import annotations

from app.services.condition_rolls import bend
from app.services.dice import Advantage as A


def test_a_condition_gives_disadvantage_with_its_reason() -> None:
    bent = bend(A.NORMAL, ["poisoned"], "attack")
    assert bent.advantage is A.DISADVANTAGE and bent.reasons == ("poisoned",)
    assert bend(A.NORMAL, ["poisoned"], "check").advantage is A.DISADVANTAGE
    assert bend(A.NORMAL, ["poisoned"], "save:con").advantage is A.NORMAL


def test_advantage_and_disadvantage_cancel() -> None:
    assert bend(A.ADVANTAGE, ["poisoned"], "attack").advantage is A.NORMAL
    assert bend(A.NORMAL, ["invisible", "prone", "blinded"], "attack").advantage is A.NORMAL
    assert bend(A.NORMAL, ["invisible"], "attack").advantage is A.ADVANTAGE


def test_saves_by_ability() -> None:
    assert bend(A.NORMAL, ["restrained"], "save:dex").advantage is A.DISADVANTAGE
    assert bend(A.NORMAL, ["restrained"], "save:str").advantage is A.NORMAL
    assert bend(A.NORMAL, ["charmed", "deafened"], "attack").advantage is A.NORMAL  # no effect


def test_exhaustion_by_edition() -> None:
    assert bend(A.NORMAL, [], "check", exhaustion=1).advantage is A.DISADVANTAGE
    assert bend(A.NORMAL, [], "attack", exhaustion=2).advantage is A.NORMAL  # 2014: from 3
    assert bend(A.NORMAL, [], "save:wis", exhaustion=3).advantage is A.DISADVANTAGE
    modern = bend(A.NORMAL, [], "attack", exhaustion=2, edition="2024")
    assert (modern.advantage, modern.penalty, modern.reasons) == (A.NORMAL, -4, ("exhaustion",))
    assert bend(A.NORMAL, [], "", exhaustion=3).reasons == ()  # not a d20 test
