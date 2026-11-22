"""The guides' variant rules for characters (app/services/character_rules.py): rest
variants, hero points, Honor and Sanity, slow natural healing, healer's kit dependency
and defeated, not dead."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models.gamestate import World
from app.models.protocol import EntryKind
from app.services import character_rules
from app.services.character_rules import hero_points_max, kept, rest_minutes
from app.services.i18n import UiCatalogue
from app.services.play import PlayError
from app.services.sheet import build_sheet
from app.services.worlds import WorldInput, WorldService
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore
from app.ui.context import Page
from app.ui.gm_pages import world_form
from app.ui.protocol_pages import rule_text
from tests.conftest import requires_corpus
from tests.test_play import ACTOR, EXAMPLE, Table

ALL = list(character_rules.RULES)


class RulesTable(Table):
    """A character whose world plays ``rules``."""

    def __init__(
        self, root: Path, rules: list[str], compendium: CompendiumStore | None = None, **data: Any
    ) -> None:
        super().__init__(root, compendium, **data)
        self.rules = rules

    def sheet(self) -> Any:
        return build_sheet(self.now, self.compendium, character_rules=self.rules)

    def act(self, action: str, **params: Any) -> Any:
        return self.play.act(
            self.pc.id, action, {k: str(v) for k, v in params.items()}, self.sheet(), ACTOR
        )


def _fighter(tmp_path: Path, rules: list[str], **extra: Any) -> RulesTable:
    return RulesTable(
        tmp_path / "c",
        rules,
        name="Bram",
        classes=[{"class": "Homebrew Fighter", "level": 4}],
        abilities={"str": 16, "dex": 12, "con": 14, "wis": 10, "cha": 8},
        overrides={"hp_max": 40},
        **extra,
    )


class TestRules:
    def test_kept_keeps_one_rest_variant(self) -> None:
        assert kept(["defeated", "rest_gritty", "rest_epic", "nonsense"]) == [
            "rest_epic",
            "defeated",
        ]

    def test_rest_minutes(self) -> None:
        assert rest_minutes([], "long") == 480
        assert rest_minutes(["rest_epic"], "short") == 5
        assert rest_minutes(["rest_gritty"], "long") == 7 * 24 * 60

    def test_hero_points_by_level(self) -> None:
        assert [hero_points_max(n) for n in (1, 2, 3, 10, 20)] == [5, 6, 6, 10, 15]


class TestHeroPoints:
    def test_none_without_the_rule(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, [])
        assert table.sheet().hero_points is None
        with pytest.raises(PlayError):
            table.act("hero_point")

    def test_a_d6_on_the_last_check_once(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["hero_points"])
        assert table.sheet().hero_points == 7  # level 4: 5 + 2
        table.roll(9).act("roll", kind="check", subject="str")
        table.roll(4).act("hero_point")
        entry = table.latest
        assert entry.kind is EntryKind.CUSTOM
        assert entry.what == "rule:hero:16:check:str"  # 9 + 3 + 4
        assert table.sheet().hero_points == 6
        with pytest.raises(PlayError) as refused:
            table.act("hero_point")  # one per roll
        assert refused.value.key == "play.error.no_hero_roll"

    def test_a_new_level_brings_a_new_total(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["hero_points"])
        table.store.update(table.pc.id, state={"hero_points_used": 3, "hero_level": 3})
        assert table.sheet().hero_points == 7  # spent at level 3; level 4 starts afresh

    def test_a_failed_death_save_made_a_success(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["hero_points"])
        table.store.update(table.pc.id, state={"hp": 0})
        table.roll(5).act("death_save")
        assert table.now.state.death_saves.failures == 1
        table.act("hero_point")
        saves = table.now.state.death_saves
        assert (saves.successes, saves.failures) == (1, 0)
        assert table.latest.what == "rule:hero_death"

    def test_not_on_damage(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["hero_points"])
        table.roll(4).act("roll", kind="custom", expr="1d6")
        with pytest.raises(PlayError) as refused:
            table.act("hero_point")
        assert refused.value.key == "play.error.no_hero_roll"


class TestHonorAndSanity:
    def test_scores_checks_and_saves(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["honor", "sanity"])
        sheet = table.sheet()
        assert [a.key for a in sheet.extra_abilities] == ["hon", "san"]
        assert sheet.open_scores == ["hon", "san"]
        table.act("extra_score", key="hon", score=14)
        assert table.now.extra_scores == {"hon": 14}
        table.roll(10).act("roll", kind="save", subject="hon")
        assert table.latest.what == "save:hon"
        assert table.latest.roll is not None and table.latest.roll.total == 12
        table.act("extra_score", key="hon", delta=1)
        assert table.now.extra_scores == {"hon": 15}

    def test_bounded_and_only_where_played(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["honor"])
        with pytest.raises(PlayError):
            table.act("extra_score", key="san", score=12)
        with pytest.raises(PlayError):
            table.act("extra_score", key="hon", score=25)
        table.act("extra_score", key="hon", score=20)
        table.act("extra_score", key="hon", delta=1)
        assert table.now.extra_scores == {"hon": 20}


class TestHealing:
    def test_slow_natural_healing(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["slow_healing"])
        table.store.update(table.pc.id, state={"hp": 10})
        table.act("rest", kind="long")
        assert table.now.state.hp == 10

    @requires_corpus
    def test_healers_kit(self, tmp_path: Path, loaded_compendium: CompendiumStore) -> None:
        data = json.loads(EXAMPLE.read_text())
        data.pop("id", None)
        data["inventory"] = [
            *data.get("inventory", []),
            {"item": "Healer's Kit|PHB", "charges_used": 9},
        ]
        data["state"] = {"hp": 3}
        table = RulesTable(tmp_path / "c", ["healers_kit"], loaded_compendium, **data)
        with pytest.raises(PlayError) as refused:
            table.roll(4).act("hit_die", faces=6)
        assert refused.value.key == "play.error.not_bandaged"
        table.act("bandage")
        assert table.now.state.bandaged
        assert table.latest.what == "rule:bandage:0"
        assert not any(i.item.name == "Healer's Kit" for i in table.now.inventory)  # used up
        table.roll(4).act("hit_die", faces=6)
        table.act("rest", kind="short")
        assert not table.now.state.bandaged


class TestDefeated:
    def test_a_third_failure_defeats(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["defeated"])
        table.store.update(
            table.pc.id, state={"hp": 0, "death_saves": {"successes": 0, "failures": 2}}
        )
        table.roll(4).act("death_save")
        state = table.now.state
        assert (state.hp, state.defeated, state.conditions) == (1, True, ["unconscious"])
        assert state.death_saves.failures == 0

    def test_damage_at_zero_defeats(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["defeated"])
        table.store.update(
            table.pc.id, state={"hp": 0, "death_saves": {"successes": 0, "failures": 2}}
        )
        table.act("hp", mode="damage", amount=3)
        assert table.now.state.defeated

    def test_dies_without_the_rule(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, [])
        table.store.update(
            table.pc.id, state={"hp": 0, "death_saves": {"successes": 0, "failures": 2}}
        )
        table.roll(4).act("death_save")
        assert table.now.state.death_saves.failures == 3
        assert not table.now.state.defeated

    def test_awakening_after_a_long_rest(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["defeated", "slow_healing"])
        table.store.update(
            table.pc.id, state={"hp": 1, "defeated": True, "conditions": ["unconscious"]}
        )
        table.roll(10).act("rest", kind="long")  # 10 + 2 < 20
        assert table.now.state.defeated
        assert table.latest.outcome == "failure"
        table.roll(18).act("rest", kind="long")  # 18 + 2
        state = table.now.state
        assert not state.defeated and state.conditions == []

    def test_greater_restoration_wakes(self, tmp_path: Path) -> None:
        table = _fighter(tmp_path, ["defeated"])
        table.store.update(
            table.pc.id, state={"hp": 1, "defeated": True, "conditions": ["unconscious"]}
        )
        table.act("wake")
        assert not table.now.state.defeated
        assert table.latest.what == "rule:wake"


class TestWorld:
    def test_kept_and_round_tripped(self, tmp_path: Path) -> None:
        store = GameStateStore(tmp_path / "library")
        made = WorldService(store).create(
            WorldInput(
                "Hard Road",
                character_rules=("rest_gritty", "rest_epic", "hero_points", "bogus"),
            )
        )
        assert made.character_rules == ["rest_epic", "hero_points"]
        again = GameStateStore(tmp_path / "library").worlds.get(made.id)
        assert again.character_rules == ["rest_epic", "hero_points"]

    def test_world_form(self) -> None:
        page = Page(UiCatalogue().translator("en"))
        world = World(name="W", character_rules=["rest_epic", "defeated"])
        html = str(
            world_form(
                page,
                _EmptyCatalog(),  # type: ignore[arg-type]
                world=world,
                name="W",
                description="",
                sources=(),
                character_rules=world.character_rules,
            )
        )
        assert 'value="rest_epic" selected' in html
        assert 'id="f-character_rules-defeated"' in html

    def test_log_words(self) -> None:
        page = Page(UiCatalogue().translator("en"))
        assert rule_text(page, "Bram", "hero:16:check:str", 4, "") == (
            "Bram spends a hero point: +4, 16 now"
        )
        assert "Honor: 14 → 15" in rule_text(page, "Bram", "score:hon:14:15", None, "")


class _EmptyCatalog:
    def get(self, code: str) -> None:
        return None

    def grouped(self) -> list[Any]:
        return []
