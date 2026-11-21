"""Paravent Markdown for any text the gamemaster writes (documentation/ADVENTURES.md §5).

Paravent Markdown is CommonMark with what a campaign needs — 5eTools tags, the campaign's
own ``{@npc}``/``{@encounter}``/``{@treasure}``/``{@character}``, objects shown where a
tag stands alone, callouts, heading links, images (``app/ui/markdown.py``). Chapters
and adventures are written in it, and so are the notes of NPCs, treasure, encounters
and sessions. This renders such a text as if written where its object is placed, so
names resolve nearest first from there and references in the right books.
"""

from __future__ import annotations

from markupsafe import Markup

from app.models.gamestate import Scope
from app.store.registry import StoreRegistry
from app.ui import tags
from app.ui.adventure_pages import ProseServices, prose_context, render_body
from app.ui.context import Page
from app.ui.markdown import MarkdownContext, Rendered, render_markdown, without_secrets


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


def players_markdown(page: Page, registry: StoreRegistry, world_id: str, text: str) -> Markup:
    """``text`` as a player reads it: its ``[!secret]`` boxes left out, references in the
    world's books (and only those the reader may open), the campaign's own links as their
    words alone — as a handout reads in the journal."""
    world = registry.gamestate.worlds.find(world_id)
    ctx = MarkdownContext(
        tags=tags.localized_context(
            page.t,
            resolver=registry.references.resolver(),
            source_set=world.source_set if world is not None else None,
            world_id=world_id,
            readable=page.readable,
            units=page.u,
        ),
        t=page.t,
        plain=True,
    )
    return render_markdown(without_secrets(text), ctx).html


__all__ = ["paravent_markdown", "players_markdown", "scoped_context", "services_of"]
