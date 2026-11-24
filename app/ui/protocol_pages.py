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
from app.services import coins
from app.services.protocol_view import Filter, LogPage, Shown
from app.services.safety import XCARD
from app.services.travel import PACES
from app.ui.clock_pages import clock_label, remaining
from app.ui.context import Page
from app.ui.cues import cue
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.names import name_text
from app.ui.rules_text import dice as dice_words

# A handout in the journal (app/ui/journal_pages.py), which imports from here.
HANDOUT_URL = "/journal"

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


def _toast_cue(page: Page, entry: ProtocolEntry) -> str | None:
    """The sound a toast makes: a handout's for its player, the X-card's for the
    gamemaster (app/ui/cues.py)."""
    if entry.kind is EntryKind.HANDOUT:
        return cue(page, "handout", entry.id)
    if entry.kind is EntryKind.CUSTOM and entry.what == XCARD:
        return cue(page, "xcard", entry.id)
    return None


def _handout_url(entry: ProtocolEntry) -> str | None:
    """Where a handout's entry leads: the handout itself, in the journal."""
    return f"{HANDOUT_URL}/{entry.what}" if entry.kind is EntryKind.HANDOUT and entry.what else None


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
                data_href=_handout_url(entry),
                data_cue=_toast_cue(page, entry),
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
    menu = f"/menu/log/{entry.id}" + ("?reveal=1" if shown.revealable else "")
    return li(class_=" ".join(c for c in classes if c), data_menu=menu)[
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
                a(href=href)[headline(page, entry)]
                if (href := _handout_url(entry))
                else span[headline(page, entry)],
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
    if kind is EntryKind.CHECK and head == "tool":
        ability, _, tool = rest.partition(":")
        return t(
            "log.tool",
            tool=name_text(page, ("item", "baseitem"), tool),
            ability=_word(page, f"ability.{ability}", ability),
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
        if head == "lost":
            spell = name_text(page, "spell", rest.partition("|")[0])
            return t("protocol.concentration_lost", spell=spell)
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
        if head == "rule":
            return rule_text(page, entry.subject_name, rest, entry.amount, entry.outcome)
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
        if head == "bastion":
            number, _, given = rest.partition(":")
            done = []
            for part in filter(None, given.split(";")):
                ref, _, order = part.partition("=")
                name = page.t.term("facility", ref.partition("|")[0]) or ref.partition("|")[0]
                done.append(f"{name} — {t(f'bastion.order.{order}')}")
            orders = ", ".join(done) if done else t("bastion.no_orders")
            return t("protocol.bastion", subject=entry.subject_name, n=number, orders=orders)
        if what == "xcard":
            return t("protocol.xcard")
        if head == "clock":
            span_text, _, after = rest.partition(":")
            now, _, why = after.partition(":")
            when = clock_label(page, int(now), entry.scope.world) if now.isdigit() else now
            if span_text == "set":
                return t("protocol.clock_set", when=when)
            spent = remaining(page, int(span_text)) if span_text.isdigit() else span_text
            if not why:
                return t("protocol.clock", span=spent, when=when)
            reason = t(f"clock.reason.{why}") if why in CLOCK_REASONS else why
            return t("protocol.clock_why", why=reason, span=spent, when=when)
        if head == "timer":
            timer, _, label = rest.partition(":")
            if timer == "custom":
                return t("protocol.timer.custom", name=label)
            categories = ("item", "baseitem") if timer == "light" else "spell"
            name = name_text(page, categories, label.partition("|")[0])
            return t(f"protocol.timer.{timer}", name=name, subject=entry.subject_name)
        if head == "light":
            return t(
                "protocol.light", item=name_text(page, ("item", "baseitem"), rest.partition("|")[0])
            )
        if head == "attune":
            ref, _, on = rest.rpartition(":")
            item = name_text(page, ("item", "baseitem"), ref.partition("|")[0])
            return t("protocol.attune_on" if on == "1" else "protocol.attune_off", item=item)
        if head in ("identify", "lift_curse"):
            item = name_text(page, ("item", "baseitem"), rest.partition("|")[0])
            return t(f"protocol.{head}", item=item)
        if head == "charge":
            # charge:<ref>:<spend|regain>:<n>:<left>
            ref, op, n, left = (rest.rsplit(":", 3) + ["", "", ""])[:4]
            item = name_text(page, ("item", "baseitem"), ref.partition("|")[0])
            key = "protocol.charge_regain" if op == "regain" else "protocol.charge_spend"
            return t(key, item=item, n=n, left=left)
        if head == "charges":
            # charges:<ref>:<n>:<left>, regained at dawn, dusk, midnight or a long rest
            ref, n, left = (rest.rsplit(":", 2) + ["", ""])[:3]
            item = name_text(page, ("item", "baseitem"), ref.partition("|")[0])
            return t("protocol.charges_back", item=item, n=n, left=left)
        if head == "use":
            return t(
                "protocol.use", item=name_text(page, ("item", "baseitem"), rest.partition("|")[0])
            )
        if head == "receive":
            items, _, purse = rest.partition(":")
            parts = []
            for part in filter(None, items.split(";")):
                ref, _, count = part.rpartition("*")
                name = (
                    t("protocol.unidentified_item")
                    if ref == "?"
                    else name_text(page, ("item", "baseitem"), ref.partition("|")[0])
                )
                parts.append(f"{name} ×{count}" if count not in ("", "1") else str(name))
            for coin in filter(None, purse.split(",")):
                amount, denomination = coin[:-2], coin[-2:]
                parts.append(f"{amount} {t(f'treasure.coin.{denomination}')}")
            return t("protocol.receive", what=", ".join(parts))
        if head in ("give", "drop"):
            # give:<ref>*<count> (to the entry's target), drop:<ref>*<count>
            ref, _, count = rest.rpartition("*")
            name = (
                t("protocol.unidentified_item")
                if ref == "?"
                else name_text(page, ("item", "baseitem"), ref.partition("|")[0])
            )
            what_text = f"{name} ×{count}" if count not in ("", "1") else str(name)
            return t(f"protocol.{head}", item=what_text)
        if head in ("buy", "sell"):
            item, _, purse = rest.partition(":")
            ref, _, count = item.rpartition("*")
            name = name_text(page, ("item", "baseitem"), ref.partition("|")[0])
            what_text = f"{name} ×{count}" if count not in ("", "1") else str(name)
            return t(f"protocol.{head}", item=what_text, coins=_purse_words(page, purse))
        if head == "spend":
            purse, _, reason = rest.partition(":")
            return t("protocol.spend", coins=_purse_words(page, purse))
        if head == "chase":
            return _chase_words(page, rest.split(":"))
        if head == "trap":
            return _trap_words(page, rest.split(":"))
        if head == "affliction":
            return _affliction_words(page, rest.split(":"))
        if head == "formula":
            step, _, ref = rest.partition(":")
            item = name_text(page, "item", ref.partition("|")[0])
            return t(f"protocol.formula_{step}", name=item)
        if head == "gift":
            step, _, more = rest.partition(":")
            if step == "granted":
                _kind, _, ref = more.partition(":")
                name = ref.partition("|")[0]
                return t("protocol.gift_granted", name=t.term("reward", name) or name)
            ref, _, tail = more.rpartition(":")
            name = ref.partition("|")[0]
            shown = t.term("reward", name) or name
            if step == "used":
                return t("protocol.gift_used", name=shown, left=tail)
            return t(f"protocol.gift_gone_{tail}", name=shown)
        if head == "scroll":
            used, spell_ref, kept, *check = [*rest.split(":"), ""][:5]
            spell_name = name_text(page, "spell", spell_ref.partition("|")[0])
            line = t(
                f"protocol.scroll_{used}" + ("" if kept == "1" else "_failed"), spell=spell_name
            )
            total, dc = (check + ["", ""])[:2]
            return f"{line} ({total} / {dc})" if total else line
        if head == "wages":
            days, paid, owed = ([*rest.split(":"), "", "", ""])[:3]
            wage_days = int(days) if days.isdigit() else 0
            paid_text = _purse_words(page, coins.text(int(paid) if paid.isdigit() else 0))
            if owed.isdigit() and int(owed):
                owed_text = _purse_words(page, coins.text(int(owed)))
                return t.plural(
                    "protocol.wages_owed", wage_days, days=days, coins=paid_text, owed=owed_text
                )
            return t.plural("protocol.wages", wage_days, days=days, coins=paid_text)
        if head == "quest":
            step, _, quest_name = rest.partition(":")
            return t(f"protocol.quest_{step}", name=quest_name)
        if head == "renown":
            faction, delta, total = ([*rest.split(":"), "", ""])[:3]
            return t("protocol.renown", faction=faction, delta=delta, total=total)
        if head == "session":
            return t(f"protocol.session_{rest}")
        if head == "contact":
            step, sort, more = ([*rest.split(":", 2), "", ""])[:3]
            kind_text = t(f"contacts.kind.{sort}")
            if step == "new":
                return t("protocol.contact_new", n=more, kind=kind_text)
            return t("protocol.contact_called", kind=kind_text, name=more).strip()
        if head == "lifestyle":
            level, days, paid, owed = (rest.split(":") + ["", "", "", ""])[:4]
            level_text = t(f"lifestyle.{level}").lower()
            day_count = int(days) if days.isdigit() else 0
            paid_text = _purse_words(page, coins.text(int(paid) if paid.isdigit() else 0))
            if owed.isdigit() and int(owed):
                owed_text = _purse_words(page, coins.text(int(owed)))
                return t.plural(
                    "protocol.lifestyle_owed",
                    day_count,
                    level=level_text,
                    days=days,
                    coins=paid_text,
                    owed=owed_text,
                )
            return t.plural(
                "protocol.lifestyle", day_count, level=level_text, days=days, coins=paid_text
            )
        if head == "learn":
            learned, _, ref = rest.partition(":")
            if learned == "spell":
                spell_name = name_text(page, "spell", ref.partition("|")[0])
                return t("protocol.learn_spell", name=spell_name)
            categories = "language" if learned == "language" else ("item", "baseitem")
            return t("protocol.learn", name=name_text(page, categories, ref.partition("|")[0]))
        if head == "travel":
            fields = rest.split(":")
            pace, hours, miles = (fields + ["", "", ""])[:3]
            where = fields[3] if len(fields) > 3 else ""
            hungry = fields[4] if len(fields) > 4 else ""
            thirsty = fields[5] if len(fields) > 5 else ""
            vessel = fields[6] if len(fields) > 6 else ""
            spent = remaining(page, int(hours) * 60) if hours.isdigit() else hours
            try:
                distance = page.u.miles(float(miles))
            except ValueError:
                distance = miles
            line = t(
                "protocol.travel",
                span=spent,
                distance=distance,
                pace=t("travel.aboard", name=vessel)
                if vessel
                else t(f"travel.pace.{pace}")
                if pace in PACES
                else pace,
            )
            if where:
                line += f" · {where}"
            if hungry:
                line += " · " + t("travel.hungry", names=hungry.replace(",", ", "))
            if thirsty:
                line += " · " + t("travel.thirsty", names=thirsty.replace(",", ", "))
            return line
        if head == "travel_encounter":
            return t("protocol.travel_encounter", row=rest) if rest else t("protocol.travel_check")
        if head == "upkeep":
            name, days, paid, owed = (rest.split(":") + ["", "", "", ""])[:4]
            line = t.plural(
                "protocol.upkeep",
                int(days) if days.isdigit() else 0,
                subject=entry.subject_name,
                name=name,
                days=days,
                paid=_purse_words(page, coins.text(int(paid) if paid.isdigit() else 0)),
            )
            if owed.isdigit() and int(owed):
                line += " · " + t(
                    "protocol.upkeep_owed", owed=_purse_words(page, coins.text(int(owed)))
                )
            return line
        if head == "wild_zone":
            return t("protocol.wild_zone_on" if rest == "1" else "protocol.wild_zone_off")
        if head == "wild_surge":
            roll, _, rest = rest.partition(":")
            cause, _, text = rest.partition(":")
            return t(
                "protocol.wild_surge_zone" if cause == "zone" else "protocol.wild_surge",
                subject=entry.subject_name,
                roll=roll,
                effect=text,
            )
        if head == "sentient":
            # sentient:conflict:<item>:<item's check>:<bearer's roll>:<DC>:<item won>
            _op, item, mine, theirs, dc, won = (rest.split(":") + [""] * 6)[:6]
            key = "contest" if mine else "save"
            return t(
                f"protocol.sentient_{key}_{'won' if won == '1' else 'lost'}",
                subject=entry.subject_name,
                item=item,
                mine=mine,
                theirs=theirs,
                dc=dc,
            )
        if head == "upkeep_new":
            name, _, cost = rest.rpartition(":")
            return t(
                "protocol.upkeep_new",
                subject=entry.subject_name,
                name=name,
                cost=_purse_words(page, coins.text(int(cost) if cost.isdigit() else 0)),
            )
        if head == "intervention":
            roll, level, yes, deity = (rest.split(":") + ["", "", "", ""])[:4]
            return t(
                "protocol.intervention_yes" if yes == "1" else "protocol.intervention_no",
                subject=entry.subject_name,
                deity=name_text(page, "deity", deity) if deity else t("intervention.god"),
                roll=roll,
                level=level,
            )
        if head == "intervention_cast":
            spell, deity, waits = (rest.split(":") + ["", "", ""])[:3]
            line = t(
                "protocol.intervention_cast",
                subject=entry.subject_name,
                deity=name_text(page, "deity", deity) if deity else t("intervention.god"),
                spell=name_text(page, "spell", spell.partition("|")[0]),
            )
            if waits.isdigit():
                line += " · " + t.plural("intervention.rests", int(waits), n=waits)
            return line
        if head == "delve_encounter":
            return t("protocol.delve_encounter", row=rest) if rest else t("protocol.delve_check")
        if head == "delve":
            step, _, more = rest.partition(":")
            if step == "stop":
                return t.plural("protocol.delve_stop", int(more) if more.isdigit() else 0, n=more)
            every, _, table = more.partition(":")
            span_text = remaining(page, int(every)) if every.isdigit() else every
            if table:
                return t("protocol.delve_start_table", span=span_text, table=table)
            return t("protocol.delve_start", span=span_text)
        if head == "downtime":
            return t.plural("protocol.downtime", int(rest) if rest.isdigit() else 0, n=rest)
        if head in ("downtime_start", "downtime_done"):
            job, _, label = rest.partition(":")
            return t(
                f"protocol.{head}",
                kind=t(f"downtime.kind.{job}"),
                what=_project_words(page, job, label),
            )
        if head == "downtime_spent":
            job, _, more = rest.partition(":")
            days, _, label = more.partition(":")
            return t.plural(
                "protocol.downtime_spent",
                int(days) if days.isdigit() else 0,
                n=days,
                kind=t(f"downtime.kind.{job}"),
                what=_project_words(page, job, label),
            )
        if head == "shop":
            op, _, name = rest.partition(":")
            return t(
                f"protocol.shop_{'open' if op == 'open' else 'close'}",
                name=name or t("shop.heading"),
            )
        if head == "bastion_defenders":
            change, _, count = rest.partition(":")
            key = (
                "protocol.defenders_up" if not change.startswith("-") else "protocol.defenders_down"
            )
            return t(key, n=change.lstrip("-+"), total=count, subject=entry.subject_name)
        if head == "bastion_event":
            name, _, lost = rest.partition(":")
            if lost not in ("", "0"):
                return t(
                    "protocol.bastion_event_lost", subject=entry.subject_name, name=name, n=lost
                )
            return t("protocol.bastion_event", subject=entry.subject_name, name=name)
        if head in ("wild_shape", "polymorph"):
            on, _, form = rest.partition(":")
            name = name_text(page, "monster", form.partition("|")[0])
            return t(f"protocol.{head}_{on}", form=name)
    return what


# The reasons the clock is moved for that have words of their own.
CLOCK_REASONS = ("short_rest", "long_rest", "fight", "travel", "downtime")


def _chase_words(page: Page, fields: list[str]) -> str:
    """A step of a chase (app/services/chase.py) in the reader's words."""
    t = page.t
    step, *rest = fields + [""]
    rest = rest + [""] * 5
    if step == "start":
        feet = int(rest[1]) if rest[1].isdigit() else 0
        return t(
            "protocol.chase_start",
            where=t(f"chase.where.{rest[0]}"),
            distance=page.u.distance(feet),
        )
    if step == "dash":
        name, n, total, kept, out = rest[:5]
        if not total:
            return t("protocol.chase_dash", name=name, n=n)
        if out:
            return t("protocol.chase_dash_out", name=name, n=n, total=total)
        key = "protocol.chase_dash_roll" if kept == "1" else "protocol.chase_dash_tired"
        return t(key, name=name, n=n, total=total)
    if step == "complication":
        return t("protocol.chase_complication", name=rest[0], text=rest[2])
    if step == "escape":
        name, total, dc, escaped = rest[:4]
        if total == "-":
            return t("protocol.chase_seen", name=name)
        key = "protocol.chase_escape" if escaped == "1" else "protocol.chase_stays"
        return t(key, name=name, total=total, dc=dc)
    if step == "end":
        return t("protocol.chase_end", outcome=t(f"chase.over.{rest[0] or 'stopped'}"))
    return ":".join(fields)


def _trap_words(page: Page, fields: list[str]) -> str:
    """A trap sprung (app/services/traps.py), and what it did to each character."""
    t = page.t
    step, *rest = fields + [""] * 9
    if step == "sprung":
        return t("protocol.trap_sprung", name=rest[0])
    name, roll, against, failed, damage, kind, conditions = rest[:7]
    if step == "hit":  # damage with no save or attack
        word = t(f"rules.damage.{kind}") if kind else ""
        word = kind if word == f"rules.damage.{kind}" else word
        return t(
            "protocol.trap_target",
            name=name,
            result=t("trap.result_damage", amount=damage, type=word).strip(),
        )
    result = t(
        "trap.result_hit" if failed == "1" else "trap.result_safe",
        roll=roll if roll not in ("", "None") else "—",
        against=against if against not in ("", "None") else "—",
    )
    if damage.isdigit() and int(damage):
        word = t(f"rules.damage.{kind}") if kind else ""
        word = kind if word == f"rules.damage.{kind}" else word
        result += f" · {t('trap.result_damage', amount=damage, type=word)}"
    if conditions:
        result += " · " + ", ".join(t(f"condition.{c}") for c in conditions.split(","))
    return t("protocol.trap_target", name=name, result=result)


def _affliction_words(page: Page, fields: list[str]) -> str:
    """A disease, poison or curse caught, showing, saved against or ended
    (app/services/afflictions.py)."""
    t = page.t
    step, *rest = fields + [""] * 7
    if step == "caught":
        kind, name, roll, dc, caught, damage = rest[:6]
        key = "protocol.affliction_caught" if caught == "1" else "protocol.affliction_resisted"
        line = t(key, kind=t(f"affliction.kind.{kind}"), name=name)
        if roll:
            key = "affliction.failed_roll" if caught == "1" else "affliction.saved_roll"
            line += " · " + t(key, roll=roll, dc=dc)
        if damage.isdigit() and int(damage):
            line += " · " + t("trap.result_damage", amount=damage, type="")
        return line.strip()
    if step == "onset":
        return t("protocol.affliction_onset", kind=t(f"affliction.kind.{rest[0]}"), name=rest[1])
    if step == "save":
        name, roll, dc, ok, outcome = rest[:5]
        line = t(
            "protocol.affliction_save",
            name=name,
            result=t(
                "affliction.saved_roll" if ok == "1" else "affliction.failed_roll", roll=roll, dc=dc
            ),
        )
        return f"{line} · {t(f'affliction.outcome.{outcome}')}" if outcome else line
    if step == "ended":
        kind, name, why = rest[:3]
        return t(
            f"protocol.affliction_ended_{why or 'cured'}",
            kind=t(f"affliction.kind.{kind}"),
            name=name,
        )
    if step == "curse":
        return t("protocol.affliction_curse", name=rest[0])
    return ":".join(fields)


def _purse_words(page: Page, purse: str) -> str:
    """``"12gp,5sp"`` in the reader's coin names."""
    parts = []
    for coin in filter(None, purse.split(",")):
        amount, denomination = coin[:-2], coin[-2:]
        parts.append(f"{amount} {page.t(f'treasure.coin.{denomination}')}")
    return " ".join(parts) or f"0 {page.t('treasure.coin.gp')}"


def _project_words(page: Page, kind: str, label: str) -> str:
    """What a downtime project works on, named for the reader."""
    if kind == "craft":
        return str(name_text(page, ("item", "baseitem"), label.partition("|")[0]))
    if kind == "train":
        what, _, ref = label.partition(":")
        categories = "language" if what == "language" else ("item", "baseitem")
        return str(name_text(page, categories, ref.partition("|")[0]))
    if kind == "scribe":
        return str(name_text(page, "spell", label.partition(";")[2].partition("|")[0]))
    if kind in ("sell_magic", "enchant"):
        return str(name_text(page, "item", label.partition("|")[0]))
    if kind == "carouse":
        return page.t(f"downtime.class_name.{label}")
    if kind == "gamble" and label.isdigit():
        gold, rest = divmod(int(label), 100)
        return _purse_words(page, f"{gold}gp" + (f",{rest}cp" if rest else ""))
    return label


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
    if head == "proficiency_die" and rest:  # the proficiency dice variant: "Prof 1d6"
        return page.t("protocol.bonus.proficiency_die", dice=rest)
    return _word(page, f"protocol.bonus.{head}", head)


def reason_word(page: Page, reason: str) -> str:
    """Why a roll had advantage or disadvantage: "the switch", "Poisoned"."""
    head, _, rest = reason.partition(":")
    if head == "condition" and rest == "encumbered":  # variant encumbrance, not a condition
        return _word(page, "protocol.reason.encumbered", rest)
    if head == "condition" and rest:
        return _word(page, f"condition.{rest}", rest)
    return _word(page, f"protocol.reason.{head}", head)


def rule_text(page: Page, subject: str, detail: str, amount: int | None, outcome: str) -> str:
    """An optional combat rule at work (app/services/combat_rules.py), from what the
    tracker logged: ``sides:<friend>:<foe>``, ``massive:<dc>``, ``shock:<text>``,
    ``morale:<dc>``, ``injury:<name>``, ``mob:<n>:<per hit>:<hits>:<action>→<target>``.
    ``amount`` is the roll (a mob's damage)."""
    t = page.t
    head, _, rest = detail.partition(":")
    made = outcome == "success"
    if head == "sides":
        friend, _, foe = rest.partition(":")
        return t("combat.rule.sides", friend=friend, foe=foe)
    if head in ("massive", "morale"):
        key = f"combat.rule.{head}_{'made' if made else 'failed'}"
        return t(key, subject=subject, amount=amount if amount is not None else "—", dc=rest)
    if head == "shock":
        return t("combat.rule.shock", subject=subject, amount=amount or 0, text=rest)
    if head == "injury":
        return t("combat.rule.injury", subject=subject, amount=amount or 0, name=rest)
    if head == "mob":
        n, per, hits, aimed = (rest.split(":", 3) + ["", "", ""])[:4]
        action, _, target = aimed.rpartition("\u2192")
        if per in ("", "0"):
            return t("combat.rule.mob_none", subject=subject, n=n, target=target, action=action)
        return t(
            "combat.rule.mob",
            subject=subject,
            n=n,
            target=target,
            action=action,
            per=per,
            hits=hits,
            damage=amount or 0,
        )
    # The variant rules for characters (app/services/character_rules.py), from the sheet.
    if head == "hero":
        total, _, _what = rest.partition(":")
        return t("rule.hero", subject=subject, die=amount or 0, total=total)
    if head in ("hero_death", "defeated", "wake"):
        return t(f"rule.{head}", subject=subject)
    if head == "bandage":
        return t("rule.bandage", subject=subject, left=rest)
    if head == "awakening":
        key = "rule.awakening_made" if made else "rule.awakening_failed"
        return t(key, subject=subject, dc=rest)
    if head == "score":
        key, old, new = (rest.split(":") + ["", ""])[:3]
        ability = t(f"ability.{key}")
        if old:
            return t("rule.score_moved", subject=subject, ability=ability, old=old, new=new)
        return t("rule.score_set", subject=subject, ability=ability, new=new)
    return detail


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
