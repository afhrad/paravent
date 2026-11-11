"""NPCs and treasure as pages, cards and the "in this chapter" panel (ADVENTURES.md §4).

* a world's **NPCs** and **treasure**, each with a form to add one wherever it belongs;
* an **NPC** — portrait, roles, where they are placed, notes, the statblock;
* a **treasure** — its items (added through the ⌘K picker, like monsters to an
  encounter), its coins and notes;
* the **cards** ``{@npc}`` / ``{@treasure}`` show alone on a line of a chapter, and the
  panel listing what an adventure or chapter holds, with the line to paste for each.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    code,
    details,
    div,
    form,
    h1,
    h2,
    img,
    label,
    li,
    option,
    p,
    section,
    select,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Coins, Encounter, Location, Npc, Treasure, World
from app.services.adventures import ChapterView
from app.services.npcs import KNOWN_ROLES, TreasureLine
from app.services.references import ReferenceEntry
from app.services.scope import encode
from app.services.search import SearchResult
from app.store.projections import ITEM_KIND_NAMES
from app.ui.auth_pages import message_panel
from app.ui.book_pages import Mention
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.forms import alert, hidden, notice, origin_fields, scope_select, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import (
    WORLDS_URL,
    chapter_url,
    confirm_page,
    crumbs,
    delete_link,
    location_url,
    media_url,
    npc_url,
    saved_version,
    treasure_url,
    world_url,
)
from app.ui.markdown import Rendered
from app.ui.palette import SearchTarget, search_form
from app.ui.ref_pages import statblock
from app.ui.tags import RenderContext, ref_url

DENOMINATIONS = ("pp", "gp", "ep", "sp", "cp")
# The delete button's name per kind of object (the holdings panel's rows).
DELETE_LABEL = {
    "npc": "npcs.delete",
    "place": "places.delete",
    "encounter": "encounters.delete",
    "treasure": "treasure.delete",
}
ROLE_TONE = {"boss": "dmg", "villain": "magic", "ally": "heal", "patron": "accent"}


def world_npcs_url(world_id: str) -> str:
    """A world's NPC list."""
    return f"{world_url(world_id)}/npcs"


def world_treasure_url(world_id: str) -> str:
    """A world's treasure list."""
    return f"{world_url(world_id)}/treasure"


# -- small pieces ---------------------------------------------------------------------


def role_pills(page: Page, roles: Sequence[str]) -> list[Element]:
    """Known roles in the reader's language, a boss in red; free tags as they were typed."""
    return [
        span(class_=f"pill {ROLE_TONE.get(role, 'neutral')}")[
            page.t(f"npcs.role.{role}") if role in KNOWN_ROLES else role
        ]
        for role in roles
    ]


def coins_text(page: Page, coins: Coins) -> str:
    """``"50 gp, 20 sp"`` — largest first, empty denominations left out."""
    parts = [
        f"{page.t.number(getattr(coins, d))} {page.t(f'treasure.coin.{d}')}" for d in DENOMINATIONS
    ]
    return ", ".join(
        part for part, d in zip(parts, DENOMINATIONS, strict=True) if getattr(coins, d)
    )


def _portrait(npc: Npc, size: str = "thumb") -> Node:
    if not npc.portrait:
        return span(class_=f"avatar {'avatar-lg' if size == 'large' else ''}", aria_hidden="true")[
            icon("circle-user")
        ]
    return img(
        class_="portrait" if size == "large" else "thumb",
        src=media_url(npc.scope.record_id, npc.portrait),
        alt="",
    )


def _place_text(names: Sequence[str]) -> str:
    return " › ".join(names)


def _snippet(kind: str, name: str) -> Element:
    return code(class_="image-snippet")[f"{{@{kind} {name}}}"]


# -- cards for ::: npc / ::: treasure ----------------------------------------------------


