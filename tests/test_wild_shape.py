"""A druid's Wild Shape: the forms the rules allow, the uses, and playing a form."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from app.models.pc import ChosenOption, ClassLevel, PlayerCharacter
from app.services.play import PlayError
from app.services.sheet import build_sheet
from app.services.wild_shape import RESOURCE, find, rules_for
from app.store.compendium import CompendiumStore
from tests.conftest import requires_corpus
from tests.test_play import Table

TABLE_2024: dict[str, Any] = {
    "classTableGroups": [{"colLabels": ["Wild Shape", "Cantrips"], "rows": [[0, 2], [2, 2]] * 10}],
    "classFeatures": ["Wild Shape|Druid|XPHB|2"],
    "edition": "one",
}
TABLE_2014: dict[str, Any] = {"classFeatures": [{"classFeature": "Wild Shape|Druid||2"}]}


def _rules(record: dict[str, Any], level: int, moon: bool = False):
    sub = {"shortName": "Moon"} if moon else None
    return rules_for(ClassLevel(name="Druid", level=level), record, sub)


class TestRules:
    def test_no_wild_shape_before_level_2_or_without_the_feature(self) -> None:
        assert _rules(TABLE_2014, 1) is None
        assert _rules({"classFeatures": []}, 5) is None

    def test_2014_beast_shapes(self) -> None:
        two, four, eight = (_rules(TABLE_2014, n) for n in (2, 4, 8))
        assert (two.max_cr, two.swim, two.fly) == (Fraction(1, 4), False, False)
        assert (four.max_cr, four.swim, four.fly) == (Fraction(1, 2), True, False)
        assert (eight.max_cr, eight.fly) == (Fraction(1), True)
        assert (two.uses, two.known, two.temp_hp, two.hours) == (2, None, 0, 1)

    def test_2014_circle_of_the_moon(self) -> None:
        assert _rules(TABLE_2014, 2, moon=True).max_cr == 1
        assert _rules(TABLE_2014, 9, moon=True).max_cr == 3

    def test_2024_known_forms_uses_and_temporary_hit_points(self) -> None:
        two, eight = _rules(TABLE_2024, 2), _rules(TABLE_2024, 8)
        assert (two.known, two.uses, two.temp_hp, two.swim) == (4, 2, 2, True)
        assert (eight.known, eight.max_cr, eight.fly) == (8, Fraction(1), True)

    def test_2024_circle_of_the_moon_from_level_3(self) -> None:
        assert not _rules(TABLE_2024, 2, moon=True).moon
        six = _rules(TABLE_2024, 6, moon=True)
        assert (six.moon, six.max_cr, six.temp_hp) == (True, Fraction(2), 18)

    def test_flying_and_swimming_beasts(self) -> None:
        two = _rules(TABLE_2014, 2)
        assert two.allows({"speed": {"walk": 40}}, Fraction(1, 4))
        assert not two.allows({"speed": {"walk": 10, "fly": 60}}, Fraction(0))
        assert not two.allows({"speed": {"walk": 20, "swim": 30}}, Fraction(0))
        assert not two.allows({"speed": {"walk": 40}}, Fraction(1, 2))


def _wolf(table: Table) -> str:
    """The wolf the sheet offers: with no world's books, the newest printing."""
    view = build_sheet(table.now, table.compendium).wild_shape
    assert view is not None
    return next(f.reference for f in view.forms if f.name == "Wolf")


def _druid(tmp_path: Path, compendium: CompendiumStore, source: str, **extra: Any) -> Table:
    data: dict[str, Any] = {
        "name": "Sanna",
        "classes": [{"class": f"Druid|{source}", "level": extra.pop("level", 2)}],
        "abilities": {"con": 12, "wis": 16},
        **extra,
    }
    return Table(tmp_path / "c", compendium, **data)


