"""UI string catalogues and per-request translation (documentation/TRANSLATION.md §1).

This is the **UI strings** system only — buttons, labels, errors, navigation. Reference
text (spell names, statblock prose) is a separate overlay system, and units are a
separate per-user setting; neither lives here.

Two layers, merged per language:

* **Shipped** — ``app/i18n/<lang>.json``, authored with the code and versioned with it.
  English is complete by definition; every other shipped catalogue is tested for
  completeness against it.
* **Library** — ``library/i18n/ui/<lang>.json``, optional. Overrides a shipped string, or
  adds a language the code does not ship. It is the user's data, so it lives in the
  library and survives an update of the code.

Lookup is per key: ``catalogue[lang][key] ?? catalogue[en][key] ?? key``. A half-done
catalogue is therefore a normal state — it renders, in a mix — never an error.

A catalogue is a flat JSON object. ``_meta.name`` is the language's own name for itself
("Deutsch"), shown in the selector. A value is either a string with ``{placeholder}``
fields, or ``{"one": ..., "other": ...}`` for counts.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.services.translations import ENTRY_FIELDS, Coverage, Overlaid, TranslationStore

logger = logging.getLogger(__name__)

# Names the glossary cannot hold one by one (made magic items): ``composer(category,
# english, glossary_lookup)`` builds them from names it does hold.
Composer = Callable[[str, str, Callable[[str, str], str | None]], str | None]

SHIPPED_DIR = Path(__file__).resolve().parent.parent / "i18n"

if TYPE_CHECKING:
    from app.services.glossary import Glossary

FALLBACK = "en"
# Corpus categories whose names the UI catalogue translates already (``condition.prone``).
UI_TERMS = ("condition", "skill")
LANG_COOKIE = "paravent_lang"

# BCP 47-ish: "en", "de", "pt-BR". Anything else is not a language code and is ignored,
# which also keeps a cookie or form value from naming an arbitrary file.
_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")

Entry = str | dict[str, str]


def is_language_code(value: str | None) -> bool:
    """True when ``value`` looks like a language code."""
    return bool(value) and _CODE.match(value or "") is not None


class _Missing(dict[str, Any]):
    """Leaves an unknown ``{placeholder}`` visible rather than raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # A broken override must not take the UI down; English still renders.
        logger.warning("Ignoring unreadable UI catalogue %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring UI catalogue %s: not a JSON object", path)
        return {}
    return data


def _load_dir(directory: Path) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        return {}
    return {
        path.stem: _read(path)
        for path in sorted(directory.glob("*.json"))
        if is_language_code(path.stem)
    }


@dataclass(frozen=True)
class Language:
    """A selectable UI language."""

    code: str
    name: str


class UiCatalogue:
    """Every UI catalogue, shipped and library, merged per language."""

    def __init__(self, library_ui_dir: Path | None = None) -> None:
        """``library_ui_dir`` is ``library/i18n/ui``; None means shipped strings only."""
        self._library_dir = library_ui_dir
        self._strings: dict[str, dict[str, Entry]] = {}
        self._names: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """(Re-)read both layers from disk."""
        layers = [_load_dir(SHIPPED_DIR)]
        if self._library_dir is not None:
            layers.append(_load_dir(self._library_dir))
        merged: dict[str, dict[str, Any]] = {}
        for layer in layers:
            for code, data in layer.items():
                merged.setdefault(code, {}).update(data)
        if FALLBACK not in merged:
            raise RuntimeError(f"The {FALLBACK} UI catalogue is missing from {SHIPPED_DIR}")
        self._names = {}
        self._strings = {}
        for code, data in merged.items():
            meta = data.pop("_meta", {})
            self._names[code] = str(meta.get("name", code)) if isinstance(meta, dict) else code
            self._strings[code] = {k: v for k, v in data.items() if isinstance(v, str | dict)}

    # -- languages ---------------------------------------------------------------

    @property
    def languages(self) -> list[Language]:
        """Selectable languages, English first, the rest by their own name."""
        others = sorted((c for c in self._names if c != FALLBACK), key=lambda c: self._names[c])
        return [Language(code, self._names[code]) for code in [FALLBACK, *others]]

    def supports(self, code: str | None) -> bool:
        """True when ``code`` has a catalogue."""
        return code is not None and code in self._strings

    def keys(self, code: str = FALLBACK) -> set[str]:
        """The keys a catalogue defines. Used by the completeness test."""
        return set(self._strings.get(code, {}))

    # -- lookup ------------------------------------------------------------------

    def entry(self, code: str, key: str) -> Entry | None:
        """The raw entry for ``key``: the language's own, else English, else None."""
        found = self._strings.get(code, {}).get(key)
        if found is None:
            found = self._strings[FALLBACK].get(key)
        return found

    def own(self, code: str, key: str) -> Entry | None:
        """The language's own entry for ``key``, without falling back to English."""
        return self._strings.get(code, {}).get(key)

    def translator(
        self,
        code: str,
        glossary: Glossary | None = None,
        translations: TranslationStore | None = None,
        composer: Composer | None = None,
    ) -> Translator:
        """A translator for ``code``, falling back to English when it is unknown.

        With a ``glossary``, :meth:`Translator.term` names corpus entries too; with
        ``translations``, :meth:`Translator.overlay` puts translated text in them; a
        ``composer`` names what the glossary cannot list (made magic items).
        """
        language = code if self.supports(code) else FALLBACK
        return Translator(self, language, glossary, translations, composer)

    def negotiate(self, *candidates: str | None, accept_language: str | None = None) -> str:
        """The first supported language among ``candidates``, then the header, then English."""
        for candidate in candidates:
            if self.supports(candidate):
                return str(candidate)
        for code in parse_accept_language(accept_language):
            if self.supports(code):
                return code
            base = code.split("-")[0]
            if self.supports(base):
                return base
        return FALLBACK


