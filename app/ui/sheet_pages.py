"""The character sheet screen (design-reference/16-character-sheet.html).

One fragment, ``#sheet``, rendered whole — by the page, by every action, and on every
live update — so there is exactly one way the sheet can look.

Layout by width, as the reference specifies:

* **Desktop (≥1024px)** — three columns: who you are on the left, what you do in the
  middle (tabs), what you spend on the right.
* **iPad (768–1023px)** — two columns; the right-hand column folds under the left.
* **Phone (<768px)** — one column and a bottom bar of four views: *Play* (HP, attacks,
  spells), *Stats*, *Track* (slots, resources, conditions, rests), *Gear*. The fifth,
  *Log*, is a button in the top bar, in reach from every view.

Every control is a real ``<form>``: without JavaScript it posts and the page reloads;
with HTMX it posts and swaps ``#sheet`` in place. Tabs are links (``?tab=spells``), so
they work without JavaScript too; ``sheet.js`` only makes them instant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from htpy import (
    Element,
    Node,
    a,
    aside,
    button,
    details,
    div,
    fieldset,
    form,
    h2,
    h3,
    header,
    label,
    legend,
    nav,
    option,
    p,
    section,
    select,
    span,
    summary,
)
from htpy import input as input_el

from app.models.gamestate import SourceSet
from app.models.pc import DraftStatus
from app.services.condition_rolls import affected
from app.services.experience import Progress, progress
from app.services.protocol_view import Filter
from app.services.sheet import (
    DAMAGE_TYPES,
    AbilityView,
    AttackView,
    ConditionView,
    FeatureView,
    ItemView,
    ResourceView,
    Sheet,
    SkillView,
    SlotLevel,
    SpellView,
    Value,
)
from app.services.translations import COMPOSITE
from app.ui import rules_text
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import ITEM, name_span, name_text, origin_text, training_text
from app.ui.names import summary as summary_line
from app.ui.protocol_pages import log_embed_slot
from app.ui.tags import RenderContext, localized_context, plain, ref_url, render_entries
from app.ui.tokens import token, token_panel
from app.ui.translate_pages import translate_url
from app.ui.translated import localized_data

DESKTOP_TABS = ("actions", "spells", "skills", "gear", "features")
MOBILE_TABS = (
    ("play", "heart"),
    ("stats", "target"),
    ("track", "sparkles"),
    ("gear", "backpack"),
)
# Every phone view: the bottom bar's, and the log, opened from the top bar.
MOBILE_VIEWS = frozenset({v for v, _ in MOBILE_TABS} | {"log"})
# Which phone view each desktop pane belongs to.
PANE_VIEW = {
    "actions": "play",
    "spells": "play",
    "skills": "stats",
    "gear": "gear",
    "features": "gear",
}


# A feature's kind → the category its name is filed under in the glossary.
FEATURE_CATEGORY = {
    "feat": "feat",
    "option": "optionalfeature",
    "class": "classFeature",
    "subclass": "subclassFeature",
}


def _url(sheet: Sheet, suffix: str = "") -> str:
    return f"/characters/{sheet.character.id}{suffix}"


def act(
    sheet: Sheet,
    action: str,
    params: Mapping[str, str | int],
    content: Node,
    *,
    cls: str = "btn btn-sm",
    title: str | None = None,
    label: str | None = None,
    pressed: bool | None = None,
) -> Element:
    """One sheet action as a form: posts normally, or swaps ``#sheet`` via HTMX."""
    url = _url(sheet, f"/act/{action}")
    return form(method="post", action=url, hx_post=url, class_="cs-act")[
        [hidden(k, str(v)) for k, v in params.items()],
        button(
            type="submit",
            class_=cls,
            title=title,
            aria_label=label,
            aria_pressed=None if pressed is None else ("true" if pressed else "false"),
        )[content],
    ]


def _value_title(page: Page, value: Value) -> str | None:
    if value.overridden:
        note = f": {value.note}" if value.note else ""
        return page.t("sheet.overridden") + note
    return page.u.text(value.explanation) or None


def speed_text(page: Page, value: Value) -> str:
    """A speed in the reader's units: ``30 ft.`` or ``9 m``."""
    return "—" if value.value is None else page.u.distance(value.value)


def _stat(
    page: Page,
    key: str,
    value: Value,
    *,
    accent: str = "",
    signed: bool = False,
    text: str | None = None,
) -> Element:
    return div(
        class_=f"cs-stat {accent} {'is-override' if value.overridden else ''}".strip(),
        title=_value_title(page, value),
    )[
        span(class_="k")[page.t(key)],
        span(class_="v mono")[text if text is not None else value.signed if signed else value.text],
    ]


# -- left column: who you are ----------------------------------------------------------


def _hp_box(page: Page, sheet: Sheet) -> Element:
    t = page.t
    top = sheet.hp_max.value
    ratio = sheet.hp_ratio
    # The sheet colours "down" like "crit"; the HP box has no separate dying style.
    state = {"down": "crit"}.get(sheet.hp_state, sheet.hp_state)
    amount_form = form(
        method="post",
        action=_url(sheet, "/act/hp"),
        hx_post=_url(sheet, "/act/hp"),
        class_="cs-amount",
    )[
        input_el(
            type="number",
            name="amount",
            min="1",
            max="999",
            inputmode="numeric",
            aria_label=t("sheet.amount"),
            placeholder="0",
            class_="mono",
        ),
        button(type="submit", name="mode", value="damage", class_="btn btn-sm btn-dmg")[
            t("sheet.damage")
        ],
        button(type="submit", name="mode", value="heal", class_="btn btn-sm btn-heal")[
            t("sheet.heal")
        ],
        button(type="submit", name="mode", value="temp", class_="btn btn-sm")[t("sheet.temp")],
    ]
    quick = div(class_="cs-hpbtns")[
        act(
            sheet,
            "hp",
            {"mode": "damage", "amount": 5},
            "−5",
            cls="btn btn-dmg",
            label=t("sheet.damage_n", n=5),
        ),
        act(
            sheet,
            "hp",
            {"mode": "damage", "amount": 1},
            "−1",
            cls="btn btn-dmg",
            label=t("sheet.damage_n", n=1),
        ),
        act(
            sheet,
            "hp",
            {"mode": "heal", "amount": 1},
            "+1",
            cls="btn btn-heal",
            label=t("sheet.heal_n", n=1),
        ),
        act(
            sheet,
            "hp",
            {"mode": "heal", "amount": 5},
            "+5",
            cls="btn btn-heal",
            label=t("sheet.heal_n", n=5),
        ),
    ]
    death = None
    if sheet.is_dying:
        saves = sheet.character.state.death_saves
        death = div(class_="cs-death")[
            span(class_="eyebrow")[t("sheet.death_saves")],
            div(class_="cs-death-row")[
                span[t("sheet.successes")],
                span(class_="cs-pips")[
                    [span(class_=f"pip ok {'on' if i < saves.successes else ''}") for i in range(3)]
                ],
            ],
            div(class_="cs-death-row")[
                span[t("sheet.failures")],
                span(class_="cs-pips")[
                    [span(class_=f"pip bad {'on' if i < saves.failures else ''}") for i in range(3)]
                ],
            ],
            act(
                sheet,
                "death_save",
                {},
                [icon("dices"), span[t("sheet.roll_death_save")]],
                cls="btn btn-sm",
            ),
        ]
    hit_dice = div(class_="cs-hitdice")[
        [
            act(
                sheet,
                "hit_die",
                {"faces": pool.faces},
                [
                    span(class_="mono")[rules_text.dice(page, f"d{pool.faces}")],
                    span(class_="mono")[f"{pool.available}/{pool.total}"],
                ],
                cls="btn btn-sm",
                title=t("sheet.spend_hit_die"),
            )
            for pool in sheet.hit_dice
        ]
    ]
    return div(class_="cs-hpbox", data_m="play stats")[
        div(class_="cs-hprow", title=_value_title(page, sheet.hp_max))[
            span(class_=f"cs-hpnow mono {state}")[sheet.hp_text],
            span(class_="cs-hpmax mono")[f"/ {top if top is not None else '—'}"],
            span(class_="pill heal")[t("sheet.temp_hp", n=sheet.temp_hp)]
            if sheet.temp_hp
            else None,
        ],
        div(class_="hpbar")[span(class_=f"hpfill {state}", style=f"width:{ratio * 100:.0f}%")],
        quick,
        amount_form,
        death,
        div(class_="cs-hd-line")[span(class_="eyebrow")[t("sheet.hit_dice")], hit_dice]
        if sheet.hit_dice
        else None,
    ]


