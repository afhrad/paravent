"""Corpus text in the reader's language (documentation/TRANSLATION.md §5).

The pages ask here for an entry's text instead of reading the English record directly:
:func:`localized` lays the language's translated passages over the record (and over a
creature's lair), passage by passage, and :func:`coverage_note` says what the reader is
looking at — a draft nobody has checked, a translation of older English, or English
because nobody has translated the rest.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from htpy import Node, p, span

from app.services.creature_builder import HOMEBREW_SOURCE
from app.services.references import ReferenceEntry
from app.services.translations import Coverage, record_key, translation_key
from app.ui.context import Page
from app.ui.icons import icon


def table_written(category: str, source: str) -> bool:
    """A creature of the creature builder: written by the table in its own language
    (Claude writes a world's creatures in the world's language), so nothing offers to
    translate it and no note calls it English."""
    return category == "monster" and source == HOMEBREW_SOURCE


def localized(page: Page, entry: ReferenceEntry) -> tuple[ReferenceEntry, Coverage]:
    """The entry with its text in the page's language where translated."""
    if table_written(entry.category, entry.source):
        return entry, Coverage()
    t = page.t
    overlaid = t.overlay(entry.category, translation_key(entry.category, entry.data), entry.data)
    lair = entry.lair
    if lair is not None:
        lair = t.overlay(
            "legendaryGroup",
            record_key(str(lair.get("name", "")), str(lair.get("source", ""))),
            lair,
        ).data
    return replace(entry, data=overlaid.data, lair=lair), overlaid.coverage


def localized_data(
    page: Page, category: str, name: str, source: str, data: dict[str, Any]
) -> tuple[dict[str, Any], Coverage]:
    """A record's data in the page's language, for pages holding the record itself."""
    overlaid = page.t.overlay(category, record_key(name, source), data)
    return overlaid.data, overlaid.coverage


def coverage_note(page: Page, coverage: Coverage, *, untranslated: bool = False) -> Node:
    """One line on how far the text is translated, or nothing when it is simply done.

    ``untranslated`` also says so when nothing is translated (the full reference page;
    a tooltip leaves that out).
    """
    t = page.t
    if page.lang == "en" or coverage.total == 0:
        return None
    if coverage.drafts:
        key, tone = "translation.note.draft", "cond"
    elif coverage.stale:
        key, tone = "translation.note.stale", "cond"
    elif coverage.any and not coverage.complete:
        key, tone = "translation.note.partial", "neutral"
    elif not coverage.any and untranslated:
        key, tone = "translation.note.english", "neutral"
    else:
        return None
    return p(class_=f"tr-note tr-{tone}")[
        icon("languages", "xs"),
        span[
            t(
                key,
                done=coverage.translated,
                total=coverage.total,
                drafts=coverage.drafts,
                stale=coverage.stale,
            )
        ],
    ]


__all__ = ["coverage_note", "localized", "localized_data"]
