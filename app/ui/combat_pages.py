"""The combat tracker's screens (documentation/COMBAT.md §5).

* :func:`combats_page` — running and finished combats;
* :func:`combat_page` — the tracker: the board, the selected combatant's statblock, and
  the panel for adding to the fight;
* :func:`board` — the round, the order and every row. It is the fragment each button
  returns and each live update fetches: rows re-sort when an initiative changes, so
  the whole order is swapped rather than one row (DESIGN.md §4 asked for a row; the
  order made that the wrong unit).

Every form posts and redirects without JavaScript; with htmx it swaps ``#combat-board``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    dialog,
    div,
    form,
    h1,
    h2,
    h3,
    header,
    img,
    label,
    li,
    ol,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Combat, Combatant, CombatLogEntry, KnownFact
from app.services.combat import (
    CharacterSnapshot,
    CombatView,
    Row,
    Summary,
    TurnView,
    pending_attacks,
)
from app.services.experience import Progress
from app.services.knowledge import Revealable, action_name
from app.services.monster_attacks import MonsterSpell, attack_of, encode, monster_spells, save_of
from app.services.protocol_view import Filter
from app.services.references import ReferenceEntry
from app.services.search import SearchResult
from app.services.sheet import CONDITIONS, Sheet
from app.services.tokens import Token
from app.ui import rules_text
from app.ui.context import Page
from app.ui.encounter_pages import level_pill
from app.ui.forms import alert, hidden
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.knowledge_text import action_names, fact_detail, fact_label, ordered
from app.ui.layout import app_shell
from app.ui.markdown import Rendered
from app.ui.names import classes_text, monster_label, name_text
from app.ui.palette import SearchTarget, search_form
from app.ui.protocol_pages import log_embed_slot
from app.ui.ref_pages import statblock
from app.ui.rulebook_pages import screen_url
from app.ui.sheet_pages import defenses, speed_text
from app.ui.tags import RenderContext, render, render_entries
from app.ui.tokens import token

COMBATS_URL = "/gm/combat"
# The add panel's choice, ``side=friend``: added on the characters' side.
FRIEND = "friend"
SCRIPTS = ("/static/js/htmx.min.js", "/static/js/roll.js", "/static/js/combat.js")
NUMBERED = re.compile(r"^(?P<base>.*?) (?P<n>\d+)$")


def combat_url(combat_id: str, **query: str) -> str:
    """The tracker of one combat, with ``sel``/``q``/``error`` if given."""
    kept = {k: v for k, v in query.items() if v}
    return f"{COMBATS_URL}/{combat_id}" + (f"?{urlencode(kept)}" if kept else "")


def _row_token(view: CombatView, row: Row, label: str) -> Element:
    """A combatant's round token, a numbered label's number on its edge ("Goblin 2")."""
    c = row.combatant
    fallback = Token("character" if c.character_id else "creature")
    match = NUMBERED.match(label)
    return token(view.tokens.get(c.id, fallback), badge=match["n"] if match else None)


# -- forms ---------------------------------------------------------------------------


def _post(url: str, sel: str, *content: Node, cls: str = "cb-form") -> Element:
    """A form that swaps the board, or reloads the page without JavaScript."""
    return form(
        method="post",
        action=url,
        hx_post=url,
        hx_target="#combat-board",
        hx_swap="outerHTML",
        class_=cls,
    )[hidden("sel", sel) if sel else None, content]


def _row_form(
    view: CombatView, row: Row, action: str, sel: str, *content: Node, **kw: str
) -> Element:
    url = f"{COMBATS_URL}/{view.combat.id}/c/{row.combatant.id}/{action}"
    return _post(url, sel, *content, **kw)


def _name_dice(view: CombatView, row: Row, caption: str, apply: str) -> Element:
    """A dice inside the label field: names this creature in its kind's style, in place.
    A hidden apply button comes first, so Enter in the field still applies the edit."""
    url = f"{COMBATS_URL}/{view.combat.id}/c/{row.combatant.id}/name"
    return span(class_="name-roll")[
        button(type="submit", class_="sr-only", tabindex="-1", aria_hidden="true")[apply],
        button(
            type="submit",
            formaction=url,
            formnovalidate=True,
            hx_post=url,
            hx_target="#combat-board",
            hx_swap="outerHTML",
            class_="btn btn-ghost btn-sm name-roll-dice",
            title=caption,
            aria_label=caption,
        )[icon("dices", "sm")],
    ]


def _combat_form(view: CombatView, action: str, sel: str, *content: Node, **kw: str) -> Element:
    return _post(f"{COMBATS_URL}/{view.combat.id}/{action}", sel, *content, **kw)


# -- the board -----------------------------------------------------------------------


def _round_head(page: Page, view: CombatView, sel: str) -> Element:
    t = page.t
    combat = view.combat
    current = view.current
    if not combat.running:
        status: Node = span(class_="pill neutral")[t("combat.ended")]
    elif combat.round == 0:
        status = span(class_="pill cond")[t("combat.rolling")]
    elif current is not None:
        status = span(class_="pill accent")[
            span(class_="dot"),
            t("combat.turn_of", name=turn_name(page, current)),
        ]
    else:
        status = None
    lair_toggle: Node = (
        _combat_form(
            view,
            "lair",
            sel,
            button(
                type="submit",
                class_="btn btn-sm btn-ghost cb-lair-toggle",
                aria_pressed="true" if combat.lair else "false",
                title=t("combat.lair_toggle_hint"),
            )[icon("vault", "sm"), span[t("combat.lair_on" if combat.lair else "combat.lair_off")]],
        )
        if combat.running and view.lair_available
        else None
    )
    if not combat.running:
        controls: Node = None
    elif combat.round == 0:
        controls = _combat_form(
            view,
            "begin",
            sel,
            button(type="submit", class_="btn btn-sm btn-primary", disabled=not view.turns)[
                icon("play"), span[t("combat.begin")]
            ],
        )
    else:
        controls = [
            _combat_form(
                view,
                "previous",
                sel,
                button(type="submit", class_="btn btn-sm", title=t("combat.previous"))[
                    icon("arrow-left"), span(class_="sr-only")[t("combat.previous")]
                ],
            ),
            _combat_form(
                view,
                "next",
                sel,
                button(type="submit", class_="btn btn-sm btn-primary")[
                    span[t("combat.next")], icon("arrow-right")
                ],
            ),
        ]
    return div(class_="round-head")[
        div(class_="round-n")[
            span(class_="eyebrow")[t("combat.round")],
            span(class_="big")[str(combat.round) if combat.round else "–"],
        ],
        status,
        _difficulty(page, view),
        div(class_="cb-controls")[lair_toggle, controls],
        _tools(page, view),
    ]


def _tools(page: Page, view: CombatView) -> Element:
    """The tracker's own links, in the round bar rather than a page header: the rules
    screen, the walkthrough, and ending the fight (deleting it, once ended)."""
    t = page.t
    combat = view.combat
    url = f"{COMBATS_URL}/{combat.id}"
    return div(class_="cb-links")[
        a(
            href=screen_url("dm", combat.world_id),
            class_="btn btn-sm btn-ghost",
            target="_blank",
            rel="noopener",
            title=t("combat.rules"),
            aria_label=t("combat.rules"),
        )[icon("book-open", "sm")],
        a(
            href=f"/help/combat?back={url}",
            class_="btn btn-sm btn-ghost",
            title=t("combat.help_title"),
        )[t("combat.help")],
        a(href=f"{url}/end", class_="btn btn-sm cb-end")[icon("x", "sm"), t("combat.end")]
        if combat.running
        else a(href=f"{url}/delete", class_="btn btn-sm cb-end")[
            icon("x", "sm"), t("combat.delete")
        ],
    ]


def _difficulty(page: Page, view: CombatView) -> Node:
    """How hard the fight still is: falls as foes fall, rises as the party does."""
    t = page.t
    rating = view.rating
    if rating is None or not view.combat.running or not rating.bands:
        return None
    if rating.monster_count == 0:
        pill: Node = span(class_="pill heal")[t("combat.difficulty_clear")]
    else:
        pill = level_pill(page, rating)
    return div(
        class_="cb-difficulty",
        title=t(
            "combat.difficulty_hint",
            xp=f"{rating.adjusted_xp:,}".replace(",", "." if page.lang == "de" else ","),
            foes=rating.monster_count,
            party=rating.party_size,
        ),
    )[span(class_="eyebrow")[t("combat.difficulty")], pill]


def turn_name(page: Page, turn: TurnView) -> str:
    """ "Goblin ×4" for a group of one kind, else the names; "Lair" for the lair."""
    if turn.turn.is_lair:
        return page.t("combat.lair")
    rows = turn.rows
    first = rows[0]
    same_kind = all(r.combatant.ref == first.combatant.ref for r in rows)
    if len(rows) > 1 and first.monster is not None and same_kind:
        return f"{name_text(page, 'monster', first.monster.name)} ×{len(rows)}"
    return ", ".join(shown_label(page, r) for r in rows)


def shown_label(page: Page, row: Row) -> str:
    """The row's label as the reader sees it: its creature's name localized."""
    return monster_label(page, row.label, row.monster.name if row.monster else None)


def _waiting(page: Page, view: CombatView) -> Node:
    if view.combat.round or not view.waiting:
        return None
    names = ", ".join(shown_label(page, r) for r in view.waiting)
    return p(class_="cb-waiting")[icon("hourglass", "sm"), page.t("combat.waiting", names=names)]


def _initiative(page: Page, view: CombatView, turn: TurnView, sel: str) -> Element:
    t = page.t
    if turn.turn.is_lair:
        return div(class_="cb-init")[
            span(class_="mono cb-init-input cb-init-fixed", title=t("combat.lair_hint"))[
                str(turn.turn.initiative)
            ]
        ]
    lead = turn.rows[0]
    value = turn.turn.initiative
    running = view.combat.running
    return div(class_="cb-init")[
        _row_form(
            view,
            lead,
            "initiative",
            sel,
            input_el(
                type="number",
                name="value",
                value="" if value is None else str(value),
                min="-10",
                max="50",
                inputmode="numeric",
                placeholder="–",
                aria_label=t("combat.initiative_of", name=shown_label(page, lead)),
                class_="mono cb-init-input" + ("" if value is not None else " is-blank"),
                disabled=not running,
            ),
            cls="cb-form cb-init-form",
        ),
        _row_form(
            view,
            lead,
            "roll",
            sel,
            button(type="submit", class_="btn btn-sm btn-ghost", title=t("combat.roll"))[
                icon("dices", "sm"), span(class_="sr-only")[t("combat.roll")]
            ],
        )
        if running and not lead.combatant.is_character
        else None,
        _order_buttons(page, view, lead, sel) if running else None,
    ]


def _order_buttons(page: Page, view: CombatView, lead: Row, sel: str) -> Node:
    """Up and down within a tie, shown only where there is a tie."""
    t = page.t
    rank = (lead.combatant.initiative, lead.combatant.initiative_bonus, lead.combatant.is_character)
    tied = [
        tv
        for tv in view.turns
        if tv.rows
        and (
            tv.rows[0].combatant.initiative,
            tv.rows[0].combatant.initiative_bonus,
            tv.rows[0].combatant.is_character,
        )
        == rank
    ]
    if lead.combatant.initiative is None or len(tied) < 2:
        return None
    return div(class_="cb-tie")[
        _row_form(
            view,
            lead,
            "order",
            sel,
            button(type="submit", name="step", value="-1", class_="btn btn-xs btn-ghost")["↑"],
            cls="cb-form",
        ),
        _row_form(
            view,
            lead,
            "order",
            sel,
            button(type="submit", name="step", value="1", class_="btn btn-xs btn-ghost")["↓"],
            cls="cb-form",
        ),
        span(class_="sr-only")[t("combat.tie")],
    ]


def _pills(page: Page, view: CombatView, row: Row, sel: str) -> list[Node]:
    t = page.t
    c = row.combatant
    pills: list[Node] = []
    if row.dead:
        pills.append(span(class_="pill cb-dead")[icon("skull", "xs"), t("combat.dead")])
    if c.is_character:
        pills.append(span(class_="pill side-friend")[t("combat.pc")])
    else:
        pills.append(_side(page, view, row, sel))
    if c.hidden:
        pills.append(span(class_="pill neutral")[icon("eye", "xs"), t("combat.hidden")])
    timers = row.timers
    for key in row.conditions:
        rounds = timers.get(key)
        pills.append(
            span(class_="pill cond")[
                t(f"condition.{key}"),
                small(class_="mono")[f" {rounds}"] if rounds else None,
            ]
        )
    if row.concentration:
        pills.append(
            span(class_="pill magic")[
                icon("hourglass", "xs"), name_text(page, "spell", row.concentration)
            ]
        )
    if row.health in ("bloodied", "critical") and not c.is_character and not row.dead:
        pills.append(span(class_="pill dmg")[t(f"combat.health.{row.health}")])
    if c.save_due is not None and view.combat.running:
        pills.append(
            span(class_="cb-save")[
                span(class_="pill dmg")[t("combat.save_due", dc=c.save_due)],
                _row_form(
                    view,
                    row,
                    "save",
                    sel,
                    button(type="submit", class_="btn btn-xs")[
                        icon("dices", "xs"), t("combat.save_roll")
                    ],
                    button(type="submit", name="kept", value="1", class_="btn btn-xs btn-ghost")[
                        t("combat.save_kept")
                    ],
                    button(type="submit", name="kept", value="0", class_="btn btn-xs btn-ghost")[
                        t("combat.save_lost")
                    ],
                    cls="cb-form cb-inline",
                ),
            ]
        )
    return pills


def _side(page: Page, view: CombatView, row: Row, sel: str) -> Node:
    """Friend or foe, as a pill that switches it (the whole group) while the fight runs."""
    t = page.t
    friend = row.combatant.ally
    word = t("combat.friend" if friend else "combat.foe")
    cls = "pill side-friend" if friend else "pill side-foe"
    if not view.combat.running:
        return span(class_=cls)[word]
    return _row_form(
        view,
        row,
        "side",
        sel,
        button(
            type="submit",
            class_=f"{cls} cb-side-switch",
            title=t("combat.make_foe" if friend else "combat.make_friend"),
        )[icon("refresh-cw", "xs"), word],
        cls="cb-form cb-inline",
    )


def _character_line(page: Page, character: CharacterSnapshot) -> str:
    """ "Kämpfer 8 · Champion": the classes in the reader's language."""
    return classes_text(page, character.classes) if character.classes else character.line


