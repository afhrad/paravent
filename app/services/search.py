"""One search across the whole reference set (documentation/DESIGN.md §4, the ⌘K palette).

You never choose a category first: a query runs over spells, creatures, items, rules and
character options at once, and the results come back grouped, the best-matching group
first. A category chip narrows it when you want to.

**Scoped to the world's books.** Results pass the same source-set rule the resolver
applies to a bare tag (documentation/TECHSTACK.md §6c): only declared books, one printing
per entry. What that hides is counted, so the palette can offer "search all imported
books" instead of silently showing less.

Features (``classFeature``, ``subclassFeature``), subclasses and legendary groups are not
searched: their names repeat across classes and levels, and they are reached through
their parent entry, never on their own.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import SourceSet
from app.store.projections import Projections
from app.store.published import PublishedBooks, chapter_headings
from app.store.query import in_source_set

GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spells", ("spell",)),
    ("bestiary", ("monster",)),
    (
        "items",
        ("item", "baseitem", "magicvariant", "itemGroup", "vehicle", "vehicleUpgrade", "deck"),
    ),
    (
        "options",
        (
            "class",
            "race",
            "subrace",
            "background",
            "feat",
            "optionalfeature",
            "charoption",
            "psionic",
        ),
    ),
    (
        "rules",
        (
            "condition",
            "status",
            "disease",
            "action",
            "variantrule",
            "skill",
            "sense",
            "itemMastery",
        ),
    ),
    (
        "world",
        (
            "deity",
            "language",
            "trap",
            "hazard",
            "reward",
            "object",
            "table",
            "facility",
            "cult",
            "boon",
        ),
    ),
    # The rulebooks' chapters and headings (TECHSTACK.md §6b phase 3), from their index.
    ("books", ("book",)),
)
GROUP_KEYS: tuple[str, ...] = tuple(key for key, _ in GROUPS)

PER_GROUP = 6  # in "all", so every group gets a look-in
PER_GROUP_FOCUSED = 50  # with one group chosen


@dataclass(frozen=True, slots=True)
class Row:
    """A searchable entry; ``projection`` is set for monsters, spells and items."""

    category: str
    name: str
    source: str
    entry: Any
    projection: Any = None
    # Rows that are not compendium entries (a book's heading) carry their own link and
    # say where they sit.
    url: str | None = None
    context: str = ""


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One match, with how well it matched (0 exact … 3 somewhere inside the name)."""

    row: Row
    tier: int
    # The entry's name in the reader's language, from the glossary, when it has one.
    label: str | None = None

    @property
    def rank(self) -> tuple[int, int, str]:
        """Sort key: better tier, then shorter name, then alphabetical."""
        return (self.tier, len(self.row.name), self.row.name.lower())


@dataclass(frozen=True)
class SearchGroup:
    """The hits of one group, best first; ``total`` counts beyond the shown ones."""

    key: str
    hits: tuple[SearchHit, ...]
    total: int


@dataclass(frozen=True)
class SearchResult:
    """What a query found.

    ``outside`` counts matches in books the world does not declare — zero when the search
    already covers everything. ``restricted`` says whether a source set applied at all.
    """

    query: str
    group: str | None
    groups: tuple[SearchGroup, ...]
    outside: int = 0
    restricted: bool = False
    all_books: bool = False

    @property
    def total(self) -> int:
        """Matches across every group, shown or not."""
        return sum(g.total for g in self.groups)


def match_tier(name: str, needle: str) -> int | None:
    """0 exact, 1 prefix, 2 a later word starts with it, 3 inside a word; None no match."""
    lowered = name.lower()
    if lowered == needle:
        return 0
    if lowered.startswith(needle):
        return 1
    position = lowered.find(needle)
    if position < 0:
        return None
    return 2 if not lowered[position - 1].isalnum() else 3


