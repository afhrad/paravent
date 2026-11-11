"""The fight as players see it (documentation/COMBAT.md §5): the round, the order and
whose turn it is — on its own page and as a strip on the character sheet.

Everything here is drawn from :class:`~app.services.combat.PlayerView`, never from the
tracker's view: monsters are a name and a state word, hidden ones are absent, and there
is no AC, no hit point number and no statblock to leak.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from urllib.parse import quote

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    dd,
    details,
    div,
    dl,
    dt,
    form,
    img,
    li,
    ol,
    p,
    small,
    span,
    summary,
    ul,
)

from app.models.gamestate import KnownFact
from app.services.combat import PlayerView, SeenCombatant, SeenDetails, SeenTurn
from app.services.protocol_view import Filter
from app.store.projections import coerce_type
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.knowledge_text import fact_detail, fact_kind, fact_label, ordered
from app.ui.layout import app_shell, initials
from app.ui.names import ITEM, monster_label, named
from app.ui.rules_text import kind_text, speed

PLAY_COMBAT_URL = "/play/combat"
SCRIPTS = ("/static/js/htmx.min.js", "/static/js/combat.js")


def play_combat_url(combat_id: str, suffix: str = "") -> str:
    """The player view of a combat, or one of its fragments."""
    return f"{PLAY_COMBAT_URL}/{combat_id}{suffix}"


def _state(page: Page, view: PlayerView) -> Element:
    t = page.t
    if not view.running:
        return span(class_="pill neutral")[t("combat.ended")]
    if view.round == 0:
        return span(class_="pill cond")[t("combat.rolling")]
    return span(class_="pill accent")[t("combat.round_n", n=view.round)]


def _label(page: Page, member: SeenCombatant) -> str:
    return monster_label(page, member.label, member.monster)


def _turn_name(page: Page, turn: SeenTurn) -> str:
    """ "Goblin ×4" for a group of one kind, as on the tracker."""
    members = turn.members
    labels = [_label(page, m) for m in members]
    if len(members) > 1 and all(m.character_id is None for m in members):
        bases = {label.rsplit(" ", 1)[0] for label in labels}
        if len(bases) == 1:
            return f"{bases.pop()} ×{len(members)}"
    return ", ".join(labels)


def quick_switch(page: Page, view: PlayerView, mine: frozenset[str], on: str) -> Element:
    """Your character and the fight side by side, one tap apart. ``on`` is the page
    showing: ``"fight"`` or the id of the character whose sheet it is."""
    t = page.t
    characters = [m for turn in view.turns for m in turn.members if m.character_id in mine]
    return div(class_="pv-switch", role="group", aria_label=t("combat.quickswitch"))[
        [
            a(
                href=f"/characters/{m.character_id}",
                title=_label(page, m),
                aria_current="page" if on == m.character_id else None,
            )[icon("circle-user", "sm"), span(class_="pv-switch-name")[_label(page, m)]]
            for m in characters
        ],
        a(
            href=play_combat_url(view.id),
            title=view.name,
            aria_current="page" if on == "fight" else None,
        )[icon("swords", "sm"), span[t("combat.fight")]],
    ]


def _fill(member: SeenCombatant) -> str:
    """The row's background as a hit point bar: a friend's share left; a foe's bar is
    full (empty once down), so only its colour — the state word — says how it fares."""
    if member.fill is not None:
        return f"--hp:{member.fill}%"
    return "--hp:0%" if member.health == "down" else "--hp:100%"


def _member(page: Page, view: PlayerView, member: SeenCombatant, mine: frozenset[str]) -> Element:
    """A row of the order. A tap opens what can be done with it: aim at it, read about it."""
    t = page.t
    you = member.character_id is not None and member.character_id in mine
    side = "friend" if member.friend else "foe"
    names = _names(page, view)
    aimed_by = sorted(c for c in mine if view.targets.get(c) == member.id)
    row = [
        span(class_="avatar", aria_hidden="true")[initials(_label(page, member))],
        b[_label(page, member)],
        span(class_="pill accent")[t("combat.you")] if you else None,
        [
            span(class_="pill accent pv-aimed", title=t("combat.your_target"))[
                icon("target", "sm"),
                t("combat.your_target") if len(mine) == 1 else names.get(c, ""),
            ]
            for c in aimed_by
        ],
        span(class_=f"pill {'neutral' if member.health == 'hurt' else 'dmg'}")[
            t(f"combat.health.{member.health}")
        ]
        if member.health != "unhurt"
        else None,
        [span(class_="pill cond")[t(f"condition.{c}")] for c in member.conditions],
    ]
    aims = _aim_forms(page, view, member, mine, names) if view.running else []
    # A character is known at the table: its row says all there is.
    creature = member.character_id is None
    item = li(class_=f"pv-member {member.health} is-{side}", style=_fill(member))
    if not aims and not creature:
        return item[div(class_="pv-pick-row")[row]]  # nothing to do with it: no menu
    return item[
        details(class_="pv-pick", data_member=member.id)[
            summary(class_="pv-pick-row")[row],
            div(class_="pv-pick-menu")[
                aims,
                details(class_="pv-details")[
                    summary(
                        class_="btn btn-sm btn-ghost",
                        title=t("combat.details"),
                        aria_label=t("combat.details"),
                    )[icon("info", "sm")],
                    # Fetched once opened: a board holds many rows.
                    div(
                        class_="pv-details-body",
                        hx_get=play_combat_url(view.id, f"/c/{member.id}/details"),
                        hx_trigger="toggle once from:closest details",
                        hx_target="this",
                        hx_swap="innerHTML",
                    ),
                ]
                if creature
                else None,
            ],
        ]
    ]


def _facts(page: Page, rows: Sequence[tuple[str, Node]]) -> Node:
    """What is seen of a creature, as a list of facts. Its side, state and conditions
    are on its row already."""
    if not rows:
        return None
    return dl(class_="pv-facts")[[(dt[k], dd[v]) for k, v in rows]]


def play_image_url(path: str) -> str:
    """A picture from the corpus's image folder."""
    return f"/play/img/{quote(path)}"


