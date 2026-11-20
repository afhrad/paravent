"""Every link of the table's world that leads nowhere, on one page (ADVENTURES.md §5.7).

* ``GET  /gm/world/links``     — the chapter check over every text of the world: its
  description, adventures, chapters, session notes and the notes of its NPCs, places,
  encounters and treasure, each problem with what would mend it (a quest's text, a
  faction's note and Claude's instructions are plain text, where a tag is never a link)
* ``POST /gm/world/links/fix`` — one suggested replacement written into its text, on its line
* ``POST /gm/world/links/rename`` — or the object renamed to the name the text uses, every
  link to its old name following

A missing NPC, place, treasure or encounter is offered to create through the same page as a
dashed link in a chapter (``/gm/editor/new``), coming back here.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from htpy import Element, a, b, button, code, div, form, li, p, section, span, ul

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Scope
from app.routes.adventures import chapter_problems, near_objects, notes_problems
from app.routes.auth import html, redirect
from app.services import scope as scopes
from app.store.registry import StoreRegistry
from app.ui import tags
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
from app.ui.session_pages import SESSIONS_URL, day_text
from app.ui.world_menu import LINKS_URL

router = APIRouter(tags=["world links"])

FIX_URL = f"{LINKS_URL}/fix"
RENAME_URL = f"{LINKS_URL}/rename"

# Where each kind of text is kept: its collection on the game-state store and its field.
FIELDS: dict[str, tuple[str, str]] = {
    "world": ("worlds", "description"),
    "adventure": ("adventures", "body"),
    "chapter": ("chapters", "body"),
    "npc": ("npcs", "notes"),
    "place": ("locations", "notes"),
    "treasure": ("treasures", "notes"),
    "encounter": ("encounters", "notes"),
    "session": ("session_notes", "text"),
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
    # Per problem (same order): ``(id, name)`` of objects it probably meant, which may be
    # renamed to the name the text uses when the text is right.
    meant: Sequence[Sequence[tuple[str, str]]] = ()


@dataclass(frozen=True)
class Text:
    """One text of the world: what holds it, where its links look, and its Markdown."""

    kind: str  # a key of FIELDS
    id: str
    title: str
    url: str
    scope: Scope
    body: str


def world_texts(page: Page, registry: StoreRegistry, world_id: str) -> Iterator[Text]:
    """Every text of the world, in the order of the world's lists."""
    world = registry.adventures.world(world_id)
    here = Scope(world_id=world_id)
    yield Text("world", world.id, world.name, world_url(world.id), here, world.description)
    for adventure in registry.adventures.adventures(world_id):
        inside = Scope(world_id=world_id, adventure_id=adventure.id)
        yield Text(
            "adventure",
            adventure.id,
            adventure.name,
            adventure_url(adventure.id),
            inside,
            adventure.body,
        )
        for chapter in registry.adventures.chapters(adventure.id):
            at = Scope(world_id=world_id, adventure_id=adventure.id, chapter_id=chapter.id)
            title = f"{adventure.name} · {chapter.title}"
            yield Text("chapter", chapter.id, title, chapter_url(chapter.id), at, chapter.body)
    for npc in registry.npcs.list(world_id):
        yield Text("npc", npc.id, npc.name, npc_url(npc.id), npc.scope, npc.notes)
    for location in registry.locations.list(world_id):
        yield Text(
            "place",
            location.id,
            location.name,
            location_url(location.id),
            location.scope,
            location.notes,
        )
    for treasure in registry.treasure.list(world_id):
        yield Text(
            "treasure",
            treasure.id,
            treasure.name,
            treasure_url(treasure.id),
            treasure.scope,
            treasure.notes,
        )
    for encounter in registry.encounters.list(world_id):
        yield Text(
            "encounter",
            encounter.id,
            encounter.name,
            encounter_url(encounter.id),
            encounter.scope or here,
            encounter.notes,
        )
    for note in sorted(
        registry.gamestate.session_notes.list(lambda n: n.world_id == world_id),
        key=lambda n: n.day,
    ):
        yield Text(
            "session",
            note.id,
            page.t("links.session_title", day=day_text(page, note.day)),
            f"{SESSIONS_URL}#note-{note.id}",
            Scope(world_id=world_id, adventure_id=note.adventure_id),
            note.text,
        )


