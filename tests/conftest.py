"""Shared fixtures.

The 5eTools corpus is not shipped with the project (see documentation/TECHSTACK.md §8 — the tool is
data-neutral and the user brings the data). Tests that need it are skipped when it is
absent, so the suite passes on a clean checkout while still exercising the real data
when it is available.
"""

from __future__ import annotations

import copy
import os
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings, get_settings
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore

# Where the corpus lives: the data folder of the 5eTools release in data/ (gitignored, the
# user's copy), which the app reads too; overridable for CI or another checkout. The
# expectations in the tests are this release's: a new one means updating them.
CORPUS_ENV = "PARAVENT_TEST_CORPUS"
DEFAULT_CORPUS = Path(__file__).resolve().parent.parent / "data" / "5etools-v2.39.0" / "data"


def corpus_path() -> Path | None:
    """Return the 5eTools corpus directory, or None when it is not available."""
    candidate = Path(os.environ.get(CORPUS_ENV, DEFAULT_CORPUS))
    return candidate if (candidate / "bestiary").is_dir() else None


requires_corpus = pytest.mark.skipif(
    corpus_path() is None,
    reason=f"5eTools corpus not found; set {CORPUS_ENV} to enable",
)


# No corpus snapshot in the repository's cache from a test (tests/test_snapshot.py asks for
# one in its own folder). Set on import, not only per test: a module-scoped fixture builds
# its registry before any per-test fixture runs.
SNAPSHOT_ENV = "PARAVENT_REFERENCE_SNAPSHOT"
os.environ[SNAPSHOT_ENV] = "false"


@pytest.fixture(autouse=True)
def isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ``.env`` out of the tests.

    ``Settings`` reads ``.env`` and ``PARAVENT_``-prefixed variables by design, so
    without this a test asserting on a default would instead see whatever corpus this
    machine happens to point at — passing here and failing on a clean checkout, or the
    reverse. Tests that want a path set it explicitly.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for key in list(os.environ):
        if key.startswith("PARAVENT_") and key != CORPUS_ENV:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(SNAPSHOT_ENV, "false")
    get_settings.cache_clear()


# -- one corpus load per test run ----------------------------------------------------------
# Most route tests build a StoreRegistry over the real corpus, and each load reads and
# validates ~114 MB of JSON (~3.5 s). The corpus never changes during a run, so the first
# load is kept and every later one over the same roots takes its result: the entry tables
# are shared (a load only ever replaces them, never edits them), while the parts a load
# changes in place — the per-category source sets — and the name index are copied per
# store. A test whose homebrew folder holds files loads in full, so homebrew stays
# private to its test.

_LOADED: dict[tuple[Any, ...], Any] = {}


def _corpus_roots(roots: tuple[Path, ...]) -> tuple[str, ...] | None:
    """The roots that hold data, when only the reference corpus does; else None."""
    with_data = [r for r in roots if r.is_dir() and any(r.rglob("*.json"))]
    corpus = corpus_path()
    if corpus is None or [Path(r).resolve() for r in with_data] != [corpus.resolve()]:
        return None
    return (str(corpus.resolve()),)