def _ability(page: Page, sheet: Sheet, ability: AbilityView) -> Element:
    return act(
        sheet,
        "roll",
        {"kind": "check", "subject": ability.key},
        [
            span(class_="k")[page.t(f"abbr.{ability.key}")],
            span(class_="m mono")[f"{ability.modifier:+d}"],
            span(class_="s mono")[ability.score.text],
        ],
        cls=f"cs-ab {'is-override' if ability.score.overridden else ''}",
        title=f"{page.t(f'ability.{ability.key}')} · {page.t('sheet.roll_check')}",
    )


def _save_row(page: Page, sheet: Sheet, ability: AbilityView) -> Element:
    return act(
        sheet,
        "roll",
        {"kind": "save", "subject": ability.key},
        [
            span(class_=f"dot {'prof' if ability.save_proficient else ''}"),
            span(class_="nm")[page.t(f"ability.{ability.key}")],
            span(class_="md mono")[ability.save.signed],
        ],
        cls=f"cs-row {'is-override' if ability.save.overridden else ''}",
        title=_value_title(page, ability.save),
    )


def _left(page: Page, sheet: Sheet) -> Element:
    t = page.t
    first = (
        _stat(page, "sheet.spell_dc", sheet.spell_save_dc, accent="magic")
        if sheet.spell_save_dc is not None
        else _stat(page, "sheet.proficiency", sheet.proficiency, signed=True)
    )
    return aside(class_="cs-left")[
        div(class_="cs-stats", data_m="stats")[
            _stat(page, "sheet.ac", sheet.armor_class, accent="accent"),
            first,
            _stat(page, "sheet.initiative", sheet.initiative, signed=True),
            _stat(page, "sheet.speed", sheet.speed, text=speed_text(page, sheet.speed)),
        ],
        _hp_box(page, sheet),
        section(class_="cs-block", data_m="stats")[
            h3(class_="eyebrow")[t("sheet.abilities")],
            div(class_="cs-ab6")[[_ability(page, sheet, ab) for ab in sheet.abilities]],
        ],
        section(class_="cs-block", data_m="stats")[
            h3(class_="eyebrow")[t("sheet.saves")],
            div(class_="cs-rows")[[_save_row(page, sheet, ab) for ab in sheet.abilities]],
        ],
    ]


# -- middle: what you do ---------------------------------------------------------------


def _attack_row(page: Page, sheet: Sheet, attack: AttackView) -> Element:
    t = page.t
    world = sheet.character.world_id
    properties: list[Node] = [
        a(href=ref_url("itemProperty", code, source, world), class_="ref rule")[
            rules_text.property_word(page, word)
        ]
        for word, (code, source) in zip(attack.properties, attack.property_refs, strict=True)
    ] or [rules_text.property_word(page, p) for p in attack.properties]
    meta: list[Node] = [
        rules_text.damage_word(page, attack.damage_type) if attack.damage_type else "",
        *properties,
    ]
    if attack.range:
        meta.append(page.u.text(f"{attack.range} ft."))
    mastery: Node = None
    if sheet.masters(attack):
        name, _, source = attack.mastery.partition("|")
        mastery = a(
            href=ref_url("itemMastery", name, source, sheet.character.world_id),
            class_="ref rule cs-mastery",
            title=page.t("sheet.mastery_title"),
        )[name_text(page, "itemMastery", name)]
    return div(class_="cs-item")[
        div(class_="cs-item-main")[
            name_span(page, ITEM, attack.name),
            span(class_="meta")[[span[m] for m in meta if m], mastery],
        ],
        div(class_="cs-item-act")[
            act(
                sheet,
                "roll",
                {"kind": "attack", "subject": attack.name},
                [icon("target"), span(class_="mono")[attack.bonus.signed]],
                cls="roll",
                title=attack.bonus.explanation or t("sheet.attack_roll"),
            ),
            act(
                sheet,
                "roll",
                {"kind": "damage", "subject": attack.name},
                [icon("dices"), span(class_="mono")[rules_text.dice(page, attack.damage)]],
                cls="roll",
                title=t("sheet.damage_roll"),
            ),
            act(
                sheet,
                "roll",
                {"kind": "damage", "subject": attack.name, "versatile": "1"},
                [icon("dices"), span(class_="mono")[attack.versatile]],
                cls="roll",
                title=t("sheet.versatile"),
            )
            if attack.versatile
            else None,
        ],
    ]


