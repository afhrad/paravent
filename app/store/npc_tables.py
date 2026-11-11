"""The tables an NPC is rolled on (TECHSTACK.md §6b, phase 2), read once.

* ``names.json`` — *Xanathar's Guide*'s name tables by ancestry (Dwarf: Female, Male,
  Clan; Human: one pair per culture, "Celtic, Female" …), and a few setting books' own;
* the 2024 *Dungeon Master's Guide*'s six name styles ("1: Common Names" … with a given
  name and a surname per row, ``isNameGenerator``);
* the "Detailed NPCs" sections of both *Dungeon Master's Guides*, read by caption:
  appearance, abilities, talents, mannerisms, interaction traits, ideals, bonds, flaws
  and secrets;
* ``life.json``'s trinkets.

Nothing is written. A table the corpus lacks is simply absent.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.store.published import body_sections

logger = logging.getLogger(__name__)

NAMES_FILE = "names.json"
LIFE_FILE = "life.json"
NPC_SECTION = "Detailed NPCs"
BOOKS = ("DMG", "XDMG")
SURNAME_TABLES = ("clan", "family")

_RANGE = re.compile(r"(\d+)\D*(\d+)?")


@dataclass(frozen=True)
class Row:
    """One row: the rolls it covers and its text cells."""

    low: int
    high: int
    cells: tuple[str, ...]


@dataclass(frozen=True)
class Table:
    """A rollable table."""

    caption: str
    source: str
    rows: tuple[Row, ...]

    @property
    def faces(self) -> int:
        """The die the table is rolled with."""
        return max((r.high for r in self.rows), default=0)


@dataclass(frozen=True)
class Ancestry:
    """An ancestry's name tables: ``option`` ("Female", "Celtic, Male", "Clan") → table."""

    name: str
    source: str
    tables: tuple[Table, ...]

    @property
    def key(self) -> str:
        """``Dwarf|XGE`` — what a form sends."""
        return f"{self.name}|{self.source}"

    @property
    def cultures(self) -> tuple[str, ...]:
        """The cultures its tables are split by ("Celtic" …), in book order; often none."""
        found: list[str] = []
        for table in self.tables:
            culture, sep, _rest = table.caption.partition(", ")
            if sep and culture not in found and not culture.startswith("Gith"):
                found.append(culture)
        return tuple(found)


def table_rows(raw: Any) -> tuple[Row, ...]:
    """``[["1", "Adrik", "Brightsun"], …]`` or ``[{"min":1,"max":2,"result":…}]`` → rows."""
    out: list[Row] = []
    for index, row in enumerate(raw or [], start=1):
        if isinstance(row, dict) and "result" in row:
            low = int(row.get("min", index))
            out.append(Row(low, int(row.get("max", low)), (str(row["result"]),)))
            continue
        if not isinstance(row, list) or len(row) < 2:
            continue
        found = _RANGE.match(str(row[0]).strip())
        low = int(found[1]) if found else index
        high = int(found[2]) if found and found[2] else low
        if found and low == 0:  # "00" is a hundred
            low = high = 100
        cells = tuple(str(c) for c in row[1:] if isinstance(c, (str, int)))
        if cells:
            out.append(Row(low, high, cells))
    return tuple(out)


def _section(node: Any, name: str) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if node.get("name") == name:
            return node
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            found = _section(child, name)
            if found is not None:
                return found
    return None


