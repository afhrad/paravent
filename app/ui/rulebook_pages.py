"""The rulebooks, readable (documentation/TECHSTACK.md §6b, phase 3).

Three pages: the books of the world (by group), a book's contents, and one chapter.
A chapter renders its named sections as headings by depth — as a printed book does —
each with an anchor (:func:`heading_anchor`, the same the search uses), and a contents
list of them beside the text. Everything inside is the tag renderer's: links resolve in
the world's books, dice roll, names follow the glossary.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    aside,
    details,
    div,
    h1,
    h2,
    h3,
    h4,
    h5,
    li,
    nav,
    p,
    section,
    small,
    span,
    summary,
    ul,
)

from app.services.translations import CHAPTER_FIELDS, Overlaid
from app.store.published import Anchors, BookBody, PublishedBook, heading_anchor, is_heading
from app.ui.book_pages import book_image
from app.ui.context import Page
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.player_view import view_switch
from app.ui.tags import RenderContext, render, render_entries
from app.ui.translate_pages import translate_url
from app.ui.translated import coverage_note

BOOKS_URL = "/books"
# How the index's groups are shown, in this order; anything else is "other".
GROUPS = (
    ("core", ("core",)),
    ("supplement", ("supplement", "supplement-alt")),
    ("setting", ("setting", "setting-alt")),
    ("other", ()),
)
HEADINGS = (h2, h3, h4, h5)


def books_url(world_id: str | None = None) -> str:
    """The list of books, in a world."""
    return f"{BOOKS_URL}?{urlencode({'world': world_id})}" if world_id else BOOKS_URL


def book_url(book_id: str, index: int | None = None, world_id: str | None = None) -> str:
    """A book's contents, or one of its chapters, in a world."""
    url = f"{BOOKS_URL}/{book_id}" + (f"/{index}" if index is not None else "")
    return f"{url}?{urlencode({'world': world_id})}" if world_id else url


def books_page(page: Page, books: Sequence[PublishedBook], world_id: str | None) -> Element:
    """The rulebooks the world declares (or all), by group."""
    t = page.t
    grouped: dict[str, list[PublishedBook]] = {key: [] for key, _ in GROUPS}
    for book in books:
        key = next((k for k, groups in GROUPS if book.group in groups), "other")
        grouped[key].append(book)
    return app_shell(
        page,
        t("books.title"),
        div(class_="bk-page")[
            page_head(t("books.eyebrow"), t("books.title"), t("books.lede")),
            div(class_="sc-links")[
                a(href=screen_url("quick", world_id), class_="btn btn-sm")[
                    icon("book-open", "sm"), span[t("screen.quick")]
                ],
                a(href=screen_url("dm", world_id), class_="btn btn-sm")[
                    icon("shield", "sm"), span[t("screen.dm")]
                ]
                if page.reader is None
                else None,
                view_switch(page),
            ],
            [
                section(class_="panel")[
                    div(class_="panel-head")[h2[t(f"books.group.{key}")]],
                    ul(class_="bk-list")[
                        [
                            li[
                                a(href=book_url(book.id, world_id=world_id))[book.name],
                                " ",
                                small(class_="muted mono")[
                                    " · ".join(x for x in (book.source, book.published[:4]) if x)
                                ],
                            ]
                            for book in grouped[key]
                        ]
                    ],
                ]
                for key, _ in GROUPS
                if grouped[key]
            ]
            if books
            else p(class_="muted")[t("books.none")],
        ],
        world_id=world_id,
    )


def book_page(page: Page, book: PublishedBook, world_id: str | None, maps: Node = None) -> Element:
    """A book's table of contents, with each chapter's headings, and its maps."""
    t = page.t
    return app_shell(
        page,
        book.name,
        div(class_="bk-page")[
            a(href=books_url(world_id), class_="backlink")[
                icon("arrow-left"), span[t("books.title")]
            ],
            page_head(t("books.eyebrow"), book.name, t("books.contents")),
            section(class_="panel")[_contents(book, world_id)],
            maps,
        ],
        world_id=world_id,
    )


