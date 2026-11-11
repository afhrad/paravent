"""Flavour text, loaded as a parallel keyspace and joined onto its entries.

The corpus keeps descriptions and images out of the entry record and in sibling
``fluff-*.json`` files, keyed by the same ``(name, source)``. A statblock alone cannot
fill a tooltip (documentation/DESIGN.md §7), so the join is what makes previews and reference pages
show prose rather than numbers.

Two things shape this module:

* **Fluff is not a searchable category.** Nobody looks up fluff; they look up the
  monster and expect its description to be attached. So it is stored beside the
  compendium and reached through it, not registered as a category of its own
  (documentation/TECHSTACK.md §6b).
* **Keys are discovered by convention, not enumerated.** Any ``<thing>Fluff`` array in
  any file is picked up, so a new book adding ``vehicleUpgradeFluff`` needs no code
  change. Twenty keys exist today; the naming rule has held across every published book.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.compendium import Fluff
from app.store.copyresolver import CopyResolver, composite_key
from app.store.errors import CopyResolutionError
from app.store.jsonfile import iter_json_files, read_json

logger = logging.getLogger(__name__)

# ``monsterFluff`` describes the ``monster`` category, and so on. The suffix is the
# whole convention; the prefix is the category name verbatim.
FLUFF_SUFFIX = "Fluff"

# Fields a fluff record may carry beyond ``(name, source)`` to identify its entry.
# Measured: only ``subclassFluff`` carries any, and only these two — a subclass name
# does not say whose subclass, so "Alchemist" needs the class to resolve. Kept narrow
# on purpose; adding a field that fluff does not carry would break the join for every
# category that relies on the two-part key.
_ENTRY_DISCRIMINATORS: tuple[str, ...] = ("className", "classSource")


def category_for(fluff_key: str) -> str:
    """The category a fluff key describes: ``monsterFluff`` → ``monster``."""
    return fluff_key[: -len(FLUFF_SUFFIX)]


@dataclass
class FluffReport:
    """What a fluff load pass produced.

    ``orphans`` is informational, not an error list. A fluff record with no matching
    entry is normal: some describe creature *groups* with no statblock of their own
    ("deep dragons"), some describe subrace variants named differently from the race
    ("aasimar (fallen)"), and some exist only to be ``_copy`` parents.
    """

    loaded: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    unresolved_copies: list[str] = field(default_factory=list)
    orphans: dict[str, int] = field(default_factory=dict)
    files_read: int = 0

    @property
    def total(self) -> int:
        """Total fluff records loaded."""
        return sum(self.loaded.values())

    @property
    def ok(self) -> bool:
        """True when nothing was rejected."""
        return not self.errors and not self.unresolved_copies

    def summary(self) -> str:
        """One line for the startup log."""
        return (
            f"{self.total} fluff records from {self.files_read} files "
            f"across {len(self.loaded)} categories "
            f"({len(self.errors)} errors, {len(self.unresolved_copies)} unresolved copies)"
        )


class FluffStore:
    """Fluff records, indexed by category and ``(name, source)``."""

    def __init__(self, *, strict: bool = False) -> None:
        """``strict`` raises on a malformed record instead of collecting it."""
        self._strict = strict
        self._by_category: dict[str, dict[str, Fluff]] = {}
        self.report = FluffReport()

    # -- loading -----------------------------------------------------------------

    def load(self, *roots: Path) -> FluffReport:
        """Discover and load every fluff array under ``roots``.

        Later roots win on key collision, so homebrew fluff overrides imported fluff
        exactly as homebrew entries override imported entries.
        """
        report = FluffReport()
        raw: dict[str, list[dict[str, Any]]] = {}
        files: set[Path] = set()

        for root in roots:
            for path in iter_json_files(root):
                if _is_vtt_payload(path):
                    continue
                try:
                    data = read_json(path)
                except json.JSONDecodeError as exc:
                    # As the compendium does: one broken file must not stop the load.
                    logger.warning("Skipping malformed JSON %s: %s", path, exc)
                    continue
                if not isinstance(data, dict):
                    continue
                found = False
                for key, value in data.items():
                    if not key.endswith(FLUFF_SUFFIX) or not isinstance(value, list):
                        continue
                    rows = [item for item in value if isinstance(item, dict)]
                    if rows:
                        raw.setdefault(key, []).extend(rows)
                        found = True
                if found:
                    files.add(path)

        by_category: dict[str, dict[str, Fluff]] = {}
        for fluff_key, rows in sorted(raw.items()):
            category = category_for(fluff_key)
            resolved = self._resolve_copies(fluff_key, rows, report)
            index: dict[str, Fluff] = {}
            for item in resolved:
                model = self._validate(fluff_key, item, report)
                if model is not None:
                    # Indexed by the same composite key the entry uses. ``subclassFluff``
                    # carries ``className``/``classSource``, because a subclass name alone
                    # does not identify one — and a two-part key joined 0 of 148 records.
                    index[composite_key(item)] = model
            if index:
                by_category[category] = index
                report.loaded[category] = len(index)

        report.files_read = len(files)
        self._by_category = by_category
        self.report = report
        logger.info("Fluff loaded: %s", report.summary())
        return report

    def _resolve_copies(
        self,
        fluff_key: str,
        rows: list[dict[str, Any]],
        report: FluffReport,
    ) -> list[dict[str, Any]]:
        """Apply ``_copy`` within the fluff keyspace.

        1,310 of 5,984 records are shells. Their parents are other *fluff* records, not
        entries, so the index is built from this keyspace alone.
        """
        index = {composite_key(row): row for row in rows if row.get("name")}
        resolver = CopyResolver(index)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not row.get("_copy"):
                out.append(row)
                continue
            try:
                out.append(resolver.resolve(row))
            except (CopyResolutionError, ValueError) as exc:
                if self._strict:
                    raise
                report.unresolved_copies.append(f"{fluff_key}: {exc}")
        if resolver.unknown_modes:
            logger.warning(
                "Unknown _copy modes in %s: %s", fluff_key, sorted(resolver.unknown_modes)
            )
        return out

    def _validate(
        self,
        fluff_key: str,
        item: dict[str, Any],
        report: FluffReport,
    ) -> Fluff | None:
        """Validate one record, collecting the failure rather than raising."""
        try:
            return Fluff.model_validate(item)
        except ValidationError as exc:
            if self._strict:
                raise
            name = item.get("name", "<unnamed>")
            report.errors.append(f"{fluff_key}: {name}: {exc.error_count()} error(s)")
            return None

    # -- reading -----------------------------------------------------------------

    @property
    def categories(self) -> list[str]:
        """Categories that have any fluff."""
        return sorted(self._by_category)

    def count(self, category: str) -> int:
        """How many fluff records a category has."""
        return len(self._by_category.get(category, {}))

    def get(self, category: str, name: str, source: str, **discriminators: Any) -> Fluff | None:
        """The fluff for one entry, or None when it has none.

        Pass any discriminators the category's key carries — ``className`` and
        ``classSource`` for a subclass — in the order
        :func:`~app.store.copyresolver.composite_key` expects.
        """
        key = composite_key({"name": name, "source": source, **discriminators})
        return self._by_category.get(category, {}).get(key)

    def for_entry(self, category: str, entry: Any) -> Fluff | None:
        """The fluff for a loaded model, or None.

        Takes the model rather than its parts so callers never rebuild a key by hand,
        and so categories whose identity needs more than ``(name, source)`` join
        correctly. A subclass is the case that matters: its fluff is keyed by class as
        well, since "Alchemist" alone does not say whose Alchemist.
        """
        name = getattr(entry, "name", None)
        source = getattr(entry, "source", None)
        if not name or not source:
            return None
        payload: dict[str, Any] = {"name": str(name), "source": str(source)}
        for field_name in _ENTRY_DISCRIMINATORS:
            value = getattr(entry, field_name, None)
            if value is not None:
                payload[field_name] = value
        return self._by_category.get(category, {}).get(composite_key(payload))

    def has_fluff(self, category: str, name: str, source: str, **discriminators: Any) -> bool:
        """True when fluff exists *and* carries something renderable."""
        found = self.get(category, name, source, **discriminators)
        return found is not None and found.has_content

    def count_orphans(self, category: str, entry_keys: Iterable[str]) -> int:
        """How many of a category's fluff records match no entry.

        Reported rather than pruned: an orphan may be a ``_copy`` parent, a creature
        group with no statblock, or a variant named differently from its entry.
        """
        known = set(entry_keys)
        return sum(1 for key in self._by_category.get(category, {}) if key not in known)


def _is_vtt_payload(path: Path) -> bool:
    """True for Foundry VTT files, which are not content.

    Mirrors the compendium's filter: ``foundry-*.json`` carries real-looking keys but
    holds VTT automation (documentation/TECHSTACK.md §6b).
    """
    return path.name == "foundry.json" or path.name.startswith("foundry-")


__all__ = [
    "FLUFF_SUFFIX",
    "FluffReport",
    "FluffStore",
    "category_for",
]
