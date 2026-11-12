"""The character creation wizard's pages (documentation/CHARGEN-WIZARD.md §3–§4).

One form per step, ``#wizard``: the step rail, the step's questions, the derived panel
and Back/Continue. Every change posts the form through htmx and the whole form comes
back, so the panel shows the consequence of a pick before it is committed (rule 3).
Without JavaScript, "Save" and "Continue" post it the ordinary way.

Questions are drawn from the plan (:mod:`app.services.chargen`): species, classes and
subclasses as cards, everything else by one renderer for any
:class:`~app.services.chargen.Pick`. The step-specific screens of later build steps
replace the generic one where they need more.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import quote

from htpy import (
    Element,
    Node,
    a,
    aside,
    b,
    button,
    dd,
    details,
    div,
    dl,
    dt,
    fieldset,
    form,
    h1,
    h2,
    img,
    label,
    legend,
    li,
    nav,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    table,
    tbody,
    td,
    textarea,
    th,
    thead,
    tr,
    ul,
)
from htpy import input as input_el

from app.models.base import ABILITIES
from app.models.gamestate import World
from app.models.pc import AbilityMethod, PlayerCharacter
from app.services.chargen import Grant, Offer, Pick, Plan, Planner, Step, Verdict
from app.services.chargen.backstory import ALL, LifeTable
from app.services.chargen.blocks import feat_reference
from app.services.chargen.catalogue import SHELVES, About, equipment_kinds, shelf
from app.services.chargen.drafts import TRADE
from app.services.chargen.plan import GOLD, SHOP, price
from app.services.chargen.rules import (
    GoldRoll,
    point_budget,
    point_cost,
    standard_array,
    starting_gold,
)
from app.services.naming import GENDERS
from app.services.sheet import Sheet, Value
from app.services.text import paragraphs, strip_tags
from app.ui.context import Page
from app.ui.forms import alert
from app.ui.forms import hidden as hidden_input
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import ITEM, name_text, named, training_text
from app.ui.names import summary as summary_line
from app.ui.naming_pages import style_options
from app.ui.sheet_pages import speed_text
from app.ui.tags import RenderContext, localized_context, ref_url, render
from app.ui.translate_pages import translate_url

CHARGEN_URL = "/chargen"
SCRIPTS = ("/static/js/htmx.min.js", "/static/js/tooltips.js", "/static/js/chargen.js")
# Lists longer than this get a filter field.
FILTER_FROM = 12


# The option kinds with a reference page of their own to open.
REF_PAGES = ("race", "class", "background")


def wizard_url(character_id: str, step: str = "") -> str:
    """The wizard of a draft, at ``step``."""
    return f"{CHARGEN_URL}/{character_id}" + (f"/{step}" if step else "")


# -- labels ----------------------------------------------------------------------------


def _name(value: str) -> str:
    """``"Fighter|XPHB"`` → ``Fighter``; ``"animal handling"`` → ``Animal handling``."""
    name = value.removeprefix("feat:").split("|")[0]
    return name if name[:1].isupper() else name[:1].upper() + name[1:]


def _source(value: str) -> str | None:
    _, _, source = value.removeprefix("feat:").partition("|")
    return source or None


def pick_title(page: Page, pick: Pick) -> str:
    """What a question asks, in the reader's language."""
    t = page.t
    if pick.id.startswith("granted."):
        # A feat's, species' or background's own spells: "Magic Initiate: cantrips".
        kind = {"variant": "list"}.get(pick.kind, pick.kind)
        return t(f"chargen.pick.granted_{kind}", origin=_origin(page, pick.origin))
    if pick.kind == "asi":
        return t("chargen.pick.asi", n=pick.label)
    if pick.kind == "expertise":
        return t("chargen.pick.expertise", what=pick.label)
    if pick.id == "featopt.spells":
        return t("chargen.pick.subclass_spells", subclass=_origin(page, pick.label))
    if pick.id == "featopt.cantrip":
        return t("chargen.pick.subclass_cantrip", subclass=_origin(page, pick.label))
    if pick.kind in ("optional", "subclass", "class", "prepared", "featureoption") and pick.label:
        return pick.label
    if pick.kind == "feat" and pick.label:
        return pick.label
    if pick.id == "abilities.method":
        return t("chargen.pick.method")
    if pick.kind == "equipment" and pick.id == "equipment":
        return t("chargen.pick.packages")
    return t(f"chargen.pick.{pick.kind}")


# A question's kind → the glossary categories its answers are named under
# (TRANSLATION.md §4). Skills come from the UI catalogue; tools are items.
_TERMS: dict[str, tuple[str, ...]] = {
    "species": ("race",),
    "subspecies": ("subrace",),
    "lineage": ("race", "subrace"),
    "class": ("class",),
    "subclass": ("subclass",),
    "background": ("background",),
    "skill": ("skill",),
    "tool": ITEM,
    "language": ("language",),
    "feat": ("feat",),
    "optional": ("optionalfeature",),
    "cantrip": ("spell",),
    "spell": ("spell",),
    "prepared": ("spell",),
    "item": ITEM,
    "expertise": ("skill", *ITEM),
    "weapon": ITEM,
}


def _localized(page: Page, kind: str, english: str) -> str | None:
    """The glossary's name for an answer of this kind, or None."""
    categories = _TERMS.get(kind)
    return named(page, categories, english) if categories else None


# Who grants a proficiency: a class, species, background or feat, by its record name.
_ORIGINS = ("class", "race", "subrace", "background", "feat", "subclass")


def _origin(page: Page, origin: str) -> str:
    """A grant's origin in the reader's language ("Soldier" → "Soldat")."""
    return named(page, _ORIGINS, origin) or origin


def _term(page: Page, kind: str, english: str) -> str:
    """A name of this kind localized, or as given."""
    return _localized(page, kind, english) or english


def _value_label(page: Page, pick: Pick, value: str) -> str:
    if pick.id == "abilities.method":
        return page.t(f"chargen.method.{value}")
    if pick.kind in ("ability", "casting") or (pick.kind == "asi" and "|" not in value):
        return page.t(f"ability.{value}") if value in ABILITIES else _name(value)
    if pick.kind == "hp":
        return page.t(f"chargen.hp.{value}")
    if pick.kind == "equipment":
        if value in ("packages", GOLD):
            return page.t("chargen.pick.take" if value == "packages" else "chargen.pick.gold")
        return value.upper()
    if pick.kind == "variant":
        index = int(value) if value.isdigit() else 0
        return pick.details[index] if index < len(pick.details) else value
    if pick.kind == "featureoption" and pick.id.endswith(".choice.damage"):
        return page.t(f"creatures.damage.{value}")  # Elemental Adept's damage type
    english = _name(value)
    return _localized(page, pick.kind, value.removeprefix("feat:").split("|")[0]) or english


# -- the rail, the panel, the footer -------------------------------------------------------


def _rail(page: Page, pc: PlayerCharacter, plan: Plan, current: str) -> Element:
    t = page.t
    items = []
    for index, step in enumerate(plan.steps, start=1):
        mark: Node = str(index)
        if step.state == "done":
            mark = icon("check", "xs")
        elif step.state == "review":
            mark = "!"
        items.append(
            a(
                href=wizard_url(pc.id, step.id),
                class_=f"step {step.state}",
                aria_current="step" if step.id == current else None,
            )[span(class_="num")[mark], span(class_="step-label")[t(f"chargen.step.{step.id}")]]
        )
    position = plan.ids.index(current) + 1 if current in plan.ids else 1
    # One line on a phone — "Step 3 of 8 · Abilities" — that opens the list of steps;
    # the full rail from tablet width up (CHARGEN-WIZARD.md §3).
    return nav(class_="cg-rail", aria_label=t("chargen.steps"))[
        details(class_="cg-rail-toggle", open=True)[
            summary(class_="cg-progress")[
                span[
                    t("chargen.step_of", n=position, total=len(plan.steps)),
                    " · ",
                    b[t(f"chargen.step.{current}")],
                ],
                span(class_="cg-progress-bar")[
                    span(style=f"width:{position / max(1, len(plan.steps)) * 100:.0f}%")
                ],
            ],
            div(class_="steps")[items],
        ]
    ]