def play_picture_url(combat_id: str, combatant_id: str) -> str:
    """The table's own picture of a combatant's kind (app/services/creature_pictures.py)."""
    return f"/play/combat/{combat_id}/c/{combatant_id}/picture"


def member_details(
    page: Page,
    details: SeenDetails,
    image_url: Callable[[str], str] = play_image_url,
    own_picture: str | None = None,
    names: Mapping[str, str] | None = None,
) -> Node:
    """What players know of a combatant: for a creature with a statblock, what anyone
    sees by looking — never a number that is not seen. ``own_picture`` is the address
    of the table's own picture of its kind, shown instead of the books'; ``names`` its
    action and trait names in the reader's language."""
    t = page.t
    member, seen = details.member, details.sight
    known = _known(page, details.known, names)
    if seen is None:
        return div(class_="pv-seen")[known or p(class_="muted")[t("combat.details_none")]]
    rows: list[tuple[str, Node]] = [
        (t("combat.kind"), kind_text(page, seen.sizes, coerce_type(seen.kind)))
    ]
    moves = speed(page, {"speed": seen.speed})
    if moves:
        rows.append((t("combat.movement"), moves))
    if seen.gear:
        rows.append((t("combat.gear"), ", ".join(named(page, ITEM, g) or g for g in seen.gear)))
    picture = own_picture or (image_url(seen.image) if seen.image else None)
    return div(class_="pv-seen")[
        img(src=picture, alt=_label(page, member), class_="pv-picture", loading="lazy")
        if picture
        else None,
        _facts(page, rows),
        known,
        p(class_="pv-lore", lang="en" if page.lang != "en" else None)[seen.description]
        if seen.description
        else None,
    ]


def _known(
    page: Page, known: Mapping[str, KnownFact], names: Mapping[str, str] | None = None
) -> Node:
    """What the party knows of this kind of creature: what it was seen to use, what each
    damage type did to it, and what the gamemaster told the table."""
    t = page.t
    groups: dict[str, list[Node]] = {
        "used": [],
        "spells": [],
        "traits": [],
        "damage": [],
        "other": [],
    }
    group_of = {"action": "used", "spell": "spells", "trait": "traits", "damage": "damage"}
    for fact_id, fact in ordered(dict(known)):
        kind = fact_kind(fact_id)
        detail = fact_detail(page, fact_id, fact)
        lang = "en" if kind in ("trait",) and page.lang != "en" else None
        groups[group_of.get(kind, "other")].append(
            li(class_=f"pv-known-{fact.effect}" if fact.effect else None)[
                b[fact_label(page, fact_id, names)],
                span(lang=lang)[f" — {detail}" if kind in ("action", "spell") else f": {detail}"]
                if detail
                else None,
            ]
        )
    if not any(groups.values()):
        return None
    return div(class_="pv-known")[
        span(class_="eyebrow")[icon("eye", "sm"), " ", t("combat.known.title")],
        [
            div(class_="pv-known-part")[small[t(f"combat.known.{name}")], ul[items]]
            for name, items in groups.items()
            if items
        ],
    ]


