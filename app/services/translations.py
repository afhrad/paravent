"""Translated text over the English corpus (documentation/TRANSLATION.md §2, §5).

A translation is a **text-only overlay**: the English record keeps its structure,
mechanics, tags and source, and a translation replaces some of its text passages. A
passage is one string in the record's text — a paragraph, a list item, a table cell, a
section's name — addressed by its **path** in the English record::

    library/i18n/de/spell.json
    {"Fireball|PHB": {"passages": {
        "entries/0": {"text": "Ein greller Lichtblitz …", "en": "1f3a9c…", "draft": true},
        "entriesHigherLevel/0/entries/0": {"text": "…", "en": "…"}}}}

Resolution is per passage: ``translation[path] ?? english[path]``, so a half-translated
entry is a normal state. Each passage keeps a hash of the English it was made from
(``en``); when the corpus changes that text, the translation still shows but is
**stale**, and a passage whose path is gone is an **orphan** — counted, never dropped.
A machine-made passage is a **draft** until someone confirms it (§7).

Rulebook and adventure chapters are long, so each book has a file of its own:
``library/i18n/de/book/<id>.json``, keyed by chapter index. So does each reference
screen: ``library/i18n/de/screen/dm.json``.

The files are re-read when they change on disk, like the glossary.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.store.jsonfile import read_json, write_json

# Where a record's text lives. Names of entries themselves belong to the glossary
# (§4); a chapter's name is its own text, so chapters add "name".
ENTRY_FIELDS = (
    "entries",
    "entriesHigherLevel",
    "entry",
    "trait",
    "action",
    "bonus",
    "reaction",
    "legendary",
    "mythic",
    "legendaryHeader",
    "mythicHeader",
    "lairActions",
    "regionalEffects",
    "spellcasting",
    "headerEntries",
    "footerEntries",
)
CHAPTER_FIELDS = ("name", *ENTRY_FIELDS)
# Inside the text, the keys that hold more text. Anything else (type, source, page,
# roll, href, a spellcasting block's spell lists) is structure and stays English.
TEXT_KEYS = frozenset(
    {
        "name",
        "entries",
        "entry",
        "items",
        "caption",
        "colLabels",
        "rows",
        "headerEntries",
        "footerEntries",
        "title",
        "by",
        "from",
    }
)
# Nodes whose "name" points at an entity rather than naming a section: never text.
NOT_TEXT = frozenset({"statblock", "statblockInline", "image", "gallery", "link", "dice"})
# Files holding one book (or published adventure, or reference screen) each.
BOOKISH = frozenset({"book", "adventure", "screen"})
MAX_PASSAGE = 20000


def english_hash(text: str) -> str:
    """The fingerprint of the English a passage was made from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def passages(
    data: Mapping[str, Any], fields: tuple[str, ...] = ENTRY_FIELDS
) -> list[tuple[str, str]]:
    """Every translatable text passage of a record, as ``(path, English)`` in order."""
    found: list[tuple[str, str]] = []
    for key in fields:
        if key in data:
            found.extend(_walk(data[key], key))
    return found


def _walk(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        if value.strip():
            yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}/{index}")
    elif isinstance(value, dict):
        if value.get("type") in NOT_TEXT:
            return
        for key, inner in value.items():
            if key in TEXT_KEYS:
                yield from _walk(inner, f"{path}/{key}")


def _set(data: dict[str, Any], path: str, text: str) -> dict[str, Any]:
    """``data`` with the string at ``path`` replaced, copying only what lies on the path."""
    keys = path.split("/")

    def go(node: Any, rest: list[str]) -> Any:
        if not rest:
            return text
        head, tail = rest[0], rest[1:]
        if isinstance(node, list):
            index = int(head)
            copy = list(node)
            copy[index] = go(node[index], tail)
            return copy
        copy_d = dict(node)
        copy_d[head] = go(node[head], tail)
        return copy_d

    result: dict[str, Any] = go(data, keys)
    return result


def tags_in(text: str) -> list[str]:
    """The ``{@...}`` tags of a text, outermost only, nested braces included."""
    found: list[str] = []
    index = 0
    while (start := text.find("{@", index)) != -1:
        depth, cursor = 0, start
        while cursor < len(text):
            if text.startswith("{", cursor):
                depth += 1
            elif text.startswith("}", cursor):
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth != 0:
            break
        found.append(text[start : cursor + 1])
        index = cursor + 1
    return found


