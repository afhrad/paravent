"""Rules tables that exist only as rendered tables inside rulebook prose.

Encounter difficulty needs four numbers tables, and character advancement one (the XP
for each level); the corpus has none of them as data:
they are ``type: table`` entries inside ``book/book-dmg.json`` (2014), ``book/book-xdmg.json``
(2024) and the Monster Manuals. The ``cr`` key that looks promising (``msbcr.json``) is
the DMG's creature-*builder* table — AC, HP and damage per CR, no XP at all.

So, as with the ability-score constants (documentation/CHARGEN.md G1), the tables are
read **out of the rendered book tables**, not retyped. Where they are kept follows from
what may be passed on:

* **Shipped** — ``data/rules/encounterdifficulty.json`` holds the three tables that are
  also in the System Reference Documents, whose licence lets anyone pass them on
  (NOTICE.md): experience by challenge rating and character advancement (SRD 5.1) and the
  2024 budgets (SRD 5.2.1). ``python -m app.cli rules`` regenerates the file; a test
  re-extracts from the corpus and compares, so a wrong number fails loudly instead of
  rating fights wrong.
* **Read from the user's corpus** — the 2014 guide's thresholds, multipliers and
  adventuring day are in no SRD: they are the publisher's, and Paravent does not carry
  them. :func:`classic` reads them from the corpus the user brought. A corpus without
  the 2014 *Dungeon Master's Guide* has no 2014 rating; nothing fails, the encounter
  says which book it lacks.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

RULES_FILE = Path(__file__).resolve().parents[2] / "data" / "rules" / "encounterdifficulty.json"
# The book the 2014 tables are read from, in the user's corpus.
CLASSIC_BOOK = "book/book-dmg.json"

# (book file, table caption, what it becomes)
TABLES = (
    (CLASSIC_BOOK, "XP Thresholds by Character Level", "classic.thresholds"),
    (CLASSIC_BOOK, "Encounter Multipliers", "classic.multipliers"),
    # How much adjusted XP a character can face between long rests (2014 only: the
    # 2024 guide sets no such budget).
    (CLASSIC_BOOK, "Adventuring Day XP", "classic.day"),
    ("book/book-dmg.json", "Experience Points by Challenge Rating", "xp_by_cr"),
    ("book/book-xdmg.json", "XP Budget per Character", "one.budgets"),
    # The experience a character needs for each level; the 2024 table is the same.
    ("book/book-phb.json", "Character Advancement", "advancement"),
)
# What the repository ships: the tables the System Reference Documents hold too.
SHIPPED = ("xp_by_cr", "one.budgets", "advancement")


class RulesTableMissing(LookupError):
    """A table the extraction expects is not in the corpus."""


def _number(cell: Any) -> int:
    """``"1,100"`` → 1100, ``"20th"`` → 20, ``"0 or 10"`` → 10 (a creature with any attack)."""
    text = str(cell).replace(",", "")
    numbers = re.findall(r"\d+", text)
    if not numbers:
        raise ValueError(f"no number in {cell!r}")
    return int(numbers[-1])


def _tables(node: Any, page: int | None = None) -> Iterator[tuple[dict[str, Any], int | None]]:
    """Every ``type: table`` entry under ``node``, with the page of its nearest section."""
    if isinstance(node, dict):
        page = node.get("page", page) if isinstance(node.get("page"), int) else page
        if node.get("type") == "table":
            yield node, page
        for value in node.values():
            yield from _tables(value, page)
    elif isinstance(node, list):
        for value in node:
            yield from _tables(value, page)


def _find(reference_dir: Path, filename: str, caption: str) -> tuple[list[list[Any]], int | None]:
    path = reference_dir / filename
    if not path.is_file():
        raise RulesTableMissing(f"{filename} is not in the corpus")
    for table, page in _tables(json.loads(path.read_text(encoding="utf-8"))):
        if table.get("caption") == caption:
            return table.get("rows") or [], page
    raise RulesTableMissing(f"{caption!r} not found in {filename}")


def _multiplier_rows(rows: list[list[Any]]) -> list[dict[str, Any]]:
    """``["3-6", "×2"]`` → ``{"min": 3, "max": 6, "multiplier": 2.0}``; open-ended max is None."""
    out = []
    for count, factor in rows:
        bounds = [int(n) for n in re.findall(r"\d+", str(count))]
        value = float(re.sub(r"[^\d.]", "", str(factor)))
        open_ended = "more" in str(count)
        low = bounds[0]
        high = None if open_ended else (bounds[1] if len(bounds) > 1 else bounds[0])
        out.append({"min": low, "max": high, "multiplier": value})
    return out


def _classic(
    thresholds: list[list[Any]], multipliers: list[list[Any]], day: list[list[Any]]
) -> dict[str, Any]:
    """The 2014 guide's three tables, from their rows."""
    return {
        "thresholds": {
            str(_number(row[0])): {
                "easy": _number(row[1]),
                "medium": _number(row[2]),
                "hard": _number(row[3]),
                "deadly": _number(row[4]),
            }
            for row in thresholds
        },
        "multipliers": _multiplier_rows(multipliers),
        "day": {str(_number(row[0])): _number(row[1]) for row in day},
    }


