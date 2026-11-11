"""The library against a new corpus release (app/services/library_check.py): translated
and written work made on one small corpus, checked on the next, which changed under it."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    Adventure,
    Chapter,
    Encounter,
    EncounterMonster,
    Npc,
    Role,
    Scope,
    SourceSet,
    Treasure,
    TreasureItem,
    World,
)
from app.models.pc import PlayerCharacter
from app.services.corpus_check import CorpusReport, Kind, compare
from app.services.i18n import Translatable
from app.services.translation_coverage import MoveTaken
from app.services.translations import english_hash
from app.store.jsonfile import write_json
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"
BOLT = {
    "name": "Test Bolt",
    "source": "ZZ",
    "level": 1,
    "school": "V",
    "entries": ["A bolt knocks you {@condition prone|ZZ}.", "It hurts."],
}
GONE = {"name": "Gone Spell", "source": "ZZ", "level": 1, "school": "V", "entries": ["Old."]}
PRONE = {"name": "Prone", "source": "ZZ", "entries": ["Down."]}
GOBLIN = {
    "name": "Test Goblin",
    "source": "ZZ",
    "size": ["S"],
    "type": "humanoid",
    "ac": [12],
    "hp": {"average": 5, "formula": "2d4"},
    "speed": {"walk": 30},
    "str": 8,
    "dex": 14,
    "con": 10,
    "int": 10,
    "wis": 8,
    "cha": 8,
    "cr": "1/4",
}
SWORD = {"name": "Test Sword", "source": "ZZ", "rarity": "rare", "entries": ["Sharp."]}
FIGHTER = {
    "name": "Tester",
    "source": "ZZ",
    "hd": {"number": 1, "faces": 10},
    "proficiency": ["str"],
    "classFeatures": ["Grit|Tester|ZZ|1"],
}
GRIT = {
    "name": "Grit",
    "source": "ZZ",
    "className": "Tester",
    "classSource": "ZZ",
    "level": 1,
    "entries": ["Hold on."],
}


def write_corpus(root: Path, *, spells: list[dict[str, Any]], monsters: bool = True) -> None:
    files: dict[str, Any] = {
        "spells/spells-zz.json": {"spell": spells},
        "conditionsdiseases.json": {"condition": [PRONE]},
        "items.json": {"item": [SWORD] if monsters else []},
        "bestiary/bestiary-zz.json": {"monster": [GOBLIN] if monsters else []},
        "class/class-tester.json": {"class": [FIGHTER], "classFeature": [GRIT]},
    }
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


def registry_on(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(
        Settings(
            reference_dir=tmp_path / "corpus",
            library_dir=tmp_path / "lib",
            reference_cache_dir=tmp_path / "cache",
            watch_files=False,
        )
    )
    registry.load()
    return registry


def run(registry: StoreRegistry) -> CorpusReport:
    return registry.corpus_health.run(references=False)


def found(report: CorpusReport, kind: Kind) -> list[str]:
    return [f"{x.subject} — {x.detail}" for g in report.groups if g.kind is kind for x in g.items]


@pytest.fixture
def old(tmp_path: Path) -> StoreRegistry:
    """The library as the old release left it: everything in step."""
    write_corpus(tmp_path / "corpus", spells=[BOLT, GONE])
    registry = registry_on(tmp_path)
    store = registry.translations
    store.save(
        "de",
        "spell",
        "Test Bolt|ZZ",
        BOLT,
        {"entries/0": "Ein Blitz wirft dich {@condition prone|ZZ} um.", "entries/1": "Autsch."},
        by="dm",
    )
    store.save("de", "spell", "Gone Spell|ZZ", GONE, {"entries/0": "Alt."}, by="dm")
    registry.glossary.set("de", "spell", "Gone Spell", "Entfallen")
    return registry


def _rewrite(tmp_path: Path, **kwargs: Any) -> StoreRegistry:
    write_corpus(tmp_path / "corpus", **kwargs)
    return registry_on(tmp_path)


class TestTranslations:
    def test_in_step_nothing_is_found(self, old: StoreRegistry) -> None:
        report = run(old)
        assert report.library and report.library_findings == 0

    def test_a_changed_passage_is_stale_and_links_to_the_editor(
        self, old: StoreRegistry, tmp_path: Path
    ) -> None:
        changed = {**BOLT, "entries": [BOLT["entries"][0], "It hurts a lot."]}  # type: ignore[index]
        report = run(_rewrite(tmp_path, spells=[changed, GONE]))
        assert found(report, Kind.TRANSLATION_STALE) == ["spell: Test Bolt|ZZ — entries/1"]
        item = next(g for g in report.groups if g.kind is Kind.TRANSLATION_STALE).items[0]
        assert item.href.startswith("/gm/translate/ref/spell/ZZ/Test%20Bolt?lang=de")
        assert report.ok  # the corpus reads; a translation out of step is no error

    def test_a_shorter_text_orphans_a_passage(self, old: StoreRegistry, tmp_path: Path) -> None:
        shorter = {**BOLT, "entries": BOLT["entries"][:1]}  # type: ignore[index]
        report = run(_rewrite(tmp_path, spells=[shorter, GONE]))
        assert found(report, Kind.TRANSLATION_ORPHAN) == ["spell: Test Bolt|ZZ — entries/1"]

    def test_a_renamed_entry_is_lost_with_its_likely_name(
        self, old: StoreRegistry, tmp_path: Path
    ) -> None:
        renamed = {**BOLT, "name": "Test Bolts"}
        report = run(_rewrite(tmp_path, spells=[renamed, GONE]))
        assert found(report, Kind.TRANSLATION_LOST) == [
            "spell: Test Bolt|ZZ — 2 passage(s); perhaps Test Bolts|ZZ"
        ]

    def test_an_entry_in_another_book_is_named(self, old: StoreRegistry, tmp_path: Path) -> None:
        moved = {**BOLT, "source": "YY"}
        report = run(_rewrite(tmp_path, spells=[moved, GONE]))
        assert "perhaps Test Bolt|YY" in found(report, Kind.TRANSLATION_LOST)[0]

    def test_a_tag_the_english_gained_is_reported(self, old: StoreRegistry, tmp_path: Path) -> None:
        tagged = {**BOLT, "entries": ["A bolt.", "It {@condition prone|ZZ} hurts."]}
        report = run(_rewrite(tmp_path, spells=[tagged, GONE]))
        assert found(report, Kind.TRANSLATION_TAGS) == [
            "spell: Test Bolt|ZZ — {@condition prone|ZZ}"
        ]

    def test_a_dead_link_in_the_translation(self, old: StoreRegistry, tmp_path: Path) -> None:
        old.translations.save(
            "de",
            "spell",
            "Gone Spell|ZZ",
            GONE,
            {"entries/0": "Siehe {@spell Nowhere|ZZ}."},
            by="x",
        )
        assert found(run(old), Kind.TRANSLATION_LINK) == [
            "spell: Gone Spell|ZZ — {@spell Nowhere|ZZ}"
        ]

    def test_a_class_feature_is_found_by_its_composite_key(self, old: StoreRegistry) -> None:
        old.translations.save(
            "de", "classFeature", "Grit|ZZ|Tester|1", GRIT, {"entries/0": "Halt durch."}, by="x"
        )
        assert not found(run(old), Kind.TRANSLATION_LOST)

    def test_a_chapter_of_a_book_that_went(self, old: StoreRegistry, tmp_path: Path) -> None:
        write_json(
            tmp_path / "lib/i18n/de/book/old.json",
            {"OLD|0": {"passages": {"name": {"text": "Alt", "en": english_hash("Old")}}}},
        )
        assert found(run(old), Kind.TRANSLATION_LOST) == ["book: OLD|0 — 1 passage(s)"]


class TestNames:
    def test_a_name_of_an_entry_that_went(self, old: StoreRegistry, tmp_path: Path) -> None:
        assert not found(run(old), Kind.GLOSSARY_LOST)
        report = run(_rewrite(tmp_path, spells=[BOLT]))
        assert found(report, Kind.GLOSSARY_LOST) == ["spell: Gone Spell — Entfallen"]


class TestLibrary:
    @pytest.fixture
    def world(self, old: StoreRegistry) -> World:
        return old.gamestate.worlds.create(
            World(name="Testwelt", source_set=SourceSet(sources=["ZZ", "NOPE"]))
        )

    def test_a_declared_book_that_went(self, old: StoreRegistry, world: World) -> None:
        assert found(run(old), Kind.LIBRARY_BOOK) == ["Testwelt — NOPE"]

    def test_records_pointing_at_entries_that_went(
        self, old: StoreRegistry, world: World, tmp_path: Path
    ) -> None:
        state = old.gamestate
        scope = Scope(world_id=world.id)
        state.encounters.create(
            Encounter(
                name="Fight",
                world_id=world.id,
                monsters=[EncounterMonster(name="Test Goblin", source="ZZ")],
            )
        )
        state.npcs.create(Npc(scope=scope, name="Gob", statblock="Test Goblin|ZZ"))
        state.treasures.create(
            Treasure(
                scope=scope, name="Hoard", items=[TreasureItem(name="Test Sword", source="ZZ")]
            )
        )
        assert not found(run(old), Kind.LIBRARY_REFERENCE)
        report = run(_rewrite(tmp_path, spells=[BOLT, GONE], monsters=False))
        assert sorted(found(report, Kind.LIBRARY_REFERENCE)) == [
            "NPC: Gob — Test Goblin|ZZ",
            "encounter: Fight — Test Goblin|ZZ",
            "treasure: Hoard — Test Sword|ZZ",
        ]

    def test_a_character_names_its_subclass_short_and_free_gear_is_no_entry(
        self, old: StoreRegistry
    ) -> None:
        pc = PlayerCharacter.model_validate(
            {
                "name": "Kim",
                "classes": [{"class": "Tester|ZZ", "level": 1}],
                "inventory": [{"item": "A lucky stone"}, {"item": "Lost Ring|ZZ"}],
            }
        )
        old.gamestate.characters.create(pc)
        assert found(run(old), Kind.LIBRARY_REFERENCE) == ["character: Kim — Lost Ring|ZZ"]

    def test_links_in_a_chapter_and_nothing_about_the_writing(
        self, old: StoreRegistry, world: World
    ) -> None:
        adventure = old.gamestate.adventures.create(Adventure(world_id=world.id, name="Abenteuer"))
        old.gamestate.chapters.create(
            Chapter(
                world_id=world.id,
                adventure_id=adventure.id,
                title="Eins",
                body="{@spell Test Bolt}\n\n{@npc Nobody}\n\n{@spell Test Blot}\n\n::: unclosed",
            )
        )
        lines = found(run(old), Kind.LIBRARY_LINK)
        assert len(lines) == 2
        assert lines[0].startswith("chapter: Eins — line 3: {@npc Nobody}")
        assert "{@spell Test Blot} → {@spell Test Bolt}" in lines[1]


class TestChanges:
    def test_the_next_check_says_what_moved(self, old: StoreRegistry, tmp_path: Path) -> None:
        before = run(old)
        after = run(_rewrite(tmp_path, spells=[BOLT]))
        changes = compare(before, after)
        assert changes.library == {"translation_lost": (0, 1), "glossary_lost": (0, 1)}
        assert not changes.empty
        assert after.changes is not None and after.changes.library == changes.library

    def test_a_report_without_a_library_compares_nothing(self, old: StoreRegistry) -> None:
        report = run(old)
        older = report.model_copy(update={"library": False})
        assert compare(older, report).library == {}


@pytest.fixture
async def client(old: StoreRegistry) -> AsyncIterator[AsyncClient]:
    old.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = old
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac


class TestPage:
    async def test_a_finding_links_to_where_it_is_mended(
        self, client: AsyncClient, old: StoreRegistry
    ) -> None:
        old.translations.save(
            "de",
            "spell",
            "Gone Spell|ZZ",
            GONE,
            {"entries/0": "Siehe {@spell Nowhere|ZZ}."},
            by="x",
        )
        run(old)
        page = await client.get("/gm/corpus")
        assert "Dead links in translations" in page.text
        assert 'href="/gm/translate/ref/spell/ZZ/Gone%20Spell' in page.text
        assert "Your work out of step" in page.text


class TestMove:
    def _renamed(self, tmp_path: Path) -> StoreRegistry:
        return _rewrite(tmp_path, spells=[{**BOLT, "name": "Test Bolts"}, GONE])

    def test_a_lost_translation_is_listed_with_its_likely_entry(
        self, tmp_path: Path, old: StoreRegistry
    ) -> None:
        coverage = self._renamed(tmp_path).translation_coverage
        assert [(x.key, x.passages, x.near) for x in coverage.lost("de")] == [
            ("Test Bolt|ZZ", 2, "Test Bolts|ZZ")
        ]

    def test_moving_keeps_the_passages_and_flags_what_changed(
        self, tmp_path: Path, old: StoreRegistry
    ) -> None:
        before = old.translations.record("de", "spell", "Test Bolt|ZZ")
        changed = {**BOLT, "name": "Test Bolts", "entries": [BOLT["entries"][0], "New."]}  # type: ignore[index]
        registry = _rewrite(tmp_path, spells=[changed, GONE])
        registry.translation_coverage.move("de", "spell", "Test Bolt|ZZ", "test bolts|zz")
        assert registry.translations.record("de", "spell", "Test Bolt|ZZ") is None
        assert registry.translations.record("de", "spell", "Test Bolts|ZZ") == before
        assert not registry.translation_coverage.lost("de")
        problem = registry.translation_coverage.problems("de")[0]
        assert (problem.target.key, problem.stale) == ("Test Bolts|ZZ", 1)

    def test_an_unknown_entry_is_refused(self, tmp_path: Path, old: StoreRegistry) -> None:
        registry = self._renamed(tmp_path)
        with pytest.raises(Translatable, match="There is no entry"):
            registry.translation_coverage.move("de", "spell", "Test Bolt|ZZ", "No Such|ZZ")
        assert registry.translations.record("de", "spell", "Test Bolt|ZZ") is not None

    def test_a_record_with_its_entry_stays(self, old: StoreRegistry) -> None:
        with pytest.raises(Translatable, match="no longer waiting"):
            old.translation_coverage.move("de", "spell", "Test Bolt|ZZ", "Gone Spell|ZZ")

    def test_a_translation_on_the_new_entry_is_replaced_only_when_told(
        self, tmp_path: Path, old: StoreRegistry
    ) -> None:
        registry = _rewrite(tmp_path, spells=[BOLT])
        with pytest.raises(MoveTaken):
            registry.translation_coverage.move("de", "spell", "Gone Spell|ZZ", "Test Bolt|ZZ")
        assert len(registry.translations.record("de", "spell", "Test Bolt|ZZ").passages) == 2  # type: ignore[union-attr]
        registry.translation_coverage.move(
            "de", "spell", "Gone Spell|ZZ", "Test Bolt|ZZ", replace=True
        )
        kept = registry.translations.record("de", "spell", "Test Bolt|ZZ")
        assert kept is not None and kept.passages["entries/0"].text == "Alt."

    def test_a_chapter_moves_between_books(self, old: StoreRegistry) -> None:
        store = old.translations
        store.save(
            "de",
            "book",
            "OLD|0",
            {"entries": ["x"]},
            {"entries/0": "y"},
            by="dm",
            fields=("entries",),
        )
        store.move("de", "book", "OLD|0", "NEW|2")
        assert store.record("de", "book", "OLD|0") is None
        assert store.record("de", "book", "NEW|2") is not None

    def test_a_category_that_is_no_file_is_refused(self, old: StoreRegistry) -> None:
        with pytest.raises(Translatable, match="There is no entry"):
            old.translation_coverage.move("de", "../spell", "Test Bolt|ZZ", "Gone Spell|ZZ")

    async def test_the_page_moves_it(self, tmp_path: Path, old: StoreRegistry) -> None:
        registry = self._renamed(tmp_path)
        registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post("/login", data={"username": "dm", "password": PASSWORD})
            page = await ac.get("/gm/translations?lang=de")
            assert 'id="lost"' in page.text and 'value="Test Bolts|ZZ"' in page.text
            answer = await ac.post(
                "/gm/translations/move",
                data={
                    "lang": "de",
                    "category": "spell",
                    "key": "Test Bolt|ZZ",
                    "to": "Test Bolts|ZZ",
                },
            )
            assert "done=translations.moved" in answer.headers["location"]
            assert 'id="lost"' not in (await ac.get("/gm/translations?lang=de")).text

    async def test_the_page_asks_before_replacing(self, tmp_path: Path, old: StoreRegistry) -> None:
        registry = _rewrite(tmp_path, spells=[BOLT])
        registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post("/login", data={"username": "dm", "password": PASSWORD})
            form = {"lang": "de", "category": "spell", "key": "Gone Spell|ZZ", "to": "Test Bolt|ZZ"}
            answer = await ac.post("/gm/translations/move", data=form)
            page = await ac.get(answer.headers["location"])
            assert 'name="replace"' in page.text and "Test Bolt|ZZ" in page.text
            assert "{entry}" not in page.text
            await ac.post("/gm/translations/move", data={**form, "replace": "1"})
            assert registry.translations.record("de", "spell", "Gone Spell|ZZ") is None