@pytest.fixture(autouse=True, scope="session")
def share_corpus_loads() -> Iterator[None]:
    """Load the corpus once per run for every store that reads it (see above)."""
    from app.store.fluff import FluffStore
    from app.store.lookups import LookupTables

    original_compendium = CompendiumStore.load
    original_fluff = FluffStore.load
    original_lookups = LookupTables.load

    def compendium_load(self: CompendiumStore, *roots: Path) -> Any:
        key = _corpus_roots(roots)
        if key is None:
            return original_compendium(self, *roots)
        cache_key = ("compendium", self._strict, key)
        loaded = _LOADED.get(cache_key)
        if loaded is None:
            loaded = CompendiumStore(strict=self._strict)
            original_compendium(loaded, *roots)
            # Built once from the same entries, which every copy shares anyway.
            loaded.projections  # noqa: B018 - builds and keeps them
            _LOADED[cache_key] = loaded
        self._entries = loaded._entries
        self._sources = defaultdict(set, {k: set(v) for k, v in loaded._sources.items()})
        self._index = copy.copy(loaded._index)
        # A copy: a reload still yields projections of its own, as it does in the app.
        self._projections = copy.copy(loaded._projections)
        self._dumps = loaded._dumps  # plain copies of the same entries, filled on use
        self.report = loaded.report
        return loaded.report

    def fluff_load(self: FluffStore, *roots: Path) -> Any:
        key = _corpus_roots(roots)
        if key is None:
            return original_fluff(self, *roots)
        loaded = _LOADED.get(("fluff", key))
        if loaded is None:
            loaded = FluffStore(strict=getattr(self, "_strict", False))
            original_fluff(loaded, *roots)
            _LOADED[("fluff", key)] = loaded
        self.__dict__.update(copy.copy(loaded.__dict__))
        return loaded.report

    def lookups_load(self: LookupTables, *roots: Path) -> None:
        key = _corpus_roots(roots)
        if key is None:
            original_lookups(self, *roots)
            return
        loaded = _LOADED.get(("lookups", key))
        if loaded is None:
            loaded = LookupTables()
            original_lookups(loaded, *roots)
            _LOADED[("lookups", key)] = loaded
        self.__dict__.update(copy.copy(loaded.__dict__))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(CompendiumStore, "load", compendium_load)
        patch.setattr(FluffStore, "load", fluff_load)
        patch.setattr(LookupTables, "load", lookups_load)
        yield


# The cost tests hash passwords with. The app's bcrypt default (12) takes about a quarter
# of a second per hash and again per check, and nearly every page test creates a user and
# logs in: hashing was most of the suite's time. Any cost verifies the same way.
TEST_BCRYPT_ROUNDS = 4


@pytest.fixture(autouse=True, scope="session")
def cheap_password_hashes() -> Iterator[None]:
    """Hash test passwords at the lowest bcrypt cost; the code paths are unchanged."""
    import bcrypt

    from app.services import auth

    original = bcrypt.gensalt

    def gensalt(rounds: int = TEST_BCRYPT_ROUNDS, prefix: bytes = b"2b") -> bytes:
        return original(min(rounds, TEST_BCRYPT_ROUNDS), prefix)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bcrypt, "gensalt", gensalt)
        # The hash an unknown name is checked against, so it costs what a real one does.
        patch.setattr(
            auth, "_DUMMY_HASH", bcrypt.hashpw(b"paravent-timing-equaliser", gensalt()).decode()
        )
        yield


@pytest.fixture(autouse=True)
def mcp_server_on_first_use(request: pytest.FixtureRequest) -> Iterator[None]:
    """An app built in a test builds its MCP server only when the test first reaches for
    it (``app.state.mcp``): registering its forty tools takes a quarter of a second per
    app, and most tests never touch it. A test marked ``mcp_server`` talks to ``/mcp``
    over HTTP and gets it built with the app, as the app always does."""
    if request.node.get_closest_marker("mcp_server") is not None:
        yield
        return
    import app.main

    mount = app.main.mount_mcp

    def mount_on_first_use(built: Any) -> None:
        class OnFirstUse:
            def __getattr__(self, name: str) -> Any:
                mount(built)  # builds it, adds /mcp, and puts the server here
                return getattr(built.state.mcp, name)

        built.state.mcp = OnFirstUse()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app.main, "mount_mcp", mount_on_first_use)
        yield


@pytest.fixture(scope="session")
def corpus() -> Path:
    """The corpus directory, skipping the test when it is unavailable."""
    path = corpus_path()
    if path is None:
        pytest.skip("5eTools corpus not available")
    return path


@pytest.fixture(scope="session")
def loaded_compendium(corpus: Path) -> CompendiumStore:
    """The full corpus, loaded once for the whole session.

    Session-scoped because loading takes about a second; per-test loading would
    dominate the suite runtime for no benefit.
    """
    store = CompendiumStore()
    store.load(corpus)
    return store


@pytest.fixture
def gamestate(tmp_path: Path) -> GameStateStore:
    """An empty game-state store in a temporary directory."""
    return GameStateStore(tmp_path / "gamestate")


def log_about(registry: Any, character_id: str) -> list[Any]:
    """The log's entries about a character, newest first, in every log (a fight's lines
    are kept in the fight's world, a character without a world rolls into the global
    log)."""
    store = registry.protocol.store
    ref = f"character:{character_id}"
    found = [e for key in store.logs() for e in store.entries(key) if e.subject == ref]
    return sorted(found, key=lambda e: e.at, reverse=True)
