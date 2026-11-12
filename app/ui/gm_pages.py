"""The gamemaster's table: the live party overview, and worlds with their books."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    h2,
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

from app.models.gamestate import World
from app.services.experience import progress
from app.services.sheet import Sheet
from app.services.worlds import Book, BookCatalog
from app.ui.auth_pages import message_panel
from app.ui.context import Page
from app.ui.forms import alert, checkbox_field, notice, select_field, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import class_line
from app.ui.protocol_pages import headline
from app.ui.sheet_pages import speed_text
from app.ui.tokens import token

PARTY_URL = "/gm"
WORLDS_URL = "/gm/worlds"


# -- the party overview -------------------------------------------------------------------


def _stat(label: str, value: str, title: str | None = None) -> Element:
    return div(class_="pc-stat", title=title)[span(class_="k")[label], span(class_="v mono")[value]]


def _last_roll(page: Page, sheet: Sheet) -> Node:
    """The character's newest roll in the log (documentation/PROTOCOL.md §8)."""
    entry = sheet.last_roll
    if entry is None or entry.roll is None:
        return None
    t = page.t
    who = f"{entry.actor_name} · " if entry.actor_name else ""
    mark = {"crit": "sparkles", "fumble": "skull"}.get(entry.outcome, "dices")
    outcome = t(f"log.outcome.{entry.outcome}") if entry.outcome in ("crit", "fumble") else None
    return div(class_=f"pc-roll {entry.outcome}")[
        icon(mark),
        span(class_="pc-roll-text")[f"{who}{headline(page, entry)}"],
        span(class_="pc-roll-outcome")[outcome] if outcome else None,
        span(class_="pc-roll-total mono")[str(entry.roll.total)],
    ]


def _card_url(sheet: Sheet, action: str) -> str:
    return f"/gm/party/{sheet.character.id}/act/{action}"


def _card_form(
    sheet: Sheet, action: str, world_filter: str, *content: Node, cls: str = "cs-act"
) -> Element:
    """A form whose result replaces just this card (or reloads the page without JS)."""
    url = _card_url(sheet, action)
    return form(
        method="post",
        action=url,
        hx_post=url,
        hx_target=f"#pc-{sheet.character.id}",
        hx_swap="outerHTML",
        class_=cls,
    )[input_el(type="hidden", name="world", value=world_filter) if world_filter else None, content]


def _card_actions(page: Page, sheet: Sheet, world_filter: str) -> Element:
    """Damage, healing, temporary HP and conditions, right on the card."""
    t = page.t
    active = {c.key for c in sheet.conditions if c.active}
    hp = _card_form(
        sheet,
        "hp",
        world_filter,
        input_el(
            type="number",
            name="amount",
            min="1",
            max="999",
            inputmode="numeric",
            placeholder="0",
            aria_label=t("sheet.amount"),
            class_="mono",
        ),
        button(type="submit", name="mode", value="damage", class_="btn btn-sm btn-dmg")[
            t("sheet.damage")
        ],
        button(type="submit", name="mode", value="heal", class_="btn btn-sm btn-heal")[
            t("sheet.heal")
        ],
        button(type="submit", name="mode", value="temp", class_="btn btn-sm")[t("sheet.temp")],
        cls="pc-amount",
    )
    conditions = details(class_="pc-conds")[
        summary(class_="btn btn-sm btn-ghost")[
            t("sheet.conditions"),
            span(class_="mono pc-conds-n")[f" {len(active)}"] if active else None,
        ],
        div(class_="pc-conds-list")[
            [
                _card_form(
                    sheet,
                    "condition",
                    world_filter,
                    input_el(type="hidden", name="key", value=c.key),
                    button(
                        type="submit",
                        class_=f"cbtn {'on' if c.key in active else ''}",
                        aria_pressed="true" if c.key in active else "false",
                    )[t(f"condition.{c.key}")],
                )
                for c in sheet.conditions
            ]
        ],
    ]
    return div(class_="pc-actions")[hp, conditions]