def npc_card(page: Page, npc: Npc, creature: ReferenceEntry | None, ctx: RenderContext) -> Element:
    """What ``::: npc Name`` shows: who, their roles, and their statblock folded."""
    t = page.t
    return div(class_="npc-embed")[
        div(class_="md-embed-head npc-embed-head")[
            _portrait(npc),
            div(class_="npc-embed-main")[
                span(class_="md-embed-kind")[t("md.embed.npc")],
                a(href=npc_url(npc.id), class_="npc-embed-name")[npc.name],
                span(class_="npc-roles")[role_pills(page, npc.roles)],
            ],
        ],
        details(class_="md-statblock")[
            summary(class_="md-embed-head")[
                span(class_="md-embed-kind")[t("md.embed.statblock")], " ", creature.name
            ],
            statblock(page, creature, ctx),
        ]
        if creature is not None
        else None,
    ]


def _item_line(page: Page, line: TreasureLine, world_id: str) -> Node:
    item, entry = line.item, line.entry
    name: Node = (
        a(href=ref_url(entry.category, item.name, item.source, world_id), class_="ref item")[
            item.name
        ]
        if entry is not None
        else span(class_="ref missing")[item.name]
    )
    return [f"{item.count}× " if item.count > 1 else None, name]


def treasure_card(
    page: Page, treasure: Treasure, lines: Sequence[TreasureLine], world_id: str
) -> Element:
    """What ``::: treasure Name`` shows: the items and the coins."""
    t = page.t
    coins = coins_text(page, treasure.coins)
    return div(class_="md-embed-head treasure-embed")[
        span(class_="md-embed-kind")[t("md.embed.treasure")],
        a(href=treasure_url(treasure.id), class_="enc-embed-name")[treasure.name],
        ul(class_="treasure-embed-items")[
            [li[_item_line(page, line, world_id)] for line in lines],
            li(class_="treasure-coins")[icon("coins"), " ", coins] if coins else None,
        ]
        if lines or coins
        else span(class_="enc-embed-monsters")[t("treasure.empty")],
    ]


# -- what an adventure or chapter holds ------------------------------------------------------


def holdings_panel(
    page: Page,
    *,
    world: World,
    place: str,
    npcs: Sequence[Npc],
    treasures: Sequence[Treasure],
    encounters: Sequence[Encounter],
    heading: str,
    open_: bool = False,
    places: Sequence[Location] = (),
) -> Element:
    """The NPCs, places, treasure and encounters placed here, each with the line to
    paste."""
    t = page.t

    def column(
        kind: str, title: str, rows: list[tuple[str, str]], action: str, extra: Node = None
    ) -> Element:
        return div(class_="holdings-col")[
            h2(class_="section-title")[title],
            ul(class_="holdings-list")[
                [
                    li[
                        div(class_="holdings-row")[
                            a(href=url)[name],
                            delete_link(page, f"{url}/delete", t(DELETE_LABEL[kind])),
                        ],
                        _snippet(kind, name),
                    ]
                    for name, url in rows
                ]
            ]
            if rows
            else p(class_="field-hint")[t("holdings.none")],
            form(method="post", action=action, class_="add-row add-row-sm")[
                hidden("place", place),
                extra,
                label(for_=f"f-add-{kind}-{place}", class_="sr-only")[t("holdings.name")],
                input_el(
                    id=f"f-add-{kind}-{place}",
                    type="text",
                    name="name",
                    required=True,
                    placeholder=t(f"holdings.new.{kind}"),
                    autocomplete="off",
                ),
                button(type="submit", class_="btn btn-sm", aria_label=t(f"holdings.new.{kind}"))[
                    icon("plus")
                ],
            ],
        ]

    return details(class_="panel holdings", open=open_)[
        summary(class_="panel-head")[
            b[heading],
            span(class_="list-meta")[
                " · ".join(
                    t.plural(f"count.{key}", n)
                    for key, n in (
                        ("npcs", len(npcs)),
                        ("places", len(places)),
                        ("encounters", len(encounters)),
                        ("treasure", len(treasures)),
                    )
                )
            ],
        ],
        div(class_="holdings-grid")[
            column(
                "npc",
                t("npcs.heading"),
                [(n.name, npc_url(n.id)) for n in npcs],
                world_npcs_url(world.id),
            ),
            column(
                "place",
                t("places.heading"),
                [(x.name, location_url(x.id)) for x in places],
                f"{world_url(world.id)}/places",
            ),
            column(
                "encounter",
                t("nav.encounters"),
                [(e.name, encounter_url(e.id)) for e in encounters],
                "/gm/encounters",
                hidden("world", world.id),
            ),
            column(
                "treasure",
                t("treasure.heading"),
                [(x.name, treasure_url(x.id)) for x in treasures],
                world_treasure_url(world.id),
            ),
        ],
    ]