def missing_tags(english: str, translated: str) -> list[str]:
    """Tags of the English that the translation lacks (§3: tags come from the source,
    so a missing one is a lost link or roll — worth saying, not worth refusing)."""
    have = list(tags_in(translated))
    missing: list[str] = []
    for tag in tags_in(english):
        if tag in have:
            have.remove(tag)
        else:
            missing.append(tag)
    return missing


@dataclass(frozen=True)
class PassageRow:
    """One passage as the editor shows it: the English, and what the language has."""

    path: str
    english: str
    text: str = ""
    draft: bool = False
    stale: bool = False
    missing: tuple[str, ...] = ()

    @property
    def tags(self) -> list[str]:
        """The English passage's tags, offered for inserting."""
        return tags_in(self.english)


@dataclass(frozen=True)
class Coverage:
    """How much of a record a language covers, and how trustworthy it is."""

    total: int = 0
    translated: int = 0
    drafts: int = 0
    stale: int = 0
    orphans: int = 0

    @property
    def any(self) -> bool:
        """True when at least one passage shows translated."""
        return self.translated > 0

    @property
    def complete(self) -> bool:
        """Every passage translated."""
        return self.total > 0 and self.translated >= self.total


@dataclass(frozen=True)
class Passage:
    """One stored translated passage."""

    text: str
    en: str
    draft: bool = False
    by: str = ""
    at: str = ""


@dataclass
class Record:
    """The stored translation of one entry or chapter."""

    passages: dict[str, Passage] = field(default_factory=dict)


@dataclass(frozen=True)
class Overlaid:
    """A record with its translation applied, and what that covered."""

    data: dict[str, Any]
    coverage: Coverage


def record_key(name: str, source: str) -> str:
    """The key of an entry's translation: its name and source, as the index has them."""
    return f"{name}|{source}"


# Categories whose (name, source) repeats: their translations key on the corpus's own
# composite key, so "Additional Maneuvers" at level 7 and at level 10 stay apart.
COMPOSITE = frozenset({"subclass", "classFeature", "subclassFeature"})


def translation_key(category: str, data: Mapping[str, Any]) -> str:
    """The key a record's translation is stored under — the editor, the reference page
    and the character sheet all ask here, so they find the same translation."""
    name, source = str(data.get("name", "")), str(data.get("source", ""))
    if category == "subclass":
        return f"{name}|{source}|{data.get('className', '')}|{data.get('classSource', '')}"
    if category == "classFeature":
        return f"{name}|{source}|{data.get('className', '')}|{data.get('level', '')}"
    if category == "subclassFeature":
        return (
            f"{name}|{source}|{data.get('className', '')}"
            f"|{data.get('subclassShortName', '')}|{data.get('level', '')}"
        )
    return record_key(name, source)


@dataclass
class SaveCount:
    """What :meth:`TranslationStore.save_many` did: passages written, and passages left
    as they were because the table had translated them already."""

    saved: int = 0
    kept: int = 0