def _names(page: Page, view: PlayerView) -> dict[str, str]:
    """Character id → the name its row shows."""
    return {
        m.character_id: _label(page, m)
        for turn in view.turns
        for m in turn.members
        if m.character_id is not None
    }


def _aim_forms(
    page: Page,
    view: PlayerView,
    member: SeenCombatant,
    mine: frozenset[str],
    names: dict[str, str],
) -> list[Node]:
    """For each of the viewer's characters (but the member itself): aim at it, or stop."""
    t = page.t
    url = play_combat_url(view.id, "/target")
    out: list[Node] = []
    for character_id in sorted(mine):
        if character_id == member.character_id:
            continue
        aimed = view.targets.get(character_id) == member.id
        # The character's name: whose target it becomes, or stops being.
        label = names.get(character_id, "")
        title = t("combat.untarget" if aimed else "combat.choose_target")
        out.append(
            form(
                method="post",
                action=url,
                hx_post=url,
                hx_target="#combat-board",
                hx_swap="outerHTML",
                class_="pv-aim",
            )[
                hidden("character_id", character_id),
                hidden("target", "" if aimed else member.id),
                button(
                    type="submit",
                    class_=f"btn btn-sm {'btn-ghost' if aimed else 'btn-primary'}",
                    title=title,
                    aria_label=f"{title}: {label}",
                )[icon("x" if aimed else "target", "sm"), span[label]],
            ]
        )
    return out


def _save_calls(
    page: Page, view: PlayerView, characters: Sequence[str], back: str, target: str
) -> list[Node]:
    """A save effect waiting for targets: each of the viewer's characters rolls its own
    save — "if you are in the area", which the gamemaster says at the table."""
    t = page.t
    out: list[Node] = []
    for call in view.saves:
        ability = t(f"ability.{call.ability}")
        for character_id in characters:
            rolled = call.rolled.get(character_id)
            what = (
                t("combat.save_call_name", name=call.name, source=call.source)
                if call.source
                else call.name
            )
            if rolled is not None:
                out.append(
                    p(class_="pv-save done")[
                        icon("check", "sm"),
                        t("combat.save_rolled", ability=ability, total=rolled),
                        " ",
                        small(class_="muted")[what],
                    ]
                )
                continue
            url = play_combat_url(view.id, "/save")
            out.append(
                form(
                    method="post",
                    action=url,
                    hx_post=url,
                    hx_target=target,
                    hx_swap="outerHTML",
                    class_="pv-save",
                )[
                    hidden("at", call.at),
                    hidden("character_id", character_id),
                    hidden("back", back),
                    button(type="submit", class_="btn btn-sm btn-primary")[
                        icon("dices"), span[t("combat.player_save_roll", ability=ability)]
                    ],
                    small(class_="muted")[f" {what} · {t('combat.save_if_in_area')}"],
                ]
            )
    return out


def _repeat_calls(
    page: Page, view: PlayerView, characters: Sequence[str], back: str, target: str
) -> list[Node]:
    """On this character's turn: a condition it may shake off with a save."""
    t = page.t
    url = play_combat_url(view.id, "/repeat")
    return [
        form(
            method="post",
            action=url,
            hx_post=url,
            hx_target=target,
            hx_swap="outerHTML",
            class_="pv-save",
        )[
            hidden("character_id", character_id),
            hidden("condition", condition),
            hidden("back", back),
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("refresh-cw"),
                span[
                    t(
                        "combat.player_repeat_roll",
                        ability=t(f"ability.{ability}"),
                        condition=t(f"condition.{condition}"),
                    )
                ],
            ],
        ]
        for character_id, condition, ability in view.repeats
        if character_id in characters
    ]