def _derived(page: Page, pc: PlayerCharacter, plan: Plan) -> Element:
    """The teaching surface: what the picks so far add up to (CHARGEN-WIZARD.md §3)."""
    t = page.t
    sheet: Sheet | None = plan.sheet
    rows: list[Node] = []
    if sheet is not None:

        def row(key: str, value: str | None) -> Element:
            return div(class_="dv-row")[span(class_="dv-k")[key], span(class_="dv-v")[value or "—"]]

        rows = [
            row(t("chargen.hp_max"), str(sheet.hp_max.value) if sheet.hp_max.value else None),
            row(t("party.ac"), sheet.armor_class.text),
            row(t("party.speed"), speed_text(page, sheet.speed)),
            row(t("party.init"), sheet.initiative.signed),
            row(t("sheet.proficiency"), sheet.proficiency.signed),
            row(t("party.dc"), sheet.spell_save_dc.text) if sheet.spell_save_dc else None,
            div(class_="abil-grid")[
                [
                    div(class_="abil")[
                        span(class_="ab-k")[a_.key.upper()],
                        span(class_="ab-v")[str(a_.score.value) if a_.score.value else "—"],
                        span(class_="ab-m")[f"{a_.modifier:+d}" if a_.score.value else ""],
                    ]
                    for a_ in sheet.abilities
                ]
            ],
        ]
    line = summary_line(page, pc) or t("chargen.nothing_yet")
    compact = None
    if sheet is not None:
        compact = " · ".join(
            f"{k} {v}"
            for k, v in (
                (t("chargen.hp_max"), sheet.hp_max.value),
                (t("party.ac"), sheet.armor_class.text),
                (t("party.speed"), speed_text(page, sheet.speed)),
            )
            if v
        )
    return aside(class_="derived", aria_live="polite", aria_label=t("chargen.derived"))[
        div[
            div(class_="dv-name")[pc.name or t("chargen.unnamed")],
            div(class_="dv-sub")[line],
            div(class_="dv-compact mono")[compact] if compact else None,
        ],
        div(class_="dv-rows")[rows],
    ]


def _footer(page: Page, pc: PlayerCharacter, plan: Plan, step: Step) -> Element:
    t = page.t
    back = plan.before(step.id)
    forward = plan.after(step.id)
    reason = None
    if step.state == "review":
        reason = t("chargen.fix_first")
    elif step.state == "open":
        reason = t("chargen.answer_first")
    return div(class_="wizard-foot cg-foot")[
        a(href=wizard_url(pc.id, back), class_="btn btn-ghost")[
            icon("arrow-left", "sm"), span[t(f"chargen.step.{back}")]
        ]
        if back
        else span,
        span(class_="cg-saved muted")[icon("check", "xs"), t("chargen.saved")],
        div(class_="cg-foot-actions")[
            button(type="submit", name="go", value="stay", class_="btn js-hide")[t("chargen.save")],
            button(
                type="submit",
                name="go",
                value="next",
                class_="btn btn-primary",
                disabled=step.state != "done",
                title=reason,
            )[span[t(f"chargen.step.{forward}")], icon("arrow-right", "sm")]
            if forward
            else button(
                type="submit",
                formaction=wizard_url(pc.id, "finish"),
                class_="btn btn-primary",
                disabled=not plan.complete,
                title=None if plan.complete else t("chargen.finish_first"),
            )[icon("check", "sm"), span[t("chargen.finish")]],
        ],
        p(class_="cg-foot-why muted")[reason] if reason and forward else None,
        p(class_="cg-foot-why muted")[t("chargen.finish_first")]
        if not forward and not plan.complete
        else None,
    ]


# -- questions -------------------------------------------------------------------------


def _choice_input(pick: Pick, value: str, checked: bool, *, disabled: bool = False) -> Node:
    return input_el(
        type="radio" if pick.count == 1 else "checkbox",
        name=pick.id,
        value=value,
        checked=checked,
        disabled=disabled,
        id=f"{pick.id}--{value}",
        class_="cg-input",
    )


def _filter(page: Page, pick: Pick, size: int) -> Node:
    if size <= FILTER_FROM:
        return None
    return input_el(
        type="search",
        class_="cg-filter",
        data_filter=pick.id,
        placeholder=page.t("chargen.filter"),
        aria_label=page.t("chargen.filter"),
    )


def _problems(page: Page, step: Step, pick: Pick) -> Node:
    found = [pr for pr in step.problems if pr.pick == pick.id]
    if not found:
        return None
    return div(class_="alert cg-problem", role="alert")[
        icon("triangle-alert", "sm"), span[" ".join(page.t.error(pr) for pr in found)]
    ]


def _question(
    page: Page,
    step: Step,
    pick: Pick,
    content: Node,
    *,
    size: int = 0,
) -> Element:
    t = page.t
    title = pick_title(page, pick)
    count_hint = (
        t("chargen.choose_n", n=pick.count)
        if pick.count > 1 and pick.kind != "asi" and not pick.weights
        else None
    )
    origin = (
        span(class_="chip-src")[t("chargen.from", origin=pick.origin)]
        if pick.origin
        and pick.id != "abilities.method"
        and pick.kind in ("skill", "tool", "language", "ability", "feat")
        else None
    )
    return fieldset(class_="cg-question", id=f"q-{pick.id}")[
        legend(class_="cg-legend")[b[title], origin, small(class_="muted")[count_hint]],
        _problems(page, step, pick),
        _filter(page, pick, size),
        content,
    ]


def _list_question(
    page: Page,
    step: Step,
    pick: Pick,
    options: Sequence[str],
    selected: Sequence[str],
    verdicts: Mapping[str, Verdict],
    *,
    taken: Mapping[str, str] | None = None,
    describe: Callable[[str], str | None] | None = None,
) -> Element:
    """Any question as a list of radio buttons or checkboxes.

    ``describe`` gives an option a line of its own text — what a fighting style does.
    """
    t = page.t
    chosen = {v.lower() for v in selected}
    taken = taken or {}
    rows = []
    for value in options:
        verdict = verdicts.get(value.removeprefix("feat:"))
        given = taken.get(value.lower())
        blocked = (verdict is not None and not verdict.allowed) or given is not None
        reason: Node = None
        if given is not None:
            reason = span(class_="grant")[t("chargen.given_by", origin=_origin(page, given))]
        elif verdict is not None and verdict.unmet:
            reason = span(class_="why")[" ".join(page.t.error(u) for u in verdict.unmet)]
        elif verdict is not None and verdict.confirm:
            reason = span(class_="why soft")[t("chargen.confirm", text="; ".join(verdict.confirm))]
        source = _source(value)
        shown, english = _value_label(page, pick, value), _name(value)
        rows.append(
            label(
                class_="cg-option" + (" is-blocked" if blocked else ""),
                for_=f"{pick.id}--{value}",
                data_name=" ".join(dict.fromkeys((shown.lower(), english.lower()))),
            )[
                _choice_input(
                    pick,
                    value,
                    value.lower() in chosen,
                    disabled=blocked and value.lower() not in chosen,
                ),
                span(class_="cg-option-name", title=english if shown != english else None)[shown],
                span(class_="chip-src")[source] if source else None,
                reason,
                span(class_="cg-option-text")[text]
                if (text := describe(value) if describe else None)
                else None,
            ]
        )
    wide = " cg-options-wide" if describe else ""
    return _question(
        page,
        step,
        pick,
        div(
            class_="cg-options" + wide,
            data_options=pick.id,
            data_max=str(pick.count) if pick.count > 1 else None,
        )[rows],
        size=len(options),
    )


def _card_question(
    page: Page,
    step: Step,
    pick: Pick,
    offers: Sequence[Offer],
    selected: Sequence[str],
    body: Callable[[Offer], Node],
    *,
    planner: Planner,
    category: str,
    world_id: str | None = None,
) -> Element:
    """A question whose options are corpus entries worth a card each: each says a line
    of what the books say about it, the chosen one all of it, folded."""
    chosen = {v.lower() for v in selected}
    cards = []
    folded: Node = None
    for o in offers:
        local = _localized(page, pick.kind, o.name)
        about = planner.catalogue.about(category, o)
        lines = _about_lines(page, about, (o.name, local or o.name))
        if about is not None and o.reference.lower() in chosen:
            readable = category in REF_PAGES and page.can_read(category, o.data)
            url = ref_url(category, o.name, o.source, world_id) if readable else None
            ctx = localized_context(
                page.t,
                resolver=planner.catalogue.compendium.resolver(),
                source_set=planner.catalogue.source_set,
                world_id=world_id,
                readable=page.readable,
                units=page.u,
            )
            folded = _about_panel(page, local or o.name, about, lines, url, ctx)
        cards.append(
            label(
                class_="pick cg-card",
                for_=f"{pick.id}--{o.reference}",
                data_name=" ".join(dict.fromkeys(n.lower() for n in (local or o.name, o.name))),
            )[
                _choice_input(pick, o.reference, o.reference.lower() in chosen),
                span(class_="cg-card-head")[
                    b(title=o.name if local else None)[local or o.name],
                    span(class_="chip-src")[o.source],
                ],
                span(class_="cg-card-en")[o.name] if local and local != o.name else None,
                body(o),
                # A card is tapped to choose it, so its line is plain: no link to steal the tap.
                span(class_="pd cg-card-about")[strip_tags(lines[0])] if lines else None,
            ]
        )
    return _question(
        page,
        step,
        pick,
        [folded, div(class_="cardgrid", data_options=pick.id)[cards]],
        size=len(offers),
    )


