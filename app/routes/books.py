"""The rulebooks (documentation/TECHSTACK.md §6b, phase 3).

* ``GET /books``                 — the world's books (``?world=``), or every book
* ``GET /books/{id}``            — a book's contents
* ``GET /books/{id}/{chapter}``  — one chapter, by its position in the book

Read by anyone logged in: players look rules up too, in their player books only
(documentation/PLAYER-VIEW.md). A world narrows the list to its books, and resolves the
links inside a chapter in them. The DM screen is the gamemaster's.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.references import ReferenceNotFound
from app.ui.rulebook_pages import (
    BOOKS_URL,
    SCREEN_URL,
    book_page,
    books_page,
    chapter_page,
    screen_page,
)
from app.ui.tags import localized_context

router = APIRouter(tags=["books"])


class BookNotFound(ReferenceNotFound):
    """No such book, or no such chapter in it."""


@router.get(BOOKS_URL, response_class=HTMLResponse)
async def books(
    _identity: IdentityDep, page: PageDep, registry: RegistryDep, world: str = ""
) -> HTMLResponse:
    """The rulebooks of the world, or all of them."""
    world_id = registry.references.known_world(world or page.home_world or None)
    source_set = registry.references.source_set(world_id)
    shown = [
        book
        for book in registry.rulebooks.all()
        if (source_set is None or source_set.is_unrestricted or source_set.allows(book.source))
        and page.reads_book(book.source)
    ]
    return html(books_page(page, shown, world_id))


@router.get(f"{BOOKS_URL}/{{book_id}}", response_class=HTMLResponse)
async def book(
    _identity: IdentityDep, page: PageDep, registry: RegistryDep, book_id: str, world: str = ""
) -> HTMLResponse:
    """A book's table of contents."""
    found = registry.rulebooks.get(book_id)
    if found is None or not page.reads_book(found.source):
        raise BookNotFound(book_id)
    return html(
        book_page(page, found, registry.references.known_world(world or page.home_world or None))
    )


@router.get(f"{BOOKS_URL}/{{book_id}}/{{index}}", response_class=HTMLResponse)
async def chapter(
    _identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    book_id: str,
    index: int,
    world: str = "",
) -> HTMLResponse:
    """One chapter, with links resolving in the world's books."""
    body = registry.rulebooks.body(book_id)
    found = registry.rulebooks.get(book_id)
    if body is None or body.section(index) is None or found is None:
        raise BookNotFound(f"{book_id}/{index}")
    if not page.reads_book(found.source):
        raise BookNotFound(f"{book_id}/{index}")
    world_id = registry.references.known_world(world or page.home_world or None)
    ctx = localized_context(
        page.t,
        resolver=registry.references.resolver(),
        source_set=registry.references.source_set(world_id),
        world_id=world_id,
        readable=page.readable,
    )
    return html(chapter_page(page, body, index, ctx, world_id))


@router.get(SCREEN_URL, response_class=HTMLResponse)
async def screen(
    _identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    ref: str = "dm",
    world: str = "",
) -> HTMLResponse:
    """The DM screen or the quick reference, on one page."""
    body = registry.screens.body(ref)
    if body is None or (ref == "dm" and page.reader is not None):
        raise BookNotFound(f"screen/{ref}")
    world_id = registry.references.known_world(world or page.home_world or None)
    source_set = registry.references.source_set(world_id)
    ctx = localized_context(
        page.t,
        resolver=registry.references.resolver(),
        source_set=source_set,
        world_id=world_id,
        readable=page.readable,
    )
    # The screens are made from the 2014 books; say so to a table on the 2024 rules only.
    older = source_set is not None and source_set.editions == {"one"}
    return html(screen_page(page, body, ref, ctx, world_id, older_rules=older))


__all__ = ["BookNotFound", "router"]
