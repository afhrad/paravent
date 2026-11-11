"""Typed filters over the projections — plain Python, so mypy checks every call.

    compendium.monsters().cr_between(3, 7).of_type("dragon").in_books(world.source_set)

Deliberately **not** a string query language (documentation/TECHSTACK.md §6c): the hard
part is coercing the corpus's shapes, which :mod:`app.store.projections` already did, and
a syntax on top would add parsing errors and nothing else. A full scan of 4,458 monsters
is sub-millisecond, so there is no index either.

Each method returns a new query; nothing runs until the query is iterated.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from fractions import Fraction
from typing import Any, Generic, Protocol, Self, TypeVar

from app.models.gamestate import SourceSet
from app.store.projections import ItemProjection, MonsterProjection, SpellProjection
from app.store.reference import reprint_targets


class Sourced(Protocol):
    """Anything with a name, a source book and the corpus entry behind it."""

    @property
    def name(self) -> str: ...
    @property
    def source(self) -> str: ...
    @property
    def entry(self) -> Any: ...


R = TypeVar("R", bound=Sourced)
P = TypeVar("P", bound=Sourced)


def _key(row: Sourced) -> str:
    return f"{row.name.lower()}|{row.source.lower()}"


def in_source_set(rows: Sequence[R], source_set: SourceSet | None) -> Callable[[R], bool]:
    """A predicate keeping what the declared books allow, minus superseded printings.

    The same rule the resolver applies to a bare tag (documentation/TECHSTACK.md §6c): a
    row is dropped when it says it was reprinted as another row the set also allows. Only
    reprints *within* the allowed rows count — a book the table does not use cannot
    supersede one it does. An empty or absent set allows every book.
    """
    if source_set is None:
        source_set = SourceSet()
    allowed = {_key(row) for row in rows if source_set.allows(row.source)}

    def keep(row: R) -> bool:
        if _key(row) not in allowed:
            return False
        return not (reprint_targets(row.entry) & allowed)

    return keep


class Query(Generic[P]):
    """A lazily filtered view over one projection list."""

    def __init__(self, rows: Sequence[P], filters: tuple[Callable[[P], bool], ...] = ()) -> None:
        """Wrap ``rows``; ``filters`` all have to hold for a row to pass."""
        self._rows: Sequence[P] = rows
        self._filters: tuple[Callable[[P], bool], ...] = filters

    def where(self, predicate: Callable[[P], bool]) -> Self:
        """Narrow by any predicate over the projection."""
        return type(self)(self._rows, (*self._filters, predicate))

    def named(self, text: str) -> Self:
        """Names containing ``text``, case-insensitively."""
        needle = text.strip().lower()
        return self.where(lambda row: needle in row.name.lower()) if needle else self

    def from_source(self, *sources: str) -> Self:
        """Only rows from these books, exactly."""
        wanted = {s.lower() for s in sources}
        return self.where(lambda row: row.source.lower() in wanted)

    def in_books(self, source_set: SourceSet | None) -> Self:
        """Only what the declared books allow, one printing per entry."""
        return self.where(in_source_set(self._rows, source_set))

    def __iter__(self) -> Iterator[P]:
        """Rows passing every filter, in load order."""
        filters = self._filters
        return (row for row in self._rows if all(f(row) for f in filters))

    def all(self) -> list[P]:
        """Every matching row."""
        return list(self)

    def count(self) -> int:
        """How many rows match."""
        return sum(1 for _ in self)

    def first(self) -> P | None:
        """The first matching row, or None."""
        return next(iter(self), None)


class MonsterQuery(Query[MonsterProjection]):
    """Filters for creatures."""

    def cr_between(self, low: float | Fraction | str, high: float | Fraction | str) -> Self:
        """Challenge rating within ``[low, high]``; ``"1/4"`` works as a bound.

        Creatures without a usable rating (summons, sidekicks) never match a CR filter.
        """
        lo, hi = _fraction(low), _fraction(high)
        return self.where(lambda m: m.cr is not None and lo <= m.cr.value <= hi)

    def of_type(self, *types: str) -> Self:
        """Any of these creature types; a "celestial or fiend" matches either."""
        wanted = {t.lower() for t in types}
        return self.where(lambda m: bool(wanted & set(m.kind.types)))

    def tagged(self, *tags: str) -> Self:
        """Any of these subtype tags (``goblinoid``, ``shapechanger``)."""
        wanted = {t.lower() for t in tags}
        return self.where(lambda m: bool(wanted & {t.lower() for t in m.kind.tags}))

    def size(self, *codes: str) -> Self:
        """Any of these sizes, by code (``T S M L H G``)."""
        wanted = {c.upper() for c in codes}
        return self.where(lambda m: bool(wanted & set(m.sizes)))

    def environment(self, *environments: str) -> Self:
        """Found in any of these environments (``forest``, ``underdark``)."""
        wanted = {e.lower() for e in environments}
        return self.where(lambda m: bool(wanted & set(m.environments)))

    def legendary(self, value: bool = True) -> Self:
        """Creatures with (or without) legendary actions."""
        return self.where(lambda m: m.legendary is value)


class SpellQuery(Query[SpellProjection]):
    """Filters for spells."""

    def level(self, *levels: int) -> Self:
        """Any of these spell levels; 0 is a cantrip."""
        wanted = set(levels)
        return self.where(lambda s: s.level in wanted)

    def level_between(self, low: int, high: int) -> Self:
        """Spell level within ``[low, high]``."""
        return self.where(lambda s: low <= s.level <= high)

    def school(self, *codes: str) -> Self:
        """Any of these schools, by code (``A C D E V I N T``)."""
        wanted = {c.upper() for c in codes}
        return self.where(lambda s: s.school.upper() in wanted)

    def concentration(self, value: bool = True) -> Self:
        """Spells that do (or do not) need concentration."""
        return self.where(lambda s: s.concentration is value)

    def ritual(self, value: bool = True) -> Self:
        """Spells that can (or cannot) be cast as rituals."""
        return self.where(lambda s: s.ritual is value)


class ItemQuery(Query[ItemProjection]):
    """Filters for items and base items."""

    def rarity(self, *rarities: str) -> Self:
        """Any of these rarities (``rare``, ``very rare``)."""
        wanted = {r.lower() for r in rarities}
        return self.where(lambda i: (i.rarity or "").lower() in wanted)

    def kind(self, *codes: str) -> Self:
        """Any of these item type codes (``M``, ``HA``, ``P``, ``WD``)."""
        wanted = {c.upper() for c in codes}
        return self.where(lambda i: i.kind.upper() in wanted)

    def magic(self, value: bool = True) -> Self:
        """Magic items (or mundane ones)."""
        return self.where(lambda i: i.magic is value)

    def attunement(self, value: bool = True) -> Self:
        """Items that do (or do not) require attunement."""
        return self.where(lambda i: i.attunement is value)


def _fraction(value: float | Fraction | str) -> Fraction:
    if isinstance(value, str):
        return Fraction(value.strip())
    return Fraction(value)


__all__ = ["ItemQuery", "MonsterQuery", "Query", "Sourced", "SpellQuery", "in_source_set"]
