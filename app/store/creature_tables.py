"""The creature builder's tables (TECHSTACK.md §6b, phase 2), read once.

* ``msbcr.json`` — the *Dungeon Master's Guide*'s "Monster Statistics by Challenge
  Rating": proficiency bonus, AC, hit-point range, attack bonus, damage-per-round
  range and save DC for each of the 34 ratings;
* ``monsterfeatures.json`` — the guide's monster features with what each does to the
  effective AC, hit points, attack bonus or damage (as 5eTools's own expressions);
* ``makebrew-creature.json`` — 231 traits and 89 actions taken from the books, written
  with placeholders (``{@hit <$to_hit__str$>}``) for a builder to fill.

Nothing is written. A missing file leaves its table empty.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATS_FILE = "msbcr.json"
FEATURES_FILE = "monsterfeatures.json"
BLOCKS_FILE = "makebrew-creature.json"


def cr_value(text: str) -> Fraction:
    """``"1/4"`` → 1/4; anything unreadable is 0."""
    try:
        return Fraction(str(text).strip())
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


@dataclass(frozen=True)
class CrRow:
    """What a creature of one challenge rating typically has."""

    cr: str
    pb: int
    ac: int
    hp_min: int
    hp_max: int
    attack: int
    dpr_min: int
    dpr_max: int
    save_dc: int

    @property
    def value(self) -> Fraction:
        """The rating as a number."""
        return cr_value(self.cr)


@dataclass(frozen=True)
class MonsterFeature:
    """A feature that shifts the CR: each effect is 5eTools's expression, or ""."""

    name: str
    example: str
    effect: str
    ac: str = ""
    hp: str = ""
    attack: str = ""
    dpr: str = ""
    takes_number: bool = False


@dataclass(frozen=True)
class BlockTemplate:
    """A ready-made trait or action: ``"Bite (Cloaker)"`` and its placeholder text."""

    name: str
    source: str
    entries: tuple[Any, ...]

    @property
    def title(self) -> str:
        """The name without the creature it was taken from: ``"Bite"``."""
        return self.name.split(" (", 1)[0]


class CreatureTables:
    """The corpus's creature-builder tables."""

    def __init__(self, reference_dir: Path | None) -> None:
        """``reference_dir`` is the corpus root, or None when none is configured."""
        self._root = reference_dir
        self._loaded: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget the tables, after the corpus changed."""
        with self._lock:
            self._loaded = None

    def _read(self, name: str) -> dict[str, Any]:
        if self._root is None:
            return {}
        try:
            data = json.loads((self._root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.info("Creature tables: %s not read: %s", name, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _load(self) -> dict[str, Any]:
        rows = []
        for raw in self._read(STATS_FILE).get("cr") or []:
            try:
                rows.append(
                    CrRow(
                        str(raw["_cr"]),
                        int(raw["pb"]),
                        int(raw["ac"]),
                        int(raw["hpMin"]),
                        int(raw["hpMax"]),
                        int(raw["attackBonus"]),
                        int(raw["dprMin"]),
                        int(raw["dprMax"]),
                        int(raw["saveDc"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort(key=lambda r: r.value)
        features = [
            MonsterFeature(
                str(f["name"]),
                str(f.get("example", "")),
                str(f.get("effect", "")),
                str(f.get("ac", "")),
                str(f.get("hp", "")),
                str(f.get("attackBonus", "")),
                str(f.get("dpr", "")),
                bool(f.get("hasNumberParam")),
            )
            for f in self._read(FEATURES_FILE).get("monsterfeatures") or []
            if isinstance(f, dict) and f.get("name")
        ]
        blocks = self._read(BLOCKS_FILE)

        def templates(key: str) -> list[BlockTemplate]:
            return sorted(
                (
                    BlockTemplate(str(b["name"]), str(b.get("source", "")), tuple(b["entries"]))
                    for b in blocks.get(key) or []
                    if isinstance(b, dict) and b.get("name") and isinstance(b.get("entries"), list)
                ),
                key=lambda b: b.name.lower(),
            )

        return {
            "rows": rows,
            "features": features,
            "traits": templates("makebrewCreatureTrait"),
            "actions": templates("makebrewCreatureAction"),
        }

    def _data(self) -> dict[str, Any]:
        with self._lock:
            if self._loaded is None:
                self._loaded = self._load()
            return self._loaded

    def rows(self) -> list[CrRow]:
        """Every challenge rating's row, lowest first."""
        found: list[CrRow] = self._data()["rows"]
        return found

    def row(self, cr: str) -> CrRow | None:
        """The row for a rating ("1/4", "5")."""
        wanted = cr_value(cr)
        return next((r for r in self.rows() if r.value == wanted), None)

    def features(self) -> list[MonsterFeature]:
        """The guide's monster features, in book order."""
        found: list[MonsterFeature] = self._data()["features"]
        return found

    def feature(self, name: str) -> MonsterFeature | None:
        """A monster feature by name."""
        return next((f for f in self.features() if f.name.lower() == name.lower()), None)

    def traits(self) -> list[BlockTemplate]:
        """The ready-made traits, by name."""
        found: list[BlockTemplate] = self._data()["traits"]
        return found

    def actions(self) -> list[BlockTemplate]:
        """The ready-made actions, by name."""
        found: list[BlockTemplate] = self._data()["actions"]
        return found

    def template(self, kind: str, name: str) -> BlockTemplate | None:
        """A trait (``kind="trait"``) or action by its full name."""
        pool = self.traits() if kind == "trait" else self.actions()
        return next((b for b in pool if b.name == name), None)


__all__ = ["BlockTemplate", "CrRow", "CreatureTables", "MonsterFeature", "cr_value"]