def party_card(
    page: Page,
    sheet: Sheet,
    *,
    owner: str | None,
    world: str | None,
    world_filter: str = "",
    flash: str | None = None,
) -> Element:
    """One character on the gamemaster's overview. Swapped alone on every change.

    ``world_filter`` is the overview's current world, carried by the card's forms so a
    page reload without JavaScript returns to the same view.
    """
    t = page.t
    pc = sheet.character
    top = sheet.hp_max.value
    ratio = sheet.hp_ratio
    state = sheet.hp_state
    active = [c for c in sheet.conditions if c.active]
    slots = [s for s in sheet.slots if s.total]
    chips: list[Node] = [span(class_="pill cond")[t(f"condition.{c.key}")] for c in active]
    if pc.state.exhaustion:
        chips.append(span(class_="pill cond")[f"{t('sheet.exhaustion')} {pc.state.exhaustion}"])
    if sheet.concentration is not None:
        chips.append(span(class_="pill magic")[icon("hourglass"), sheet.concentration.name])
    if pc.state.inspiration:
        chips.append(span(class_="pill accent")[icon("sparkles"), t("sheet.inspiration")])
    death = None
    if sheet.is_dying:
        saves = pc.state.death_saves
        death = div(class_="pc-death")[
            span[t("sheet.death_saves")],
            span(class_="mono ok")[f"✓ {saves.successes}"],
            span(class_="mono bad")[f"✗ {saves.failures}"],
        ]
    return li(
        id=f"pc-{pc.id}",
        class_=f"pc-card {state or ('nohp' if top is None else '')}",
        data_rev=sheet.revision or "",
    )[
        div(class_="alert pc-flash", role="alert")[icon("triangle-alert"), span[flash]]
        if flash
        else None,
        div(class_="pc-head")[
            token(page.token_of("character", pc.world_id, pc.id)),
            div(class_="pc-who")[
                a(href=f"/characters/{pc.id}", class_="pc-name")[pc.name or t("play.unnamed")],
                span(class_="pc-meta")[
                    " · ".join(
                        x
                        for x in (class_line(page, sheet.character), owner or t("chars.unassigned"))
                        if x
                    )
                ],
            ],
            span(class_="pill neutral pc-world")[world] if world else None,
            span(class_="pill accent", title=t.plural("levelup.pending", pc.pending_levels))[
                icon("sparkles", "sm"), f"+{pc.pending_levels}"
            ]
            if pc.pending_levels
            else None,
            span(class_="pill cond", title=t("levelup.xp_due", level=xp.reached))[
                icon("sparkles", "sm"), f"L{xp.reached}"
            ]
            if (xp := progress(pc)) is not None and xp.due
            else None,
            span(class_="pill dmg", title=sheet.file_error)[t("chars.file_error")]
            if sheet.file_error
            else None,
        ],
        div(class_="pc-hp")[
            div(class_="pc-hp-row")[
                span(class_=f"pc-hp-now mono {state}")[sheet.hp_text],
                span(class_="pc-hp-max mono")[f"/ {top if top is not None else '—'}"],
                span(class_="pill heal")[t("sheet.temp_hp", n=sheet.temp_hp)]
                if sheet.temp_hp
                else None,
            ],
            div(class_="hpbar")[span(class_=f"hpfill {state}", style=f"width:{ratio * 100:.0f}%")],
        ],
        death,
        div(class_="pc-stats")[
            _stat(t("party.ac"), sheet.armor_class.text, sheet.armor_class.explanation),
            _stat(t("party.init"), sheet.initiative.signed),
            _stat(t("party.pp"), sheet.passive_perception.text),
            _stat(t("party.dc"), sheet.spell_save_dc.text) if sheet.spell_save_dc else None,
            _stat(t("party.speed"), speed_text(page, sheet.speed)),
        ],
        div(class_="pc-chips")[chips] if chips else None,
        div(class_="pc-slots")[
            [
                span(class_="pc-slot mono", title=t("sheet.spell_slots"))[
                    f"L{s.level} {s.available}/{s.total}"
                ]
                for s in slots
            ],
            span(class_="pc-slot mono")[
                f"{t('sheet.pact_short')} {sheet.pact.available}/{sheet.pact.total}"
            ]
            if sheet.pact
            else None,
        ]
        if slots or sheet.pact
        else None,
        _last_roll(page, sheet),
        _card_actions(page, sheet, world_filter),
    ]