def _meta(page: Page, view: CombatView, row: Row) -> list[Node]:
    t = page.t
    parts: list[Node] = []
    if row.ac is not None:
        parts.append(span(class_="mono")[t("combat.ac", ac=row.ac)])
    if row.character is not None:
        if row.character.line:
            parts.append(span[_character_line(page, row.character)])
        aimed = view.row(row.combatant.target) if row.combatant.target else None
        if aimed is not None:
            parts.append(
                span(class_="cb-target")[
                    icon("target", "sm"), " ", t("combat.target_is", name=shown_label(page, aimed))
                ]
            )
        if row.character.dying:
            ok, bad = row.character.death_saves
            parts.append(
                span(class_="cb-death")[
                    t("sheet.death_saves"),
                    " ",
                    span(class_="mono ok")[f"✓ {ok}"],
                    " ",
                    span(class_="mono bad")[f"✗ {bad}"],
                ]
            )
    elif row.monster is not None:
        parts.append(span[name_text(page, "monster", row.monster.name)])
        if row.monster.cr is not None:
            parts.append(span(class_="mono")[t("combat.cr", cr=row.monster.cr.text)])
    out: list[Node] = []
    for i, part in enumerate(parts):
        if i:
            out.append(" · ")
        out.append(part)
    return out


def _hp(row: Row) -> Node:
    if row.hp is None:
        return span(class_="hp-val")["—"]
    return span(class_="hp-val")[
        str(row.hp),
        small[f"/{row.hp_max}" if row.hp_max else ""],
        small(class_="cb-temp")[f" +{row.temp}"] if row.temp else None,
    ]


def _hp_fill(row: Row) -> str | None:
    """The row's background as its hit point bar: filled to the share left, coloured
    by the health word (green, amber when bloodied, red when critical)."""
    return f"--hp:{row.ratio * 100:.0f}%" if row.hp_max else None