def _actions_pane(page: Page, sheet: Sheet) -> Element:
    t = page.t
    custom = form(
        method="post",
        action=_url(sheet, "/act/roll"),
        hx_post=_url(sheet, "/act/roll"),
        class_="cs-custom",
    )[
        hidden("kind", "custom"),
        input_el(
            type="text",
            name="expr",
            placeholder="2d6+3",
            aria_label=t("sheet.custom_roll"),
            autocomplete="off",
            spellcheck="false",
            class_="mono",
        ),
        button(type="submit", class_="btn btn-sm")[icon("dices"), span[t("sheet.roll")]],
    ]
    return section(class_="cs-pane", data_tab="actions", data_m="play")[
        div(class_="cs-pane-head")[
            h2(class_="eyebrow")[t("sheet.attacks")],
            act(
                sheet,
                "roll",
                {"kind": "initiative"},
                [icon("swords"), span[t("sheet.roll_initiative")]],
                cls="btn btn-sm",
            ),
        ],
        _bent_rolls(page, sheet),
        _weapon_master(page, sheet),
        div(class_="cs-list")[[_attack_row(page, sheet, atk) for atk in sheet.attacks]],
        h2(class_="eyebrow cs-sub")[t("sheet.custom_roll")],
        custom,
    ]


def _weapon_master(page: Page, sheet: Sheet) -> Node:
    """Weapon Master (2024): which kind of weapon's mastery the character uses — chosen
    here, and again after a long rest."""
    if not sheet.weapon_master:
        return None
    t = page.t
    if not sheet.mastery_open:
        return p(class_="cs-note muted")[
            t("sheet.weapon_master_set", kind=name_text(page, ITEM, sheet.mastery_kind))
        ]
    url = _url(sheet, "/act/mastery")
    return form(method="post", action=url, hx_post=url, class_="cs-act cs-mastery-pick")[
        label(for_="cs-mastery-kind")[t("sheet.weapon_master")],
        select(name="kind", id="cs-mastery-kind")[
            [
                option(value=kind, selected=kind == sheet.mastery_kind)[name_text(page, ITEM, kind)]
                for kind in sheet.mastery_choices
            ]
        ],
        button(type="submit", class_="btn btn-sm")[t("sheet.weapon_master_choose")],
    ]


def _spell_tags(page: Page, sheet: Sheet, spell: SpellView) -> list[Node]:
    t = page.t
    tags: list[Node] = []
    if spell.concentration:
        tags.append(span(class_="tag cond")[t("sheet.conc")])
    if spell.ritual:
        tags.append(span(class_="tag")[t("sheet.ritual")])
    dc = sheet.save_dc_for(spell)
    if spell.save and dc is not None:
        tags.append(span(class_="tag heal")[t("sheet.save_dc", ability=spell.save, dc=dc.text)])
    tags.extend(span(class_="tag dmg")[rules_text.damage_word(page, d)] for d in spell.damage_types)
    if spell.always:
        tags.append(span(class_="tag", title=t("sheet.always_title"))[t("sheet.always")])
    if spell.origin:
        # Granted by a feat, species or background, cast with its own ability.
        origin = name_text(page, ("feat", "race", "subrace", "background"), spell.origin)
        ability = t(f"abbr.{spell.ability}") if spell.ability else ""
        tags.append(
            span(class_="tag magic", title=t("sheet.granted_title", origin=origin))[
                " · ".join(x for x in (origin, ability) if x)
            ]
        )
    return tags


def _roll_form_id(sheet: Sheet) -> str:
    return f"roll-{sheet.character.id}"


def _text(
    page: Page, entries: tuple[object, ...], fallback: tuple[str, ...], ctx: RenderContext | None
) -> Node:
    """Rules text through the tag renderer — links, roll chips — or plain when it can't."""
    if entries and ctx is not None:
        return render_entries(list(entries), ctx)
    return [p[para] for para in fallback]


def _spell_entries(page: Page, spell: SpellView) -> tuple[object, ...]:
    """A spell's text in the reader's language where translated (TRANSLATION.md §5)."""
    if page.lang == "en" or not spell.entries or not spell.source:
        return spell.entries
    split = spell.higher_from or len(spell.entries)
    data = {
        "entries": list(spell.entries[:split]),
        "entriesHigherLevel": list(spell.entries[split:]),
    }
    shown, _coverage = localized_data(page, "spell", spell.name, spell.source, data)
    return (*shown["entries"], *shown["entriesHigherLevel"])


def _cast_form(page: Page, sheet: Sheet, spell: SpellView) -> Element:
    """Damage, "Call for saves" and Cast in one form, so all three use the slot chosen —
    a leveled spell with more than one slot level open gets the choice (upcasting)."""
    t = page.t
    roll_url, cast_url = _url(sheet, "/act/roll"), _url(sheet, "/act/cast")
    choices: list[tuple[str, str]] = [
        (str(n), t("rules.spell_level", level=n)) for n in sheet.castable(spell)
    ]
    free = _free_castings(sheet, spell)
    if free is not None:
        # A granted spell's casting without a slot comes first: it is what it is for.
        choices.insert(
            0, ("free", t("sheet.cast_will") if free < 0 else t("sheet.cast_free", n=free))
        )
    slot: Node = (
        select(name="level", class_="cs-slot-pick", aria_label=t("sheet.slot_level"))[
            [option(value=value)[shown] for value, shown in choices]
        ]
        if len(choices) > 1
        else hidden("level", choices[0][0] if choices else str(spell.level))
    )

    def go(url: str, content: Node, title: str, cls: str = "roll magic") -> Element:
        return button(type="submit", formaction=url, hx_post=url, class_=cls, title=title)[content]

    heal: Node = None
    if spell.heal and sheet.allies:
        whom: Node = select(
            name="target", class_="cs-slot-pick", aria_label=t("sheet.heal_target")
        )[[option(value=cid)[name] for cid, name in sheet.allies]]
        if spell.heal_targets > 1:
            # Mass Cure Wounds: up to six — one box per character, the caster ticked.
            whom = fieldset(class_="cs-heal-group")[
                legend(class_="muted")[t("sheet.heal_up_to", n=spell.heal_targets)],
                [
                    label(class_="cs-heal-pick")[
                        input_el(type="checkbox", name="target", value=cid, checked=n == 0),
                        span[name],
                    ]
                    for n, (cid, name) in enumerate(sheet.allies)
                ],
            ]
        heal = [
            whom,
            go(
                _url(sheet, "/heal"),
                [icon("heart", "sm"), span[t("sheet.heal_cast")]],
                t("sheet.heal_title"),
                cls="roll heal",
            ),
        ]
    fight: Node = None
    dc = sheet.save_dc_for(spell)
    if sheet.in_fight and spell.save and dc is not None:
        fight = go(
            _url(sheet, "/fight-spell"),
            [icon("shield-check"), span[t("sheet.spell_saves")]],
            t("sheet.spell_saves_title", dc=dc.text),
        )
    return form(method="post", action=cast_url, hx_post=cast_url, class_="cs-act cs-cast")[
        hidden("spell", spell.reference),
        hidden("subject", spell.reference),
        hidden("kind", "damage"),
        slot,
        go(
            roll_url,
            [icon("dices"), span(class_="mono")[rules_text.dice(page, spell.damage)]],
            t("sheet.damage_roll"),
        )
        if spell.damage
        else None,
        fight,
        heal,
        go(
            cast_url,
            [icon("sparkles"), span[t("sheet.cast")]] if spell.level else t("sheet.cast"),
            t("sheet.cast_title", name=name_text(page, "spell", spell.name)),
        ),
    ]