def world_problems(page: Page, registry: StoreRegistry, world_id: str) -> list[Checked]:
    """Every text of the world that has a problem, in the order of the world's lists."""
    found: list[Checked] = []
    for text in world_texts(page, registry, world_id):
        if not text.body.strip():
            continue
        if text.kind == "chapter":
            faults = chapter_problems(page, registry, text.id, text.body)
        else:
            faults = notes_problems(page, registry, text.scope, text.body)
        if faults:
            meant = [_meant(registry, text.scope, x) for x in faults]
            found.append(
                Checked(text.kind, text.id, text.title, text.url, text.scope, faults, meant)
            )
    return found


@dataclass(frozen=True)
class Unused:
    """An NPC, place, treasure or encounter nothing in the world uses."""

    kind: str
    id: str
    name: str
    url: str


_BLOCK_LINE = re.compile(r"^ {0,3}:{3,}\s*(\S+)\s+(.+?)\s*$")


def _named(body: str) -> Iterator[tuple[str, str]]:
    """``(kind, name)`` of every NPC, place, treasure and encounter a text names: its
    ``{@…}`` tags, nested ones too, and old ``:::`` blocks."""

    def walk(nodes: Sequence[object]) -> Iterator[tuple[str, str]]:
        for node in nodes:
            if isinstance(node, tags.Tag):
                if node.name in RENAMEABLE and node.part(0):
                    yield node.name, node.part(0)
                for part in node.parts:
                    yield from walk(tags.parse(part))

    yield from walk(tags.parse(body))
    for line in body.splitlines():
        block = _BLOCK_LINE.match(line)
        if block and block.group(1).lower() in RENAMEABLE:
            yield block.group(1).lower(), block.group(2)


def unused_objects(page: Page, registry: StoreRegistry, world_id: str) -> list[Unused]:
    """The world's NPCs, places, treasure and encounters that no text names (as the page
    resolves the name, nearest scope first), no adventure lists as ready-made and, for an
    encounter, that was never fought."""
    finders = {
        "npc": registry.npcs.find,
        "place": registry.locations.find,
        "treasure": registry.treasure.find,
        "encounter": registry.encounters.find,
    }
    used: set[str] = set()
    for text in world_texts(page, registry, world_id):
        for kind, name in _named(text.body):
            record = finders[kind](name, text.scope)
            if record is not None and record.id != text.id:  # naming itself is no use
                used.add(record.id)
    for adventure in registry.adventures.adventures(world_id):
        used.update(adventure.uses)
    used.update(
        c.encounter_id
        for c in registry.gamestate.combats.list(lambda c: c.world_id == world_id)
        if c.encounter_id
    )
    found = [
        *(Unused("npc", n.id, n.name, npc_url(n.id)) for n in registry.npcs.list(world_id)),
        *(
            Unused("place", x.id, x.name, location_url(x.id))
            for x in registry.locations.list(world_id)
        ),
        *(
            Unused("treasure", x.id, x.name, treasure_url(x.id))
            for x in registry.treasure.list(world_id)
        ),
        *(
            Unused("encounter", e.id, e.name, encounter_url(e.id))
            for e in registry.encounters.list(world_id)
        ),
    ]
    return [u for u in found if u.id not in used]


RENAMEABLE: dict[str, str] = {
    "npc": "npcs",
    "place": "locations",
    "treasure": "treasures",
    "encounter": "encounters",
}


def _meant(registry: StoreRegistry, here: Scope, problem: Problem) -> list[tuple[str, str]]:
    """The objects a missing NPC, place, treasure or encounter link probably meant."""
    if problem.create is None:
        return []
    kind, name = problem.create
    finders = {
        "npc": registry.npcs.find,
        "place": registry.locations.find,
        "treasure": registry.treasure.find,
        "encounter": registry.encounters.find,
    }
    found = (finders[kind](near, here) for near in near_objects(registry, here, kind, name))
    return [(record.id, record.name) for record in found if record is not None]


