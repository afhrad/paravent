"""Naming styles: patterns, parts, the books' tables and the starters (NAMES.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.naming import ByGenderPart, ElementsPart, ListPart, NamingStyle
from app.services.dice import Dice
from app.services.naming import ANY_STYLE, NameMaker, Naming, NamingError
from app.store.npc_tables import NpcTables


def _style(**parts: object) -> dict[str, object]:
    return {
        "name": "Test",
        "pattern": "{given}",
        "parts": {"given": ListPart(names=["A"])},
        **parts,
    }


class TestStyle:
    def test_a_pattern_names_only_its_parts(self) -> None:
        with pytest.raises(ValidationError):
            NamingStyle(**_style(pattern="{given} {nobody}"))
        with pytest.raises(ValidationError):
            NamingStyle(**_style(pattern="{given:shout}"))
        with pytest.raises(ValidationError):
            NamingStyle(**_style(pattern="no parts"))

    def test_gender_is_settled_once(self) -> None:
        style = NamingStyle(
            name="Test",
            pattern="{given} {link}",
            parts={
                "given": ListPart(female=["Asa"], male=["Bo"]),
                "link": ByGenderPart(female="bint", male="ibn"),
            },
        )
        names = {NameMaker(Dice()).make(style) for _ in range(40)}
        assert names == {"Asa bint", "Bo ibn"}
        assert NameMaker(Dice()).make(style, "female") == "Asa bint"

    def test_rewrites_and_initials_and_one_value_per_part(self) -> None:
        style = NamingStyle(
            name="Test",
            pattern="{father:initial}. {given} {father}sson",
            parts={"given": ListPart(names=["Asa"]), "father": ListPart(names=["Bjorn"])},
            rewrites={"bjornsson": "bjarnarson"},
        )
        assert NameMaker(Dice()).make(style) == "B. Asa bjarnarson"

    def test_elements_never_double(self) -> None:
        part = ElementsPart(first=["Wulf"], second=["wulf", "ric"])
        style = NamingStyle(name="Test", pattern="{given}", parts={"given": part})
        assert {NameMaker(Dice()).make(style) for _ in range(20)} == {"Wulfric"}


class TestStarters:
    @pytest.fixture
    def naming(self) -> Naming:
        return Naming(NpcTables(None))

    def test_there_are_fifteen_and_each_makes_names(self, naming: Naming) -> None:
        assert len(naming.starters) == 15
        for entry in naming.entries():
            assert entry.kind in ("starter", "race", "subject")
            assert len(naming.names(entry.key, count=5)) == 5

    def test_a_norse_genitive(self, naming: Naming) -> None:
        names = naming.names("starter:old-norse", "female", count=60)
        surnames = {n.split()[1] for n in names}
        assert all(n.endswith("dottir") for n in surnames)
        assert not any("bjornsd" in n for n in surnames)

    def test_a_tamil_initial_and_a_nahua_number(self, naming: Naming) -> None:
        assert naming.name("starter:tamil")[1:3] == ". "
        assert (
            naming.name("starter:nahua-calendar")
            .split()[-1]
            .endswith(("tl", "tli", "lli", "li", "in"))
        )

    def test_names_differ_and_avoid_the_used(self, naming: Naming) -> None:
        all_of_it = naming.names("starter:tamil", "male", count=400)
        assert len(all_of_it) == len(set(all_of_it))
        rest = naming.names("starter:tamil", "male", count=400, avoid=all_of_it[1:])
        assert rest == all_of_it[:1]

    def test_an_unknown_key(self, naming: Naming) -> None:
        with pytest.raises(NamingError):
            naming.name("starter:nobody")


def test_the_tables_own_styles_are_offered(tmp_path: Path) -> None:
    own = NamingStyle(
        id="x", name="Murloc", pattern="{given}", parts={"given": ListPart(names=["Mrgl"])}
    )
    naming = Naming(NpcTables(None), lambda: [own])
    assert "own:x" in [e.key for e in naming.entries()]
    assert naming.name("own:x") == "Mrgl"


class TestBooks:
    @pytest.fixture
    def tables(self, corpus: Path) -> NpcTables:
        return NpcTables(corpus)

    def test_every_book_style_makes_a_name(self, tables: NpcTables) -> None:
        naming = Naming(tables)
        for entry in naming.entries():
            assert naming.name(entry.key), entry.key

    def test_the_default(self, tables: NpcTables) -> None:
        assert Naming(tables).default_key("2024") == ANY_STYLE
        assert Naming(tables).default_key("2014").startswith("Human|")

    def test_a_given_name_has_the_gender_asked_for(self, tables: NpcTables) -> None:
        naming = Naming(tables)
        dwarf = next(e for e in naming.entries() if e.key == "Dwarf|XGE").style
        assert dwarf is not None and dwarf.pattern == "{given} {family}"
        part = dwarf.parts["given"]
        assert isinstance(part, ListPart) and "Anbera" in part.female and "Adrik" in part.male
