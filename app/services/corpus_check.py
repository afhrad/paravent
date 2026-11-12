"""Corpus health: is every 5eTools file in a shape Paravent reads?

The loaders collect what they cannot use rather than failing, so a malformed hand edit
does not take the other entries down (``LoadReport``). That is right for the server,
and a trap for a re-import: a load can report success while a key went unread or a
count came out short — the four silent bugs of 2026-10-09 all looked like clean loads.
This check reads the corpus again, independently, and says so:

1. **Files** — JSON that does not parse, a file that is not an object.
2. **Keys** — every top-level key, matched against what a loader reads. A key nobody
   reads and nobody planned is new upstream; one planned is "not loaded yet".
3. **Entries** — every row of every loaded category resolved (``_copy``) and validated
   with the store's own models, attributed to its file, the Pydantic error path given.
4. **Counts** — rows in the files against the entries the store holds. Every difference
   must be explained by a rejected row or a repeated key; the rest is an error.
5. **Fluff, lookups, tables, published text** — what their loaders rejected, orphaned
   fluff, books indexed without a body, tables a feature reads that came out empty.
6. **References** (optional) — tags Paravent's catalogue does not know, and entity tags
   that resolve to nothing in the whole corpus: its entries, descriptions, rulebooks and
   adventures.
7. **The library** — the table's own work against this corpus: translations, names, links
   (app/services/library_check.py). Part of every run, as its point is the first check
   after a new release.

The report is compared with the previous one (``reference-cache/corpus-check.json``):
keys, fields and row counts that changed since, which is what a re-import wants to know.
The corpus's own version comes from ``changelog.json`` (:func:`corpus_version`).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.store import item_entries
from app.store.compendium import (
    CATEGORIES,
    Category,
    CompendiumStore,
    expand_glob,
    is_vtt_payload,
    iter_json_files,
    key_of,
)
from app.store.copyresolver import CopyResolver, composite_key
from app.store.creature_tables import BLOCKS_FILE, FEATURES_FILE, STATS_FILE, CreatureTables
from app.store.encounter_tables import ENCOUNTERS_FILE, SHAPES_FILE, EncounterTables
from app.store.errors import CopyResolutionError
from app.store.fluff import FLUFF_SUFFIX, FluffStore
from app.store.jsonfile import read_json, write_json
from app.store.lookups import SPELL_SOURCE_LOOKUP, SUBCLASS_LOOKUP, LookupTables
from app.store.loot import ITEMS_FILE, LOOT_FILE, LootTables
from app.store.magicvariants import generic_entry
from app.store.npc_tables import LIFE_FILE, NAMES_FILE, NpcTables
from app.store.published import (
    BOOK_INDEX_FILE,
    INDEX_FILE,
    SCREENS,
    PublishedAdventures,
    PublishedBooks,
)
from app.store.reference import parse_reference

logger = logging.getLogger(__name__)

CHANGELOG_FILE = "changelog.json"
REPORT_FILE = "corpus-check.json"
# Findings of one kind kept in a report; the count says how many there were.
MAX_PER_KIND = 200


class CorpusVersion(BaseModel):
    """The corpus's release, from the newest entry of its ``changelog.json``."""

    version: str
    date: str = ""