def party_grid(page: Page, cards: Sequence[Element], *, world_id: str | None) -> Element:
    """The cards, as one swappable element (refetched when a character appears or goes)."""
    t = page.t
    query = f"?world={world_id}" if world_id else ""
    return div(id="party", class_="party", data_url=f"{PARTY_URL}/party{query}")[
        ul(class_="party-grid")[cards]
        if cards
        else empty_state(
            "users-round",
            t("party.empty_heading"),
            t("party.empty_text"),
            a(href="/gm/characters", class_="btn btn-primary")[t("party.to_characters")],
        ),
    ]


def _world_filter(page: Page, worlds: Sequence[World], selected: str | None) -> Element:
    t = page.t
    return form(method="get", action=PARTY_URL, class_="inline-form party-filter")[
        label(for_="party-world", class_="eyebrow")[t("party.world")],
        select(id="party-world", name="world", onchange="this.form.submit()")[
            option(value="", selected=not selected)[t("party.all_worlds")],
            [option(value=w.id, selected=w.id == selected)[w.name] for w in worlds],
        ],
        button(type="submit", class_="btn btn-sm js-hide")[t("party.show")],
    ]


def party_page(
    page: Page,
    cards: Sequence[Element],
    *,
    worlds: Sequence[World],
    world: World | None,
    combats: Node = None,
) -> Element:
    """The gamemaster's landing screen: every character at the table, live.

    ``combats`` is the strip of running fights above the cards.
    """
    t = page.t
    if world is not None:
        books = ", ".join(world.source_set.sources) or t("worlds.all_books")
        lede = t("party.lede_world", world=world.name, books=books)
    else:
        lede = t("party.lede")
    return app_shell(
        page,
        t("gm.page_title"),
        [
            page_head(
                t("gm.eyebrow"),
                t("gm.heading"),
                lede,
                div(class_="party-tools")[
                    _world_filter(page, worlds, world.id if world else None),
                    form(method="post", action="/gm/party/grant-level", class_="inline-form")[
                        input_el(type="hidden", name="world", value=world.id if world else ""),
                        button(
                            type="submit",
                            class_="btn btn-sm",
                            title=t("levelup.grant_all_hint"),
                        )[icon("sparkles", "sm"), span[t("levelup.grant_all")]],
                    ],
                    a(href=f"/help/start?back={PARTY_URL}", class_="btn btn-sm btn-ghost")[
                        icon("book-open", "sm"), span[t("help.start")]
                    ],
                    span(class_="cs-live", data_live_indicator="1", title=t("party.live_title"))[
                        span(class_="dot"), span[t("sheet.live")]
                    ],
                ],
            ),
            combats,
            party_grid(page, cards, world_id=world.id if world else None),
        ],
        extra_scripts=("/static/js/htmx.min.js", "/static/js/party.js"),
        main_class="shell-main shell-wide",
        world_id=world.id if world else None,
    )


# -- worlds ----------------------------------------------------------------------------


