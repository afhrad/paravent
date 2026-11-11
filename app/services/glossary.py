"""The term glossary: localized names for corpus entries (documentation/TRANSLATION.md §4).

One file per language, ``library/i18n/<lang>/_glossary.json``::

    {"spell": {"Fireball": "Feuerball"}, "monster": {"Owlbear": "Eulenbär"}}

keyed by the compendium category and the entry's **English** name. A glossary name is a
label only: references still resolve in English (§3), so a German name never changes
which entry a link means. Paravent ships no glossary — like the corpus, the names are
the user's: filled in on the reference pages, or by dropping in a file in this format.

The file is re-read when it changes on disk, so an import or a hand edit shows at once.

**``"-"`` marks a name still to give.** ``fill`` lists every entry of the corpus with
``"-"``, so the file holds the whole list to translate by hand; a cleared name goes
back to ``"-"`` rather than leaving the file. Anything without a letter or digit is a
placeholder and reads as missing everywhere: labels show English, search ignores it,
and the glossary page offers the entry as unnamed.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.store.jsonfile import read_json, write_json

GLOSSARY_FILE = "_glossary.json"
MAX_TERM = 120
# What a name not given yet is written as.
PLACEHOLDER = "-"
# A name needs a letter or digit: lists mark "no translation" with "-" or "—", and a
# name like that would replace every mention of the entry with a dash.
_NAMED = re.compile(r"[^\W_]")


def is_name(value: str) -> bool:
    """True for a usable localized name, not a placeholder like ``-``."""
    return bool(_NAMED.search(value))


@dataclass(frozen=True)
class Term:
    """One glossary entry."""

    category: str
    english: str
    localized: str


class Glossary:
    """Localized names per language, read from and written to the library."""

    def __init__(self, i18n_dir: Path) -> None:
        """Bind to ``library/i18n``."""
        self._dir = i18n_dir
        self._lock = threading.Lock()
        # lang → (mtime the file had, category → english lower → (english, localized),
        # category → English names still a placeholder)
        self._cache: dict[
            str, tuple[float, dict[str, dict[str, tuple[str, str]]], dict[str, list[str]]]
        ] = {}

    def path(self, lang: str) -> Path:
        """The language's glossary file."""
        return self._dir / lang / GLOSSARY_FILE

    def _load(self, lang: str) -> dict[str, dict[str, tuple[str, str]]]:
        return self._read(lang)[0]

    def _read(
        self, lang: str
    ) -> tuple[dict[str, dict[str, tuple[str, str]]], dict[str, list[str]]]:
        path = self.path(lang)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}, {}
        cached = self._cache.get(lang)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]
        raw = read_json(path, {}) or {}
        table: dict[str, dict[str, tuple[str, str]]] = {}
        still: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for category, names in raw.items():
                if category.startswith("_") or not isinstance(names, dict):
                    continue
                table[category] = {
                    str(english).lower(): (str(english), str(localized))
                    for english, localized in names.items()
                    if isinstance(localized, str) and is_name(localized)
                }
                open_names = sorted(
                    (
                        str(english)
                        for english, localized in names.items()
                        if not (isinstance(localized, str) and is_name(localized))
                    ),
                    key=str.lower,
                )
                if open_names:
                    still[category] = open_names
        self._cache[lang] = (mtime, table, still)
        return table, still

    def table(self, lang: str) -> dict[str, dict[str, tuple[str, str]]]:
        """The language's names as read: category → lower-case English → (English, name)."""
        return self._load(lang)

    def term(self, lang: str, category: str, english: str) -> str | None:
        """The localized name of an entry, or None."""
        found = self._load(lang).get(category, {}).get(english.lower())
        return found[1] if found else None

    def terms(self, lang: str) -> list[Term]:
        """Every term of a language, by category and English name."""
        return sorted(
            (
                Term(category, english, localized)
                for category, names in self._load(lang).items()
                for english, localized in names.values()
            ),
            key=lambda t: (t.category, t.english.lower()),
        )

    def still_open(self, lang: str) -> dict[str, list[str]]:
        """The names the file lists but does not give yet (``"-"``), per category,
        alphabetically: what is left to translate by hand."""
        return self._read(lang)[1]

    def count(self, lang: str) -> int:
        """How many terms the language has."""
        return sum(len(names) for names in self._load(lang).values())

    def set(self, lang: str, category: str, english: str, localized: str | None) -> None:
        """Name an entry; an empty name (or a placeholder) is written as ``"-"``, so the
        entry stays in the file as one still to name."""
        self.set_many(lang, category, {english: localized})

    def set_many(self, lang: str, category: str, names: Mapping[str, str | None]) -> None:
        """Name several entries of a category at once, written in one go; as
        :meth:`set`, an empty name is written as ``"-"``."""
        with self._lock:
            raw = self._raw(lang)
            stored = raw.get(category)
            stored = dict(stored) if isinstance(stored, dict) else {}
            for english, localized in names.items():
                value = (localized or "").strip()[:MAX_TERM]
                if not is_name(value):
                    value = PLACEHOLDER
                stored = {k: v for k, v in stored.items() if k.lower() != english.lower()}
                stored[english] = value
            raw[category] = stored
            self._write(lang, raw)

    def fill(self, lang: str, entries: Mapping[str, Iterable[str]]) -> dict[str, int]:
        """Add every English name of ``entries`` (category → names) the language's file
        lacks, as ``"-"``; names already there, given or not, are left as they are.
        Returns how many were added per category."""
        added: dict[str, int] = {}
        with self._lock:
            raw = self._raw(lang)
            for category, english_names in entries.items():
                names = raw.get(category)
                if not isinstance(names, dict):
                    names = {}
                known = {k.lower() for k in names}
                for english in english_names:
                    if english and english.lower() not in known:
                        names[english] = PLACEHOLDER
                        known.add(english.lower())
                        added[category] = added.get(category, 0) + 1
                if names:
                    raw[category] = names
            if added:
                self._write(lang, raw)
        return added

    def _raw(self, lang: str) -> dict[str, Any]:
        raw = read_json(self.path(lang), {}) or {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, lang: str, raw: dict[str, Any]) -> None:
        """The file, categories and names in alphabetical order."""
        ordered = {
            category: dict(sorted(names.items(), key=lambda kv: kv[0].lower()))
            if isinstance(names, dict)
            else names
            for category, names in sorted(raw.items())
        }
        path = self.path(lang)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, ordered)
        self._cache.pop(lang, None)