def creatures_panel(
    page: Page,
    view: ChapterView,
    mentions: Sequence[Mention],
    encounters: Sequence[Encounter],
) -> Node:
    """A book chapter's creatures, each one click from an encounter of this chapter."""
    t = page.t
    if not mentions:
        return None
    url = f"{chapter_url(view.chapter.id)}/creatures"
    choices = [*((e.id, e.name) for e in encounters), ("", t("book.new_encounter"))]
    return details(class_="panel holdings", id="creatures")[
        summary(class_="panel-head")[
            b[t("book.creatures")],
            span(class_="list-meta")[t("book.creatures_hint")],
        ],
        ul(class_="creature-list")[
            [
                li(class_="creature-row")[
                    a(
                        href=ref_url("monster", m.name, m.source, view.world.id),
                        class_="ref creature",
                    )[m.name],
                    span(class_="res-src mono")[m.source],
                    span(class_="list-meta")[t("book.mentions", n=m.count)],
                    form(method="post", action=url, class_="creature-add")[
                        hidden("ref", f"{m.name}|{m.source}"),
                        label(class_="sr-only", for_=f"f-enc-{index}")[t("nav.encounters")],
                        select(id=f"f-enc-{index}", name="encounter")[
                            [option(value=value)[text] for value, text in choices]
                        ],
                        button(
                            type="submit",
                            class_="btn btn-sm",
                            aria_label=t("book.add_creature", name=m.name),
                        )[icon("plus")],
                    ],
                ]
                for index, m in enumerate(mentions)
            ]
        ],
    ]


# -- lists ---------------------------------------------------------------------------


def _create_form(
    page: Page,
    action: str,
    scopes: Sequence[tuple[str, str, int]],
    selected: str,
    placeholder: str,
    submit: str,
    generate: str | None = None,
) -> Element:
    """Name and place a new object; ``generate`` adds a button that rolls one instead."""
    t = page.t
    return form(method="post", action=action, class_="panel stack-form create-form")[
        text_field("name", t("adventures.name"), value="", autocomplete="off"),
        scope_select("place", t("scope.label"), scopes, selected=selected, hint=t("scope.hint")),
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[icon("plus"), span[submit]],
            button(
                type="submit",
                formaction=f"{action}/generate",
                formnovalidate=True,
                class_="btn",
            )[icon("dices", "sm"), span[generate]]
            if generate
            else None,
        ],
    ]


def npcs_page(
    page: Page,
    world: World,
    rows: Sequence[tuple[Npc, Sequence[str]]],
    scopes: Sequence[tuple[str, str, int]],
    *,
    selected: str,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """A world's NPCs, bosses first, with where each is placed."""
    t = page.t
    ordered = sorted(rows, key=lambda r: ("boss" not in r[0].roles, r[0].name.casefold()))
    items = [
        li(class_="list-row")[
            _portrait(npc),
            div(class_="list-main")[
                a(href=npc_url(npc.id), class_="list-title list-link")[npc.name],
                span(class_="list-meta")[role_pills(page, npc.roles), " ", _place_text(names)],
            ],
            delete_link(page, f"{npc_url(npc.id)}/delete", t("npcs.delete")),
        ]
        for npc, names in ordered
    ]
    return app_shell(
        page,
        t("npcs.page_title", world=world.name),
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("npcs.heading"), None),
            ),
            page_head(t("adventures.world_eyebrow"), t("npcs.heading"), t("npcs.lede")),
            notice(notice_text),
            alert(error),
            ul(class_="list")[items]
            if items
            else empty_state("circle-user", t("npcs.empty_heading"), t("npcs.empty_text")),
            _create_form(
                page,
                world_npcs_url(world.id),
                scopes,
                selected,
                t("npcs.new"),
                t("npcs.new"),
                generate=t("npcgen.new"),
            ),
        ],
        world_id=world.id,
    )