def worlds_page(
    page: Page,
    worlds: Sequence[World],
    counts: dict[str, int],
    *,
    notice_text: str | None = None,
) -> Element:
    """Every world, with how many books and characters it has."""
    t = page.t
    rows = [
        li(class_="list-row")[
            span(class_="avatar", aria_hidden="true")[icon("globe")],
            div(class_="list-main")[
                a(href=f"{WORLDS_URL}/{w.id}", class_="list-title list-link")[w.name],
                span(class_="list-meta")[
                    " · ".join(
                        [
                            t("worlds.books_n", n=len(w.source_set))
                            if len(w.source_set)
                            else t("worlds.all_books"),
                            t.plural("players.characters", counts.get(w.id, 0)),
                        ]
                    )
                ],
            ],
            a(href=f"{PARTY_URL}?world={w.id}", class_="btn btn-sm")[
                icon("users-round"), span(class_="list-action-label")[t("worlds.party")]
            ],
            a(href=f"{WORLDS_URL}/{w.id}/edit", class_="btn btn-sm")[
                icon("pencil"), span(class_="list-action-label")[t("players.edit")]
            ],
        ]
        for w in worlds
    ]
    return app_shell(
        page,
        t("worlds.page_title"),
        [
            page_head(
                t("gm.eyebrow"),
                t("worlds.heading"),
                t("worlds.lede"),
                a(href=f"{WORLDS_URL}/new", class_="btn btn-primary")[
                    icon("plus"), span[t("worlds.new")]
                ],
            ),
            notice(notice_text),
            ul(class_="list")[rows]
            if rows
            else empty_state(
                "globe",
                t("worlds.empty_heading"),
                t("worlds.empty_text"),
                a(href=f"/help/start?back={WORLDS_URL}", class_="btn")[
                    icon("book-open"), span[t("help.start")]
                ],
            ),
        ],
    )


def _book_choice(page: Page, book: Book, checked: bool, players: bool) -> Element:
    """A book to declare, and whether players may read it (PLAYER-VIEW.md); never an
    adventure."""
    field_id = f"book-{book.code}"
    return div(class_="book-row")[
        label(class_="book", for_=field_id)[
            input_el(
                type="checkbox", id=field_id, name="sources", value=book.code, checked=checked
            ),
            span(class_="book-name")[book.name],
            span(class_="book-code mono")[book.code],
            span(class_="pill accent book-ed")["2024"] if book.is_2024 else None,
        ],
        label(class_="book-players", title=page.t("worlds.players_read"))[
            input_el(type="checkbox", name="players", value=book.code, checked=players),
            span[page.t("worlds.players")],
        ]
        if book.group != "adventure"
        else None,
    ]