def parse_accept_language(header: str | None) -> list[str]:
    """Language codes from an ``Accept-Language`` header, highest quality first."""
    if not header:
        return []
    weighted: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        code, _, params = part.strip().partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        code = code.strip().lower()
        if is_language_code(code) and quality > 0:
            weighted.append((-quality, index, code))
    return [code for _, _, code in sorted(weighted)]


class Translator:
    """Renders UI strings in one language."""

    def __init__(
        self,
        catalogue: UiCatalogue,
        code: str,
        glossary: Glossary | None = None,
        translations: TranslationStore | None = None,
        composer: Composer | None = None,
    ) -> None:
        """Bind to a catalogue and a language it supports (the term glossary, the
        translated corpus text, and the composer of names the glossary cannot hold)."""
        self._catalogue = catalogue
        self.code = code
        self._glossary = glossary
        self._translations = translations
        self._composer = composer

    @property
    def languages(self) -> list[Language]:
        """Every language the selector should offer."""
        return self._catalogue.languages

    def __call__(self, key: str, /, **params: Any) -> str:
        """The string for ``key`` with ``params`` filled in."""
        entry = self._catalogue.entry(self.code, key)
        if entry is None:
            # Visible rather than blank: a missing key is a bug to notice, not hide.
            return key
        if isinstance(entry, dict):
            entry = entry.get("other") or next(iter(entry.values()), key)
        return entry.format_map(_Missing(params))

    def plural(self, key: str, count: int, /, **params: Any) -> str:
        """The ``one``/``other`` form for ``count``, with ``{count}`` available."""
        entry = self._catalogue.entry(self.code, key)
        if entry is None:
            return key
        if isinstance(entry, str):
            text = entry
        else:
            # English and German both distinguish exactly one from everything else.
            text = entry.get("one" if count == 1 else "other") or entry.get("other", key)
        return text.format_map(_Missing({"count": count, **params}))

    def term(self, category: str, english: str) -> str | None:
        """A corpus entry's name in this language (TRANSLATION.md §4); None if unnamed.

        English has no glossary: the corpus's own names are English.
        """
        if self.code == FALLBACK:
            return None
        found = self._glossary.term(self.code, category, english) if self._glossary else None
        if found is None and category in UI_TERMS:
            # Conditions and skills are UI strings already ("Liegend", "Heimlichkeit").
            own = self._catalogue.own(self.code, f"{category}.{english.lower()}")
            found = own if isinstance(own, str) else None
        if found is None and self._composer is not None and self._glossary is not None:
            glossary, code = self._glossary, self.code
            found = self._composer(category, english, lambda c, n: glossary.term(code, c, n))
        return found

    def terms(self) -> Callable[[str, str], str | None]:
        """:meth:`term` for scanning many entries: the glossary is read once."""
        if self.code == FALLBACK:
            return lambda category, english: None
        table = self._glossary.table(self.code) if self._glossary else {}

        def plain(category: str, english: str) -> str | None:
            hit = table.get(category, {}).get(english.lower())
            return hit[1] if hit is not None else None

        def look(category: str, english: str) -> str | None:
            found = plain(category, english)
            if found is not None:
                return found
            if category in UI_TERMS:
                return self.term(category, english)
            if self._composer is not None:
                return self._composer(category, english, plain)
            return None

        return look

    def overlay(
        self,
        category: str,
        key: str,
        data: dict[str, Any],
        fields: tuple[str, ...] | None = None,
    ) -> Overlaid:
        """A corpus record with this language's translated passages in it (TRANSLATION.md
        §5); English, or a record nobody translated, comes back as it was."""
        if self.code == FALLBACK or self._translations is None:
            return Overlaid(data, Coverage())
        return self._translations.overlay(self.code, category, key, data, fields or ENTRY_FIELDS)

    def overlay_at(self, category: str, key: str, prefix: str, value: Any) -> Overlaid:
        """One part of a record in this language (a species trait inside its species):
        ``result.data["value"]`` is ``value`` with its translated passages."""
        if self.code == FALLBACK or self._translations is None:
            return Overlaid({"value": value}, Coverage())
        return self._translations.overlay_at(self.code, category, key, prefix, value)

    def glossary_term(self, lang: str, category: str, english: str) -> str | None:
        """The glossary's own name for an entry in ``lang`` (for editing it)."""
        return self._glossary.term(lang, category, english) if self._glossary else None

    def number(self, value: int) -> str:
        """A whole number with the language's thousands separator: 85,000 or 85.000."""
        separator = self("format.thousands")
        if separator == "format.thousands":
            separator = ","
        return f"{value:,}".replace(",", separator)

    def error(self, exc: Translatable) -> str:
        """Render a translatable error."""
        return self(exc.key, **exc.params)


class Translatable(Exception):
    """An exception whose message is a catalogue key, rendered in the reader's language.

    ``str(exc)`` renders English, so logs and the CLI stay readable without a request.
    """

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key)
        self.key = key
        self.params = params

    def __str__(self) -> str:
        return english()(self.key, **self.params)


@lru_cache(maxsize=1)
def _shipped() -> UiCatalogue:
    return UiCatalogue()


def english() -> Translator:
    """An English translator over the shipped strings, for code with no request."""
    return _shipped().translator(FALLBACK)


def language_names(languages: Iterable[Language]) -> Mapping[str, str]:
    """``code → name`` for a list of languages."""
    return {lang.code: lang.name for lang in languages}


__all__ = [
    "FALLBACK",
    "LANG_COOKIE",
    "Language",
    "Translatable",
    "Translator",
    "UiCatalogue",
    "english",
    "is_language_code",
    "language_names",
    "parse_accept_language",
]