def _menu(page: Page, view: CombatView, row: Row, sel: str) -> Element:
    """Conditions and everything rarer than damage, folded away."""
    t = page.t
    c = row.combatant
    active = set(row.conditions)
    group_size = sum(1 for tv in view.turns for r in tv.rows if r.combatant.group == c.group)
    conditions = _row_form(
        view,
        row,
        "condition",
        sel,
        div(class_="cb-conds")[
            [
                button(
                    type="submit",
                    name="key",
                    value=key,
                    class_=f"cbtn {'on' if key in active else ''}",
                    aria_pressed="true" if key in active else "false",
                )[t(f"condition.{key}")]
                for key in CONDITIONS
            ]
        ],
        div(class_="cb-rounds")[
            span[t("combat.rounds_label")],
            input_el(
                type="number",
                name="rounds",
                min="1",
                max="100",
                inputmode="numeric",
                placeholder="–",
                aria_label=t("combat.rounds_label"),
                class_="mono",
            ),
        ],
    )
    monster_only: list[Node] = []
    if not c.is_character:
        monster_only.append(
            _row_form(
                view,
                row,
                "concentrate",
                sel,
                input_el(
                    type="text",
                    name="spell",
                    value=c.concentration or "",
                    placeholder=t("combat.concentration_on"),
                    aria_label=t("combat.concentration_on"),
                ),
                button(type="submit", class_="btn btn-sm")[t("combat.concentrate")],
                cls="cb-form cb-line",
            )
        )
        monster_only.append(
            _row_form(
                view,
                row,
                "edit",
                sel,
                span(class_="name-field")[
                    input_el(
                        type="text", name="label", value=c.label, aria_label=t("combat.label")
                    ),
                    _name_dice(view, row, t("naming.roll"), t("combat.apply")),
                ],
                input_el(
                    type="number",
                    name="ac",
                    value="" if c.ac is None else str(c.ac),
                    min="0",
                    max="40",
                    aria_label=t("combat.ac_label"),
                    placeholder=t("combat.ac_label"),
                    class_="mono",
                ),
                input_el(
                    type="number",
                    name="hp",
                    value=str(c.hp),
                    min="0",
                    aria_label=t("combat.hp_label"),
                    class_="mono",
                ),
                input_el(
                    type="number",
                    name="hp_max",
                    value=str(c.hp_max),
                    min="0",
                    aria_label=t("combat.hp_max_label"),
                    class_="mono",
                ),
                button(type="submit", class_="btn btn-sm")[t("combat.apply")],
                cls="cb-form cb-line cb-edit",
            )
        )
    tools: list[Node] = []
    if group_size > 1 and not c.is_character:
        tools.append(
            _row_form(
                view,
                row,
                "name",
                sel,
                hidden("each", "1"),
                button(
                    type="submit", class_="btn btn-sm btn-ghost", title=t("combat.name_each_hint")
                )[icon("dices", "sm"), t("combat.name_each")],
            )
        )
    if group_size > 1:
        tools.append(
            _row_form(
                view,
                row,
                "split",
                sel,
                button(type="submit", class_="btn btn-sm btn-ghost")[t("combat.split")],
            )
        )
    tools.append(
        _row_form(
            view,
            row,
            "reveal" if c.hidden else "hide",
            sel,
            button(type="submit", class_="btn btn-sm btn-ghost")[
                icon("eye", "sm"), t("combat.reveal" if c.hidden else "combat.hide")
            ],
        )
    )
    if c.character_id is not None:
        tools.append(
            a(href=f"/characters/{c.character_id}", class_="btn btn-sm btn-ghost")[
                t("combat.open_sheet")
            ]
        )
    tools.append(
        _row_form(
            view,
            row,
            "remove",
            sel,
            button(type="submit", class_="btn btn-sm btn-ghost cb-remove")[
                icon("x", "sm"), t("combat.remove")
            ],
        )
    )
    return details(class_="cb-menu")[
        summary(
            class_="btn btn-sm btn-ghost", aria_label=t("combat.more", name=shown_label(page, row))
        )["⋯"],
        div(class_="cb-menu-body")[
            conditions,
            monster_only,
            div(class_="cb-tools")[tools],
        ],
    ]


def combatant_row(page: Page, view: CombatView, turn: TurnView, row: Row, sel: str) -> Element:
    """One combatant: stripe, token, name and pills, numbers, damage and healing; the
    background fills with the hit points left."""
    t = page.t
    c = row.combatant
    state = {"bloodied": "is-bloodied", "critical": "is-critical", "down": "is-down"}.get(
        row.health, ""
    )
    classes = " ".join(
        x
        for x in (
            "combatant",
            state,
            "is-active" if turn.active else "",
            "is-hidden" if c.hidden else "",
            "is-selected" if sel == c.id else "",
            "is-friend" if c.is_friend else "is-foe",
            "is-dead" if row.dead else "",
            "has-hp" if row.hp_max else "",
        )
        if x
    )
    running = view.combat.running
    return li(id=f"c-{c.id}", class_=classes, style=_hp_fill(row))[
        _row_token(view, row, shown_label(page, row)),
        div(class_="c-body")[
            div(class_="c-name")[
                a(
                    href=combat_url(view.combat.id, sel=c.id),
                    hx_get=f"{COMBATS_URL}/{view.combat.id}/side?sel={c.id}",
                    hx_target="#combat-side",
                    hx_swap="outerHTML",
                    class_="cb-name",
                )[
                    b(title=row.label if shown_label(page, row) != row.label else None)[
                        shown_label(page, row)
                    ]
                ],
                _pills(page, view, row, sel),
            ],
            div(class_="c-meta")[_meta(page, view, row)],
        ],
        div(class_="c-actions")[_hp(row)],
        # One line at the bottom: amount, damage, healing, temp HP, and the row menu.
        div(class_="c-damage")[
            _row_form(
                view,
                row,
                "hp",
                sel,
                input_el(
                    type="number",
                    name="amount",
                    min="1",
                    max="999",
                    inputmode="numeric",
                    placeholder="0",
                    aria_label=t("combat.amount_for", name=shown_label(page, row)),
                    class_="mono",
                ),
                button(
                    type="submit",
                    name="mode",
                    value="damage",
                    class_="btn btn-sm btn-dmg",
                    title=t("sheet.damage"),
                )["−"],
                button(
                    type="submit",
                    name="mode",
                    value="heal",
                    class_="btn btn-sm btn-heal",
                    title=t("sheet.heal"),
                )["+"],
                button(
                    type="submit",
                    name="mode",
                    value="temp",
                    class_="btn btn-sm cb-temp-btn",
                    title=t("sheet.temp"),
                )[t("combat.temp_short")],
                cls="cb-form cb-amount",
            ),
            _menu(page, view, row, sel),
        ]
        if running
        else None,
    ]


def board(
    page: Page,
    view: CombatView,
    *,
    sel: str = "",
    flash: str | None = None,
    ctx: RenderContext | None = None,
) -> Element:
    """The round, its controls and the order. Swapped whole after every change."""
    t = page.t
    combat = view.combat
    query = urlencode({"sel": sel}) if sel else ""
    return div(
        id="combat-board",
        class_="tracker",
        # The combat's own revision: an echo of this screen's change matches it, while
        # a character's change arrives as "<combat>|<character>" and never does.
        data_rev=combat.updated_at.isoformat(),
        data_url=f"{COMBATS_URL}/{combat.id}/board" + (f"?{query}" if query else ""),
        data_events=f"{COMBATS_URL}/{combat.id}/events",
    )[
        alert(flash),
        _round_head(page, view, sel),
        # Rolled attacks and save effects wait above the order, not inside its scroll
        # box: on a phone that box is short, and they would push the order out of it.
        _pending_attacks(page, view, sel),
        _repeat_saves(page, view, sel),
        # The order scrolls on its own, so the round, whose turn and Next stay in view;
        # combat.js keeps the position across swaps.
        div(class_="cb-scroll", data_scroll_keep="combat-order")[
            _waiting(page, view),
            _reminders(page, view, sel, ctx),
            ol(class_="cb-order")[
                [
                    li(class_="cb-turn" + (" is-active" if turn.active else ""))[
                        _initiative(page, view, turn, sel),
                        ul(class_="cb-members")[
                            _lair_row(page, view, turn)
                            if turn.turn.is_lair
                            else [combatant_row(page, view, turn, row, sel) for row in turn.rows]
                        ],
                    ]
                    for turn in view.turns
                ]
            ]
            if view.turns
            else p(class_="cb-empty")[t("combat.empty")],
            combat_log(page, combat),
        ],
    ]


def _lair_row(page: Page, view: CombatView, turn: TurnView) -> Element:
    """The lair's place in the order: whose lair, on 20, losing ties."""
    t = page.t
    names = sorted({row_label(page, view, c) for c in turn.turn.lair})
    down = all(row.down for row in (view.row(c.id) for c in turn.turn.lair) if row is not None)
    return li(
        class_="combatant cb-lair"
        + (" is-active" if turn.active else "")
        + (" is-down" if down else "")
    )[
        span(class_="token", aria_hidden="true")[icon("vault", "sm")],
        div(class_="c-body")[
            div(class_="c-name")[b[t("combat.lair")]],
            div(class_="c-meta")[t("combat.lair_of", names=", ".join(names))],
        ],
    ]


def row_label(page: Page, view: CombatView, combatant: Combatant) -> str:
    """The label a combatant's row shows."""
    row = view.row(combatant.id)
    return shown_label(page, row) if row is not None else combatant.label


def _pending_attacks(page: Page, view: CombatView, sel: str) -> Node:
    """Rolled attacks waiting for a verdict: hit a target (its damage applied, its
    resistances counted) or miss. The attacker's opponents are offered first."""
    combat = view.combat
    pending = pending_attacks(combat) if combat.running else []
    if not pending:
        return None
    t = page.t
    rows = [r for turn in view.turns for r in turn.rows]
    items = []
    for entry in pending[:6]:
        attacker = next((r for r in rows if r.combatant.id == entry.actor), None)
        side = _party(attacker) if attacker is not None else False
        others = [r for r in rows if r.combatant.id != entry.actor]
        targets = [r for r in others if _party(r) != side] + [
            r for r in others if _party(r) == side
        ]
        # The target the character's player picked on their phone comes first.
        aimed = attacker.combatant.target if attacker is not None else None
        targets.sort(key=lambda r: r.combatant.id != aimed)
        if entry.kind == "save_effect":
            items.append(_pending_save(page, view, sel, entry, targets))
            continue
        items.append(
            li(class_=f"cb-pending {entry.outcome}".rstrip())[
                span(class_="cb-pending-text")[log_text(page, entry)],
                _combat_form(
                    view,
                    "attack",
                    sel,
                    hidden("at", entry.at.isoformat()),
                    select(name="target", aria_label=t("combat.attack_target"))[
                        [
                            option(value=r.combatant.id)[
                                shown_label(page, r), _target_note(page, r)
                            ]
                            for r in targets
                        ]
                    ],
                    button(type="submit", class_="btn btn-sm btn-dmg")[
                        icon("swords", "sm"), span[t("combat.attack_hit")]
                    ],
                    button(
                        type="submit",
                        name="crit",
                        value="1",
                        class_="btn btn-sm btn-ghost",
                        title=t("combat.attack_crit_title", distance=page.u.distance(5)),
                    )[t("combat.attack_crit")]
                    if entry.outcome != "crit" and entry.damage_dice
                    else None,
                    button(type="submit", name="miss", value="1", class_="btn btn-sm btn-ghost")[
                        t("combat.attack_miss")
                    ],
                    cls="cb-form cb-pending-form",
                ),
            ]
        )
    return section(class_="cb-pending-list", aria_label=t("combat.attacks_pending"))[
        span(class_="eyebrow")[icon("dices", "sm"), " ", t("combat.attacks_pending")],
        ul[items],
    ]