class TranslationStore:
    """Translated passages per language and category, in ``library/i18n/<lang>/``."""

    def __init__(self, i18n_dir: Path) -> None:
        """Bind to ``library/i18n``."""
        self._dir = i18n_dir
        self._lock = threading.Lock()
        # path → (mtime, key lower → (key as written, record))
        self._cache: dict[Path, tuple[float, dict[str, tuple[str, Record]]]] = {}

    # -- files -----------------------------------------------------------------------

    def path(self, lang: str, category: str, key: str) -> Path:
        """The file holding ``key``'s translation: a category's, or one book's."""
        if category in BOOKISH:
            book = key.split("|", 1)[0]
            safe = "".join(ch for ch in book.lower() if ch.isalnum() or ch in "-_") or "_"
            return self._dir / lang / category / f"{safe}.json"
        return self._dir / lang / f"{category}.json"

    def _load(self, path: Path) -> dict[str, tuple[str, Record]]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        cached = self._cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        raw = read_json(path, {}) or {}
        table: dict[str, tuple[str, Record]] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key.startswith("_") or not isinstance(value, dict):
                    continue
                stored = value.get("passages")
                if not isinstance(stored, dict):
                    continue
                record = Record(
                    {
                        str(p): Passage(
                            text=str(v["text"]),
                            en=str(v.get("en", "")),
                            draft=bool(v.get("draft", False)),
                            by=str(v.get("by", "")),
                            at=str(v.get("at", "")),
                        )
                        for p, v in stored.items()
                        if isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"]
                    }
                )
                table[key.lower()] = (key, record)
        self._cache[path] = (mtime, table)
        return table

    def record(self, lang: str, category: str, key: str) -> Record | None:
        """The stored translation of one entry or chapter, or None."""
        found = self._load(self.path(lang, category, key)).get(key.lower())
        return found[1] if found else None

    def records(self, lang: str, category: str) -> dict[str, Record]:
        """Every stored translation of a category (not books: those are per book)."""
        return {
            key: record
            for key, record in self._load(self._dir / lang / f"{category}.json").values()
        }

    def languages(self) -> list[str]:
        """Every language with a folder here; ``ui`` holds the UI catalogues instead."""
        if not self._dir.is_dir():
            return []
        return sorted(p.name for p in self._dir.iterdir() if p.is_dir() and p.name != "ui")

    def book_records(self, lang: str, kind: str) -> Iterator[tuple[str, Record]]:
        """Every stored chapter of a kind (:data:`BOOKISH`), across its books' files."""
        for path in sorted((self._dir / lang / kind).glob("*.json")):
            yield from self._load(path).values()

    # -- reading -----------------------------------------------------------------------

    def overlay(
        self,
        lang: str,
        category: str,
        key: str,
        data: dict[str, Any],
        fields: tuple[str, ...] = ENTRY_FIELDS,
    ) -> Overlaid:
        """``data`` with the language's passages in place of the English ones.

        The English record is never changed: only the containers on a translated
        passage's path are copied.
        """
        english = passages(data, fields)
        record = self.record(lang, category, key) if lang != "en" else None
        if record is None or not record.passages:
            return Overlaid(data, Coverage(total=len(english)))
        result = data
        translated = drafts = stale = 0
        present = {path for path, _ in english}
        for path, text in english:
            passage = record.passages.get(path)
            if passage is None:
                continue
            result = _set(result, path, passage.text)
            translated += 1
            drafts += passage.draft
            stale += passage.en != english_hash(text)
        orphans = sum(1 for path in record.passages if path not in present)
        return Overlaid(result, Coverage(len(english), translated, drafts, stale, orphans))

    def rows(
        self,
        lang: str,
        category: str,
        key: str,
        data: Mapping[str, Any],
        fields: tuple[str, ...] = ENTRY_FIELDS,
    ) -> list[PassageRow]:
        """Every passage of a record with its translation, for the editor."""
        record = self.record(lang, category, key)
        stored = record.passages if record else {}
        out: list[PassageRow] = []
        for path, english in passages(data, fields):
            passage = stored.get(path)
            if passage is None:
                out.append(PassageRow(path, english))
                continue
            out.append(
                PassageRow(
                    path,
                    english,
                    passage.text,
                    passage.draft,
                    passage.en != english_hash(english),
                    tuple(missing_tags(english, passage.text)),
                )
            )
        return out

    def overlay_at(self, lang: str, category: str, key: str, prefix: str, value: Any) -> Overlaid:
        """One part of a record — a species trait, a background feature — with the
        record's translated passages under ``prefix`` laid over it. ``value`` is what
        the English record holds at ``prefix``; the result's ``data["value"]`` is it
        translated."""
        english = list(_walk(value, prefix))
        record = self.record(lang, category, key) if lang != "en" else None
        holder: dict[str, Any] = {"value": value}
        if record is None or not record.passages:
            return Overlaid(holder, Coverage(total=len(english)))
        translated = drafts = stale = 0
        for path, text in english:
            passage = record.passages.get(path)
            if passage is None:
                continue
            inner = path[len(prefix) :].lstrip("/")
            holder = _set(holder, f"value/{inner}" if inner else "value", passage.text)
            translated += 1
            drafts += passage.draft
            stale += passage.en != english_hash(text)
        return Overlaid(holder, Coverage(len(english), translated, drafts, stale))

    # -- writing -----------------------------------------------------------------------

    def save(
        self,
        lang: str,
        category: str,
        key: str,
        data: Mapping[str, Any],
        texts: Mapping[str, str],
        *,
        by: str,
        draft: bool = False,
        fields: tuple[str, ...] = ENTRY_FIELDS,
    ) -> Record:
        """Store translated passages of one record; an empty text removes its passage.

        Only paths the English record has are stored, each with the hash of the English
        it translates — so a later change upstream is noticed (§5).
        """
        self.save_many(lang, category, [(key, data, texts)], by=by, draft=draft, fields=fields)
        return self.record(lang, category, key) or Record()

    def save_many(
        self,
        lang: str,
        category: str,
        items: Iterable[tuple[str, Mapping[str, Any], Mapping[str, str]]],
        *,
        by: str,
        draft: bool = False,
        fields: tuple[str, ...] = ENTRY_FIELDS,
        keep: bool = False,
    ) -> SaveCount:
        """:meth:`save` for many records ``(key, English data, texts)`` of a category,
        each file read and written once. With ``keep``, a passage already translated is
        left as it is (an import never overwrites the table's own work)."""
        count = SaveCount()
        by_file: dict[Path, list[tuple[str, Mapping[str, Any], Mapping[str, str]]]] = {}
        for item in items:
            by_file.setdefault(self.path(lang, category, item[0]), []).append(item)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            for path, records in by_file.items():
                raw = read_json(path, {}) or {}
                if not isinstance(raw, dict):
                    raw = {}
                lower = {k.lower(): k for k in raw}
                for key, data, texts in records:
                    english = dict(passages(data, fields))
                    stored_key = lower.setdefault(key.lower(), key)
                    entry = raw.get(stored_key)
                    kept: dict[str, Any] = (
                        dict(entry.get("passages") or {}) if isinstance(entry, dict) else {}
                    )
                    for passage_path, text in texts.items():
                        value = (text or "").strip()[:MAX_PASSAGE]
                        if passage_path not in english:
                            continue
                        if not value:
                            kept.pop(passage_path, None)
                            continue
                        found = kept.get(passage_path)
                        previous: dict[str, Any] = found if isinstance(found, dict) else {}
                        if previous.get("text") == value:
                            # Shown and sent back as it was: not a review. Confirming a
                            # draft is explicit (:meth:`confirm`).
                            continue
                        if keep and previous.get("text"):
                            count.kept += 1
                            continue
                        kept[passage_path] = {
                            "text": value,
                            "en": english_hash(english[passage_path]),
                            **({"draft": True} if draft else {}),
                            "by": by,
                            "at": now,
                        }
                        count.saved += 1
                    order = {p: i for i, p in enumerate(english)}
                    ordered = dict(
                        sorted(kept.items(), key=lambda kv: order.get(kv[0], len(order)))
                    )
                    if ordered:
                        raw[stored_key] = {"passages": ordered}
                    else:
                        raw.pop(stored_key, None)
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json(path, dict(sorted(raw.items(), key=lambda kv: kv[0].lower())))
                self._cache.pop(path, None)
        return count

    def confirm(
        self,
        lang: str,
        category: str,
        key: str,
        *,
        by: str,
        paths: set[str] | None = None,
    ) -> int:
        """Mark draft passages of a record as reviewed — ``paths``, or all. How many."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            path = self.path(lang, category, key)
            raw = read_json(path, {}) or {}
            stored_key = next((k for k in raw if k.lower() == key.lower()), None)
            if stored_key is None:
                return 0
            count = 0
            for passage_path, value in (raw[stored_key].get("passages") or {}).items():
                if paths is not None and passage_path not in paths:
                    continue
                if isinstance(value, dict) and value.get("draft"):
                    value["draft"] = False
                    value["by"], value["at"] = by, now
                    count += 1
            if count:
                write_json(path, raw)
                self._cache.pop(path, None)
        return count

    def move(self, lang: str, category: str, key: str, new_key: str) -> None:
        """Put a record under another key, every passage as it is: each keeps the hash of
        the English it translates, so what differs on the new entry shows as outdated.
        Whatever the new key held is replaced; the caller decides that may happen."""
        with self._lock:
            source, target = self.path(lang, category, key), self.path(lang, category, new_key)
            raw = read_json(source, {}) or {}
            stored_key = next((k for k in raw if k.lower() == key.lower()), None)
            if stored_key is None:
                return
            entry = raw.pop(stored_key)
            other = raw if target == source else read_json(target, {}) or {}
            for held in [k for k in other if k.lower() == new_key.lower()]:
                del other[held]
            other[new_key] = entry
            # The new place first: stopped between the two, the text is held twice, not lost.
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(target, dict(sorted(other.items(), key=lambda kv: kv[0].lower())))
            if target != source:
                write_json(source, raw)
            self._cache.pop(source, None)
            self._cache.pop(target, None)


__all__ = [
    "BOOKISH",
    "COMPOSITE",
    "CHAPTER_FIELDS",
    "ENTRY_FIELDS",
    "Coverage",
    "Overlaid",
    "Passage",
    "PassageRow",
    "Record",
    "TranslationStore",
    "english_hash",
    "missing_tags",
    "passages",
    "record_key",
    "translation_key",
    "tags_in",
]
