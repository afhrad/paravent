"""Context menus (documentation/CONTEXT-MENUS.md): each answers with the menu of one thing,
as the reader may use it; ``static/js/menu.js`` shows it.

* ``GET  /menu/link?href=…``        — a campaign link, card or list row by where it leads
  (NPC, place, treasure, encounter, chapter, adventure, character, session note, creature
  design, naming style), or a missing link
* ``GET  /menu/party/{character}``  — a party card
* ``GET  /menu/item/{character}/{n}`` — an item on the sheet
* ``GET  /menu/spell/{character}?ref=…`` — a spell on the sheet
* ``GET  /menu/character/{character}`` — a row of a character list, a player's own too
* ``GET  /menu/sound/{sound}``      — a sound of the library
* ``GET  /menu/image?share=…&src=…`` — a picture in a chapter
* ``GET  /menu/log/{entry}``        — an entry of the log
* ``POST /menu/duplicate``          — a copy of an NPC, place, treasure or encounter
* ``POST /menu/move``               — an object placed in a chapter or adventure, moved up

Every item leads to a route that already exists and checks the reader's role itself;
these only say what there is to do. ``next`` is the page the menu was opened on, where a
delete or a move returns.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.dependencies import CharactersDep, GamemasterDep, IdentityDep, PageDep, RegistryDep
from app.models.gamestate import Scope
from app.routes.adventures import meant_objects
from app.routes.auth import html, redirect, safe_next
from app.services import copies
from app.services import scope as scopes
from app.services.creature_builder import CreatureNotFound
from app.services.holdings import HoldingsError
from app.services.protocol_view import ProtocolReader, hidden_from_players
from app.services.session_notes import SessionNoteNotFound
from app.store.errors import EntryNotFound
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.context_menu import MenuGroup, MenuItem, menu_fragment
from app.ui.creature_links import CREATURES_URL, creature_url
from app.ui.encounter_pages import ENCOUNTERS_URL, encounter_url
from app.ui.hand_pages import hand_url
from app.ui.journal_pages import SHARE_URL
from app.ui.library_links import (
    ADVENTURES_URL,
    CHAPTERS_URL,
    LOCATIONS_URL,
    NEW_OBJECT_URL,
    NPCS_URL,
    TREASURE_URL,
    adventure_url,
    chapter_url,
    location_url,
    npc_url,
    treasure_url,
    world_url,
)
from app.ui.names import name_text
from app.ui.naming_pages import NAMING_URL, style_url
from app.ui.protocol_pages import LOG_URL
from app.ui.session_pages import SESSIONS_URL, day_text, note_url
from app.ui.sound_panel import SOUND_URL, SOUNDS_URL
from app.ui.tags import ref_url

router = APIRouter(tags=["menus"])

_ADDRESS = re.compile(
    "^(?:"
    f"(?P<npc>{NPCS_URL})|(?P<place>{LOCATIONS_URL})|(?P<encounter>{ENCOUNTERS_URL})"
    f"|(?P<treasure>{TREASURE_URL})|(?P<chapter>{CHAPTERS_URL})|(?P<adventure>{ADVENTURES_URL})"
    "|(?P<character>/characters)"
    f"|(?P<note>{SESSIONS_URL})|(?P<creature>{CREATURES_URL})|(?P<style>{NAMING_URL})"
    ")/(?P<id>[A-Za-z0-9_-]+)$"
)
# The kinds a copy can be made of, and the collection each lives in.
COPYABLE = {"npc": "npcs", "place": "locations", "treasure": "treasures", "encounter": "encounters"}
# Where each placed kind's pages are.
PAGES = {
    "npc": npc_url,
    "place": location_url,
    "treasure": treasure_url,
    "encounter": encounter_url,
}
EDITS = {"npc", "place"}  # the others are edited on their own page


def _menu(page: Page, title: str, meta: str, groups: list[MenuGroup]) -> HTMLResponse:
    return html(menu_fragment(page, title, meta, groups))


def _placed(registry: StoreRegistry, kind: str, record_id: str) -> Any:
    """The NPC, place, treasure or encounter of that id, or None."""
    return getattr(registry.gamestate, COPYABLE[kind]).find(record_id)


def _where(page: Page, registry: StoreRegistry, where: Scope | None) -> str:
    if where is None:
        return ""
    return " › ".join(scopes.label(registry.adventures, where))


def _object_menu(
    page: Page, registry: StoreRegistry, kind: str, record_id: str, next_page: str
) -> HTMLResponse:
    """An NPC, place, treasure or encounter: open, edit, run, copy, move up, delete."""
    t = page.t
    record = _placed(registry, kind, record_id)
    if record is None:
        raise HTTPException(status_code=404)
    url = PAGES[kind](record_id)
    name = str(getattr(record, "name", ""))
    where: Scope | None = getattr(record, "scope", None)
    opening = [
        MenuItem(t("menu.open"), "arrow-right", href=url),
        MenuItem(t("menu.open_tab"), "arrow-up", href=url, new_tab=True),
    ]
    if kind in EDITS:
        opening.append(MenuItem(t("menu.edit"), "pencil", href=f"{url}/edit"))
    if kind == "encounter":
        opening.append(MenuItem(t("menu.run"), "play", post=f"{url}/run"))
    changing = [
        MenuItem(
            t("menu.duplicate"),
            "copy",
            post="/menu/duplicate",
            fields={"kind": kind, "id": record_id},
        ),
    ]
    if where is not None and (where.chapter_id or where.adventure_id):
        up = "menu.move_adventure" if where.chapter_id else "menu.move_world"
        changing.append(
            MenuItem(
                t(up),
                "arrow-up",
                post="/menu/move",
                fields={"kind": kind, "id": record_id, "next": next_page},
            )
        )
    deleting = [
        MenuItem(
            t("menu.delete"),
            "trash-2",
            post=f"{url}/delete",
            fields={"next": next_page},
            danger=True,
            hint=t("menu.undo_hint"),
        )
    ]
    meta = " · ".join(x for x in (t(f"menu.kind.{kind}"), _where(page, registry, where)) if x)
    return _menu(page, name, meta, [MenuGroup(opening), MenuGroup(changing), MenuGroup(deleting)])


def _chapter_menu(
    page: Page, registry: StoreRegistry, chapter_id: str, next_page: str
) -> HTMLResponse:
    """A chapter: open, edit, run or end it, new things placed in it, delete (asked)."""
    t = page.t
    view = registry.adventures.chapter_view(chapter_id)
    chapter = view.chapter
    url = chapter_url(chapter_id)
    here = f"c:{chapter_id}"
    running = chapter.status == "running"
    return _menu(
        page,
        chapter.title,
        " · ".join([t("menu.kind.chapter"), view.adventure.name]),
        [
            MenuGroup(
                [
                    MenuItem(t("menu.open"), "arrow-right", href=url),
                    MenuItem(t("menu.open_tab"), "arrow-up", href=url, new_tab=True),
                    MenuItem(t("menu.edit"), "pencil", href=f"{url}/edit"),
                    MenuItem(
                        t("menu.end" if running else "menu.start"),
                        "check" if running else "play",
                        post=f"{url}/{'end' if running else 'start'}",
                        fields={"back": next_page},
                    ),
                ]
            ),
            _new_here(page, chapter.world_id, here),
            MenuGroup(
                [
                    MenuItem(
                        t("menu.delete_ask"),
                        "trash-2",
                        dialog=f"{url}/delete",
                        danger=True,
                    )
                ]
            ),
        ],
    )


def _adventure_menu(
    page: Page, registry: StoreRegistry, adventure_id: str, next_page: str
) -> HTMLResponse:
    t = page.t
    adventure = registry.adventures.get(adventure_id)
    url = adventure_url(adventure_id)
    running = adventure.status == "running"
    return _menu(
        page,
        adventure.name,
        t("menu.kind.adventure"),
        [
            MenuGroup(
                [
                    MenuItem(t("menu.open"), "arrow-right", href=url),
                    MenuItem(t("menu.open_tab"), "arrow-up", href=url, new_tab=True),
                    MenuItem(t("menu.edit"), "pencil", href=f"{url}/edit"),
                    MenuItem(t("menu.new_chapter"), "plus", href=f"{url}?tool=chapter"),
                    MenuItem(
                        t("menu.end" if running else "menu.start"),
                        "check" if running else "play",
                        post=f"{url}/{'end' if running else 'start'}",
                        fields={"back": next_page},
                    ),
                ]
            ),
            _new_here(page, adventure.world_id, f"a:{adventure_id}"),
            MenuGroup(
                [MenuItem(t("menu.delete_ask"), "trash-2", dialog=f"{url}/delete", danger=True)]
            ),
        ],
    )


def _new_here(page: Page, world_id: str, here: str) -> MenuGroup:
    """New NPCs, places, encounters and treasure placed in a chapter or adventure: the
    lists' own New dialogs, told where (``place``)."""
    t = page.t
    return MenuGroup(
        [
            MenuItem(t("menu.new.npc"), "users-round", href=f"{NPCS_URL}?tool=npc&place={here}"),
            MenuItem(
                t("menu.new.place"),
                "map-pin",
                href=f"{world_url(world_id)}/places?tool=place&place={here}",
            ),
            MenuItem(
                t("menu.new.encounter"),
                "swords",
                href=f"{ENCOUNTERS_URL}?tool=encounter&place={here}",
            ),
            MenuItem(
                t("menu.new.treasure"),
                "gem",
                href=f"{TREASURE_URL}?tool=treasure&place={here}",
            ),
        ],
        label=t("menu.new_here"),
    )