def _repeat_saves(page: Page, view: CombatView, sel: str) -> Node:
    """Conditions the acting creatures may shake off this turn: their save, with a Roll
    button — a character's player can roll it on their phone instead."""
    combat = view.combat
    current = view.current
    if not combat.running or not combat.round or current is None:
        return None
    t = page.t
    items = []
    for row in current.rows:
        for due in row.combatant.repeat_saves:
            if due.rolled_round == combat.round:
                continue
            text = t(
                f"combat.repeat_due_{due.when}",
                name=shown_label(page, row),
                ability=t(f"ability.{due.ability}"),
                dc=due.dc,
                condition=t(f"condition.{due.condition}"),
            )
            items.append(
                li(class_="cb-pending")[
                    span(class_="cb-pending-text")[
                        text,
                        small(class_="muted")[f" · {t('combat.repeat_player')}"]
                        if row.combatant.character_id
                        else None,
                    ],
                    _row_form(
                        view,
                        row,
                        "repeat_save",
                        sel,
                        hidden("condition", due.condition),
                        button(type="submit", class_="btn btn-sm")[
                            icon("dices", "sm"), span[t("combat.repeat_roll")]
                        ],
                        cls="cb-form cb-pending-form",
                    ),
                ]
            )
    if not items:
        return None
    return section(class_="cb-pending-list", aria_label=t("combat.repeats"))[
        span(class_="eyebrow")[icon("refresh-cw", "sm"), " ", t("combat.repeats")],
        ul[items],
    ]


def _rolled_verdict(entry: CombatLogEntry, row: Row) -> str:
    """A player's own roll decides the default: saved or failed against the DC."""
    rolled = entry.rolls.get(row.combatant.id)
    if rolled is None:
        return ""
    return "save" if rolled >= (entry.amount or 0) else "fail"


# What a target's condition does to an attack against it (both editions agree).
_AGAINST = {
    "blinded": "combat.against.adv",
    "restrained": "combat.against.adv",
    "stunned": "combat.against.adv",
    "petrified": "combat.against.adv",
    "paralyzed": "combat.against.adv_crit",
    "unconscious": "combat.against.adv_crit",
    "prone": "combat.against.prone",
    "invisible": "combat.against.dis",
}


def _target_note(page: Page, row: Row) -> str:
    """ " · Paralyzed (advantage; a critical within 5 ft.)" for the target's dropdown."""
    t = page.t
    notes = [
        f"{t(f'condition.{c}')} ({t(_AGAINST[c], distance=page.u.distance(5))})"
        for c in row.conditions
        if c in _AGAINST
    ]
    return f" · {', '.join(notes)}" if notes else ""


def _party(row: Row) -> bool:
    """On the characters' side: a character, or a creature fighting with them."""
    return row.combatant.character_id is not None or row.combatant.ally


def _pending_save(
    page: Page, view: CombatView, sel: str, entry: CombatLogEntry, targets: list[Row]
) -> Element:
    """A save effect's damage, rolled once: each target fails, saves, or has its save
    rolled; "—" leaves one out of the area."""
    t = page.t
    choices = (
        ("", "combat.verdict.none"),
        ("roll", "combat.verdict.roll"),
        ("fail", "combat.verdict.fail"),
        ("save", "combat.verdict.save"),
    )
    picked = len(entry.rolls)
    return li(class_="cb-pending cb-pending-save")[
        details(class_="cb-pending-fold", data_at=entry.at.isoformat())[
            summary[
                span(class_="cb-pending-text")[log_text(page, entry)],
                span(class_="cb-pending-cue")[
                    icon("chevron-down", "sm"),
                    t("combat.save_targets"),
                    small(class_="muted")[
                        f" · {t.plural('combat.save_players_rolled', picked, n=picked)}"
                    ]
                    if picked
                    else None,
                ],
            ],
            _save_form(page, view, sel, entry, targets, choices),
        ]
    ]


def _save_form(
    page: Page,
    view: CombatView,
    sel: str,
    entry: CombatLogEntry,
    targets: list[Row],
    choices: tuple[tuple[str, str], ...],
) -> Element:
    t = page.t
    return _combat_form(
        view,
        "save",
        sel,
        hidden("at", entry.at.isoformat()),
        ul(class_="cb-verdicts")[
            [
                li[
                    span(class_="cb-verdict-name")[
                        shown_label(page, r),
                        small(class_="mono cb-verdict-roll", title=t("combat.save_by_player"))[
                            f" {entry.rolls[r.combatant.id]}"
                        ]
                        if r.combatant.id in entry.rolls
                        else None,
                    ],
                    select(name=f"v.{r.combatant.id}", aria_label=shown_label(page, r))[
                        [
                            option(value=v, selected=v == _rolled_verdict(entry, r) or None)[t(key)]
                            for v, key in choices
                        ]
                    ],
                ]
                for r in targets
            ]
        ],
        button(type="submit", class_="btn btn-sm btn-dmg")[
            icon("check", "sm"), span[t("combat.save_apply")]
        ],
        cls="cb-form cb-pending-form",
    )


def _reminders(page: Page, view: CombatView, sel: str, ctx: RenderContext | None) -> Node:
    """What is due as the turn starts (§4): reminders with a button where one helps."""
    t = page.t
    due = view.reminders
    if due.empty or not view.combat.running or not view.combat.round:
        return None
    items: list[Node] = []
    for name, entries in due.lair:
        items.append(
            div(class_="cb-due-lair")[
                b[t("combat.due_lair", name=name_text(page, "legendaryGroup", name))],
                render_entries(list(entries), ctx),
            ]
        )
    for combatant_id, label_, count in due.legendary:
        row = view.row(combatant_id)
        who = shown_label(page, row) if row is not None else label_
        items.append(
            p(class_="cb-due-item")[
                icon("sparkles", "sm"), " ", t("combat.due_legendary", name=who, n=count)
            ]
        )
    for name, spell in due.concentration:
        items.append(
            p(class_="cb-due-item")[
                icon("eye", "sm"),
                " ",
                t("combat.due_concentration", name=name, spell=name_text(page, "spell", spell)),
            ]
        )
    for regen in due.regeneration:
        row = view.row(regen.combatant_id)
        if row is None:
            continue
        items.append(
            div(class_="cb-due-line")[
                span[t("combat.due_regeneration", name=shown_label(page, row), n=regen.amount)],
                _row_form(
                    view,
                    row,
                    "hp",
                    sel,
                    input_el(type="hidden", name="amount", value=str(regen.amount)),
                    button(type="submit", name="mode", value="heal", class_="btn btn-sm btn-heal")[
                        f"+{regen.amount}"
                    ],
                    cls="cb-form cb-inline",
                ),
            ]
        )
    for recharge in due.recharge:
        row = view.row(recharge.combatant_id)
        if row is None:
            continue
        span_text = f"{recharge.at}–6" if recharge.at < 6 else "6"
        items.append(
            div(class_="cb-due-line")[
                span[
                    t(
                        "combat.due_recharge",
                        name=shown_label(page, row),
                        ability=recharge.ability,
                        on=span_text,
                    )
                ],
                _row_form(
                    view,
                    row,
                    "recharge",
                    sel,
                    input_el(type="hidden", name="ability", value=recharge.ability),
                    input_el(type="hidden", name="at", value=str(recharge.at)),
                    button(type="submit", class_="btn btn-sm")[icon("dices", "sm"), span["d6"]],
                    cls="cb-form cb-inline",
                ),
            ]
        )
    return section(class_="cb-due", aria_label=t("combat.due"))[
        span(class_="eyebrow")[icon("bell", "sm"), " ", t("combat.due")],
        items,
    ]


# -- the log ---------------------------------------------------------------------------