def world_form_page(
    page: Page,
    catalog: BookCatalog,
    *,
    world: World | None,
    name: str,
    description: str,
    sources: Sequence[str],
    players: frozenset[str] = frozenset(),
    rerolls: bool = False,
    encumbrance: bool = False,
    language: str = "en",
    languages: Sequence[tuple[str, str]] = (),
    error: str | None = None,
    extra: Node = None,
) -> Element:
    """Create a world (``world`` is None) or edit one, with its books. ``languages`` are
    ``(code, name)`` of the languages Paravent speaks.

    ``extra`` follows the form — the world's images panel when editing.
    """
    t = page.t
    chosen = {s.upper() for s in sources}
    action = WORLDS_URL if world is None else f"{WORLDS_URL}/{world.id}"
    title = t("worlds.new") if world is None else t("worlds.edit_title", name=world.name)
    # Declared books that have no content loaded are kept, and shown as missing.
    missing = [s for s in sources if catalog.get(s) is None]
    groups = [
        details(
            class_="book-group",
            open=any(bk.code.upper() in chosen for bk in books) or group == "core",
        )[
            summary[
                span[t(f"worlds.group.{group}")],
                span(class_="book-count mono")[
                    f"{sum(1 for bk in books if bk.code.upper() in chosen)}/{len(books)}"
                ],
            ],
            div(class_="book-list")[
                [
                    _book_choice(page, bk, bk.code.upper() in chosen, bk.code.upper() in players)
                    for bk in books
                ]
            ],
        ]
        for group, books in catalog.grouped()
    ]
    return app_shell(
        page,
        title,
        div(class_="formpage formpage-wide")[
            a(href=WORLDS_URL if world is None else f"{WORLDS_URL}/{world.id}", class_="backlink")[
                icon("arrow-left"),
                span[t("worlds.back") if world is None else world.name],
            ],
            div(class_="panel")[
                div(class_="panel-head")[span(class_="eyebrow")[t("gm.eyebrow")], b[title]],
                alert(error),
                form(method="post", action=action, class_="stack-form")[
                    text_field(
                        "name",
                        t("worlds.name"),
                        value=name,
                        autocomplete="off",
                        autofocus=world is None,
                    ),
                    div(class_="field")[
                        label(for_="f-description")[
                            t("worlds.description"), " ", span(class_="opt")[t("common.optional")]
                        ],
                        textarea(id="f-description", name="description", rows="3")[description],
                    ],
                    select_field(
                        "language",
                        t("worlds.language"),
                        list(languages),
                        selected=language,
                        hint=t("worlds.language_hint"),
                    )
                    if languages
                    else None,
                    section(class_="field books")[
                        h2(class_="eyebrow")[t("worlds.books")],
                        p(class_="field-hint")[t("worlds.books_hint")],
                        [
                            label(class_="book missing")[
                                input_el(type="checkbox", name="sources", value=code, checked=True),
                                span(class_="book-name")[t("worlds.not_loaded", code=code)],
                            ]
                            for code in missing
                        ],
                        groups,
                    ],
                    checkbox_field(
                        "rerolls",
                        t("worlds.rerolls"),
                        checked=rerolls,
                        hint=t("worlds.rerolls_hint"),
                    ),
                    checkbox_field(
                        "encumbrance",
                        t("worlds.encumbrance"),
                        checked=encumbrance,
                        hint=t("worlds.encumbrance_hint"),
                    ),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"),
                            span[t("worlds.create") if world is None else t("worlds.save")],
                        ],
                        a(
                            href=WORLDS_URL if world is None else f"{WORLDS_URL}/{world.id}",
                            class_="btn btn-ghost",
                        )[t("common.cancel")],
                    ],
                ],
            ],
            extra,
        ],
    )


def world_delete_page(
    page: Page, world: World, characters: int, *, error: str | None = None
) -> Element:
    """Confirm, saying what happens to the world's characters."""
    t = page.t
    return app_shell(
        page,
        t("worlds.delete"),
        div(class_="formpage")[
            a(href=f"{WORLDS_URL}/{world.id}/edit", class_="backlink")[
                icon("arrow-left"), span[t("common.cancel")]
            ],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("gm.eyebrow")],
                    b[t("worlds.delete_heading", name=world.name)],
                ],
                alert(error),
                p[t("worlds.delete_lede")],
                p[
                    t.plural("worlds.delete_characters", characters)
                    if characters
                    else t("worlds.delete_no_characters")
                ],
                form(method="post", action=f"{WORLDS_URL}/{world.id}/delete")[
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[
                            icon("x"), span[t("worlds.delete")]
                        ],
                        a(href=f"{WORLDS_URL}/{world.id}/edit", class_="btn btn-ghost")[
                            t("common.cancel")
                        ],
                    ]
                ],
            ],
        ],
    )


def world_not_found_page(page: Page) -> Element:
    """The world does not exist (any more)."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "globe",
            t("notfound.world"),
            t("notfound.world_lede"),
            a(href=WORLDS_URL, class_="btn btn-primary")[
                icon("arrow-left"), span[t("worlds.back")]
            ],
        ),
    )


__all__ = [
    "PARTY_URL",
    "WORLDS_URL",
    "party_card",
    "party_grid",
    "party_page",
    "world_delete_page",
    "world_form_page",
    "world_not_found_page",
    "worlds_page",
]