def _missing_menu(page: Page, registry: StoreRegistry, href: str) -> HTMLResponse:
    """A link to something the world lacks: make it in the chapter, the adventure or the
    world, or open what it probably meant."""
    t = page.t
    query = {k: v[0] for k, v in parse_qs(urlsplit(href).query).items()}
    kind, name, value = query.get("kind", ""), query.get("name", ""), query.get("scope", "")
    if kind not in PAGES or not name:
        raise HTTPException(status_code=404)
    try:
        where = scopes.decode(value, registry.adventures)
    except scopes.ScopeError as exc:
        raise HTTPException(status_code=404) from exc
    places: list[tuple[str, str, str]] = []
    if where.chapter_id:
        places.append(("menu.in_chapter", "plus", f"c:{where.chapter_id}"))
    if where.adventure_id:
        places.append(("menu.in_adventure", "book-open", f"a:{where.adventure_id}"))
    places.append(("menu.in_world", "globe", f"w:{where.world_id}"))
    create = [
        MenuItem(
            t(key), glyph, post=NEW_OBJECT_URL, fields={"kind": kind, "name": name, "scope": s}
        )
        for key, glyph, s in places
    ]
    meant = [
        MenuItem(found, "arrow-right", href=PAGES[kind](ident))
        for ident, found in meant_objects(registry, where, kind, name)[:4]
    ]
    return _menu(
        page,
        name,
        t("menu.missing"),
        [
            MenuGroup(create, label=t(f"menu.create.{kind}")),
            MenuGroup(meant, label=t("menu.meant")),
            MenuGroup([MenuItem(t("menu.more_choices"), "list", href=href)]),
        ],
    )


