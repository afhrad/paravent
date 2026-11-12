"""Published adventures and rulebooks from the corpus, read on demand (ADVENTURES.md §7).

The corpus holds 99 adventures in two parts, both read in place and never written:

* ``adventures.json`` — **the index**: name, id, source, level range, publication date and
  the table of contents of each adventure;
* ``adventure/adventure-<id>.json`` — **the body**: a ``data`` array of sections, each a
  tree of 5eTools entries. Every section and most named entries carry a stable ``id``
  (``"00e"`` is *Goblin Ambush* in LMoP); those ids are what the gamemaster's notes
  anchor to.

Bodies are 24 KB to 2.5 MB each, so they are **not** loaded at start: a body is read the
first time a chapter based on it is shown and kept in a small cache, which a corpus reload
clears. The model is a thin typed view over the corpus's own dicts rather than a Pydantic
tree — the reference side is lossless, and nothing here validates or rewrites it.

**Rulebooks** are laid out the same way (``books.json`` and ``book/book-<id>.json``),
and are read by :class:`PublishedBooks` (TECHSTACK.md §6b, phase 3).

Note the two names an adventure has: ``id`` names its body file and is what an
adventure's ``based_on`` stores; ``source`` is what its tags cite (``{@creature
Name|SOURCE}``). They differ for two adventures (``MOT-NSS`` cites ``MOT``).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INDEX_FILE = "adventures.json"
BODY_DIR = "adventure"
BOOK_INDEX_FILE = "books.json"
BOOK_DIR = "book"
CACHE_SIZE = 6


@dataclass(frozen=True)
class PublishedChapter:
    """One entry of an adventure's (or a book's) table of contents."""

    name: str
    ordinal: str | None  # "Part 1", "Appendix A", or None
    # The section headings listed under it (books list them; adventures mostly do too).
    headers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishedAdventure:
    """An adventure in the corpus's index."""

    id: str
    source: str
    name: str
    level_start: int | None
    level_end: int | None
    published: str
    storyline: str
    contents: tuple[PublishedChapter, ...]


@dataclass(frozen=True)
class Section:
    """A top-level section of a body — what a chapter shows."""

    id: str
    name: str
    entry: dict[str, Any]


@dataclass
class AdventureBody:
    """A loaded body: its sections, and every entry with an id."""

    adventure: PublishedAdventure
    sections: tuple[Section, ...]
    _ids: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for section in self.sections:
            for entry in _walk(section.entry):
                ident = entry.get("id")
                if isinstance(ident, str) and ident not in self._ids:
                    self._ids[ident] = (section.id, entry)

    def section(self, section_id: str) -> Section | None:
        """The section with this id."""
        return next((s for s in self.sections if s.id == section_id), None)

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        """The entry with this id, wherever it lies."""
        found = self._ids.get(entry_id)
        return found[1] if found else None

    def section_of(self, entry_id: str) -> str | None:
        """Which section an entry id lies in."""
        found = self._ids.get(entry_id)
        return found[0] if found else None

    def places(self, section_id: str) -> list[tuple[str, str, int]]:
        """The named entries of a section as ``(id, name, depth)``: where notes can go."""
        section = self.section(section_id)
        if section is None:
            return []
        out: list[tuple[str, str, int]] = []

        def visit(entry: Any, depth: int) -> None:
            if isinstance(entry, list):
                for child in entry:
                    visit(child, depth)
                return
            if not isinstance(entry, dict):
                return
            ident, name = entry.get("id"), entry.get("name")
            if isinstance(ident, str) and isinstance(name, str) and entry is not section.entry:
                out.append((ident, name, depth))
                depth += 1
            for key in ("entries", "items"):
                if key in entry:
                    visit(entry[key], depth)

        visit(section.entry, 0)
        return out


def _walk(entry: Any) -> Iterator[dict[str, Any]]:
    if isinstance(entry, dict):
        yield entry
        for value in entry.values():
            yield from _walk(value)
    elif isinstance(entry, list):
        for value in entry:
            yield from _walk(value)


