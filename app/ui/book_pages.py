"""A published adventure's section with the gamemaster's notes in it (ADVENTURES.md §7).

The section is the corpus's text, rendered by the entries renderer and never copied.
The chapter's own Markdown is the overlay:

* text outside any ``:::: at`` block shows at the top;
* ``:::: at 00e`` … ``::::`` shows right after the book's entry ``00e``, marked as the
  gamemaster's;
* a block whose entry no longer exists — a corpus update renumbered the book — shows at
  the end, under its id, rather than disappearing.

Named entries become headings (the book's own structure, so the table of contents and
the "add a note here" links come from it). Images, which this corpus ships without
files, become captioned placeholders; ``statblock`` entries become references, a
creature's with its statblock folded beneath.

The result is a :class:`~app.ui.markdown.Rendered`, like a chapter of the gamemaster's
own, so the chapter page, its contents list and its live updates need nothing new.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from htpy import (
    Node,
    a,
    aside,
    div,
    figcaption,
    figure,
    fragment,
    h2,
    h3,
    h4,
    h5,
    img,
    polygon,
    section,
    span,
    svg,
)
from htpy import title as title_el
from markupsafe import Markup

from app.models.gamestate import SourceSet
from app.services.creature_sight import image_file
from app.services.translations import CHAPTER_FIELDS
from app.store.published import AdventureBody, Section
from app.store.reference import parse_reference
from app.ui import tags
from app.ui.context import Page
from app.ui.icons import icon
from app.ui.markdown import (
    Heading,
    MarkdownContext,
    Rendered,
    render_markdown,
    share_link,
    split_anchors,
)
from app.ui.translate_pages import translate_url
from app.ui.translated import coverage_note

HEADINGS = (h2, h3, h4, h5)
NAMED_BLOCKS = frozenset({"entries", "section"})
# 5eTools statblock tags → the entity tag that links them.
STATBLOCK_TAG = {"creature": "creature", "item": "item", "spell": "spell", "reward": "reward"}


def anchor_id(entry_id: str) -> str:
    """The HTML id a book entry is reachable at."""
    return f"e-{entry_id}"


def _note(page: Page, rendered: Rendered, label: str | None = None) -> Node:
    return aside(class_="gm-note")[
        div(class_="gm-note-label")[icon("pencil", "xs"), label or page.t("book.your_note")],
        div(class_="md-body")[rendered.html],
    ]


# The book's boxes the gamemaster may hand to players (documentation/JOURNAL.md).
SHAREABLE = frozenset({"insetReadaloud", "inset", "variant", "variantSub", "variantInner", "quote"})


def book_key(entry: dict[str, Any]) -> str:
    """What names a book's box for sharing: its id, else its content."""
    ident = entry.get("id")
    if isinstance(ident, str) and ident:
        return ident
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]


def find_book_entry(data: Any, key: str) -> dict[str, Any] | None:
    """The shareable box of a book's text that ``key`` names, searched depth first."""
    if isinstance(data, dict):
        if data.get("type") in SHAREABLE and book_key(data) == key:
            return data
        children: Iterable[Any] = data.values()
    elif isinstance(data, list):
        children = data
    else:
        return None
    for child in children:
        found = find_book_entry(child, key)
        if found is not None:
            return found
    return None


def book_picture(images: Path | None) -> Callable[[str], str | None]:
    """The address of a book picture (a path in the 5eTools image folder ``images``)
    when the folder holds it, else None."""

    def picture(path: str) -> str | None:
        if images is None or image_file(images, path) is None:
            return None
        return f"/play/img/{quote(path)}"

    return picture


def image_path(entry: dict[str, Any]) -> str | None:
    """A book picture's path in the 5eTools image folder, if it names one."""
    href = entry.get("href")
    if isinstance(href, dict) and href.get("type") == "internal" and href.get("path"):
        return str(href["path"])
    return None


# Where a map's numbered area leads: (address, the area's name), or None for nowhere.
AreaLink = Callable[[str], tuple[str, str] | None]


def area_links(body: AdventureBody, here: str, chapters: Mapping[str, str]) -> AreaLink:
    """Where a map's areas lead in the section ``here`` of ``body``: an area in this
    section to its heading, one in another section to that heading in the chapter that
    shows it (``chapters``: section id → chapter address), else nowhere."""

    def lead(entry_id: str) -> tuple[str, str] | None:
        entry = body.entry(entry_id)
        name = entry.get("name") if entry is not None else None
        if entry is None or not isinstance(name, str):
            return None
        if entry.get("type", "entries") not in NAMED_BLOCKS:
            return None
        section_id = body.section_of(entry_id)
        if section_id == here:
            return f"#{anchor_id(entry_id)}", tags.plain(name)
        chapter = chapters.get(section_id or "")
        return (f"{chapter}#{anchor_id(entry_id)}", tags.plain(name)) if chapter else None

    return lead