def _about_lines(page: Page, about: About | None, names: Sequence[str]) -> list[str]:
    """What the books say, in paragraphs with their tags, in the reader's language where
    translated; the heading the books open with, the option's own name, is left out."""
    if about is None:
        return []
    shown = page.t.overlay(about.category, about.key, {"entries": about.entries}, ("entries",))
    lines = paragraphs(shown.data["entries"], tags=True)
    for name in names:
        if lines and lines[0].startswith(f"{name}. "):
            lines[0] = lines[0].removeprefix(f"{name}. ")
    return lines


def _about_panel(
    page: Page,
    name: str,
    about: About,
    lines: Sequence[str],
    url: str | None,
    ctx: RenderContext,
) -> Element:
    """The chosen option's whole description, above the cards so it stays in reach, its
    references links (to what the reader may open) and its units hinted in metres; the
    gamemaster can translate it from here and come back."""
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    return details(class_="cg-about")[
        summary[page.t("chargen.about", name=name)],
        div(class_="ref-fluff-body")[
            img(
                class_="ref-fluff-image",
                src=f"/play/img/{quote(about.image)}",
                alt="",
                loading="lazy",
            )
            if about.image
            else None,
            [p[render(line, ctx)] for line in lines],
            a(class_="ref", href=url)[page.t("ref.open")] if url else None,
            a(
                class_="btn btn-sm",
                href=translate_url("fluff", *about.where, back=page.path),
            )[icon("languages", "sm"), span[page.t("translate.text")]]
            if gamemaster and about.entries
            else None,
        ],
    ]


# -- card bodies -------------------------------------------------------------------------


def _speed(page: Page, data: Mapping[str, object]) -> str | None:
    speed = data.get("speed")
    if isinstance(speed, int):
        return page.u.distance(speed)
    if isinstance(speed, Mapping) and isinstance(speed.get("walk"), int):
        return page.u.distance(speed["walk"])
    return None


def _species_body(page: Page) -> Callable[[Offer], Node]:
    t = page.t

    def body(o: Offer) -> Node:
        data = o.data
        facts = [
            "/".join(str(s) for s in data.get("size") or []) or None,
            _speed(page, data),
            t("chargen.darkvision", distance=page.u.distance(data["darkvision"]))
            if isinstance(data.get("darkvision"), int)
            else None,
        ]
        bonuses = ""
        first = (data.get("ability") or [None])[0]
        if isinstance(first, Mapping):
            fixed = [
                f"{k.upper()} {v:+d}"
                for k, v in first.items()
                if k in ABILITIES and isinstance(v, int)
            ]
            if "choose" in first:
                fixed.append(t("chargen.choose_bonus"))
            bonuses = ", ".join(fixed)
        traits = [
            str(e.get("name"))
            for e in data.get("entries") or []
            if isinstance(e, Mapping) and e.get("name")
        ][:4]
        return [
            span(class_="pd mono")[" · ".join(f for f in facts if f)],
            span(class_="pd")[bonuses] if bonuses else span(class_="pd")[", ".join(traits)],
        ]

    return body


def _class_body(page: Page) -> Callable[[Offer], Node]:
    t = page.t

    def body(o: Offer) -> Node:
        data = o.data
        hd = data.get("hd")
        faces = hd.get("faces") if isinstance(hd, Mapping) else None
        saves = ", ".join(str(s).upper() for s in data.get("proficiency") or [])
        return [
            span(class_="pd mono")[
                " · ".join(x for x in (f"d{faces}" if faces else None, saves) if x)
            ],
            span(class_="pd")[t("chargen.caster")] if data.get("casterProgression") else None,
        ]

    return body


def _plain_body(_page: Page) -> Callable[[Offer], Node]:
    return lambda o: None


# -- steps -------------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """What a step's view draws from."""

    page: Page
    pc: PlayerCharacter
    plan: Plan
    step: Step
    planner: Planner
    world: World | None = None

    def selected(self, pick: Pick) -> list[str]:
        """The answer to ``pick`` so far."""
        return list(self.pc.choices.get(pick.id) or [])


def _taken(step: Step) -> dict[str, str]:
    return {
        g.value.lower(): g.origin
        for g in reversed(step.grants)  # the first grant names the origin
        if g.kind in ("skill", "tool", "language")
    }


def _picks(v: View, picks: Sequence[Pick]) -> list[Node]:
    """Questions with no screen of their own, each the way its kind reads best."""
    taken = _taken(v.step)
    out: list[Node] = []
    for pick in picks:
        if pick.weights:
            out.append(_weighted_question(v, pick))
            continue
        out.append(
            _list_question(
                v.page,
                v.step,
                pick,
                v.planner.options(pick),
                v.selected(pick),
                v.plan.verdicts,
                taken=taken if pick.kind in ("skill", "tool", "language") else None,
            )
        )
    return out


def _generic(v: View) -> Node:
    out = _picks(v, v.step.picks)
    if v.step.grants:
        out.insert(0, _grants(v.page, v.step))
    return out


def _weighted_question(v: View, pick: Pick) -> Element:
    """A distribution to assign — "+2 to ___, +1 to ___" — not a set to pick from."""
    t = v.page.t
    chosen = v.selected(pick)
    rows = []
    for index, weight in enumerate(pick.weights):
        current = chosen[index] if index < len(chosen) else ""
        rows.append(
            label(class_="cg-weight", for_=f"{pick.id}-{index}")[
                span(class_="cg-weight-n mono")[f"+{weight}"],
                select(name=pick.id, id=f"{pick.id}-{index}", class_="cg-level cg-wide")[
                    option(value="")["—"],
                    [
                        option(value=a, selected=a == current)[t(f"ability.{a}")]
                        for a in pick.options
                    ],
                ],
            ]
        )
    return _question(v.page, v.step, pick, div(class_="cg-weights")[rows])


def _unique(grants: Sequence[Grant]) -> list[Grant]:
    """Each grant once: a subrace often repeats what its race already gives."""
    seen: set[tuple[str, str]] = set()
    out = []
    for grant in grants:
        key = (grant.kind, grant.value.lower())
        if key not in seen:
            seen.add(key)
            out.append(grant)
    return out


def _grant_label(page: Page, grant: Grant) -> str:
    if grant.kind == "feat":
        name, _source, variant = feat_reference(grant.value)
        shown = _term(page, "feat", name.title())
        return f"{shown} ({_term(page, 'class', variant.title())})" if variant else shown
    return _term(page, grant.kind, _name(grant.value))


def _grants(page: Page, step: Step) -> Element:
    t = page.t
    return div(class_="cg-grants")[
        span(class_="eyebrow")[t("chargen.granted")],
        ul[
            [
                li[span[_grant_label(page, g)], span(class_="chip-src")[_origin(page, g.origin)]]
                for g in _unique(step.grants)
            ]
        ],
    ]


def _species_step(v: View) -> Node:
    page, pc, plan, step, planner = v.page, v.pc, v.plan, v.step, v.planner
    cat = planner.catalogue
    out: list[Node] = []
    for pick in step.picks:
        selected = pc.choices.get(pick.id) or []
        if pick.id == "species":
            offers = [
                o for o in cat.species(planner.edition(plan.cls)) if o.reference in pick.options
            ]
            out.append(
                _card_question(
                    page,
                    step,
                    pick,
                    offers,
                    selected,
                    _species_body(page),
                    planner=planner,
                    category="race",
                    world_id=pc.world_id,
                )
            )
        elif pick.id == "subspecies" and plan.species is not None:
            offers = cat.subspecies(plan.species)
            out.append(
                _card_question(
                    page,
                    step,
                    pick,
                    offers,
                    selected,
                    _species_body(page),
                    planner=planner,
                    category="subrace",
                    world_id=pc.world_id,
                )
            )
        else:
            out.append(
                _list_question(page, step, pick, planner.options(pick), selected, plan.verdicts)
            )
    return out


def _class_step(v: View) -> Node:
    page, pc, plan, step, planner = v.page, v.pc, v.plan, v.step, v.planner
    t = page.t
    cat = planner.catalogue
    out: list[Node] = []
    for pick in step.picks:
        selected = pc.choices.get(pick.id) or []
        if pick.id == "class":
            out.append(
                _card_question(
                    page,
                    step,
                    pick,
                    cat.classes(),
                    selected,
                    _class_body(page),
                    planner=planner,
                    category="class",
                    world_id=pc.world_id,
                )
            )
        elif pick.id == "level":
            current = selected[0] if selected else "1"
            out.append(
                _question(
                    page,
                    step,
                    pick,
                    select(name="level", id="level", class_="cg-level")[
                        [
                            option(value=str(n), selected=str(n) == current)[str(n)]
                            for n in range(1, 21)
                        ]
                    ],
                )
            )
        elif pick.id == "subclass" and plan.cls is not None:
            offers = cat.subclasses(plan.cls)
            out.append(
                _card_question(
                    page,
                    step,
                    pick,
                    offers,
                    selected,
                    _plain_body(page),
                    planner=planner,
                    category="subclass",
                    world_id=pc.world_id,
                )
            )
        else:
            out.append(
                _list_question(page, step, pick, planner.options(pick), selected, plan.verdicts)
            )
    if plan.level is not None and plan.cls is not None:
        features = [
            f"{named(page, ('classFeature', 'subclassFeature'), name) or name} ({lvl})"
            for name, lvl in plan.level.features
        ]
        out.append(
            section(class_="cg-features")[
                span(class_="eyebrow")[t("chargen.features_through", n=plan.level.level)],
                p[", ".join(features)],
            ]
        )
    return out