def _contents(book: PublishedBook, world_id: str | None) -> Element:
    """The chapters, each with its headings as jump links."""
    return ul(class_="bk-contents")[
        [
            li[
                a(href=book_url(book.id, index, world_id), class_="bk-chapter")[
                    small(class_="muted")[chapter.ordinal] if chapter.ordinal else None,
                    span[chapter.name],
                ],
                ul(class_="bk-headers")[
                    [
                        li[a(href=f"{book_url(book.id, index, world_id)}#{heading_anchor(h)}")[h]]
                        for h in chapter.headers
                    ]
                ]
                if chapter.headers
                else None,
            ]
            for index, chapter in enumerate(book.contents)
        ]
    ]


class _Headings:
    """The chapter's anchors (the store's rule, shared with search) and its contents."""

    def __init__(self) -> None:
        self.anchors = Anchors()
        self.toc: list[tuple[int, str, str]] = []

    def anchor(self, name: str, depth: int, shown: str | None = None) -> str:
        """The anchor of an English heading ``name``; the contents list ``shown``."""
        anchor = self.anchors.take(name)
        if depth <= 2:
            self.toc.append((depth, anchor, shown or name))
        return anchor


def _book_block(
    entry: Any, ctx: RenderContext, depth: int, headings: _Headings, english: Any = None
) -> Node:
    """One entry: a named section becomes a heading (by depth), anything else renders.

    ``entry`` is what is shown, ``english`` the corpus's own: a translation changes the
    text but never the structure, and anchors stay the English ones search links to.
    """
    english = entry if english is None else english
    if is_heading(english, depth):
        name = entry["name"]
        anchor = headings.anchor(english["name"], depth, name)
        heading = HEADINGS[min(depth, len(HEADINGS)) - 1]
        return section(class_=f"bk-sec bk-d{depth}", id=anchor)[
            heading(class_="bk-h")[render(name, ctx)],
            [
                _book_block(child, ctx, depth + 1, headings, original)
                for child, original in zip(entry["entries"], english["entries"], strict=True)
            ],
        ]
    return render_entries([entry], ctx)


def _chapter_text(page: Page, book_id: str, index: int, chapter: Any) -> Overlaid:
    """A chapter's section in the reader's language where translated."""
    return page.t.overlay("book", f"{book_id}|{index}", chapter.entry, CHAPTER_FIELDS)


def chapter_page(
    page: Page,
    body: BookBody,
    index: int,
    ctx: RenderContext,
    world_id: str | None,
    picture: Callable[[str], str | None] | None = None,
) -> Element:
    """One chapter of a book, its headings listed beside it; its pictures and maps from
    the image folder (``picture(path)``, the address of a file it holds), each at its
    anchor, which the book's maps link to."""
    t = page.t
    book = body.book
    chapter = body.sections[index]

    def pictures(entry: dict[str, Any], default: Callable[[], Node]) -> Node | None:
        kind = entry.get("type")
        if kind == "image":
            return book_image(page, entry, picture)
        if kind == "gallery":
            return div(class_="book-gallery")[
                [book_image(page, i, picture) for i in entry.get("images", [])]
            ]
        return None

    ctx = replace(ctx, block_hook=pictures)
    shown = _chapter_text(page, book.id, index, chapter)
    title = str(shown.data.get("name") or chapter.name)
    headings = _Headings()
    text = [
        _book_block(child, ctx, 1, headings, original)
        for child, original in zip(
            shown.data.get("entries") or [], chapter.entry.get("entries") or [], strict=True
        )
    ]
    toc = ul(class_="bk-toc-list")[
        [
            li(class_=f"toc-{depth}")[a(href=f"#{anchor}")[render(name, ctx)]]
            for depth, anchor, name in headings.toc
        ]
    ]
    ordinal = book.contents[index].ordinal if index < len(book.contents) else None
    previous_ = body.section(index - 1)
    following = body.section(index + 1)
    return app_shell(
        page,
        f"{title} · {book.name}",
        div(class_="bk-page bk-reading")[
            a(href=book_url(book.id, world_id=world_id), class_="backlink")[
                icon("arrow-left"), span[book.name]
            ],
            div(class_="bk-layout")[
                div(class_="bk-main panel md-body")[
                    span(class_="eyebrow")[ordinal or book.source],
                    h1[title],
                    coverage_note(page, shown.coverage, untranslated=True),
                    a(
                        href=translate_url("book", book.id, str(index), back=page.path),
                        class_="btn btn-sm bk-translate",
                    )[icon("languages", "sm"), span[t("translate.text")]]
                    if page.identity is not None and page.identity.is_gamemaster
                    else None,
                    # On a phone the contents fold above the text instead of beside it.
                    details(class_="bk-toc-fold")[summary[t("books.in_chapter")], toc]
                    if headings.toc
                    else None,
                    text,
                    nav(class_="bk-pager", aria_label=t("books.pager"))[
                        a(href=book_url(book.id, index - 1, world_id), rel="prev")[
                            icon("arrow-left"), span[previous_.name]
                        ]
                        if previous_
                        else span,
                        a(href=book_url(book.id, index + 1, world_id), rel="next")[
                            span[following.name], icon("arrow-right")
                        ]
                        if following
                        else span,
                    ],
                ],
                aside(class_="bk-aside")[
                    div(class_="panel bk-toc")[span(class_="eyebrow")[t("books.in_chapter")], toc]
                    if headings.toc
                    else None,
                ],
            ],
        ],
        main_class="shell-main shell-wide",
        extra_scripts=("/static/js/roll.js",),
        world_id=world_id,
    )


