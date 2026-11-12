"""The players' journal (documentation/JOURNAL.md).

* ``GET  /gm/share``               — what a share button hands over, to whom
* ``POST /gm/share``               — hand it over
* ``GET  /journal``                — the reader's handouts; the gamemaster's, every one
* ``GET  /journal/{id}``           — one handout, read (a player's now opened)
* ``GET  /journal/{id}/img/{name}``— one of its pictures, ``?fx=`` with its effects
* ``POST /journal/{id}/delete``    — withdraw it, the gamemaster's
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from htpy import Node, div, img
from markupsafe import Markup

from app.dependencies import GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.models.gamestate import Handout
from app.routes.adventures import _chapter_context as chapter_context
from app.routes.auth import html, redirect, safe_next
from app.services import image_effects, media
from app.services.creature_sight import image_file
from app.services.handouts import HandoutError
from app.services.translations import CHAPTER_FIELDS
from app.store.registry import StoreRegistry
from app.ui import tags
from app.ui.book_pages import find_book_entry
from app.ui.context import Page
from app.ui.journal_pages import (
    JOURNAL_URL,
    SHARE_URL,
    handout_page,
    handout_url,
    journal_page,
    picture_url,
    share_page,
)
from app.ui.library_links import chapter_url, media_url
from app.ui.markdown import MarkdownContext, render_markdown

router = APIRouter(tags=["journal"])


def _reading(page: Page, registry: StoreRegistry, handout: Handout) -> MarkdownContext:
    """How a handout reads: in its world's books, links to what the reader may open,
    its own copied pictures, and the campaign's links as plain words."""
    world = registry.gamestate.worlds.find(handout.world_id)

    def picture(path: str) -> str | None:
        name = path.rsplit("/", 1)[-1]
        if registry.handouts.picture_file(handout, name) is None:
            return None
        return picture_url(handout.id, name)

    return MarkdownContext(
        tags=tags.localized_context(
            page.t,
            resolver=registry.references.resolver(),
            source_set=world.source_set if world is not None else None,
            world_id=handout.world_id,
            readable=page.readable,
        ),
        media=picture,
        find_media=picture,
        t=page.t,
        plain=True,
    )


def _book_entry(page: Page, registry: StoreRegistry, chapter: str, key: str) -> dict[str, Any]:
    """A box of the chapter's book text as the reader's language has it; 404 without."""
    view = registry.adventures.chapter_view(chapter)
    book = registry.adventures.book_section(view)
    if book is None:
        raise HTTPException(status_code=404)
    body, part = book
    data = page.t.overlay(
        "adventure", f"{body.adventure.id}|{part.id}", part.entry, CHAPTER_FIELDS
    ).data
    found = find_book_entry(data.get("entries") or [], key)
    if found is None:
        raise HTTPException(status_code=404)
    return found


def _book_picture(registry: StoreRegistry, path: str) -> Path:
    """A picture of the image folder; 404 without."""
    root = registry.settings.images_root
    found = image_file(root, path) if root is not None and path else None
    if found is None:
        raise HTTPException(status_code=404)
    return found


@router.get(SHARE_URL, response_class=HTMLResponse)
async def share_form(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter: str,
    box: str = "",
    image: str = "",
    book: str = "",
    bookimage: str = "",
    back: str = "",
    error: str = "",
) -> HTMLResponse:
    """What will be handed over, as the players will read it, and to which characters."""
    view = registry.adventures.chapter_view(chapter)
    handouts = registry.handouts
    ctx = chapter_context(page, registry, chapter)
    fields = {"chapter": chapter, "back": safe_next(back) or chapter_url(chapter)}
    preview: Node
    if book:
        entry = _book_entry(page, registry, chapter, book)
        preview = div(class_="book-text")[tags.render_entries([entry], ctx.tags)]
        fields["book"] = book
        name = entry.get("name")
        title = name if isinstance(name, str) else ""
    elif bookimage:
        _book_picture(registry, bookimage)
        preview = img(class_="journal-picture", alt="", src=f"/play/img/{quote(bookimage)}")
        fields["bookimage"] = bookimage
        title = ""
    elif box:
        found = handouts.box(chapter, box)
        if found is None:
            raise HTTPException(status_code=404)
        preview = render_markdown(found.text, ctx).html
        fields["box"] = box
        title = found.title
    else:
        path = handouts.picture(chapter, image) if image else None
        if path is None:
            raise HTTPException(status_code=404)
        name, effects = image_effects.split(image)
        found_media = registry.adventures.find_media(chapter, view.world.id, name)
        url = media_url(*found_media) if found_media else media_url(chapter, name)
        preview = Markup(
            f'<img class="journal-picture{" fx" if effects else ""}" alt="" '
            f'src="{image_effects.with_effects(url, effects)}">'
        )
        fields["image"] = image
        title = ""
    return html(
        share_page(
            page,
            chapter_title=view.chapter.title,
            chapter_href=fields["back"],
            preview=preview,
            fields=fields,
            title=title,
            recipients=handouts.recipients(view.world.id),
            world_id=view.world.id,
            error=page.t(error) if error.startswith("journal.error.") else None,
        )
    )