def _abilities_step(v: View) -> Node:
    """The method, then the scores the way that method makes them, then the totals."""
    t = v.page.t
    method_pick = v.step.pick("abilities.method")
    method = (v.pc.choices.get("abilities.method") or [""])[0]
    out: list[Node] = []
    if method_pick is not None:
        out.append(_method_question(v, method_pick, method))
    if method:
        out.append(
            fieldset(class_="cg-question cg-scores")[
                legend(class_="cg-legend")[b[t("chargen.scores")]],
                _problems_for(v.page, v.step, "abilities.scores"),
                _score_inputs(v, method),
            ]
        )
    out.extend(_picks(v, [p for p in v.step.picks if p.id != "abilities.method"]))
    out.append(_score_table(v))
    return out


def _method_question(v: View, pick: Pick, method: str) -> Element:
    t = v.page.t
    cards = [
        label(class_="pick cg-card", for_=f"{pick.id}--{value}")[
            _choice_input(pick, value, value == method),
            span(class_="cg-card-head")[b[t(f"chargen.method.{value}")]],
            span(class_="pd")[t(f"chargen.method_hint.{value}")],
        ]
        for value in pick.options
    ]
    return _question(v.page, v.step, pick, div(class_="cardgrid")[cards])


def _assign(v: View, values: Sequence[int]) -> Element:
    """Each ability gets one of ``values`` — the standard array, or the rolled six."""
    t = v.page.t
    return div(class_="cg-score-grid")[
        [
            label(class_="cg-score", for_=f"score-{ab}")[
                span(class_="ab-k")[t(f"ability.{ab}")],
                select(name=f"score.{ab}", id=f"score-{ab}", class_="cg-level")[
                    option(value="")["—"],
                    [
                        option(value=str(n), selected=v.pc.abilities.get(ab) == n)[str(n)]
                        for n in sorted(set(values), reverse=True)
                    ],
                ],
            ]
            for ab in ABILITIES
        ]
    ]


def _score_inputs(v: View, method: str) -> Node:
    t = v.page.t
    pc = v.pc
    url = wizard_url(pc.id, "abilities")
    if method == AbilityMethod.STANDARD.value:
        return [
            p(class_="muted")[
                t("chargen.standard_lede", values=", ".join(map(str, standard_array())))
            ],
            _assign(v, standard_array()),
        ]
    if method == AbilityMethod.ROLL.value:
        may_roll = not pc.ability_rolls or (v.world is not None and v.world.allow_rerolls)
        roll = button(
            type="submit",
            formaction=f"{url}/roll",
            hx_post=f"{url}/roll",
            hx_target="#wizard",
            hx_swap="outerHTML",
            class_="btn" + ("" if pc.ability_rolls else " btn-primary"),
        )[
            icon("dices", "sm"),
            span[t("chargen.roll_again" if pc.ability_rolls else "chargen.roll")],
        ]
        if not pc.ability_rolls:
            return [p(class_="muted")[t("chargen.roll_lede")], roll]
        return [
            div(class_="cg-rolls", role="status")[
                span(class_="eyebrow")[t("chargen.rolled")],
                [span(class_="cg-roll mono")[str(n)] for n in pc.ability_rolls],
                roll if may_roll else small(class_="muted")[t("chargen.rolls_final")],
            ],
            _assign(v, pc.ability_rolls),
        ]
    if method == AbilityMethod.POINTBUY.value:
        scores = {ab: pc.abilities.get(ab) or 8 for ab in ABILITIES}
        spent = point_cost(scores) or 0
        left = point_budget() - spent

        def step_button(ab: str, delta: str, glyph: str, enabled: bool) -> Element:
            return button(
                type="submit",
                name="adjust",
                value=f"{ab}:{delta}",
                id=f"pb-{ab}-{'up' if delta == '+1' else 'down'}",
                hx_post=url,
                hx_target="#wizard",
                hx_swap="outerHTML",
                class_="btn btn-sm cg-step-btn",
                disabled=not enabled,
                aria_label=t(
                    f"chargen.point_{'up' if delta == '+1' else 'down'}", ability=t(f"ability.{ab}")
                ),
            )[glyph]

        rows = []
        for ab in ABILITIES:
            value = scores[ab]
            up = {**scores, ab: value + 1}
            affordable = value < 15 and (point_cost(up) or 99) <= point_budget()
            rows.append(
                div(class_="cg-pb-row")[
                    span(class_="cg-pb-name")[t(f"ability.{ab}")],
                    step_button(ab, "-1", "−", value > 8),
                    span(class_="cg-pb-value mono")[str(value)],
                    step_button(ab, "+1", "+", affordable),
                    hidden_input(f"score.{ab}", str(value)),
                ]
            )
        return [
            div(class_="budget" + (" full" if left == 0 else ""), role="status")[
                span[t("chargen.points_left")],
                span(class_="n")[str(left)],
                span(class_="muted")[f"/ {point_budget()}"],
            ],
            div(class_="cg-pb")[rows],
        ]
    return div(class_="cg-score-grid")[
        [
            label(class_="cg-score", for_=f"score-{ab}")[
                span(class_="ab-k")[t(f"ability.{ab}")],
                input_el(
                    type="number",
                    name=f"score.{ab}",
                    id=f"score-{ab}",
                    min="1",
                    max="30",
                    value=str(pc.abilities.get(ab) or ""),
                    inputmode="numeric",
                ),
            ]
            for ab in ABILITIES
        ]
    ]


def _score_table(v: View) -> Node:
    """Base, bonus and total side by side, so no number is opaque (§4 Abilities)."""
    t = v.page.t
    sheet = v.plan.sheet
    if sheet is None:
        return None
    rows = []
    for view in sheet.abilities:
        base = v.pc.abilities.get(view.key)
        total = view.score.value
        bonus = total - base if total is not None and base is not None else None
        rows.append(
            tr[
                th(scope="row")[t(f"ability.{view.key}")],
                td(class_="num")[str(base) if base is not None else "—"],
                td(class_="num", title=view.score.explanation or None)[
                    f"{bonus:+d}" if bonus else ("" if bonus == 0 else "—")
                ],
                td(class_="num")[b[str(total)] if total is not None else "—"],
                td(class_="num")[f"{view.modifier:+d}" if total is not None else ""],
            ]
        )
    return table(class_="score-tbl")[
        thead[
            tr[
                th[t("chargen.ability")],
                th(class_="num")[t("chargen.base")],
                th(class_="num")[t("chargen.bonus")],
                th(class_="num")[t("chargen.total")],
                th(class_="num")[t("chargen.modifier")],
            ]
        ],
        tbody[rows],
    ]


def _background_body(page: Page) -> Callable[[Offer], Node]:
    t = page.t

    def fixed(blocks: object) -> list[str]:
        out: list[str] = []
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, Mapping):
                out.extend(_name(str(k)) for k, val in block.items() if val is True)
        return out

    def body(o: Offer) -> Node:
        data = o.data
        skills = [_term(page, "skill", s) for s in fixed(data.get("skillProficiencies"))]
        tools = [_term(page, "tool", s) for s in fixed(data.get("toolProficiencies"))]
        feats = [
            _term(page, "feat", feat_reference(k)[0].title()) for k in fixed(data.get("feats"))
        ]
        abilities = ""
        for block in data.get("ability") or []:
            weighted = (
                block.get("choose", {}).get("weighted") if isinstance(block, Mapping) else None
            )
            if isinstance(weighted, Mapping):
                abilities = "/".join(str(a).upper() for a in weighted.get("from") or [])
                break
        feature = next(
            (
                str(e.get("name")).removeprefix("Feature: ")
                for e in data.get("entries") or []
                if isinstance(e, Mapping) and str(e.get("name", "")).startswith("Feature:")
            ),
            None,
        )
        return [
            span(class_="pd")[", ".join(skills)] if skills else None,
            span(class_="pd mono")[", ".join(tools)] if tools else None,
            span(class_="pd")[t("chargen.raises", abilities=abilities)] if abilities else None,
            span(class_="pd")[t("chargen.feat_named", name=", ".join(feats))] if feats else None,
            span(class_="pd")[feature] if feature else None,
        ]

    return body


def _background_step(v: View) -> Node:
    cat = v.planner.catalogue
    out: list[Node] = []
    rest = []
    for pick in v.step.picks:
        if pick.id == "background":
            offers = [
                o
                for o in cat.backgrounds(v.planner.edition(v.plan.cls))
                if o.reference in pick.options
            ]
            out.append(
                _card_question(
                    v.page,
                    v.step,
                    pick,
                    offers,
                    v.selected(pick),
                    _background_body(v.page),
                    planner=v.planner,
                    category="background",
                    world_id=v.pc.world_id,
                )
            )
        else:
            rest.append(pick)
    if v.step.grants:
        out.append(_grants(v.page, v.step))
    out.extend(_picks(v, rest))
    return out


