"""The ``generated/`` lookup tables: joins the corpus does not put on the records.

These files are not entry arrays, so they are not :class:`~app.store.compendium.Category`
rows — they are nested maps, loaded once and queried directly.

The important one is the spell-source lookup. **Spell list membership is not a field on
the spell.** It lives here, keyed by source, because membership is itself
source-dependent: *air bubble* is a Druid spell in PHB and an Artificer spell in TCE.
Any builder that instead filters spells by a ``classes`` field on the spell record gets
2014 and 2024 lists wrong (documentation/CHARGEN.md trap #2).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.store.jsonfile import read_json

logger = logging.getLogger(__name__)

SPELL_SOURCE_LOOKUP = "generated/gendata-spell-source-lookup.json"
SUBCLASS_LOOKUP = "generated/gendata-subclass-lookup.json"


class SpellSourceLookup:
    """Which classes and subclasses may learn a given spell.

    The file nests ``source -> spell name -> {"class": …, "subclass": …}``. Spell names
    are lowercased upstream; sources are not, so comparisons here normalise both.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """``data`` is the raw file contents; empty when the corpus lacks the file."""
        self._by_source: dict[str, dict[str, Any]] = {}
        for source, spells in (data or {}).items():
            if isinstance(spells, dict):
                self._by_source[source.lower()] = spells

    @property
    def sources(self) -> list[str]:
        """Every source that contributes spell-list membership."""
        return sorted(self._by_source)

    def names(self, source: str) -> list[str]:
        """The spell names (lower case) one source contributes membership for."""
        return list(self._by_source.get(source.lower(), {}))

    @property
    def spell_count(self) -> int:
        """How many distinct spell names the lookup covers."""
        return len({name for spells in self._by_source.values() for name in spells})

    def entry(self, spell_name: str, spell_source: str) -> dict[str, Any] | None:
        """The raw membership record for one spell, or None when absent."""
        spells = self._by_source.get(spell_source.lower())
        if not spells:
            return None
        found = spells.get(spell_name.lower())
        return found if isinstance(found, dict) else None

    def classes_for(
        self,
        spell_name: str,
        spell_source: str,
        *,
        include_variants: bool = True,
    ) -> dict[str, set[str]]:
        """Class lists this spell belongs to, as ``{class source: {class name}}``.

        Keyed by the *class's* source rather than flattened, because that is the
        distinction that makes a 2014 list differ from a 2024 one. A caller holding a
        declared source set (documentation/TECHSTACK.md §6c) filters on these keys.

        Two upstream keys carry membership and **both matter**:

        * ``class`` — the spell is on that class's list as printed.
        * ``classVariant`` — the class gets it through an *expanded* list published
          elsewhere, e.g. Tasha's adding Mind Sliver to the Sorcerer, Warlock and
          Wizard. 119 of 936 records carry **only** ``classVariant``, so ignoring it
          silently empties their class lists.

        ``include_variants=False`` restricts the answer to as-printed lists, for a table
        that has not adopted the expanded ones.
        """
        record = self.entry(spell_name, spell_source)
        if record is None:
            return {}
        out: dict[str, set[str]] = {}
        wanted = ("class", "classVariant") if include_variants else ("class",)
        for field in wanted:
            for class_source, classes in (record.get(field) or {}).items():
                if not isinstance(classes, dict):
                    continue
                # ``class`` maps to ``True``; ``classVariant`` to a dict carrying
                # ``definedInSources``. Both mean "this class gets the spell"; only a
                # literal False excludes it.
                names = {name for name, value in classes.items() if value is not False}
                if names:
                    out.setdefault(class_source, set()).update(names)
        return out

    def variant_sources(self, spell_name: str, spell_source: str) -> dict[str, set[str]]:
        """Which books add this spell to a class, as ``{class name: {book}}``.

        Lets a caller show *why* a spell is on a list — "added by TCE" — and lets a
        declared source set exclude an expansion the table does not own.
        """
        record = self.entry(spell_name, spell_source)
        if record is None:
            return {}
        out: dict[str, set[str]] = {}
        for classes in (record.get("classVariant") or {}).values():
            if not isinstance(classes, dict):
                continue
            for name, value in classes.items():
                if isinstance(value, dict):
                    defined = value.get("definedInSources") or []
                    if isinstance(defined, list):
                        out.setdefault(name, set()).update(str(s) for s in defined)
        return out

    def has_class(
        self,
        spell_name: str,
        spell_source: str,
        class_name: str,
        class_source: str,
        *,
        include_variants: bool = True,
    ) -> bool:
        """True when the named class, published in ``class_source``, gets this spell."""
        classes = self.classes_for(
            spell_name, spell_source, include_variants=include_variants
        ).get(class_source, set())
        return any(name.lower() == class_name.lower() for name in classes)

    def subclasses_for(self, spell_name: str, spell_source: str) -> dict[str, Any]:
        """Subclass membership, left in its nested upstream shape.

        The nesting is ``class source -> class -> subclass source -> subclass``, which
        has no flatter form that keeps the source distinctions the resolver needs.
        """
        record = self.entry(spell_name, spell_source)
        return dict(record.get("subclass") or {}) if record else {}