def _ordinal(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    kind, ident = raw.get("type"), raw.get("identifier")
    if not kind or ident is None:
        return None
    return f"{str(kind).title()} {ident}"


def heading_anchor(name: str) -> str:
    """The anchor a book heading gets: ``"Grappling"`` → ``"h-grappling"``."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"h-{slug}" if slug else "h"


# Named entries this deep (chapter = 0) are headings; deeper ones print inline, as the
# books print them.
MAX_HEADING_DEPTH = 4


def is_heading(entry: Any, depth: int) -> bool:
    """A named section or entries block shallow enough to be a heading of its own."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and entry.get("type", "entries") in ("section", "entries")
        and depth <= MAX_HEADING_DEPTH
        and isinstance(entry.get("entries"), list)
    )


class Anchors:
    """Anchors handed out in reading order, unique within one chapter."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def take(self, name: str) -> str:
        """The next anchor for a heading called ``name`` ("h-cover", then "h-cover-2")."""
        base = heading_anchor(name)
        anchor, n = base, 2
        while anchor in self._used:
            anchor, n = f"{base}-{n}", n + 1
        self._used.add(anchor)
        return anchor


def chapter_headings(section: Section) -> list[tuple[int, str, str]]:
    """A chapter's headings as ``(depth, name, anchor)``, as its page renders them.

    The page (:mod:`app.ui.rulebook_pages`) and the search walk the same rule, so a
    search result's anchor is one the page has.
    """
    anchors = Anchors()
    out: list[tuple[int, str, str]] = []

    def visit(entry: Any, depth: int) -> None:
        if not is_heading(entry, depth):
            return
        out.append((depth, entry["name"], anchors.take(entry["name"])))
        for child in entry["entries"]:
            visit(child, depth + 1)

    for child in section.entry.get("entries") or []:
        visit(child, 1)
    return out


def _holds_table(node: Any, wanted: str) -> bool:
    if isinstance(node, list):
        return any(_holds_table(x, wanted) for x in node)
    if not isinstance(node, dict):
        return False
    if node.get("type") == "table" and wanted in (
        str(node.get("caption", "")).lower(),
        str(node.get("name", "")).lower(),
    ):
        return True
    return any(_holds_table(v, wanted) for v in node.values() if isinstance(v, list | dict))


def table_anchor(section: Section, caption: str) -> str | None:
    """The anchor of the heading a table sits under, by its caption: ``""`` when it sits
    before the chapter's first heading, None when the chapter has no such table.

    Walks the headings as :func:`chapter_headings` does, so the anchor is one the page has.
    """
    anchors = Anchors()
    wanted = caption.lower()

    def visit(entry: Any, depth: int, current: str) -> str | None:
        if is_heading(entry, depth):
            anchor = anchors.take(entry["name"])
            for child in entry["entries"]:
                found = visit(child, depth + 1, anchor)
                if found is not None:
                    return found
            return None
        return current if _holds_table(entry, wanted) else None

    for child in section.entry.get("entries") or []:
        found = visit(child, 1, "")
        if found is not None:
            return found
    return None


def _contents(item: dict[str, Any]) -> tuple[PublishedChapter, ...]:
    """The table of contents of an index entry, with each chapter's headings."""
    out = []
    for chapter in item.get("contents", []):
        if not isinstance(chapter, dict):
            continue
        headers = tuple(
            str(h if isinstance(h, str) else h.get("header", ""))
            for h in chapter.get("headers") or []
            if isinstance(h, str) or (isinstance(h, dict) and h.get("header"))
        )
        out.append(
            PublishedChapter(
                str(chapter.get("name", "")), _ordinal(chapter.get("ordinal")), headers
            )
        )
    return tuple(out)