def _problems_for(page: Page, step: Step, pick_id: str) -> Node:
    found = [pr for pr in step.problems if pr.pick == pick_id]
    if not found:
        return None
    return div(class_="alert cg-problem", role="alert")[
        icon("triangle-alert", "sm"), span[" ".join(page.t.error(pr) for pr in found)]
    ]


def _details_step(v: View) -> Node:
    """A name and who the character is, then the whole of it to check (§4 Details)."""
    t, pc = v.page.t, v.pc

    def text(name: str, value: str, *, rows: int = 0, required: bool = False) -> Element:
        control: Node = (
            textarea(name=name, id=f"cg-{name}", rows=str(rows))[value]
            if rows
            else input_el(
                type="text",
                name=name,
                id=f"cg-{name}",
                value=value,
                maxlength="80",
                required=required,
                autocomplete="off",
            )
        )
        return label(class_="field", for_=f"cg-{name}")[
            span(class_="field-label")[t(f"chargen.{name}")], control
        ]

    return [
        _problems_for(v.page, v.step, "name"),
        div(class_="cg-identity")[
            text("name", pc.name, required=True),
            text("pronouns", pc.pronouns),
        ],
        _name_suggestion(v),
        text("appearance", pc.appearance, rows=2),
        _physique_roll(v),
        text("backstory", pc.backstory, rows=6 if pc.backstory else 4),
        _backstory_tables(v),
        _review(v),
    ]


def _name_suggestion(v: View) -> Element:
    """A name in the species' style, or one chosen here (NAMES.md §5)."""
    t = v.page.t
    url = f"{wizard_url(v.pc.id, 'details')}/name"
    return div(class_="cg-suggest")[
        label(class_="field", for_="cg-name-gender")[
            span(class_="field-label")[t("npcgen.gender")],
            select(name="name_gender", id="cg-name-gender")[
                [option(value=g)[t(f"npcgen.gender.{g}")] for g in GENDERS]
            ],
        ],
        label(class_="field", for_="cg-name-style")[
            span(class_="field-label")[t("npcgen.names")],
            select(name="name_style", id="cg-name-style")[
                option(value="")[t("naming.by_species")],
                style_options(v.page, v.planner.styles(), ""),
            ],
        ],
        label(class_="field", for_="cg-name-parent")[
            span(class_="field-label")[t("naming.child_of")],
            select(name="name_parent", id="cg-name-parent")[
                option(value="")[t("naming.no_parent")],
                [option(value=ref)[name] for ref, name in v.planner.relatives],
            ],
        ]
        if v.planner.relatives
        else None,
        button(
            type="submit",
            formaction=url,
            formnovalidate=True,
            hx_post=url,
            hx_target="#wizard",
            hx_swap="outerHTML",
            class_="btn btn-sm",
        )[icon("dices", "sm"), span[t("chargen.suggest_name")]],
    ]


def _physique_roll(v: View) -> Node:
    """Height and weight from the species' table, added to the appearance above."""
    if v.planner.physique(v.pc) is None:
        return None
    url = f"{wizard_url(v.pc.id, 'details')}/physique"
    return button(
        type="submit",
        formaction=url,
        hx_post=url,
        hx_target="#wizard",
        hx_swap="outerHTML",
        class_="btn btn-sm",
    )[icon("dices", "sm"), span[v.page.t("chargen.physique.roll")]]


def _backstory_tables(v: View) -> Node:
    """A background's characteristics and *Xanathar's* "This Is Your Life": a roll adds a
    line to the backstory above."""
    backstory = v.planner.backstory
    tables = backstory.tables(v.pc) if backstory is not None else []
    if not tables:
        return None
    t = v.page.t
    url = f"{wizard_url(v.pc.id, 'details')}/backstory"

    def roll(key: str, caption: Node, *, primary: bool = False) -> Element:
        return button(
            type="submit",
            name="table",
            value=key,
            formaction=url,
            hx_post=url,
            hx_target="#wizard",
            hx_swap="outerHTML",
            class_="btn btn-sm" + (" btn-primary" if primary else ""),
        )[icon("dices", "sm"), span[caption]]

    def label_of(life: LifeTable) -> str:
        if life.kind == "characteristic":
            return t(f"chargen.backstory.{life.caption}")
        if life.caption:
            return life.caption
        owner = name_text(v.page, life.kind, life.owner)
        return t(f"chargen.backstory.why_{life.kind}", name=str(owner))

    return section(class_="cg-backstory")[
        div(class_="cg-backstory-head")[
            span(class_="field-label")[t("chargen.backstory.title")],
            roll(ALL, t("chargen.backstory.all"), primary=True),
        ],
        p(class_="muted")[t("chargen.backstory.lede")],
        div(class_="cg-backstory-tables")[
            [
                roll(life.key, [label_of(life), small(class_="muted")[f" d{life.table.faces}"]])
                for life in tables
            ]
        ],
    ]


def _derivation(label_: str, value: Value) -> Element:
    """A number with how it was reached, open on tap (§4 Details & review)."""
    shown = value.text
    if not value.explanation:
        return div(class_="cg-stat")[span(class_="dv-k")[label_], span(class_="dv-v")[shown]]
    return details(class_="cg-stat")[
        summary[span(class_="dv-k")[label_], span(class_="dv-v")[shown]],
        small(class_="muted")[value.explanation],
    ]


def _review(v: View) -> Node:
    """The character as a whole: the last chance to catch a mistake."""
    t = v.page.t
    sheet = v.plan.sheet
    if sheet is None:
        return None
    unfinished = [s for s in v.plan.steps if s.state != "done" and s.id != "details"]
    todo: Node = None
    if unfinished:
        todo = div(class_="alert cg-todo", role="status")[
            icon("triangle-alert", "sm"),
            span[
                t("chargen.still_open"),
                " ",
                [a(href=wizard_url(v.pc.id, s.id))[t(f"chargen.step.{s.id}")] for s in unfinished],
            ],
        ]
    page = v.page
    skills = [
        f"{_term(page, 'skill', sk.name)} {sk.value.signed}" for sk in sheet.skills if sk.proficient
    ]
    feats = [
        _term(page, "feat" if f.kind == "feat" else "optional", f.name)
        for f in sheet.features
        if f.kind in ("feat", "option")
    ]
    return section(class_="cg-review", aria_labelledby="review-h")[
        h2(id="review-h", class_="cg-step-title")[t("chargen.review")],
        todo,
        p(class_="muted")[summary_line(page, v.pc) or sheet.summary],
        div(class_="cg-stats")[
            _derivation(t("chargen.hp_max"), sheet.hp_max),
            _derivation(t("party.ac"), sheet.armor_class),
            _derivation(t("party.init"), sheet.initiative),
            _derivation(t("party.speed"), sheet.speed),
            _derivation(t("party.pp"), sheet.passive_perception),
            _derivation(t("party.dc"), sheet.spell_save_dc) if sheet.spell_save_dc else None,
        ],
        div(class_="cg-stats cg-abilities")[
            [
                _derivation(
                    a_.key.upper() + (" ●" if a_.save_proficient else ""),
                    Value(a_.score.value, a_.score.explanation),
                )
                for a_ in sheet.abilities
            ]
        ],
        dl(class_="cg-facts")[
            dt[t("chargen.step.skills")],
            dd[", ".join(skills) or "—"],
            dt[t("chargen.pick.language")],
            dd[", ".join(_term(page, "language", n) for n in sheet.languages) or "—"],
            dt[t("chargen.pick.tool")],
            dd[", ".join(_term(page, "tool", n) for n in sheet.tools) or "—"],
            dt[t("chargen.pick.feat")],
            dd[", ".join(feats) or "—"],
            dt[t("chargen.step.spells")],
            dd[", ".join(_term(page, "spell", sp.name) for sp in sheet.spells) or "—"],
            dt[t("chargen.step.equipment")],
            dd[", ".join(_term(page, "item", i.item.name) for i in v.pc.inventory) or "—"],
        ],
    ]


# -- features ------------------------------------------------------------------------


def _described_by(pick: Pick) -> Callable[[str], str | None]:
    """An option's text as the question carries it (``details``, by position)."""
    texts = dict(zip(pick.options, pick.details, strict=False))

    def describe(value: str) -> str | None:
        return texts.get(value) or None

    return describe


def _summary_of(v: View, category: str) -> Callable[[str], str | None]:
    """The first sentence or so of an option's own text, tags stripped."""
    cat = v.planner.catalogue

    def describe(value: str) -> str | None:
        offer = cat.find(category, value.removeprefix("feat:"))
        if offer is None:
            return None
        text = " ".join(paragraphs(offer.data.get("entries"), limit=1))
        text = strip_tags(text)
        return text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "…"

    return describe