def treasures_page(
    page: Page,
    world: World,
    rows: Sequence[tuple[Treasure, Sequence[str]]],
    scopes: Sequence[tuple[str, str, int]],
    *,
    selected: str,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """A world's treasure, with where each is placed."""
    t = page.t
    items = [
        li(class_="list-row")[
            span(class_="avatar", aria_hidden="true")[icon("gem")],
            div(class_="list-main")[
                a(href=treasure_url(x.id), class_="list-title list-link")[x.name],
                span(class_="list-meta")[
                    " · ".join(
                        part
                        for part in (
                            t.plural("treasure.items_n", sum(i.count for i in x.items))
                            if x.items
                            else "",
                            coins_text(page, x.coins),
                            _place_text(names),
                        )
                        if part
                    )
                ],
            ],
            delete_link(page, f"{treasure_url(x.id)}/delete", t("treasure.delete")),
        ]
        for x, names in rows
    ]
    return app_shell(
        page,
        t("treasure.page_title", world=world.name),
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("treasure.heading"), None),
            ),
            page_head(t("adventures.world_eyebrow"), t("treasure.heading"), t("treasure.lede")),
            notice(notice_text),
            alert(error),
            ul(class_="list")[items]
            if items
            else empty_state("gem", t("treasure.empty_heading"), t("treasure.empty_text")),
            _create_form(
                page,
                world_treasure_url(world.id),
                scopes,
                selected,
                t("treasure.new"),
                t("treasure.new"),
            ),
        ],
        world_id=world.id,
    )


# -- an NPC -----------------------------------------------------------------------------


def npc_page(
    page: Page,
    npc: Npc,
    world: World,
    place: Sequence[str],
    notes: Rendered,
    creature: ReferenceEntry | None,
    ctx: RenderContext,
    *,
    notice_text: str | None = None,
) -> Element:
    """One NPC: portrait, roles, where they are, notes and statblock."""
    t = page.t
    return app_shell(
        page,
        npc.name,
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("npcs.heading"), world_npcs_url(world.id)),
                (npc.name, None),
            ),
            notice(notice_text),
            div(class_="npc-head")[
                _portrait(npc, "large"),
                div(class_="npc-head-main")[
                    span(class_="eyebrow")[_place_text(place)],
                    h1[npc.name],
                    div(class_="npc-roles")[role_pills(page, npc.roles)],
                    p(class_="field-hint")[t("holdings.paste"), " ", _snippet("npc", npc.name)],
                ],
                div(class_="npc-head-actions")[
                    a(href=f"{npc_url(npc.id)}/edit", class_="btn")[
                        icon("pencil"), span[t("players.edit")]
                    ],
                    a(href=f"{npc_url(npc.id)}/generate", class_="btn btn-ghost")[
                        icon("dices", "sm"), span[t("npcgen.open")]
                    ],
                    delete_link(page, f"{npc_url(npc.id)}/delete", t("npcs.delete"), text=True),
                ],
            ],
            div(class_="md-body")[notes.html] if notes.html.strip() else None,
            statblock(page, creature, ctx) if creature is not None else None,
            p(class_="ref-roll-result", aria_live="polite", hidden=True),
        ],
        extra_scripts=("/static/js/roll.js",),
        world_id=world.id,
    )