class SubclassLookup:
    """Which subclasses a class has, per source.

    Nested ``class source -> class name -> subclass source -> short name -> {...}``.
    Used to resolve a subclass reference without scanning every class file.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """``data`` is the raw file contents; empty when the corpus lacks the file."""
        self._data: dict[str, Any] = dict(data or {})

    @property
    def class_sources(self) -> list[str]:
        """Every source that publishes classes."""
        return sorted(self._data)

    def classes_in(self, class_source: str) -> list[str]:
        """The classes published in one source."""
        classes = self._data.get(class_source)
        return sorted(classes) if isinstance(classes, dict) else []

    def subclasses_for(self, class_name: str, class_source: str) -> dict[str, list[str]]:
        """Subclass short names for a class, as ``{subclass source: [short name]}``."""
        classes = self._data.get(class_source)
        if not isinstance(classes, dict):
            return {}
        by_source = classes.get(class_name)
        if not isinstance(by_source, dict):
            return {}
        return {
            source: sorted(names)
            for source, names in by_source.items()
            if isinstance(names, dict)
        }


class LookupTables:
    """Every ``generated/`` lookup, loaded together.

    A missing file is not an error: the corpus is user-supplied and may be partial
    (documentation/TECHSTACK.md §8). An empty lookup answers "no" to every question, which is the
    correct behaviour for a source nobody imported.
    """

    def __init__(self) -> None:
        """Start empty; :meth:`load` fills the tables."""
        self.spell_sources = SpellSourceLookup()
        self.subclasses = SubclassLookup()
        self.missing: list[str] = []

    def load(self, *roots: Path) -> None:
        """Read the lookups from the first root that has them."""
        self.spell_sources = SpellSourceLookup(self._read(SPELL_SOURCE_LOOKUP, roots))
        self.subclasses = SubclassLookup(self._read(SUBCLASS_LOOKUP, roots))
        if self.missing:
            logger.info("Lookup tables not found: %s", ", ".join(self.missing))

    def _read(self, relative: str, roots: tuple[Path, ...]) -> dict[str, Any] | None:
        """Return the first readable copy of ``relative`` across ``roots``."""
        for root in roots:
            path = root / relative
            if not path.is_file():
                continue
            try:
                data = read_json(path)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSON %s: %s", path, exc)
                continue
            if isinstance(data, dict):
                return data
            logger.warning("%s is not a JSON object; ignoring", path)
        self.missing.append(relative)
        return None


__all__ = [
    "LookupTables",
    "SpellSourceLookup",
    "SubclassLookup",
]