@requires_corpus
class TestOnTheSheet:
    def test_a_2014_druid_may_take_any_beast_that_fits(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        druid = PlayerCharacter.model_validate(
            {"classes": [{"class": "Druid|PHB", "level": 2}], "abilities": {"wis": 16}}
        )
        view = build_sheet(druid, loaded_compendium).wild_shape
        assert view is not None
        names = {f.name for f in view.forms}
        assert "Wolf" in names
        assert "Giant Eagle" not in names and "Crocodile" not in names
        assert view.choices == view.forms

    def test_the_uses_are_a_resource(self, loaded_compendium: CompendiumStore) -> None:
        druid = PlayerCharacter.model_validate({"classes": [{"class": "Druid|XPHB", "level": 6}]})
        uses = next(
            r for r in build_sheet(druid, loaded_compendium).resources if r.name == RESOURCE
        )
        assert (uses.max, uses.short_regain) == (3, 1)


@requires_corpus
class TestPlaying:
    def test_a_2014_form_takes_damage_first_and_carries_the_rest(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        table = _druid(tmp_path, loaded_compendium, "PHB")
        wolf = _wolf(table)
        table.act("wild_shape", op="assume", form=wolf)
        state = table.now.state
        assert state.wild_shape is not None and state.wild_shape.name == "Wolf"
        assert state.wild_shape_hp == 11
        sheet = build_sheet(table.now, loaded_compendium)
        assert sheet.speed.value == 40
        assert sheet.armor_class.explanation == "Wolf form"
        druid_hp = sheet.hp_current
        table.act("hp", mode="damage", amount=4)
        assert (table.now.state.wild_shape_hp, table.now.state.hp) == (7, druid_hp)
        table.act("hp", mode="damage", amount=10)  # 7 for the wolf, 3 carry over
        assert table.now.state.wild_shape is None
        assert table.now.state.hp == druid_hp - 3
        assert table.latest.what == f"wild_shape:off:{wolf}"

    def test_uses_run_out_and_a_short_rest_restores_them(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        table = _druid(tmp_path, loaded_compendium, "PHB")
        wolf = _wolf(table)
        for _ in range(2):
            table.act("wild_shape", op="assume", form=wolf)
            table.act("wild_shape", op="revert")
        with pytest.raises(PlayError):
            table.act("wild_shape", op="assume", form=wolf)
        table.act("rest", kind="short")
        assert RESOURCE not in table.now.state.resources_used

    def test_a_2024_druid_takes_only_known_forms_and_gains_temporary_hit_points(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        table = _druid(tmp_path, loaded_compendium, "XPHB", level=4)
        with pytest.raises(PlayError):
            table.act("wild_shape", op="assume", form="Wolf|XMM")
        table.act("wild_forms", op="learn", form="Wolf|XMM")
        assert [f.name for f in table.now.wild_shapes] == ["Wolf"]
        table.act("wild_shape", op="assume", form="Wolf|XMM")
        state = table.now.state
        assert (state.temp_hp, state.wild_shape_hp) == (4, None)
        assert table.latest.what == "wild_shape:on:Wolf|XMM"
        table.act("wild_forms", op="forget", form="Wolf|XMM")
        assert table.now.wild_shapes == []

    def test_a_2024_short_rest_gives_back_one_use(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        table = _druid(tmp_path, loaded_compendium, "XPHB", wild_shapes=["Wolf|XMM"])
        for _ in range(2):
            table.act("wild_shape", op="assume", form="Wolf|XMM")
        table.act("rest", kind="short")
        assert table.now.state.resources_used == {RESOURCE: 1}
        assert table.now.state.wild_shape is not None  # a short rest keeps the form
        table.act("rest", kind="long")
        assert table.now.state.wild_shape is None
        assert RESOURCE not in table.now.state.resources_used

    def test_learning_more_than_the_level_allows_is_refused(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        sheet_forms = build_sheet(
            _druid(tmp_path, loaded_compendium, "XPHB").pc, loaded_compendium
        ).wild_shape
        assert sheet_forms is not None
        four = [f.reference for f in sheet_forms.forms[:4]]
        table = _druid(tmp_path / "b", loaded_compendium, "XPHB", wild_shapes=four)
        fifth = sheet_forms.forms[4].reference
        with pytest.raises(PlayError):
            table.act("wild_forms", op="learn", form=fifth)
        assert find(sheet_forms.forms, ChosenOption.model_validate(fifth)) is not None