def log_text(page: Page, entry: CombatLogEntry) -> str:
    """One log entry in the reader's language: the record stores what, not a sentence."""
    t = page.t
    kind = entry.kind
    fields = {"subject": entry.subject, "amount": entry.amount or 0, "detail": entry.detail}
    if kind in ("condition_on", "condition_off", "condition_expired", "condition_immune"):
        fields["detail"] = t(f"condition.{entry.detail}") if entry.detail else ""
        if kind == "condition_on" and entry.amount:
            return t("combat.log.condition_on_rounds", **fields)
    elif kind == "concentration" and not entry.detail:
        return t("combat.log.concentration_off", **fields)
    elif kind == "add" and (entry.amount or 1) > 1:
        return t("combat.log.add_many", **fields)
    elif kind == "side":
        fields["detail"] = t("combat.friend" if entry.detail == "friend" else "combat.foe")
    elif kind == "recharge":
        outcome = "success" if entry.outcome == "success" else "failure"
        return t(f"combat.log.recharge_{outcome}", **fields)
    elif kind == "save_effect":
        ability = t(f"ability.{entry.save_ability}")
        if entry.damage:
            text = t("combat.log.save_effect", **fields | {"ability": ability}, damage=entry.damage)
        else:
            text = t("combat.log.save_effect_only", **fields | {"ability": ability})
        if entry.condition:
            condition = t(f"condition.{entry.condition}")
            text += " · " + (
                t("combat.log.save_condition_rounds", condition=condition, n=entry.rounds)
                if entry.rounds
                else t("combat.log.save_condition", condition=condition)
            )
        return (
            f"{text} · {t('combat.log.save_resolved', n=entry.applied)}" if entry.applied else text
        )
    elif kind == "mastery":
        distance = page.u.distance(10)
        rule = t(f"combat.mastery.{entry.detail}", target=entry.subject, distance=distance)
        return f"{name_text(page, 'itemMastery', entry.detail.title())} — {rule}"
    elif kind == "repeat_save":
        key = "combat.log.repeat_ends" if entry.outcome == "success" else "combat.log.repeat_stays"
        return t(
            key,
            subject=entry.subject,
            ability=t(f"ability.{entry.save_ability}"),
            amount=entry.amount if entry.amount is not None else "—",
            condition=t(f"condition.{entry.detail}"),
        )
    elif kind == "save_damage":
        roll = f" ({entry.amount})" if entry.amount is not None else ""
        ability = t(f"ability.{entry.detail}")
        if entry.outcome == "failure" and not entry.damage:
            return t(
                "combat.log.save_failed_none", subject=entry.subject, ability=ability, roll=roll
            )
        if entry.outcome == "failure":
            return t(
                "combat.log.save_failed",
                subject=entry.subject,
                ability=ability,
                roll=roll,
                damage=entry.damage or 0,
            )
        key = "combat.log.save_succeeded" if entry.damage else "combat.log.save_succeeded_none"
        return t(key, subject=entry.subject, ability=ability, roll=roll, damage=entry.damage or 0)
    elif kind == "attack":
        if entry.outcome == "fumble":
            return t("combat.log.attack_fumble", **fields)
        if entry.damage is None:
            return t("combat.log.attack_no_damage", **fields)
        text = t("combat.log.attack", **fields, damage=entry.damage)
        if entry.applied == "-":
            return f"{text} · {t('combat.log.attack_missed')}"
        if entry.applied:
            return f"{text} → {entry.applied}"
        return text
    return t(f"combat.log.{kind}", **fields)


def combat_log(page: Page, combat: Combat) -> Node:
    """The fight so far, newest first: turns, damage, conditions, saves and who did each.

    Part of the board, so every change brings its entry; folded by default, and kept
    open across swaps by ``combat.js``.
    """
    t = page.t
    # A slice of the log (documentation/PROTOCOL.md §8): this fight, newest first, with
    # the dice and every bonus.
    return details(class_="cb-log")[
        summary[icon("scroll", "sm"), span(class_="cb-log-title")[t("combat.log_title")]],
        log_embed_slot(Filter("fight", combat=combat.id)),
    ]


# -- the side: statblock or character ------------------------------------------------


def side_panel(
    page: Page,
    view: CombatView,
    row: Row | None,
    entry: ReferenceEntry | None,
    ctx: RenderContext,
    sheet: Sheet | None = None,
    known: Mapping[str, KnownFact] | None = None,
    offer: Sequence[Revealable] = (),
    translate: TranslateOffer | None = None,
    picture: PictureOffer | None = None,
) -> Element:
    """The selected combatant: a monster's statblock, a character's fighting stats.

    For a creature, ``known`` is what the players know of its kind and ``offer`` what
    its statblock has to tell them; the gamemaster reveals and forgets facts there.

    A character's panel shows numbers that change in the fight (HP, slots, conditions),
    so it carries ``data-live`` and ``combat.js`` reloads it whenever the board changes.
    """
    t = page.t
    live = False
    if row is None:
        content: Node = p(class_="cb-side-hint")[t("combat.select_hint")]
    elif entry is not None:
        if view.combat.running and entry.category == "monster":
            ctx = replace(ctx, block_hook=_attack_hook(page, view, row))
        spells = monster_spells(entry.data)
        content = [
            _translate_button(page, translate) if translate is not None else None,
            _known_panel(page, view, row, known, offer, action_names(page, entry.data))
            if known is not None
            else None,
            _picture_panel(page, row, picture) if picture is not None else None,
            _cast_form(page, view, row, spells) if spells and view.combat.running else None,
            statblock(page, entry, ctx),
        ]
    elif row.character is not None and sheet is not None:
        rage: Node = None
        if sheet.has_feature("Rage") and view.combat.running:
            raging = sheet.character.state.raging
            rage = _row_form(
                view,
                row,
                "rage",
                row.combatant.id,
                button(
                    type="submit",
                    class_="cbtn cs-rage" + (" on" if raging else ""),
                    aria_pressed="true" if raging else "false",
                    title=t("sheet.rage_hint"),
                )[t("combat.raging") if raging else t("combat.not_raging")],
                cls="cb-form cb-inline",
            )
        content = character_block(page, sheet, row.combatant.character_id or "", ctx, rage)
        live = True
    elif row.character is not None:
        content = div(class_="panel")[
            h3[shown_label(page, row)],
            p[_character_line(page, row.character)],
            a(href=f"/characters/{row.combatant.character_id}", class_="btn btn-sm")[
                t("combat.open_sheet")
            ],
        ]
    else:
        content = div(class_="panel")[h3[shown_label(page, row)], p[t("combat.no_statblock")]]
    return div(
        id="combat-side",
        class_="cb-side",
        data_url=f"{COMBATS_URL}/{view.combat.id}/side?sel={row.combatant.id}"
        if row is not None
        else None,
        data_live=f"{COMBATS_URL}/{view.combat.id}/side?sel={row.combatant.id}"
        if live and row is not None
        else None,
    )[content]


# What the statblock could still tell the players, folded by kind: the group's label key.
_OFFER_GROUPS = {
    "action": "combat.known.group_actions",
    "spell": "combat.known.spells",
    "trait": "combat.known.traits",
    "damage": "combat.known.damage",
    "immune": "combat.known.group_immune",
    "rating": "combat.known.group_ratings",
}
_OFFER_GROUP = {kind: kind for kind in _OFFER_GROUPS} | {"ac": "rating", "cr": "rating"}
# Up to this many facts to tell, every group starts open; more, and they start folded.
_OPEN_UP_TO = 8


def _known_panel(
    page: Page,
    view: CombatView,
    row: Row,
    known: Mapping[str, KnownFact],
    offer: Sequence[Revealable],
    names: Mapping[str, str] | None = None,
) -> Element:
    """What the players know of this kind of creature, each fact with Forget, and what
    the statblock could still tell them, ticked and revealed at once with a note.
    ``names`` are its action and trait names in the reader's language."""
    t = page.t
    url = f"{COMBATS_URL}/{view.combat.id}/known/{row.combatant.id}"

    def post(*content: Node, cls: str) -> Element:
        return form(
            method="post",
            action=url,
            hx_post=url,
            hx_target="#combat-side",
            hx_swap="outerHTML",
            class_=cls,
        )[hidden("sel", row.combatant.id), content]

    shown = [(i, f) for i, f in ordered(dict(known)) if i != "note"]
    facts = ul(class_="cb-known-list")[
        [
            li[
                span(class_="cb-known-how", title=t(f"combat.known.{fact.how}"))[
                    icon("eye" if fact.how == "observed" else "megaphone", "sm")
                ],
                b[fact_label(page, fact_id, names)],
                _dash(fact_detail(page, fact_id, fact)),
                post(
                    hidden("do", "forget"),
                    hidden("fact", fact_id),
                    button(
                        type="submit",
                        class_="btn btn-sm btn-ghost",
                        title=t("combat.known.forget"),
                        aria_label=t("combat.known.forget"),
                    )[icon("x", "sm")],
                    cls="cb-known-forget",
                ),
            ]
            for fact_id, fact in shown
        ]
    ]
    still = [r for r in offer if known.get(r.id) is None or known[r.id].how != "revealed"]
    note = known.get("note")
    return details(class_="cb-known", data_known=row.combatant.id)[
        summary[
            icon("eye", "sm"),
            " ",
            t("combat.known.gm_title"),
            small(class_="muted")[f" · {len(shown)}"] if shown else None,
        ],
        facts if shown else p(class_="muted")[t("combat.known.nothing")],
        post(
            hidden("do", "reveal"),
            p(class_="muted")[t("combat.known.reveal_hint")] if still else None,
            div(class_="cb-known-offer")[
                [
                    details(class_="cb-known-group", open=len(still) <= _OPEN_UP_TO)[
                        summary[t(_OFFER_GROUPS[group]), small(class_="muted")[f" · {len(picks)}"]],
                        [
                            label(class_="cb-known-pick")[
                                input_el(type="checkbox", name="fact", value=r.id),
                                " ",
                                fact_label(page, r.id, names),
                                small(class_="muted")[_dash(fact_detail(page, r.id, r.fact))]
                                if r.kind != "trait"
                                else None,
                            ]
                            for r in picks
                        ],
                    ]
                    for group in _OFFER_GROUPS
                    for picks in [[r for r in still if _OFFER_GROUP.get(r.kind) == group]]
                    if picks
                ]
            ]
            if still
            else p(class_="muted")[t("combat.known.all_told")],
            label(class_="cb-known-note")[
                span[t("combat.known.note_label")],
                textarea(name="note", rows="2")[note.text if note is not None else ""],
            ],
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("megaphone", "sm"), span[t("combat.known.reveal")]
            ],
            cls="cb-known-reveal",
        ),
    ]