def _free_castings(sheet: Sheet, spell: SpellView) -> int | None:
    """How many castings without a slot a granted spell has left: -1 at will, None for
    none (a class spell, or the uses spent)."""
    if spell.free == "will":
        return -1
    resource = next((r for r in sheet.resources if spell.free and r.name == spell.free), None)
    return resource.available if resource is not None and resource.available else None


def _spell_row(
    page: Page, sheet: Sheet, spell: SpellView, ctx: RenderContext | None = None
) -> Element:
    t = page.t
    level_label = (
        t("sheet.cantrip") if spell.level == 0 else t("rules.spell_level", level=spell.level)
    )
    if not spell.found:
        return div(class_="cs-spell missing", data_level=str(spell.level))[
            div(class_="cs-item-main")[
                name_span(page, "spell", spell.name),
                span(class_="meta")[
                    icon("triangle-alert"), span[t("sheet.not_in_books", ref=spell.reference)]
                ],
            ],
        ]
    held = sheet.concentration is not None and sheet.concentration.name == spell.name
    buttons: list[Node] = []
    attack = sheet.attack_for(spell)
    if spell.attack and attack is not None:
        buttons.append(
            act(
                sheet,
                "roll",
                {"kind": "attack", "subject": spell.reference},
                [icon("target"), span(class_="mono")[attack.signed]],
                cls="roll",
                title=t("sheet.attack_roll"),
            )
        )
    buttons.append(_cast_form(page, sheet, spell))
    meta = [
        f"{level_label} {rules_text.school(page, spell.school_code) or spell.school}".strip(),
        rules_text.spell_time(page, spell),
        rules_text.spell_range(page, spell),
        spell.components,
    ]
    return details(
        class_=f"cs-spell {'held' if held else ''}",
        data_level=str(spell.level),
        data_key=f"spell:{spell.reference}",
    )[
        summary[
            div(class_="cs-item-main")[
                name_span(page, "spell", spell.name),
                span(class_="meta")[[span[m] for m in meta if m], _spell_tags(page, sheet, spell)],
            ],
            div(class_="cs-item-act")[buttons],
        ],
        div(class_="cs-spell-body")[
            p(class_="cs-spell-facts")[
                " · ".join(
                    x
                    for x in (
                        rules_text.spell_duration(page, spell),
                        f"M: {spell.material}" if spell.material else "",
                        spell.source or "",
                    )
                    if x
                )
            ],
            _text(page, _spell_entries(page, spell), spell.text, ctx),
            a(
                href=ref_url("spell", spell.name, spell.source or "-", sheet.character.world_id),
                class_="cs-open-ref",
            )[icon("book-open"), span[page.t("ref.open")]],
        ],
    ]


def _spells_pane(page: Page, sheet: Sheet, ctx: RenderContext | None = None) -> Element:
    t = page.t
    levels = sorted({s.level for s in sheet.spells})
    return section(class_="cs-pane", data_tab="spells", data_m="play")[
        div(class_="cs-pane-head")[
            div(class_="cs-filter", role="group", aria_label=t("sheet.filter_level"))[
                button(type="button", class_="fchip", data_level="", aria_pressed="true")[
                    t("sheet.all")
                ],
                [
                    button(type="button", class_="fchip", data_level=str(lv), aria_pressed="false")[
                        t("sheet.cantrip") if lv == 0 else f"L{lv}"
                    ]
                    for lv in levels
                ],
            ],
            _advantage(page),
        ],
        _bent_rolls(page, sheet),
        _prepare_strip(page, sheet),
        div(class_="cs-list")[[_spell_row(page, sheet, s, ctx) for s in sheet.spells]]
        if sheet.spells
        else p(class_="cs-empty")[t("sheet.no_spells")],
    ]


def _prepare_strip(page: Page, sheet: Sheet) -> Node:
    """How many spells are prepared, and the way to change them after a long rest."""
    if not sheet.preparing:
        return None
    t = page.t
    short = sheet.prepared_count < sheet.prepare_limit
    return div(class_="cs-prepare")[
        span(class_="cs-prepare-count" + (" short" if short else ""))[
            t("sheet.prepared_n", n=sheet.prepared_count, limit=sheet.prepare_limit)
        ],
        a(href=f"/characters/{sheet.character.id}/prepare", class_="btn btn-sm")[
            icon("book-open", "sm"), span[t("sheet.prepare")]
        ],
    ]


def _bent_rolls(page: Page, sheet: Sheet) -> Node:
    """What the character's conditions do to its rolls, beside the advantage switch."""
    t = page.t
    lines = []
    for condition, effects in affected(c.key for c in sheet.conditions if c.active).items():
        parts = [
            t(
                "sheet.bent_adv" if direction > 0 else "sheet.bent_dis",
                what=t(f"sheet.bent_kind.{kind.replace(':', '_')}"),
            )
            for kind, direction in effects
        ]
        lines.append(f"{t(f'condition.{condition}')}: {', '.join(parts)}")
    level = sheet.character.state.exhaustion
    if level and sheet.edition == "2024":
        lines.append(t("sheet.bent_exhaustion_2024", level=level, penalty=2 * level))
    elif level:
        kinds = ["check"] + (["attack", "saves"] if level >= 3 else [])
        what = ", ".join(t(f"sheet.bent_kind.{k}") for k in kinds)
        lines.append(f"{t('sheet.exhaustion')} {level}: {t('sheet.bent_dis', what=what)}")
    if not lines:
        return None
    return p(class_="cs-bent", role="note")[icon("triangle-alert", "sm"), span["; ".join(lines)]]


def _advantage(page: Page) -> Element:
    t = page.t
    return div(class_="cs-adv")[
        span(class_="cs-adv-label")[t("sheet.roll_with")],
        div(class_="advset", role="group", aria_label=t("sheet.advantage"))[
            button(type="button", data_adv="-1", aria_pressed="false", class_="dis")[
                t("sheet.adv_dis")
            ],
            button(type="button", data_adv="0", aria_pressed="true")[t("sheet.adv_normal")],
            button(type="button", data_adv="1", aria_pressed="false", class_="advn")[
                t("sheet.adv_adv")
            ],
        ],
        # Rolled privately, the log keeps them between this player and the gamemaster
        # (documentation/PROTOCOL.md §4). sheet.js remembers it, as the advantage.
        button(
            type="button",
            class_="cs-private",
            data_private=True,
            aria_pressed="false",
            title=t("sheet.private_title"),
        )[icon("lock", "xs"), span[t("sheet.private")]],
    ]


