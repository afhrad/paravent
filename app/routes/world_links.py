"""Every link of the table's world that leads nowhere, on one page (ADVENTURES.md §5.7).

* ``GET  /gm/world/links``     — the chapter check over every text of the world: its
  description, adventures, chapters, session notes and the notes of its NPCs, places,
  encounters and treasure, each problem with what would mend it (a quest's text, a
  faction's note and Claude's instructions are plain text, where a tag is never a link)
* ``POST /gm/world/links/fix`` — one suggested replacement written into its text, on its line
* ``POST /gm/world/links/rename`` — or the object renamed to the name the text uses, every
  link to its old name following
* ``POST /gm/world/links/unused`` — unused objects ticked: asked, then deleted
* ``POST /gm/world/links/restore`` — a deleted object back from the trash (app/services/trash.py)
* ``POST /gm/world/links/empty-trash`` — the world's trash forgotten

A missing NPC, place, treasure or encounter is offered to create through the same page as a
dashed link in a chapter (``/gm/editor/new``), coming back here.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from htpy import Element, a, b, button, code, div, form, li, p, section, span, ul
from htpy import input as input_el

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Scope, TrashedObject
from app.routes.adventures import chapter_problems, notes_problems
from app.routes.auth import html, redirect, safe_next
from app.routes.editor import RENAMEABLE, rename_object
from app.services import scope as scopes
from app.services.trash import KEEP_DAYS, TrashError
from app.store.registry import StoreRegistry
from app.ui import tags
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.faction_pages import factions_url
from app.ui.forms import hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import (
    TRASH_RESTORE_URL,
    adventure_url,
    chapter_url,
    location_url,
    new_object_url,
    npc_url,
    treasure_url,
    world_url,
)
from app.ui.prose_check import Problem
from app.ui.quest_pages import quests_url
from app.ui.session_pages import SESSIONS_URL, day_text
from app.ui.world_menu import DELETED_WORLDS_URL, LINKS_URL

router = APIRouter(tags=["world links"])

FIX_URL = f"{LINKS_URL}/fix"
RENAME_URL = f"{LINKS_URL}/rename"
UNUSED_URL = f"{LINKS_URL}/unused"
RESTORE_URL = TRASH_RESTORE_URL
EMPTY_URL = f"{LINKS_URL}/empty-trash"

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
            found.append(Checked(text.kind, text.id, text.title, text.url, text.scope, faults))
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


def names_in_texts(page: Page, registry: StoreRegistry, world_id: str) -> Counter[tuple[str, str]]:
    """How many texts of the world name each ``(kind, name)``, the name in lower case."""
    found: Counter[tuple[str, str]] = Counter()
    for text in world_texts(page, registry, world_id):
        found.update({(kind, name.casefold()) for kind, name in _named(text.body)})
    return found


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


def deleted_worlds_page(page: Page, trashed: Sequence[TrashedObject]) -> Element:
    """The worlds deleted lately: each with when it went and **Restore**."""
    t = page.t
    return app_shell(
        page,
        t("world.deleted_title"),
        div(class_="links-page")[
            page_head(t("gm.eyebrow"), t("world.deleted_title"), t("world.deleted_lede")),
            section(class_="links-text links-unused panel")[
                ul(class_="links-unused-list")[
                    [
                        li(class_="links-unused-row")[
                            span(class_="muted links-unused-kind")[t("md.embed.world")],
                            span[x.name],
                            span(class_="muted")[
                                x.deleted_at.astimezone().strftime("%Y-%m-%d %H:%M")
                            ],
                            form(method="post", action=RESTORE_URL, class_="inline-form")[
                                hidden("id", x.id),
                                button(type="submit", class_="btn btn-sm")[
                                    icon("rotate-ccw", "sm"), span[t("trash.restore")]
                                ],
                            ],
                        ]
                        for x in trashed
                    ]
                ]
                if trashed
                else p(class_="muted")[t("world.deleted_none")],
            ],
        ],
    )


def links_page(
    page: Page,
    world_name: str,
    checked: Sequence[Checked],
    message: str | None = None,
    unused: Sequence[Unused] = (),
    trashed: Sequence[TrashedObject] = (),
    naming: Counter[tuple[str, str]] | None = None,
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
            _trash_section(page, trashed, naming or Counter()) if trashed else None,
        ],
    )


def _trash_section(
    page: Page, trashed: Sequence[TrashedObject], naming: Counter[tuple[str, str]]
) -> Element:
    """What was deleted lately, to restore; how many texts still name each, whose links
    work again once it is back."""
    t = page.t
    return section(class_="links-text links-unused panel", id="trash")[
        div(class_="links-text-head")[
            span(class_="eyebrow")[t.plural("links.unused_count", len(trashed))],
            b(class_="links-text-title")[t("trash.title")],
            p(class_="field-hint")[t("trash.hint", days=KEEP_DAYS)],
        ],
        ul(class_="links-unused-list")[
            [
                li(class_="links-unused-row")[
                    span(class_="muted links-unused-kind")[t(f"md.embed.{x.kind}")],
                    span[x.name],
                    span(class_="muted")[x.deleted_at.astimezone().strftime("%Y-%m-%d %H:%M")],
                    span(class_="muted")[
                        t.plural("trash.named", naming[(x.kind, x.name.casefold())])
                    ]
                    if naming[(x.kind, x.name.casefold())]
                    else None,
                    form(method="post", action=RESTORE_URL, class_="inline-form")[
                        hidden("id", x.id),
                        button(type="submit", class_="btn btn-sm")[
                            icon("rotate-ccw", "sm"), span[t("trash.restore")]
                        ],
                    ],
                ]
                for x in trashed
            ]
        ],
        form(method="post", action=EMPTY_URL, class_="form-actions")[
            button(type="submit", class_="btn btn-sm btn-ghost btn-danger")[
                icon("trash-2", "sm"), span[t("trash.empty")]
            ]
        ],
    ]


def _unused_section(page: Page, unused: Sequence[Unused]) -> Element:
    t = page.t
    return section(class_="links-text links-unused panel", id="unused")[
        div(class_="links-text-head")[
            span(class_="eyebrow")[t.plural("links.unused_count", len(unused))],
            b(class_="links-text-title")[t("links.unused_title")],
            p(class_="field-hint")[t("links.unused_hint")],
        ],
        form(method="post", action=UNUSED_URL)[
            ul(class_="links-unused-list")[
                [
                    li(class_="links-unused-row")[
                        input_el(
                            type="checkbox",
                            name="pick",
                            value=f"{u.kind}:{u.id}",
                            id=f"pick-{u.id}",
                            aria_label=u.name,
                        ),
                        span(class_="muted links-unused-kind")[t(f"md.embed.{u.kind}")],
                        a(href=u.url)[u.name],
                        a(href=f"{u.url}/delete", class_="btn btn-sm btn-ghost btn-danger")[
                            icon("trash-2", "sm"), span[t("links.delete")]
                        ],
                    ]
                    for u in unused
                ]
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-sm btn-danger")[
                    icon("trash-2", "sm"), span[t("links.delete_ticked")]
                ]
            ],
        ],
    ]


def _confirm_unused(page: Page, picked: Sequence[Unused]) -> Element:
    """Ask before deleting several: their names, and one button."""
    t = page.t
    return app_shell(
        page,
        t("links.delete_title", count=len(picked)),
        div(class_="formpage")[
            a(href=f"{LINKS_URL}#unused", class_="backlink")[
                icon("arrow-left"), span[t("common.cancel")]
            ],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("links.title")],
                    b[t.plural("links.delete_heading", len(picked))],
                ],
                ul[[li[t(f"md.embed.{u.kind}"), ": ", b[u.name]] for u in picked]],
                p(class_="field-hint")[t("links.delete_hint")],
                form(method="post", action=UNUSED_URL)[
                    [hidden("pick", f"{u.kind}:{u.id}") for u in picked],
                    hidden("confirmed", "1"),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[
                            icon("trash-2"), span[t.plural("links.delete_n", len(picked))]
                        ],
                        a(href=f"{LINKS_URL}#unused", class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
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
                            for object_id, object_name in x.rename
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
                for x in checked.problems
            ]
        ],
    ]


@router.get(LINKS_URL, response_class=HTMLResponse)
async def world_links(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, fixed: str = "", n: str = ""
) -> HTMLResponse:
    """The chapter check over the whole table's world."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    message = {
        "1": page.t("links.fixed"),
        "stale": page.t("links.stale"),
        "renamed": page.t("links.renamed"),
        "deleted": page.t.plural("links.deleted", int(n) if n.isdigit() else 0),
        "emptied": page.t("trash.emptied"),
    }.get(fixed)
    if fixed.startswith("trash."):  # a restore refused, by its key
        message = page.t(fixed) if fixed in TRASH_ERRORS else None
    checked = await asyncio.to_thread(world_problems, page, registry, world.id)
    unused = await asyncio.to_thread(unused_objects, page, registry, world.id)
    trashed = registry.trash.of_world(world.id)
    naming = await asyncio.to_thread(names_in_texts, page, registry, world.id) if trashed else None
    return html(links_page(page, world.name, checked, message, unused, trashed, naming))


