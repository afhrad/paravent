"""The log on screen (documentation/PROTOCOL.md §5): the top-bar button, the panel, and
the page that is the panel without JavaScript.

Entries are facts; this module phrases them in the reader's language — the kind, the
dice, every bonus by its source — and lays them out newest first: the result large in a
box on the left, who and what beside it, the breakdown beneath, with a divider where a
fight's round changes and where a new session starts (a gap of more than three hours).

What a reader may see was decided before anything reaches here
(:mod:`app.services.protocol_view`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from htpy import Element, Node, a, b, button, dialog, div, form, li, ol, p, span

from app.models.protocol import Die, EntryKind, ProtocolEntry, RollRecord, Visibility
from app.services.protocol_view import Filter, LogPage, Shown
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.names import name_text
from app.ui.rules_text import dice as dice_words

LOG_URL = "/log"
SESSION_GAP = timedelta(hours=3)
_OUTCOME_CLASS = {
    "crit": "crit",
    "fumble": "fumble",
    "success": "ok",
    "hit": "ok",
    "failure": "bad",
    "miss": "bad",
}


# -- the button and the dialog --------------------------------------------------------


def log_button(page: Page) -> Element:
    """The log in the top bar of every signed-in page, with a badge for what is new.
    A link to the log page; ``protocol.js`` opens the panel instead."""
    label = page.t("protocol.title")
    return a(
        href=LOG_URL,
        class_="btn btn-ghost shell-log",
        aria_label=label,
        title=label,
        aria_haspopup="dialog",
    )[icon("scroll", "md"), span(class_="log-badge", hidden=True, aria_live="polite")]


def log_dialog(page: Page, chosen: Filter) -> Element:
    """The panel's frame; its content is fetched when it opens. ``chosen`` is what the
    page pre-selects: its fight, or the character whose sheet it is."""
    t = page.t
    return dialog(
        id="log-panel",
        class_="logpanel",
        aria_label=t("protocol.title"),
        data_query=_query(chosen),
    )[
        div(class_="logpanel-head")[
            b[t("protocol.title")],
            button(
                type="button",
                class_="btn btn-ghost logpanel-close",
                data_log_close=True,
                aria_label=t("protocol.close"),
                title=t("protocol.close"),
            )[icon("x", "md")],
        ],
        div(class_="logpanel-content", aria_live="polite")[
            p(class_="muted logpanel-loading")[t("protocol.loading")]
        ],
    ]


def toast_layer(page: Page) -> Element:
    """Where toasts appear (PROTOCOL.md §6): above the bottom bar on a phone, bottom
    right on wider screens. ``protocol.js`` fills it with what arrives after the page
    was made, by the reader's setting."""
    identity = page.identity
    mode = identity.user.toasts if identity is not None else "off"
    return div(
        id="log-toasts",
        class_="log-toasts",
        aria_live="polite",
        data_since=datetime.now(UTC).isoformat(),
        data_mode=mode,
    )


def toast_cards(page: Page, shown: Sequence[Shown]) -> Element:
    """Toasts for these entries, newest first: the result large, what and who, ✕."""
    t = page.t
    newest = shown[0].entry.at.isoformat() if shown else ""
    cards = []
    for item in shown:
        entry = item.entry
        outcome = entry.outcome
        cards.append(
            div(
                class_=f"log-toast {_OUTCOME_CLASS.get(outcome, '')}".strip(),
                data_entry=entry.id,
                tabindex="0",
            )[
                div(class_="log-result mono")[_result(entry)],
                div(class_="log-body")[
                    div(class_="log-who")[
                        b[entry.subject_name or entry.actor_name],
                        span(class_="log-target")[f"→ {entry.target_name}"]
                        if entry.target_name
                        else None,
                    ],
                    div(class_="log-what")[
                        span[headline(page, entry)],
                        _mode_chip(page, entry.roll),
                        span(class_=f"pill log-outcome {_OUTCOME_CLASS.get(outcome, '')}")[
                            _outcome_word(page, outcome)
                        ]
                        if outcome
                        else None,
                    ],
                ],
                button(
                    type="button",
                    class_="btn btn-ghost log-toast-close",
                    data_toast_close=True,
                    aria_label=t("protocol.toast_close"),
                    title=t("protocol.toast_close"),
                )[icon("x", "md")],
            ]
        )
    return div(class_="log-toast-batch", data_newest=newest)[cards]