def _dot_state(skill: SkillView) -> str:
    return "prof exp" if skill.expertise else "prof" if skill.proficient else ""


def _skill_row(page: Page, sheet: Sheet, skill: SkillView) -> Element:
    label = page.t(f"skill.{skill.key}")
    name = skill.name if label == f"skill.{skill.key}" else label
    return act(
        sheet,
        "roll",
        {"kind": "skill", "subject": skill.key},
        [
            span(class_=f"dot {_dot_state(skill)}"),
            span(class_="nm")[name],
            span(class_="ab")[page.t(f"abbr.{skill.ability}")],
            span(class_="md mono")[skill.value.signed],
        ],
        cls=f"cs-row {'is-override' if skill.value.overridden else ''}",
        title=_value_title(page, skill.value),
    )


def _kv(label: str, value: str) -> Element:
    return div(class_="cs-kv")[span(class_="k")[label], span(class_="v mono")[value]]


def defenses(page: Page, sheet: Sheet) -> list[tuple[str, list[Element]]]:
    """Resistances, immunities, vulnerabilities: the damage types in the reader's
    words, each titled with what grants it."""
    out: list[tuple[str, list[Element]]] = []
    for kind in ("resist", "immune", "vulnerable"):
        found = [d for d in sheet.defenses if d.kind == kind]
        if not found:
            continue
        origins: dict[str, list[str]] = {}
        for d in found:
            origins.setdefault(d.damage, []).append(d.origin)
        out.append(
            (
                page.t(f"ref.{kind}"),
                [
                    span(title=", ".join(dict.fromkeys(o for o in names if o)) or None)[
                        rules_text.damage_word(page, damage)
                    ]
                    for damage, names in origins.items()
                ],
            )
        )
    return out


def _skills_pane(page: Page, sheet: Sheet) -> Element:
    t = page.t
    items = sheet.training_items
    lists = [
        (t("sheet.languages"), [name_text(page, "language", n) for n in sheet.languages]),
        (t("sheet.tools"), [name_text(page, ITEM, n) for n in sheet.tools]),
        (
            t("sheet.armor_training"),
            [training_text(page, w, items) for w in sheet.armor_proficiencies],
        ),
        (
            t("sheet.weapon_training"),
            [training_text(page, w, items) for w in sheet.weapon_proficiencies],
        ),
    ]
    return section(class_="cs-pane", data_tab="skills", data_m="stats")[
        div(class_="cs-pane-head")[h2(class_="eyebrow")[t("sheet.skills")], _advantage(page)],
        _bent_rolls(page, sheet),
        div(class_="cs-rows cs-rows-2")[[_skill_row(page, sheet, s) for s in sheet.skills]],
        div(class_="cs-kvs")[
            _kv(t("sheet.passive_perception"), sheet.passive_perception.text),
            _kv(t("sheet.proficiency"), sheet.proficiency.signed),
            _kv(t("sheet.spell_attack"), sheet.spell_attack.signed) if sheet.spell_attack else None,
        ],
        [
            div(class_="cs-prof")[h3(class_="eyebrow")[label], p[", ".join(items)]]
            for label, items in lists
            if items
        ],
        [
            div(class_="cs-prof cs-defense")[h3(class_="eyebrow")[label], p[_comma(items)]]
            for label, items in defenses(page, sheet)
        ],
    ]


def _comma(items: list[Element]) -> list[Node]:
    out: list[Node] = []
    for index, item in enumerate(items):
        if index:
            out.append(", ")
        out.append(item)
    return out


def _item_row(page: Page, sheet: Sheet, item: ItemView) -> Element:
    t = page.t
    can_equip = item.kind in ("armor", "shield", "weapon") or item.equipped
    badges = [
        span(class_="tag")[f"×{item.quantity}"] if item.quantity != 1 else None,
        span(class_="tag magic")[t("sheet.attuned")] if item.attuned else None,
        span(class_="tag")[_item_summary(page, item.summary)] if item.summary else None,
        span(class_="tag warn")[t("sheet.not_found")]
        if not item.found and "|" in item.name
        else None,
    ]
    return div(class_=f"cs-item {'on' if item.equipped else ''}")[
        div(class_="cs-item-main")[
            name_span(page, ITEM, item.name),
            span(class_="meta")[badges, span[item.notes] if item.notes else None],
        ],
        div(class_="cs-item-act")[
            act(
                sheet,
                "equip",
                {"index": item.index},
                t("sheet.equipped") if item.equipped else t("sheet.equip"),
                cls=f"btn btn-sm {'is-on' if item.equipped else ''}",
                pressed=item.equipped,
            )
            if can_equip
            else None,
            act(
                sheet,
                "drink",
                {"index": item.index},
                [icon("heart", "sm"), span[t("sheet.drink")]],
                cls="btn btn-sm cs-drink",
                title=t("sheet.drink_title", dice=rules_text.dice(page, item.heal)),
            )
            if item.heal
            else None,
        ],
    ]


_ARMOR_SUMMARY = re.compile(r"AC (\+?)(\S+)")


def _item_summary(page: Page, summary: str) -> str:
    """An item's line in the reader's words: "RK 16", "1W6 Wucht" (the sheet keeps it in
    English: "AC 16", "1d6 bludgeoning")."""
    armor = _ARMOR_SUMMARY.fullmatch(summary)
    if armor:
        return f"{page.t('party.ac')} {armor.group(1)}{armor.group(2)}"
    dice_part, _, kind = summary.partition(" ")
    words = rules_text.damage_word(page, kind) if kind else ""
    return f"{rules_text.dice(page, dice_part)} {words}".strip()


def _load_line(page: Page, sheet: Sheet) -> Node:
    """Carried weight against carrying capacity, in the reader's units; past it, what that
    means. The title says what is counted."""
    load = sheet.load
    if load is None:
        return None
    t, u = page.t, page.u
    parts = [t("sheet.load_items", weight=u.weight(load.items))]
    if load.coins:
        parts.append(t("sheet.load_coins", weight=u.weight(load.coins)))
    if load.powerful_build:
        parts.append(t("sheet.load_powerful"))
    if load.unweighed:
        parts.append(t.plural("sheet.load_unweighed", load.unweighed))
    if load.variant:
        parts.append(
            t("sheet.load_variant", light=u.weight(load.encumbered), heavy=u.weight(load.heavy))
        )
    burden = (
        span(class_="tag warn")[
            t(
                "sheet.load_heavy" if load.burden == 2 else "sheet.load_encumbered",
                speed=u.distance(10 * load.burden),
            )
        ]
        if load.burden and not load.over
        else None
    )
    return p(class_=f"cs-load {'over' if load.over else ''}", title=" · ".join(parts))[
        span[
            t(
                "sheet.load",
                carried=u.weight(round(load.carried, 1)),
                capacity=u.weight(load.capacity),
            )
        ],
        " · ",
        span(class_="muted")[t("sheet.load_push", weight=u.weight(load.push))],
        span(class_="tag warn")[t("sheet.load_over", speed=u.distance(5))] if load.over else None,
        burden,
    ]