def _character_items(page: Page, character_id: str, next_page: str) -> list[MenuGroup]:
    t = page.t
    sheet = f"/characters/{character_id}"
    return [
        MenuGroup(
            [
                MenuItem(t("menu.open_sheet"), "arrow-right", href=sheet),
                MenuItem(t("menu.open_tab"), "arrow-up", href=sheet, new_tab=True),
                MenuItem(t("menu.downtime"), "hourglass", href=f"{sheet}/downtime"),
                MenuItem(t("menu.followers"), "users", href=f"{sheet}/followers"),
                MenuItem(t("menu.bastion"), "vault", href=f"{sheet}/bastion"),
                MenuItem(t("menu.story"), "scroll", href=f"{sheet}/story"),
            ]
        ),
        MenuGroup(
            [
                MenuItem(
                    t("menu.grant_level"),
                    "plus",
                    post=f"{sheet}/grant-level",
                    fields={"next": next_page},
                ),
                MenuItem(t("menu.award_xp"), "sparkles", href="/gm?tool=xp"),
                MenuItem(t("menu.madness"), "skull", href="/gm?tool=madness"),
            ]
        ),
    ]


def _character_delete(page: Page, character_id: str) -> MenuGroup:
    """Deleting a character's file: asked in a dialog, for good."""
    return MenuGroup(
        [
            MenuItem(
                page.t("menu.delete_ask"),
                "trash-2",
                dialog=f"/characters/{character_id}/delete",
                danger=True,
                hint=page.t("menu.for_good"),
            )
        ]
    )


