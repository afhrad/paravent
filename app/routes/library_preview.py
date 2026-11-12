"""``GET /library/preview?href=…``: the preview of whatever a campaign link points at.

A link says where it leads and no more, so the preview is found from that address: every
``{@npc}``-style link, wikilink and listed object has one without any markup of its own.
Only the addresses below answer. Campaign objects are the gamemaster's; a character
follows its own access rule.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from htpy import Element

from app.dependencies import IdentityDep, PageDep, RegistryDep
from app.routes.auth import html
from app.services.auth import Identity
from app.services.characters import CharacterNotFound
from app.store.registry import StoreRegistry
from app.ui.cast_pages import coins_text, role_pills
from app.ui.context import Page
from app.ui.encounter_pages import ENCOUNTERS_URL, level_pill
from app.ui.library_links import CHAPTERS_URL, LOCATIONS_URL, NPCS_URL, TREASURE_URL
from app.ui.library_preview import library_card
from app.ui.names import monster_label, name_text, summary

router = APIRouter(tags=["library"])

_ADDRESS = re.compile(
    "^(?:"
    f"(?P<npc>{NPCS_URL})|(?P<place>{LOCATIONS_URL})|(?P<encounter>{ENCOUNTERS_URL})"
    f"|(?P<treasure>{TREASURE_URL})|(?P<chapter>{CHAPTERS_URL})|(?P<character>/characters)"
    ")/(?P<id>[A-Za-z0-9_-]+)$"
)


def _npc(page: Page, registry: StoreRegistry, npc_id: str, url: str) -> Element | None:
    npc = registry.gamestate.npcs.find(npc_id)
    if npc is None:
        return None
    statblock = npc.statblock.split("|")[0] if npc.statblock else ""
    return library_card(
        page,
        kind="npc",
        glyph="users-round",
        token=page.token_of("npc", npc.scope.world_id, npc.id),
        name=npc.name,
        url=url,
        pills=role_pills(page, npc.roles),
        meta=[name_text(page, "monster", statblock) if statblock else ""],
        text=npc.notes,
    )


def _place(page: Page, registry: StoreRegistry, place_id: str, url: str) -> Element | None:
    place = registry.gamestate.locations.find(place_id)
    if place is None:
        return None
    return library_card(
        page,
        kind="place",
        glyph="map",
        token=page.token_of("place", place.scope.world_id, place.id),
        name=place.name,
        url=url,
        pills=role_pills(page, place.tags),
        text=place.notes,
    )


def _encounter(page: Page, registry: StoreRegistry, encounter_id: str, url: str) -> Element | None:
    encounter = registry.gamestate.encounters.find(encounter_id)
    if encounter is None:
        return None
    view = registry.encounters.view(encounter)
    return library_card(
        page,
        kind="encounter",
        glyph="swords",
        token=page.token_of("encounter", encounter.world_id, encounter.id),
        name=encounter.name,
        url=url,
        pills=level_pill(page, view.rating),
        meta=[
            ", ".join(
                f"{line.planned.count} × "
                f"{monster_label(page, line.planned.label or '', line.planned.name)}"
                for line in view.lines
            )
        ],
        text=encounter.notes,
    )


def _treasure(page: Page, registry: StoreRegistry, treasure_id: str, url: str) -> Element | None:
    treasure = registry.gamestate.treasures.find(treasure_id)
    if treasure is None:
        return None
    items = ", ".join(
        f"{item.count} × {name_text(page, ('item', 'baseitem'), item.name)}"
        if item.count > 1
        else name_text(page, ("item", "baseitem"), item.name)
        for item in treasure.items
    )
    return library_card(
        page,
        kind="treasure",
        glyph="gem",
        token=page.token_of("treasure", treasure.scope.world_id, treasure.id),
        name=treasure.name,
        url=url,
        meta=[coins_text(page, treasure.coins), items],
        text=treasure.notes,
    )


def _chapter(page: Page, registry: StoreRegistry, chapter_id: str, url: str) -> Element | None:
    chapter = registry.gamestate.chapters.find(chapter_id)
    if chapter is None:
        return None
    adventure = registry.gamestate.adventures.find(chapter.adventure_id)
    return library_card(
        page,
        kind="chapter",
        glyph="book-open",
        name=chapter.title,
        url=url,
        meta=[adventure.name if adventure else ""],
        text=chapter.body,
    )


_CARDS = {
    "npc": _npc,
    "place": _place,
    "encounter": _encounter,
    "treasure": _treasure,
    "chapter": _chapter,
}


def _character(
    page: Page, identity: Identity, registry: StoreRegistry, pc_id: str, url: str
) -> Element:
    pc = registry.characters.get_for(identity, pc_id)
    sheet = registry.characters.sheet(pc)
    t = page.t
    return library_card(
        page,
        kind="character",
        glyph="shield",
        token=page.token_of("character", pc.world_id, pc.id),
        name=pc.name,
        url=url,
        meta=[
            summary(page, pc),
            t(
                "tip.character_numbers",
                hp=pc.state.hp,
                hp_max=sheet.hp_max.value,
                ac=sheet.armor_class.value,
            ),
        ],
        text=pc.appearance or pc.backstory,
    )


@router.get("/library/preview", response_class=HTMLResponse)
async def library_preview(
    identity: IdentityDep, page: PageDep, registry: RegistryDep, href: str = ""
) -> HTMLResponse:
    """The tooltip or bottom-sheet fragment for one campaign link; its "Open" goes where
    the link does, heading included."""
    path = href.partition("#")[0].partition("?")[0]
    found = _ADDRESS.match(path)
    if found is None:
        raise HTTPException(status_code=404)
    record_id = found["id"]
    if found["character"]:
        try:
            return html(_character(page, identity, registry, record_id, href))
        except CharacterNotFound as exc:
            raise HTTPException(status_code=404) from exc
    if not identity.is_gamemaster:
        raise HTTPException(status_code=403)
    build = next(card for kind, card in _CARDS.items() if found[kind])
    made = build(page, registry, record_id, href)
    if made is None:
        raise HTTPException(status_code=404)
    return html(made)


__all__ = ["router"]