@dataclass(frozen=True)
class Wanted:
    """An entry the rules text links to, not yet named in a language."""

    category: str
    english: str
    links: int


class LinkCounts:
    """How often the corpus's text links to each entry — what to translate first.

    One pass over every loaded entry's text for entity tags (``{@spell fireball}``),
    keyed by the category the tag resolves in and the name as written; ~0.2 s, done
    once per corpus load, on first use.
    """

    def __init__(self, compendium: Any, tags: Mapping[str, tuple[str, ...]]) -> None:
        """Bind to the compendium and the tag → category table of the renderer."""
        self._compendium = compendium
        self._tags = tags
        self._pattern = re.compile(r"\{@(" + "|".join(map(re.escape, tags)) + r") ([^}|]+)")
        self._for: Any = None
        self._counts: Counter[tuple[str, str]] = Counter()

    def counts(self) -> Counter[tuple[str, str]]:
        """(category, lower-case name) → links, recomputed after a corpus reload."""
        loaded = getattr(self._compendium, "projections", None)
        if self._for is not loaded or not self._counts:
            counts: Counter[tuple[str, str]] = Counter()
            for category in self._compendium.categories():
                for entry in self._compendium.all(category):
                    text = entry.model_dump_json(by_alias=True, exclude_none=True)
                    for tag, name in self._pattern.findall(text):
                        counts[(self._tags[tag][0], name.strip().lower())] += 1
            self._counts, self._for = counts, loaded
        return self._counts

    def wanted(
        self,
        named: Callable[[str, str], str | None],
        *,
        category: str = "",
        limit: int = 50,
    ) -> list[Wanted]:
        """The most-linked entries ``named`` has no name for, most links first."""
        out: list[Wanted] = []
        for (cat, name), links in self.counts().most_common():
            if category and cat != category:
                continue
            entry = self._compendium.find(cat, name)
            if entry is None:
                continue
            english = str(getattr(entry, "name", name))
            if named(cat, english):
                continue
            out.append(Wanted(cat, english, links))
            if len(out) >= limit:
                break
        return out


__all__ = ["GLOSSARY_FILE", "PLACEHOLDER", "Glossary", "LinkCounts", "Term", "Wanted"]