def corpus_version(root: Path | None) -> CorpusVersion | None:
    """The version of the 5eTools data under ``root``, or None when it does not say.

    5eTools ships no version file with its data; ``changelog.json`` is a list of
    ``{"ver": "2.29.0", "date": "2026-05-30", "txt": …}``, oldest first.
    """
    if root is None:
        return None
    try:
        data = json.loads((root / CHANGELOG_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    releases = (
        [x for x in data if isinstance(x, dict) and x.get("ver")] if isinstance(data, list) else []
    )
    if not releases:
        return None
    newest = max(releases, key=lambda x: (str(x.get("date", "")), _version_tuple(str(x["ver"]))))
    return CorpusVersion(version=str(newest["ver"]), date=str(newest.get("date", "")))


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


# -- what the loaders read -------------------------------------------------------------


class Reader(BaseModel):
    """Keys one loader reads from the files a pattern matches (None: the whole file)."""

    pattern: str
    keys: frozenset[str] | None
    loader: str


def _readers() -> tuple[Reader, ...]:
    """Every (file, key) a Paravent loader reads; fluff is found by its suffix instead.

    The compendium's part comes from its category table, so a new category is covered
    without touching this. The table readers are listed by hand: keep them in step.
    """
    by_glob: dict[str, set[str]] = defaultdict(set)
    for category in CATEGORIES:
        for pattern in category.globs:
            by_glob[pattern] |= {category.json_key, *category.aux_keys}
        for pattern in category.aux_globs:
            by_glob[pattern] |= set(category.aux_keys)
    compendium = [
        Reader(pattern=p, keys=frozenset(k), loader="compendium") for p, k in by_glob.items()
    ]
    return (
        *compendium,
        Reader(pattern=SPELL_SOURCE_LOOKUP, keys=None, loader="lookups"),
        Reader(
            pattern=item_entries.TEMPLATE_FILE,
            keys=frozenset({item_entries.TEMPLATE_KEY}),
            loader="items",
        ),
        Reader(pattern=SUBCLASS_LOOKUP, keys=None, loader="lookups"),
        Reader(pattern=BOOK_INDEX_FILE, keys=frozenset({"book"}), loader="books"),
        Reader(pattern="book/book-*.json", keys=frozenset({"data", "bookData"}), loader="books"),
        Reader(pattern=INDEX_FILE, keys=frozenset({"adventure"}), loader="adventures"),
        Reader(
            pattern="adventure/adventure-*.json",
            keys=frozenset({"data", "adventureData"}),
            loader="adventures",
        ),
        *(
            Reader(pattern=filename, keys=frozenset({"reference", "data"}), loader="screens")
            for filename, _key in SCREENS.values()
        ),
        Reader(pattern=LOOT_FILE, keys=None, loader="treasure"),
        Reader(pattern=ITEMS_FILE, keys=frozenset({"itemGroup"}), loader="treasure"),
        Reader(pattern=ENCOUNTERS_FILE, keys=frozenset({"encounter"}), loader="encounters"),
        Reader(pattern=SHAPES_FILE, keys=frozenset({"encounterShape"}), loader="encounters"),
        Reader(pattern=NAMES_FILE, keys=frozenset({"name"}), loader="npcs"),
        Reader(
            pattern=LIFE_FILE,
            keys=frozenset({"lifeTrinket", "lifeClass", "lifeBackground"}),
            loader="npcs",
        ),
        Reader(pattern=STATS_FILE, keys=frozenset({"cr"}), loader="creatures"),
        Reader(pattern=FEATURES_FILE, keys=frozenset({"monsterfeatures"}), loader="creatures"),
        Reader(
            pattern=BLOCKS_FILE,
            keys=frozenset({"makebrewCreatureTrait", "makebrewCreatureAction"}),
            loader="creatures",
        ),
    )


READERS = _readers()

# Keys Paravent knows and has not loaded yet (TECHSTACK.md §6b): reported, not warned.
# None are left; a key named here is listed as "not loaded yet".
PLANNED_KEYS: frozenset[str] = frozenset()

# Keys Paravent knows and leaves unread because they are not content, by key → why.
NOT_CONTENT_KEYS = {
    # Font files for writing a language's script, not text.
    "languageScript": "fonts",
    # Which loot table a variant item sits on; treasure rolls read the tables themselves.
    "linkedLootTables": "loot_links",
    # 5eTools's notes on its own data.
    "raceFluffMeta": "meta",
    # Short texts for 5eTools's card maker (makecards.json).
    "reducedItemProperty": "card_maker",
    "reducedItemType": "card_maker",
}

# Files that are not content, by pattern → why. Their keys are not checked.
NOT_CONTENT: tuple[tuple[str, str], ...] = (
    ("index.json", "site_index"),
    ("*/index.json", "site_index"),
    ("*/fluff-index.json", "site_index"),
    ("spells/sources.json", "site_index"),
    ("generated/*", "generated"),
    ("renderdemo.json", "demo"),
    # Sample texts for 5eTools's own converter page.
    ("converter.json", "demo"),
    (CHANGELOG_FILE, "changelog"),
    # Paravent's own tag catalogue, which once sat beside a corpus in data/reference/.
    ("5etools_tags.json", "paravent"),
)

# Files whose top level is not an object, as upstream ships them.
NOT_OBJECTS = frozenset({CHANGELOG_FILE})


def _matches(relative: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(relative, pattern)


def _not_content(relative: str) -> str | None:
    return next((why for pattern, why in NOT_CONTENT if _matches(relative, pattern)), None)


def _reader_of(relative: str, key: str) -> str | None:
    """Which loader reads ``key`` of this file, if any."""
    if key.endswith(FLUFF_SUFFIX):
        return "fluff"
    for reader in READERS:
        if _matches(relative, reader.pattern) and (reader.keys is None or key in reader.keys):
            return reader.loader
    return None


# -- the report ------------------------------------------------------------------------


class Severity(StrEnum):
    """How much a finding matters: errors fail the check, warnings ask for a look."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Kind(StrEnum):
    """What a finding is about; the UI names each (``corpus.kind.<kind>``)."""

    UNREADABLE = "unreadable"
    NOT_OBJECT = "not_object"
    BAD_ROW = "bad_row"
    INVALID = "invalid"
    UNRESOLVED_COPY = "unresolved_copy"
    UNKNOWN_COPY_MODE = "unknown_copy_mode"
    MISSING_TEMPLATE = "missing_template"
    MISSING_ITEM_ENTRY = "missing_item_entry"
    UNFILLED_ITEM_ENTRY = "unfilled_item_entry"
    COUNT_MISMATCH = "count_mismatch"
    FLUFF_INVALID = "fluff_invalid"
    FLUFF_UNRESOLVED = "fluff_unresolved"
    TABLE_UNREADABLE = "table_unreadable"
    UNKNOWN_KEY = "unknown_key"
    MISSING_FILE = "missing_file"
    TABLE_EMPTY = "table_empty"
    MISSING_BODY = "missing_body"
    UNINDEXED_BODY = "unindexed_body"
    LOOKUP_MISSING = "lookup_missing"
    UNKNOWN_TAG = "unknown_tag"
    DUPLICATE_KEY = "duplicate_key"
    FLUFF_ORPHANS = "fluff_orphans"
    DEAD_REFERENCE = "dead_reference"
    TRANSLATION_STALE = "translation_stale"
    TRANSLATION_ORPHAN = "translation_orphan"
    TRANSLATION_LOST = "translation_lost"
    TRANSLATION_TAGS = "translation_tags"
    TRANSLATION_LINK = "translation_link"
    GLOSSARY_LOST = "glossary_lost"
    LIBRARY_BOOK = "library_book"
    LIBRARY_REFERENCE = "library_reference"
    LIBRARY_LINK = "library_link"


# What the table made, held against the corpus: compared between checks on their own.
LIBRARY_KINDS = frozenset(
    {
        Kind.TRANSLATION_STALE,
        Kind.TRANSLATION_ORPHAN,
        Kind.TRANSLATION_LOST,
        Kind.TRANSLATION_TAGS,
        Kind.TRANSLATION_LINK,
        Kind.GLOSSARY_LOST,
        Kind.LIBRARY_BOOK,
        Kind.LIBRARY_REFERENCE,
        Kind.LIBRARY_LINK,
    }
)

SEVERITY: dict[Kind, Severity] = {
    Kind.UNREADABLE: Severity.ERROR,
    Kind.INVALID: Severity.ERROR,
    Kind.UNRESOLVED_COPY: Severity.ERROR,
    Kind.UNKNOWN_COPY_MODE: Severity.ERROR,
    Kind.COUNT_MISMATCH: Severity.ERROR,
    Kind.FLUFF_INVALID: Severity.ERROR,
    Kind.FLUFF_UNRESOLVED: Severity.ERROR,
    Kind.TABLE_UNREADABLE: Severity.ERROR,
    Kind.NOT_OBJECT: Severity.WARNING,
    Kind.MISSING_TEMPLATE: Severity.WARNING,
    Kind.MISSING_ITEM_ENTRY: Severity.WARNING,
    Kind.UNFILLED_ITEM_ENTRY: Severity.WARNING,
    Kind.BAD_ROW: Severity.WARNING,
    Kind.UNKNOWN_KEY: Severity.WARNING,
    Kind.MISSING_FILE: Severity.WARNING,
    Kind.TABLE_EMPTY: Severity.WARNING,
    Kind.MISSING_BODY: Severity.WARNING,
    Kind.UNINDEXED_BODY: Severity.WARNING,
    Kind.LOOKUP_MISSING: Severity.WARNING,
    Kind.UNKNOWN_TAG: Severity.WARNING,
    Kind.TRANSLATION_STALE: Severity.WARNING,
    Kind.TRANSLATION_ORPHAN: Severity.WARNING,
    Kind.TRANSLATION_LOST: Severity.WARNING,
    Kind.TRANSLATION_TAGS: Severity.WARNING,
    Kind.TRANSLATION_LINK: Severity.WARNING,
    Kind.LIBRARY_BOOK: Severity.WARNING,
    Kind.LIBRARY_REFERENCE: Severity.WARNING,
    Kind.LIBRARY_LINK: Severity.WARNING,
    Kind.GLOSSARY_LOST: Severity.INFO,
    Kind.DUPLICATE_KEY: Severity.INFO,
    Kind.FLUFF_ORPHANS: Severity.INFO,
    Kind.DEAD_REFERENCE: Severity.INFO,
}


class Finding(BaseModel):
    """One thing the check found: where, about what, and the technical detail."""

    kind: Kind
    file: str = ""
    subject: str = ""
    detail: str = ""
    # Where to mend it, for what lies in the library.
    href: str = ""

    @property
    def severity(self) -> Severity:
        """How much it matters, by its kind."""
        return SEVERITY[self.kind]


class FindingGroup(BaseModel):
    """The findings of one kind: how many, and the first :data:`MAX_PER_KIND`."""

    kind: Kind
    count: int
    items: list[Finding]

    @property
    def severity(self) -> Severity:
        """How much the kind matters."""
        return SEVERITY[self.kind]


class CategoryCount(BaseModel):
    """One compendium category: rows in the files against entries in the store."""

    category: str
    files: int
    rows: int
    invalid: int
    unresolved: int
    repeated: int
    made: int = 0
    loaded: int

    @property
    def expected(self) -> int:
        """What the store should hold: rows, less those rejected or repeating a key."""
        return self.rows - self.invalid - self.unresolved - self.repeated + self.made

    @property
    def matches(self) -> bool:
        """True when every difference between rows and entries is explained."""
        return self.expected == self.loaded


class KeyUse(BaseModel):
    """A top-level key across the corpus: who reads it, in how many files and rows."""

    key: str
    status: str  # "loaded", "skipped" (not content), "planned" or "unknown"
    # Why a skipped key is not content (a NOT_CONTENT_KEYS reason).
    reason: str = ""
    loader: str = ""
    files: list[str]
    rows: int


class UnreadFile(BaseModel):
    """A file no loader reads, and why that is fine (or not)."""

    file: str
    reason: str  # a NOT_CONTENT or NOT_CONTENT_KEYS reason, "planned" or "unknown"


class Changes(BaseModel):
    """What differs from the previous check."""

    since: str
    version_before: str = ""
    new_keys: list[str] = []
    gone_keys: list[str] = []
    rows: dict[str, tuple[int, int]] = {}
    new_fields: dict[str, list[str]] = {}
    # A kind of :data:`LIBRARY_KINDS` → its findings then and now.
    library: dict[str, tuple[int, int]] = {}

    @property
    def empty(self) -> bool:
        """True when nothing changed but, perhaps, the version."""
        return not (self.new_keys or self.gone_keys or self.rows or self.new_fields or self.library)


class CorpusReport(BaseModel):
    """Everything one check found."""

    root: str
    version: CorpusVersion | None
    checked_at: str
    seconds: float
    files: int
    files_read: int
    vtt_files: int
    entries: int
    fluff: int
    references: bool
    groups: list[FindingGroup]
    counts: list[CategoryCount]
    keys: list[KeyUse]
    unread: list[UnreadFile]
    # category → undeclared field → rows: kept verbatim, read by nothing.
    fields: dict[str, dict[str, int]]
    changes: Changes | None = None
    # False for a check of the corpus alone.
    library: bool = False

    def _count(self, severity: Severity) -> int:
        return sum(g.count for g in self.groups if g.severity is severity)

    @property
    def errors(self) -> int:
        """Findings that fail the check."""
        return self._count(Severity.ERROR)

    @property
    def warnings(self) -> int:
        """Findings worth a look."""
        return self._count(Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when nothing is broken."""
        return self.errors == 0

    @property
    def library_findings(self) -> int:
        """What in the table's own work no longer fits this corpus."""
        return sum(g.count for g in self.groups if g.kind in LIBRARY_KINDS)


# -- the check -------------------------------------------------------------------------


class _Findings:
    def __init__(self) -> None:
        self._by_kind: dict[Kind, list[Finding]] = defaultdict(list)
        self._counts: Counter[Kind] = Counter()

    def add(
        self, kind: Kind, file: str = "", subject: str = "", detail: str = "", href: str = ""
    ) -> None:
        self._counts[kind] += 1
        if len(self._by_kind[kind]) < MAX_PER_KIND:
            self._by_kind[kind].append(
                Finding(kind=kind, file=file, subject=subject, detail=detail, href=href)
            )

    def groups(self) -> list[FindingGroup]:
        order = list(Severity)
        return sorted(
            (
                FindingGroup(kind=k, count=n, items=self._by_kind[k])
                for k, n in self._counts.items()
            ),
            key=lambda g: (order.index(g.severity), list(Kind).index(g.kind)),
        )


def _subject(item: dict[str, Any]) -> str:
    name = item.get("name", "<unnamed>")
    source = item.get("source") or "?"
    return f"{name} ({source})"


def _problems(exc: ValidationError) -> str:
    """The first few of Pydantic's errors as ``path: message``."""
    parts = [
        f"{'.'.join(str(x) for x in error['loc']) or '(entry)'}: {error['msg']}"
        for error in exc.errors()[:3]
    ]
    more = exc.error_count() - len(parts)
    return "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")


class EntityTags:
    """Whether the entity tags of a text lead anywhere, among all the loaded books."""

    def __init__(self, compendium: CompendiumStore) -> None:
        """Bind to the loaded compendium."""
        # Imported here: the renderer is UI, and only these passes need it.
        from app.ui.tags import ENTITY_CATEGORY

        self._categories = ENTITY_CATEGORY
        self._pattern = re.compile(
            r"\{@(" + "|".join(map(re.escape, ENTITY_CATEGORY)) + r") ([^{}]*)\}"
        )
        self._resolver = compendium.resolver()
        self._resolved: dict[tuple[str, str, str | None], bool] = {}

    def dead(self, text: str) -> Iterator[tuple[str, str]]:
        """``(tag, "target|source")`` of each tag in ``text`` that resolves to nothing."""
        for tag, payload in self._pattern.findall(text):
            # Text read out of JSON keeps its escapes: {@creature Volothamp \"Volo\" Geddarm}
            target, source, _display = parse_reference(payload.replace('\\"', '"'), tag)
            key = (tag, target.lower(), source.lower() if source else None)
            if key not in self._resolved:
                self._resolved[key] = any(
                    self._resolver.resolve_parts(c, target, source).entry is not None
                    for c in self._categories[tag]
                )
            if not self._resolved[key]:
                yield tag, f"{target}|{source}" if source else target


class CorpusCheck:
    """One pass over the corpus beside the stores that loaded it."""

    def __init__(
        self,
        reference: Path | None,
        homebrew: Path | None,
        compendium: CompendiumStore,
        fluff: FluffStore,
        lookups: LookupTables,
        library: Callable[[_Findings], None] | None = None,
    ) -> None:
        """Check the files under both roots against the stores loaded from them;
        ``library`` then holds the table's own work against them."""
        self._reference = reference
        self._roots = [
            (root, label) for root, label in ((reference, ""), (homebrew, "homebrew/")) if root
        ]
        self._compendium = compendium
        self._fluff = fluff
        self._lookups = lookups
        self._library = library
        self._found = _Findings()
        # (root, relative path) → the parsed object
        self._data: dict[tuple[Path, str], dict[str, Any]] = {}

    def run(
        self, *, references: bool = False, previous: CorpusReport | None = None
    ) -> CorpusReport:
        """Check everything; ``references`` also reads every tag (a few seconds more)."""
        started = time.perf_counter()
        files, vtt = self._scan()
        keys, unread, read = self._keys()
        counts, fields = self._entries()
        self._fluff_check()
        self._lookups_check()
        self._tables_check()
        self._published_check()
        if references:
            self._references_check()
        if self._library is not None:
            self._library(self._found)
        report = CorpusReport(
            root=str(self._reference or ""),
            version=corpus_version(self._reference),
            checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
            seconds=round(time.perf_counter() - started, 2),
            files=files,
            files_read=read,
            vtt_files=vtt,
            entries=sum(self._compendium.count(c) for c in self._compendium.categories()),
            fluff=self._fluff.report.total,
            references=references,
            groups=self._found.groups(),
            counts=counts,
            keys=keys,
            unread=unread,
            fields=fields,
            library=self._library is not None,
        )
        if previous is not None:
            report.changes = compare(previous, report)
        return report

    # -- 1. files ------------------------------------------------------------------------

    def _scan(self) -> tuple[int, int]:
        files = vtt = 0
        for root, label in self._roots:
            for path in iter_json_files(root):
                relative = path.relative_to(root).as_posix()
                files += 1
                if is_vtt_payload(path):
                    vtt += 1
                    continue
                try:
                    data = read_json(path)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    self._found.add(Kind.UNREADABLE, label + relative, detail=str(exc))
                    continue
                if isinstance(data, dict):
                    self._data[(root, relative)] = data
                elif path.name not in NOT_OBJECTS:
                    self._found.add(Kind.NOT_OBJECT, label + relative, detail=type(data).__name__)
        return files, vtt

    def _label(self, root: Path) -> str:
        return next(label for r, label in self._roots if r == root)

    # -- 2. keys -------------------------------------------------------------------------

    def _keys(self) -> tuple[list[KeyUse], list[UnreadFile], int]:
        uses: dict[str, dict[str, Any]] = {}
        unread: list[UnreadFile] = []
        read = 0
        for (root, relative), data in sorted(self._data.items(), key=lambda x: str(x[0])):
            shown = self._label(root) + relative
            loaders = {key: _reader_of(relative, key) for key in data if key != "_meta"}
            why = _not_content(relative)
            if why is not None:
                # A lookup in generated/ is read whole; its keys are book codes, not
                # categories, and listing them would only bury the real ones.
                if any(loaders.values()):
                    read += 1
                else:
                    unread.append(UnreadFile(file=shown, reason=why))
                continue
            statuses: set[str] = set()
            for key, loader in loaders.items():
                value = data[key]
                skipped = NOT_CONTENT_KEYS.get(key, "")
                status = (
                    "loaded"
                    if loader
                    else "skipped"
                    if skipped
                    else "planned"
                    if key in PLANNED_KEYS
                    else "unknown"
                )
                statuses.add(status)
                use = uses.setdefault(
                    key,
                    {
                        "status": status,
                        "loader": loader or "",
                        "reason": skipped if status == "skipped" else "",
                        "files": [],
                        "rows": 0,
                    },
                )
                # A key read in one file and not another (``data`` of renderdemo) counts
                # as read; the unread file is listed on its own.
                if status == "loaded":
                    use["status"], use["loader"], use["reason"] = status, loader, ""
                use["files"].append(shown)
                use["rows"] += len(value) if isinstance(value, list) else 1
                if status == "unknown":
                    self._found.add(Kind.UNKNOWN_KEY, shown, key)
            if "loaded" in statuses:
                read += 1
            else:
                reason = (
                    "unknown"
                    if "unknown" in statuses
                    else "planned"
                    if "planned" in statuses
                    else next((NOT_CONTENT_KEYS[k] for k in loaders if k in NOT_CONTENT_KEYS), "")
                )
                unread.append(UnreadFile(file=shown, reason=reason))
        keys = [KeyUse(key=k, **v) for k, v in sorted(uses.items())]
        return keys, unread, read

    # -- 3, 4. entries and counts ---------------------------------------------------------

    def _rows(
        self, category: Category
    ) -> tuple[list[tuple[dict[str, Any], str, int]], list[dict[str, Any]], int]:
        """``(row, file, root index)`` of a category, its ``_copy``-only rows, its files."""
        rows: list[tuple[dict[str, Any], str, int]] = []
        aux: list[dict[str, Any]] = []
        files = 0
        for index, (root, label) in enumerate(self._roots):
            for pattern in category.globs:
                for path in expand_glob(root, pattern):
                    data = self._data.get((root, path.relative_to(root).as_posix()))
                    if data is None:
                        continue
                    shown = label + path.relative_to(root).as_posix()
                    value = data.get(category.json_key)
                    if isinstance(value, list) and value:
                        files += 1
                        for row in value:
                            if isinstance(row, dict):
                                rows.append((row, shown, index))
                            else:
                                self._found.add(
                                    Kind.BAD_ROW, shown, category.json_key, type(row).__name__
                                )
                    for aux_key in category.aux_keys:
                        aux.extend(x for x in data.get(aux_key) or [] if isinstance(x, dict))
            for pattern in category.aux_globs:
                for path in expand_glob(root, pattern):
                    data = self._data.get((root, path.relative_to(root).as_posix())) or {}
                    for aux_key in category.aux_keys:
                        aux.extend(x for x in data.get(aux_key) or [] if isinstance(x, dict))
        return rows, aux, files

    def _entries(self) -> tuple[list[CategoryCount], dict[str, dict[str, int]]]:
        counts: list[CategoryCount] = []
        fields: dict[str, dict[str, int]] = {}
        for category in CATEGORIES:
            rows, aux, files = self._rows(category)
            views = (category.copy_view(item) for item in [*(r for r, _f, _i in rows), *aux])
            index = {
                composite_key(view): view
                for view in views
                if view.get("name") and view.get("source")
            }
            resolver = CopyResolver(index)
            invalid = unresolved = repeated = 0
            seen: dict[str, tuple[str, int]] = {}
            extra: Counter[str] = Counter()
            for row, file, root_index in rows:
                item = row
                if category.supports_copy and row.get("_copy"):
                    try:
                        item = category.resolve_copy(resolver, row)
                    except (CopyResolutionError, ValueError) as exc:
                        unresolved += 1
                        self._found.add(Kind.UNRESOLVED_COPY, file, _subject(row), str(exc))
                        continue
                if category.name == "magicvariant":
                    item = generic_entry(item)
                try:
                    model = category.model.model_validate(item)
                except ValidationError as exc:
                    invalid += 1
                    self._found.add(
                        Kind.INVALID, file, f"{category.name}: {_subject(item)}", _problems(exc)
                    )
                    continue
                extra.update((model.model_extra or {}).keys())
                key = key_of(model)
                if key in seen:
                    repeated += 1
                    first_file, first_root = seen[key]
                    # Homebrew replacing a corpus entry is the point of homebrew.
                    if first_root == root_index:
                        self._found.add(
                            Kind.DUPLICATE_KEY,
                            file,
                            f"{category.name}: {_subject(item)}",
                            f"also in {first_file}" if first_file != file else "twice in this file",
                        )
                seen[key] = (file, root_index)
            for template in sorted(resolver.missing_templates):
                self._found.add(Kind.MISSING_TEMPLATE, subject=category.name, detail=template)
            for mode in sorted(resolver.unknown_modes):
                self._found.add(Kind.UNKNOWN_COPY_MODE, subject=category.name, detail=mode)
            made = self._compendium.report.variants_made if category.name == "item" else 0
            count = CategoryCount(
                category=category.name,
                files=files,
                rows=len(rows),
                invalid=invalid,
                unresolved=unresolved,
                repeated=repeated,
                made=made,
                loaded=self._compendium.count(category.name),
            )
            if not count.matches:
                self._found.add(
                    Kind.COUNT_MISMATCH,
                    subject=category.name,
                    detail=f"expected {count.expected}, the store holds {count.loaded}",
                )
            counts.append(count)
            if extra:
                fields[category.name] = dict(sorted(extra.items()))
        loaded = self._compendium.report
        for reference in sorted(loaded.missing_item_entries):
            self._found.add(Kind.MISSING_ITEM_ENTRY, item_entries.TEMPLATE_FILE, reference)
        for gap in sorted(loaded.unfilled_item_entries):
            self._found.add(Kind.UNFILLED_ITEM_ENTRY, subject=gap)
        return counts, fields

    # -- 5. fluff, lookups, tables, published text -----------------------------------------

    def _fluff_check(self) -> None:
        report = self._fluff.report
        for error in report.errors:
            self._found.add(Kind.FLUFF_INVALID, detail=error)
        for error in report.unresolved_copies:
            self._found.add(Kind.FLUFF_UNRESOLVED, detail=error)
        known = set(self._compendium.categories())
        for category in self._fluff.categories:
            if category not in known:
                continue  # fluff of a category not loaded yet (recipes): nothing to join
            keys = (_raw_key(m) for m in self._compendium.all(category))
            orphans = self._fluff.count_orphans(category, keys)
            if orphans:
                self._found.add(
                    Kind.FLUFF_ORPHANS,
                    subject=f"{category}{FLUFF_SUFFIX}",
                    detail=f"{orphans} of {self._fluff.count(category)}",
                )

    def _lookups_check(self) -> None:
        if self._reference is None:
            return
        for relative in self._lookups.missing:
            self._found.add(Kind.MISSING_FILE, relative)
        spells = self._lookups.spell_sources
        missing: Counter[str] = Counter()
        for source in spells.sources:
            for name in spells.names(source):
                if self._compendium.find("spell", name, source) is None:
                    missing[source] += 1
        for source, count in sorted(missing.items()):
            self._found.add(
                Kind.LOOKUP_MISSING,
                SPELL_SOURCE_LOOKUP,
                source.upper(),
                f"{count} spell(s) the lookup lists are not loaded",
            )

    def _tables_check(self) -> None:
        root = self._reference
        if root is None:
            return
        loot, encounters = LootTables(root), EncounterTables(root)
        npcs, creatures = NpcTables(root), CreatureTables(root)
        checks: tuple[tuple[str, Any], ...] = (
            (LOOT_FILE, lambda: loot.books()),
            (ENCOUNTERS_FILE, lambda: encounters.all()),
            (SHAPES_FILE, lambda: encounters.shapes()),
            (NAMES_FILE, lambda: npcs.ancestries()),
            (LIFE_FILE, lambda: npcs.trinkets()),
            (STATS_FILE, lambda: creatures.rows()),
            (FEATURES_FILE, lambda: creatures.features()),
            (BLOCKS_FILE, lambda: creatures.traits() or creatures.actions()),
        )
        for filename, read in checks:
            if not (root / filename).is_file():
                self._found.add(Kind.MISSING_FILE, filename)
                continue
            try:
                found = read()
            except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
                self._found.add(
                    Kind.TABLE_UNREADABLE, filename, detail=f"{type(exc).__name__}: {exc}"
                )
                continue
            if not found:
                self._found.add(Kind.TABLE_EMPTY, filename)

    def _published_check(self) -> None:
        root = self._reference
        if root is None:
            return
        for kind, catalog, folder in (
            ("book", PublishedBooks(root), "book"),
            ("adventure", PublishedAdventures(root), "adventure"),
        ):
            indexed = {x.id.lower(): x for x in catalog.all()}
            bodies = {
                p.stem.removeprefix(f"{kind}-").lower(): p
                for p in sorted((root / folder).glob(f"{kind}-*.json"))
            }
            index_file = BOOK_INDEX_FILE if kind == "book" else INDEX_FILE
            for ident in sorted(indexed.keys() - bodies.keys()):
                self._found.add(
                    Kind.MISSING_BODY,
                    index_file,
                    indexed[ident].name,
                    f"{folder}/{kind}-{ident}.json",
                )
            for ident in sorted(bodies.keys() - indexed.keys()):
                self._found.add(Kind.UNINDEXED_BODY, f"{folder}/{bodies[ident].name}")

    # -- 6. references ---------------------------------------------------------------------

    def _references_check(self) -> None:
        # Imported here: the renderer is UI, and only this optional pass needs it.
        from app.ui.tags import ENTITY_CATEGORY, catalogue

        known = set(catalogue()) | set(ENTITY_CATEGORY) | {"card"}
        any_tag = re.compile(r"\{@([A-Za-z0-9]+)[ }]")
        entity = EntityTags(self._compendium)
        unknown: dict[str, tuple[int, str]] = {}
        dead: dict[tuple[str, str], tuple[int, str]] = {}
        for owner, text in self._texts():
            for name in any_tag.findall(text):
                if name not in known:
                    count, first = unknown.get(name, (0, owner))
                    unknown[name] = (count + 1, first)
            for found in entity.dead(text):
                count, first = dead.get(found, (0, owner))
                dead[found] = (count + 1, first)
        for name, (count, first) in sorted(unknown.items(), key=lambda x: -x[1][0]):
            self._found.add(
                Kind.UNKNOWN_TAG, subject=f"{{@{name}}}", detail=f"{count}× — first in {first}"
            )
        for (tag, target), (count, first) in sorted(dead.items(), key=lambda x: -x[1][0]):
            self._found.add(
                Kind.DEAD_REFERENCE,
                subject=f"{{@{tag} {target}}}",
                detail=f"{count}× — first in {first}",
            )

    def _texts(self) -> Iterator[tuple[str, str]]:
        """``(owner, text)`` of everything readers see: entries, then the files of
        descriptions, rulebooks and adventures, which are no entries of the compendium."""
        for category, entry in self._compendium:
            owner = f"{category}: {getattr(entry, 'name', '')} ({getattr(entry, 'source', '')})"
            yield owner, entry.model_dump_json(by_alias=True, exclude_none=True)
        for (root, relative), data in sorted(self._data.items(), key=lambda x: str(x[0])):
            name = relative.rsplit("/", 1)[-1]
            if relative.startswith(("book/", "adventure/")) or name.startswith("fluff-"):
                yield self._label(root) + relative, json.dumps(data, ensure_ascii=False)


def _raw_key(model: Any) -> str:
    """The key fluff is joined by: ``name|source`` plus the subclass discriminators."""
    raw = {"name": getattr(model, "name", ""), "source": getattr(model, "source", "")}
    for field in ("className", "classSource"):
        value = getattr(model, field, None)
        if value:
            raw[field] = value
    return composite_key(raw)


# -- comparing and keeping reports -----------------------------------------------------


def compare(before: CorpusReport, after: CorpusReport) -> Changes:
    """What changed between two checks: keys, row counts and undeclared fields."""
    keys_before = {k.key for k in before.keys}
    keys_after = {k.key for k in after.keys}
    rows_before = {c.category: c.rows for c in before.counts}
    rows = {
        c.category: (rows_before.get(c.category, 0), c.rows)
        for c in after.counts
        if rows_before.get(c.category, 0) != c.rows
    }
    new_fields = {
        category: sorted(set(found) - set(before.fields.get(category, {})))
        for category, found in after.fields.items()
    }
    counts_before = {g.kind: g.count for g in before.groups}
    counts_after = {g.kind: g.count for g in after.groups}
    library = {
        kind.value: (counts_before.get(kind, 0), counts_after.get(kind, 0))
        for kind in Kind
        if kind in LIBRARY_KINDS and counts_before.get(kind, 0) != counts_after.get(kind, 0)
    }
    return Changes(
        since=before.checked_at,
        version_before=before.version.version if before.version else "",
        new_keys=sorted(keys_after - keys_before),
        gone_keys=sorted(keys_before - keys_after),
        rows=rows,
        new_fields={c: f for c, f in new_fields.items() if f},
        library=library if before.library and after.library else {},
    )


def load_report(path: Path) -> CorpusReport | None:
    """The report saved at ``path``, or None when there is none (or it is from an older
    Paravent that wrote it differently)."""
    try:
        data = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if data is None:
        return None
    try:
        return CorpusReport.model_validate(data)
    except ValidationError:
        return None


def save_report(path: Path, report: CorpusReport) -> None:
    """Keep the report as the one the next check compares with."""
    write_json(path, report.model_dump(mode="json"))


class CorpusHealth:
    """The check as the settings page runs it: in the background, its report kept in
    ``reference-cache/`` so the CLI's run shows there too."""

    def __init__(
        self,
        reference: Path | None,
        homebrew: Path | None,
        cache_dir: Path,
        stores: Any,
    ) -> None:
        """``stores`` has the registry's ``compendium``, ``fluff`` and ``lookups``."""
        self._reference = reference
        self._homebrew = homebrew
        self.path = cache_dir / REPORT_FILE
        self._stores = stores
        self._lock = threading.Lock()
        self._running = False
        self.failure: str | None = None

    @property
    def running(self) -> bool:
        """True while a check is under way."""
        return self._running

    def last(self) -> CorpusReport | None:
        """The newest report, from this server or the command line."""
        return load_report(self.path)

    def version(self) -> CorpusVersion | None:
        """The corpus's version, read now (it changes with a re-import)."""
        return corpus_version(self._reference)

    def start(self, *, references: bool) -> bool:
        """Run a check in the background; False when one is already running."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self.failure = None
        threading.Thread(target=self._run, args=(references,), daemon=True).start()
        return True

    def run(self, *, references: bool) -> CorpusReport:
        """Run a check now, keep and return its report."""
        from app.services.library_check import LibraryCheck

        check = CorpusCheck(
            self._reference,
            self._homebrew,
            self._stores.compendium,
            self._stores.fluff,
            self._stores.lookups,
            LibraryCheck(self._stores),
        )
        report = check.run(references=references, previous=self.last())
        save_report(self.path, report)
        return report

    def _run(self, references: bool) -> None:
        try:
            self.run(references=references)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.exception("Corpus check failed")
            self.failure = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._running = False


def iter_findings(report: CorpusReport, severity: Severity | None = None) -> Iterable[FindingGroup]:
    """The report's groups, optionally of one severity."""
    return (g for g in report.groups if severity is None or g.severity is severity)


__all__ = [
    "Changes",
    "CategoryCount",
    "CorpusCheck",
    "CorpusHealth",
    "CorpusReport",
    "CorpusVersion",
    "EntityTags",
    "Finding",
    "FindingGroup",
    "Kind",
    "KeyUse",
    "LIBRARY_KINDS",
    "READERS",
    "Severity",
    "UnreadFile",
    "compare",
    "corpus_version",
    "iter_findings",
    "load_report",
    "save_report",
]
