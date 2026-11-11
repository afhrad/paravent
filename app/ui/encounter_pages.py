"""The gamemaster's encounters: a list, and the builder that rates a fight as it is planned.

The builder reads top to bottom on a phone and as two columns on a tablet or desktop:
the plan and the monster picker on the left, the rating and the settings on the right.
Every change is a plain form post that comes back to the same place, so it works
without JavaScript; ``palette.js`` only makes the picker's results follow typing.
"""

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
    h1,
    h2,
    label,
    li,
    optgroup,
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

from app.models.gamestate import EDITION_CLASSIC, Combat, Encounter, World
from app.services.combat import Summary
from app.services.creature_builder import HOMEBREW_SOURCE
from app.services.difficulty import Rating
from app.services.encounters import EncounterView, PlannedMonster
from app.services.naming import StyleEntry
from app.services.search import SearchResult
from app.ui.auth_pages import message_panel
from app.ui.context import Page
from app.ui.creature_links import creature_url
from app.ui.forms import (
    alert,
    checkbox_field,
    hidden,
    notice,
    scope_select,
    select_field,
    text_field,
)
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import delete_link
from app.ui.markdown import Rendered
from app.ui.name_roller import name_roller
from app.ui.names import name_text
from app.ui.palette import SearchTarget, search_form
from app.ui.tags import ref_url

ENCOUNTERS_URL = "/gm/encounters"

# How a difficulty reads at a glance: the pill's tint per level.
LEVEL_TONE = {
    "trivial": "neutral",
    "easy": "heal",
    "low": "heal",
    "medium": "cond",
    "moderate": "cond",
    "hard": "dmg",
    "high": "dmg",
    "deadly": "dmg",
    "beyond": "dmg",
}


def encounter_url(encounter_id: str) -> str:
    """The builder for one encounter."""
    return f"{ENCOUNTERS_URL}/{encounter_id}"


def _number(page: Page, value: int) -> str:
    """Thousands grouped as the reader writes them: 1,100 in English, 1.100 in German."""
    text = f"{value:,}"
    return text.replace(",", ".") if page.lang == "de" else text


def _decimal(page: Page, value: float) -> str:
    """``2.5`` in English, ``2,5`` in German."""
    text = f"{value:g}"
    return text.replace(".", ",") if page.lang == "de" else text


def _xp(page: Page, value: int) -> str:
    return page.t("encounters.xp", xp=_number(page, value))


def level_pill(page: Page, rating: Rating) -> Node:
    """The difficulty as a pill, or nothing when there is nothing to rate."""
    if not rating.rated or rating.level is None:
        return None
    tone = LEVEL_TONE.get(rating.level, "neutral")
    strong = " pill-strong" if rating.level in ("deadly", "beyond") else ""
    return span(class_=f"pill {tone}{strong}")[page.t(f"encounters.level.{rating.level}")]


def _summary(page: Page, view: EncounterView) -> str:
    t = page.t
    count = sum(line.planned.count for line in view.lines)
    parts = [view.world.name if view.world else t("encounters.no_world")]
    if count:
        parts.append(t.plural("encounters.creatures", count))
        parts.append(_xp(page, view.rating.base_xp))
    else:
        parts.append(t("encounters.empty_plan"))
    return " · ".join(parts)


# -- the list ------------------------------------------------------------------------------


def _world_choices(page: Page, worlds: Sequence[World]) -> list[tuple[str, str]]:
    return [("", page.t("encounters.no_world")), *((w.id, w.name) for w in worlds)]


