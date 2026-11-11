"""Tests for the UI string catalogues and language negotiation."""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

import pytest

from app.config import Settings
from app.services.auth import AuthError
from app.services.i18n import (
    FALLBACK,
    SHIPPED_DIR,
    UiCatalogue,
    english,
    is_language_code,
    parse_accept_language,
)
from app.store.registry import StoreRegistry

SHIPPED = sorted(p.stem for p in SHIPPED_DIR.glob("*.json"))


def _raw(code: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((SHIPPED_DIR / f"{code}.json").read_text("utf-8"))
    data.pop("_meta")
    return data


def _fields(entry: object) -> set[str]:
    texts = entry.values() if isinstance(entry, dict) else [entry]
    return {name for text in texts for _, name, _, _ in string.Formatter().parse(str(text)) if name}


@pytest.fixture
def library_ui(tmp_path: Path) -> Path:
    path = tmp_path / "ui"
    path.mkdir()
    return path


class TestShippedCatalogues:
    @pytest.mark.parametrize("code", ["en", "de"])
    def test_no_key_twice(self, code: str) -> None:
        """JSON keeps the last of two equal keys silently: a new string named like an
        old one would quietly replace it on every screen that used it."""
        # Top-level keys only: a plural's "one" and "other" repeat by design.
        text = (SHIPPED_DIR / f"{code}.json").read_text("utf-8")
        seen = re.findall(r'^  "([^"]+)":', text, re.MULTILINE)
        assert not sorted({k for k in seen if seen.count(k) > 1})

    def test_english_and_german_ship(self) -> None:
        assert {"en", "de"} <= set(SHIPPED)

    @pytest.mark.parametrize("code", [c for c in SHIPPED if c != FALLBACK])
    def test_every_catalogue_is_complete(self, code: str) -> None:
        # A missing key would silently show English in the middle of a German page.
        assert set(_raw(code)) == set(_raw(FALLBACK))

    @pytest.mark.parametrize("code", [c for c in SHIPPED if c != FALLBACK])
    def test_placeholders_match_english(self, code: str) -> None:
        # A renamed {placeholder} renders literally instead of the value.
        english_raw, other = _raw(FALLBACK), _raw(code)
        for key, entry in english_raw.items():
            assert _fields(other[key]) == _fields(entry), key

    @pytest.mark.parametrize("code", SHIPPED)
    def test_plural_entries_have_one_and_other(self, code: str) -> None:
        for key, entry in _raw(code).items():
            if isinstance(entry, dict):
                assert set(entry) == {"one", "other"}, key

    def test_every_key_used_in_code_exists(self) -> None:
        # Catches a typo in a t("...") call, which would render the raw key.
        app_dir = SHIPPED_DIR.parent
        pattern = re.compile(r"""(?:\bt|\.t|t\.plural|AuthError)\(\s*["']([a-z_]+\.[a-z_.]+)["']""")
        used = {m for path in app_dir.rglob("*.py") for m in pattern.findall(path.read_text())}
        assert used, "the pattern found no keys at all"
        missing = used - set(_raw(FALLBACK))
        assert not missing


class TestLookup:
    def test_german(self) -> None:
        t = UiCatalogue().translator("de")
        assert t("login.submit") == "Anmelden"

    def test_parameters(self) -> None:
        t = UiCatalogue().translator("en")
        assert t("error.username_taken", username="alice") == (
            "The username alice is already taken."
        )

    def test_missing_parameter_stays_visible(self) -> None:
        assert "{username}" in UiCatalogue().translator("en")("error.username_taken")

    def test_unknown_key_renders_the_key(self) -> None:
        assert UiCatalogue().translator("de")("no.such.key") == "no.such.key"

    @pytest.mark.parametrize(
        ("count", "expected"), [(0, "0 Charaktere"), (1, "1 Charakter"), (2, "2 Charaktere")]
    )
    def test_plural(self, count: int, expected: str) -> None:
        assert UiCatalogue().translator("de").plural("players.characters", count) == expected

    def test_unknown_language_falls_back_to_english(self) -> None:
        t = UiCatalogue().translator("xx")
        assert t.code == FALLBACK
        assert t("login.submit") == "Log in"

    def test_languages_english_first(self) -> None:
        codes = [lang.code for lang in UiCatalogue().languages]
        assert codes[0] == "en"
        assert "de" in codes
        names = {lang.code: lang.name for lang in UiCatalogue().languages}
        assert names["de"] == "Deutsch"

    def test_translatable_str_is_english(self) -> None:
        assert str(AuthError("error.password_short", min=8)) == (
            "Password must be at least 8 characters."
        )
        assert english().code == "en"


class TestLibraryLayer:
    def test_override_replaces_one_string(self, library_ui: Path) -> None:
        (library_ui / "de.json").write_text(json.dumps({"login.submit": "Los geht's"}))
        t = UiCatalogue(library_ui).translator("de")
        assert t("login.submit") == "Los geht's"
        assert t("login.heading") == "Anmelden"  # untouched shipped strings remain

    def test_new_language_falls_back_per_key(self, library_ui: Path) -> None:
        (library_ui / "fr.json").write_text(
            json.dumps({"_meta": {"name": "Français"}, "login.submit": "Se connecter"})
        )
        catalogue = UiCatalogue(library_ui)
        assert "fr" in [lang.code for lang in catalogue.languages]
        t = catalogue.translator("fr")
        assert t("login.submit") == "Se connecter"
        assert t("login.heading") == "Log in"  # half-translated is a normal state

    def test_broken_file_is_ignored(self, library_ui: Path) -> None:
        (library_ui / "de.json").write_text("{ not json")
        assert UiCatalogue(library_ui).translator("de")("login.submit") == "Anmelden"

    def test_non_language_filenames_are_ignored(self, library_ui: Path) -> None:
        (library_ui / "notes.json").write_text(json.dumps({"login.submit": "x"}))
        assert "notes" not in [lang.code for lang in UiCatalogue(library_ui).languages]

    def test_reload_picks_up_changes(self, library_ui: Path) -> None:
        catalogue = UiCatalogue(library_ui)
        (library_ui / "de.json").write_text(json.dumps({"login.submit": "Rein"}))
        catalogue.load()
        assert catalogue.translator("de")("login.submit") == "Rein"

    def test_registry_reloads_on_change(self, tmp_path: Path) -> None:
        registry = StoreRegistry(Settings(library_dir=tmp_path / "lib", watch_files=False))
        registry.load()
        assert registry.ui.translator("de")("login.submit") == "Anmelden"
        ui_dir = registry.settings.i18n_dir / "ui"
        ui_dir.mkdir(parents=True)
        (ui_dir / "de.json").write_text(json.dumps({"login.submit": "Rein"}))
        registry._on_change({ui_dir / "de.json"})
        assert registry.ui.translator("de")("login.submit") == "Rein"


class TestNegotiation:
    def test_first_supported_candidate_wins(self) -> None:
        catalogue = UiCatalogue()
        assert catalogue.negotiate("xx", "de", "en") == "de"

    def test_accept_language_by_quality(self) -> None:
        catalogue = UiCatalogue()
        assert catalogue.negotiate(None, accept_language="fr;q=0.9, de;q=0.8, en;q=0.1") == "de"

    def test_region_falls_back_to_base(self) -> None:
        assert UiCatalogue().negotiate(accept_language="de-AT,de;q=0.9") == "de"

    def test_nothing_usable_is_english(self) -> None:
        assert UiCatalogue().negotiate(None, "", accept_language="ja, zh") == "en"

    def test_parse_accept_language(self) -> None:
        assert parse_accept_language("en;q=0.5, de, fr;q=0") == ["de", "en"]
        assert parse_accept_language(None) == []
        assert parse_accept_language("*, de;q=bad") == []

    @pytest.mark.parametrize(
        ("value", "ok"),
        [("en", True), ("de", True), ("pt-BR", True), ("", False), ("../x", False), ("EN", False)],
    )
    def test_language_code(self, value: str, ok: bool) -> None:
        assert is_language_code(value) is ok