def player_board(
    page: Page, view: PlayerView, mine: frozenset[str], flash: str | None = None
) -> Element:
    """The order, the round and whose turn — swapped whole on every change."""
    t = page.t
    current = view.current
    yours = view.running and view.acting(mine)
    if yours:
        banner: Node = div(class_="pv-yours", role="status")[
            icon("zap"), span[t("combat.your_turn")]
        ]
    elif view.running and current is not None:
        banner = p(class_="pv-turn")[t("combat.turn_of", name=_turn_name(page, current))]
    else:
        banner = None
    waiting = mine & view.waiting if view.running else frozenset()
    return div(
        id="combat-board",
        class_="pv-board",
        data_rev=view.revision,
        data_url=play_combat_url(view.id, "/board"),
        data_events=play_combat_url(view.id, "/events"),
    )[
        div(class_="pv-head")[_state(page, view), quick_switch(page, view, mine, "fight")],
        div(class_="notice cs-flash", role="status")[flash] if flash else None,
        banner,
        _save_calls(page, view, sorted(mine), "board", "#combat-board") if view.running else None,
        _repeat_calls(page, view, sorted(mine), "board", "#combat-board"),
        p(class_="cb-waiting")[icon("hourglass", "sm"), t("combat.roll_on_sheet")]
        if waiting
        else None,
        ol(class_="pv-order")[
            [
                li(class_=f"pv-turn-row{' active' if turn.active else ''}")[
                    ol(class_="pv-members")[[_member(page, view, m, mine) for m in turn.members]]
                ]
                for turn in view.turns
            ]
        ],
    ]


def player_combat_page(page: Page, view: PlayerView, mine: frozenset[str]) -> Element:
    """The fight on a player's phone: no heading, the round leads (the fight is known
    at the table), with the switch back to the character beside it."""
    return app_shell(
        page,
        view.name,
        div(class_="pv-page")[player_board(page, view, mine)],
        extra_scripts=SCRIPTS,
        log_scope=Filter("fight", combat=view.id),
    )


def fights_list(page: Page, views: Sequence[PlayerView], mine: frozenset[str]) -> Node:
    """The fights a player's characters are in, on their landing screen."""
    t = page.t
    if not views:
        return None
    return div(class_="pv-fights")[
        [
            div(class_=f"pv-strip{' yours' if v.acting(mine) else ''}")[
                a(href=play_combat_url(v.id), class_="pv-strip-link")[icon("zap"), b[v.name]],
                _state(page, v),
                b(class_="pv-strip-yours")[t("combat.your_turn")] if v.acting(mine) else None,
            ]
            for v in views
        ]
    ]


def combat_strip(
    page: Page, character_id: str, views: Sequence[PlayerView], flash: str | None = None
) -> Element:
    """The running fights this character is in, above the sheet. Always present, so a
    fight that starts while the sheet is open appears in it."""
    t = page.t
    mine = frozenset({character_id})

    def item(view: PlayerView) -> Element:
        current = view.current
        yours = view.acting(mine)
        turn: Node = None
        if yours:
            turn = b(class_="pv-strip-yours")[icon("zap"), t("combat.your_turn")]
        elif view.round and current is not None:
            turn = span(class_="muted")[t("combat.turn_of", name=_turn_name(page, current))]
        roll: Node = None
        if character_id in view.waiting:
            url = f"/characters/{character_id}/act/roll"
            roll = form(
                method="post",
                action=url,
                hx_post=url,
                hx_target="#sheet",
                hx_swap="outerHTML",
                class_="cs-act",
            )[
                hidden("kind", "initiative"),
                button(type="submit", class_="btn btn-sm btn-primary")[
                    icon("swords"), span[t("combat.roll_initiative")]
                ],
            ]
        aimed = next(
            (m for t_ in view.turns for m in t_.members if m.id == view.targets.get(character_id)),
            None,
        )
        return div(class_=f"pv-strip{' yours' if yours else ''}")[
            quick_switch(page, view, mine, character_id),
            _state(page, view),
            turn,
            span(class_="pill accent pv-aimed")[
                icon("target", "sm"), t("combat.target_is", name=_label(page, aimed))
            ]
            if aimed is not None
            else None,
            roll,
            _save_calls(page, view, [character_id], "strip", "#combat-strip"),
            _repeat_calls(page, view, [character_id], "strip", "#combat-strip"),
        ]

    return div(
        id="combat-strip",
        data_url=f"/characters/{character_id}/combat",
        data_events=f"/characters/{character_id}/combat/events",
    )[
        div(class_="notice cs-flash", role="status")[flash] if flash else None,
        [item(v) for v in views],
    ]


__all__ = [
    "PLAY_COMBAT_URL",
    "combat_strip",
    "fights_list",
    "member_details",
    "play_image_url",
    "play_combat_url",
    "player_board",
    "player_combat_page",
    "quick_switch",
]