def body_sections(raw: Any) -> list[Any]:
    """The sections of a book or adventure file. Newer releases wrap them, as
    ``{"bookData": [{"id": …, "data": […]}]}`` (``adventureData`` likewise)."""
    if not isinstance(raw, dict):
        return []
    wrapped = raw.get("bookData") or raw.get("adventureData")
    if isinstance(wrapped, list):
        return [s for part in wrapped if isinstance(part, dict) for s in part.get("data") or []]
    return raw.get("data") or []


def _sections(raw: Any) -> tuple[Section, ...]:
    return tuple(
        Section(str(s.get("id", index)), str(s.get("name", "")), s)
        for index, s in enumerate(body_sections(raw))
        if isinstance(s, dict)
    )


def _level(raw: Any, key: str) -> int | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, int) else None


class PublishedAdventures:
    """The corpus's adventures: the index at hand, bodies when asked for."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._index: dict[str, PublishedAdventure] | None = None
        self._bodies: OrderedDict[str, AdventureBody] = OrderedDict()
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget everything read, after the corpus changed."""
        with self._lock:
            self._index = None
            self._bodies.clear()

    # -- the index ---------------------------------------------------------------------

    def _load_index(self) -> dict[str, PublishedAdventure]:
        if self._root is None:
            return {}
        try:
            raw = json.loads((self._root / INDEX_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("Adventure index not read: %s", exc)
            return {}
        out: dict[str, PublishedAdventure] = {}
        for item in raw.get("adventure", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            contents = _contents(item)
            ident = str(item["id"])
            out[ident.lower()] = PublishedAdventure(
                id=ident,
                source=str(item.get("source") or ident),
                name=str(item.get("name", ident)),
                level_start=_level(item.get("level"), "start"),
                level_end=_level(item.get("level"), "end"),
                published=str(item.get("published", "")),
                storyline=str(item.get("storyline", "")),
                contents=contents,
            )
        return out

    def _ensure_index(self) -> dict[str, PublishedAdventure]:
        with self._lock:
            if self._index is None:
                self._index = self._load_index()
            return self._index

    def all(self) -> list[PublishedAdventure]:
        """Every adventure in the index, by storyline and date."""
        return sorted(self._ensure_index().values(), key=lambda a: (a.published, a.name))

    def get(self, adventure_id: str | None) -> PublishedAdventure | None:
        """One adventure by its id (any case)."""
        return self._ensure_index().get(adventure_id.lower()) if adventure_id else None

    # -- bodies ------------------------------------------------------------------------

    def body(self, adventure_id: str | None) -> AdventureBody | None:
        """An adventure's sections, read on first use; None when it has no body file."""
        adventure = self.get(adventure_id)
        if adventure is None or self._root is None:
            return None
        key = adventure.id.lower()
        with self._lock:
            if key in self._bodies:
                self._bodies.move_to_end(key)
                return self._bodies[key]
        path = self._root / BODY_DIR / f"adventure-{key}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Adventure body %s not read: %s", path, exc)
            return None
        loaded = AdventureBody(adventure, _sections(raw))
        with self._lock:
            self._bodies[key] = loaded
            while len(self._bodies) > CACHE_SIZE:
                self._bodies.popitem(last=False)
        return loaded


# -- rulebooks ------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishedBook:
    """A rulebook in the corpus's index."""

    id: str
    source: str
    name: str
    group: str  # "core", "supplement", "setting", … as books.json says
    published: str
    contents: tuple[PublishedChapter, ...]


@dataclass(frozen=True)
class BookBody:
    """A loaded rulebook: its chapters, in order."""

    book: PublishedBook
    sections: tuple[Section, ...]

    def section(self, index: int) -> Section | None:
        """The chapter at ``index`` (books are read by position: ids are not unique)."""
        return self.sections[index] if 0 <= index < len(self.sections) else None


class PublishedBooks:
    """The corpus's rulebooks: the index at hand, bodies when asked for."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._index: dict[str, PublishedBook] | None = None
        self._bodies: OrderedDict[str, BookBody] = OrderedDict()
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget everything read, after the corpus changed."""
        with self._lock:
            self._index = None
            self._bodies.clear()

    def _load_index(self) -> dict[str, PublishedBook]:
        if self._root is None:
            return {}
        try:
            raw = json.loads((self._root / BOOK_INDEX_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("Book index not read: %s", exc)
            return {}
        out: dict[str, PublishedBook] = {}
        for item in raw.get("book", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            ident = str(item["id"])
            out[ident.lower()] = PublishedBook(
                id=ident,
                source=str(item.get("source") or ident),
                name=str(item.get("name", ident)),
                group=str(item.get("group", "other")),
                published=str(item.get("published", "")),
                contents=_contents(item),
            )
        return out

    def _ensure_index(self) -> dict[str, PublishedBook]:
        with self._lock:
            if self._index is None:
                self._index = self._load_index()
            return self._index

    def all(self) -> list[PublishedBook]:
        """Every rulebook in the index, by publication date."""
        return sorted(self._ensure_index().values(), key=lambda b: (b.published, b.name))

    def get(self, book_id: str | None) -> PublishedBook | None:
        """One book by its id (any case)."""
        return self._ensure_index().get(book_id.lower()) if book_id else None

    def body(self, book_id: str | None) -> BookBody | None:
        """A book's chapters, read on first use; None when it has no body file."""
        book = self.get(book_id)
        if book is None or self._root is None:
            return None
        key = book.id.lower()
        with self._lock:
            if key in self._bodies:
                self._bodies.move_to_end(key)
                return self._bodies[key]
        path = self._root / BOOK_DIR / f"book-{key}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Book body %s not read: %s", path, exc)
            return None
        loaded = BookBody(book, _sections(raw))
        with self._lock:
            self._bodies[key] = loaded
            while len(self._bodies) > CACHE_SIZE:
                self._bodies.popitem(last=False)
        return loaded


# -- the reference screens ----------------------------------------------------------------

SCREENS = {
    "dm": ("generated/bookref-dmscreen.json", "bookref-dmscreen"),
    "quick": ("generated/bookref-quick.json", "bookref-quick"),
}


class ReferenceScreens:
    """5eTools's two reference screens: the DM's, and the players' quick reference.

    Generated from the 2014 books into ``generated/bookref-*.json`` — their sections are
    copies of PHB and DMG text, which is why they are read as a page of their own and
    kept out of search and the backlinks, where they would only repeat the books.
    """

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._bodies: dict[str, BookBody] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget what was read, after the corpus changed."""
        with self._lock:
            self._bodies.clear()

    def body(self, screen: str) -> BookBody | None:
        """A screen's chapters (``"dm"`` or ``"quick"``); None when the corpus lacks it."""
        if screen not in SCREENS or self._root is None:
            return None
        with self._lock:
            if screen in self._bodies:
                return self._bodies[screen]
        filename, key = SCREENS[screen]
        try:
            raw = json.loads((self._root / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("Reference screen %s not read: %s", filename, exc)
            return None
        meta = (raw.get("reference") or {}).get(key) or {}
        chapters = (raw.get("data") or {}).get(key) or []
        contents = _contents(meta)
        sections = tuple(
            # A chapter's name is only in the contents; on the entry it can be translated.
            Section(str(index), name, {**chapter, "name": name})
            for index, chapter in enumerate(chapters)
            if isinstance(chapter, dict)
            for name in [contents[index].name if index < len(contents) else str(index)]
        )
        book = PublishedBook(screen, "", str(meta.get("name", key)), "screen", "", contents)
        loaded = BookBody(book, sections)
        with self._lock:
            self._bodies[screen] = loaded
        return loaded


__all__ = [
    "SCREENS",
    "ReferenceScreens",
    "AdventureBody",
    "BookBody",
    "PublishedBook",
    "Anchors",
    "chapter_headings",
    "table_anchor",
    "heading_anchor",
    "is_heading",
    "PublishedBooks",
    "PublishedAdventure",
    "PublishedAdventures",
    "PublishedChapter",
    "Section",
]
