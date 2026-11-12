"""The corpus check (app/services/corpus_check.py): a small corpus with known faults, and
the real one, which must come out clean."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app import cli
from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.corpus_check import (
    REPORT_FILE,
    CorpusCheck,
    CorpusHealth,
    CorpusReport,
    Kind,
    compare,
    corpus_version,
    load_report,
    save_report,
)
from app.store.compendium import CompendiumStore
from app.store.fluff import FluffStore
from app.store.lookups import LookupTables
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"

SPELL = {"name": "Test Bolt", "source": "ZZ", "level": 1, "school": "V"}


def write(root: Path, relative: str, data: Any) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def check(root: Path, *, references: bool = False) -> CorpusReport:
    compendium, fluff, lookups = CompendiumStore(), FluffStore(), LookupTables()
    compendium.load(root)
    fluff.load(root)
    lookups.load(root)
    return CorpusCheck(root, None, compendium, fluff, lookups).run(references=references)


def kinds(report: CorpusReport) -> dict[Kind, int]:
    return {g.kind: g.count for g in report.groups}


class TestVersion:
    def test_the_newest_changelog_entry(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "changelog.json",
            [
                {"ver": "1.0.0", "date": "2016-12-18", "txt": ""},
                {"ver": "2.29.0", "date": "2026-05-30", "txt": ""},
                {"ver": "2.28.1", "date": "2026-04-02", "txt": ""},
            ],
        )
        version = corpus_version(tmp_path)
        assert version is not None
        assert (version.version, version.date) == ("2.29.0", "2026-05-30")

    def test_none_without_a_changelog(self, tmp_path: Path) -> None:
        assert corpus_version(tmp_path) is None
        assert corpus_version(None) is None
        write(tmp_path, "changelog.json", "{broken")
        assert corpus_version(tmp_path) is None


class TestFaults:
    @pytest.fixture
    def corpus(self, tmp_path: Path) -> Path:
        root = tmp_path / "corpus"
        write(
            root,
            "spells/spells-zz.json",
            {
                "spell": [
                    SPELL,
                    {"name": "Broken", "source": "ZZ", "level": "high", "school": "V"},
                    {"name": "Shell", "source": "ZZ", "_copy": {"name": "Nowhere", "source": "ZZ"}},
                    SPELL,
                    "not a row",
                ],
                "spellNovelty": [{"name": "x"}],
            },
        )
        write(root, "items.json", "{not json")
        write(root, "legendarygroups-extra.json", {"legendaryGroupTemplate": [{"name": "Boss"}]})
        write(root, "spells/index.json", {"ZZ": "spells-zz.json"})
        return root

    def test_each_fault_is_found_where_it_is(self, corpus: Path) -> None:
        report = check(corpus)
        found = kinds(report)
        assert found[Kind.UNREADABLE] == 1
        # The shell too: spells take no _copy, so it is a spell without a level.
        assert found[Kind.INVALID] == 2
        assert found[Kind.BAD_ROW] == 1
        assert found[Kind.UNKNOWN_KEY] == 1
        assert found[Kind.DUPLICATE_KEY] == 1
        assert Kind.COUNT_MISMATCH not in found
        invalid = next(g for g in report.groups if g.kind is Kind.INVALID).items[0]
        assert invalid.file == "spells/spells-zz.json"
        assert "Broken" in invalid.subject and invalid.detail.startswith("level:")
        assert not report.ok and report.errors >= 2

    def test_counts_add_up(self, corpus: Path) -> None:
        spells = next(c for c in check(corpus).counts if c.category == "spell")
        assert (spells.rows, spells.invalid, spells.repeated, spells.loaded) == (4, 2, 1, 1)
        assert spells.matches

    def test_keys_planned_unknown_and_files_not_read(self, corpus: Path) -> None:
        report = check(corpus)
        status = {k.key: k.status for k in report.keys}
        assert status["spell"] == "loaded"
        assert status["legendaryGroupTemplate"] == "planned"
        assert status["spellNovelty"] == "unknown"
        unread = {u.file: u.reason for u in report.unread}
        assert unread == {
            "legendarygroups-extra.json": "planned",
            "spells/index.json": "site_index",
        }

    def test_an_entry_lost_without_a_report_is_an_error(self, corpus: Path) -> None:
        compendium, fluff, lookups = CompendiumStore(), FluffStore(), LookupTables()
        compendium.load(corpus)
        fluff.load(corpus)
        compendium._entries["spell"].clear()  # what a silent loader bug looks like
        report = CorpusCheck(corpus, None, compendium, fluff, lookups).run()
        mismatch = next(g for g in report.groups if g.kind is Kind.COUNT_MISMATCH)
        assert mismatch.items[0].subject == "spell"


class TestChanges:
    def test_new_keys_rows_and_fields_since_the_last_report(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        write(root, "spells/spells-zz.json", {"spell": [SPELL]})
        before = check(root)
        write(
            root,
            "spells/spells-zz.json",
            {"spell": [SPELL, {**SPELL, "name": "New", "glow": True}], "spellNovelty": []},
        )
        changes = compare(before, check(root))
        assert changes.new_keys == ["spellNovelty"]
        assert changes.rows["spell"] == (1, 2)
        assert changes.new_fields == {"spell": ["glow"]}

    def test_a_report_round_trips(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        write(root, "spells/spells-zz.json", {"spell": [SPELL, {"name": "B", "source": "ZZ"}]})
        report = check(root)
        save_report(tmp_path / REPORT_FILE, report)
        loaded = load_report(tmp_path / REPORT_FILE)
        assert loaded == report
        assert load_report(tmp_path / "absent.json") is None


class TestRealCorpus:
    def test_the_corpus_reads_clean(self, corpus: Path) -> None:
        report = check(corpus, references=True)
        assert report.ok, [g.kind for g in report.groups if g.count and g.severity == "error"]
        found = kinds(report)
        assert Kind.UNKNOWN_KEY not in found
        assert Kind.UNKNOWN_COPY_MODE not in found
        assert Kind.MISSING_TEMPLATE not in found
        assert Kind.UNKNOWN_TAG not in found
        assert all(c.matches for c in report.counts)
        assert report.version is not None

    def test_templates_apply(self, loaded_compendium: CompendiumStore) -> None:
        skeleton = loaded_compendium.get("monster", "cat skeleton|cos")
        assert skeleton.type == "undead" or getattr(skeleton.type, "type", None) == "undead"
        weak = loaded_compendium.get("monster", "reduced-threat aboleth|tftyp").model_dump(
            by_alias=True
        )
        assert weak["hp"]["average"] == 67
        assert weak["cr"]["xp"] == 2950


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(
            reference_dir=corpus,
            library_dir=tmp_path / "lib",
            reference_cache_dir=tmp_path / "cache",
            watch_files=False,
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, registry


async def _login(ac: AsyncClient, user: str) -> None:
    await ac.post("/login", data={"username": user, "password": PASSWORD})


class TestPage:
    async def test_the_gamemaster_sees_the_version_and_runs_a_check(
        self, client: tuple[AsyncClient, StoreRegistry], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        page = await ac.get("/gm/corpus")
        assert page.status_code == 200
        version = corpus_version(registry.settings.reference_dir)
        assert version is not None and version.version in page.text
        assert "Not checked yet" in page.text

        health: CorpusHealth = registry.corpus_health
        # Run in this thread, so the test sees the report when the POST returns.
        monkeypatch.setattr(
            health, "start", lambda *, references: bool(health.run(references=references))
        )
        response = await ac.post("/gm/corpus/check", data={})
        assert response.status_code == 303
        page = await ac.get("/gm/corpus")
        assert "Everything is readable" in page.text
        assert "Counts per category" in page.text
        assert (registry.settings.reference_cache_dir / REPORT_FILE).is_file()

        status = await ac.get("/gm/corpus/status")
        assert status.headers.get("HX-Refresh") == "true"

    async def test_players_may_not(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _registry = client
        await _login(ac, "alice")
        assert (await ac.get("/gm/corpus")).status_code == 403
        assert (await ac.post("/gm/corpus/check")).status_code == 403


class TestCli:
    def test_check_prints_and_fails_on_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        root = tmp_path / "corpus"
        write(root, "changelog.json", [{"ver": "9.9.9", "date": "2030-01-01"}])
        write(root, "spells/spells-zz.json", {"spell": [SPELL]})
        monkeypatch.setenv("PARAVENT_REFERENCE_DIR", str(root))
        monkeypatch.setenv("PARAVENT_LIBRARY_DIR", str(tmp_path / "lib"))
        monkeypatch.setenv("PARAVENT_REFERENCE_CACHE_DIR", str(tmp_path / "cache"))
        cli.get_settings.cache_clear()
        assert cli.main(["corpus", "check"]) == 0
        out = capsys.readouterr().out
        assert "5eTools 9.9.9 (2030-01-01)" in out and "\nOK, " in out
        assert "WARNING missing_file" in out  # a corpus this small lacks loot.json and more

        write(root, "spells/spells-zz.json", {"spell": [{**SPELL, "level": "x"}]})
        assert cli.main(["corpus", "check"]) == 1
        out = capsys.readouterr().out
        assert "ERROR invalid (1)" in out and "spells/spells-zz.json" in out
        assert "Since the check of" in out and "spell: 1 → 1 rows" not in out