def links_page(
    page: Page,
    world_name: str,
    checked: Sequence[Checked],
    message: str | None = None,
    unused: Sequence[Unused] = (),
) -> Element:
    """The world's texts with their problems, each mendable from here; then what nothing
    uses, to open or delete."""
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
            _unused_section(page, unused) if unused else None,
        ],
    )


def _unused_section(page: Page, unused: Sequence[Unused]) -> Element:
    t = page.t
    return section(class_="links-text links-unused panel", id="unused")[
        div(class_="links-text-head")[
            span(class_="eyebrow")[t.plural("links.unused_count", len(unused))],
            b(class_="links-text-title")[t("links.unused_title")],
            p(class_="field-hint")[t("links.unused_hint")],
        ],
        ul(class_="links-unused-list")[
            [
                li(class_="links-unused-row")[
                    span(class_="muted links-unused-kind")[t(f"md.embed.{u.kind}")],
                    a(href=u.url)[u.name],
                    a(href=f"{u.url}/delete", class_="btn btn-sm btn-ghost btn-danger")[
                        icon("trash-2", "sm"), span[t("links.delete")]
                    ],
                ]
                for u in unused
            ]
        ],
    ]


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
                        [
                            form(method="post", action=RENAME_URL, class_="inline-form")[
                                hidden("kind", x.create[0]),
                                hidden("id", object_id),
                                hidden("name", x.create[1]),
                                button(type="submit", class_="btn btn-sm btn-ghost check-rename")[
                                    icon("pencil", "sm"),
                                    span[t("links.rename", old=object_name, new=x.create[1])],
                                ],
                            ]
                            for object_id, object_name in meant
                        ]
                        if x.create
                        else None,
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
                for x, meant in zip(
                    checked.problems, checked.meant or [[]] * len(checked.problems), strict=True
                )
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
    message = {
        "1": page.t("links.fixed"),
        "stale": page.t("links.stale"),
        "renamed": page.t("links.renamed"),
    }.get(fixed)
    checked = await asyncio.to_thread(world_problems, page, registry, world.id)
    unused = await asyncio.to_thread(unused_objects, page, registry, world.id)
    return html(links_page(page, world.name, checked, message, unused))


@router.get(f"{LINKS_URL}/count", response_class=PlainTextResponse)
async def count_links(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> str:
    """How many things in the table's world lead nowhere, for the world menu's badge."""
    world = registry.table.world()
    if world is None:
        return "0"
    checked = await asyncio.to_thread(world_problems, page, registry, world.id)
    return str(sum(len(c.problems) for c in checked))


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


@router.post(RENAME_URL)
async def rename_object(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    id: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
    name: Annotated[str, Form()] = "",
) -> Response:
    """The text is right and the object's name is not: rename it, and every link to its old
    name in the world's texts follows."""
    if kind not in RENAMEABLE or not name.strip():
        raise HTTPException(status_code=400)
    collection = getattr(registry.gamestate, RENAMEABLE[kind])
    record = collection.find(id)
    if record is None or _world_of(kind, record) != registry.table.world_id():
        raise HTTPException(status_code=404)
    old, new = record.name, name.strip()
    collection.update(id, name=new)
    where: Scope | None = record.scope  # an encounter may have none: its world's, then
    world_id = where.world_id if where is not None else str(record.world_id)
    adventure_id = where.adventure_id if where is not None else None
    registry.adventures.rewrite_references(world_id, kind, old, new, adventure_id)
    return redirect(f"{LINKS_URL}?fixed=renamed")


def _world_of(kind: str, record: object) -> str | None:
    if kind == "world":
        return str(getattr(record, "id", ""))
    scope = getattr(record, "scope", None)
    if isinstance(scope, Scope):
        return scope.world_id
    world_id = getattr(record, "world_id", None)
    return str(world_id) if world_id else None


__all__ = ["router", "world_problems"]