SCREEN_URL = "/screen"


def screen_url(screen: str, world_id: str | None = None) -> str:
    """A reference screen, in a world."""
    query = {"ref": screen, **({"world": world_id} if world_id else {})}
    return f"{SCREEN_URL}?{urlencode(query)}"


def screen_page(
    page: Page,
    body: BookBody,
    screen: str,
    ctx: RenderContext,
    world_id: str | None,
    *,
    older_rules: bool = False,
) -> Element:
    """A reference screen on one page: every chapter, its headings listed beside it."""
    t = page.t
    headings = _Headings()
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    chapters = []
    for index, chapter in enumerate(body.sections):
        shown = page.t.overlay("screen", f"{screen}|{index}", chapter.entry, CHAPTER_FIELDS)
        title = str(shown.data.get("name") or chapter.name)
        anchor = headings.anchor(chapter.name, 1, title)
        chapters.append(
            section(class_="bk-sec bk-d1", id=anchor)[
                h2(class_="bk-h")[title],
                coverage_note(page, shown.coverage, untranslated=True),
                a(
                    href=translate_url("screen", screen, str(index), back=f"{page.path}#{anchor}"),
                    class_="btn btn-sm bk-translate",
                )[icon("languages", "sm"), span[t("translate.text")]]
                if gamemaster
                else None,
                [
                    _book_block(child, ctx, 2, headings, original)
                    for child, original in zip(
                        shown.data.get("entries") or [],
                        chapter.entry.get("entries") or [],
                        strict=True,
                    )
                ],
            ]
        )
    toc = ul(class_="bk-toc-list")[
        [
            li(class_=f"toc-{depth}")[a(href=f"#{anchor}")[render(name, ctx)]]
            for depth, anchor, name in headings.toc
        ]
    ]
    return app_shell(
        page,
        t(f"screen.{screen}"),
        div(class_="bk-page bk-reading")[
            nav(class_="sc-tabs", aria_label=t("screen.tabs"))[
                [
                    a(
                        href=screen_url(key, world_id),
                        class_="chip",
                        aria_current="page" if key == screen else None,
                    )[t(f"screen.{key}")]
                    for key in ("dm", "quick")
                    if key == "quick" or page.reader is None
                ]
            ],
            p(class_="muted sc-note")[t("screen.older_rules")] if older_rules else None,
            div(class_="bk-layout")[
                div(class_="bk-main panel md-body")[
                    span(class_="eyebrow")[t("screen.eyebrow")],
                    h1[t(f"screen.{screen}")],
                    details(class_="bk-toc-fold")[summary[t("screen.sections")], toc],
                    chapters,
                ],
                aside(class_="bk-aside")[
                    div(class_="panel bk-toc")[span(class_="eyebrow")[t("screen.sections")], toc]
                ],
            ],
        ],
        main_class="shell-main shell-wide",
        extra_scripts=("/static/js/roll.js",),
        world_id=world_id,
    )


__all__ = [
    "BOOKS_URL",
    "SCREEN_URL",
    "screen_page",
    "screen_url",
    "book_page",
    "book_url",
    "books_page",
    "books_url",
    "chapter_page",
]