def _map_areas(entry: dict[str, Any], area: AreaLink | None) -> Node:
    """A map's numbered areas as links over the picture, each to its text: an SVG laid
    over the image in the map's own pixels, so it scales with it. None when it has none."""
    width, height = entry.get("width"), entry.get("height")
    if area is None or not isinstance(width, int) or not isinstance(height, int):
        return None
    links = []
    for region in entry.get("mapRegions") or []:
        if not isinstance(region, dict) or not isinstance(region.get("area"), str):
            continue
        found = area(region["area"])
        points = region.get("points") or []
        if found is None or len(points) < 3:
            continue
        href, name = found
        drawn = " ".join(f"{x},{y}" for x, y in points)
        links.append(a(href=href, aria_label=name)[polygon(points=drawn)[title_el[name]]])
    if not links:
        return None
    return svg(class_="map-areas", viewBox=f"0 0 {width} {height}", preserveAspectRatio="none")[
        links
    ]


def book_image(
    page: Page,
    entry: dict[str, Any],
    picture: Callable[[str], str | None] | None = None,
    share: Callable[[str, str], str | None] | None = None,
    area: AreaLink | None = None,
) -> Node:
    """A book's picture from the image folder when it holds it, with its share button
    and, on a map, its numbered areas leading to their text (``area``); else a captioned
    placeholder. An image with an id is reachable at its anchor, as the maps list links."""
    title = entry.get("title") or entry.get("altText") or page.t("book.image")
    path = image_path(entry)
    url = picture(path) if picture is not None and path else None
    ident = anchor_id(entry["id"]) if isinstance(entry.get("id"), str) else None
    if url is not None and path is not None:
        shared = share("bookimage", path) if share is not None else None
        areas = _map_areas(entry, area)
        return figure(class_="book-image book-picture", id=ident)[
            span(class_="md-shareable")[
                span(class_="map-frame")[img(src=url, alt=str(title), loading="lazy"), areas]
                if areas is not None
                else img(src=url, alt=str(title), loading="lazy"),
                share_link(shared, page.t("journal.share")) if shared else None,
            ],
            figcaption[span(class_="book-image-title")[str(title)]] if entry.get("title") else None,
        ]
    return figure(class_="book-image", id=ident)[
        icon("map" if entry.get("imageType") == "map" else "scroll", "lg", muted=True),
        figcaption[
            span(class_="book-image-title")[str(title)],
            span(class_="book-image-note")[page.t("book.image_missing")],
        ],
    ]