def _gear_pane(page: Page, sheet: Sheet) -> Element:
    t = page.t
    coins = sheet.character.currency
    return section(class_="cs-pane", data_tab="gear", data_m="gear")[
        div(class_="cs-pane-head")[h2(class_="eyebrow")[t("sheet.inventory")]],
        div(class_="cs-coins")[
            [
                div(class_="cs-coin")[
                    span(class_="v mono")[str(getattr(coins, c))],
                    span(class_="k")[t(f"treasure.coin.{c}")],
                ]
                for c in ("pp", "gp", "ep", "sp", "cp")
            ]
        ],
        _load_line(page, sheet),
        div(class_="cs-list")[[_item_row(page, sheet, i) for i in sheet.inventory]]
        if sheet.inventory
        else p(class_="cs-empty")[t("sheet.no_items")],
    ]


def _feature_text(page: Page, feature: FeatureView) -> tuple[str | None, tuple[object, ...]]:
    """A feature's name (when its record translates it) and text in the reader's
    language, from the record it lives in (TRANSLATION.md §5)."""
    if page.lang == "en" or feature.text_at is None or not feature.entries:
        return None, feature.entries
    category, key, prefix = feature.text_at
    if not prefix:
        shown = page.t.overlay(category, key, {"entries": list(feature.entries)}).data
        return None, tuple(shown["entries"])
    value = {"name": feature.name, "entries": list(feature.entries)}
    part = page.t.overlay_at(category, key, prefix, value).data["value"]
    name = plain(str(part.get("name", ""))) if part.get("name") != feature.name else None
    return name, tuple(part.get("entries") or ())


def _feature_editor(page: Page, feature: FeatureView) -> Node:
    """The way to translate a feature's text, for the gamemaster."""
    if page.identity is None or not page.identity.is_gamemaster or feature.text_at is None:
        return None
    if page.lang == "en":
        return None
    category, key, _prefix = feature.text_at
    if category in COMPOSITE:
        url = translate_url("feature", category, key, back=page.path)
    else:
        name, _, source = key.partition("|")
        url = translate_url("ref", category, source, name, back=page.path)
    return a(href=url, class_="cs-open-ref")[icon("languages"), span[page.t("translate.text")]]


def _feature(page: Page, feature: FeatureView, ctx: RenderContext | None = None) -> Element:
    name, entries = _feature_text(page, feature)
    return details(class_="cs-feature", data_key=f"feature:{feature.origin}:{feature.name}")[
        summary[
            span(class_="nm", title=feature.name)[name]
            if name
            else name_span(page, FEATURE_CATEGORY.get(feature.kind, "feat"), feature.name),
            span(class_="pill neutral")[
                ", ".join(
                    page.t(f"creatures.damage.{c}") if c in DAMAGE_TYPES.values() else c
                    for c in feature.chosen
                )
            ]
            if feature.chosen
            else None,
            span(class_="meta")[origin_text(page, feature.origin)],
        ],
        div(class_="cs-spell-body")[
            _text(page, entries, feature.text, ctx), _feature_editor(page, feature)
        ],
    ]


def _features_pane(page: Page, sheet: Sheet, ctx: RenderContext | None = None) -> Element:
    t = page.t
    pc = sheet.character
    identity = [
        (t("sheet.pronouns"), pc.pronouns),
        (t("sheet.appearance"), pc.appearance),
        (t("sheet.backstory"), pc.backstory),
        (t("sheet.notes"), pc.notes),
    ]
    return section(class_="cs-pane", data_tab="features", data_m="gear")[
        div(class_="cs-pane-head")[h2(class_="eyebrow")[t("sheet.features")]],
        div(class_="cs-list")[[_feature(page, f, ctx) for f in sheet.features]]
        if sheet.features
        else p(class_="cs-empty")[t("sheet.no_features")],
        [
            div(class_="cs-prof")[h3(class_="eyebrow")[label], p[text]]
            for label, text in identity
            if text
        ],
        token_panel(page, page.token_of("character", pc.world_id, pc.id), "character", pc.id),
    ]


def _main(page: Page, sheet: Sheet, tab: str, ctx: RenderContext | None = None) -> Element:
    t = page.t
    tabs = [x for x in DESKTOP_TABS if x != "spells" or sheet.is_spellcaster]
    return section(class_="cs-main")[
        nav(class_="cs-tabs", aria_label=t("sheet.sections"))[
            [
                a(href=f"?tab={x}", data_tab_link=x, aria_current="page" if x == tab else None)[
                    t(f"sheet.tab.{x}")
                ]
                for x in tabs
            ]
        ],
        _actions_pane(page, sheet),
        _spells_pane(page, sheet, ctx) if sheet.is_spellcaster else None,
        _skills_pane(page, sheet),
        _gear_pane(page, sheet),
        _features_pane(page, sheet, ctx),
    ]


# -- right column: what you spend --------------------------------------------------------


def _slot_row(page: Page, sheet: Sheet, slot: SlotLevel, *, pact: bool = False) -> Element:
    t = page.t
    level_param = "pact" if pact else str(slot.level)
    pips = [
        act(
            sheet,
            "slot",
            {"level": level_param, "op": "spend" if i < slot.available else "recover"},
            "",
            cls=f"pip {'used' if i >= slot.available else ''}",
            label=t("sheet.slot_spend", level=slot.level)
            if i < slot.available
            else t("sheet.slot_recover", level=slot.level),
        )
        for i in range(slot.total)
    ]
    return div(class_="cs-slotrow")[
        span(class_="lv mono")[t("sheet.pact_short") if pact else f"L{slot.level}"],
        div(class_="cs-pips")[pips],
        span(class_="cnt mono")[f"{slot.available}/{slot.total}"],
    ]


def _resource_row(page: Page, sheet: Sheet, resource: ResourceView) -> Element:
    t = page.t
    return div(class_="cs-res")[
        span(class_="nm")[
            resource.name, span(class_="meta")[t(f"sheet.recharge.{resource.recharge.value}")]
        ],
        div(class_="cs-res-act")[
            act(
                sheet,
                "resource",
                {"name": resource.name, "op": "restore"},
                "−",
                cls="btn btn-sm",
                label=t("sheet.restore_one"),
            ),
            span(class_="mono cnt")[f"{resource.available}/{resource.max}"],
            act(
                sheet,
                "resource",
                {"name": resource.name, "op": "use"},
                t("sheet.use"),
                cls="btn btn-sm",
            ),
        ],
    ]


