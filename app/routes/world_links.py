"""Every link of the table's world that leads nowhere, on one page (ADVENTURES.md §5.7).

* ``GET  /gm/world/links``     — the chapter check over every text of the world: its
  description, adventures, chapters and the notes of its NPCs, places, encounters and
  treasure, each problem with what would mend it
* ``POST /gm/world/links/fix`` — one suggested replacement written into its text, on its line

A missing NPC, place, treasure or encounter is offered to create through the same page as a
dashed link in a chapter (``/gm/editor/new``), coming back here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response
from htpy import Element, a, button, code, div, form, li, section, span, ul

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Scope
from app.routes.adventures import chapter_problems, notes_problems
from app.routes.auth import html, redirect
from app.services import scope as scopes
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.forms import hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import (
    adventure_url,
    chapter_url,
    location_url,
    new_object_url,
    npc_url,
    treasure_url,
    world_url,
)
from app.ui.prose_check import Problem
from app.ui.world_menu import LINKS_URL

router = APIRouter(tags=["world links"])

FIX_URL = f"{LINKS_URL}/fix"

# Where each kind of text is kept: its collection on the game-state store and its field.
FIELDS: dict[str, tuple[str, str]] = {
    "world": ("worlds", "description"),
    "adventure": ("adventures", "body"),
    "chapter": ("chapters", "body"),
    "npc": ("npcs", "notes"),
    "place": ("locations", "notes"),
    "treasure": ("treasures", "notes"),
    "encounter": ("encounters", "notes"),
}


@dataclass(frozen=True)
class Checked:
    """One text of the world with what in it leads nowhere."""

    kind: str  # a key of FIELDS
    id: str
    title: str
    url: str
    scope: Scope
    problems: Sequence[Problem]


def world_problems(page: Page, registry: StoreRegistry, world_id: str) -> list[Checked]:
    """Every text of the world that has a problem, in the order of the world's lists."""
    found: list[Checked] = []

    def add(
        kind: str, ident: str, title: str, url: str, here: Scope, faults: list[Problem]
    ) -> None:
        if faults:
            found.append(Checked(kind, ident, title, url, here, faults))

    world = registry.adventures.world(world_id)
    here = Scope(world_id=world_id)
    add(
        "world",
        world.id,
        world.name,
        world_url(world.id),
        here,
        _notes(page, registry, here, world.description),
    )
    for adventure in registry.adventures.adventures(world_id):
        inside = Scope(world_id=world_id, adventure_id=adventure.id)
        add(
            "adventure",
            adventure.id,
            adventure.name,
            adventure_url(adventure.id),
            inside,
            _notes(page, registry, inside, adventure.body),
        )
        for chapter in registry.adventures.chapters(adventure.id):
            at = Scope(world_id=world_id, adventure_id=adventure.id, chapter_id=chapter.id)
            faults = (
                chapter_problems(page, registry, chapter.id, chapter.body)
                if chapter.body.strip()
                else []
            )
            add(
                "chapter",
                chapter.id,
                f"{adventure.name} · {chapter.title}",
                chapter_url(chapter.id),
                at,
                faults,
            )
    for npc in registry.npcs.list(world_id):
        add(
            "npc",
            npc.id,
            npc.name,
            npc_url(npc.id),
            npc.scope,
            _notes(page, registry, npc.scope, npc.notes),
        )
    for location in registry.locations.list(world_id):
        add(
            "place",
            location.id,
            location.name,
            location_url(location.id),
            location.scope,
            _notes(page, registry, location.scope, location.notes),
        )
    for treasure in registry.treasure.list(world_id):
        add(
            "treasure",
            treasure.id,
            treasure.name,
            treasure_url(treasure.id),
            treasure.scope,
            _notes(page, registry, treasure.scope, treasure.notes),
        )
    for encounter in registry.encounters.list(world_id):
        at = encounter.scope or here
        add(
            "encounter",
            encounter.id,
            encounter.name,
            encounter_url(encounter.id),
            at,
            _notes(page, registry, at, encounter.notes),
        )
    return found