def book_chapter(
    page: Page,
    body: AdventureBody,
    part: Section,
    ctx: MarkdownContext,
    overlay: str,
    note_url: Callable[[str], str],
    picture: Callable[[str], str | None] | None = None,
    area: AreaLink | None = None,
) -> Rendered:
    """The section ``part`` of ``body`` with ``overlay``'s notes woven in.

    ``note_url(entry_id)`` is where the "add a note here" link on each heading goes;
    ``picture(path)`` the URL of a book picture the image folder holds, or None;
    ``area(entry_id)`` where a map's numbered area leads. With ``ctx.share``, the book's
    boxes and pictures carry the share button.
    """
    t = page.t
    # The book's text in the reader's language where translated; ids, and so anchors
    # and notes, are the English record's.
    translated = t.overlay(
        "adventure", f"{body.adventure.id}|{part.id}", part.entry, CHAPTER_FIELDS
    )
    rest, anchored = split_anchors(overlay)
    notes: dict[str, list[Rendered]] = {}
    orphans: list[tuple[str, Rendered]] = []
    for entry_id, text in anchored:
        rendered = render_markdown(text, ctx)
        if body.section_of(entry_id) == part.id:
            notes.setdefault(entry_id, []).append(rendered)
        else:
            orphans.append((entry_id, rendered))

    depth = {ident: d for ident, _name, d in body.places(part.id)}
    toc: list[Heading] = []

    def hook(entry: dict[str, Any], default: Callable[[], Node]) -> Node | None:
        kind = entry.get("type", "entries")
        ident = entry.get("id") if isinstance(entry.get("id"), str) else None
        name = entry.get("name")
        if kind == "image":
            shown: Node = book_image(page, entry, picture, ctx.share, area)
        elif kind == "gallery":
            shown = div(class_="book-gallery")[
                [book_image(page, i, picture, ctx.share, area) for i in entry.get("images", [])]
            ]
        elif kind in SHAREABLE and ctx.share is not None:
            url = ctx.share("book", book_key(entry))
            shown = div(class_="md-shareable-block")[
                default(), share_link(url, t("journal.share")) if url else None
            ]
        elif kind == "statblock":
            shown = _statblock_entry(entry, ctx)
        elif ident and isinstance(name, str) and kind in NAMED_BLOCKS and ident in depth:
            level = min(depth[ident], len(HEADINGS) - 1)
            if level <= 1:  # a book section has dozens of named entries; two levels list
                toc.append(Heading(level + 1, tags.plain(name), anchor_id(ident)))
            heading = HEADINGS[level](class_="book-heading")[
                tags.render(name, inner_ctx),
                a(
                    href=note_url(ident),
                    class_="book-note-link",
                    title=t("book.add_note"),
                    aria_label=t("book.add_note_at", name=tags.plain(name)),
                )["+"],
            ]
            inner = entry.get("entries") or []
            return section(class_="book-entry", id=anchor_id(ident))[
                heading,
                tags.render_entries(inner, inner_ctx),
                [_note(page, n) for n in notes.get(ident, [])],
            ]
        elif ident and ident in notes:
            shown = default()
        else:
            return None
        if ident and ident in notes:
            return [shown, [_note(page, n) for n in notes[ident]]]
        return shown

    inner_ctx = replace(ctx.tags, block_hook=hook)
    book_text = tags.render_entries(translated.data.get("entries") or [], inner_ctx)
    top = render_markdown(rest, ctx) if rest.strip() else None
    html = fragment[
        _note(page, top, t("book.your_notes")) if top is not None else None,
        coverage_note(page, translated.coverage),
        a(
            href=translate_url("adventure", body.adventure.id, part.id, back=page.path),
            class_="btn btn-sm book-translate",
        )[icon("languages", "sm"), span[t("translate.text")]],
        div(class_="book-text")[book_text],
        [_note(page, n) for n in notes.get(part.id, [])],
        section(class_="book-orphans")[
            h2[t("book.orphans")],
            [
                _note(page, rendered, t("book.orphan_at", anchor=entry_id))
                for entry_id, rendered in orphans
            ],
        ]
        if orphans
        else None,
    ]
    return Rendered(Markup(str(html)), tuple(toc))


def _statblock_entry(entry: dict[str, Any], ctx: MarkdownContext) -> Node:
    tag = str(entry.get("tag", "creature"))
    name = str(entry.get("name", ""))
    source = str(entry.get("source", ""))
    link = tags.render(f"{{@{STATBLOCK_TAG.get(tag, tag)} {name}|{source}}}", ctx.tags)
    folded = (
        ctx.embed("statblock", f"{name}|{source}")
        if tag == "creature" and ctx.embed is not None
        else None
    )
    return folded if folded is not None else div(class_="book-statblock")[link]


# -- creatures mentioned -----------------------------------------------------------------


@dataclass(frozen=True)
class Mention:
    """A creature the section mentions, resolved, and how often."""

    name: str
    source: str
    count: int


def _strings(entry: Any) -> Iterable[str]:
    if isinstance(entry, str):
        yield entry
    elif isinstance(entry, dict):
        for key, value in entry.items():
            if key not in ("id", "type", "href", "source", "page"):
                yield from _strings(value)
    elif isinstance(entry, list):
        for value in entry:
            yield from _strings(value)


def _creature_tags(text: str) -> Iterable[str]:
    for node in tags.parse(text):
        if isinstance(node, tags.Tag):
            if node.name == "creature":
                yield "|".join(node.parts)
            for part in node.parts:
                yield from _creature_tags(part)


def creatures_in(part: Section, resolver: Any, source_set: SourceSet | None) -> list[Mention]:
    """Every creature the section names, resolved in these books, most mentioned first."""
    counts: Counter[tuple[str, str]] = Counter()
    for text in _strings(part.entry):
        for payload in _creature_tags(text):
            name, source, _display = parse_reference(payload, "creature")
            result = resolver.resolve_parts("monster", name, source, source_set)
            if result.entry is not None:
                counts[(str(result.entry.name), str(result.entry.source))] += 1
    return [
        Mention(name, source, n)
        for (name, source), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0]))
    ]


__all__ = ["Mention", "anchor_id", "book_chapter", "creatures_in"]