# -- the panel's content ------------------------------------------------------------


def log_panel(page: Page, result: LogPage, chosen: Filter, *, gamemaster: bool) -> Element:
    """Filters, the entries and "older" — the fragment the panel shows."""
    t = page.t
    newest = result.shown[0].entry.at.isoformat() if result.shown else ""
    return div(class_="logpanel-body", data_newest=newest)[
        _filters(page, chosen, gamemaster=gamemaster),
        log_rows(page, result, chosen)
        if result.shown
        else p(class_="cs-empty")[t("protocol.empty")],
    ]


def log_rows(page: Page, result: LogPage, chosen: Filter) -> Element:
    """The entries with their dividers, and the button that fetches older ones."""
    t = page.t
    items: list[Node] = []
    previous: ProtocolEntry | None = None
    for shown in result.shown:
        entry = shown.entry
        divider = _divider(page, entry, previous, result.fights)
        if divider is not None:
            items.append(divider)
        items.append(_row(page, shown))
        previous = entry
    older: Node = None
    if result.more and result.shown:
        query = _query(chosen, before=result.shown[-1].entry.id)
        older = li(class_="log-more")[
            a(
                href=f"{LOG_URL}?{query}",
                class_="btn btn-sm btn-ghost",
                data_log_older=query,
            )[t("protocol.older")]
        ]
    return ol(class_="log-list")[items, older]


def _filters(page: Page, chosen: Filter, *, gamemaster: bool) -> Element:
    t = page.t
    options: list[tuple[str, Filter]] = [("all", Filter("all"))]
    if not gamemaster:
        options.append(("me", Filter("me")))
    if chosen.combat:
        options.append(("fight", Filter("fight", combat=chosen.combat)))
    if gamemaster and chosen.character:
        options.append(("character", Filter("character", character=chosen.character)))
    if gamemaster:
        options.append(("monsters", Filter("monsters")))
    keep = {"combat": chosen.combat, "character": chosen.character}
    return div(class_="log-filters", role="group", aria_label=t("protocol.filter.label"))[
        [
            a(
                href=f"{LOG_URL}?{_query(option, **keep)}",
                class_="fchip",
                data_log_query=_query(option, **keep),
                aria_pressed="true" if option.kind == chosen.kind else "false",
            )[t(f"protocol.filter.{key}")]
            for key, option in options
        ]
    ]


def _query(chosen: Filter, **extra: str | None) -> str:
    values = {
        "filter": chosen.kind,
        "combat": extra.get("combat", chosen.combat),
        "character": extra.get("character", chosen.character),
        "before": extra.get("before"),
    }
    return urlencode({k: v for k, v in values.items() if v})


def _divider(
    page: Page,
    entry: ProtocolEntry,
    previous: ProtocolEntry | None,
    fights: dict[str, str],
) -> Element | None:
    """A line where the evening moves on: a new session, a fight's round."""
    t = page.t
    if previous is not None and previous.at - entry.at > SESSION_GAP:
        return li(class_="log-divider session")[
            span[previous.at.astimezone().strftime("%d.%m.%Y %H:%M")]
        ]
    combat, round_ = entry.scope.combat, entry.scope.round
    if combat is None:
        return None
    if previous is not None and (previous.scope.combat, previous.scope.round) == (combat, round_):
        return None
    name = fights.get(combat, "")
    label = t("protocol.round", n=round_) if round_ else t("protocol.before_round")
    return li(class_="log-divider")[span[f"{label} · {name}" if name else label]]