def npc_form_page(
    page: Page,
    npc: Npc,
    world: World,
    scopes: Sequence[tuple[str, str, int]],
    *,
    name: str,
    place: str,
    roles: Sequence[str],
    statblock_ref: str,
    notes: str,
    race: str = "",
    culture: str = "",
    races: Sequence[str] = (),
    cultures: Sequence[str] = (),
    base: str = "",
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
) -> Element:
    """Edit an NPC: name, what names them, place, roles, statblock, notes and portrait."""
    t = page.t
    url = npc_url(npc.id)
    free = ", ".join(r for r in roles if r not in KNOWN_ROLES)
    return app_shell(
        page,
        t("adventures.edit_title", name=npc.name),
        div(class_="formpage formpage-wide")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[npc.name]],
            notice(notice_text),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("npcs.eyebrow")],
                    b[t("adventures.edit_title", name=npc.name)],
                ],
                alert(error),
                form(method="post", action=f"{url}/edit", class_="stack-form")[
                    text_field("name", t("adventures.name"), value=name, autocomplete="off"),
                    origin_fields(page, race, culture, races, cultures),
                    p(class_="field-hint")[t("naming.npc_hint")],
                    scope_select(
                        "place", t("scope.label"), scopes, selected=place, hint=t("scope.hint")
                    ),
                    div(class_="field")[
                        span(class_="field-label")[t("npcs.roles")],
                        div(class_="role-choices")[
                            [
                                label(class_="check-inline")[
                                    input_el(
                                        type="checkbox",
                                        name="roles",
                                        value=role,
                                        checked=role in roles,
                                    ),
                                    t(f"npcs.role.{role}"),
                                ]
                                for role in KNOWN_ROLES
                            ]
                        ],
                    ],
                    text_field(
                        "roles",
                        t("npcs.tags"),
                        value=free,
                        autocomplete="off",
                        required=False,
                        optional_text=t("common.optional"),
                        hint=t("npcs.tags_hint"),
                    ),
                    text_field(
                        "statblock",
                        t("npcs.statblock"),
                        value=statblock_ref,
                        autocomplete="off",
                        required=False,
                        optional_text=t("common.optional"),
                        hint=t("npcs.statblock_hint"),
                    ),
                    div(class_="field")[
                        label(for_="f-notes")[t("npcs.notes")],
                        textarea(
                            id="f-notes",
                            name="notes",
                            rows="10",
                            class_="prose-editor",
                            data_scope=encode(npc.scope),
                        )[notes],
                        hidden("base", base),
                    ],
                    saved_version(page, conflict),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"), span[t("worlds.save")]
                        ],
                        a(href=url, class_="btn btn-ghost")[t("common.cancel")],
                        a(href=f"{url}/generate", class_="btn btn-ghost")[
                            icon("dices", "sm"), span[t("npcgen.open")]
                        ],
                    ],
                ],
            ],
            section(class_="panel images-panel")[
                div(class_="panel-head")[b[t("npcs.portrait")]],
                div(class_="image-row")[
                    _portrait(npc),
                    form(
                        method="post",
                        action=f"{url}/portrait",
                        enctype="multipart/form-data",
                        class_="upload-form",
                    )[
                        label(for_="f-portrait", class_="sr-only")[t("adventures.image_file")],
                        input_el(
                            id="f-portrait",
                            type="file",
                            name="file",
                            accept="image/png,image/jpeg,image/webp,image/gif,image/avif",
                            required=True,
                        ),
                        button(type="submit", class_="btn")[
                            icon("upload"), span[t("adventures.upload")]
                        ],
                    ],
                    form(method="post", action=f"{url}/portrait/clear")[
                        button(type="submit", class_="btn btn-sm btn-ghost")[
                            t("npcs.clear_portrait")
                        ]
                    ]
                    if npc.portrait
                    else None,
                ],
            ],
        ],
        world_id=world.id,
        insert_layer=True,
    )


def npc_delete_page(page: Page, npc: Npc) -> Element:
    """Confirm deleting an NPC."""
    t = page.t
    url = npc_url(npc.id)
    return confirm_page(
        page,
        title=t("npcs.delete"),
        heading=t("chapters.delete_heading", name=npc.name),
        lede=t("npcs.delete_lede"),
        action=f"{url}/delete",
        back=url,
        submit=t("npcs.delete"),
    )


# -- a treasure ----------------------------------------------------------------------------


def _treasure_line(page: Page, treasure: Treasure, line: TreasureLine, world_id: str) -> Element:
    t = page.t
    item, entry = line.item, line.entry
    url = f"{treasure_url(treasure.id)}/items/{line.index}"
    meta = []
    if entry is None:
        meta.append(t("treasure.missing"))
    else:
        if entry.rarity and entry.rarity not in ("none", "unknown"):
            meta.append(
                t(f"treasure.rarity.{entry.rarity}") if entry.rarity in _RARITIES else entry.rarity
            )
        kind = ITEM_KIND_NAMES.get(entry.kind)
        if kind:
            meta.append(kind)
        if entry.attunement:
            meta.append(t("treasure.attunement"))
    return li(class_="enc-line")[
        span(class_="res-ico magic", aria_hidden="true")[icon("gem")],
        div(class_="enc-line-main")[
            b[
                a(href=ref_url(entry.category, item.name, item.source, world_id), class_="ref")[
                    item.name
                ]
                if entry is not None
                else span(class_="ref missing")[item.name],
                " ",
                span(class_="res-src mono")[item.source],
            ],
            span(class_="list-meta")[" · ".join(meta)],
        ],
        form(method="post", action=url, class_="enc-count")[
            button(
                type="submit",
                name="count",
                value=str(item.count - 1),
                class_="btn btn-sm",
                aria_label=t("encounters.fewer", name=item.name),
            )[icon("x") if item.count == 1 else "−"],
            span(class_="enc-count-n mono", aria_live="polite")[str(item.count)],
            button(
                type="submit",
                name="count",
                value=str(item.count + 1),
                class_="btn btn-sm",
                aria_label=t("encounters.more", name=item.name),
                disabled=item.count >= 999,
            )["+"],
        ],
    ]


