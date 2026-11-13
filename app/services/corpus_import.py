"""Importing a translated 5eTools corpus (documentation/TRANSLATION.md §7).

A corpus in another language, in the 5eTools format and layout, becomes translations
over the English one: never replacement records. Each translated record is matched to
its English one by ``(category, name, source)``; translated corpora name the English in
``ENG_name`` (and ``ENG_className``, …) where they translate the name, so that comes
first. Then, per passage (the same paths the editor uses):

* a passage is taken where its text differs from the English and its links point at the
  same entries as the English's (display text may differ: ``{@spell fireball||Feuerball}``
  links where ``{@spell fireball}`` does); otherwise it stays English, counted;
* a passage the table translated already is kept, unless asked to replace it;
* what is taken is a **draft** until confirmed, unless the import is marked reviewed, so
  text nobody here has read is never indistinguishable from a reviewed one (§7);
* a translated entry name goes into the glossary where it names nothing yet.

Entries of every compendium category, their descriptions (fluff), rulebook chapters
and published adventures' sections are read. Nothing is written under either corpus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import CORPUS_FOLDER, CORPUS_MARKER
from app.services.glossary import Glossary, is_name
from app.services.i18n import Translatable
from app.services.translations import (
    CHAPTER_FIELDS,
    ENTRY_FIELDS,
    FORMATTING,
    TranslationStore,
    passages,
    tags_in,
    translation_key,
)
from app.store.compendium import CATEGORIES, CompendiumStore, expand_glob, is_vtt_payload
from app.store.fluff import FluffStore
from app.store.published import (
    BODY_DIR,
    BOOK_DIR,
    PublishedAdventures,
    PublishedBooks,
    body_sections,
)

logger = logging.getLogger(__name__)

BY = "import"
# What marks a folder as a 5eTools corpus, any one of them.
CORPUS_SIGNS = ("books.json", "spells", "bestiary", "book", "adventure")
# The fields a translated corpus names in English beside its own spelling.
ENGLISH_FIELDS = ("name", "className", "subclassShortName")


@dataclass
class ImportReport:
    """What an import found and did, per kind (a compendium category, ``<category>Fluff``,
    ``book`` or ``adventure``)."""

    records: Counter[str] = field(default_factory=Counter)
    passages: Counter[str] = field(default_factory=Counter)
    kept: Counter[str] = field(default_factory=Counter)
    links: Counter[str] = field(default_factory=Counter)
    unmatched: Counter[str] = field(default_factory=Counter)
    names: Counter[str] = field(default_factory=Counter)
    # Books and adventures whose chapters do not line up with the English, by id.
    misaligned: list[str] = field(default_factory=list)

    @property
    def kinds(self) -> list[str]:
        """Every kind anything was found for, in order."""
        seen = self.records + self.unmatched + self.links
        return sorted(seen)


def corpus_root(path: Path) -> Path:
    """The folder holding the corpus: a whole release's ``data/``, as for the English."""
    inner = path / CORPUS_FOLDER
    if not (path / CORPUS_MARKER).is_file() and (inner / CORPUS_MARKER).is_file():
        return inner
    return path


def _target(tag: str) -> str | None:
    """What a tag points at, without the text it shows; None for a formatting tag."""
    inner = tag[2:-1].strip()
    name, _, rest = inner.partition(" ")
    if name.lower() in FORMATTING:
        return None
    parts = rest.split("|")
    # {@deity Bahamut|Nonhuman|PHB|label} and {@card …|deck|source|label} shift the
    # display text one slot along (TRANSLATION.md §3).
    shown = 3 if name.lower() in ("deity", "card") else 2
    kept = [p.strip().lower() for p in parts[:shown]]
    while kept and not kept[-1]:
        kept.pop()
    return f"{name.lower()} " + "|".join(kept)


