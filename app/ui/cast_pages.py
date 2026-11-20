"""NPCs and treasure as pages, cards and the "in this chapter" panel (ADVENTURES.md §4).

* a world's **NPCs** and **treasure**, each with a form to add one wherever it belongs;
* an **NPC** — portrait, roles, where they are placed, notes, the statblock;
* a **treasure** — its items (added through the ⌘K picker, like monsters to an
  encounter), its coins and notes;
* the **cards** ``{@npc}`` / ``{@treasure}`` show alone on a line of a chapter, and the
  panel listing what an adventure or chapter holds, with the line to paste for each.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    datalist,
    details,
    div,
    fieldset,
    form,
    h1,
    h2,
    h3,
    label,
    legend,
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

from app.models.gamestate import Coins, Encounter, Location, Npc, Treasure, TreasureItem, World
from app.services.adventures import ChapterView
from app.services.holdings import Held
from app.services.naming import StyleEntry
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
    NPCS_URL,
    WORLDS_URL,
    chapter_url,
    confirm_page,
    copy_snippet,
    crumbs,
    delete_link,
    location_url,
    new_button,
    new_dialog,
    npc_url,
    saved_version,
    treasure_url,
    world_url,
)
from app.ui.markdown import Rendered
from app.ui.name_roller import name_roller
from app.ui.palette import SearchTarget, search_form
from app.ui.ref_pages import statblock
from app.ui.tags import RenderContext, ref_url
from app.ui.tokens import token, token_panel
from app.ui.tool_dialog import tool_button, tool_dialog

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


def _portrait(page: Page, npc: Npc, size: str = "thumb") -> Node:
    """The portrait as its round token: one picture, one look (documentation/TOKENS.md)."""
    tok = page.token_of("npc", npc.scope.world_id, npc.id)
    return token(tok, "xxl" if size == "large" else "lg", title=npc.name)


def _place_text(names: Sequence[str]) -> str:
    return " › ".join(names)


def _snippet(page: Page, kind: str, name: str) -> Element:
    return copy_snippet(page, f"{{@{kind} {name}}}")


# -- cards for ::: npc / ::: treasure ----------------------------------------------------


def npc_card(page: Page, npc: Npc, creature: ReferenceEntry | None, ctx: RenderContext) -> Element:
    """What ``::: npc Name`` shows: who, their roles, and their statblock folded."""
    t = page.t
    return div(class_="npc-embed")[
        div(class_="md-embed-head npc-embed-head")[
            _portrait(page, npc),
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


# Each kind's page, for an object the adventure uses.
URLS = {
    "npc": npc_url,
    "place": location_url,
    "encounter": encounter_url,
    "treasure": treasure_url,
}


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
    adventure_id: str | None = None,
    used: Mapping[str, Sequence[Held]] | None = None,
    ready: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    chapter_id: str | None = None,
) -> Element:
    """The NPCs, places, treasure and encounters placed here, each with the line to
    paste. For an adventure (``adventure_id``) also the world's ready-made ones it
    ``used``, marked, and a list of the ``ready`` ones to add; there a row's button takes
    the object out of the adventure without deleting it (app/services/holdings.py); for
    a chapter (``chapter_id``) out of the chapter, up to its adventure."""
    t = page.t
    holdings_url = (
        f"/gm/adventures/{adventure_id}/holdings"
        if adventure_id
        else f"/gm/chapters/{chapter_id}/holdings"
        if chapter_id
        else ""
    )
    drop_label = t("holdings.drop_chapter") if chapter_id else t("holdings.drop")

    def out_of_here(kind: str, object_id: str) -> Element:
        return form(method="post", action=holdings_url, class_="inline-form")[
            hidden("kind", kind),
            hidden("object", object_id),
            hidden("op", "drop"),
            button(
                type="submit",
                class_="btn btn-sm btn-ghost",
                title=drop_label,
                aria_label=drop_label,
            )[icon("x", "sm")],
        ]

    def column(
        kind: str,
        title: str,
        rows: list[tuple[str, str, str]],
        action: str,
        extra: Node = None,
    ) -> Element:
        mine = [(name, url, ident, False) for name, url, ident in rows]
        theirs = [(h.name, URLS[kind](h.id), h.id, True) for h in (used or {}).get(kind, ())]
        offered = (ready or {}).get(kind, ())
        return div(class_="holdings-col")[
            h2(class_="section-title")[title],
            ul(class_="holdings-list")[
                [
                    li[
                        div(class_="holdings-row")[
                            a(href=url)[name],
                            span(class_="pill neutral", title=t("holdings.from_world_hint"))[
                                t("holdings.from_world")
                            ]
                            if from_world
                            else None,
                            out_of_here(kind, ident)
                            if holdings_url
                            else delete_link(page, f"{url}/delete", t(DELETE_LABEL[kind])),
                        ],
                        _snippet(page, kind, name),
                    ]
                    for name, url, ident, from_world in [*mine, *theirs]
                ]
            ]
            if mine or theirs
            else p(class_="field-hint")[t("holdings.none")],
            form(method="post", action=holdings_url, class_="add-row add-row-sm")[
                hidden("kind", kind),
                hidden("op", "use"),
                label(for_=f"f-use-{kind}", class_="sr-only")[t(f"holdings.ready.{kind}")],
                select(id=f"f-use-{kind}", name="object", required=True)[
                    option(value="")[t(f"holdings.ready.{kind}")],
                    [option(value=ident)[name] for ident, name in offered],
                ],
                button(type="submit", class_="btn btn-sm", aria_label=t("holdings.use"))[
                    icon("plus")
                ],
            ]
            if adventure_id and offered
            else None,
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

    return details(class_="panel holdings", id="holdings", open=open_)[
        summary(class_="panel-head")[
            b[heading],
            span(class_="list-meta")[
                " · ".join(
                    t.plural(f"count.{key}", n)
                    for key, n in (
                        ("npcs", len(npcs) + len((used or {}).get("npc", ()))),
                        ("places", len(places) + len((used or {}).get("place", ()))),
                        ("encounters", len(encounters) + len((used or {}).get("encounter", ()))),
                        ("treasure", len(treasures) + len((used or {}).get("treasure", ()))),
                    )
                )
            ],
        ],
        div(class_="holdings-grid")[
            column(
                "npc",
                t("npcs.heading"),
                [(n.name, npc_url(n.id), n.id) for n in npcs],
                world_npcs_url(world.id),
            ),
            column(
                "place",
                t("places.heading"),
                [(x.name, location_url(x.id), x.id) for x in places],
                f"{world_url(world.id)}/places",
            ),
            column(
                "encounter",
                t("nav.encounters"),
                [(e.name, encounter_url(e.id), e.id) for e in encounters],
                "/gm/encounters",
                hidden("world", world.id),
            ),
            column(
                "treasure",
                t("treasure.heading"),
                [(x.name, treasure_url(x.id), x.id) for x in treasures],
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


def _create_dialog(
    page: Page,
    dialog_id: str,
    action: str,
    world_id: str,
    *,
    title: str,
    submit: str,
    error: str | None,
    is_open: bool,
) -> Element:
    """A new treasure of the table's world in one dialog, as a new place: its name and a
    first note (moved into an adventure or chapter on its page if wanted)."""
    t = page.t
    return new_dialog(
        page,
        dialog_id,
        title=title,
        action=action,
        back=action,
        submit=submit,
        is_open=is_open,
        fields=[
            alert(error),
            hidden("place", f"w:{world_id}"),
            text_field("name", t("adventures.name"), value="", autocomplete="off"),
            div(class_="field")[
                label(for_=f"{dialog_id}-notes")[
                    t("npcs.notes"), " ", span(class_="opt")[t("common.optional")]
                ],
                textarea(id=f"{dialog_id}-notes", name="notes", rows="4"),
            ],
        ],
    )


NEW_NPC_NAME_URL = f"{NPCS_URL}/new/name"


def new_npc_name_field(
    page: Page, name: str, styles: Sequence[StyleEntry], style: str = ""
) -> Element:
    """A new NPC's name with dice for one, rolled in the style of the people or creature
    written above it (or the style chosen), which a roll swaps in place."""
    return span(class_="name-field")[
        input_el(
            id="f-new-npc-name",
            name="name",
            type="text",
            value=name,
            required=True,
            autocomplete="off",
        ),
        name_roller(page, NEW_NPC_NAME_URL, styles, field_id="f-new-npc-name", selected=style),
    ]


def _new_npc_dialog(
    page: Page,
    world: World,
    *,
    races: Sequence[str],
    styles: Sequence[StyleEntry],
    error: str | None,
    is_open: bool,
) -> Element:
    """A new NPC in the table's world (documentation/CURRENT-WORLD.md): first the people or
    creature it is, which its name is rolled for, then the name with its dice, a first
    note, or a whole NPC rolled instead."""
    t = page.t
    action = world_npcs_url(world.id)
    return new_dialog(
        page,
        "new-npc",
        title=t("npcs.new"),
        action=action,
        back=action,
        submit=t("npcs.create"),
        is_open=is_open,
        fields=[
            alert(error),
            hidden("place", f"w:{world.id}"),
            div(class_="field")[
                label(for_="f-new-npc-race")[t("npcs.race")],
                input_el(
                    id="f-new-npc-race",
                    name="race",
                    type="text",
                    list="known-new-npc-race",
                    autocomplete="off",
                    aria_describedby="f-new-npc-race-hint",
                ),
                datalist(id="known-new-npc-race")[[option(value=r) for r in races]],
                p(class_="field-hint", id="f-new-npc-race-hint")[t("npcs.race_hint")],
                label(class_="check-inline")[
                    input_el(type="checkbox", name="statblock_from", value="1"),
                    t("npcs.statblock_from"),
                ],
            ],
            div(class_="field")[
                label(for_="f-new-npc-name")[t("adventures.name")],
                new_npc_name_field(page, "", styles),
            ],
            div(class_="field")[
                label(for_="new-npc-notes")[
                    t("npcs.notes"), " ", span(class_="opt")[t("common.optional")]
                ],
                textarea(id="new-npc-notes", name="notes", rows="4"),
            ],
        ],
        more=button(
            type="submit",
            formaction=f"{action}/generate",
            formnovalidate=True,
            class_="btn",
        )[icon("dices", "sm"), span[t("npcgen.new")]],
    )


def npcs_page(
    page: Page,
    world: World,
    rows: Sequence[tuple[Npc, Sequence[str]]],
    scopes: Sequence[tuple[str, str, int]],
    *,
    selected: str,
    notice_text: str | None = None,
    error: str | None = None,
    new: bool = False,
    races: Sequence[str] = (),
    styles: Sequence[StyleEntry] = (),
) -> Element:
    """A world's NPCs, bosses first, with where each is placed, and **New NPC**, which
    opens a dialog for one (open from the start with ``new``, or after a refused one);
    ``races`` are the peoples the naming styles know, ``styles`` what its dice may roll."""
    t = page.t
    ordered = sorted(rows, key=lambda r: ("boss" not in r[0].roles, r[0].name.casefold()))
    items = [
        li(class_="list-row")[
            # The token is made from the portrait: one picture, shown once.
            token(page.token_of("npc", npc.scope.world_id, npc.id)),
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
            page_head(
                t("adventures.world_eyebrow"),
                t("npcs.heading"),
                t("npcs.lede"),
                new_button(page, "new-npc", t("npcs.new")),
            ),
            notice(notice_text),
            alert(error) if not new else None,
            ul(class_="list")[items]
            if items
            else empty_state("circle-user", t("npcs.empty_heading"), t("npcs.empty_text")),
            _new_npc_dialog(
                page,
                world,
                races=races,
                styles=styles,
                error=error if new else None,
                is_open=new,
            ),
        ],
        world_id=world.id,
        extra_scripts=("/static/js/htmx.min.js",),
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
    new: bool = False,
) -> Element:
    """A world's treasure, with where each is placed, and **New treasure**, which opens
    a dialog for one (open from the start with ``new``, or after a refused one)."""
    t = page.t
    items = [
        li(class_="list-row")[
            token(page.token_of("treasure", x.scope.world_id, x.id)),
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
            page_head(
                t("adventures.world_eyebrow"),
                t("treasure.heading"),
                t("treasure.lede"),
                new_button(page, "new-treasure", t("treasure.new")),
            ),
            notice(notice_text),
            alert(error) if not new else None,
            ul(class_="list")[items]
            if items
            else empty_state("gem", t("treasure.empty_heading"), t("treasure.empty_text")),
            _create_dialog(
                page,
                "new-treasure",
                world_treasure_url(world.id),
                world.id,
                title=t("treasure.new"),
                submit=t("treasure.create"),
                error=error if new else None,
                is_open=new,
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
                _portrait(page, npc, "large"),
                div(class_="npc-head-main")[
                    span(class_="eyebrow")[_place_text(place)],
                    h1[npc.name],
                    div(class_="npc-roles")[role_pills(page, npc.roles)],
                    p(class_="field-hint")[
                        t("holdings.paste"), " ", _snippet(page, "npc", npc.name)
                    ],
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
    result: SearchResult | None = None,
) -> Element:
    """Edit an NPC: name, what names them, place, roles, statblock, notes and portrait;
    ``result`` is the creature picker's first view (the world's books), beside the
    statblock."""
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
                    div(class_="field")[
                        label(for_="f-statblock")[
                            t("npcs.statblock"), " ", span(class_="opt")[t("common.optional")]
                        ],
                        div(class_="tool-inline npc-statblock")[
                            input_el(
                                id="f-statblock",
                                name="statblock",
                                type="text",
                                value=statblock_ref,
                                autocomplete="off",
                                aria_describedby="f-statblock-hint",
                            ),
                            tool_button(page, "statblock", t("npcs.statblock_pick"), "search")
                            if result is not None
                            else None,
                        ],
                        p(class_="field-hint", id="f-statblock-hint")[t("npcs.statblock_hint")],
                    ],
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
            # The creature picker, outside the form above (a form never holds another).
            tool_dialog(
                page,
                "statblock",
                t("npcs.statblock_pick"),
                search_form(
                    page,
                    result,
                    world.id,
                    in_dialog=False,
                    target=npc_statblock_target(npc.id),
                    autofocus=False,
                ),
            )
            if result is not None
            else None,
            # One picture for both: the portrait is the token's picture, placed in its
            # frame here (documentation/TOKENS.md).
            token_panel(
                page,
                page.token_of("npc", npc.scope.world_id, npc.id),
                "npc",
                npc.id,
                world_id=npc.scope.world_id,
                has_picture=bool(npc.portrait),
                extra=form(method="post", action=f"{url}/portrait/clear")[
                    button(type="submit", class_="btn btn-sm btn-ghost")[t("npcs.clear_portrait")]
                ]
                if npc.portrait
                else None,
            ),
        ],
        world_id=world.id,
        insert_layer=True,
    )


def npc_statblock_target(npc_id: str) -> SearchTarget:
    """The NPC form's creature picker: a pick fills the statblock field (with the
    script), or saves it at once (``POST /gm/npcs/<id>/statblock``)."""
    return SearchTarget(
        action=f"{npc_url(npc_id)}/edit",
        dom_id="picker-results",
        pick_action=f"{npc_url(npc_id)}/statblock",
        pick=npc_id,
        pick_hint="npcs.statblock_pick_hint",
        fill="f-statblock",
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
                a(
                    href=ref_url(entry.category, item.name, item.source, world_id),
                    class_="ref",
                    title=item.name,
                )[page.t.term(entry.category, item.name) or item.name]
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
    characters: Sequence[tuple[str, str]] = (),
) -> Element:
    """A treasure, laid out for editing: what it holds (items, coins, the picker to add
    more) and its details (name, place, notes) in the wide column; sharing it out, rolling
    more and its token beside them. ``characters`` (the world's) are whom it can be shared
    out to.

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
    tok = page.token_of("treasure", treasure.scope.world_id, treasure.id)
    return app_shell(
        page,
        treasure.name,
        div(class_="enc-page tr-edit")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("treasure.heading"), world_treasure_url(world.id)),
                (treasure.name, None),
            ),
            div(class_="npc-head tr-head")[
                token(tok, "lg"),
                div(class_="npc-head-main")[
                    span(class_="eyebrow")[_place_text(place)],
                    h1[treasure.name],
                    p(class_="field-hint")[
                        t("holdings.paste"), " ", _snippet(page, "treasure", treasure.name)
                    ],
                ],
                div(class_="npc-head-actions")[
                    delete_link(page, f"{url}/delete", t("treasure.delete"), text=True),
                ],
            ],
            alert(error),
            notice(notice_text),
            div(class_="tr-grid")[
                div(class_="tr-main")[
                    section(class_="panel tr-contents", aria_labelledby="plan-h")[
                        div(class_="panel-head")[
                            h2(id="plan-h")[icon("gem", "sm"), " ", t("treasure.contents")]
                        ],
                        div(id="plan")[
                            ul(class_="enc-lines")[
                                [_treasure_line(page, treasure, line, world.id) for line in lines]
                            ]
                            if lines
                            else p(class_="enc-empty")[t("treasure.none_yet")],
                        ],
                        div(class_="tr-add", id="picker")[
                            h3(class_="tr-sub")[t("treasure.add_heading")],
                            search_form(
                                page,
                                result,
                                world.id,
                                in_dialog=False,
                                target=target,
                                autofocus=bool(result.query),
                            ),
                        ],
                        _coins_form(page, url, treasure.coins),
                    ],
                    _details_panel(
                        page,
                        treasure,
                        scopes,
                        notes,
                        notes_text=notes_text,
                        base=base,
                        conflict=conflict,
                    ),
                ],
                div(class_="tr-side")[
                    _share_panel(page, treasure, lines, characters),
                    _roll_panel(page, url, roll) if roll is not None else None,
                    token_panel(page, tok, "treasure", treasure.id),
                ],
            ],
        ],
        world_id=world.id,
        insert_layer=True,
        main_class="shell-main shell-wide",
    )