@router.post(SHARE_URL)
async def share(
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    chapter: Annotated[str, Form()],
    box: Annotated[str, Form()] = "",
    image: Annotated[str, Form()] = "",
    book: Annotated[str, Form()] = "",
    bookimage: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    characters: Annotated[list[str] | None, Form()] = None,
    back: Annotated[str, Form()] = "",
) -> Response:
    """Hand the box or picture to the characters ticked."""
    handouts = registry.handouts
    try:
        chosen = characters or []
        if book:
            entry = _book_entry(page, registry, chapter, book)
            made = handouts.share_entry(chapter, entry, chosen, title, gm)
        elif bookimage:
            source = _book_picture(registry, bookimage)
            made = handouts.share_file(chapter, source, chosen, title, gm)
        elif box:
            made = handouts.share_box(chapter, box, chosen, title, gm)
        else:
            made = handouts.share_image(chapter, image, chosen, title, gm)
    except HandoutError as exc:
        query = {"chapter": chapter, "back": back, "error": exc.key}
        for field, value in (("book", book), ("bookimage", bookimage), ("box", box)):
            if value:
                query[field] = value
                break
        else:
            query["image"] = image
        return redirect(f"{SHARE_URL}?{urlencode(query)}")
    return redirect(f"{handout_url(made.id)}?shared=1")


@router.get(JOURNAL_URL, response_class=HTMLResponse)
async def journal(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, world: str = "", gone: str = ""
) -> HTMLResponse:
    """The reader's handouts, newest first."""
    handouts = registry.handouts
    found = handouts.readable(identity, world or None)
    names = {h.id: handouts.names(h) for h in found} if identity.is_gamemaster else None
    return html(
        journal_page(
            page, found, names=names, message=page.t("journal.withdrawn") if gone else None
        )
    )


@router.get(JOURNAL_URL + "/{handout_id}", response_class=HTMLResponse)
async def read(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    handout_id: str,
    shared: str = "",
) -> HTMLResponse:
    """One handout; opening it marks it read for its player."""
    handouts = registry.handouts
    try:
        handout = handouts.get(identity, handout_id)
    except HandoutError as exc:
        raise HTTPException(status_code=404) from exc
    handouts.opened(identity, handout)
    reading = _reading(page, registry, handout)
    if handout.entry is not None:
        content = Markup(
            div(class_="book-text")[tags.render_entries([handout.entry], reading.tags)]
        )
    elif handout.kind == "box":
        content = render_markdown(handout.text, reading).html
    else:
        content = Markup("")
    users = {u.username: u.display_name or u.username for u in registry.gamestate.users.list()}
    return html(
        handout_page(
            page,
            handout,
            content,
            names=handouts.names(handout),
            readers=[users.get(name, name) for name in handout.read_by],
            chapter_href=chapter_url(handout.chapter_id)
            if identity.is_gamemaster and handout.chapter_id
            else None,
            message=page.t("journal.shared", names=", ".join(handouts.names(handout)))
            if shared and identity.is_gamemaster
            else None,
        )
    )


@router.get(JOURNAL_URL + "/{handout_id}/img/{name}")
async def picture(
    identity: IdentityDep, registry: RegistryDep, handout_id: str, name: str, fx: str = ""
) -> FileResponse:
    """A handout's picture, for those who may read the handout."""
    handouts = registry.handouts
    try:
        handout = handouts.get(identity, handout_id)
    except HandoutError as exc:
        raise HTTPException(status_code=404) from exc
    path = handouts.picture_file(handout, name)
    if path is None:
        raise HTTPException(status_code=404)
    effects = image_effects.parse(fx) if fx else None
    if effects:
        try:
            path = await asyncio.to_thread(registry.image_effects.render, path, effects)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404) from exc
    return FileResponse(
        path,
        media_type="image/webp" if effects else media.media_type(path),
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.post(JOURNAL_URL + "/{handout_id}/delete")
async def withdraw(_gm: GamemasterDep, registry: RegistryDep, handout_id: str) -> Response:
    """Withdraw a handout from every journal."""
    registry.handouts.delete(handout_id)
    return redirect(f"{JOURNAL_URL}?gone=1")


__all__ = ["router"]