def encounters_page(
    page: Page,
    views: Sequence[EncounterView],
    worlds: Sequence[World],
    *,
    world_filter: str | None,
    notice_text: str | None = None,
    error: str | None = None,
    draft_name: str = "",
) -> Element:
    """Every encounter, newest first, and a form to start one."""
    t = page.t
    rows = [
        li(class_="list-row")[
            span(class_="avatar", aria_hidden="true")[icon("skull")],
            div(class_="list-main")[
                a(href=encounter_url(v.encounter.id), class_="list-title list-link")[
                    v.encounter.name
                ],
                span(class_="list-meta")[_summary(page, v)],
            ],
            level_pill(page, v.rating),
            delete_link(page, f"{encounter_url(v.encounter.id)}/delete", t("encounters.delete")),
        ]
        for v in views
    ]
    create = form(method="post", action=ENCOUNTERS_URL, class_="panel enc-new")[
        div(class_="panel-head")[b[t("encounters.new")]],
        alert(error),
        div(class_="enc-new-fields")[
            text_field(
                "name", t("encounters.name"), value=draft_name, autocomplete="off", required=True
            ),
            select_field(
                "world",
                t("encounters.world"),
                _world_choices(page, worlds),
                selected=world_filter or (worlds[0].id if len(worlds) == 1 else ""),
            ),
            button(type="submit", class_="btn btn-primary")[
                icon("plus"), span[t("encounters.create")]
            ],
        ],
    ]
    filter_form = (
        form(method="get", action=ENCOUNTERS_URL, class_="inline-form party-filter")[
            label(for_="enc-world", class_="eyebrow")[t("encounters.world")],
            select(id="enc-world", name="world", onchange="this.form.submit()")[
                option(value="", selected=not world_filter)[t("encounters.all_worlds")],
                [option(value=w.id, selected=w.id == world_filter)[w.name] for w in worlds],
            ],
            button(type="submit", class_="btn btn-sm js-hide")[t("encounters.show")],
        ]
        if worlds
        else None
    )
    return app_shell(
        page,
        t("encounters.page_title"),
        [
            page_head(t("gm.eyebrow"), t("encounters.heading"), t("encounters.lede"), filter_form),
            notice(notice_text),
            create,
            ul(class_="list")[rows]
            if rows
            else empty_state("skull", t("encounters.empty_heading"), t("encounters.empty_text")),
        ],
        world_id=world_filter,
    )


# -- the builder ---------------------------------------------------------------------------


def _in_place(action: str) -> dict[str, str]:
    """A line's form changed in place with HTMX: the answer is the whole builder, of
    which the plan and the rating are taken, so the page neither reloads nor scrolls.
    Without HTMX the form posts as a page."""
    return {
        "hx_post": action,
        "hx_target": "#plan",
        "hx_select": "#plan",
        "hx_select_oob": "#rating",
        "hx_swap": "outerHTML",
    }