def _coins_form(page: Page, url: str, coins: Coins) -> Element:
    """The coins it holds, each kind in a box of its own, saved on their own."""
    t = page.t
    return form(method="post", action=url, class_="tr-coins", id="coins")[
        hidden("part", "coins"),
        div(class_="tr-coins-head")[
            h3(class_="tr-sub")[t("treasure.coins")],
            b(class_="mono muted")[coins_text(page, coins)] if not coins.empty else None,
        ],
        div(class_="field-row coin-row")[
            [_coin_field(d, t(f"treasure.coin.{d}"), getattr(coins, d)) for d in DENOMINATIONS],
            button(type="submit", class_="btn tr-coins-save")[
                icon("check", "sm"), span[t("treasure.coins_save")]
            ],
        ],
    ]


def _details_panel(
    page: Page,
    treasure: Treasure,
    scopes: Sequence[tuple[str, str, int]],
    notes: Rendered,
    *,
    notes_text: str | None,
    base: str,
    conflict: str | None,
) -> Element:
    """Name and place side by side, the notes below at the column's full width; how
    the saved notes read, folded beneath."""
    t = page.t
    return section(class_="panel tr-details", id="details", aria_labelledby="details-h")[
        div(class_="panel-head")[
            h2(id="details-h")[icon("pencil", "sm"), " ", t("encounters.settings")]
        ],
        form(method="post", action=treasure_url(treasure.id), class_="stack-form")[
            hidden("part", "details"),
            div(class_="field-row")[
                text_field("name", t("adventures.name"), value=treasure.name, autocomplete="off"),
                scope_select("place", t("scope.label"), scopes, selected=encode(treasure.scope)),
            ],
            div(class_="field")[
                label(for_="f-notes")[t("npcs.notes")],
                textarea(
                    id="f-notes",
                    name="notes",
                    rows="12",
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
        details(class_="tr-preview")[
            summary[t("treasure.notes_read")], div(class_="md-body")[notes.html]
        ]
        if notes.html.strip()
        else None,
    ]


def _share_panel(
    page: Page,
    treasure: Treasure,
    lines: Sequence[TreasureLine],
    characters: Sequence[tuple[str, str]],
) -> Node:
    """Share the treasure out: each item to a character (or kept), the coins split evenly
    among the ticked ones; it lands in their files, and the players are told."""
    if not characters or (not treasure.items and treasure.coins.empty):
        return None
    t = page.t
    url = f"{treasure_url(treasure.id)}/share"
    return details(class_="panel enc-share", open=True)[
        summary(class_="panel-head")[icon("send"), b[t("treasure.share")]],
        form(method="post", action=url, class_="stack")[
            [
                label(class_="field")[
                    span[_item_label(page, item, treasure.scope.world_id)],
                    select(name=f"item.{index}")[
                        option(value="")[t("treasure.share_keep")],
                        [option(value=cid)[name] for cid, name in characters],
                    ],
                ]
                for index, item in enumerate(treasure.items)
            ],
            fieldset(class_="enc-share-coins")[
                legend[t("treasure.share_coins", coins=coins_text(page, treasure.coins))],
                [
                    label(class_="cs-heal-pick")[
                        input_el(type="checkbox", name="coins_to", value=cid, checked=True),
                        span[name],
                    ]
                    for cid, name in characters
                ],
            ]
            if not treasure.coins.empty
            else None,
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-sm btn-primary")[
                    icon("send", "sm"), span[t("treasure.share_go")]
                ]
            ],
        ],
    ]


def _item_label(page: Page, item: TreasureItem, world_id: str | None) -> str:
    """ "Potion of Healing ×2", in the reader's language."""
    name = page.t.term("item", item.name) or page.t.term("baseitem", item.name) or item.name
    return f"{name} ×{item.count}" if item.count > 1 else name


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
    "new_npc_name_field",
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
