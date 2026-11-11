"""Levelling up (documentation/CHARGEN-WIZARD.md §9): one page for one level.

It asks only what the new level brings, with the creation wizard's own question
renderers, so an Ability Score Improvement, an invocation or a spell looks the same in
both places; the derived panel shows the character at the new level while choosing.
When another class is open to the character, the first question is which class gains
the level, and the classes the scores keep out say what they need.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    div,
    fieldset,
    form,
    h1,
    h2,
    label,
    legend,
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    ul,
)

from app.models.gamestate import World
from app.models.pc import HitPointMethod, PlayerCharacter
from app.services.chargen import Pick, Plan, Planner, Step
from app.services.chargen.catalogue import Offer
from app.services.chargen.levelup import SWAP, LevelUp
from app.services.chargen.rules import Requirement
from app.services.text import strip_tags
from app.ui.chargen_pages import (
    SCRIPTS,
    View,
    _asi_question,
    _card_question,
    _derived,
    _described_by,
    _list_question,
    _picks,
    _plain_body,
    _problems,
    _spell_question,
    _summary_of,
)
from app.ui.context import Page
from app.ui.forms import alert
from app.ui.icons import icon
from app.ui.layout import app_shell


def levelup_url(character_id: str, suffix: str = "") -> str:
    """The level-up page of a character, or one of its actions."""
    return f"/characters/{character_id}/level-up{suffix}"


def _view(
    page: Page, pc: PlayerCharacter, lu: LevelUp, planner: Planner, world: World | None
) -> View:
    """The wizard's view over this level's questions and in-progress answers."""
    answers = {
        k.removeprefix("levelup."): v for k, v in pc.choices.items() if k.startswith("levelup.")
    }
    step = Step("features", lu.picks, (), lu.missing, lu.problems)
    plan = Plan(
        lu.cls.edition,
        (step,),
        None,
        cls=lu.cls,
        subclass=lu.subclass,
        sheet=lu.after,
        verdicts=lu.verdicts,
    )
    return View(page, pc.model_copy(update={"choices": answers}), plan, step, planner, world)


def requirement_text(page: Page, need: Requirement) -> str:
    """ "Strength 13 or Dexterity 13", in the reader's language."""
    t = page.t
    return f" {t('levelup.req_or')} ".join(
        f" {t('levelup.req_and')} ".join(f"{t(f'ability.{k}')} {v}" for k, v in option.items())
        for option in need
    )


def _class_body(page: Page, pc: PlayerCharacter) -> Callable[[Offer], Node]:
    """A class card: the step it gives (Wizard 10 → 11), or a new class and its die."""
    t = page.t
    levels = {(c.name.lower(), (c.source or "").lower()): c.level for c in pc.classes}

    def body(o: Offer) -> Node:
        hd = o.data.get("hd")
        faces = hd.get("faces") if isinstance(hd, dict) else None
        level = levels.get((o.name.lower(), o.source.lower()))
        if level is None:
            level = next((n for (name, _src), n in levels.items() if name == o.name.lower()), None)
        line = (
            t("levelup.class_next", level=level, next=level + 1)
            if level is not None
            else t("levelup.class_new")
        )
        return span(class_="pd mono")[f"{line} · d{faces}" if faces else line]

    return body


def _class_notes(page: Page, lu: LevelUp) -> Node:
    t = page.t
    if lu.held:
        return p(class_="muted cg-note")[
            t(
                "levelup.held",
                classes=", ".join(
                    f"{cls.name} ({requirement_text(page, need)})" for cls, need in lu.held
                ),
            )
        ]
    if lu.blocked:
        return p(class_="muted cg-note")[
            t(
                "levelup.blocked",
                classes=", ".join(
                    f"{cls.name} ({requirement_text(page, need)})" for cls, need in lu.blocked
                ),
            )
        ]
    return None


def _multiclass_gains(lu: LevelUp) -> list[str]:
    """What a new class's reduced proficiencies add, as plain words."""
    if not lu.new_class:
        return []
    gained = (lu.cls.data.get("multiclassing") or {}).get("proficienciesGained") or {}
    words = []
    for key in ("armor", "weapons", "tools"):
        for entry in gained.get(key) or []:
            text = entry if isinstance(entry, str) else str(entry.get("proficiency", ""))
            words.append(strip_tags(text))
    return words