def _row(page: Page, shown: Shown) -> Element:
    t = page.t
    entry = shown.entry
    roll = entry.roll
    outcome = entry.outcome
    classes = ["log-entry", _OUTCOME_CLASS.get(outcome, "")]
    if shown.corrected:
        classes.append("corrected")
    who = entry.subject_name or entry.actor_name
    by = (
        entry.actor_name
        if entry.actor_name and entry.subject_name and entry.actor_name != entry.subject_name
        else ""
    )
    marks: list[Node] = []
    if shown.revealable and entry.visibility is Visibility.PUBLIC:
        # Public, but the players read it without the numbers: a monster, a DC.
        marks.append(
            span(class_="log-mark gm", title=t("protocol.result_only"))[icon("eye-off", "xs")]
        )
    elif entry.visibility is Visibility.PRIVATE:
        marks.append(span(class_="log-mark", title=t("protocol.private"))[icon("lock", "xs")])
    elif entry.visibility is Visibility.GM and not shown.revealed:
        marks.append(span(class_="log-mark gm", title=t("protocol.gm_only"))[icon("eye-off", "xs")])
    return li(class_=" ".join(c for c in classes if c))[
        div(class_="log-result mono")[_result(entry)],
        div(class_="log-body")[
            div(class_="log-who")[
                span(class_="log-time mono")[entry.at.astimezone().strftime("%H:%M")],
                b[who],
                span(class_="log-target")[f"→ {entry.target_name}"] if entry.target_name else None,
                span(class_="log-by muted")[t("protocol.by", name=by)] if by else None,
                marks,
            ],
            div(class_="log-what")[
                span[headline(page, entry)],
                _mode_chip(page, roll),
                span(class_=f"pill log-outcome {_OUTCOME_CLASS.get(outcome, '')}")[
                    _outcome_word(page, outcome)
                ]
                if outcome
                else None,
                span(class_="pill neutral")[t("protocol.corrected")] if shown.corrected else None,
            ],
            _breakdown(page, entry),
            _reveal(page, entry) if shown.revealable else None,
        ],
    ]


def _reveal(page: Page, entry: ProtocolEntry) -> Element:
    """The gamemaster's "Reveal": the players read the entry in full from then on."""
    t = page.t
    return form(
        method="post", action=f"{LOG_URL}/reveal", class_="log-reveal", data_log_reveal=True
    )[
        hidden("entry", entry.id),
        hidden("next", LOG_URL),
        button(type="submit", class_="btn btn-xs btn-ghost")[
            icon("eye", "xs"), span[t("protocol.reveal")]
        ],
    ]


def _result(entry: ProtocolEntry) -> str:
    if entry.roll is not None:
        return str(entry.roll.total)
    if entry.amount is not None:
        return str(entry.amount)
    if entry.outcome == "miss":
        return "–"
    return "·"


def _outcome_word(page: Page, outcome: str) -> str:
    if outcome in ("crit", "fumble", "success", "failure"):
        return page.t(f"log.outcome.{outcome}")
    return page.t(f"protocol.outcome.{outcome}")


def _mode_chip(page: Page, roll: RollRecord | None) -> Node:
    if roll is None or roll.mode == "normal":
        return None
    t = page.t
    reasons = ", ".join(reason_word(page, r) for r in roll.reasons)
    return span(
        class_=f"pill log-mode {roll.mode}",
        title=reasons or None,
    )[t(f"protocol.mode.{roll.mode}")]