def _condition_pill(page: Page, sheet: Sheet, condition: ConditionView) -> Element:
    label = page.t(f"condition.{condition.key}")
    return act(
        sheet,
        "condition",
        {"key": condition.key},
        label,
        cls=f"cbtn {'on' if condition.active else ''}",
        title=" ".join(condition.text) or None,
        pressed=condition.active,
    )


# A natural 20 and a natural 1 stand out in every log (the gamemaster's cards too).
_OUTCOME_ICONS = {"crit": "sparkles", "fumble": "skull"}


def _right(page: Page, sheet: Sheet) -> Element:
    t = page.t
    state = sheet.character.state
    slots = None
    if sheet.slots or sheet.pact:
        slots = section(class_="cs-block", data_m="track")[
            h3(class_="eyebrow")[t("sheet.spell_slots")],
            div(class_="cs-slots")[
                [_slot_row(page, sheet, s) for s in sheet.slots],
                _slot_row(page, sheet, sheet.pact, pact=True) if sheet.pact else None,
            ],
        ]
    concentration = None
    if sheet.concentration is not None:
        concentration = div(class_="cs-conc", data_m="play track")[
            icon("hourglass"),
            span[t("sheet.concentrating", name=name_text(page, "spell", sheet.concentration.name))],
            act(sheet, "concentration", {"op": "drop"}, t("sheet.drop"), cls="btn btn-sm"),
        ]
    rest = section(class_="cs-block", data_m="track")[
        h3(class_="eyebrow")[t("sheet.rest")],
        div(class_="cs-restrow")[
            act(
                sheet,
                "rest",
                {"kind": "short"},
                [icon("hourglass"), span[t("sheet.rest_short")]],
                cls="btn btn-sm",
            ),
            act(
                sheet,
                "rest",
                {"kind": "long"},
                [icon("moon"), span[t("sheet.rest_long")]],
                cls="btn btn-sm",
            ),
        ],
    ]
    resources = None
    if sheet.resources:
        resources = section(class_="cs-block", data_m="track")[
            h3(class_="eyebrow")[t("sheet.resources")],
            [_resource_row(page, sheet, r) for r in sheet.resources],
        ]
    conditions = section(class_="cs-block", data_m="track")[
        h3(class_="eyebrow")[t("sheet.conditions")],
        div(class_="cs-conds")[
            [_condition_pill(page, sheet, c) for c in sheet.conditions],
            act(
                sheet,
                "rage",
                {},
                name_text(page, "classFeature", "Rage"),
                cls=f"cbtn cs-rage {'on' if state.raging else ''}",
                title=t("sheet.rage_hint"),
                pressed=state.raging,
            )
            if sheet.has_feature("Rage")
            else None,
        ],
        div(class_="cs-exh")[
            span[t("sheet.exhaustion")],
            act(
                sheet,
                "exhaustion",
                {"delta": -1},
                "−",
                cls="btn btn-sm",
                label=t("sheet.exhaustion_down"),
            ),
            span(class_=f"mono cnt {'on' if state.exhaustion else ''}")[str(state.exhaustion)],
            act(
                sheet,
                "exhaustion",
                {"delta": 1},
                "+",
                cls="btn btn-sm",
                label=t("sheet.exhaustion_up"),
            ),
        ],
    ]
    # A slice of the log (documentation/PROTOCOL.md §8): this character's entries, as
    # this reader may read them; "Clear" starts it afresh without deleting anything.
    log = section(id="sheet-log", class_="cs-block cs-logblock", data_m="log")[
        div(class_="cs-pane-head")[
            h3(class_="eyebrow")[t("sheet.dice_log")],
            act(sheet, "clear_log", {}, t("sheet.clear"), cls="btn btn-sm btn-ghost"),
        ],
        log_embed_slot(Filter("character", character=sheet.character.id)),
    ]
    return aside(class_="cs-right")[concentration, slots, resources, conditions, rest, log]


# -- the whole sheet -------------------------------------------------------------------


def _head(page: Page, sheet: Sheet, *, file_label: str | None) -> Element:
    t = page.t
    pc = sheet.character
    return header(class_="cs-head")[
        token(page.token_of("character", pc.world_id, pc.id), "lg"),
        div(class_="cs-who")[
            h2(class_="cs-name")[pc.name or t("play.unnamed")],
            span(class_="cs-summary")[
                summary_line(page, sheet.character) if sheet.summary else t("play.not_started")
            ],
        ],
        div(class_="cs-head-meta")[
            span(class_="pill neutral", title=_value_title(page, sheet.passive_perception))[
                t("sheet.passive_short", n=sheet.passive_perception.text)
            ],
            act(
                sheet,
                "inspiration",
                {},
                [icon("sparkles"), span[t("sheet.inspiration")]],
                cls=f"btn btn-sm {'is-on' if pc.state.inspiration else ''}",
                pressed=pc.state.inspiration,
            ),
            span(class_="cs-live", data_live_indicator="1", title=t("sheet.live_title"))[
                span(class_="dot"), span[t("sheet.live")]
            ],
        ],
        p(class_="cs-file mono")[file_label] if file_label else None,
    ]


def _open_saves(page: Page, sheet: Sheet) -> Node:
    """Resilient taken before the file recorded its save: ask which one, once."""
    if sheet.open_saves < 1:
        return None
    t = page.t
    url = _url(sheet, "/act/saving_throw")
    return div(class_="notice cs-open-saves", role="status")[
        icon("shield-check"),
        span[t("sheet.resilient_ask", feat=name_text(page, "feat", "Resilient"))],
        form(method="post", action=url, hx_post=url, class_="inline-form cs-save-choice")[
            [
                button(type="submit", name="ability", value=a.key, class_="btn btn-sm")[
                    t(f"ability.{a.key}")
                ]
                for a in sheet.abilities
                if not a.save_proficient
            ]
        ],
    ]


def _open_resistances(page: Page, sheet: Sheet) -> Node:
    """A resistance the species or a feat lets the character choose, not chosen yet."""
    if not sheet.open_resistances:
        return None
    t = page.t
    url = _url(sheet, "/act/resistance")
    origin, options = sheet.open_resistances[0]
    shown = name_text(page, ("race", "subrace", "feat"), origin)
    return div(class_="notice cs-open-saves", role="status")[
        icon("shield-check"),
        span[t("sheet.resistance_ask", origin=shown)],
        form(method="post", action=url, hx_post=url, class_="inline-form cs-save-choice")[
            [
                button(type="submit", name="damage", value=d, class_="btn btn-sm")[
                    rules_text.damage_word(page, d)
                ]
                for d in options
            ]
        ],
    ]