@router.get(f"{LINKS_URL}/count", response_class=PlainTextResponse)
async def count_links(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> str:
    """How many things in the table's world lead nowhere, for the world menu's badge."""
    world = registry.table.world()
    if world is None:
        return "0"
    checked = await asyncio.to_thread(world_problems, page, registry, world.id)
    return str(sum(len(c.problems) for c in checked))


@router.get(f"{LINKS_URL}/unused-count", response_class=PlainTextResponse)
async def count_unused(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> str:
    """How many objects of the table's world nothing uses, for the world menu."""
    world = registry.table.world()
    if world is None:
        return "0"
    return str(len(await asyncio.to_thread(unused_objects, page, registry, world.id)))


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
async def rename_from_links(
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
    if not rename_object(registry, kind, id, name, registry.table.world_id()):
        raise HTTPException(status_code=404)
    return redirect(f"{LINKS_URL}?fixed=renamed")


@router.post(UNUSED_URL, response_class=HTMLResponse)
async def delete_unused(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    pick: Annotated[list[str] | None, Form()] = None,
    confirmed: Annotated[str, Form()] = "",
) -> Response:
    """Several unused objects deleted at once: asked first, then each deleted only if it is
    still unused (a text may name it by now)."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    unused = await asyncio.to_thread(unused_objects, page, registry, world.id)
    wanted = set(pick or ())
    picked = [u for u in unused if f"{u.kind}:{u.id}" in wanted]
    if not picked:
        return redirect(f"{LINKS_URL}#unused")
    if confirmed != "1":
        return html(_confirm_unused(page, picked))
    deleters = {
        "npc": registry.npcs.delete,
        "place": registry.locations.delete,
        "treasure": registry.treasure.delete,
        "encounter": registry.encounters.delete,
    }
    for u in picked:
        deleters[u.kind](u.id)
    return redirect(f"{LINKS_URL}?{urlencode({'fixed': 'deleted', 'n': len(picked)})}#unused")


TRASH_ERRORS = frozenset(
    {
        "trash.error.gone",
        "trash.error.world",
        "trash.error.taken",
        "trash.error.unreadable",
        "trash.error.adventure",
        "trash.error.file",
    }
)


@router.get(DELETED_WORLDS_URL, response_class=HTMLResponse)
async def deleted_worlds(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> Response:
    """The worlds deleted lately, each to restore."""
    return html(deleted_worlds_page(page, registry.trash.worlds()))


@router.post(RESTORE_URL)
async def restore_trashed(
    _gm: GamemasterDep,
    registry: RegistryDep,
    id: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
    back: Annotated[str, Form()] = "",
) -> Response:
    """Put a deleted object back where it was (its world, at least) and open it, or go
    ``back`` to the text whose link asked for it (the create page)."""
    if registry.gamestate.trash.find(id) is None:
        raise HTTPException(status_code=404)
    try:
        kind, record = registry.trash.restore(id)
    except TrashError as exc:
        return redirect(f"{LINKS_URL}?{urlencode({'fixed': exc.key})}#trash")
    if safe_next(back):
        return redirect(str(safe_next(back)))
    urls = {
        "npc": npc_url,
        "place": location_url,
        "treasure": treasure_url,
        "encounter": encounter_url,
        "chapter": chapter_url,
        "adventure": adventure_url,
    }
    if kind == "world":
        if registry.table.world_id() is None:  # a table with no world plays in it again
            registry.table.select(record.id)
            registry.table.announce()
        return redirect(world_url(record.id))
    if kind == "quest":
        return redirect(quests_url(str(getattr(record, "world_id", ""))))
    if kind == "faction":
        return redirect(factions_url(str(getattr(record, "world_id", ""))))
    return redirect(urls[kind](record.id))


@router.post(EMPTY_URL)
async def empty_trash(_gm: GamemasterDep, registry: RegistryDep) -> Response:
    """Forget what the table's world's trash holds."""
    world_id = registry.table.world_id()
    if world_id is None:
        raise HTTPException(status_code=404)
    registry.trash.empty(world_id)
    return redirect(f"{LINKS_URL}?fixed=emptied")


def _world_of(kind: str, record: object) -> str | None:
    if kind == "world":
        return str(getattr(record, "id", ""))
    scope = getattr(record, "scope", None)
    if isinstance(scope, Scope):
        return scope.world_id
    world_id = getattr(record, "world_id", None)
    return str(world_id) if world_id else None


__all__ = ["router", "world_problems"]