def _delete_ask(page: Page, url: str) -> MenuGroup:
    """Delete…, asked in a dialog: a kind deleted for good (CONTEXT-MENUS.md §5)."""
    return MenuGroup(
        [
            MenuItem(
                page.t("menu.delete_ask"),
                "trash-2",
                dialog=url,
                danger=True,
                hint=page.t("menu.for_good"),
            )
        ]
    )


def _note_menu(page: Page, registry: StoreRegistry, note_id: str) -> HTMLResponse:
    """A session note: edit it, send it to the players, delete it (asked)."""
    t = page.t
    try:
        note = registry.session_notes.get(note_id)
    except SessionNoteNotFound as exc:
        raise HTTPException(status_code=404) from exc
    url = note_url(note.id)
    return _menu(
        page,
        day_text(page, note.day),
        t("menu.kind.note"),
        [
            MenuGroup(
                [
                    MenuItem(t("menu.edit"), "pencil", href=f"{url}/edit"),
                    MenuItem(t("sessions.share"), "send", post=f"{url}/share"),
                ]
            ),
            _delete_ask(page, f"{url}/delete"),
        ],
    )


def _creature_menu(page: Page, registry: StoreRegistry, design_id: str) -> HTMLResponse:
    """A creature design: open it in the builder, delete it (asked)."""
    t = page.t
    try:
        design = registry.creatures.get(design_id)
    except CreatureNotFound as exc:
        raise HTTPException(status_code=404) from exc
    url = creature_url(design.id)
    return _menu(
        page,
        design.name,
        t("menu.kind.creature"),
        [
            MenuGroup(
                [
                    MenuItem(t("menu.open"), "arrow-right", href=url),
                    MenuItem(t("menu.open_tab"), "arrow-up", href=url, new_tab=True),
                ]
            ),
            _delete_ask(page, f"{url}/delete"),
        ],
    )


def _style_menu(page: Page, registry: StoreRegistry, style_id: str) -> HTMLResponse:
    """One of the table's naming styles: open, edit, its raw text, delete (asked)."""
    t = page.t
    try:
        style = registry.naming_workshop.get(style_id)
    except EntryNotFound as exc:
        raise HTTPException(status_code=404) from exc
    url = style_url(style.id)
    return _menu(
        page,
        style.name,
        " · ".join(x for x in (t("menu.kind.style"), style.race) if x),
        [
            MenuGroup(
                [
                    MenuItem(t("menu.open"), "arrow-right", href=url),
                    MenuItem(t("menu.edit"), "pencil", href=f"{url}/edit"),
                    MenuItem(t("naming.raw"), "list", href=f"{url}/raw"),
                ]
            ),
            _delete_ask(page, f"{url}/delete"),
        ],
    )


@router.get("/menu/link", response_class=HTMLResponse)
async def link_menu(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    href: str = "",
    next: str = "",  # noqa: A002 — the query's name
) -> HTMLResponse:
    """The menu of whatever a campaign link, card or list row leads to."""
    next_page = safe_next(next)
    path = urlsplit(href).path
    if path == NEW_OBJECT_URL:
        return _missing_menu(page, registry, href)
    found = _ADDRESS.match(path)
    if found is None:
        raise HTTPException(status_code=404)
    kind = [k for k, v in found.groupdict().items() if v and k != "id"][0]
    record_id = found["id"]
    if kind in PAGES:
        return _object_menu(page, registry, kind, record_id, next_page)
    if kind == "chapter":
        return _chapter_menu(page, registry, record_id, next_page)
    if kind == "adventure":
        return _adventure_menu(page, registry, record_id, next_page)
    if kind == "note":
        return _note_menu(page, registry, record_id)
    if kind == "creature":
        return _creature_menu(page, registry, record_id)
    if kind == "style":
        return _style_menu(page, registry, record_id)
    pc = registry.gamestate.characters.find(record_id)
    if pc is None:
        raise HTTPException(status_code=404)
    return _menu(
        page,
        pc.name or "?",
        page.t("menu.kind.character"),
        [*_character_items(page, pc.id, next_page), _character_delete(page, pc.id)],
    )


@router.get("/menu/party/{character_id}", response_class=HTMLResponse)
async def party_menu(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    character_id: str,
    next: str = "",  # noqa: A002 — the query's name
) -> HTMLResponse:
    """A party card: the character's pages, a level, experience, madness."""
    pc = registry.gamestate.characters.find(character_id)
    if pc is None:
        raise HTTPException(status_code=404)
    summary = " · ".join(f"{name_text(page, 'class', c.name)} {c.level}" for c in pc.classes)
    return _menu(page, pc.name or "?", summary, _character_items(page, pc.id, safe_next(next)))


