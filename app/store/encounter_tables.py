"""Random-encounter tables from the corpus (TECHSTACK.md §6b, phase 2), read once.

``encounters.json`` holds 42 environments — *Xanathar's Guide*'s Forest, Mountain,
Underdark… by level band (1–4, 5–10, 11–16, 17–20), the *Dungeon Master's Guide*'s
Urban, Sylvan Forest, At Sea…, and setting books' own. A row is prose with tags:
``"{@dice 2d4} woodcutters ({@creature commoner||commoners})"``. Nothing is written.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENCOUNTERS_FILE = "encounters.json"


@dataclass(frozen=True)
class EncounterTable:
    """One environment's table for a level band (or for every level)."""

    environment: str
    source: str
    min_level: int | None
    max_level: int | None
    dice: str
    rows: tuple[dict[str, Any], ...]

    def covers(self, level: int) -> bool:
        """True when the table is for this party level."""
        low = self.min_level if self.min_level is not None else 1
        high = self.max_level if self.max_level is not None else 20
        return low <= level <= high


@dataclass(frozen=True)
class Environment:
    """An environment with its tables."""

    name: str
    source: str
    tables: tuple[EncounterTable, ...]

    @property
    def key(self) -> str:
        """``Forest|XGE`` — what a form sends."""
        return f"{self.name}|{self.source}"

    def table_for(self, level: int) -> EncounterTable | None:
        """The table for a party level; an environment with one table uses it always."""
        if len(self.tables) == 1:
            return self.tables[0]
        return next((t for t in self.tables if t.covers(level)), None)


class EncounterTables:
    """The corpus's random-encounter tables."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._environments: list[Environment] | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget the tables, after the corpus changed."""
        with self._lock:
            self._environments = None

    def _load(self) -> list[Environment]:
        if self._root is None:
            return []
        try:
            raw = json.loads((self._root / ENCOUNTERS_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("Encounter tables not read: %s", exc)
            return []
        out = []
        for item in raw.get("encounter", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name, source = str(item["name"]), str(item.get("source", ""))
            tables = tuple(
                EncounterTable(
                    name,
                    source,
                    t.get("minlvl") if isinstance(t.get("minlvl"), int) else None,
                    t.get("maxlvl") if isinstance(t.get("maxlvl"), int) else None,
                    str(t.get("diceExpression") or "d100"),
                    tuple(r for r in t.get("table") or [] if isinstance(r, dict)),
                )
                for t in item.get("tables") or []
                if isinstance(t, dict)
            )
            if tables:
                out.append(Environment(name, source, tables))
        return sorted(out, key=lambda e: (e.name.lower(), e.source))

    def all(self) -> list[Environment]:
        """Every environment, by name."""
        with self._lock:
            if self._environments is None:
                self._environments = self._load()
            return list(self._environments)

    def get(self, key: str) -> Environment | None:
        """An environment by ``Name|SOURCE``."""
        return next((e for e in self.all() if e.key.lower() == key.lower()), None)


__all__ = ["EncounterTable", "EncounterTables", "Environment"]