def _swap_question(v: View, out: Pick, lu: LevelUp) -> Element:
    """Replace one spell, cantrip, option (an invocation, maneuver or Metamagic) or the
    Fighting Style: two selects, both left empty to keep everything."""
    t = v.page.t
    into = next(p for p in lu.picks if p.id == out.id.removesuffix("out") + "in")
    category = {"spell": "spell", "cantrip": "spell", "feat": "feat"}.get(
        out.kind, "optionalfeature"
    )
    catalogue = v.planner.catalogue

    def name(ref: str) -> str:
        offer = catalogue.find(category, ref)
        if offer is None:
            return ref.split("|")[0]
        level = offer.data.get("level")
        return f"{offer.name} ({level})" if category == "spell" and level else offer.name

    def choices(pick: Pick, empty: str) -> Element:
        chosen = (v.selected(pick) or [""])[0].lower()
        return select(name=pick.id, id=f"q-{pick.id}-select", class_="cg-select")[
            option(value="")[empty],
            [
                option(
                    value=ref,
                    selected=ref.lower() == chosen,
                    disabled=not lu.verdicts[ref].allowed if ref in lu.verdicts else False,
                )[name(ref)]
                for ref in pick.options
            ],
        ]

    kind = {"spell": "spell", "cantrip": "cantrip", "feat": "style"}.get(out.kind, "invocation")
    if out.kind == "optional" and out.options:
        held = catalogue.find("optionalfeature", out.options[0])
        types = (held.data.get("featureType") or []) if held is not None else []
        kind = "maneuver" if "MV:B" in types else "metamagic" if "MM" in types else kind
    return fieldset(class_="cg-question", id=f"q-{out.id}")[
        legend(class_="cg-legend")[
            b[t(f"levelup.swap_{kind}")], small(class_="muted")[t("common.optional")]
        ],
        _problems(v.page, v.step, out),
        _problems(v.page, v.step, into),
        div(class_="cg-swap")[
            label(class_="cg-swap-part")[
                span(class_="muted")[t("levelup.swap_out")], choices(out, t("levelup.swap_keep"))
            ],
            label(class_="cg-swap-part")[
                span(class_="muted")[t("levelup.swap_in")], choices(into, "—")
            ],
        ],
    ]


def _question(v: View, pick: Pick, lu: LevelUp) -> Node:
    if pick.id.startswith(SWAP):
        return _swap_question(v, pick, lu) if pick.id.endswith(".out") else None
    if pick.kind == "class":
        offers = [o for ref in pick.options if (o := v.planner.catalogue.find("class", ref))]
        selected = v.selected(pick) or [lu.cls.reference]
        asked = replace(pick, label=v.page.t("levelup.which_class"))
        fixed = lu.hp_roll is not None and not (v.world is not None and v.world.allow_rerolls)
        return [
            _card_question(
                v.page,
                v.step,
                asked,
                offers,
                selected,
                _class_body(v.page, v.pc),
                planner=v.planner,
                category="class",
                world_id=v.pc.world_id,
            ),
            p(class_="muted cg-note")[v.page.t("levelup.class_fixed")] if fixed else None,
            _class_notes(v.page, lu),
        ]
    if pick.kind == "asi":
        return _asi_question(v, pick)
    if pick.kind == "subclass" and v.plan.cls is not None:
        offers = v.planner.catalogue.subclasses(v.plan.cls)
        return _card_question(
            v.page,
            v.step,
            pick,
            offers,
            v.selected(pick),
            _plain_body(v.page),
            planner=v.planner,
            category="subclass",
            world_id=v.pc.world_id,
        )
    if pick.kind in ("cantrip", "spell"):
        return _spell_question(v, pick)
    if pick.kind == "featureoption":
        return _list_question(
            v.page,
            v.step,
            pick,
            list(pick.options),
            v.selected(pick),
            v.plan.verdicts,
            describe=_described_by(pick),
        )
    if pick.kind in ("optional", "feat"):
        category = "optionalfeature" if pick.kind == "optional" else "feat"
        return _list_question(
            v.page,
            v.step,
            pick,
            v.planner.options(pick),
            v.selected(pick),
            v.plan.verdicts,
            describe=_summary_of(v, category),
        )
    return _picks(v, [pick])


