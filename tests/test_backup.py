"""Backups of the library (app/services/backup.py): only the user's data, on a schedule."""

from __future__ import annotations

import os
import stat
import zipfile
from datetime import datetime, time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import cli
from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.backup import (
    MANIFEST,
    BackupError,
    BackupService,
    next_run,
    parse_times,
)
from app.store.jsonfile import write_json
from app.store.registry import ReferenceRootViolation, StoreRegistry

PASSWORD = "correct horse battery"


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    write_json(root / "users.json", {"u1": {"username": "dm"}})
    (root / "characters").mkdir()
    (root / "characters" / "bryn.json").write_text('{"name": "Bryn"}', encoding="utf-8")
    (root / "media").mkdir()
    (root / "media" / "map.png").write_bytes(b"\x89PNG")
    (root / ".secret_key").write_text("k" * 64, encoding="utf-8")
    (root / ".combats.json.tmp").write_text("half-written", encoding="utf-8")
    (root / ".DS_Store").write_bytes(b"x")
    return root


def _names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


class TestSchedule:
    def test_times(self) -> None:
        assert parse_times("15:30, 03:00,") == (time(3, 0), time(15, 30))
        assert parse_times("") == ()
        for bad in ("25:00", "3 Uhr", "03:60"):
            with pytest.raises(BackupError):
                parse_times(bad)

    def test_next_run_today_or_tomorrow(self) -> None:
        times = (time(3, 0), time(15, 30))
        assert next_run(times, datetime(2026, 10, 26, 10, 0)) == datetime(2026, 10, 26, 15, 30)
        assert next_run(times, datetime(2026, 10, 26, 16, 0)) == datetime(2026, 10, 27, 3, 0)
        assert next_run((), datetime(2026, 10, 26)) is None


class TestBackup:
    def test_the_library_and_nothing_else(self, library: Path, tmp_path: Path) -> None:
        service = BackupService(library, tmp_path / "backups")
        backup = service.run(now=datetime(2026, 10, 26, 3, 0))
        assert backup is not None
        assert backup.path.name == "paravent-library-2026-10-26_030000.zip"
        assert _names(backup.path) == {
            "users.json",
            "characters/bryn.json",
            "media/map.png",
            ".secret_key",
            MANIFEST,
        }
        assert stat.S_IMODE(os.stat(backup.path).st_mode) == 0o600
        assert not list((tmp_path / "backups").glob(".*.part"))

    def test_a_scheduled_run_skips_an_unchanged_library(
        self, library: Path, tmp_path: Path
    ) -> None:
        service = BackupService(library, tmp_path / "backups")
        assert service.run(force=False, now=datetime(2026, 10, 26, 3, 0)) is not None
        assert service.run(force=False, now=datetime(2026, 10, 26, 4, 0)) is None
        (library / "characters" / "bryn.json").unlink()  # a deletion is a change too
        assert service.run(force=False, now=datetime(2026, 10, 26, 5, 0)) is not None

    def test_only_the_newest_are_kept(self, library: Path, tmp_path: Path) -> None:
        service = BackupService(library, tmp_path / "backups", keep=2)
        for hour in (1, 2, 3):
            service.run(now=datetime(2026, 10, 26, hour, 0))
        assert [b.made.hour for b in service.backups()] == [3, 2]

    def test_never_inside_the_library(self, library: Path) -> None:
        with pytest.raises(BackupError):
            BackupService(library, library / "backups")

    def test_never_inside_the_reference(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        registry = StoreRegistry(
            Settings(
                reference_dir=corpus,
                library_dir=tmp_path / "lib",
                backup_dir=corpus / "backups",
                watch_files=False,
            )
        )
        with pytest.raises(ReferenceRootViolation):
            registry.assert_reference_readonly()


class TestCli:
    def test_run_and_list(
        self,
        library: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PARAVENT_LIBRARY_DIR", str(library))
        monkeypatch.setenv("PARAVENT_BACKUP_DIR", str(tmp_path / "backups"))
        cli.get_settings.cache_clear()
        assert cli.main(["backup", "run"]) == 0
        assert cli.main(["backup", "run", "--if-changed"]) == 0
        assert cli.main(["backup", "list"]) == 0
        out = capsys.readouterr().out
        assert "Wrote" in out and "Nothing changed" in out and "paravent-library-" in out

    def test_not_configured(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.get_settings.cache_clear()
        assert cli.main(["backup", "run"]) == 1
        assert "PARAVENT_BACKUP_DIR is not set" in capsys.readouterr().err


class TestScheduler:
    async def test_it_backs_up_when_due_and_skips_an_unchanged_library(
        self, library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import timedelta

        from app.services import backup as module

        service = BackupService(library, tmp_path / "backups", at="03:00")
        due = iter([timedelta(seconds=0.01), timedelta(seconds=0.01), None])

        def soon(_times: object, now: datetime) -> datetime | None:
            step = next(due)
            return None if step is None else now + step

        monkeypatch.setattr(module, "next_run", soon)
        await service.schedule()
        # Two runs fell due; the second found nothing changed.
        assert len(service.backups()) == 1


class TestPage:
    @staticmethod
    async def _client(tmp_path: Path, backup_dir: Path | None) -> tuple[AsyncClient, StoreRegistry]:
        settings = Settings(
            library_dir=tmp_path / "lib",
            backup_dir=backup_dir or Path(),
            backup_at="03:00",
            watch_files=False,
        )
        registry = StoreRegistry(settings)
        registry.load()
        registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        app = create_app()
        app.state.stores = registry
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), registry

    async def test_the_gamemaster_backs_up_now(self, tmp_path: Path) -> None:
        ac, registry = await self._client(tmp_path, tmp_path / "backups")
        async with ac:
            await ac.post("/login", data={"username": "dm", "password": PASSWORD})
            page = await ac.get("/gm/backups")
            assert page.status_code == 200
            assert "Back up now" in page.text and "Daily at 03:00" in page.text
            assert "None yet." in page.text
            made = await ac.post("/gm/backups/run")
            assert made.status_code == 303 and "made=paravent-library-" in made.headers["location"]
            page = await ac.get(made.headers["location"])
            assert "1 backup kept" in page.text and "made." in page.text
            assert len(registry.backups.backups()) == 1

    async def test_off_and_players(self, tmp_path: Path) -> None:
        ac, _registry = await self._client(tmp_path, None)
        async with ac:
            await ac.post("/login", data={"username": "dm", "password": PASSWORD})
            assert "Backups are off" in (await ac.get("/gm/backups")).text
            await ac.post("/logout")
            await ac.post("/login", data={"username": "alice", "password": PASSWORD})
            assert (await ac.get("/gm/backups")).status_code == 403
            assert (await ac.post("/gm/backups/run")).status_code == 403