def _line(
    page: Page,
    view: EncounterView,
    line: PlannedMonster,
    styles: Sequence[StyleEntry] = (),
    style: str = "",
) -> Element:
    t = page.t
    planned, monster = line.planned, line.monster
    url = f"{encounter_url(view.encounter.id)}/monsters/{line.index}"
    world_id = view.world.id if view.world else None
    if monster is None:
        meta = t("encounters.missing")
    elif monster.cr is None:
        meta = t("encounters.no_cr")
    else:
        meta = t("encounters.cr_xp", cr=monster.cr.text, xp=_number(page, line.xp or 0))
    shown = name_text(page, "monster", planned.name)
    name_node: Node = (
        a(
            href=ref_url("monster", planned.name, planned.source, world_id),
            class_="ref creature",
            title=planned.name if shown != planned.name else None,
        )[shown]
        if monster is not None
        else span(class_="ref missing")[planned.name]
    )
    # A creature of the builder opens there, to change it (its draft included).
    design_id = (
        view.designs.get(planned.name.casefold()) if planned.source == HOMEBREW_SOURCE else None
    )
    if design_id is not None:
        name_node = a(href=creature_url(design_id), class_="ref creature")[shown]
    side = "friend" if planned.ally else "foe"
    return li(class_=f"enc-line is-{side}")[
        span(class_="res-ico " + ("heal" if planned.ally else "dmg"), aria_hidden="true")[
            icon("shield" if planned.ally else "skull")
        ],
        div(class_="enc-line-main")[
            b[planned.label, " ", span(class_="enc-reskin")[t("encounters.as"), " "]]
            if planned.label
            else None,
            b[
                name_node,
                " ",
                span(class_="res-src mono")[planned.source],
                " ",
                a(
                    href=creature_url(design_id),
                    class_="btn btn-sm btn-ghost enc-edit",
                    title=t("encounters.edit_creature"),
                    aria_label=t("encounters.edit_creature"),
                )[icon("pencil", "sm")]
                if design_id is not None
                else None,
            ],
            span(class_="list-meta")[
                meta,
                " ",
                # Friend or foe, as a pill that switches it — the tracker's words.
                form(
                    method="post",
                    action=f"{url}/side",
                    class_="cb-form cb-inline",
                    **_in_place(f"{url}/side"),
                )[
                    hidden("ally", "" if planned.ally else "1"),
                    button(
                        type="submit",
                        class_=f"pill side-{side} cb-side-switch",
                        title=t("combat.make_foe" if planned.ally else "combat.make_friend"),
                    )[icon("refresh-cw", "xs"), t(f"combat.{side}")],
                ],
            ],
        ],
        form(method="post", action=f"{url}/label", class_="enc-label", **_in_place(f"{url}/label"))[
            label(for_=f"f-label-{line.index}", class_="sr-only")[t("encounters.shown_as")],
            span(class_="name-field")[
                input_el(
                    id=f"f-label-{line.index}",
                    name="label",
                    value=planned.label or "",
                    placeholder=t("encounters.shown_as"),
                    autocomplete="off",
                    title=t("encounters.shown_as_hint"),
                ),
                name_roller(
                    page,
                    f"{url}/label/roll",
                    styles,
                    field_id=f"f-label-{line.index}",
                    target="closest li",
                    selected=style,
                    hint=t("naming.roll_each") if planned.count > 1 else None,
                )
                if styles
                else None,
            ],
        ],
        form(method="post", action=url, class_="enc-count", **_in_place(url))[
            button(
                type="submit",
                name="count",
                value=str(planned.count - 1),
                class_="btn btn-sm",
                aria_label=t("encounters.fewer", name=shown),
            )[icon("x") if planned.count == 1 else "−"],
            span(class_="enc-count-n mono", aria_live="polite")[str(planned.count)],
            button(
                type="submit",
                name="count",
                value=str(planned.count + 1),
                class_="btn btn-sm",
                aria_label=t("encounters.more", name=shown),
                disabled=planned.count >= 99,
            )["+"],
        ],
        # An ally earns no XP: it counts for the party instead.
        span(class_="enc-line-xp mono")[
            _xp(page, line.xp * planned.count) if line.xp and not planned.ally else "—"
        ],
    ]


def encounter_line(
    page: Page,
    view: EncounterView,
    index: int,
    styles: Sequence[StyleEntry] = (),
    style: str = "",
) -> Element:
    """One line of the plan alone, as a roll of its name swaps it in place; ``style``
    the one it was rolled in, kept in its menu for the next roll."""
    return _line(page, view, view.lines[index], styles, style)


def _plan(page: Page, view: EncounterView, styles: Sequence[StyleEntry] = ()) -> Element:
    t = page.t
    lines = [_line(page, view, line, styles) for line in view.lines]
    count = sum(line.planned.count for line in view.lines)
    return section(class_="panel enc-plan", id="plan", aria_labelledby="plan-h")[
        div(class_="panel-head")[
            h2(id="plan-h")[t("encounters.monsters")],
            span(class_="list-meta")[t.plural("encounters.creatures", count)] if count else None,
        ],
        ul(class_="enc-lines")[lines] if lines else p(class_="enc-empty")[t("encounters.none_yet")],
        div(class_="enc-total")[
            span[t("encounters.total")], b(class_="mono")[_xp(page, view.rating.base_xp)]
        ]
        if lines
        else None,
        p(class_="field-hint")[t.plural("encounters.unrated", view.unrated)]
        if view.unrated
        else None,
    ]


def _bar(page: Page, rating: Rating) -> Element:
    t = page.t
    return div(class_="enc-bar", role="img", aria_label=t("encounters.bar_label"))[
        div(
            class_=f"enc-bar-fill tone-{LEVEL_TONE.get(rating.level or '', 'neutral')}",
            style=f"width:{rating.fill * 100:.1f}%",
        ),
        [
            span(class_="enc-bar-mark", style=f"left:{rating.share(band) * 100:.1f}%")[
                span(class_="enc-bar-label")[t(f"encounters.level.{band.key}")]
            ]
            for band in rating.bands
        ],
    ]