def headline(page: Page, entry: ProtocolEntry) -> str:
    """What happened, in the reader's words: "Dexterity save", "Longsword attack"."""
    t = page.t
    kind, what = entry.kind, entry.what
    head, _, rest = what.partition(":")
    if kind is EntryKind.SAVE and head == "repeat":
        # A condition shaken off, or kept: "Wisdom save 14 — Frightened ends".
        ability, _, condition = rest.partition(":")
        key = "combat.log.repeat_ends" if entry.outcome == "success" else "combat.log.repeat_stays"
        return t(
            key,
            subject=entry.subject_name,
            ability=_word(page, f"ability.{ability}", ability),
            amount=entry.roll.total if entry.roll is not None else "",
            condition=_word(page, f"condition.{condition}", condition),
        )
    if kind in (EntryKind.CHECK, EntryKind.SAVE):
        ability = rest
        if head in ("check", "save") and ability:
            key = "log.check" if kind is EntryKind.CHECK else "log.save"
            return t(key, subject=_word(page, f"ability.{ability}", ability))
    if kind is EntryKind.SKILL and head == "skill":
        return _word(page, f"skill.{rest}", rest.title())
    if kind is EntryKind.INITIATIVE:
        return t("log.initiative")
    if kind is EntryKind.ATTACK:
        return t("log.attack", subject=rest if head == "attack" else what)
    if kind is EntryKind.DAMAGE:
        if head == "damage" and rest:
            return t("log.damage", subject=rest)
        return t("protocol.damage_taken", amount=entry.amount or 0)
    if kind is EntryKind.HEAL:
        if head == "heal" and rest:
            return t("log.heal", subject=rest)
        return t("protocol.healed", amount=entry.amount or 0)
    if kind is EntryKind.TEMP_HP:
        return t("protocol.temp", amount=entry.amount or 0)
    if kind is EntryKind.CAST and head == "cast":
        name, _, level = rest.partition(":")
        return t("log.cast", subject=f"{name} ({level})" if level else name)
    if kind is EntryKind.CONCENTRATION:
        return t("log.concentration", subject=rest if head == "concentration" else what)
    if kind is EntryKind.DEATH_SAVE:
        return t("log.death_save")
    if kind is EntryKind.HIT_DIE:
        return t("log.hit_die", subject=dice_words(page, rest))
    if kind is EntryKind.REST:
        return _word(page, f"sheet.rest_{rest}", rest)
    if kind is EntryKind.CONDITION:
        condition = _word(page, f"condition.{rest}", rest)
        return t(f"protocol.condition_{head}", condition=condition)
    if kind is EntryKind.TURN:
        if what == "lair":
            return t("combat.log.lair", subject=entry.subject_name)
        return t("combat.log.turn", subject=entry.subject_name)
    if kind is EntryKind.ROUND:
        return t("combat.log.begin")
    if kind is EntryKind.COMBAT_START:
        return t("combat.log.start", subject=entry.subject_name)
    if kind is EntryKind.COMBAT_END:
        return t("combat.log.end")
    if kind is EntryKind.COMBAT:
        # The fight's bookkeeping, in the tracker's words: added, hidden, changed side …
        if head == "add" and (entry.amount or 1) > 1:
            return t("combat.log.add_many", subject=entry.subject_name, amount=entry.amount)
        detail = rest
        if head == "side":
            detail = t("combat.friend" if rest == "friend" else "combat.foe")
        key = f"combat.log.{head}"
        text = t(key, subject=entry.subject_name, detail=detail, amount=entry.amount or 0)
        return what if text == key else text
    if kind is EntryKind.HANDOUT:
        return t("protocol.handout", title=entry.note, name=entry.subject_name)
    if kind is EntryKind.EXPERIENCE:
        text = t("protocol.experience", amount=entry.amount or 0)
        return f"{text} · {entry.note}" if entry.note else text
    if kind is EntryKind.LEVEL_UP and head == "level":
        name, _, level = rest.partition(":")
        return t("protocol.level_up", name=name, level=level, hp=entry.amount or 0)
    if kind is EntryKind.ABILITY_SCORES:
        return t("protocol.ability_scores", rolls=dice_words(page, what))
    if kind is EntryKind.STARTING_GOLD:
        return t("protocol.starting_gold", rolls=dice_words(page, what))
    if kind is EntryKind.HIT_POINTS:
        return t("protocol.hit_points", rolls=dice_words(page, what))
    if kind is EntryKind.CUSTOM:
        if head == "mastery":
            rule = t(
                f"combat.mastery.{rest}",
                target=entry.subject_name,
                distance=page.u.distance(10),
            )
            return f"{rest.title()} — {rule}"
        if head == "recharge":
            outcome = "success" if entry.outcome == "success" else "failure"
            return t(
                f"combat.log.recharge_{outcome}",
                subject=entry.subject_name,
                detail=rest,
                amount=entry.amount or 0,
            )
        if head == "custom":
            return dice_words(page, rest)
        if head == "wild_shape":
            on, _, form = rest.partition(":")
            name = name_text(page, "monster", form.partition("|")[0])
            return t(f"protocol.wild_shape_{on}", form=name)
    return what


def _breakdown(page: Page, entry: ProtocolEntry) -> Node:
    """The dice and every bonus by its source, then damage by type."""
    parts: list[str] = []
    if entry.roll is not None:
        parts.append(roll_words(page, entry.roll))
    for damage in entry.damage_rolls:
        parts.append(roll_words(page, damage))
    if entry.types:
        parts.append(
            ", ".join(
                f"{amount} {page.t(f'rules.damage.{kind}') if kind else ''}".strip()
                for kind, amount in entry.types.items()
            )
        )
    if not parts:
        return None
    return div(class_="log-breakdown mono")[" · ".join(parts)]


