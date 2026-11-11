"""How far a language covers the corpus, and machine drafts in bulk (TRANSLATION.md §7).

* :class:`TranslationCoverage` counts what is translated, drafted and outdated — per
  category, per rulebook, per published adventure and per reference screen — lists the
  most-linked entries nobody has translated (translate what people actually hit), and
  every record whose English moved on (stale) or lost passages (orphans), which are
  reported, never pruned. A record whose entry is gone is listed with the entry it
  probably goes by now, and moves there on the gamemaster's word.
* :class:`TranslationJobs` runs machine drafts for many records in the background —
  the most-linked untranslated entries, a whole rulebook or a whole adventure — and
  keeps their progress for the page to show.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.services.glossary import LinkCounts
from app.services.i18n import Translatable
from app.services.machine_translation import MachineTranslator
from app.services.player_view import READABLE, Reader
from app.services.references import ReferenceNotFound
from app.services.translation_targets import Target, TargetNotFound, TranslationTargets
from app.services.translations import (
    BOOKISH,
    COMPOSITE,
    Record,
    TranslationStore,
    english_hash,
    missing_tags,
    passages,
    record_key,
    translation_key,
)
from app.store.compendium import key_of
from app.store.errors import EntryNotFound
from app.store.fluff import FLUFF_SUFFIX
from app.store.published import SCREENS, PublishedAdventures, PublishedBooks, ReferenceScreens

logger = logging.getLogger(__name__)

WANTED_LIMIT = 40
# What the books say about these, a player reads in the wizard.
PLAYER_FLUFF = frozenset({"background", "class", "race", "subclass"})
BookKind = Literal["book", "adventure", "screen"]


@dataclass(frozen=True)
class CategoryCount:
    """One category: entries with any translation, of how many loaded."""

    category: str
    entries: int
    translated: int
    drafts: int = 0
    stale: int = 0


@dataclass(frozen=True)
class BookCount:
    """One rulebook, adventure or reference screen: chapters started, of how many."""

    kind: BookKind
    id: str
    name: str
    chapters: int
    started: int
    drafts: int = 0
    stale: int = 0


@dataclass(frozen=True)
class WantedEntry:
    """An untranslated entry the corpus links to often."""

    category: str
    name: str
    source: str
    links: int


@dataclass(frozen=True)
class Problem:
    """A record whose translation needs a look: outdated or orphaned passages."""

    target: Target
    stale: int
    orphans: int


@dataclass(frozen=True)
class Drafted:
    """A record with drafts nobody has confirmed: how many, and the latest's author and
    time."""

    target: Target
    drafts: int
    by: str = ""
    at: str = ""


@dataclass(frozen=True)
class Unfinished:
    """A record translated in part: passages still English, most often ones the machine
    gave back with a tag changed."""

    target: Target
    open: int
    total: int


@dataclass(frozen=True)
class Audit:
    """One stored translation against the corpus as it is now. ``target`` is None when
    the entry, chapter or book it translates is gone."""

    category: str
    key: str
    target: Target | None
    # Every passage held, as (path, translated text).
    held: tuple[tuple[str, str], ...]
    stale: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()
    # Tags of the English that the translation lacks: lost links and rolls.
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class Lost:
    """A stored translation whose entry, chapter or book is gone."""

    category: str
    key: str
    passages: int
    # The key it probably goes by now; empty when nothing comes close.
    near: str = ""


class MoveTaken(Translatable):
    """The new entry has a translation of its own, which a move would replace."""


class TranslationCoverage:
    """Counts over the translation files of one language."""

    def __init__(
        self,
        store: TranslationStore,
        targets: TranslationTargets,
        compendium: Any,
        link_counts: LinkCounts,
        rulebooks: PublishedBooks,
        adventures: PublishedAdventures,
        i18n_dir: Path,
        screens: ReferenceScreens | None = None,
    ) -> None:
        """Bind to the store, the corpus, its published books and reference screens."""
        self._screens = screens
        self._store = store
        self._targets = targets
        self._compendium = compendium
        self._links = link_counts
        self._rulebooks = rulebooks
        self._adventures = adventures
        self._dir = i18n_dir
        # category → (the load it was read from, translation key → the compendium's key)
        self._composite: dict[str, tuple[Any, dict[str, str]]] = {}

    @property
    def targets(self) -> TranslationTargets:
        """The English records behind the translatable pages."""
        return self._targets

    def _category_files(self, lang: str) -> Iterator[str]:
        folder = self._dir / lang
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.json")):
            # "_glossary.json", "_glossary_suggestions.json": not translated text
            if not path.name.startswith("_") and path.stem not in BOOKISH:
                yield path.stem

    def _composite_key(self, category: str, key: str) -> str | None:
        """The compendium's key of a record whose translation key differs from it."""
        loaded = getattr(self._compendium, "projections", None)
        cached = self._composite.get(category)
        if cached is None or cached[0] is not loaded:
            keys = {
                translation_key(category, m.model_dump(by_alias=True, exclude_none=True)).lower(): (
                    key_of(m)
                )
                for m in self._compendium.all(category)
            }
            cached = self._composite[category] = (loaded, keys)
        return cached[1].get(key.lower())

    def _entry_target(self, category: str, key: str) -> Target | None:
        name, _, source = key.partition("|")
        try:
            if category in COMPOSITE:
                found = self._composite_key(category, key)
                return self._targets.feature(category, found) if found else None
            if category.endswith(FLUFF_SUFFIX):
                # A subclass's key goes on with its class: name|source|class|class source.
                name, source, *owner = key.split("|")
                return self._targets.fluff(
                    category.removesuffix(FLUFF_SUFFIX), source, name, *owner
                )
            return self._targets.entry(category, source, name)
        except (ReferenceNotFound, TargetNotFound):
            return None

    def categories(self, lang: str) -> list[CategoryCount]:
        """Every category the language has translations in, with its counts."""
        out: list[CategoryCount] = []
        for category in self._category_files(lang):
            translated = drafts = stale = 0
            for key in self._store.records(lang, category):
                target = self._entry_target(category, key)
                if target is None:
                    continue
                coverage = self._store.overlay(lang, category, key, target.data).coverage
                translated += coverage.any
                drafts += bool(coverage.drafts)
                stale += bool(coverage.stale)
            try:
                # A description counts against the entries it describes.
                entries = len(self._compendium.all(category.removesuffix(FLUFF_SUFFIX)))
            except EntryNotFound:
                entries = 0
            out.append(CategoryCount(category, entries, translated, drafts, stale))
        return out

    def books(self, lang: str) -> list[BookCount]:
        """Every rulebook, adventure and reference screen, with the chapters begun."""
        out: list[BookCount] = []
        for book in self._rulebooks.all():
            out.append(self._book_count(lang, "book", book.id, book.name, len(book.contents)))
        for adventure in self._adventures.all():
            out.append(self._book_count(lang, "adventure", adventure.id, adventure.name, 0))
        for screen in SCREENS:
            body = self._screens.body(screen) if self._screens is not None else None
            if body is not None:
                out.append(
                    self._book_count(lang, "screen", screen, body.book.name, len(body.sections))
                )
        return out

    def _book_count(
        self, lang: str, kind: BookKind, ident: str, name: str, chapters: int
    ) -> BookCount:
        path = self._store.path(lang, kind, f"{ident}|0")
        if not path.is_file():
            return BookCount(kind, ident, name, chapters, 0)
        started = drafts = stale = 0
        targets = self.chapters(kind, ident)
        for target in targets:
            coverage = self._store.overlay(
                lang, kind, target.key, target.data, target.fields
            ).coverage
            started += coverage.any
            drafts += bool(coverage.drafts)
            stale += bool(coverage.stale)
        return BookCount(kind, ident, name, len(targets) or chapters, started, drafts, stale)

    def chapters(self, kind: str, ident: str) -> list[Target]:
        """Every chapter of a rulebook or screen, or section of an adventure, as targets."""
        out: list[Target] = []
        if kind == "screen":
            screen = self._screens.body(ident) if self._screens is not None else None
            return (
                [self._targets.screen(ident, n) for n in range(len(screen.sections))]
                if screen
                else []
            )
        if kind == "book":
            body = self._rulebooks.body(ident)
            for index in range(len(body.sections) if body else 0):
                out.append(self._targets.chapter(ident, index))
        else:
            body_a = self._adventures.body(ident)
            for part in body_a.sections if body_a else ():
                try:
                    out.append(self._targets.section(ident, part.id))
                except TargetNotFound:
                    continue
        return out

    def wanted(self, lang: str, limit: int = WANTED_LIMIT) -> list[WantedEntry]:
        """The most-linked entries without any translation, most links first."""
        out: list[WantedEntry] = []
        for (category, name), links in self._links.counts().most_common():
            entry = self._compendium.find(category, name)
            if entry is None:
                continue
            english, source = str(entry.name), str(entry.source)
            if self._store.record(lang, category, record_key(english, source)) is not None:
                continue
            out.append(WantedEntry(category, english, source, links))
            if len(out) >= limit:
                break
        return out

    def problems(self, lang: str) -> list[Problem]:
        """Records with outdated or orphaned passages (reference entries and books)."""
        out: list[Problem] = []
        for category in self._category_files(lang):
            for key in self._store.records(lang, category):
                target = self._entry_target(category, key)
                if target is None:
                    continue
                coverage = self._store.overlay(lang, category, key, target.data).coverage
                if coverage.stale or coverage.orphans:
                    out.append(Problem(target, coverage.stale, coverage.orphans))
        for kind in sorted(BOOKISH):
            for key, _record in self._store.book_records(lang, kind):
                chapter = self._chapter_target(kind, key)
                if chapter is None:
                    continue
                coverage = self._store.overlay(
                    lang, chapter.category, chapter.key, chapter.data, chapter.fields
                ).coverage
                if coverage.stale or coverage.orphans:
                    out.append(Problem(chapter, coverage.stale, coverage.orphans))
        return out

    def drafts(self, lang: str) -> list[Drafted]:
        """Every record holding unconfirmed drafts, by category and title. A record whose
        entry is gone is left to :meth:`lost`."""
        found: list[tuple[Record, Target | None]] = []
        for category in self._category_files(lang):
            for key, record in self._store.records(lang, category).items():
                if any(p.draft for p in record.passages.values()):
                    found.append((record, self._entry_target(category, key)))
        for kind in sorted(BOOKISH):
            for key, record in self._store.book_records(lang, kind):
                if any(p.draft for p in record.passages.values()):
                    found.append((record, self._chapter_target(kind, key)))
        out = []
        for record, target in found:
            if target is None:
                continue
            drafted = [p for p in record.passages.values() if p.draft]
            latest = max(drafted, key=lambda p: p.at)
            out.append(Drafted(target, len(drafted), latest.by, latest.at))
        return sorted(out, key=lambda d: (d.target.category, d.target.title.lower()))

    def unfinished(self, lang: str) -> list[Unfinished]:
        """Every record translated in part, by category and title: its passages still
        English. A record whose entry is gone is left to :meth:`lost`."""
        found: list[Target] = []
        for category in self._category_files(lang):
            for key in self._store.records(lang, category):
                target = self._entry_target(category, key)
                if target is not None:
                    found.append(target)
        for kind in sorted(BOOKISH):
            for key, _record in self._store.book_records(lang, kind):
                chapter = self._chapter_target(kind, key)
                if chapter is not None:
                    found.append(chapter)
        out = []
        for target in found:
            coverage = self._store.overlay(
                lang, target.category, target.key, target.data, target.fields
            ).coverage
            if coverage.translated < coverage.total:
                out.append(Unfinished(target, coverage.total - coverage.translated, coverage.total))
        return sorted(out, key=lambda u: (u.target.category, u.target.title.lower()))

    def for_players(self, lang: str, reader: Reader) -> list[Target]:
        """Every record a player reads that still has English passages: the player kinds
        in the player books (documentation/PLAYER-VIEW.md), class and subclass features
        included, and what the books say about species, classes, subclasses and
        backgrounds, as the wizard shows it."""
        out: list[Target] = []

        def keep(target: Target) -> None:
            coverage = self._store.overlay(
                lang, target.category, target.key, target.data, target.fields
            ).coverage
            if coverage.translated < coverage.total:
                out.append(target)

        for category in sorted(READABLE):
            try:
                models = self._compendium.all(category)
            except EntryNotFound:
                continue
            for model in models:
                if not reader.readable(category, model):
                    continue
                try:
                    if category in COMPOSITE:
                        keep(self._targets.feature(category, key_of(model)))
                    else:
                        keep(self._targets.entry(category, str(model.source), model.ref_name))
                    if category in PLAYER_FLUFF:
                        data = model.model_dump(by_alias=True, exclude_none=True)
                        owner = (
                            (str(data.get("className", "")), str(data.get("classSource", "")))
                            if category == "subclass"
                            else ()
                        )
                        keep(self._targets.fluff(category, str(model.source), model.name, *owner))
                except (ReferenceNotFound, TargetNotFound):
                    continue
        return out

    def confirm_drafts(self, lang: str, *, by: str) -> int:
        """Confirm every draft :meth:`drafts` lists, unread; how many passages."""
        return sum(
            self._store.confirm(lang, x.target.category, x.target.key, by=by)
            for x in self.drafts(lang)
        )

    def lost(self, lang: str) -> list[Lost]:
        """Every stored translation of something gone, with where it may have gone."""
        names: dict[str, dict[str, Any]] = {}
        return [
            Lost(x.category, x.key, len(x.held), self._near(x.category, x.key, names))
            for x in self.audit(lang)
            if x.target is None
        ]

    def _near(self, category: str, key: str, names: dict[str, dict[str, Any]]) -> str:
        """The key of the entry a gone one may be now: its name in another book, or a close
        name. ``names`` keeps each category's names between calls."""
        kind = category.removesuffix(FLUFF_SUFFIX)
        if kind not in self._compendium.categories():
            return ""
        name = key.split("|", 1)[0]
        found = self._compendium.find(kind, name)
        if found is None:
            if kind not in names:
                names[kind] = {
                    str(getattr(e, "name", "")).lower(): e for e in self._compendium.all(kind)
                }
            close = difflib.get_close_matches(name.lower(), list(names[kind]), n=1, cutoff=0.8)
            found = names[kind][close[0]] if close else None
        if found is None:
            return ""
        return translation_key(kind, found.model_dump(by_alias=True, exclude_none=True))

    def move(self, lang: str, category: str, key: str, to: str, *, replace: bool = False) -> None:
        """Move a lost translation onto the entry named by ``to``. A record that still has
        its entry stays, and one on the new entry is replaced only when told to."""
        if category not in BOOKISH and category not in set(self._category_files(lang)):
            raise Translatable("translations.move_unknown", entry=to)
        chapters = category in BOOKISH
        find = self._chapter_target if chapters else self._entry_target
        if self._store.record(lang, category, key) is None or find(category, key) is not None:
            raise Translatable("translations.move_not_lost", entry=key)
        try:
            target = find(category, to.strip())
        except ValueError:  # a description's key without its source
            target = None
        if target is None:
            raise Translatable("translations.move_unknown", entry=to)
        if not replace and self._store.record(lang, category, target.key) is not None:
            raise MoveTaken("translations.move_taken", entry=target.key)
        self._store.move(lang, category, key, target.key)

    def audit(self, lang: str) -> Iterator[Audit]:
        """Every stored translation held against the corpus: what the page's counts cannot
        say, as they start from what still exists."""
        for category in self._category_files(lang):
            for key, record in self._store.records(lang, category).items():
                yield _audit(category, key, record, self._entry_target(category, key))
        for kind in sorted(BOOKISH):
            for key, record in self._store.book_records(lang, kind):
                yield _audit(kind, key, record, self._chapter_target(kind, key))

    def _chapter_target(self, kind: str, key: str) -> Target | None:
        ident, _, place = key.partition("|")
        try:
            if kind == "adventure":
                return self._targets.section(ident, place)
            find = self._targets.chapter if kind == "book" else self._targets.screen
            return find(ident, int(place))
        except (TargetNotFound, ValueError):
            return None