def _asi_question(v: View, pick: Pick) -> Element:
    """An Ability Score Improvement: +1 and +1 (the same twice is +2), or a feat."""
    t = v.page.t
    chosen = v.selected(pick)
    feat = next((c for c in chosen if c.startswith("feat:")), "")
    raised = [] if feat else [c for c in chosen if c in ABILITIES]

    def ability_select(index: int) -> Element:
        current = raised[index] if index < len(raised) else ""
        return label(class_="cg-weight", for_=f"{pick.id}-{index}")[
            span(class_="cg-weight-n mono")["+1"],
            select(
                name=pick.id,
                id=f"{pick.id}-{index}",
                class_="cg-level cg-wide",
                disabled=bool(feat),
            )[
                option(value="")["—"],
                [option(value=a, selected=a == current)[t(f"ability.{a}")] for a in ABILITIES],
            ],
        ]

    feats = []
    for value in pick.options:
        if not value.startswith("feat:"):
            continue
        verdict = v.plan.verdicts.get(value.removeprefix("feat:"))
        blocked = verdict is not None and not verdict.allowed
        why = " ".join(v.page.t.error(u) for u in verdict.unmet) if verdict and blocked else ""
        feats.append(
            option(value=value, selected=value == feat, disabled=blocked and value != feat)[
                _name(value) + (f" — {why}" if why else "")
            ]
        )
    return _question(
        v.page,
        v.step,
        pick,
        [
            div(class_="cg-weights")[ability_select(0), ability_select(1)],
            small(class_="muted")[t("chargen.asi_hint")],
            label(class_="cg-weight cg-asi-feat", for_=f"{pick.id}-feat")[
                span(class_="cg-weight-n")[t("chargen.or_feat")],
                select(name=pick.id, id=f"{pick.id}-feat", class_="cg-level cg-wide")[
                    option(value="")["—"], feats
                ],
            ],
            p(class_="cg-option-text")[_summary_of(v, "feat")(feat)] if feat else None,
        ],
    )