def roll_words(page: Page, roll: RollRecord) -> str:
    """ "d20 15 (7) + DEX 3 + Prof 2" — the kept die, the dropped one in brackets, each
    bonus by its source; in German "W20 15 (7) + GES 3 + ÜB 2"."""
    words = [_die(d) for d in roll.dice]
    for bonus in roll.bonuses:
        sign = "+" if bonus.value >= 0 else "−"
        word = bonus_word(page, bonus.source)
        words.append(f"{sign} {word} {abs(bonus.value)}" if word else f"{sign} {abs(bonus.value)}")
    return dice_words(page, " ".join(words))


def _die(die: Die) -> str:
    if die.faces == 20 and len(die.results) > 1 and die.kept:
        dropped = list(die.results)
        dropped.remove(die.kept[0])
        return f"d20 {die.kept[0]} ({', '.join(map(str, dropped))})"
    if len(die.results) == 1:
        return f"d{die.faces} {die.results[0]}"
    return f"{len(die.results)}d{die.faces} [{', '.join(map(str, die.results))}]"


def bonus_word(page: Page, source: str) -> str:
    """A bonus's source as the reader says it: "DEX", "Prof", "Cloak of Protection"."""
    head, _, rest = source.partition(":")
    if head == "ability" and rest:
        return _word(page, f"abbr.{rest}", rest.upper())
    if head in ("item", "feature", "feat", "spell") and rest:
        return rest
    if head == "condition" and rest:
        return _word(page, f"condition.{rest}", rest)
    if head == "override":
        return page.t("protocol.bonus.override")
    return _word(page, f"protocol.bonus.{head}", head)


def reason_word(page: Page, reason: str) -> str:
    """Why a roll had advantage or disadvantage: "the switch", "Poisoned"."""
    head, _, rest = reason.partition(":")
    if head == "condition" and rest == "encumbered":  # variant encumbrance, not a condition
        return _word(page, "protocol.reason.encumbered", rest)
    if head == "condition" and rest:
        return _word(page, f"condition.{rest}", rest)
    return _word(page, f"protocol.reason.{head}", head)


def _word(page: Page, key: str, fallback: str) -> str:
    """The catalogue's word, or ``fallback`` when it has none (a homebrew skill)."""
    word = page.t(key)
    return fallback if word == key else word  # "" is a word too: no label


def log_embed_slot(chosen: Filter) -> Element:
    """A place on a page that shows a slice of the log — the sheet's dice log, the
    tracker's fold — fetched when the page (or its swapped fragment) arrives, and again
    on every append (``protocol.js`` raises ``paravent-log``)."""
    return div(
        class_="log-embed",
        hx_get=f"{LOG_URL}/panel?{_query(chosen)}&embed=1",
        hx_trigger="load, paravent-log from:body",
        # Its own target: the sheet's root sets hx-target="this", which would be inherited
        # and fill the whole sheet with the log.
        hx_target="this",
        hx_swap="innerHTML",
    )


def log_embed(page: Page, result: LogPage, chosen: Filter) -> Element:
    """The embedded slice: the newest entries, and the way to the whole log."""
    t = page.t
    if not result.shown:
        return p(class_="cs-empty")[t("protocol.empty")]
    return div(class_="log-embedded")[
        log_rows(page, LogPage(result.shown, more=False, fights=result.fights), chosen),
        a(
            href=f"{LOG_URL}?{_query(chosen)}",
            class_="btn btn-sm btn-ghost log-open",
            data_log_open=True,
        )[t("protocol.open_all")]
        if result.more
        else None,
    ]


def log_page_content(page: Page, result: LogPage, chosen: Filter, *, gamemaster: bool) -> Element:
    """The log as a page of its own — what the button opens without JavaScript."""
    return div(class_="log-page")[log_panel(page, result, chosen, gamemaster=gamemaster)]


def shown_ids(result: LogPage) -> Sequence[str]:
    """The ids on a page, newest first (tests and paging)."""
    return [s.entry.id for s in result.shown]


__all__ = [
    "LOG_URL",
    "bonus_word",
    "headline",
    "log_button",
    "log_dialog",
    "log_embed",
    "log_embed_slot",
    "log_page_content",
    "log_panel",
    "log_rows",
    "reason_word",
    "roll_words",
    "shown_ids",
    "toast_cards",
    "toast_layer",
]