def _rating(page: Page, view: EncounterView) -> Element:
    t = page.t
    rating = view.rating
    rules_name = t(f"encounters.rules.{rating.rules}")
    rules_origin = (
        t("encounters.rules_set") if view.encounter.rules else t("encounters.rules_from_books")
    )
    body: list[Node] = []
    if not view.party:
        body.append(p(class_="enc-empty")[t("encounters.no_party")])
    elif not rating.monster_count:
        body.append(p(class_="enc-empty")[t("encounters.no_monsters")])
    elif not rating.bands:
        # A party and monsters, but no table to hold them against: the 2014 thresholds
        # are read from the corpus, and this one has no 2014 Dungeon Master's Guide.
        body.append(p(class_="enc-empty")[t("encounters.no_tables")])
    else:
        body.append(
            div(class_="enc-verdict")[
                level_pill(page, rating),
                span(class_="enc-verdict-xp mono")[_xp(page, rating.adjusted_xp)],
            ]
        )
        body.append(_bar(page, rating))
        facts = [
            (t("encounters.xp_total"), _xp(page, rating.base_xp)),
        ]
        if rating.rules == EDITION_CLASSIC:
            facts.append(
                (
                    t("encounters.multiplier"),
                    t(
                        "encounters.multiplier_value",
                        factor=_decimal(page, rating.multiplier),
                        count=rating.monster_count,
                        party=rating.party_size,
                    ),
                )
            )
            facts.append((t("encounters.adjusted"), _xp(page, rating.adjusted_xp)))
        body.append(ul(class_="enc-facts")[[li[span[k], span(class_="mono")[v]] for k, v in facts]])
    if rating.bands:
        body.append(
            ul(class_="enc-bands")[
                [
                    li(class_="current" if band.key == rating.level else None)[
                        span[t(f"encounters.level.{band.key}")],
                        span(class_="mono")[_xp(page, band.xp)],
                    ]
                    for band in rating.bands
                ]
            ]
        )
    party_line: Node
    if view.party_stated and view.encounter.party is not None:
        spec = view.encounter.party
        party_line = t("encounters.party_stated", size=spec.size, level=spec.level)
    else:
        party_line = ", ".join(f"{m.name} ({m.level})" for m in view.party)
    if view.party:
        body.append(p(class_="enc-party")[b[t("encounters.party"), ": "], party_line])
    body.append(
        p(class_="field-hint")[t("encounters.rules_line", rules=rules_name, origin=rules_origin)]
    )
    return section(
        class_="panel enc-rating", id="rating", aria_labelledby="rating-h", aria_live="polite"
    )[
        div(class_="panel-head")[h2(id="rating-h")[t("encounters.difficulty")]],
        body,
    ]


def _picker(page: Page, view: EncounterView, result: SearchResult) -> Element:
    t = page.t
    url = encounter_url(view.encounter.id)
    target = SearchTarget(
        action=url,
        dom_id="picker-results",
        pick_action=f"{url}/monsters",
        pick=view.encounter.id,
        swap="#plan #rating",
    )
    return section(class_="panel enc-picker", id="picker", aria_labelledby="picker-h")[
        div(class_="panel-head")[h2(id="picker-h")[t("encounters.add_heading")]],
        search_form(
            page,
            result,
            view.world.id if view.world else None,
            in_dialog=False,
            target=target,
            autofocus=bool(result.query) or not view.lines,
        ),
    ]