def no_classic() -> dict[str, Any]:
    """The 2014 tables of a corpus that has none: every table empty."""
    return {"thresholds": {}, "multipliers": [], "day": {}}


def classic_stamp(reference_dir: Path | None) -> tuple[int, int] | None:
    """When the book the 2014 tables come from last changed, and its size; None without
    the book. A caller keeps what :func:`classic` gave it until this changes."""
    if reference_dir is None:
        return None
    try:
        stat = (reference_dir / CLASSIC_BOOK).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def classic(reference_dir: Path | None) -> dict[str, Any]:
    """The 2014 guide's thresholds, multipliers and adventuring day, read from the user's
    corpus. Without the book, or with a table missing or unreadable in it, every table
    is empty (:func:`no_classic`): there is then no 2014 rating, and nothing fails."""
    if reference_dir is None:
        return no_classic()
    wanted = {caption: target for book, caption, target in TABLES if target.startswith("classic.")}
    try:
        book = json.loads((reference_dir / CLASSIC_BOOK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return no_classic()
    rows: dict[str, list[list[Any]]] = {}
    for table, _page in _tables(book):
        target = wanted.get(str(table.get("caption")))
        if target is not None and target not in rows:
            rows[target] = table.get("rows") or []
    if len(rows) < len(wanted):
        return no_classic()
    try:
        return _classic(
            rows["classic.thresholds"], rows["classic.multipliers"], rows["classic.day"]
        )
    except (ValueError, IndexError, TypeError):
        return no_classic()


def extract(reference_dir: Path) -> dict[str, Any]:
    """Read every encounter table out of the corpus's rulebooks."""
    found: dict[str, tuple[list[list[Any]], int | None]] = {
        target: _find(reference_dir, filename, caption) for filename, caption, target in TABLES
    }
    thresholds, _ = found["classic.thresholds"]
    multipliers, _ = found["classic.multipliers"]
    day, _ = found["classic.day"]
    xp_rows, _ = found["xp_by_cr"]
    budgets, _ = found["one.budgets"]
    advancement, _ = found["advancement"]
    return {
        "_meta": {
            "note": (
                "Generated by `python -m app.cli rules` from rendered tables in the rulebook "
                "text; the corpus has no structured copy. A test re-extracts and compares. "
                "See app/store/rulestables.py."
            ),
            "sources": [
                {
                    "source": filename.split("book-")[1].split(".")[0].upper(),
                    "table": caption,
                    "page": found[target][1],
                }
                for filename, caption, target in TABLES
            ],
        },
        "xp_by_cr": {str(row[0]): _number(row[1]) for row in xp_rows},
        # Level → the experience points it starts at (PHB: XP, Level, Proficiency Bonus).
        "advancement": {str(_number(row[1])): _number(row[0]) for row in advancement},
        "classic": _classic(thresholds, multipliers, day),
        "one": {
            "budgets": {
                str(_number(row[0])): {
                    "low": _number(row[1]),
                    "moderate": _number(row[2]),
                    "high": _number(row[3]),
                }
                for row in budgets
            },
        },
    }


def shipped(data: dict[str, Any]) -> dict[str, Any]:
    """The part of an extraction the repository carries: the tables the System Reference
    Documents hold too (:data:`SHIPPED`), with where each was read."""
    captions = {caption for _book, caption, target in TABLES if target in SHIPPED}
    return {
        "_meta": {
            "note": (
                "Generated by `python -m app.cli rules` from rendered tables in the rulebook "
                "text; the corpus has no structured copy. A test re-extracts and compares. "
                "Only the tables that are also in the System Reference Documents 5.1 and "
                "5.2.1 (NOTICE.md) are kept here; the 2014 guide's thresholds, multipliers "
                "and adventuring day are read from the user's corpus. See "
                "app/store/rulestables.py."
            ),
            "sources": [
                source for source in data["_meta"]["sources"] if source["table"] in captions
            ],
        },
        "xp_by_cr": data["xp_by_cr"],
        "advancement": data["advancement"],
        "one": data["one"],
    }


def write(reference_dir: Path, target: Path = RULES_FILE) -> dict[str, Any]:
    """Extract and write the rules file (its shipped part); returns what was written."""
    data = shipped(extract(reference_dir))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def load(path: Path = RULES_FILE, reference_dir: Path | None = None) -> dict[str, Any]:
    """The tables: the committed rules file, and under ``classic`` the 2014 guide's from
    the corpus at ``reference_dir`` (empty tables without it)."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data["classic"] = classic(reference_dir)
    return data


__all__ = [
    "CLASSIC_BOOK",
    "RULES_FILE",
    "SHIPPED",
    "RulesTableMissing",
    "classic",
    "classic_stamp",
    "extract",
    "load",
    "no_classic",
    "shipped",
    "write",
]