_RARITIES = frozenset(
    {"common", "uncommon", "rare", "very rare", "legendary", "artifact", "varies"}
)


def _coin_field(name: str, caption: str, value: int) -> Element:
    return div(class_="field field-narrow")[
        label(for_=f"f-{name}")[caption],
        input_el(
            id=f"f-{name}",
            name=name,
            type="number",
            min="0",
            inputmode="numeric",
            value=str(value),
        ),
    ]


@dataclass(frozen=True)
class RollForm:
    """The roll form's choices: what to roll, how hard, on which book's tables."""

    kind: str  # "individual" or "hoard"
    cr: int
    book: str  # "DMG" or "XDMG"
    books: tuple[str, ...]


def _roll_panel(page: Page, url: str, roll: RollForm) -> Element:
    """Roll individual treasure or a hoard on the Dungeon Master's Guide tables."""
    t = page.t
    return section(class_="panel tr-roll", aria_labelledby="roll-h")[
        div(class_="panel-head")[
            h2(id="roll-h")[icon("dices", "sm"), " ", t("treasure.roll")],
            p(class_="muted")[t("treasure.roll_hint")],
        ],
        form(method="post", action=f"{url}/roll", class_="stack-form")[
            div(class_="field tr-kinds", role="radiogroup", aria_label=t("treasure.roll_kind"))[
                [
                    label(class_="tr-kind")[
                        input_el(type="radio", name="kind", value=kind, checked=roll.kind == kind),
                        span[t(f"treasure.roll_{kind}")],
                    ]
                    for kind in ("individual", "hoard")
                ]
            ],
            div(class_="field-row")[
                div(class_="field")[
                    label(for_="f-roll-cr")[t("treasure.roll_cr")],
                    input_el(
                        type="number",
                        id="f-roll-cr",
                        name="cr",
                        min="0",
                        max="30",
                        value=str(roll.cr),
                        inputmode="numeric",
                    ),
                ],
                div(class_="field")[
                    label(for_="f-roll-book")[t("treasure.roll_tables")],
                    select(id="f-roll-book", name="book")[
                        [
                            option(value=book, selected=book == roll.book)[
                                t(f"treasure.roll_book.{book}")
                            ]
                            for book in roll.books
                        ]
                    ],
                ],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("dices", "sm"), span[t("treasure.roll_go")]
                ]
            ],
        ],
    ]