def _settings(page: Page, view: EncounterView, scopes: Sequence[tuple[str, str, int]]) -> Element:
    t = page.t
    enc = view.encounter
    current = ""
    if enc.world_id:
        current = (
            f"c:{enc.chapter_id}"
            if enc.chapter_id
            else f"a:{enc.adventure_id}"
            if enc.adventure_id
            else f"w:{enc.world_id}"
        )
    inferred = t(f"encounters.rules.{view.rules_inferred}")
    party = enc.party
    return details(class_="panel enc-settings", open=False)[
        summary(class_="panel-head")[icon("pencil"), b[t("encounters.settings")]],
        form(method="post", action=encounter_url(enc.id), class_="stack-form")[
            text_field("name", t("encounters.name"), value=enc.name, autocomplete="off"),
            scope_select(
                "world",
                t("encounters.scope"),
                scopes,
                selected=current,
                none_label=t("encounters.no_world"),
            ),
            select_field(
                "rules",
                t("encounters.rules_label"),
                [
                    ("", t("encounters.rules_auto", rules=inferred)),
                    ("classic", t("encounters.rules.classic")),
                    ("one", t("encounters.rules.one")),
                ],
                selected=enc.rules or "",
            ),
            select_field(
                "sound",
                t("encounters.sound"),
                [("", "—"), *[(name, name) for name in view.sound_names]],
                selected=enc.sound,
                hint=t("encounters.sound_hint"),
            ),
            checkbox_field(
                "use_party",
                t("encounters.party_override"),
                checked=party is not None,
                hint=t("encounters.party_override_hint"),
            ),
            div(class_="enc-party-fields")[
                div(class_="field")[
                    label(for_="f-party_size")[t("encounters.party_size")],
                    input_el(
                        id="f-party_size",
                        name="party_size",
                        type="number",
                        min="1",
                        max="12",
                        value=str(party.size if party else max(1, len(view.party)) or 4),
                    ),
                ],
                div(class_="field")[
                    label(for_="f-party_level")[t("encounters.party_level")],
                    input_el(
                        id="f-party_level",
                        name="party_level",
                        type="number",
                        min="1",
                        max="20",
                        value=str(party.level if party else _typical_level(view)),
                    ),
                ],
            ],
            div(class_="field")[
                label(for_="f-notes")[
                    t("encounters.notes"), " ", span(class_="opt")[t("common.optional")]
                ],
                p(class_="field-hint", id="f-notes-hint")[t("encounters.notes_hint")],
                textarea(
                    id="f-notes",
                    name="notes",
                    rows="6",
                    class_="prose-editor",
                    data_scope=current or None,
                    aria_describedby="f-notes-hint",
                )[enc.notes],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("check"), span[t("encounters.save")]
                ],
            ],
        ],
    ]


