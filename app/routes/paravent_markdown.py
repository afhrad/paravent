"""Paravent Markdown for any text the gamemaster writes (documentation/ADVENTURES.md §5).

Paravent Markdown is CommonMark with what a campaign needs — 5eTools tags, the campaign's
own ``{@npc}``/``{@encounter}``/``{@treasure}``/``{@character}``, objects shown where a
tag stands alone, callouts, heading links, images (``app/ui/markdown.py``). Chapters
and adventures are written in it, and so are the notes of NPCs, treasure, encounters
and sessions. This renders such a text as if written where its object is placed, so
names resolve nearest first from there and references in the right books.
"""

from __future__ import annotations

from app.models.gamestate import Scope
from app.store.registry import StoreRegistry
from app.ui.adventure_pages import ProseServices, prose_context, render_body
from app.ui.context import Page
from app.ui.markdown import MarkdownContext, Rendered


def services_of(registry: StoreRegistry) -> ProseServices:
    """What rendering Paravent Markdown asks: the services behind its names."""
    return ProseServices(
        registry.adventures,
        registry.encounters,
        registry.references,
        registry.npcs,
        registry.treasure,
        registry.combats,
        registry.characters,
        registry.locations,
        registry.sounds,
    )


def scoped_context(page: Page, registry: StoreRegistry, here: Scope) -> MarkdownContext:
    """How a text placed at ``here`` — a world, an adventure or a chapter — resolves."""
    adventures = registry.adventures
    world = adventures.world(here.world_id)
    view = adventures.view(here.adventure_id) if here.adventure_id else None
    return prose_context(
        page,
        services_of(registry),
        record_id=here.record_id,
        world=world,
        adventure=view.adventure if view else None,
        source_set=view.source_set if view else world.source_set,
        chapter_id=here.chapter_id,
    )


def paravent_markdown(page: Page, registry: StoreRegistry, here: Scope, text: str) -> Rendered:
    """``text`` rendered as Paravent Markdown, written where ``here`` places it."""
    return render_body(text, scoped_context(page, registry, here))


__all__ = ["paravent_markdown", "scoped_context", "services_of"]