def same_links(english: str, translated: str) -> bool:
    """True when both texts link and roll the same things, whatever they display."""

    def targets(text: str) -> list[str]:
        return sorted(t for tag in tags_in(text) if (t := _target(tag)) is not None)

    return targets(english) == targets(translated)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        yield from (v for v in value if isinstance(v, dict))


def _english_view(item: Mapping[str, Any]) -> dict[str, Any]:
    """A translated record as the English names it, for the key it is stored under."""
    view = dict(item)
    for name in ENGLISH_FIELDS:
        english = item.get(f"ENG_{name}")
        if isinstance(english, str) and english.strip():
            view[name] = english.strip()
    return view


class CorpusImport:
    """One import of a translated corpus into one language's translations."""

    def __init__(
        self,
        *,
        compendium: CompendiumStore,
        fluff: FluffStore,
        books: PublishedBooks,
        adventures: PublishedAdventures,
        store: TranslationStore,
        glossary: Glossary,
    ) -> None:
        """The English side to match against, and where translations are written."""
        self._compendium = compendium
        self._fluff = fluff
        self._books = books
        self._adventures = adventures
        self._store = store
        self._glossary = glossary

    def run(
        self,
        source: Path,
        lang: str,
        *,
        reviewed: bool = False,
        replace: bool = False,
        dry_run: bool = False,
        phase: Callable[[str], None] | None = None,
    ) -> ImportReport:
        """Import everything ``source`` holds that matches the English corpus; ``phase``
        hears ``entries``, ``fluff`` and ``books`` as each part begins."""
        root = corpus_root(source)
        report = ImportReport()
        say = phase or (lambda _name: None)
        say("entries")
        self._entries(root, lang, report, reviewed, replace, dry_run)
        say("fluff")
        self._fluff_texts(root, lang, report, reviewed, replace, dry_run)
        say("books")
        self._bodies(root, lang, report, reviewed, replace, dry_run)
        return report

    # -- one record ------------------------------------------------------------------

    def _texts(
        self,
        kind: str,
        english: Mapping[str, Any],
        translated: Mapping[str, Any],
        fields: tuple[str, ...],
        report: ImportReport,
    ) -> dict[str, str]:
        """The passages of ``translated`` worth taking, by path."""
        theirs = dict(passages(translated, fields))
        out: dict[str, str] = {}
        for path, text in passages(english, fields):
            given = theirs.get(path)
            if not given or given.strip() == text.strip():
                continue
            if not same_links(text, given):
                report.links[kind] += 1
                continue
            out[path] = given
        return out

    def _write(
        self,
        kind: str,
        lang: str,
        given: Sequence[tuple[str, Mapping[str, Any], Mapping[str, str]]],
        fields: tuple[str, ...],
        report: ImportReport,
        reviewed: bool,
        replace: bool,
        dry_run: bool,
    ) -> None:
        items = [item for item in given if item[2]]
        if not items:
            return
        report.records[kind] += len(items)
        if dry_run:  # what a real run would do, the table's own passages kept
            for key, _data, texts in items:
                record = self._store.record(lang, kind, key)
                have = record.passages if record is not None and not replace else {}
                report.kept[kind] += sum(1 for path in texts if path in have)
                report.passages[kind] += sum(1 for path in texts if path not in have)
            return
        count = self._store.save_many(
            lang,
            kind,
            items,
            by=BY,
            draft=not reviewed,
            fields=fields,
            keep=not replace,
        )
        report.passages[kind] += count.saved
        report.kept[kind] += count.kept

    def _names(
        self,
        lang: str,
        category: str,
        names: Mapping[str, str],
        report: ImportReport,
        replace: bool,
        dry_run: bool,
    ) -> None:
        """Translated entry names into the glossary, where it names nothing yet."""
        new = {
            english: local
            for english, local in names.items()
            if replace or not is_name(self._glossary.term(lang, category, english) or "")
        }
        if not new:
            return
        report.names[category] += len(new)
        if not dry_run:
            self._glossary.set_many(lang, category, new)

    # -- entries, descriptions, books ------------------------------------------------

    def _entries(
        self,
        root: Path,
        lang: str,
        report: ImportReport,
        reviewed: bool,
        replace: bool,
        dry_run: bool,
    ) -> None:
        loaded = set(self._compendium.categories())
        for category in CATEGORIES:
            if category.name not in loaded:
                continue
            index = {
                translation_key(category.name, data).lower(): data
                for model in self._compendium.all(category.name)
                for data in [model.model_dump(by_alias=True, exclude_none=True)]
            }
            items: list[tuple[str, Mapping[str, Any], Mapping[str, str]]] = []
            names: dict[str, str] = {}
            for pattern in category.globs:
                for path in expand_glob(root, pattern):
                    raw = _read(path)
                    if not isinstance(raw, dict):
                        continue
                    for item in _dicts(raw.get(category.json_key)):
                        view = _english_view(item)
                        key = translation_key(category.name, view)
                        english = index.get(key.lower())
                        if english is None:
                            report.unmatched[category.name] += 1
                            continue
                        texts = self._texts(category.name, english, item, ENTRY_FIELDS, report)
                        items.append((translation_key(category.name, english), english, texts))
                        local = str(item.get("name") or "").strip()
                        if local and local != str(english.get("name", "")):
                            names[str(english["name"])] = local
            self._write(
                category.name, lang, items, ENTRY_FIELDS, report, reviewed, replace, dry_run
            )
            self._names(lang, category.name, names, report, replace, dry_run)

    def _fluff_texts(
        self,
        root: Path,
        lang: str,
        report: ImportReport,
        reviewed: bool,
        replace: bool,
        dry_run: bool,
    ) -> None:
        loaded = set(self._compendium.categories())
        found: dict[str, list[tuple[str, Mapping[str, Any], Mapping[str, str]]]] = {}
        for path in sorted(root.rglob("fluff-*.json")):
            raw = _read(path)
            if is_vtt_payload(path) or not isinstance(raw, dict):
                continue
            for json_key, value in raw.items():
                base = json_key.removesuffix("Fluff")
                if not json_key.endswith("Fluff") or base not in loaded:
                    continue
                kind = json_key
                for item in _dicts(value):
                    view = _english_view(item)
                    extra = {k: view[k] for k in ("className", "classSource") if k in view}
                    english_model = self._fluff.get(
                        base, str(view.get("name", "")), str(view.get("source", "")), **extra
                    )
                    if english_model is None:
                        report.unmatched[kind] += 1
                        continue
                    english = english_model.model_dump(by_alias=True, exclude_none=True)
                    texts = self._texts(kind, english, item, ENTRY_FIELDS, report)
                    found.setdefault(kind, []).append(
                        (translation_key(base, english), english, texts)
                    )
        for kind, items in found.items():
            self._write(kind, lang, items, ENTRY_FIELDS, report, reviewed, replace, dry_run)

    def _bodies(
        self,
        root: Path,
        lang: str,
        report: ImportReport,
        reviewed: bool,
        replace: bool,
        dry_run: bool,
    ) -> None:
        """Rulebook chapters by position, adventure sections by their id, each only when
        the translated file has as many as the English."""
        for book in self._books.all():
            body = self._books.body(book.id)
            raw = _read(root / BOOK_DIR / f"book-{book.id.lower()}.json")
            if body is None or raw is None:
                continue
            theirs = [s for s in body_sections(raw) if isinstance(s, dict)]
            if len(theirs) != len(body.sections):
                report.misaligned.append(book.id)
                continue
            items = [
                (
                    f"{book.id}|{index}",
                    section.entry,
                    self._texts("book", section.entry, theirs[index], CHAPTER_FIELDS, report),
                )
                for index, section in enumerate(body.sections)
            ]
            self._write("book", lang, items, CHAPTER_FIELDS, report, reviewed, replace, dry_run)
        for adventure in self._adventures.all():
            told = self._adventures.body(adventure.id)
            raw = _read(root / BODY_DIR / f"adventure-{adventure.id.lower()}.json")
            if told is None or raw is None:
                continue
            theirs = [s for s in body_sections(raw) if isinstance(s, dict)]
            if len(theirs) != len(told.sections):
                report.misaligned.append(adventure.id)
                continue
            items = [
                (
                    f"{adventure.id}|{section.id}",
                    section.entry,
                    self._texts("adventure", section.entry, theirs[index], CHAPTER_FIELDS, report),
                )
                for index, section in enumerate(told.sections)
            ]
            self._write(
                "adventure", lang, items, CHAPTER_FIELDS, report, reviewed, replace, dry_run
            )


