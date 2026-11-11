"""What the editors' insert pop-up inserts: a result's tag (app/ui/palette.py).

A reference is written as short as the world allows: ``{@creature Goblin}`` when the
world's books resolve the bare name to that very entry (TECHSTACK.md §6c), else with its
book, ``{@creature Goblin|MM}``, so it means the same thing whatever the world declares
later. Entries addressed by a code are written by it (``{@itemProperty V|XPHB}``), and a
deity carries its pantheon in the slot before the book. The campaign's own records are
Paravent's tags, by name: ``{@npc Maren Holt}`` (app/ui/markdown.py).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.models.gamestate import SourceSet
from app.services.search import Row, SearchGroup, SearchHit, match_tier
from app.ui.markdown import LIBRARY_TAGS
from app.ui.tags import ENTITY_CATEGORY

# Category → the tag that names it; the first tag listing a category wins.
TAG_OF: dict[str, str] = {}
for _tag, _categories in ENTITY_CATEGORY.items():
    for _category in _categories:
        TAG_OF.setdefault(_category, _tag)


def tag_maker(resolver: Any, source_set: SourceSet | None) -> Callable[[Row], str | None]:
    """A function writing a search row's tag in this world, or None for a row that has
    none (a rulebook heading)."""

    def tag(row: Row) -> str | None:
        if row.category in LIBRARY_TAGS:
            return f"{{@{row.category} {row.name}}}"
        name = TAG_OF.get(row.category)
        if name is None:
            return None
        written = str(getattr(row.entry, "ref_name", None) or row.name)
        found = resolver.resolve_parts(row.category, written, None, source_set).entry
        if found is not None and str(getattr(found, "source", "")).lower() == row.source.lower():
            return f"{{@{name} {written}}}"
        if row.category == "deity":
            pantheon = str(getattr(row.entry, "pantheon", "") or "")
            return f"{{@{name} {written}|{pantheon}|{row.source}}}"
        return f"{{@{name} {written}|{row.source}}}"

    return tag


def campaign_group(
    query: str, records: Iterable[tuple[str, str, str, str]], limit: int = 6
) -> SearchGroup | None:
    """The world's own records matching ``query``, as a search group. ``records`` are
    ``(kind, name, url, context)``: kind is ``npc``, ``encounter``, ``treasure``,
    ``character`` or ``chapter``."""
    needle = query.strip().lower()
    if not needle:
        return None
    hits = []
    for kind, name, url, context in records:
        tier = match_tier(name, needle)
        if tier is not None:
            hits.append(SearchHit(Row(kind, name, "", None, url=url, context=context), tier))
    if not hits:
        return None
    hits.sort(key=lambda h: h.rank)
    return SearchGroup("campaign", tuple(hits[:limit]), len(hits))


__all__ = ["TAG_OF", "campaign_group", "tag_maker"]