def _hp_question(v: View, pick: Pick) -> Element:
    t = v.page.t
    pc = v.pc
    method = (v.selected(pick) or ["average"])[0]
    cls = v.plan.cls
    hd = cls.data.get("hd") if cls is not None else None
    faces = int(hd.get("faces", 8)) if isinstance(hd, Mapping) else 8
    level = v.planner.level_of(pc.choices)
    radios = _list_question(v.page, v.step, pick, pick.options, v.selected(pick), {})
    if method == "rolled":
        url = wizard_url(pc.id, "features") + "/roll-hp"
        results = pc.hit_points.per_level
        may_roll = not results or (v.world is not None and v.world.allow_rerolls)
        roll = button(
            type="submit",
            formaction=url,
            hx_post=url,
            hx_target="#wizard",
            hx_swap="outerHTML",
            class_="btn" + ("" if results else " btn-primary"),
        )[
            icon("dices", "sm"),
            span[
                t(
                    "chargen.roll_hp_again" if results else "chargen.roll_hp",
                    n=level - 1,
                    faces=faces,
                )
            ],
        ]
        body: Node = div(class_="cg-rolls", role="status")[
            [
                span(class_="cg-roll mono", title=t("chargen.level_n", n=i + 1))[str(r)]
                for i, r in enumerate(results)
            ]
            if results
            else None,
            roll if may_roll else small(class_="muted")[t("chargen.rolls_final")],
        ]
    else:
        body = p(class_="muted")[
            t("chargen.hp_average", faces=faces, first=faces, each=faces // 2 + 1)
        ]
    return div(class_="cg-hp")[radios, _problems_for(v.page, v.step, "hp.rolls"), body]


def _features_step(v: View) -> Node:
    t = v.page.t
    out: list[Node] = []
    levels = v.plan.level
    if levels is not None and levels.features:
        by_level: dict[int, list[str]] = {}
        for name, lvl in levels.features:
            by_level.setdefault(lvl, []).append(name)
        out.append(
            section(class_="cg-features")[
                span(class_="eyebrow")[t("chargen.features_through", n=levels.level)],
                ul(class_="cg-feature-list")[
                    [
                        li[span(class_="mono muted")[str(lvl)], span[", ".join(names)]]
                        for lvl, names in sorted(by_level.items())
                    ]
                ],
            ]
        )
    for pick in v.step.picks:
        if pick.kind == "asi":
            out.append(_asi_question(v, pick))
        elif pick.kind == "hp":
            out.append(_hp_question(v, pick))
        elif pick.kind == "featureoption":
            out.append(
                _list_question(
                    v.page,
                    v.step,
                    pick,
                    list(pick.options),
                    v.selected(pick),
                    v.plan.verdicts,
                    describe=_described_by(pick),
                )
            )
        elif pick.kind in ("optional", "feat"):
            category = "optionalfeature" if pick.kind == "optional" else "feat"
            out.append(
                _list_question(
                    v.page,
                    v.step,
                    pick,
                    v.planner.options(pick),
                    v.selected(pick),
                    v.plan.verdicts,
                    describe=_summary_of(v, category),
                )
            )
        else:
            out.extend(_picks(v, [pick]))
    return out


# -- skills ----------------------------------------------------------------------------


def _skills_step(v: View) -> Node:
    """Every skill with its ability, bonus and where the proficiency comes from (§4)."""
    t = v.page.t
    origin: dict[str, str] = {}
    for grant in v.step.grants:
        if grant.kind == "skill":
            origin.setdefault(grant.value.lower(), grant.origin)
    for pick in v.step.picks:
        if pick.kind == "skill":
            for value in v.selected(pick):
                origin.setdefault(value.lower(), pick.origin)
    out: list[Node] = []
    sheet = v.plan.sheet
    if sheet is not None and sheet.skills:
        out.append(
            table(class_="score-tbl cg-skills")[
                thead[
                    tr[
                        th[t("chargen.skill")],
                        th[t("chargen.ability")],
                        th(class_="num")[t("chargen.bonus")],
                        th[t("chargen.source")],
                    ]
                ],
                tbody[
                    [
                        tr(class_="is-proficient" if sk.name.lower() in origin else None)[
                            th(scope="row")[_term(v.page, "skill", sk.name)],
                            td(class_="muted")[t(f"abbr.{sk.ability}")],
                            td(class_="num")[sk.value.signed],
                            td[
                                span(class_="chip-src")[_origin(v.page, origin[sk.name.lower()])]
                                if sk.name.lower() in origin
                                else None
                            ],
                        ]
                        for sk in sheet.skills
                    ]
                ],
            ]
        )
        items = sheet.training_items
        armor = ", ".join(training_text(v.page, w, items) for w in sheet.armor_proficiencies)
        weapons = ", ".join(training_text(v.page, w, items) for w in sheet.weapon_proficiencies)
        if armor or weapons:
            out.append(
                p(class_="muted cg-training")[
                    t("chargen.training", armor=armor or "—", weapons=weapons or "—")
                ]
            )
    others = [g for g in v.step.grants if g.kind != "skill"]
    if others:
        out.append(_grants(v.page, replace(v.step, grants=tuple(others))))
    out.extend(_picks(v, v.step.picks))
    return out


# -- equipment -------------------------------------------------------------------------


def _kinds_text(page: Page, kinds: Sequence[str]) -> str:
    """What a deferred picker offers; several kinds are one choice."""
    return " / ".join(page.t(f"chargen.equipment_type.{kind}") for kind in kinds)


def _entry_text(v: View, entry: object) -> str:
    """One line of a package: "Chain Mail", "8 Javelins", "4 GP", "any martial weapon"."""
    page = v.page
    if isinstance(entry, str):
        found = v.planner.catalogue.item(entry)
        return _term(page, "item", found.name if found else _name(entry))
    if not isinstance(entry, Mapping):
        return ""
    quantity = int(entry.get("quantity", 1))
    prefix = f"{quantity} × " if quantity > 1 else ""
    if "value" in entry:
        return _coins_text(page, int(entry["value"]))
    kinds = equipment_kinds(entry)
    if kinds:
        return prefix + _kinds_text(page, kinds)
    if entry.get("item"):
        found = v.planner.catalogue.item(str(entry["item"]))
        name = found.name if found else _name(str(entry["item"]))
        # A display name ("Holy Symbol (amulet)") says more than the item; it is shown.
        return prefix + _term(page, "item", str(entry.get("displayName") or name))
    if entry.get("special"):
        return prefix + str(entry["special"])
    return ""


def _coins_text(page: Page, copper: int) -> str:
    """Copper as gold, silver and copper, with the language's coin names ("4 GM 5 SM")."""
    gp, rest = divmod(copper, 100)
    sp, cp = divmod(rest, 10)
    unit = {k: page.t(f"treasure.coin.{k}") for k in ("gp", "sp", "cp")}
    parts = [f"{n} {unit[k]}" for n, k in ((gp, "gp"), (sp, "sp"), (cp, "cp")) if n]
    return " ".join(parts) or f"0 {unit['gp']}"


def _package_groups(v: View, record: Offer | None, prefix: str) -> list[Node]:
    if record is None:
        return []
    t = v.page.t
    raw = record.data.get("startingEquipment")
    groups = raw.get("defaultData") if isinstance(raw, Mapping) else raw
    out: list[Node] = []
    for index, group in enumerate(groups if isinstance(groups, list) else []):
        if not isinstance(group, Mapping):
            continue
        pick_id = f"{prefix}.{index}"
        keys = [k for k in group if k != "_"]
        fixed = [_entry_text(v, e) for e in group.get("_", [])]
        choice = v.step.pick(pick_id)
        if fixed:
            out.append(
                div(class_="cg-pack-fixed")[
                    span(class_="eyebrow")[
                        t("chargen.always", origin=_origin(v.page, record.name))
                    ],
                    p[", ".join(x for x in fixed if x)],
                ]
            )
        if choice is not None:
            selected = (v.selected(choice) or [""])[0]
            cards = [
                label(class_="pick cg-card", for_=f"{pick_id}--{key.lower()}")[
                    _choice_input(choice, key.lower(), key.lower() == selected),
                    span(class_="cg-card-head")[
                        b[key.upper()], span(class_="chip-src")[_origin(v.page, record.name)]
                    ],
                    span(class_="pd")[
                        ", ".join(x for x in (_entry_text(v, e) for e in group[key]) if x)
                    ],
                ]
                for key in keys
            ]
            out.append(_question(v.page, v.step, choice, div(class_="cardgrid cg-packs")[cards]))
        for pick in v.step.picks:
            if pick.kind == "item" and pick.id.startswith(pick_id + "."):
                out.append(_item_question(v, pick))
    return out


def _item_question(v: View, pick: Pick) -> Element:
    """A deferred picker — "any martial weapon" — as one select per item to choose."""
    chosen = v.selected(pick)
    selects = [
        select(name=pick.id, id=f"{pick.id}-{n}", class_="cg-level cg-wide")[
            option(value="")["—"],
            [
                option(value=o, selected=n < len(chosen) and chosen[n] == o)[
                    _term(v.page, "item", _name(o))
                ]
                for o in pick.options
            ],
        ]
        for n in range(pick.count)
    ]
    return fieldset(class_="cg-question", id=f"q-{pick.id}")[
        legend(class_="cg-legend")[
            b[_kinds_text(v.page, pick.label.split())],
            span(class_="chip-src")[_origin(v.page, pick.origin)],
        ],
        _problems(v.page, v.step, pick),
        div(class_="cg-weights")[selects],
    ]


def _gold_formula(page: Page, roll: GoldRoll) -> str:
    """``5d4 × 10`` as the language writes dice ("5W4 × 10")."""
    times = f" × {roll.times}" if roll.times > 1 else ""
    return f"{roll.count}{page.t('rules.dice_letter')}{roll.faces}{times}"


def _gold_roll(v: View, roll: GoldRoll) -> Node:
    """The button that rolls starting gold, then what it rolled."""
    t = v.page.t
    gold = v.planner.gold(v.pc, v.plan.cls)
    url = f"{wizard_url(v.pc.id, 'equipment')}/roll-gold"
    again = gold is None or (v.world is not None and v.world.allow_rerolls)
    button_el = button(
        type="submit",
        formaction=url,
        hx_post=url,
        hx_target="#wizard",
        hx_swap="outerHTML",
        class_="btn" + (" btn-primary" if gold is None else ""),
    )[
        icon("dices", "sm"),
        span[t("chargen.gold_roll", dice=_gold_formula(v.page, roll))],
    ]
    if gold is None:
        return button_el
    return div(class_="cg-rolls", role="status")[
        span(class_="eyebrow")[t("chargen.gold_rolled")],
        span(class_="cg-roll mono")[_coins_text(v.page, gold)],
        button_el if again else small(class_="muted")[t("chargen.rolls_final")],
    ]


def _shop_row(v: View, offer: Offer, owned: int, left: int) -> Element:
    """One thing for sale: its name, its price, and what has been bought of it."""
    t, page = v.page.t, v.page
    url = wizard_url(v.pc.id, "equipment")
    catalogue = v.planner.catalogue
    category = "baseitem" if catalogue.find("baseitem", offer.reference) else "item"
    name = _term(page, "item", offer.name)
    cost = price(offer)

    def trade(sign: str, glyph: str, words: str, *, disabled: bool = False) -> Element:
        return button(
            type="submit",
            name=TRADE,
            value=f"{sign}{offer.reference}",
            id=f"shop-{'buy' if sign == '+' else 'back'}-{offer.reference}",
            hx_post=url,
            hx_target="#wizard",
            hx_swap="outerHTML",
            class_="btn btn-sm cg-step-btn",
            disabled=disabled,
            aria_label=words,
        )[glyph]

    return div(
        class_="cg-option cg-shop-row" + (" is-owned" if owned else ""),
        data_name=f"{offer.name} {name}".lower(),
        data_level=shelf(offer),
    )[
        a(
            class_="ref cg-option-name",
            href=ref_url(category, offer.name, offer.source, v.pc.world_id)
            if v.page.can_read(category, offer.data)
            else None,
        )[name],
        span(class_="cg-shop-price mono muted")[f"{_coins_text(page, cost)} · {offer.source}"],
        span(class_="cg-shop-trade")[
            [
                trade("-", "−", t("chargen.shop.back", name=name)),
                span(class_="mono")[str(owned)],
            ]
            if owned
            else None,
            trade("+", "+", t("chargen.shop.buy", name=name), disabled=cost > left),
        ],
    ]


def _shop(v: View) -> Node:
    """The shop: everything mundane the books price, and a purse that runs down."""
    t = v.page.t
    start, spent = v.planner.purse(v.pc)
    bought = v.pc.choices.get(SHOP) or []
    if start <= 0 and not bought:
        return None
    left = max(start - spent, 0)
    owned = Counter(bought)
    offers = v.planner.shop_offers(v.pc)
    shelves = {shelf(o) for o in offers}
    return div(class_="cg-question cg-shop", id="q-equipment-shop")[
        div(class_="cg-legend cg-shop-head")[
            b[t("chargen.shop.heading")],
            span(class_="cg-count mono")[t("chargen.shop.left", coins=_coins_text(v.page, left))],
        ],
        p(class_="muted cg-hint")[t("chargen.shop.hint")],
        div(class_="cg-spell-tools")[
            input_el(
                type="search",
                class_="cg-filter",
                data_filter=SHOP,
                placeholder=t("chargen.filter"),
                aria_label=t("chargen.filter"),
            ),
            div(class_="cg-level-chips", role="group", aria_label=t("chargen.shop.heading"))[
                button(type="button", class_="chip", data_level_chip="", aria_pressed="true")[
                    t("chargen.shop.all")
                ],
                [
                    button(type="button", class_="chip", data_level_chip=key, aria_pressed="false")[
                        t(f"chargen.shop.kind.{key}")
                    ]
                    for key in SHELVES
                    if key in shelves
                ],
            ],
        ],
        div(class_="cg-options cg-options-wide", data_options=SHOP)[
            [_shop_row(v, o, owned[o.reference], left) for o in offers]
        ],
    ]


def _equipment_step(v: View) -> Node:
    t = v.page.t
    take = v.step.pick("equipment")
    way = (v.pc.choices.get("equipment") or [""])[0]
    cls = v.plan.cls
    roll = starting_gold(cls.data) if cls is not None else None
    by_gold = way == GOLD and roll is not None
    out: list[Node] = []
    if not by_gold:
        out.extend(_package_groups(v, v.plan.cls, "equipment.class"))
        out.extend(_package_groups(v, v.plan.background, "equipment.background"))
    if take is not None:
        out.append(_problems(v.page, v.step, take))
        cards = [
            label(class_="pick cg-card cg-take", for_="equipment--packages")[
                _choice_input(take, "packages", way == "packages"),
                span(class_="cg-card-head")[b[t("chargen.pick.take")]],
                span(class_="pd")[t("chargen.take_hint")],
            ]
        ]
        if roll is not None:
            cards.append(
                label(class_="pick cg-card cg-take", for_=f"equipment--{GOLD}")[
                    _choice_input(take, GOLD, way == GOLD),
                    span(class_="cg-card-head")[b[t("chargen.pick.gold")]],
                    span(class_="pd")[t("chargen.gold_hint", dice=_gold_formula(v.page, roll))],
                ]
            )
        out.append(div(class_="cardgrid cg-packs")[cards])
    if by_gold and roll is not None:
        out.append(_gold_roll(v, roll))
    pc = v.pc
    if way in ("packages", GOLD) and (pc.inventory or by_gold):
        coins = pc.currency
        out.append(
            section(class_="cg-features")[
                span(class_="eyebrow")[t("chargen.you_carry")],
                ul(class_="cg-feature-list cg-inventory")[
                    [
                        li[
                            span(class_="mono muted")[str(i.quantity)],
                            span(title=i.item.name)[
                                _term(v.page, "item", i.item.name),
                                span(class_="chip-src")[t("chargen.worn")] if i.equipped else None,
                            ],
                        ]
                        for i in pc.inventory
                    ]
                ],
                p(class_="mono")[_coins_text(v.page, coins.gp * 100 + coins.sp * 10 + coins.cp)],
            ]
        )
    if way in ("packages", GOLD):
        out.append(_shop(v))
    return out


# -- spells ----------------------------------------------------------------------------

SCHOOLS = ("A", "C", "D", "E", "V", "I", "N", "T")


def _spell_facts(data: Mapping[str, object]) -> tuple[int, str, bool, bool]:
    level = data.get("level")
    durations = data.get("duration")
    concentration = isinstance(durations, list) and any(
        isinstance(d, Mapping) and d.get("concentration") for d in durations
    )
    meta = data.get("meta")
    ritual = isinstance(meta, Mapping) and bool(meta.get("ritual"))
    return (
        (level if isinstance(level, int) else 0),
        str(data.get("school", "")),
        concentration,
        ritual,
    )


def _spell_question(v: View, pick: Pick, hint: str | None = None) -> Element:
    """A list of spells: filter by level, preview on hover or tap, a running count."""
    t = v.page.t
    index = v.planner.catalogue.index("spell")
    chosen = {x.lower() for x in v.selected(pick)}
    rows = []
    levels: set[int] = set()
    for ref in pick.options:
        offer = index.get(ref.lower())
        if offer is None:
            continue
        level, school, concentration, ritual = _spell_facts(offer.data)
        levels.add(level)
        facts = [
            t("sheet.cantrip") if level == 0 else t("chargen.level_n", n=level),
            t(f"chargen.school.{school}") if school in SCHOOLS else school,
        ]
        rows.append(
            label(
                class_="cg-option cg-spell",
                for_=f"{pick.id}--{ref}",
                data_name=offer.name.lower(),
                data_level=str(level),
            )[
                _choice_input(pick, ref, ref.lower() in chosen),
                a(
                    class_="ref cg-option-name",
                    href=ref_url("spell", offer.name, offer.source, v.pc.world_id)
                    if v.page.can_read("spell", offer.data)
                    else None,
                )[offer.name],
                span(class_="chip-src")[offer.source],
                span(class_="cg-option-text")[
                    " · ".join(facts),
                    span(class_="pill magic cg-tag")["C"] if concentration else None,
                    span(class_="pill neutral cg-tag")["R"] if ritual else None,
                ],
            ]
        )
    chips = None
    if len(levels) > 1:
        chips = div(class_="cg-level-chips", role="group", aria_label=t("chargen.filter_level"))[
            button(type="button", class_="chip", data_level_chip="", aria_pressed="true")[
                t("chargen.all_levels")
            ],
            [
                button(type="button", class_="chip", data_level_chip=str(n), aria_pressed="false")[
                    t("sheet.cantrip") if n == 0 else str(n)
                ]
                for n in sorted(levels)
            ],
        ]
    picked = len([x for x in v.selected(pick) if x])
    return fieldset(class_="cg-question", id=f"q-{pick.id}")[
        legend(class_="cg-legend")[
            b[pick_title(v.page, pick)],
            span(class_="cg-count mono" + (" full" if picked >= pick.count else ""))[
                f"{picked} / {pick.count}"
            ],
        ],
        p(class_="muted cg-hint")[hint] if hint else None,
        _problems(v.page, v.step, pick),
        div(class_="cg-spell-tools")[_filter(v.page, pick, len(rows) + FILTER_FROM), chips],
        div(class_="cg-options cg-options-wide", data_options=pick.id, data_max=str(pick.count))[
            rows
        ],
    ]


def _spells_step(v: View) -> Node:
    t = v.page.t
    levels = v.plan.level
    spells = levels.spells if levels is not None else None
    out: list[Node] = []
    if spells is not None and (spells.cantrips or spells.known or spells.prepared):
        out.append(
            p(class_="muted")[
                t(
                    "chargen.spells_lede",
                    level=spells.max_level,
                )
                if spells.max_level
                else t("chargen.cantrips_only")
            ]
        )
    for pick in v.step.picks:
        hint = None
        if pick.kind in ("variant", "casting"):
            out.extend(_picks(v, [pick]))
            continue
        if pick.id.startswith("granted.") and pick.label:
            hint = t(f"chargen.granted_use.{pick.label}")
        elif pick.id == "spells.known" and spells is not None and spells.spellbook:
            hint = t("chargen.spellbook_hint")
        elif pick.id == "spells.prepared":
            hint = (
                t("chargen.prepare_from_book")
                if spells is not None and spells.known
                else t("chargen.prepare_from_list")
            )
            if not pick.options:
                out.append(
                    fieldset(class_="cg-question")[
                        legend(class_="cg-legend")[b[pick_title(v.page, pick)]],
                        p(class_="muted")[t("chargen.book_first")],
                    ]
                )
                continue
        out.append(_spell_question(v, pick, hint))
    return out


STEP_VIEWS: dict[str, Callable[[View], Node]] = {
    "species": _species_step,
    "class": _class_step,
    "abilities": _abilities_step,
    "background": _background_step,
    "features": _features_step,
    "skills": _skills_step,
    "equipment": _equipment_step,
    "spells": _spells_step,
    "details": _details_step,
}


# -- the wizard ------------------------------------------------------------------------


def wizard(
    page: Page,
    pc: PlayerCharacter,
    plan: Plan,
    step_id: str,
    planner: Planner,
    *,
    world: World | None = None,
    flash: str | None = None,
) -> Element:
    """The form for one step: rail, questions, derived panel, footer."""
    t = page.t
    step = plan.step(step_id) or plan.steps[0]
    view = STEP_VIEWS.get(step.id, _generic)
    url = wizard_url(pc.id, step.id)
    return form(
        id="wizard",
        class_="wizard cg-wizard",
        method="post",
        action=url,
        hx_post=url,
        hx_target="this",
        hx_swap="outerHTML",
        hx_trigger="change[!event.target.matches('[data-filter]')]",
        data_step=step.id,
    )[
        _rail(page, pc, plan, step.id),
        div(class_="cg-body")[
            div(class_="cg-main")[
                alert(flash),
                h2(class_="cg-step-title")[t(f"chargen.step.{step.id}")],
                p(class_="muted cg-step-lede")[t(f"chargen.lede.{step.id}")],
                view(View(page, pc, plan, step, planner, world)),
            ],
            _derived(page, pc, plan),
        ],
        _footer(page, pc, plan, step),
    ]


def wizard_page(
    page: Page,
    pc: PlayerCharacter,
    plan: Plan,
    step_id: str,
    planner: Planner,
    *,
    world: World | None,
    flash: str | None = None,
) -> Element:
    """The wizard as a full page."""
    t = page.t
    return app_shell(
        page,
        pc.name or t("chargen.title"),
        div(class_="cg-page")[
            div(class_="cg-head")[
                span(class_="eyebrow")[world.name if world else t("chargen.all_books")],
                h1[pc.name or t("chargen.title")],
            ],
            wizard(page, pc, plan, step_id, planner, world=world, flash=flash),
        ],
        extra_scripts=SCRIPTS,
        main_class="shell-main shell-wide",
        world_id=pc.world_id,
        player_bar=False,
    )


def new_character_page(
    page: Page,
    worlds: Sequence[World],
    players: Sequence[str] | None,
    *,
    error: str | None = None,
) -> Element:
    """Start a character: pick its world (and, for the gamemaster, its player)."""
    t = page.t
    return app_shell(
        page,
        t("chargen.title"),
        div(class_="formpage")[
            page_head(t("chargen.eyebrow"), t("chargen.title"), t("chargen.new_lede")),
            alert(error),
            form(method="post", action=CHARGEN_URL, class_="panel cg-new")[
                label(class_="field", for_="cg-world")[
                    span(class_="field-label")[t("chargen.world")],
                    select(name="world", id="cg-world", required=True)[
                        [option(value=w.id)[w.name] for w in worlds]
                    ],
                    small(class_="field-hint")[t("chargen.world_hint")],
                ]
                if worlds
                else p(class_="muted")[t("chargen.no_worlds")],
                label(class_="field", for_="cg-player")[
                    span(class_="field-label")[t("chargen.player")],
                    select(name="player", id="cg-player")[
                        option(value="")[t("chars.unassigned")],
                        [option(value=name)[name] for name in players],
                    ],
                ]
                if players is not None
                else None,
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("sparkles"), span[t("chargen.start")]
                    ]
                ],
            ],
        ],
    )


__all__ = [
    "CHARGEN_URL",
    "new_character_page",
    "pick_title",
    "wizard",
    "wizard_page",
    "wizard_url",
]
