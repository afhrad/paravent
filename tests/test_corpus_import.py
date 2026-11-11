"""Importing a translated 5eTools corpus (app/services/corpus_import.py, TRANSLATION.md §7)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.corpus_import import CorpusImport, corpus_root, same_links
from app.services.glossary import Glossary
from app.services.translations import ENTRY_FIELDS, TranslationStore, passages
from app.store.compendium import CompendiumStore
from app.store.fluff import FluffStore
from app.store.published import PublishedAdventures, PublishedBooks


def _german(data: dict[str, Any], fields: tuple[str, ...] = ENTRY_FIELDS) -> dict[str, Any]:
    """``data`` with every passage marked as translated, its tags untouched."""
    from app.services.translations import _set

    out = json.loads(json.dumps(data))
    for path, text in passages(data, fields):
        out = _set(out, path, f"DE {text}")
    return out


def _write(root: Path, relative: str, content: dict[str, Any]) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(content), encoding="utf-8")


@pytest.fixture(scope="module")
def fluff(corpus: Path) -> FluffStore:
    store = FluffStore()
    store.load(corpus)
    return store


def _importer(
    tmp_path: Path, compendium: CompendiumStore, fluff: FluffStore, corpus: Path
) -> tuple[CorpusImport, TranslationStore, Glossary]:
    store, glossary = TranslationStore(tmp_path / "i18n"), Glossary(tmp_path / "i18n")
    return (
        CorpusImport(
            compendium=compendium,
            fluff=fluff,
            books=PublishedBooks(corpus),
            adventures=PublishedAdventures(corpus),
            store=store,
            glossary=glossary,
        ),
        store,
        glossary,
    )


class TestLinks:
    def test_display_text_may_differ_the_target_may_not(self) -> None:
        assert same_links("a {@spell fireball} b", "x {@spell fireball||Feuerball} y")
        assert same_links("{@damage 8d6} fire", "{@damage 8d6} Feuer")
        assert not same_links("{@spell fireball}", "{@spell feuerball}")
        assert not same_links("{@condition prone}", "liegend")  # a lost link
        # Formatting tags hold text a translator translates.
        assert same_links("{@b Hit:} {@damage 1d6}", "{@b Treffer:} {@damage 1d6}")
        assert same_links("{@deity Bahamut|Nonhuman|PHB}", "{@deity Bahamut|Nonhuman|PHB|Bahamut}")

    def test_a_release_folder_reads_its_data(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "books.json").write_text("{}", encoding="utf-8")
        assert corpus_root(tmp_path) == tmp_path / "data"
        assert corpus_root(tmp_path / "data") == tmp_path / "data"


class TestImport:
    def test_entries_descriptions_and_names_become_drafts(
        self, tmp_path: Path, loaded_compendium: CompendiumStore, fluff: FluffStore, corpus: Path
    ) -> None:
        fireball = loaded_compendium.find("spell", "Fireball", "PHB")
        assert fireball is not None
        english = fireball.model_dump(by_alias=True, exclude_none=True)
        german = {**_german(english), "name": "Feuerball", "ENG_name": "Fireball"}
        # A passage whose link now points elsewhere stays English.
        first = german["entries"][0]
        german["entries"][0] = first.replace("{@", "{@unknownthing ", 1) if "{@" in first else first
        goblin = fluff.get("monster", "Goblin", "MM")
        assert goblin is not None
        goblin_fluff = goblin.model_dump(by_alias=True, exclude_none=True)
        source = tmp_path / "corpus-de"
        _write(
            source,
            "spells/spells-phb.json",
            {"spell": [german, {"name": "Unbekannt", "ENG_name": "Nothing", "source": "PHB"}]},
        )
        _write(source, "bestiary/fluff-bestiary-mm.json", {"monsterFluff": [_german(goblin_fluff)]})
        importer, store, glossary = _importer(tmp_path, loaded_compendium, fluff, corpus)

        dry = importer.run(source, "de", dry_run=True)
        assert dry.records["spell"] == 1 and dry.unmatched["spell"] == 1
        assert store.record("de", "spell", "Fireball|PHB") is None  # nothing written
        assert glossary.term("de", "spell", "Fireball") is None

        report = importer.run(source, "de")
        record = store.record("de", "spell", "Fireball|PHB")
        assert record is not None
        english_passages = dict(passages(english))
        translated = record.passages
        assert all(p.draft and p.by == "import" for p in translated.values())
        assert all(translated[path].text == f"DE {english_passages[path]}" for path in translated)
        if "{@" in first:
            assert "entries/0" not in translated and report.links["spell"] >= 1
        assert glossary.term("de", "spell", "Fireball") == "Feuerball"
        assert report.records["monsterFluff"] == 1
        assert store.record("de", "monsterFluff", "Goblin|MM") is not None

    def test_the_tables_own_translation_is_kept_unless_replaced(
        self, tmp_path: Path, loaded_compendium: CompendiumStore, fluff: FluffStore, corpus: Path
    ) -> None:
        fireball = loaded_compendium.find("spell", "Fireball", "PHB")
        assert fireball is not None
        english = fireball.model_dump(by_alias=True, exclude_none=True)
        path, _text = passages(english)[-1]
        importer, store, glossary = _importer(tmp_path, loaded_compendium, fluff, corpus)
        store.save("de", "spell", "Fireball|PHB", english, {path: "Unser eigener Text"}, by="dm")
        glossary.set("de", "spell", "Fireball", "Feuerkugel")
        source = tmp_path / "corpus-de"
        _write(
            source,
            "spells/spells-phb.json",
            {"spell": [{**_german(english), "name": "Feuerball", "ENG_name": "Fireball"}]},
        )

        report = importer.run(source, "de", reviewed=True)
        record = store.record("de", "spell", "Fireball|PHB")
        assert record is not None and record.passages[path].text == "Unser eigener Text"
        assert (
            report.kept["spell"] == 1 and glossary.term("de", "spell", "Fireball") == "Feuerkugel"
        )
        assert not any(p.draft for p in record.passages.values())  # --reviewed

        importer.run(source, "de", replace=True)
        record = store.record("de", "spell", "Fireball|PHB")
        assert record is not None and record.passages[path].text.startswith("DE ")
        assert glossary.term("de", "spell", "Fireball") == "Feuerball"

    def test_a_book_is_read_only_when_its_chapters_line_up(
        self, tmp_path: Path, loaded_compendium: CompendiumStore, fluff: FluffStore, corpus: Path
    ) -> None:
        books = PublishedBooks(corpus)
        body = books.body("PHB")
        assert body is not None and len(body.sections) > 1
        source = tmp_path / "corpus-de"
        importer, store, _glossary = _importer(tmp_path, loaded_compendium, fluff, corpus)
        _write(source, "book/book-phb.json", {"data": [{"type": "section", "name": "Nur eins"}]})
        assert "PHB" in importer.run(source, "de", dry_run=True).misaligned

        from app.services.translations import CHAPTER_FIELDS

        chapters = [_german(s.entry, CHAPTER_FIELDS) for s in body.sections]
        _write(source, "book/book-phb.json", {"data": chapters})
        report = importer.run(source, "de")
        assert report.records["book"] >= 1 and not report.misaligned
        first = store.record("de", "book", "PHB|0")
        assert first is not None and first.passages["name"].text.startswith("DE ")


class TestFromTheApp:
    @pytest.fixture
    async def client(self, tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, Any]]:
        from httpx import ASGITransport

        from app.config import Settings
        from app.main import create_app
        from app.models.gamestate import Role
        from app.store.registry import StoreRegistry

        registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
        )
        registry.load()
        registry.auth.create_user("dm", "correct horse battery", Role.GAMEMASTER)
        app = create_app()
        app.state.stores = registry
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.post("/login", data={"username": "dm", "password": "correct horse battery"})
            yield ac, registry

    async def test_a_folder_is_checked_then_imported_in_the_background(
        self, tmp_path: Path, client: tuple[AsyncClient, Any], corpus: Path
    ) -> None:
        ac, registry = client
        page = (await ac.get("/gm/translations", params={"lang": "de"})).text
        assert "Import a translated corpus" in page and 'action="/gm/translations/import"' in page

        (tmp_path / "empty").mkdir()
        # Folders it refuses, with what was typed.
        for folder, error in (
            ("relative/path", "translations.import.error.folder"),
            (str(tmp_path / "empty"), "translations.import.error.corpus"),
            (str(corpus), "translations.import.error.own"),
            (str(registry.settings.library_dir), "translations.import.error.own"),
        ):
            (registry.settings.library_dir).mkdir(parents=True, exist_ok=True)
            answer = await ac.post(
                "/gm/translations/import", data={"lang": "de", "folder": folder, "mode": "check"}
            )
            assert f"error={error}" in answer.headers["location"], folder

        fireball = registry.compendium.find("spell", "Fireball", "PHB")
        english = fireball.model_dump(by_alias=True, exclude_none=True)
        source = tmp_path / "corpus-de"
        _write(
            source,
            "spells/spells-phb.json",
            {"spell": [{**_german(english), "name": "Feuerball", "ENG_name": "Fireball"}]},
        )
        answer = await ac.post(
            "/gm/translations/import", data={"lang": "de", "folder": str(source), "mode": "check"}
        )
        assert "done=translations.import.started" in answer.headers["location"]
        imports = registry.corpus_imports
        await asyncio.gather(*imports._tasks)
        run = imports.latest("de")
        assert run is not None and run.dry_run and run.report.records["spell"] == 1
        assert registry.translations.record("de", "spell", "Fireball|PHB") is None
        panel = (await ac.get("/gm/translations/import", params={"lang": "de"})).text
        assert "nothing written yet" in panel and "Import now" in panel and "every 3s" not in panel

        await ac.post(
            "/gm/translations/import", data={"lang": "de", "folder": str(source), "mode": "import"}
        )
        await asyncio.gather(*imports._tasks)
        record = registry.translations.record("de", "spell", "Fireball|PHB")
        assert record is not None and all(p.draft for p in record.passages.values())
        panel = (await ac.get("/gm/translations/import", params={"lang": "de"})).text
        assert "New passages wait as drafts" in panel and "Import now" not in panel