@dataclass(frozen=True)
class PictureOffer:
    """A creature's picture for the gamemaster: their own (its address) or the books'
    (its address), and where an upload or a removal goes."""

    action: str
    own: str | None = None
    book: str | None = None
    # Why the last upload was refused, shown in the panel.
    error: str = ""


def _picture_panel(page: Page, row: Row, offer: PictureOffer) -> Element:
    """The picture the players see in Details, with a way to set the table's own."""
    t = page.t
    shown = offer.own or offer.book
    state = "own" if offer.own else "book" if offer.book else "none"

    def post(*content: Node, multipart: bool = False) -> Element:
        return form(
            method="post",
            action=offer.action,
            hx_post=offer.action,
            hx_target="#combat-side",
            hx_swap="outerHTML",
            hx_encoding="multipart/form-data" if multipart else None,
            enctype="multipart/form-data" if multipart else None,
            class_="cb-picture-form",
        )[hidden("sel", row.combatant.id), content]

    return details(class_="cb-picture", open=bool(offer.error))[
        summary[
            icon("upload", "sm"),
            " ",
            t("combat.picture.title"),
            small(class_="muted")[f" · {t(f'combat.picture.{state}')}"],
        ],
        img(src=shown, alt=row.combatant.label, class_="cb-picture-img", loading="lazy")
        if shown
        else None,
        p(class_="muted")[t("combat.picture.hint")],
        alert(offer.error),
        post(
            input_el(
                type="file",
                name="picture",
                accept="image/png,image/jpeg,image/webp,image/gif,image/avif",
                required=True,
                aria_label=t("combat.picture.choose"),
            ),
            button(type="submit", class_="btn btn-sm")[t("combat.picture.upload")],
            multipart=True,
        ),
        post(
            hidden("do", "remove"),
            button(type="submit", class_="btn btn-sm btn-ghost")[
                icon("trash-2", "sm"), span[t("combat.picture.remove")]
            ],
        )
        if offer.own
        else None,
    ]


@dataclass(frozen=True)
class TranslateOffer:
    """A creature not yet in the reader's language: the popup editor's address, how
    many passages are still open, and whether its name is missing."""

    url: str
    still: int
    unnamed: bool
    # The full editor, where the button leads without JavaScript.
    page_url: str = ""
    # The books' description (what the players read in Details), when passages of it
    # are open: its popup, its full editor and how many.
    fluff_url: str = ""
    fluff_page_url: str = ""
    fluff_still: int = 0


def _translate_button(page: Page, offer: TranslateOffer) -> Element:
    """Opens the translation popup over the tracker (``combat.js``); saving there
    reloads this creature."""
    t = page.t
    missing = [t("combat.translate_name")] if offer.unnamed else []
    if offer.still:
        missing.append(t.plural("combat.translate_open", offer.still, n=offer.still))
    if offer.fluff_still:
        missing.append(
            t.plural("combat.translate_description_open", offer.fluff_still, n=offer.fluff_still)
        )
    return div(class_="cb-translate")[
        a(
            href=offer.page_url or offer.url,
            class_="btn btn-sm",
            data_translate=offer.url,
        )[icon("languages", "sm"), span[t("combat.translate")]]
        if offer.unnamed or offer.still
        else None,
        a(
            href=offer.fluff_page_url or offer.fluff_url,
            class_="btn btn-sm",
            data_translate=offer.fluff_url,
            title=t("combat.translate_description_hint"),
        )[icon("languages", "sm"), span[t("combat.translate_description")]]
        if offer.fluff_url
        else None,
        small(class_="muted")[" · ".join(missing)],
    ]


def _cast_form(page: Page, view: CombatView, row: Row, spells: Sequence[MonsterSpell]) -> Element:
    """The creature's spells, a button each: one tap casts it into the log for the table
    to see; what it does is applied as usual (damage, a condition, a save effect). A
    spell from a slot or a daily use says how many are left and is greyed out once they
    are gone."""
    t = page.t
    spent = row.combatant.spent
    buttons: list[Element] = []
    for spell in spells:
        left = (
            None
            if spell.pool is None or spell.uses is None
            else max(0, spell.uses - spent.get(spell.pool, 0))
        )
        buttons.append(
            button(
                type="submit",
                name="spell",
                value=spell.name,
                class_="btn btn-sm cb-cast-spell",
                disabled=left == 0,
            )[
                span[name_text(page, "spell", spell.name)],
                small(class_="cb-cast-left")[
                    t.plural("combat.spell_left", left, n=left) if left else t("combat.spell_gone")
                ]
                if left is not None
                else None,
            ]
        )
    cast = _row_form(
        view,
        row,
        "cast",
        row.combatant.id,
        div(class_="cb-cast-spells", role="group", aria_label=t("combat.cast_spell"))[buttons],
        cls="cb-form cb-cast",
    )
    restore = (
        _row_form(
            view,
            row,
            "restore_spells",
            row.combatant.id,
            button(
                type="submit", class_="btn btn-sm btn-ghost", title=t("combat.restore_spells_hint")
            )[icon("refresh-cw", "sm"), span[t("combat.restore_spells")]],
            cls="cb-form cb-restore",
        )
        if spent
        else None
    )
    return div(class_="cb-cast-wrap")[
        span(class_="eyebrow")[icon("sparkles", "sm"), " ", t("combat.cast_spell")],
        cast,
        restore,
    ]


def _dash(detail: str) -> str | None:
    return f" — {detail}" if detail else None


def _attack_hook(
    page: Page, view: CombatView, row: Row
) -> Callable[[dict[str, Any], Callable[[], Node]], Node | None]:
    """Statblock actions with a to-hit bonus get buttons that roll them into the log."""
    t = page.t

    def hook(entry: dict[str, Any], default: Callable[[], Node]) -> Node | None:
        attack = attack_of(entry) if entry.get("name") else None
        if attack is None:
            effect = save_of(entry) if entry.get("name") else None
            if effect is None:
                return None
            name = action_name(str(entry["name"]))
            roll_effect = _row_form(
                view,
                row,
                "save_effect",
                row.combatant.id,
                hidden("name", name),
                hidden("dc", str(effect.dc)),
                hidden("ability", effect.ability),
                hidden("dmg", ";".join(f"{d.expression}:{d.kind}" for d in effect.damage)),
                hidden("half", "1" if effect.half else ""),
                hidden("condition", effect.condition),
                hidden("rounds", str(effect.rounds) if effect.rounds else ""),
                hidden("repeat", effect.repeat),
                button(
                    type="submit",
                    class_="btn btn-sm cb-attack-roll",
                    title=t("combat.save_effect_roll", name=name),
                )[
                    icon("dices" if effect.damage else "shield-check", "sm"),
                    span[t("combat.save_effect" if effect.damage else "combat.save_call")],
                ],
                cls="cb-form cb-attack",
            )
            return div(class_="cb-attack-entry")[default(), roll_effect]
        name = action_name(str(entry["name"]))
        roll = _row_form(
            view,
            row,
            "attack",
            row.combatant.id,
            hidden("name", name),
            hidden("hit", str(attack.hit)),
            hidden("dmg", encode(attack)),
            button(
                type="submit",
                class_="btn btn-sm cb-attack-roll",
                title=t("combat.attack_roll", name=name),
            )[icon("dices", "sm"), span[t("combat.attack")]],
            button(type="submit", name="adv", value="adv", class_="btn btn-sm btn-ghost")[
                t("sheet.adv_adv")
            ],
            button(type="submit", name="adv", value="dis", class_="btn btn-sm btn-ghost")[
                t("sheet.adv_dis")
            ],
            cls="cb-form cb-attack",
        )
        return div(class_="cb-attack-entry")[default(), roll]

    return hook


def _sb_line(label: str, *content: Node) -> Element:
    return p(class_="sb-line")[b[label], " ", content]


def _joined_nodes(parts: Sequence[Node]) -> list[Node]:
    out: list[Node] = []
    for index, part in enumerate(parts):
        if index:
            out.append(", ")
        out.append(part)
    return out


