"""Resolving a reference to exactly one entry.

The whole corpus stays resident, so *finding* an entry is never the problem — a keyed
lookup measures 0.24 µs. The problem is knowing **which** entry a reference means, because
most references do not name a source: 87% of ``{@spell …}`` tags and 44% of
``{@creature …}`` tags are bare, and 379 of 557 spell names exist in two or more books
(documentation/TECHSTACK.md §6c).

Resolution is four deterministic steps:

1. An explicit source on the reference → exact keyed lookup, done.
2. Filter the candidates to the world's declared source set.
3. Drop candidates superseded via ``reprintedAs`` *within that set*.
4. Exactly one survivor → resolve. Zero survivors → the "not in your books" state, which
   is a correct outcome rather than an error (documentation/DESIGN.md §6).

Step 3 costs nothing because the corpus already carries the reprint graph, so there is no
hand-maintained book-precedence table to drift out of date.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models.gamestate import SourceSet
from app.store.copyresolver import entry_key

logger = logging.getLogger(__name__)


class Outcome(StrEnum):
    """Why a resolution ended the way it did.

    Carried on the result so the UI can tell the two unresolved cases apart: a book the
    user never imported needs "import source", one excluded by the world's declaration
    needs "add to this world's books" (documentation/DESIGN.md §6).
    """

    EXACT = "exact"
    """An explicit source matched a loaded entry."""

    RESOLVED = "resolved"
    """A bare reference narrowed to one entry."""

    NOT_IMPORTED = "not_imported"
    """Nothing by that name is loaded at all."""

    NOT_IN_SOURCE_SET = "not_in_source_set"
    """Candidates exist, but none from a declared book."""

    AMBIGUOUS = "ambiguous"
    """Several survived. Should not happen — see :meth:`Resolution.is_unexpected`."""


@dataclass(frozen=True)
class Resolution:
    """What a reference resolved to, and how."""

    outcome: Outcome
    entry: Any | None = None
    candidates: tuple[Any, ...] = ()
    superseded: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        """True when an entry was found."""
        return self.entry is not None

    @property
    def is_unexpected(self) -> bool:
        """True for an outcome the measured data says should not occur.

        Measured against the real corpus, a declared source set plus ``reprintedAs``
        supersession resolves **every** ambiguous name — including the worst case of
        both editions declared at once (1,357 ambiguous names, 0 unresolved). So an
        ambiguous result means the corpus has a shape we have not seen, and is worth
        surfacing rather than silently picking the first.
        """
        return self.outcome is Outcome.AMBIGUOUS

    @property
    def alternatives(self) -> tuple[Any, ...]:
        """Printings the user declared but supersession set aside.

        5eTools models the 2024 books as reprints of the 2014 ones, so a both-editions
        table silently gets the newer entry. That is the right default but it is a
        decision, and it must be visible: the tooltip offers these as a secondary action
        rather than dropping a book the user explicitly declared.
        """
        return self.superseded


# Tags whose slot 2 is **not** a source. ``{@deity Bahamut|Nonhuman|PHB}`` puts the
# pantheon there and the source in slot 3, so parsing a deity like a creature would look
# for a book called "Nonhuman". ``{@card Sun|Deck of Many Things|DMG}`` does the same
# with the deck. Verified against the corpus: these are the only entity tags that shift
# the source along (documentation/TRANSLATION.md §3 notes the same trap for label
# substitution).
SOURCE_IN_SLOT_3: frozenset[str] = frozenset({"deity", "card"})


def parse_reference(
    payload: str, category: str | None = None
) -> tuple[str, str | None, str | None]:
    """Split a tag payload into ``(name, source, display)``.

    5eTools addresses a target as ``name``, ``name|SOURCE`` or
    ``name|SOURCE|display text``. Slot 3 is a display override, used heavily for
    inflection (``{@creature troll||trolls}``) and, under a translation, for the
    localized label. An empty slot means "unspecified", not an empty string.

    Pass ``category`` so that tags in :data:`SOURCE_IN_SLOT_3` are read correctly; the
    returned middle value is always the source, whichever slot it came from.
    """
    parts = [p.strip() for p in payload.split("|")]
    name = parts[0]

    def slot(i: int) -> str | None:
        return parts[i] if len(parts) > i and parts[i] else None

    if category in SOURCE_IN_SLOT_3:
        # name | pantheon | source | display
        return name, slot(2), slot(3)
    return name, slot(1), slot(2)


def reprint_targets(entry: Any) -> set[str]:
    """The keys this entry declares it was reprinted as.

    ``reprintedAs`` holds either a ``"Name|SOURCE"`` string or, for 69 cross-category
    reprints, a dict carrying the same string under ``uid`` plus a ``tag`` naming the
    target category. Both forms are read; the tag is ignored because supersession is
    only applied within a category.
    """
    raw = getattr(entry, "reprintedAs", None)
    if raw is None and isinstance(entry, dict):
        raw = entry.get("reprintedAs")
    if not isinstance(raw, list):
        return set()

    targets: set[str] = set()
    for item in raw:
        uid = item if isinstance(item, str) else (item or {}).get("uid")
        if not isinstance(uid, str) or not uid:
            continue
        name, source, _ = parse_reference(uid)
        if source:
            targets.add(entry_key(name, source))
    return targets


# Categories whose identity needs more than ``(name, source)``, so a bare name cannot
# address one of them. A class feature called "Potent Spellcasting" exists for several
# classes at several levels; nothing in the corpus references one by name alone, and
# bucketing them by name would manufacture ambiguity the data does not have. Features
# are reached through their parent class instead.
COMPOSITE_KEY_CATEGORIES: frozenset[str] = frozenset(
    {"subclass", "classFeature", "subclassFeature", "card"}
)


class ReferenceIndex:
    """Candidate buckets by bare name, built once per load.

    The compendium already keys entries exactly. What a bare reference needs is the
    *list* of entries sharing a name, which this adds: 0.21 µs per lookup against the
    real corpus.
    """

    def __init__(self) -> None:
        """Start empty; :meth:`build` fills the buckets."""
        self._buckets: dict[str, dict[str, list[Any]]] = {}

    def build(self, entries_by_category: dict[str, dict[str, Any]]) -> None:
        """Index every loaded entry by ``(category, lowercased name)``.

        Composite-key categories are skipped: see :data:`COMPOSITE_KEY_CATEGORIES`.
        """
        buckets: dict[str, dict[str, list[Any]]] = {}
        for category, entries in entries_by_category.items():
            if category in COMPOSITE_KEY_CATEGORIES:
                continue
            by_name: dict[str, list[Any]] = defaultdict(list)
            for model in entries.values():
                # What tags name it by: the abbreviation, for an item property.
                name = getattr(model, "ref_name", None) or getattr(model, "name", None)
                if isinstance(name, str) and name:
                    by_name[name.lower()].append(model)
                # A subrace also by the name tags write it as: "Elf (Wood)".
                composed = getattr(model, "composed_name", None)
                if isinstance(composed, str) and composed.lower() != str(name).lower():
                    by_name[composed.lower()].append(model)
            if by_name:
                buckets[category] = dict(by_name)
        self._buckets = buckets

    @property
    def categories(self) -> list[str]:
        """Categories with at least one indexed entry."""
        return sorted(self._buckets)

    def name_count(self) -> int:
        """How many distinct names are indexed, across every category."""
        return sum(len(names) for names in self._buckets.values())

    def candidates(self, category: str, name: str) -> list[Any]:
        """Every loaded entry in ``category`` with this name, any source."""
        return list(self._buckets.get(category, {}).get(name.lower(), ()))


class ReferenceResolver:
    """Turns a reference into one entry, given the books a table declared."""

    def __init__(self, index: ReferenceIndex, exact: dict[str, dict[str, Any]]) -> None:
        """``exact`` is the compendium's own ``(category, key) -> model`` mapping."""
        self._index = index
        self._exact = exact

    def resolve(
        self,
        category: str,
        payload: str,
        source_set: SourceSet | None = None,
    ) -> Resolution:
        """Resolve a tag payload such as ``"fireball"`` or ``"fireball|PHB"``."""
        name, source, _ = parse_reference(payload, category)
        return self.resolve_parts(category, name, source, source_set)

    def resolve_parts(
        self,
        category: str,
        name: str,
        source: str | None = None,
        source_set: SourceSet | None = None,
    ) -> Resolution:
        """Resolve a reference already split into its parts."""
        declared = source_set or SourceSet()

        # Step 1 — an explicit source is an exact lookup. The declared set still
        # applies: a reference into a book the table excluded must not resolve, or the
        # set would stop being a correctness boundary.
        if source:
            found = self._exact.get(category, {}).get(entry_key(name, source))
            if found is None:
                # A subrace is keyed by its own name ("Wood"); a tag writes the composed
                # one ("Elf (Wood)|PHB"), which the name index holds.
                found = next(
                    (
                        e
                        for e in self._index.candidates(category, name)
                        if str(getattr(e, "source", "")).lower() == source.lower()
                    ),
                    None,
                )
            if found is None:
                return Resolution(Outcome.NOT_IMPORTED)
            if not declared.allows(source):
                return Resolution(Outcome.NOT_IN_SOURCE_SET, candidates=(found,))
            return Resolution(Outcome.EXACT, entry=found, candidates=(found,))

        all_candidates = self._index.candidates(category, name)
        if not all_candidates:
            return Resolution(Outcome.NOT_IMPORTED)

        # Step 2 — narrow to the declared books.
        allowed = [
            entry for entry in all_candidates if declared.allows(getattr(entry, "source", None))
        ]
        if not allowed:
            return Resolution(Outcome.NOT_IN_SOURCE_SET, candidates=tuple(all_candidates))
        if len(allowed) == 1:
            return Resolution(Outcome.RESOLVED, entry=allowed[0], candidates=tuple(allowed))

        # Step 3 — drop anything reprinted as another candidate we also hold.
        survivors, superseded = self._apply_supersession(allowed)

        if len(survivors) == 1:
            return Resolution(
                Outcome.RESOLVED,
                entry=survivors[0],
                candidates=tuple(allowed),
                superseded=tuple(superseded),
            )
        if not survivors:
            # Every candidate was superseded by another in the set, which means the
            # reprint graph is cyclic. Prefer the declared entries over nothing.
            return Resolution(Outcome.AMBIGUOUS, candidates=tuple(allowed))

        # Step 4 — more than one survived, because the corpus does not always record a
        # reprint link where one exists in print. Measured: with a 2014 set this affects
        # 136 names, overwhelmingly deities (105) and legendary groups (17), where
        # 5eTools simply reprints an entry without pointing back.
        #
        # Falling back to declaration order is deliberate and visible: the user listed
        # their books, so the earlier-listed one wins, and every candidate stays on the
        # result for the UI to offer. Picking silently by some global rule would hide a
        # real gap; refusing outright would make a common case unusable.
        ranked = self._by_declaration_order(survivors, declared)
        return Resolution(
            Outcome.AMBIGUOUS,
            entry=ranked[0],
            candidates=tuple(ranked),
            superseded=tuple(superseded),
        )

    @staticmethod
    def _by_declaration_order(candidates: list[Any], declared: SourceSet) -> list[Any]:
        """Order candidates by where their source appears in the declaration.

        Unrestricted sets have no declaration to order by, so the order is left as the
        index produced it — stable across runs because the compendium loads files in
        sorted order.
        """
        if declared.is_unrestricted:
            return candidates
        order = {source: i for i, source in enumerate(s.lower() for s in declared.sources)}
        return sorted(
            candidates,
            key=lambda e: order.get(str(getattr(e, "source", "")).lower(), len(order)),
        )

    def _apply_supersession(self, candidates: list[Any]) -> tuple[list[Any], list[Any]]:
        """Split candidates into survivors and those reprinted as a sibling.

        Only reprints *within the candidate set* count. A book the table does not have
        cannot supersede one it does — otherwise declaring fewer books would resolve to
        nothing.
        """
        keys = {key for entry in candidates for key in self._keys_of(entry)}
        survivors: list[Any] = []
        superseded: list[Any] = []
        for entry in candidates:
            if reprint_targets(entry) & keys:
                superseded.append(entry)
            else:
                survivors.append(entry)
        return survivors, superseded

    @classmethod
    def _keys_of(cls, entry: Any) -> set[str]:
        """Every ``name|source`` a reprint may name the entry by: a subrace also by its
        composed name ("Genasi (Fire)|MPMM" for MPMM's Fire)."""
        keys = {cls._key_of(entry)}
        composed = getattr(entry, "composed_name", None)
        if isinstance(composed, str):
            keys.add(f"{composed.lower()}|{str(getattr(entry, 'source', '')).lower()}")
        return keys

    @staticmethod
    def _key_of(entry: Any) -> str:
        """The ``name|source`` key for a loaded entry (``abbreviation|source`` for one
        addressed by its code, as its ``reprintedAs`` is)."""
        name = str(getattr(entry, "ref_name", None) or getattr(entry, "name", "")).lower()
        source = str(getattr(entry, "source", "")).lower()
        return f"{name}|{source}"


__all__ = [
    "COMPOSITE_KEY_CATEGORIES",
    "SOURCE_IN_SLOT_3",
    "Outcome",
    "ReferenceIndex",
    "ReferenceResolver",
    "Resolution",
    "parse_reference",
    "reprint_targets",
]