@router.get("/menu/item/{character_id}/{index}", response_class=HTMLResponse)
async def item_menu(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
    index: int,
) -> HTMLResponse:
    """An item on the sheet: what its row's buttons do, and its page."""
    t = page.t
    pc = characters.get_for(identity, character_id)
    sheet = registry.characters.sheet(pc)
    item = next((i for i in sheet.inventory if i.index == index), None)
    if item is None:
        raise HTTPException(status_code=404)
    act = f"/characters/{pc.id}/act"
    fields = {"index": str(index)}

    def do(label: str, glyph: str, action: str, **more: str) -> MenuItem:
        return MenuItem(
            label, glyph, post=f"{act}/{action}", fields={**fields, **more}, hx_target="#sheet"
        )

    using: list[MenuItem] = []
    equips = item.kind in ("armor", "shield", "weapon") or item.equipped
    if equips and not (item.bound and item.equipped):
        using.append(do(t("menu.unequip" if item.equipped else "sheet.equip"), "shield", "equip"))
    if item.attunable and not item.bound:
        using.append(
            do(t("menu.attune_end" if item.attuned else "sheet.attune"), "sparkles", "attune")
        )
    if item.charges is not None and item.charges_left > 0:
        using.append(do(t("sheet.charge_spend"), "zap", "charge", op="spend"))
    if item.heal and not item.bound:
        using.append(do(t("sheet.drink"), "heart", "drink"))
    elif item.light:
        using.append(do(t("sheet.light"), "flame", "light"))
    elif item.consumable and not item.bound:
        using.append(do(t("sheet.use"), "check", "use"))
    reading: list[MenuItem] = []
    name, _, source = item.name.partition("|")
    if item.found and not item.unidentified:
        reading.append(
            MenuItem(
                t("menu.full_entry"), "book-open", href=ref_url("item", name, source, pc.world_id)
            )
        )
    if identity.is_gamemaster and item.unidentified:
        reading.append(do(t("menu.identify"), "eye", "identify"))
    if registry.shops.serves(pc.world_id, pc.id) is not None:
        reading.append(MenuItem(t("menu.sell"), "coins", href=f"/characters/{pc.id}/shop"))
    shown = (
        item.shown_as or str(name_text(page, ("item", "baseitem"), name))
        if item.unidentified
        else str(name_text(page, ("item", "baseitem"), name))
    )
    meta = " · ".join(
        x
        for x in (
            t("sheet.equipped") if item.equipped else "",
            t("sheet.attuned") if item.attuned else "",
            f"×{item.quantity}" if item.quantity != 1 else "",
        )
        if x
    )
    handing: list[MenuItem] = []
    if not item.bound:
        form_url = f"{hand_url(pc.id)}?index={index}"
        if registry.characters.companions(pc):
            handing.append(MenuItem(t("menu.give"), "send", dialog=form_url))
        handing.append(
            MenuItem(t("menu.drop"), "trash-2", dialog=f"{form_url}&drop=1", danger=True)
        )
    return _menu(page, shown, meta, [MenuGroup(using), MenuGroup(reading), MenuGroup(handing)])


@router.get("/menu/spell/{character_id}", response_class=HTMLResponse)
async def spell_menu(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
    ref: str = "",
) -> HTMLResponse:
    """A spell on the sheet: cast it at each level open, its full entry, preparing."""
    t = page.t
    pc = characters.get_for(identity, character_id)
    sheet = registry.characters.sheet(pc)
    spell = next((s for s in sheet.spells if s.reference == ref), None)
    if spell is None or not spell.found:
        raise HTTPException(status_code=404)
    act = f"/characters/{pc.id}/act"
    base = {"spell": spell.reference, "subject": spell.reference, "kind": "damage"}
    levels = [0] if spell.level == 0 else list(sheet.castable(spell))
    casting = [
        MenuItem(
            t("sheet.cast") if n == 0 else t("menu.cast_at", level=n),
            "sparkles",
            post=f"{act}/cast",
            fields={**base, "level": str(n or spell.level)},
            hx_target="#sheet",
        )
        for n in levels
    ]
    if spell.damage:
        casting.append(
            MenuItem(
                t("sheet.damage_roll"),
                "dices",
                post=f"{act}/roll",
                fields={**base, "level": str(levels[0] if levels and levels[0] else spell.level)},
                hx_target="#sheet",
            )
        )
    name, _, source = spell.reference.partition("|")
    reading = [
        MenuItem(
            t("menu.full_entry"), "book-open", href=ref_url("spell", name, source, pc.world_id)
        ),
        MenuItem(t("menu.prepare"), "notebook-pen", href=f"/characters/{pc.id}/prepare"),
    ]
    meta = t("sheet.cantrip") if spell.level == 0 else t("rules.spell_level", level=spell.level)
    return _menu(
        page,
        str(name_text(page, "spell", spell.name)),
        meta,
        [MenuGroup(casting), MenuGroup(reading)],
    )