def _typical_level(view: EncounterView) -> int:
    levels = sorted(m.level for m in view.party)
    return levels[len(levels) // 2] if levels else 1


def _fought(page: Page, fought: Sequence[tuple[Combat, Summary]]) -> Node:
    """The fights already run from this plan: when, how long, and the XP they gave."""
    t = page.t
    if not fought:
        return None
    return section(class_="panel enc-fought", aria_labelledby="fought-h")[
        div(class_="panel-head")[h2(id="fought-h")[t("combat.fought")]],
        ul(class_="cb-list")[
            [
                li(class_="cb-list-item")[
                    a(href=f"/gm/combat/{combat.id}")[
                        combat.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
                    ],
                    span(class_="pill neutral")[t.plural("combat.rounds_n", result.rounds)],
                    span(class_="mono muted")[t("combat.xp_n", xp=result.xp)],
                ]
                for combat, result in fought
            ]
        ],
    ]


def _run_button(page: Page, encounter: Encounter, running: Combat | None) -> Element:
    """Into the running combat, or start one."""
    t = page.t
    if running is not None:
        state = t("combat.round_n", n=running.round) if running.round else t("combat.rolling")
        return a(href=f"/gm/combat/{running.id}", class_="btn btn-primary")[
            icon("zap"), span[state]
        ]
    return form(method="post", action=f"{encounter_url(encounter.id)}/run")[
        button(type="submit", class_="btn btn-primary", disabled=not encounter.monsters)[
            icon("play"), span[t("combat.run")]
        ]
    ]


def encounter_page(
    page: Page,
    view: EncounterView,
    scopes: Sequence[tuple[str, str, int]],
    result: SearchResult,
    *,
    place: Sequence[str] = (),
    notice_text: str | None = None,
    error: str | None = None,
    running: Combat | None = None,
    fought: Sequence[tuple[Combat, Summary]] = (),
    environments: Sequence[tuple[str, str, bool]] = (),
    notes: Rendered | None = None,
    name_styles: Sequence[StyleEntry] = (),
) -> Element:
    """The builder: plan, rating, picker and settings; ``notes`` the encounter's notes
    rendered as Paravent Markdown; ``name_styles`` what the "Shown as" dice may roll.

    ``scopes`` are every place the encounter could belong; ``place`` names where it does;
    ``running`` is the combat being fought from it, if any, and ``fought`` the finished
    ones with what they came to.
    """
    t = page.t
    enc = view.encounter
    return app_shell(
        page,
        enc.name,
        div(class_="enc-page")[
            a(href=ENCOUNTERS_URL, class_="backlink")[
                icon("arrow-left"), span[t("encounters.back")]
            ],
            div(class_="enc-head")[
                span(class_="eyebrow")[" › ".join(place) or t("encounters.no_world")],
                div(class_="enc-title")[
                    h1[enc.name],
                    _run_button(page, enc, running),
                    delete_link(
                        page, f"{encounter_url(enc.id)}/delete", t("encounters.delete"), text=True
                    ),
                ],
                div(class_="md-body enc-notes")[notes.html]
                if notes is not None and notes.html.strip()
                else None,
            ],
            alert(error),
            notice(notice_text),
            div(class_="enc-grid")[
                div(class_="enc-main")[_plan(page, view, name_styles), _picker(page, view, result)],
                div(class_="enc-side")[
                    _rating(page, view),
                    _roll_table(page, view, environments),
                    _fought(page, fought),
                    _settings(page, view, scopes),
                ],
            ],
        ],
        main_class="shell-main shell-wide",
        world_id=view.world.id if view.world else None,
        insert_layer=True,
        extra_scripts=("/static/js/htmx.min.js",),
    )


def _roll_table(
    page: Page, view: EncounterView, environments: Sequence[tuple[str, str, bool]]
) -> Node:
    """Roll on a random-encounter table: the result replaces the monsters."""
    t = page.t
    chosen = view.encounter.roll_table
    if not environments:
        return None
    own = [(key, label) for key, label, ours in environments if ours]
    other = [(key, label) for key, label, ours in environments if not ours]
    levels = [m.level for m in view.party]
    level = round(sum(levels) / len(levels)) if levels else 1
    return section(class_="panel enc-roll", aria_labelledby="roll-h")[
        div(class_="panel-head")[
            h2(id="roll-h")[t("encounters.roll")],
            p(class_="muted")[t("encounters.roll_hint", level=level)],
        ],
        form(method="post", action=f"{encounter_url(view.encounter.id)}/roll", class_="stack-form")[
            div(class_="field")[
                label(for_="f-env")[t("encounters.roll_environment")],
                select(id="f-env", name="environment")[
                    optgroup(label=t("encounters.roll_own"))[
                        [option(value=key, selected=key == chosen)[name] for key, name in own]
                    ]
                    if own
                    else None,
                    optgroup(label=t("encounters.roll_other"))[
                        [option(value=key, selected=key == chosen)[name] for key, name in other]
                    ]
                    if other
                    else None,
                ],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn")[
                    icon("dices", "sm"), span[t("encounters.roll_go")]
                ]
            ],
        ],
    ]


def encounter_delete_page(page: Page, encounter: Encounter) -> Element:
    """Confirm before deleting."""
    t = page.t
    url = encounter_url(encounter.id)
    return app_shell(
        page,
        t("encounters.delete"),
        div(class_="formpage")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[t("common.cancel")]],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("gm.eyebrow")],
                    b[t("encounters.delete_heading", name=encounter.name)],
                ],
                p[t("encounters.delete_lede")],
                form(method="post", action=f"{url}/delete")[
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[
                            icon("x"), span[t("encounters.delete")]
                        ],
                        a(href=url, class_="btn btn-ghost")[t("common.cancel")],
                    ]
                ],
            ],
        ],
    )


def encounter_not_found_page(page: Page) -> Element:
    """The encounter does not exist (any more)."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "skull",
            t("encounters.notfound"),
            t("encounters.notfound_lede"),
            a(href=ENCOUNTERS_URL, class_="btn btn-primary")[t("encounters.back")],
        ),
    )


__all__ = [
    "ENCOUNTERS_URL",
    "encounter_delete_page",
    "encounter_line",
    "encounter_not_found_page",
    "encounter_page",
    "encounter_url",
    "encounters_page",
    "level_pill",
]