def _life(raw: Any) -> dict[str, list[Table]]:
    """``life.json``'s ``lifeClass`` or ``lifeBackground`` by lower-case name: the
    ``reasons`` first (caption ``""``), then each of ``other`` by its caption. A row is
    one line, numbered from 1, so a table of six rolls a d6."""
    out: dict[str, list[Table]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        source = str(item.get("source", ""))
        lists = [("", item.get("reasons")), *(item.get("other") or {}).items()]
        tables = [
            Table(
                str(caption),
                source,
                tuple(Row(i, i, (str(line),)) for i, line in enumerate(lines, 1)),
            )
            for caption, lines in lists
            if isinstance(lines, list) and lines
        ]
        name = str(item["name"]).lower()
        out[name] = tables
        # "Guild Artisan (Guild Merchant)": the background is "Guild Artisan".
        short = name.split(" (")[0]
        out.setdefault(short, tables)
    return out


def _tables(node: Any, source: str, out: dict[str, Table]) -> None:
    """Every captioned table under ``node``, by caption."""
    if isinstance(node, dict):
        if node.get("type") == "table" and node.get("caption"):
            caption = str(node["caption"])
            out[caption] = Table(caption, source, table_rows(node.get("rows")))
        for value in node.values():
            _tables(value, source, out)
    elif isinstance(node, list):
        for child in node:
            _tables(child, source, out)


class NpcTables:
    """The corpus's NPC tables."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._loaded: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget the tables, after the corpus changed."""
        with self._lock:
            self._loaded = None

    def _read(self, relative: str) -> Any:
        if self._root is None:
            return None
        try:
            return json.loads((self._root / relative).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("NPC tables: %s not read: %s", relative, exc)
            return None

    def _load(self) -> dict[str, Any]:
        books: dict[str, dict[str, Table]] = {}
        for code in BOOKS:
            raw = self._read(f"book/book-{code.lower()}.json")
            section = _section(body_sections(raw), NPC_SECTION)
            if section is not None:
                found: dict[str, Table] = {}
                _tables(section, code, found)
                books[code] = found
        ancestries: list[Ancestry] = []
        names = self._read(NAMES_FILE)
        for item in names.get("name", []) if isinstance(names, dict) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            source = str(item.get("source", ""))
            tables = tuple(
                Table(str(t.get("option", "")), source, table_rows(t.get("table")))
                for t in item.get("tables") or []
                if isinstance(t, dict) and t.get("option")
            )
            if tables:
                ancestries.append(Ancestry(str(item["name"]), source, tables))
        life = self._read(LIFE_FILE)
        life = life if isinstance(life, dict) else {}
        trinkets = tuple(str(x) for x in (life.get("lifeTrinket") or []) if isinstance(x, str))
        return {
            "books": books,
            "ancestries": ancestries,
            "trinkets": trinkets,
            "classes": _life(life.get("lifeClass")),
            "backgrounds": _life(life.get("lifeBackground")),
        }

    def _data(self) -> dict[str, Any]:
        with self._lock:
            if self._loaded is None:
                self._loaded = self._load()
            return self._loaded

    def books(self) -> set[str]:
        """The *Dungeon Master's Guides* whose NPC tables are present."""
        return set(self._data()["books"])

    def table(self, book: str, caption: str) -> Table | None:
        """A book's NPC table by caption ("NPC Talents")."""
        found: Table | None = self._data()["books"].get(book, {}).get(caption)
        return found

    def name_styles(self) -> list[Table]:
        """The 2024 guide's name tables (given name, surname), in book order."""
        tables = self._data()["books"].get("XDMG", {})
        return [t for c, t in tables.items() if c.endswith(" Names") and t.rows]

    def ancestries(self) -> list[Ancestry]:
        """The name tables by ancestry, by name."""
        return sorted(self._data()["ancestries"], key=lambda a: (a.name.lower(), a.source))

    def ancestry(self, key: str) -> Ancestry | None:
        """An ancestry by ``Name|SOURCE``."""
        return next((a for a in self.ancestries() if a.key.lower() == key.lower()), None)

    def trinkets(self) -> tuple[str, ...]:
        """*Xanathar's Guide*'s trinkets."""
        found: tuple[str, ...] = self._data()["trinkets"]
        return found

    def life_class(self, name: str) -> list[Table]:
        """*Xanathar's* "This Is Your Life" tables for a class: why you became one
        (caption ``""``), then its own ("Personal Totems", "Tattoo"…). Empty for a class
        it has none for (the Artificer)."""
        found: list[Table] = self._data()["classes"].get(name.lower(), [])
        return found

    def life_background(self, name: str) -> list[Table]:
        """The same for a background: why you took it up. "Guild Artisan (Guild
        Merchant)" answers to "Guild Artisan" too."""
        backgrounds: dict[str, list[Table]] = self._data()["backgrounds"]
        return backgrounds.get(name.lower(), [])


__all__ = ["SURNAME_TABLES", "Ancestry", "NpcTables", "Row", "Table"]