# -- from the app ----------------------------------------------------------------------


class CorpusImportError(Translatable):
    """A folder that cannot be imported, as a key the page says in its language."""


@dataclass
class ImportRun:
    """One import started from the translations page, run in the background."""

    id: str
    lang: str
    folder: str
    dry_run: bool
    reviewed: bool
    replace: bool
    phase: str = ""
    finished: bool = False
    error: str = ""
    report: ImportReport | None = None


class CorpusImports:
    """Imports started from the app: one at a time, off the event loop, kept in memory
    so the page can show how the newest went."""

    def __init__(
        self,
        importer: Callable[[], CorpusImport],
        *,
        reference_dir: Path | None,
        library_dir: Path,
    ) -> None:
        """``importer`` makes the import (it needs the loaded corpus); the English corpus
        and the library are folders an import never reads from."""
        self._importer = importer
        self._reference = reference_dir
        self._library = library_dir
        self._runs: list[ImportRun] = []
        self._tasks: set[asyncio.Task[None]] = set()

    def latest(self, lang: str) -> ImportRun | None:
        """The newest run for ``lang``."""
        return next((r for r in reversed(self._runs) if r.lang == lang), None)

    @property
    def running(self) -> bool:
        """True while a run goes on."""
        return any(not r.finished for r in self._runs)

    def folder(self, given: str) -> Path:
        """The folder named, if it may be imported: it exists, looks like a 5eTools
        corpus, and is neither the English corpus nor the library."""
        text = given.strip()
        if not text:
            raise CorpusImportError("translations.import.error.folder", entry=given)
        path = Path(text).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise CorpusImportError("translations.import.error.folder", entry=text)
        path = path.resolve()
        root = corpus_root(path)
        for own in (self._reference, self._library):
            if own is not None and (
                root == own.resolve()
                or own.resolve() in root.parents
                or root in own.resolve().parents
            ):
                raise CorpusImportError("translations.import.error.own", entry=text)
        if not any((root / sign).exists() for sign in CORPUS_SIGNS):
            raise CorpusImportError("translations.import.error.corpus", entry=text)
        return path

    def start(
        self, lang: str, given: str, *, dry_run: bool, reviewed: bool, replace: bool
    ) -> ImportRun:
        """Check the folder and start the run; one at a time."""
        if self.running:
            raise CorpusImportError("translations.import.error.busy")
        path = self.folder(given)
        run = ImportRun(uuid.uuid4().hex[:12], lang, str(path), dry_run, reviewed, replace)
        self._runs.append(run)
        del self._runs[:-10]  # the newest few are enough to show

        def phase(name: str) -> None:
            run.phase = name

        async def go() -> None:
            try:
                run.report = await asyncio.to_thread(
                    self._importer().run,
                    path,
                    lang,
                    reviewed=reviewed,
                    replace=replace,
                    dry_run=dry_run,
                    phase=phase,
                )
            except OSError as exc:
                logger.exception("Importing the translated corpus in %s failed", path)
                run.error = str(exc)
            finally:
                run.finished = True

        task = asyncio.get_running_loop().create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run


__all__ = [
    "CorpusImport",
    "CorpusImports",
    "CorpusImportError",
    "ImportReport",
    "ImportRun",
    "corpus_root",
    "same_links",
]