def _hit_points(v: View, lu: LevelUp, world: World | None) -> Element:
    t = v.page.t
    if lu.hp_method is HitPointMethod.ROLLED:
        url = levelup_url(v.pc.id, "/roll-hp")
        may_roll = lu.hp_roll is None or (world is not None and world.allow_rerolls)
        roll: Node = (
            button(
                type="submit",
                formaction=url,
                hx_post=url,
                hx_target="#levelup",
                hx_swap="outerHTML",
                class_="btn" + ("" if lu.hp_roll else " btn-primary"),
            )[
                icon("dices", "sm"),
                span[
                    t(
                        "levelup.roll_hp" if lu.hp_roll is None else "chargen.roll_again",
                        faces=lu.hit_die,
                    )
                ],
            ]
            if may_roll
            else small(class_="muted")[t("chargen.rolls_final")]
        )
        body: Node = div(class_="cg-rolls", role="status")[
            span(class_="cg-roll mono")[str(lu.hp_roll)] if lu.hp_roll is not None else None,
            roll,
        ]
    else:
        body = p(class_="muted")[
            t("levelup.hp_average", each=lu.hit_die // 2 + 1, faces=lu.hit_die)
        ]
    return section(class_="cg-question cg-hp")[
        div(class_="cg-legend")[b[t("chargen.pick.hp")]],
        body,
        p[t("levelup.hp_gain", n=lu.hp_gain)]
        if lu.hp_method is HitPointMethod.AVERAGE or lu.hp_roll
        else None,
    ]


def levelup_form(
    page: Page,
    pc: PlayerCharacter,
    lu: LevelUp,
    planner: Planner,
    *,
    world: World | None = None,
    flash: str | None = None,
) -> Element:
    """The form: what the level gives, what it asks, the character after it."""
    t = page.t
    v = _view(page, pc, lu, planner, world)
    url = levelup_url(pc.id)
    gains = [f"{name}" for name, _level in lu.gains]
    proficient = _multiclass_gains(lu)
    if proficient:
        gains.insert(0, t("levelup.proficiencies", list=", ".join(proficient)))
    reason = None if lu.complete else t("chargen.answer_first")
    return form(
        id="levelup",
        class_="wizard cg-wizard",
        method="post",
        action=url,
        hx_post=url,
        hx_target="this",
        hx_swap="outerHTML",
        hx_trigger="change[!event.target.matches('[data-filter]')]",
    )[
        div(class_="cg-body")[
            div(class_="cg-main")[
                alert(flash),
                # Which class first: what the level brings depends on it.
                [_question(v, pick, lu) for pick in lu.picks if pick.kind == "class"]
                or _class_notes(page, lu),
                h2(class_="cg-step-title")[t("levelup.title", cls=lu.cls.name, level=lu.to_level)],
                section(class_="cg-features")[
                    span(class_="eyebrow")[t("levelup.gains")],
                    ul(class_="cg-feature-list")[
                        [li[span(class_="muted", aria_hidden="true")["+"], span[g]] for g in gains]
                    ]
                    if gains
                    else p(class_="muted")[t("levelup.no_new_features")],
                ],
                [_question(v, pick, lu) for pick in lu.picks if pick.kind != "class"],
                p(class_="muted")[t("levelup.prepared", n=lu.prepared)] if lu.prepared else None,
                _hit_points(v, lu, world),
            ],
            _derived(page, v.pc.model_copy(update={"name": pc.name}), v.plan),
        ],
        div(class_="wizard-foot cg-foot")[
            a(href=f"/characters/{pc.id}", class_="btn btn-ghost")[
                icon("arrow-left", "sm"), span[t("levelup.back")]
            ],
            div(class_="cg-foot-actions")[
                button(type="submit", name="go", value="stay", class_="btn js-hide")[
                    t("chargen.save")
                ],
                button(
                    type="submit",
                    name="go",
                    value="take",
                    class_="btn btn-primary",
                    disabled=not lu.complete,
                    title=reason,
                )[icon("check", "sm"), span[t("levelup.take", level=lu.character_level)]],
            ],
            p(class_="cg-foot-why muted")[reason] if reason else None,
        ],
    ]


def levelup_page(
    page: Page,
    pc: PlayerCharacter,
    lu: LevelUp,
    planner: Planner,
    *,
    world: World | None = None,
    flash: str | None = None,
) -> Element:
    """The level-up page."""
    t = page.t
    return app_shell(
        page,
        t("levelup.page_title", name=pc.name or "?"),
        div(class_="cg-page")[
            div(class_="cg-head")[
                span(class_="eyebrow")[t("levelup.eyebrow")],
                h1[t("levelup.heading", name=pc.name or "?", level=lu.character_level)],
            ],
            levelup_form(page, pc, lu, planner, world=world, flash=flash),
        ],
        extra_scripts=SCRIPTS,
        main_class="shell-main shell-wide",
        world_id=pc.world_id,
        player_bar=False,
    )


__all__ = ["levelup_form", "levelup_page", "levelup_url"]
