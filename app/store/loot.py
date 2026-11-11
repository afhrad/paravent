"""Treasure tables from the corpus (TECHSTACK.md §6b, phase 2), read once on demand.

``loot.json`` holds the *Dungeon Master's Guide* tables of both editions: individual
treasure and hoards by challenge rating, gems and art objects by value, and the magic
item tables — A–I in 2014, by theme and rarity (plus "by level") in 2024. ``items.json``
adds the item groups a table row may name ("Potion of Resistance" — one of eleven).
Nothing here is written; the tables are the corpus's own dicts.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOOT_FILE = "loot.json"
ITEMS_FILE = "items.json"


class LootTables:
    """The treasure tables of one corpus, by book."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._loot: dict[str, list[dict[str, Any]]] | None = None
        # (lower-case name, source) → the group's items; the same name exists per edition.
        self._groups: dict[tuple[str, str], list[str]] | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget the tables, after the corpus changed."""
        with self._lock:
            self._loot = None
            self._groups = None

    def _read(self, name: str) -> dict[str, Any]:
        if self._root is None:
            return {}
        try:
            data = json.loads((self._root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("%s not read: %s", name, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _tables(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            if self._loot is None:
                raw = self._read(LOOT_FILE)
                self._loot = {
                    key: [t for t in value if isinstance(t, dict)]
                    for key, value in raw.items()
                    if isinstance(value, list)
                }
            return self._loot

    def books(self) -> set[str]:
        """The books whose individual and hoard tables are present ("DMG", "XDMG")."""
        return {str(t.get("source")) for t in self._tables().get("hoard", [])}

    def _by_cr(self, kind: str, source: str, cr: float) -> dict[str, Any] | None:
        for table in self._tables().get(kind, []):
            if str(table.get("source")) != source:
                continue
            low = table.get("crMin", 0)
            high = table.get("crMax")
            if cr >= low and (high is None or cr <= high):
                return table
        return None

    def individual(self, source: str, cr: float) -> dict[str, Any] | None:
        """The individual-treasure table for a challenge rating."""
        return self._by_cr("individual", source, cr)

    def hoard(self, source: str, cr: float) -> dict[str, Any] | None:
        """The hoard table for a challenge rating."""
        return self._by_cr("hoard", source, cr)

    def _typed(self, kind: str, source: str, kind_type: Any) -> dict[str, Any] | None:
        return next(
            (
                t
                for t in self._tables().get(kind, [])
                if str(t.get("source")) == source and t.get("type") == kind_type
            ),
            None,
        )

    def gems(self, source: str, value: int) -> dict[str, Any] | None:
        """The gemstones worth ``value`` gp each."""
        return self._typed("gems", source, value)

    def art(self, source: str, value: int) -> dict[str, Any] | None:
        """The art objects worth ``value`` gp each."""
        return self._typed("artObjects", source, value)

    def magic(self, source: str, table_type: str) -> dict[str, Any] | None:
        """A magic item table: "A"–"I" (2014), "arcana.rare" or "byLevel.5-10" (2024)."""
        return self._typed("magicItems", source, table_type)

    def group(self, name: str, source: str | None = None) -> list[str]:
        """The items of an item group ("Potion of Resistance"), from ``source``'s if given.

        The same group is printed in both *Dungeon Master's Guides*; without a source,
        any one of them answers.
        """
        with self._lock:
            if self._groups is None:
                raw = self._read(ITEMS_FILE)
                self._groups = {
                    (str(g.get("name", "")).lower(), str(g.get("source", ""))): [
                        str(i) for i in g.get("items") or []
                    ]
                    for g in raw.get("itemGroup", [])
                    if isinstance(g, dict)
                }
            groups = self._groups
        if source is not None and (name.lower(), source) in groups:
            return list(groups[(name.lower(), source)])
        return next((list(v) for (n, _s), v in groups.items() if n == name.lower()), [])


__all__ = ["LootTables"]