def _level_up(page: Page, sheet: Sheet) -> Node:
    """A granted level to take (CHARGEN-WIZARD.md §9); for the gamemaster, the grant."""
    t = page.t
    pc = sheet.character
    if pc.status is not DraftStatus.READY:
        return None
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    pending = pc.pending_levels
    xp = progress(pc)
    if not pending and not gamemaster and xp is None:
        return None
    take = (
        a(href=f"/characters/{pc.id}/level-up", class_="btn btn-primary btn-sm")[
            icon("sparkles", "sm"), span[t("levelup.banner", level=sheet.level + 1)]
        ]
        if pending
        else None
    )
    grant = None
    if gamemaster:
        grant = [
            form(method="post", action=f"/characters/{pc.id}/grant-level", class_="inline-form")[
                button(
                    type="submit",
                    class_="btn btn-ghost btn-sm",
                    disabled=sheet.level + pending >= 20,
                )[icon("plus", "sm"), span[t("levelup.grant")]]
            ],
            form(method="post", action=f"/characters/{pc.id}/withdraw-level", class_="inline-form")[
                button(type="submit", class_="btn btn-ghost btn-sm")[t("levelup.withdraw")]
            ]
            if pending
            else None,
        ]
    return div(class_="cs-levelup" + (" is-pending" if pending else ""), role="status")[
        _experience(page, xp) if xp is not None else None,
        span(class_="muted")[t.plural("levelup.pending", pending)] if pending else None,
        span(class_="pill cond")[t("levelup.xp_due", level=xp.reached)]
        if gamemaster and xp is not None and xp.due
        else None,
        take,
        grant,
    ]


def _experience(page: Page, xp: Progress) -> Element:
    """Experience points against the next level's threshold, as a bar."""
    t = page.t
    text = (
        t("sheet.xp_of", xp=t.number(xp.xp), next=t.number(xp.next))
        if xp.next is not None
        else t("sheet.xp_total", xp=t.number(xp.xp))
    )
    return div(class_="cs-xp", title=t("sheet.xp"))[
        span(class_="cs-xp-label")[t("sheet.xp")],
        span(class_="cs-xp-bar", aria_hidden="true")[
            span(class_="cs-xp-fill", style=f"width:{round(xp.fraction * 100)}%")
        ],
        span(class_="cs-xp-text mono")[text],
    ]


def sheet_fragment(
    page: Page,
    sheet: Sheet,
    *,
    tab: str = "",
    view: str = "play",
    spell_filter: str = "",
    flash: str | None = None,
    file_label: str | None = None,
    resolver: object | None = None,
    source_set: SourceSet | None = None,
) -> Element:
    """The ``#sheet`` element — the unit every update replaces.

    With a ``resolver``, rules text renders through the tag renderer: references link to
    their entries (in the character's world) and roll chips roll into this character's
    log through one hidden form, so they work without JavaScript too.
    """
    t = page.t
    tabs = [x for x in DESKTOP_TABS if x != "spells" or sheet.is_spellcaster]
    if tab not in tabs:
        tab = "spells" if sheet.is_spellcaster else "actions"
    if view not in MOBILE_VIEWS:
        view = "play"
    ctx = (
        localized_context(
            t,
            resolver=resolver,
            source_set=source_set,
            world_id=sheet.character.world_id,
            roll_form=_roll_form_id(sheet),
            readable=page.readable,
            units=page.u,
        )
        if resolver is not None
        else None
    )
    roll_url = _url(sheet, "/act/roll")
    return div(
        id="sheet",
        class_="cs",
        data_character=sheet.character.id,
        data_rev=sheet.revision or "",
        data_tab=tab,
        data_m=view,
        # The spell-level filter sheet.js applies; where the user left it.
        data_filter=spell_filter,
        hx_target="this",
        hx_swap="outerHTML",
    )[
        div(class_="alert", role="alert")[
            icon("triangle-alert"), span[t("sheet.file_error", error=sheet.file_error)]
        ]
        if sheet.file_error
        else None,
        div(class_="notice cs-flash", role="status")[icon("triangle-alert"), span[flash]]
        if flash
        else None,
        _head(page, sheet, file_label=file_label),
        _level_up(page, sheet),
        _open_saves(page, sheet),
        _open_resistances(page, sheet),
        # The one form every roll chip in rules text submits (button form="…").
        form(
            id=_roll_form_id(sheet), method="post", action=roll_url, hx_post=roll_url, hidden=True
        )[hidden("kind", "custom")],
        div(class_="cs-grid")[
            _left(page, sheet), _main(page, sheet, tab, ctx), _right(page, sheet)
        ],
        nav(class_="cs-mtabs", aria_label=t("sheet.views"))[
            [
                a(href=f"?view={v}", data_view_link=v, aria_current="page" if v == view else None)[
                    icon(ic, "lg"), span[t(f"sheet.view.{v}")]
                ]
                for v, ic in MOBILE_TABS
            ]
        ],
    ]


def sheet_page(
    page: Page,
    sheet: Sheet,
    *,
    tab: str = "",
    view: str = "play",
    spell_filter: str = "",
    flash: str | None = None,
    file_label: str | None = None,
    resolver: object | None = None,
    source_set: SourceSet | None = None,
    combat_strip: Node = None,
) -> Element:
    """The full page around the sheet; ``combat_strip`` shows the fights it is in."""
    return app_shell(
        page,
        sheet.character.name or page.t("play.unnamed"),
        [
            combat_strip,
            sheet_fragment(
                page,
                sheet,
                tab=tab,
                view=view,
                spell_filter=spell_filter,
                flash=flash,
                file_label=file_label,
                resolver=resolver,
                source_set=source_set,
            ),
        ],
        extra_scripts=(
            "/static/js/htmx.min.js",
            "/static/js/sheet.js",
            "/static/js/combat-strip.js",
        ),
        main_class="shell-main shell-wide",
        world_id=sheet.character.world_id,
        player_bar=False,
        # The log opens on this character (PROTOCOL.md §5).
        log_scope=Filter("character", character=sheet.character.id),
    )


def character_missing_page(page: Page) -> Element:
    """The character does not exist, or is not this player's."""
    from app.ui.auth_pages import message_panel

    t = page.t
    home = "/gm/characters" if page.identity and page.identity.is_gamemaster else "/play"
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "users-round",
            t("notfound.character"),
            t("notfound.character_lede"),
            a(href=home, class_="btn btn-primary")[icon("arrow-left"), span[t("forbidden.back")]],
        ),
    )


__all__ = ["act", "character_missing_page", "sheet_fragment", "sheet_page"]