def character_block(
    page: Page, sheet: Sheet, character_id: str, ctx: RenderContext, rage: Node = None
) -> Element:
    """A character as a statblock: what the gamemaster needs at the table, read live
    from the derived sheet. The full sheet is one button away."""
    t = page.t
    pc = sheet.character
    hp = f"{sheet.hp_current}/{sheet.hp_max.value if sheet.hp_max.value is not None else '—'}"
    saves = [
        span(class_="cb-pc-prof" if a_.save_proficient else None)[
            f"{t(f'abbr.{a_.key}')} {a_.save.signed}"
        ]
        for a_ in sheet.abilities
    ]
    skills = [
        f"{s_.name} {s_.value.signed}" + ("*" if s_.expertise else "")
        for s_ in sheet.skills
        if s_.proficient or s_.expertise
    ]
    active = [c for c in sheet.conditions if c.active]
    exhaustion = pc.state.exhaustion
    saves_state = pc.state.death_saves
    spells = [sp for sp in sheet.spells if sp.found]
    by_level: dict[int, list[Node]] = {}
    for spell in sorted(spells, key=lambda sp: (sp.level, sp.name)):
        by_level.setdefault(spell.level, []).append(
            span(class_="cb-pc-spell")[
                render(f"{{@spell {spell.reference}}}", ctx),
                small(class_="cb-pc-c", title=t("combat.pc_concentration"))[" ©"]
                if spell.concentration
                else None,
            ]
        )
    slots = [
        span(class_="cb-pc-slot mono", title=t("sheet.spell_slots"))[
            f"{slot.level}: {slot.available}/{slot.total}"
        ]
        for slot in sheet.slots
        if slot.total
    ]
    if sheet.pact is not None and sheet.pact.total:
        slots.append(
            span(class_="cb-pc-slot mono")[
                f"{t('sheet.pact_short')} {sheet.pact.level}: "
                f"{sheet.pact.available}/{sheet.pact.total}"
            ]
        )
    return div(class_="panel statblock cb-pc")[
        header(class_="sb-head cb-pc-head")[
            span(class_="eyebrow")[t("combat.pc_kind")],
            div(class_="cb-pc-title")[
                h1(class_="sb-name")[pc.name or "?"],
                a(
                    href=f"/characters/{character_id}",
                    class_="btn btn-sm cb-pc-open",
                    title=t("combat.open_sheet"),
                )[icon("scroll", "sm"), span[t("combat.pc_sheet")]],
            ],
            span(class_="sb-source")[sheet.summary or sheet.class_line],
        ],
        div(class_="sb-core")[
            _sb_line(t("sheet.ac"), str(sheet.armor_class.text)),
            _sb_line(
                t("ref.hp"),
                span(class_="mono")[hp],
                small(class_="cb-temp")[f" +{sheet.temp_hp} {t('combat.temp_short')}"]
                if sheet.temp_hp
                else None,
            ),
            _sb_line(t("sheet.speed"), speed_text(page, sheet.speed)),
            _sb_line(t("sheet.initiative"), sheet.initiative.signed),
            _sb_line(t("sheet.proficiency"), sheet.proficiency.signed),
        ],
        div(class_="sb-abilities")[
            [
                div(class_="sb-ability")[
                    span(class_="k")[t(f"abbr.{a_.key}")],
                    span(class_="mono")[
                        str(a_.score.value if a_.score.value is not None else "—"),
                        " ",
                        small[f"({a_.modifier:+d})"],
                    ],
                ]
                for a_ in sheet.abilities
            ]
        ],
        div(class_="sb-core")[
            _sb_line(t("sheet.saves"), _joined_nodes(saves)),
            _sb_line(t("sheet.skills"), ", ".join(skills)) if skills else None,
            _sb_line(t("sheet.passive_perception"), sheet.passive_perception.text),
            _sb_line(name_text(page, "classFeature", "Rage"), rage) if rage is not None else None,
            [_sb_line(label, _joined_nodes(items)) for label, items in defenses(page, sheet)],
            _sb_line(
                t("sheet.conditions"),
                _joined_nodes([t(f"condition.{c.key}") for c in active]),
            )
            if active
            else None,
            _sb_line(t("sheet.exhaustion"), str(exhaustion)) if exhaustion else None,
            _sb_line(t("combat.pc_concentration"), sheet.concentration.name)
            if sheet.concentration is not None
            else None,
            _sb_line(
                t("sheet.death_saves"),
                span(class_="mono ok")[f"✓ {saves_state.successes}"],
                " ",
                span(class_="mono bad")[f"✗ {saves_state.failures}"],
            )
            if sheet.is_dying
            else None,
        ],
        section(class_="sb-section")[
            h3(class_="sb-heading")[t("sheet.attacks")],
            [
                p(class_="cb-pc-attack")[
                    b(class_="entry-name")[name_text(page, ("item", "baseitem"), attack.name)],
                    " ",
                    span(class_="mono")[attack.bonus.signed],
                    f" · {rules_text.dice(page, attack.damage)} {attack.damage_type}".rstrip(),
                    f" · {attack.range}" if attack.range else None,
                ]
                for attack in sheet.attacks
            ],
        ]
        if sheet.attacks
        else None,
        section(class_="sb-section")[
            h3(class_="sb-heading")[t("combat.pc_spellcasting")],
            _sb_line(
                t("sheet.spell_dc"),
                sheet.spell_save_dc.text if sheet.spell_save_dc else "—",
                " · ",
                b[t("sheet.spell_attack")],
                " ",
                sheet.spell_attack.signed if sheet.spell_attack else "—",
            ),
            _sb_line(t("sheet.spell_slots"), slots) if slots else None,
            [
                p(class_="cb-pc-spells")[
                    b[t("sheet.cantrip") if level == 0 else t("combat.pc_level", level=level)],
                    " ",
                    _joined_nodes(names),
                ]
                for level, names in sorted(by_level.items())
            ],
        ]
        if sheet.is_spellcaster
        else None,
        section(class_="sb-section")[
            h3(class_="sb-heading")[t("sheet.resources")],
            [
                _sb_line(resource.name, span(class_="mono")[f"{resource.available}/{resource.max}"])
                for resource in sheet.resources
            ],
        ]
        if sheet.resources
        else None,
    ]


# -- adding to the fight -------------------------------------------------------------


def add_panel(
    page: Page,
    view: CombatView,
    result: SearchResult,
    characters: Sequence[tuple[str, str]],
    *,
    ally: bool = False,
) -> Element:
    """A monster through the picker, a character from the world, or someone ad hoc.

    Monsters and ad-hoc combatants join as foes, or as friends when chosen: the choice
    sits inside each form, so it goes with whichever "add" is pressed.
    """
    t = page.t
    combat = view.combat
    url = combat_url(combat.id)
    target = SearchTarget(
        action=url,
        dom_id="picker-results",
        pick_action=f"{url}/monsters",
        pick=combat.id,
        swap="#combat-board",
    )
    return section(class_="panel cb-add", id="add", aria_labelledby="add-h")[
        div(class_="panel-head")[h2(id="add-h")[t("combat.add_heading")]],
        search_form(
            page,
            result,
            combat.world_id,
            in_dialog=False,
            target=target,
            autofocus=bool(result.query),
            extra=_side_choice(page, ally, "pick"),
        ),
        form(method="post", action=f"{url}/characters", class_="cb-line")[
            select(name="character_id", aria_label=t("combat.add_character"))[
                [option(value=cid)[name] for cid, name in characters]
            ],
            button(type="submit", class_="btn btn-sm")[
                icon("plus", "sm"), t("combat.add_character")
            ],
        ]
        if characters
        else None,
        form(method="post", action=f"{url}/others", class_="cb-line cb-other")[
            input_el(
                type="text",
                name="label",
                required=True,
                placeholder=t("combat.label"),
                aria_label=t("combat.label"),
            ),
            input_el(
                type="number",
                name="ac",
                min="0",
                max="40",
                placeholder=t("combat.ac_label"),
                aria_label=t("combat.ac_label"),
                class_="mono",
            ),
            input_el(
                type="number",
                name="hp",
                min="0",
                placeholder=t("combat.hp_label"),
                aria_label=t("combat.hp_label"),
                class_="mono",
            ),
            input_el(
                type="number",
                name="initiative",
                min="-10",
                max="50",
                placeholder=t("combat.init_short"),
                aria_label=t("combat.init_short"),
                class_="mono",
            ),
            _side_choice(page, ally, "other"),
            button(type="submit", class_="btn btn-sm")[icon("plus", "sm"), t("combat.add_other")],
        ],
    ]


def _side_choice(page: Page, ally: bool, key: str) -> Element:
    """Foe or friend for what is added next; foe unless chosen."""
    t = page.t
    return div(class_="seg cb-side-choice", role="radiogroup", aria_label=t("combat.add_as"))[
        [
            label(class_=f"seg-opt side-{value}")[
                input_el(
                    type="radio",
                    name="side",
                    value=value,
                    checked=(value == FRIEND) == ally,
                    id=f"side-{key}-{value}",
                ),
                span[t(f"combat.{value}")],
            ]
            for value in ("foe", FRIEND)
        ]
    ]