def _notes(page: Page, registry: StoreRegistry, here: Scope, text: str) -> list[Problem]:
    return notes_problems(page, registry, here, text) if text.strip() else []


def links_page(
    page: Page, world_name: str, checked: Sequence[Checked], message: str | None = None
) -> Element:
    """The world's texts with their problems, each mendable from here."""
    t = page.t
    count = sum(len(c.problems) for c in checked)
    return app_shell(
        page,
        t("links.title"),
        div(class_="links-page")[
            page_head(
                world_name,
                t("links.title"),
                t.plural("links.lede", count) if count else t("links.none"),
            ),
            notice(message),
            [_text_section(page, c) for c in checked],
        ],
    )


def _text_section(page: Page, checked: Checked) -> Element:
    t = page.t
    return section(class_="links-text panel", id=f"t-{checked.id}")[
        div(class_="links-text-head")[
            span(class_="eyebrow")[t(f"links.kind.{checked.kind}")],
            a(href=checked.url, class_="links-text-title")[checked.title],
        ],
        ul(class_="check-list")[
            [
                li(class_=f"check-item check-{x.kind}")[
                    span(class_="check-line mono")[t("check.line", n=x.line)],
                    span(class_="check-what")[
                        t(f"check.kind.{x.kind}", name=x.name or x.text),
                        " ",
                        code(class_="check-text")[x.text],
                    ],
                    span(class_="check-fixes")[
                        [
                            form(method="post", action=FIX_URL, class_="inline-form")[
                                hidden("kind", checked.kind),
                                hidden("id", checked.id),
                                hidden("line", str(x.line)),
                                hidden("old", x.text),
                                hidden("new", instead),
                                button(type="submit", class_="btn btn-sm check-fix")[instead],
                            ]
                            for instead in x.instead
                        ],
                        a(
                            href=new_object_url(
                                x.create[0], x.create[1], scopes.encode(checked.scope), LINKS_URL
                            ),
                            class_="btn btn-sm check-create",
                        )[
                            icon("plus", "sm"),
                            span[t(f"search.create.{x.create[0]}", name=x.create[1])],
                        ]
                        if x.create
                        else None,
                    ],
                ]
                for x in checked.problems
            ]
        ],
    ]


@router.get(LINKS_URL, response_class=HTMLResponse)
async def world_links(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, fixed: str = ""
) -> HTMLResponse:
    """The chapter check over the whole table's world."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    message = {"1": page.t("links.fixed"), "stale": page.t("links.stale")}.get(fixed)
    return html(links_page(page, world.name, world_problems(page, registry, world.id), message))


@router.post(FIX_URL)
async def fix_link(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    id: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
    line: Annotated[int, Form()] = 0,
    old: Annotated[str, Form()] = "",
    new: Annotated[str, Form()] = "",
) -> Response:
    """Write one replacement into its text, on the line the check found it; a text changed
    meanwhile is left alone and the page says so."""
    if kind not in FIELDS or not old or not new:
        raise HTTPException(status_code=400)
    collection_name, field = FIELDS[kind]
    collection = getattr(registry.gamestate, collection_name)
    record = collection.find(id)
    world = registry.table.world_id()
    if record is None or _world_of(kind, record) != world:
        raise HTTPException(status_code=404)
    lines = str(getattr(record, field)).split("\n")
    if not 0 < line <= len(lines) or old not in lines[line - 1]:
        return redirect(f"{LINKS_URL}?{urlencode({'fixed': 'stale'})}")
    lines[line - 1] = lines[line - 1].replace(old, new, 1)
    collection.update(id, **{field: "\n".join(lines)})
    return redirect(f"{LINKS_URL}?fixed=1#t-{id}")


def _world_of(kind: str, record: object) -> str | None:
    if kind == "world":
        return str(getattr(record, "id", ""))
    scope = getattr(record, "scope", None)
    if isinstance(scope, Scope):
        return scope.world_id
    world_id = getattr(record, "world_id", None)
    return str(world_id) if world_id else None


__all__ = ["router", "world_problems"]