@router.get("/menu/character/{character_id}", response_class=HTMLResponse)
async def character_menu(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    character_id: str,
    next: str = "",  # noqa: A002 — the query's name
) -> HTMLResponse:
    """A row of a character list: the gamemaster's whole menu, a player's own pages of
    their character and deleting it (asked)."""
    t = page.t
    pc = characters.get_for(identity, character_id)
    summary = " · ".join(f"{name_text(page, 'class', c.name)} {c.level}" for c in pc.classes)
    if identity.is_gamemaster:
        groups = _character_items(page, pc.id, safe_next(next))
    else:
        sheet = f"/characters/{pc.id}"
        groups = [
            MenuGroup(
                [
                    MenuItem(t("menu.open_sheet"), "arrow-right", href=sheet),
                    MenuItem(t("menu.downtime"), "hourglass", href=f"{sheet}/downtime"),
                    MenuItem(t("menu.followers"), "users", href=f"{sheet}/followers"),
                    MenuItem(t("menu.story"), "scroll", href=f"{sheet}/story"),
                ]
            )
        ]
    return _menu(
        page, pc.name or t("play.unnamed"), summary, [*groups, _character_delete(page, pc.id)]
    )


@router.get("/menu/sound/{sound_id}", response_class=HTMLResponse)
async def sound_menu(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, sound_id: str
) -> HTMLResponse:
    """A sound of the library: play it on the speaker, hear it here, delete it (asked)."""
    t = page.t
    try:
        sound = registry.sounds.sounds.get(sound_id)
    except EntryNotFound as exc:
        raise HTTPException(status_code=404) from exc
    return _menu(
        page,
        sound.name,
        t(f"sound.kind.{sound.kind}"),
        [
            MenuGroup(
                [
                    MenuItem(t("menu.speaker"), "volume-2", post=f"{SOUND_URL}/press/{sound.id}"),
                    MenuItem(
                        t("menu.listen"), "play", href=f"{SOUND_URL}/file/{sound.id}", new_tab=True
                    ),
                ]
            ),
            _delete_ask(page, f"{SOUNDS_URL}/{sound.id}/delete"),
        ],
    )


_MEDIA_SRC = re.compile(r"^/media/(?P<record>[A-Za-z0-9_-]+)/(?P<path>[^?#]+)")


@router.get("/menu/image", response_class=HTMLResponse)
async def image_menu(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    share: str = "",
    src: str = "",
) -> HTMLResponse:
    """A picture in a chapter: share it with the players, open the original, edit the
    chapter, and every other place the world uses it."""
    t = page.t
    split = urlsplit(share)
    query = {k: v[0] for k, v in parse_qs(split.query).items()}
    chapter_id = query.get("chapter", "")
    if split.path != SHARE_URL or not chapter_id or not query.get("image"):
        raise HTTPException(status_code=404)
    try:
        view = registry.adventures.chapter_view(chapter_id)
    except LookupError as exc:
        raise HTTPException(status_code=404) from exc
    found = _MEDIA_SRC.match(src)
    original = f"/media/{found['record']}/{found['path']}" if found else ""
    used: list[MenuItem] = []
    if found:
        world_path = registry.media_tags.world_path(found["record"], found["path"])
        uses = registry.media_uses.uses(view.world.id).get(world_path or "", [])
        used = [
            MenuItem(use.name or use.owner, "arrow-right", href=use.url)
            for use in uses
            if not use.url.startswith(chapter_url(chapter_id))
        ][:6]
    name = query["image"].partition("|")[0].rsplit("/", 1)[-1]
    return _menu(
        page,
        name,
        t("menu.kind.picture"),
        [
            MenuGroup(
                [
                    MenuItem(t("journal.share"), "send", href=share),
                    *(
                        [MenuItem(t("menu.original"), "image", href=original, new_tab=True)]
                        if original
                        else []
                    ),
                    MenuItem(t("menu.frame"), "crop", href=f"{chapter_url(chapter_id)}/edit"),
                ]
            ),
            MenuGroup(used, label=t("menu.used_in")),
        ],
    )