# -- pages ---------------------------------------------------------------------------


def _notes(page: Page, notes: Rendered | None) -> Node:
    """The encounter's notes beside the board — tactics, terrain, how it ends — folded
    until opened. Outside the board, so its live swaps leave them be."""
    if notes is None or not notes.html.strip():
        return None
    return details(class_="panel cb-notes")[
        summary(class_="cb-notes-head")[icon("scroll", "sm"), page.t("combat.notes")],
        div(class_="md-body")[notes.html],
    ]


def combat_page(
    page: Page,
    view: CombatView,
    *,
    side: Element,
    add: Element | None,
    summary_: Summary | None,
    progress: Sequence[XpRow] | None = None,
    sel: str = "",
    error: str | None = None,
    ctx: RenderContext | None = None,
    notes: Rendered | None = None,
) -> Element:
    """The tracker: no page header — the round bar carries the fight's own links, and
    the fight's name is the page's title (and the nav's Combat leads back). ``notes`` are
    the encounter's, in Paravent Markdown: the gamemaster's only, never the players'."""
    combat = view.combat
    return app_shell(
        page,
        combat.name,
        div(class_="cb-page")[
            _summary(page, combat, summary_, progress or ()) if summary_ is not None else None,
            div(class_="cb-grid")[
                div(class_="cb-main")[
                    board(page, view, sel=sel, flash=error, ctx=ctx),
                    _notes(page, notes),
                    add,
                ],
                div(class_="cb-aside")[side],
            ],
            # The translation popup, filled when a creature's Translate is pressed.
            dialog(id="tr-dialog", class_="tr-dialog", aria_label=page.t("combat.translate"))[
                div(id="tr-dialog-body")
            ],
        ],
        main_class="shell-main shell-wide",
        world_id=combat.world_id,
        extra_scripts=(*SCRIPTS, "/static/js/translate.js"),
        log_scope=Filter("fight", combat=combat.id),
    )


@dataclass(frozen=True)
class XpRow:
    """A character who took part, with their experience (None: not tracked)."""

    character_id: str
    name: str
    progress: Progress | None


def _xp_award(page: Page, combat: Combat, s: Summary, rows: Sequence[XpRow]) -> Node:
    """Award the XP once; afterwards, where each character stands (COMBAT.md §6)."""
    t = page.t
    if not rows:
        return None
    if combat.awarded_at is None:
        url = f"{COMBATS_URL}/{combat.id}/award"
        return form(method="post", action=url, class_="cb-award")[
            label(for_="cb-award-xp")[t("combat.award_label")],
            input_el(
                type="number",
                id="cb-award-xp",
                name="xp",
                value=str(s.share),
                min="0",
                max="1000000",
                inputmode="numeric",
                class_="mono",
            ),
            button(type="submit", class_="btn btn-primary btn-sm")[
                icon("sparkles", "sm"), span[t.plural("combat.award", len(rows))]
            ],
        ]
    here = combat_url(combat.id)
    return div(class_="cb-awarded")[
        p[icon("check", "sm"), " ", t("combat.awarded", xp=t.number(combat.awarded_xp or 0))],
        ul(class_="cb-xp-list")[
            [
                li(class_="cb-xp-row")[
                    a(href=f"/characters/{row.character_id}")[b[row.name]],
                    span(class_="mono muted")[
                        t(
                            "combat.xp_of",
                            xp=t.number(row.progress.xp),
                            next=t.number(row.progress.next),
                        )
                        if row.progress is not None and row.progress.next is not None
                        else t("combat.xp_total", xp=t.number(row.progress.xp))
                        if row.progress is not None
                        else ""
                    ],
                    [
                        span(class_="pill cond")[
                            t("combat.level_reached", level=row.progress.reached)
                        ],
                        form(
                            method="post",
                            action=f"/characters/{row.character_id}/grant-level",
                            class_="inline-form",
                        )[
                            input_el(type="hidden", name="next", value=here),
                            button(type="submit", class_="btn btn-sm")[
                                icon("plus", "sm"), span[t("levelup.grant")]
                            ],
                        ],
                    ]
                    if row.progress is not None and row.progress.due
                    else None,
                ]
                for row in rows
            ]
        ],
    ]


def _summary(page: Page, combat: Combat, s: Summary, rows: Sequence[XpRow]) -> Element:
    t = page.t
    return div(class_="panel cb-summary")[
        div(class_="panel-head")[h2[t("combat.summary")]],
        p[t("combat.summary_line", rounds=s.rounds, fallen=len(s.fallen))],
        ul(class_="cb-defeated")[
            [
                li[span[label], span(class_="mono muted")[t("combat.xp_n", xp=xp)]]
                for label, xp in s.defeated
            ]
        ]
        if s.defeated
        else None,
        p(class_="cb-summary-xp")[
            t("combat.summary_xp", xp=s.xp, share=s.share, n=s.characters)
            if s.characters
            else t("combat.summary_xp_nobody", xp=s.xp)
        ],
        p(class_="muted")[", ".join(s.party)] if s.party else None,
        p(class_="cb-summary-down")[
            icon("triangle-alert", "sm"), t("combat.summary_down", names=", ".join(s.party_down))
        ]
        if s.party_down
        else None,
        _xp_award(page, combat, s, rows),
        form(method="post", action=f"{COMBATS_URL}/{combat.id}/treasure", class_="cb-treasure")[
            button(type="submit", class_="btn btn-sm")[
                icon("gem", "sm"), span[t("combat.roll_treasure")]
            ]
        ]
        if combat.world_id
        else None,
        p(class_="field-hint")[t("combat.summary_hint")],
        a(href=f"{COMBATS_URL}/{combat.id}/recap", class_="btn btn-sm")[
            icon("scroll", "sm"), span[t("recap.open")]
        ],
    ]


def combats_page(page: Page, running: Sequence[Combat], finished: Sequence[Combat]) -> Element:
    """Every combat: the ones on now, then the finished."""
    t = page.t

    def item(combat: Combat) -> Element:
        state = (
            t("combat.round_n", n=combat.round)
            if combat.running and combat.round
            else t("combat.rolling")
            if combat.running
            else t("combat.ended")
        )
        return li(class_="cb-list-item")[
            a(href=combat_url(combat.id))[b[combat.name]],
            span(class_="pill neutral")[state],
            span(class_="muted mono")[combat.created_at.strftime("%Y-%m-%d %H:%M")],
        ]

    return app_shell(
        page,
        t("combat.list_title"),
        div(class_="cb-list-page")[
            page_head(t("gm.eyebrow"), t("combat.list_title"), t("combat.list_lede")),
            section(class_="panel")[
                div(class_="panel-head")[h2[t("combat.running")]],
                ul(class_="cb-list")[[item(c) for c in running]]
                if running
                else empty_state("swords", t("combat.none_running"), t("combat.none_running_hint")),
            ],
            section(class_="panel")[
                div(class_="panel-head")[h2[t("combat.finished")]],
                ul(class_="cb-list")[[item(c) for c in finished]],
            ]
            if finished
            else None,
        ],
    )


def combat_not_found_page(page: Page) -> Element:
    """A combat id that names nothing (or, for a player, no fight of theirs)."""
    t = page.t
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    back = (
        a(href=COMBATS_URL, class_="backlink")[icon("arrow-left"), span[t("combat.list_title")]]
        if gamemaster
        else a(href="/play", class_="backlink")[icon("arrow-left"), span[t("play.heading")]]
    )
    return app_shell(
        page,
        t("combat.not_found"),
        div(class_="formpage")[
            back,
            empty_state("swords", t("combat.not_found"), t("combat.not_found_hint")),
        ],
    )


__all__ = [
    "TranslateOffer",
    "FRIEND",
    "XpRow",
    "COMBATS_URL",
    "add_panel",
    "board",
    "combat_not_found_page",
    "combat_page",
    "combat_url",
    "combatant_row",
    "combats_page",
    "side_panel",
]


def party_combats(page: Page, views: Sequence[CombatView], *, world_id: str | None) -> Element:
    """The running fights on the party overview: a way in, and who still has to roll.

    Swapped whole when a combat changes, so a player's initiative roll takes their name
    off the waiting line as it lands.
    """
    t = page.t
    query = f"?{urlencode({'world': world_id})}" if world_id else ""

    def item(view: CombatView) -> Element:
        combat = view.combat
        current = view.current
        if combat.round == 0:
            state: Node = span(class_="pill cond")[t("combat.rolling")]
        else:
            state = span(class_="pill accent")[t("combat.round_n", n=combat.round)]
        turn = (
            span(class_="muted")[t("combat.turn_of", name=turn_name(page, current))]
            if combat.round and current is not None
            else None
        )
        return li(class_="party-combat")[
            a(href=combat_url(combat.id), class_="party-combat-link")[icon("zap"), b[combat.name]],
            state,
            turn,
            _waiting(page, view),
        ]

    return div(id="party-combats", data_url=f"/gm/party/combats{query}")[
        ul(class_="party-combats")[[item(v) for v in views]] if views else None
    ]