class SearchService:
    """Searches the loaded compendium; rows are rebuilt whenever it reloads."""

    def __init__(self, compendium: Any, books: PublishedBooks | None = None) -> None:
        """Bind to a compendium store, and the rulebooks whose headings are searched too."""
        self._compendium = compendium
        self._books = books
        self._built_for: Projections | None = None
        self._rows: dict[str, tuple[Row, ...]] = {}
        self._scopes: dict[str, dict[str, Callable[[Row], bool]]] = {}

    def _ensure_rows(self) -> dict[str, tuple[Row, ...]]:
        projections = self._compendium.projections
        if projections is self._built_for:
            return self._rows
        projected: dict[int, Any] = {}
        for p in (*projections.monsters, *projections.spells, *projections.items):
            projected[id(p.entry)] = p
        rows: dict[str, tuple[Row, ...]] = {}
        for _group, categories in GROUPS:
            for category in categories:
                if category == "book":
                    rows[category] = self._book_rows()
                    continue
                rows[category] = tuple(
                    Row(
                        category=category,
                        name=str(getattr(entry, "name", "")),
                        source=str(getattr(entry, "source", "")),
                        entry=entry,
                        projection=projected.get(id(entry)),
                    )
                    for entry in self._compendium.all(category)
                )
        self._rows, self._scopes, self._built_for = rows, {}, projections
        return rows

    def _book_rows(self) -> tuple[Row, ...]:
        """Every chapter and heading of every rulebook — the bodies read once, ~0.2 s."""
        if self._books is None:
            return ()
        rows = []
        for book in self._books.all():
            body = self._books.body(book.id)
            if body is None:
                continue
            for index, chapter in enumerate(body.sections):
                url = f"/books/{book.id}/{index}"
                rows.append(
                    Row("book", chapter.name, book.source, book, url=url, context=book.name)
                )
                rows.extend(
                    Row(
                        "book",
                        name,
                        book.source,
                        book,
                        url=f"{url}#{anchor}",
                        context=f"{book.name} · {chapter.name}",
                    )
                    for _depth, name, anchor in chapter_headings(chapter)
                )
        return tuple(rows)

    def _scope(self, source_set: SourceSet | None) -> dict[str, Callable[[Row], bool]]:
        """Per category: does this row survive the declared books and supersession?"""
        rows = self._ensure_rows()
        identity = source_set.identity if source_set is not None else ""
        if identity not in self._scopes:
            if len(self._scopes) > 32:  # one per world in practice; never unbounded
                self._scopes.clear()
            self._scopes[identity] = {
                category: in_source_set(category_rows, source_set)
                for category, category_rows in rows.items()
            }
        return self._scopes[identity]

    def search(
        self,
        query: str,
        source_set: SourceSet | None = None,
        *,
        group: str | None = None,
        all_books: bool = False,
        term: Callable[[str, str], str | None] | None = None,
    ) -> SearchResult:
        """Search every group (or one), ranked, within the books the world declares.

        With ``term`` (the reader's glossary), a localized name matches too: "Feuerball"
        finds Fireball, and the hit carries the name to show.
        """
        needle = query.strip().lower()
        group = group if group in GROUP_KEYS else None
        restricted = source_set is not None and not source_set.is_unrestricted
        if not needle:
            return SearchResult(query, group, (), restricted=restricted, all_books=all_books)
        rows = self._ensure_rows()
        # "All imported books" is the escape from the world's choices, supersession
        # included: it shows every printing, so the 2014 goblin is findable next to 2024's.
        scope = None if all_books else self._scope(source_set)
        books = None if all_books else source_set

        groups: list[SearchGroup] = []
        outside = 0
        for key, categories in GROUPS:
            if group is not None and key != group:
                continue
            hits: list[SearchHit] = []
            for category in categories:
                keep = scope[category] if scope is not None else None
                for row in rows[category]:
                    tier = match_tier(row.name, needle)
                    label = term(category, row.name) if term is not None else None
                    if label is not None:
                        localized = match_tier(label, needle)
                        if localized is not None and (tier is None or localized < tier):
                            tier = localized
                    if tier is None:
                        continue
                    if keep is None or keep(row):
                        hits.append(SearchHit(row, tier, label))
                    elif books is not None and not books.allows(row.source):
                        outside += 1
            if hits:
                hits.sort(key=lambda h: h.rank)
                limit = PER_GROUP_FOCUSED if group else PER_GROUP
                groups.append(SearchGroup(key, tuple(hits[:limit]), len(hits)))
        groups.sort(key=lambda g: g.hits[0].rank)
        return SearchResult(
            query=query.strip(),
            group=group,
            groups=tuple(groups),
            outside=outside,
            restricted=restricted,
            all_books=all_books,
        )

    def browse(
        self,
        categories: Sequence[str],
        source_set: SourceSet | None,
        *,
        weight: Callable[[Row], int],
        limit: int,
        term: Callable[[str, str], str | None] | None = None,
    ) -> list[SearchHit]:
        """Entries of ``categories`` in the world's books, heaviest first, then by name —
        what the editor offers when asked before a name is typed."""
        rows = self._ensure_rows()
        scope = self._scope(source_set)
        found = [
            row for category in categories for row in rows.get(category, ()) if scope[category](row)
        ]
        found.sort(key=lambda row: (-weight(row), row.name.casefold()))
        return [
            SearchHit(row, 0, term(row.category, row.name) if term is not None else None)
            for row in found[:limit]
        ]


def flatten(groups: Sequence[SearchGroup]) -> list[SearchHit]:
    """Every shown hit, in display order."""
    return [hit for g in groups for hit in g.hits]


__all__ = [
    "GROUPS",
    "GROUP_KEYS",
    "Row",
    "SearchGroup",
    "SearchHit",
    "SearchResult",
    "SearchService",
    "flatten",
    "match_tier",
]
