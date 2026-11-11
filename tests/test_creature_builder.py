"""The creature builder (app/services/creature_builder.py) — maths, text and pages."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.compendium import Monster
from app.models.gamestate import CreatureBlock, CreatureDesign, Role, SourceSet, World
from app.services.creature_builder import (
    HOMEBREW_SOURCE,
    CreatureError,
    Numbers,
    attack_text,
    entries_text,
    evaluate,
    link_numbers,
    read_offence,
    text_entries,
)
from app.store.creature_tables import CreatureTables
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


class TestExpressions:
    def test_the_corpus_formulas(self) -> None:
        legendary = (
            "((expectedCr >= 0 && expectedCr <= 4) ? 10*trait : ((expectedCr >= 5 && "
            "expectedCr <= 10) ? 20*trait : ((expectedCr >= 11) ? 30*trait : 0)))"
        )
        assert evaluate(legendary, {"expectedCr": 12, "trait": 3}) == 90
        assert evaluate(legendary, {"expectedCr": 2, "trait": 1}) == 10
        frightful = "Math.floor((expectedCr <= 10) ? hp * 0.25 : 0)"
        assert evaluate(frightful, {"expectedCr": 5, "hp": 90}) == 22
        assert evaluate("hpActual * 0.33", {"hpActual": 100}) == pytest.approx(33)
        assert evaluate("2", {}) == 2

    def test_nothing_else_is_evaluated(self) -> None:
        assert evaluate("__import__('os')", {}) == 0
        assert evaluate("hp +", {"hp": 1}) == 0
        assert evaluate("", {}) == 0


class TestText:
    def test_placeholders_follow_the_numbers(self) -> None:
        numbers = Numbers("Bog Hag", 3, {"str": 2, "dex": 0, "con": 1})
        text = (
            "<$title_short_name$> bites. {@hit <$to_hit__str$>}, {@h}<$damage_avg__4.5+str$> "
            "({@damage 1d8<$damage_mod__str$>}), DC <$dc__con$>, "
            "{@damage 1d4<$damage_mod__dex$>}, <$mystery$>"
        )
        assert numbers.fill(text) == (
            "The bog hag bites. {@hit 5}, {@h}6 ({@damage 1d8 + 2}), DC 12, "
            "{@damage 1d4}, <$mystery$>"
        )
        # an average without an ability is the dice's own; an unknown ability stays
        assert numbers.fill("<$damage_avg__10.5$> <$damage_avg__4.5+foo$>") == (
            "10 <$damage_avg__4.5+foo$>"
        )

    def test_printed_numbers_are_linked_where_they_match(self) -> None:
        mods = {"str": 2, "dex": 2, "con": 1, "int": -1, "wis": 0, "cha": -1}
        text = (
            "{@atk mw} {@hit 4} to hit, reach 5 ft. {@h}11 ({@damage 2d8 + 2}) piercing "
            "damage. DC 11 Constitution, or {@hit 9} and 7 ({@damage 2d6})."
        )
        linked = link_numbers(text, 2, mods)
        assert "{@hit <$to_hit__str$>}" in linked
        assert "<$damage_avg__9+str$> ({@damage 2d8<$damage_mod__str$>})" in linked
        assert "DC <$dc__con$>" in linked
        assert "{@hit 9}" in linked and "7 ({@damage 2d6})" in linked  # no match: as printed
        assert Numbers("x", 2, mods).fill(linked) == text

    def test_entries_and_text(self) -> None:
        entries = [
            "First.",
            {"type": "list", "items": ["one", {"type": "item", "name": "Two.", "entry": "b"}]},
        ]
        assert entries_text(entries) == "First.\n\n• one\n\n• Two. b"
        assert text_entries("A\nline.\n\n  B.  ") == ["A line.", "B."]

    def test_an_attack_in_placeholders(self) -> None:
        text = attack_text(ranged=True, ability="dex", dice="2d6", damage_type="piercing")
        assert "{@atk rw} {@hit <$to_hit__dex$>}" in text and "range 80/320 ft." in text
        assert "<$damage_avg__7+dex$> ({@damage 2d6<$damage_mod__dex$>})" in text

    def test_offence_is_read_from_the_actions(self) -> None:
        actions = [
            {"name": "Multiattack", "entries": ["It makes three attacks."]},
            {"name": "Claw", "entries": ["{@hit 6} to hit. {@h}10 ({@damage 2d6 + 3}) slashing."]},
            {"name": "Breath", "entries": ["{@dc 15} Dexterity, 21 ({@damage 6d6}) fire."]},
        ]
        assert read_offence(actions) == (30, 6, 15)


@pytest.fixture(scope="module")
def tables(corpus: Path) -> CreatureTables:
    return CreatureTables(corpus)


class TestTables:
    def test_rows_features_and_templates(self, tables: CreatureTables) -> None:
        assert len(tables.rows()) == 34
        row = tables.row("1/4")
        assert row is not None and (row.pb, row.ac, row.hp_min, row.hp_max) == (2, 13, 36, 49)
        assert len(tables.features()) == 25 and tables.feature("pack tactics") is not None
        assert len(tables.traits()) == 231 and len(tables.actions()) == 89
        bite = tables.template("action", "Bite (Cloaker)")
        assert bite is not None and bite.title == "Bite"

    def test_no_corpus(self) -> None:
        empty = CreatureTables(None)
        assert empty.rows() == [] and empty.features() == [] and empty.traits() == []


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> Iterator[StoreRegistry]:
    store = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    store.load()
    yield store


class TestDesigns:
    def test_a_blank_creature_starts_at_its_cr(self, registry: StoreRegistry) -> None:
        service = registry.creatures
        off = []
        for row in registry.creature_tables.rows()[1:]:  # CR 0's damage band is 0–1
            design = service.create(f"Test {row.cr.replace('/', '-')}", None, row.cr)
            estimate = service.math.estimate(design)
            assert estimate is not None
            if estimate.final != row.cr:
                off.append((row.cr, estimate.final))
        assert off == []

    def test_from_a_printed_creature_and_scaled(self, registry: StoreRegistry) -> None:
        service = registry.creatures
        design = service.create("", None, "5", "bugbear|mm")
        assert design.name == "Bugbear (variant)" and design.based_on == "Bugbear|MM"
        assert design.cr == "5" and [a.name for a in design.actions] == ["Morningstar", "Javelin"]
        made = service.math.statblock(design)
        # proficiency +3 at CR 5, STR +2: the linked attack follows
        assert made["action"][0]["entries"][0].startswith("{@atk mw} {@hit 5} to hit")
        assert Monster.model_validate(made).cr == "5"
        fitted = service.math.fit(design)
        hp, _formula = service.math.hp(fitted)
        assert fitted.ac == 15 and 131 <= hp <= 145

    def test_names_are_needed_and_unique(self, registry: StoreRegistry) -> None:
        service = registry.creatures
        service.create("Ash Wight", None, "3")
        with pytest.raises(CreatureError) as taken:
            service.create("ash wight", None, "1")
        assert taken.value.key == "creatures.error.name_taken"
        with pytest.raises(CreatureError) as missing:
            service.create("X", None, "1", "Nothing|XX")
        assert missing.value.key == "creatures.error.base"
        with pytest.raises(CreatureError):
            service.create("   ", None, "1")

    def test_publish_makes_it_a_creature_of_the_world(self, registry: StoreRegistry) -> None:
        world = registry.gamestate.worlds.create(
            World(name="Mirefall", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        service = registry.creatures
        design = service.create("Ash Wight", world.id, "3")
        service.add_feature(design, "Pack Tactics", None)
        service.add_template(design, "action", "Bite (Cloaker)")
        service.save(design)
        assert service.unpublished(service.get(design.id))
        done = service.publish()
        assert done.count == 1 and done.worlds == ["Mirefall"]
        data = json.loads(service.homebrew_file.read_text())
        assert data["_meta"]["sources"][0]["json"] == HOMEBREW_SOURCE
        assert [m["name"] for m in data["monster"]] == ["Ash Wight"]
        assert not service.unpublished(service.get(design.id))
        world_now = registry.gamestate.worlds.get(world.id)
        assert world_now.source_set.allows(HOMEBREW_SOURCE)
        entry = registry.compendium.get("monster", "Ash Wight|Homebrew")
        assert [t.name for t in entry.trait] == ["Pack Tactics"]  # type: ignore[attr-defined]
        found = registry.compendium.resolver().resolve_parts(
            "monster", "ash wight", None, world_now.source_set
        )
        assert found.entry is not None
        service.delete(design.id)
        service.publish()
        assert json.loads(service.homebrew_file.read_text())["monster"] == []


@pytest.fixture
async def client(
    registry: StoreRegistry,
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("pia", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


def _form(design: CreatureDesign, **extra: object) -> dict[str, object]:
    """The builder form as the page would send it."""
    data: dict[str, object] = {
        "name": design.name,
        "world": design.world_id or "",
        "size": design.size,
        "type": design.type,
        "alignment": " ".join(design.alignment),
        "cr": design.cr,
        "ac": str(design.ac),
        "hit_dice": str(design.hit_dice),
        "speed_walk": "30",
        **{f"ab_{k}": str(v) for k, v in design.abilities.items()},
        "actions_name": [b.name for b in design.actions],
        "actions_text": [b.text for b in design.actions],
    }
    return {**data, **extra}


class TestPages:
    async def test_the_builder_round_trip(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        listed = await ac.get("/gm/creatures")
        assert listed.status_code == 200 and "No creatures yet" in listed.text
        made = await ac.post("/gm/creatures", data={"name": "Mire Hound", "cr": "2"})
        assert made.status_code == 303
        url = made.headers["location"]
        page = (await ac.get(url)).text
        assert "Mire Hound" in page and "Typical at CR 2" in page and "Not published" in page
        design = registry.creatures.list()[0]
        added = await ac.post(
            url,
            data=_form(
                design,
                action="attack",
                attack_name="Bite",
                attack_kind="melee",
                attack_ability="str",
                attack_dice="2d6",
                attack_type="piercing",
                saves=["dex", "con"],
            ),
        )
        assert added.status_code == 303 and added.headers["location"].endswith("#cb-actions")
        design = registry.creatures.get(design.id)
        assert [b.name for b in design.actions] == ["Slam", "Bite"]
        assert design.saves == ["dex", "con"]
        removed = await ac.post(url, data=_form(design, action="remove:actions:0"))
        assert removed.status_code == 303
        assert [b.name for b in registry.creatures.get(design.id).actions] == ["Bite"]
        current = registry.creatures.get(design.id)
        published = await ac.post(url, data=_form(current, action="publish"))
        assert "published=1" in published.headers["location"]
        assert "in the compendium as Homebrew" in (await ac.get(published.headers["location"])).text
        ref = await ac.get("/ref/monster/Homebrew/Mire%20Hound")
        assert ref.status_code == 200 and "Bite" in ref.text

    async def test_a_name_clash_keeps_the_form(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        registry.creatures.create("Taken", None, "1")
        other = registry.creatures.create("Other", None, "1")
        answer = await ac.post(
            f"/gm/creatures/{other.id}", data=_form(other, name="Taken", action="save")
        )
        assert answer.status_code == 422 and "already a creature called Taken" in answer.text

    async def test_delete_where_it_is_viewed(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        design = registry.creatures.create("Bog Hound", None, "1")
        link = f'href="/gm/creatures/{design.id}/delete"'
        assert link in (await ac.get("/gm/creatures")).text  # on its row
        builder = (await ac.get(f"/gm/creatures/{design.id}")).text
        head = builder[builder.index('class="cb-title"') : builder.index('class="cb-layout"')]
        assert link in head and builder.count(link) == 1  # in the head, not the form

    async def test_unknown_and_forbidden(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        assert (await ac.get("/gm/creatures/nope")).status_code == 404
        design = registry.creatures.create("Hidden", None, "1")
        await ac.post("/logout")
        await ac.post("/login", data={"username": "pia", "password": PASSWORD})
        assert (await ac.get(f"/gm/creatures/{design.id}")).status_code == 403

    async def test_the_block_kinds_survive_a_save(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        design = registry.creatures.create("Keeper", None, "1")
        design.traits.append(CreatureBlock(name="Keen Smell", text="Advantage on smell."))
        registry.creatures.save(design)
        form = _form(
            design,
            action="save",
            traits_name=["Keen Smell"],
            traits_text=["Advantage on Wisdom ({@skill Perception}) checks that rely on smell."],
        )
        await ac.post(f"/gm/creatures/{design.id}", data=form)
        traits = registry.creatures.get(design.id).traits
        assert traits[0].text.startswith("Advantage on Wisdom")
