"""The reference page: ``GET /ref/{category}/{source}/{name}`` — what every link opens.

Any logged-in user may read reference entries. ``?world=`` keeps the links inside the
entry resolving against the same books as the page the reader came from.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.backlinks import Owner
from app.services.references import ReferenceEntry, ReferenceNotFound
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.ref_pages import preview_card, reference_page
from app.ui.tags import RenderContext, card_url, dice_letter, localized_context, ref_url

router = APIRouter(tags=["reference"])


def _readable(page: Page, entry: ReferenceEntry) -> ReferenceEntry:
    """The entry, or the "not found" a missing one gets when the reader may not open it
    (documentation/PLAYER-VIEW.md)."""
    if not page.can_read(entry.category, entry.data):
        raise ReferenceNotFound(f"{entry.category}/{entry.source}/{entry.name}")
    return entry


def _owner_readable(page: Page, registry: StoreRegistry, owner: Owner) -> bool:
    """Whether a player may open what links here: a rulebook chapter by its book, an
    entry as itself."""
    if owner.url:
        return page.reads_book(owner.source)
    found = registry.compendium.find(owner.category, owner.name, owner.source)
    return found is not None and page.can_read(owner.category, found)


@router.get("/ref/{category}/{source}/{name}", response_class=HTMLResponse)
async def reference(
    _identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    category: str,
    source: str,
    name: str,
    world: str = "",
) -> HTMLResponse:
    """One entry, rendered in full, with its links in the same world."""
    references = registry.references
    entry = _readable(
        page, references.get(category, source, name, world or page.home_world or None)
    )
    world_id = references.known_world(world or page.home_world or None)
    source_set = references.source_set(world or page.home_world or None)
    ctx = localized_context(
        page.t,
        resolver=references.resolver(),
        source_set=source_set,
        world_id=world_id,
        readable=page.readable,
        units=page.u,
    )
    backlinks = registry.backlinks.links_to(
        entry.category, entry.name, entry.source, source_set=source_set, world_id=world_id
    )
    # The campaign's chapters, NPCs and treasure are the gamemaster's, not the players'.
    if page.identity is None or not page.identity.is_gamemaster or page.as_player:
        backlinks = replace(backlinks, library=())
    if page.reader is not None:
        backlinks = replace(
            backlinks,
            groups=tuple(
                (kind, owners)
                for kind, all_owners in backlinks.groups
                if (owners := tuple(o for o in all_owners if _owner_readable(page, registry, o)))
            ),
        )
    printings = [
        p
        for p in references.other_printings(entry)
        if page.can_read(entry.category, {**entry.data, "name": p.name, "source": p.source})
    ]
    return html(reference_page(page, entry, ctx, backlinks, printings, registry.description(entry)))


@router.get("/ref/{category}/{source}/{name}/preview", response_class=HTMLResponse)
async def reference_preview(
    _identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    category: str,
    source: str,
    name: str,
    world: str = "",
    card: str = "",
) -> HTMLResponse:
    """The tooltip or bottom-sheet fragment for one reference (DESIGN.md §7).

    Rendered inert — no resolver, chips that do not roll — so references inside a
    preview do not chain; its "Open" link is the way to the full entry. A ``{@card}``
    link opens its deck with ``card=``, so the preview shows that one card.
    """
    references = registry.references
    entry = _readable(
        page, references.get(category, source, name, world or page.home_world or None)
    )
    inert = RenderContext(static_rolls=True, dice_letter=dice_letter(page.t), units=page.u)
    open_url = ref_url(
        category, entry.name, entry.source, references.known_world(world or page.home_world or None)
    )
    shown = references.card(entry, card) if card and category == "deck" else None
    if shown is not None:
        open_url = card_url(open_url, shown.name)
    printings = (
        [
            p
            for p in references.other_printings(entry)
            if page.can_read(entry.category, {**entry.data, "name": p.name, "source": p.source})
        ]
        if shown is None
        else []
    )
    return html(preview_card(page, shown or entry, inert, open_url, printings))


__all__ = ["router"]