@router.get("/menu/log/{entry_id}", response_class=HTMLResponse)
async def log_menu(
    identity: IdentityDep,
    page: PageDep,
    registry: RegistryDep,
    entry_id: str,
    reveal: bool = False,
    next: str = "",  # noqa: A002 — the query's name
) -> HTMLResponse:
    """An entry of the log: reveal it to the players (the gamemaster; ``reveal`` when the
    players do not read it in full yet), and narrow the log to its character or its
    fight, as the reader may."""
    t = page.t
    viewer = registry.viewer(identity)
    found = registry.protocol_reader.find(entry_id)
    face = ProtocolReader.face(found, viewer) if found is not None else None
    if found is None or face is None:
        raise HTTPException(status_code=404)
    doing: list[MenuItem] = []
    if viewer.gamemaster and reveal and hidden_from_players(found):
        doing.append(
            MenuItem(
                t("protocol.reveal"),
                "eye",
                post=f"{LOG_URL}/reveal",
                fields={"entry": found.id, "next": safe_next(next) or LOG_URL},
                data={"log-reveal": ""},
            )
        )
    narrowing: list[MenuItem] = []
    for ref, name in ((found.subject, found.subject_name), (found.target, found.target_name)):
        if not ref or not ref.startswith("character:"):
            continue
        character = ref.removeprefix("character:")
        if not (viewer.gamemaster or character in viewer.characters):
            continue
        query = urlencode({"filter": "character", "character": character})
        narrowing.append(
            MenuItem(
                t("menu.log_only", name=name or "?"),
                "filter",
                href=f"{LOG_URL}?{query}",
                data={"log-query": query},
            )
        )
        narrowing.append(
            MenuItem(
                t("menu.open_sheet_of", name=name or "?"),
                "book-open",
                href=f"/characters/{character}",
            )
        )
    if found.scope.combat:
        query = urlencode({"filter": "fight", "combat": found.scope.combat})
        narrowing.append(
            MenuItem(
                t("menu.log_fight"), "swords", href=f"{LOG_URL}?{query}", data={"log-query": query}
            )
        )
    narrowing.append(
        MenuItem(t("menu.log_all"), "list", href=LOG_URL, data={"log-query": "filter=all"})
    )
    who = face.subject_name or face.actor_name or "?"
    return _menu(
        page, who, face.at.astimezone().strftime("%H:%M"), [MenuGroup(doing), MenuGroup(narrowing)]
    )


@router.post("/menu/duplicate")
async def duplicate(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    id: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """A copy of an NPC, place, treasure or encounter, in the same place, opened."""
    if kind not in COPYABLE:
        raise HTTPException(status_code=404)
    collection = getattr(registry.gamestate, COPYABLE[kind])
    record = collection.find(id)
    if record is None:
        raise HTTPException(status_code=404)
    copy = copies.duplicate(collection, record, page.t("menu.copy_suffix"))
    return redirect(PAGES[kind](copy.id))


@router.post("/menu/move")
async def move_up(
    _gm: GamemasterDep,
    registry: RegistryDep,
    kind: Annotated[str, Form()] = "",
    id: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Move an object placed in a chapter up to its adventure, or one placed in an
    adventure up to the world (app/services/holdings.py)."""
    if kind not in COPYABLE:
        raise HTTPException(status_code=404)
    record = getattr(registry.gamestate, COPYABLE[kind]).find(id)
    where: Scope | None = getattr(record, "scope", None) if record is not None else None
    if where is None:
        raise HTTPException(status_code=404)
    try:
        if where.chapter_id:
            registry.holdings.drop_from_chapter(where.chapter_id, kind, id)
        elif where.adventure_id:
            registry.holdings.drop(where.adventure_id, kind, id)
    except HoldingsError as exc:
        raise HTTPException(status_code=409) from exc
    return redirect(safe_next(next) or PAGES[kind](id))


__all__ = ["router"]