def treasure_page(
    page: Page,
    treasure: Treasure,
    world: World,
    place: Sequence[str],
    lines: Sequence[TreasureLine],
    result: SearchResult,
    scopes: Sequence[tuple[str, str, int]],
    notes: Rendered,
    *,
    notes_text: str | None = None,
    base: str = "",
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
    roll: RollForm | None = None,
) -> Element:
    """A treasure: its items with the picker to add more, its coins, notes and settings.

    ``notes_text`` is the notes as typed when a save was refused; ``base`` the fingerprint
    of the notes the form opened, ``conflict`` the notes saved meanwhile."""
    t = page.t
    url = treasure_url(treasure.id)
    target = SearchTarget(
        action=url,
        dom_id="picker-results",
        pick_action=f"{url}/items",
        pick=treasure.id,
        pick_hint="treasure.hint_pick",
        swap="#plan",
    )
    coins = treasure.coins
    return app_shell(
        page,
        treasure.name,
        div(class_="enc-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("treasure.heading"), world_treasure_url(world.id)),
                (treasure.name, None),
            ),
            div(class_="enc-head")[
                span(class_="eyebrow")[_place_text(place)],
                div(class_="enc-title")[
                    h1[treasure.name],
                    delete_link(
                        page, f"{treasure_url(treasure.id)}/delete", t("treasure.delete"), text=True
                    ),
                ],
                p(class_="field-hint")[
                    t("holdings.paste"), " ", _snippet("treasure", treasure.name)
                ],
            ],
            alert(error),
            notice(notice_text),
            div(class_="enc-grid")[
                div(class_="enc-main")[
                    section(class_="panel enc-plan", id="plan", aria_labelledby="plan-h")[
                        div(class_="panel-head")[h2(id="plan-h")[t("treasure.items")]],
                        ul(class_="enc-lines")[
                            [_treasure_line(page, treasure, line, world.id) for line in lines]
                        ]
                        if lines
                        else p(class_="enc-empty")[t("treasure.none_yet")],
                        div(class_="enc-total")[
                            span[t("treasure.coins")], b(class_="mono")[coins_text(page, coins)]
                        ]
                        if not coins.empty
                        else None,
                    ],
                    section(class_="panel enc-picker", id="picker", aria_labelledby="picker-h")[
                        div(class_="panel-head")[h2(id="picker-h")[t("treasure.add_heading")]],
                        search_form(
                            page,
                            result,
                            world.id,
                            in_dialog=False,
                            target=target,
                            autofocus=bool(result.query),
                        ),
                    ],
                    div(class_="md-body")[notes.html] if notes.html.strip() else None,
                ],
                div(class_="enc-side")[
                    _roll_panel(page, url, roll) if roll is not None else None,
                    details(class_="panel enc-settings", open=True)[
                        summary(class_="panel-head")[icon("pencil"), b[t("encounters.settings")]],
                        form(method="post", action=url, class_="stack-form")[
                            text_field(
                                "name",
                                t("adventures.name"),
                                value=treasure.name,
                                autocomplete="off",
                            ),
                            scope_select(
                                "place",
                                t("scope.label"),
                                scopes,
                                selected=encode(treasure.scope),
                            ),
                            div(class_="field-row coin-row")[
                                [
                                    _coin_field(d, t(f"treasure.coin.{d}"), getattr(coins, d))
                                    for d in DENOMINATIONS
                                ]
                            ],
                            div(class_="field")[
                                label(for_="f-notes")[t("npcs.notes")],
                                textarea(
                                    id="f-notes",
                                    name="notes",
                                    rows="5",
                                    class_="prose-editor",
                                    data_scope=encode(treasure.scope),
                                )[treasure.notes if notes_text is None else notes_text],
                                hidden("base", base),
                            ],
                            saved_version(page, conflict),
                            div(class_="form-actions")[
                                button(type="submit", class_="btn btn-primary")[
                                    icon("check"), span[t("worlds.save")]
                                ],
                            ],
                        ],
                    ],
                ],
            ],
        ],
        world_id=world.id,
        insert_layer=True,
    )


def treasure_delete_page(page: Page, treasure: Treasure) -> Element:
    """Confirm deleting a treasure."""
    t = page.t
    url = treasure_url(treasure.id)
    return confirm_page(
        page,
        title=t("treasure.delete"),
        heading=t("chapters.delete_heading", name=treasure.name),
        lede=t("treasure.delete_lede"),
        action=f"{url}/delete",
        back=url,
        submit=t("treasure.delete"),
    )


def object_not_found_page(page: Page) -> Element:
    """The NPC or treasure does not exist (any more)."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "circle-user",
            t("adventures.not_found"),
            t("npcs.not_found_lede"),
            a(href=WORLDS_URL, class_="btn btn-primary")[
                icon("arrow-left"), span[t("worlds.back")]
            ],
        ),
    )


__all__ = [
    "coins_text",
    "creatures_panel",
    "holdings_panel",
    "npc_card",
    "npc_delete_page",
    "npc_form_page",
    "npc_page",
    "npcs_page",
    "object_not_found_page",
    "role_pills",
    "treasure_card",
    "treasure_delete_page",
    "treasure_page",
    "treasures_page",
    "world_npcs_url",
    "world_treasure_url",
]