def _audit(category: str, key: str, record: Record, target: Target | None) -> Audit:
    held = tuple((path, passage.text) for path, passage in record.passages.items())
    if target is None:
        return Audit(category, key, None, held)
    english = dict(passages(target.data, target.fields))
    stale, orphans, missing = [], [], []
    for path, passage in record.passages.items():
        text = english.get(path)
        if text is None:
            orphans.append(path)
            continue
        if passage.en != english_hash(text):
            stale.append(path)
        missing.extend(missing_tags(text, passage.text))
    return Audit(category, key, target, held, tuple(stale), tuple(orphans), tuple(missing))


# -- bulk machine drafts ---------------------------------------------------------------


@dataclass
class Job:
    """One background run of machine drafts."""

    id: str
    lang: str
    label: str
    total: int
    done: int = 0
    passages: int = 0
    rejected: int = 0
    failed: int = 0
    finished: bool = False
    error: str = ""
    targets: list[Target] = field(default_factory=list, repr=False)


class TranslationJobs:
    """Machine drafts for many records, one record after another, in the background."""

    def __init__(
        self,
        machine: MachineTranslator | None,
        coverage: TranslationCoverage,
        players: Callable[[], Reader | None] | None = None,
    ) -> None:
        """Bind to the translator (None: no service configured), the coverage, and what
        a player of every world reads (for the ``players`` scope)."""
        self._machine = machine
        self._coverage = coverage
        self._players = players
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def available(self) -> bool:
        """True when a translation service is configured."""
        return self._machine is not None

    def jobs(self, lang: str) -> list[Job]:
        """The language's jobs, newest first."""
        return [j for j in reversed(self._jobs.values()) if j.lang == lang]

    def scope(self, lang: str, scope: str) -> tuple[str, list[Target]]:
        """What a scope names: ``top:<n>``, ``book:<id>``, ``adventure:<id>``,
        ``screen:<dm|quick>``, ``players``, every record a player reads with English
        passages, or ``unfinished``, every record translated in part (its passages still
        English are tried again)."""
        kind, _, value = scope.partition(":")
        if kind == "top":
            limit = max(1, min(int(value or 25), 200))
            targets: list[Target] = []
            for wanted in self._coverage.wanted(lang, limit):
                try:
                    targets.append(
                        self._coverage.targets.entry(wanted.category, wanted.source, wanted.name)
                    )
                except ReferenceNotFound:
                    continue
            return f"top {limit}", targets
        if kind in ("book", "adventure", "screen"):
            return value, self._coverage.chapters(kind, value)
        if kind == "players":
            reader = self._players() if self._players is not None else None
            return "players", self._coverage.for_players(lang, reader) if reader else []
        if kind == "unfinished":
            return "unfinished", [u.target for u in self._coverage.unfinished(lang)]
        return "", []

    def start(self, lang: str, scope: str, *, by: str) -> Job | None:
        """Start drafting a scope; None when there is nothing to do or no service."""
        machine = self._machine
        if machine is None:
            return None
        label, targets = self.scope(lang, scope)
        if not targets:
            return None
        job = Job(uuid.uuid4().hex[:12], lang, label, len(targets), targets=targets)
        self._jobs[job.id] = job

        async def run() -> None:
            # The translator turns service errors into counts; what is left is the
            # library's own disk. Whatever happens, the job ends as finished.
            try:
                for target in job.targets:
                    try:
                        result = await machine.draft_record(lang, target, by=by)
                    except OSError as exc:
                        logger.exception("Could not store machine drafts of %s", target.key)
                        job.error = str(exc)
                        job.failed += 1
                    else:
                        job.passages += result.translated
                        job.rejected += result.rejected
                        job.failed += result.failed
                    job.done += 1
            finally:
                job.finished = True

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job


__all__ = [
    "Audit",
    "BookCount",
    "CategoryCount",
    "Drafted",
    "Job",
    "Lost",
    "MoveTaken",
    "Problem",
    "TranslationCoverage",
    "TranslationJobs",
    "WantedEntry",
]
