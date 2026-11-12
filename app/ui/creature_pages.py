"""The creature builder's pages (app/services/creature_builder.py).

* ``/gm/creatures`` — the designs, with their CR and whether they are published, and a
  form to start one blank (at a CR) or from a printed creature;
* ``/gm/creatures/{id}`` — the builder: one form, every button submits it with an
  ``action``, so nothing typed is lost and the page works without JavaScript. Beside it
  the CR the design plays at, worked out, and its statblock as it will be published.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    dd,
    div,
    dl,
    dt,
    form,
    h2,
    img,
    label,
    li,
    option,
    p,
    section,
    select,
    span,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import CreatureBlock, CreatureDesign, World
from app.services.creature_builder import (
    ABILITIES,
    ALIGNMENTS,
    BLOCK_KINDS,
    DAMAGE_TYPES,
    HOMEBREW_SOURCE,
    SIZES,
    SPEEDS,
    TYPES,
    CrEstimate,
    modifier,
    signed,
)
from app.services.references import ReferenceEntry
from app.services.translation_targets import BUILDER_FIELDS, builder_key
from app.store.creature_tables import BlockTemplate, CrRow, MonsterFeature
from app.ui.auth_pages import message_panel
from app.ui.context import Page
from app.ui.creature_links import CREATURES_URL, creature_url
from app.ui.forms import alert, hidden, notice, select_field, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import delete_link
from app.ui.ref_pages import statblock
from app.ui.rules_text import senses_shown
from app.ui.tags import RenderContext, ref_url
from app.ui.translations_pages import BUILDER_URL


def _cr_choices(rows: Sequence[CrRow]) -> list[tuple[str, str]]:
    return [(r.cr, r.cr) for r in rows]


def _world_choices(page: Page, worlds: Sequence[World]) -> list[tuple[str, str]]:
    return [("", page.t("creatures.no_world")), *((w.id, w.name) for w in worlds)]


# -- the list ------------------------------------------------------------------------------


@dataclass(frozen=True)
class DesignRow:
    """One design in the list."""

    design: CreatureDesign
    plays_at: str | None
    world: str
    unpublished: bool


def creatures_page(
    page: Page,
    rows: Sequence[DesignRow],
    worlds: Sequence[World],
    crs: Sequence[CrRow],
    *,
    notice_text: str | None = None,
    error: str | None = None,
    draft: dict[str, str] | None = None,
) -> Element:
    """Every design, and a form to start one."""
    t = page.t
    draft = draft or {}
    items = [
        li(class_="list-row")[
            span(class_="avatar", aria_hidden="true")[icon("skull")],
            div(class_="list-main")[
                a(href=creature_url(r.design.id), class_="list-title list-link")[r.design.name],
                span(class_="list-meta")[
                    " · ".join(
                        x
                        for x in (
                            t("creatures.cr_short", cr=r.design.cr),
                            t("creatures.plays_at", cr=r.plays_at) if r.plays_at else "",
                            r.world,
                            t("creatures.based_on", ref=r.design.based_on)
                            if r.design.based_on
                            else "",
                        )
                        if x
                    )
                ],
            ],
            span(class_="pill accent", title=t("creatures.revision_hint"))[t("creatures.revision")]
            if r.design.revision_of
            else span(class_="pill accent", title=t("creatures.draft_hint"))[t("creatures.draft")]
            if r.design.drafted_by
            else span(class_="pill cond")[t("creatures.unpublished")]
            if r.unpublished
            else None,
            delete_link(page, f"{creature_url(r.design.id)}/delete", t("creatures.delete")),
        ]
        for r in rows
    ]
    create = form(method="post", action=CREATURES_URL, class_="panel stack-form create-form")[
        div(class_="panel-head")[b[t("creatures.new")]],
        alert(error),
        div(class_="cb-new-fields")[
            text_field(
                "name",
                t("creatures.name"),
                value=draft.get("name", ""),
                autocomplete="off",
                required=False,
                optional_text=t("common.optional"),
            ),
            select_field(
                "world",
                t("creatures.world"),
                _world_choices(page, worlds),
                selected=draft.get("world", worlds[0].id if len(worlds) == 1 else ""),
            ),
            select_field("cr", t("creatures.cr"), _cr_choices(crs), selected=draft.get("cr", "1")),
            text_field(
                "based_on",
                t("creatures.start_from"),
                value=draft.get("based_on", ""),
                autocomplete="off",
                required=False,
                optional_text=t("common.optional"),
                hint=t("creatures.start_from_hint"),
            ),
        ],
        div(class_="form-actions")[
            button(type="submit", class_="btn btn-primary")[
                icon("plus"), span[t("creatures.create")]
            ]
        ],
    ]
    return app_shell(
        page,
        t("creatures.page_title"),
        [
            page_head(t("gm.eyebrow"), t("creatures.heading"), t("creatures.lede")),
            notice(notice_text),
            ul(class_="list")[items]
            if items
            else empty_state("skull", t("creatures.empty_heading"), t("creatures.empty_text")),
            create,
        ],
    )


# -- the builder ---------------------------------------------------------------------------


@dataclass
class BuilderView:
    """Everything the builder shows."""

    design: CreatureDesign
    worlds: Sequence[World]
    crs: Sequence[CrRow]
    estimate: CrEstimate | None
    preview: ReferenceEntry
    hp: tuple[int, str]
    traits: Sequence[BlockTemplate]
    actions: Sequence[BlockTemplate]
    features: Sequence[MonsterFeature]
    unpublished: bool
    published: bool
    # The address of its picture (versioned), when it has one.
    picture: str | None = None
    # A revision's creature, and the fields the revision changes on it.
    original: CreatureDesign | None = None
    changes: Sequence[str] = ()
    # Claude's open revision of this creature.
    revision: CreatureDesign | None = None


# Labels of the fields a revision changes, where the field's own label is not
# ``creatures.<field>``.
_CHANGE_LABELS = {
    "type_tags": "creatures.type",
    "speed": "creatures.change.speed",
    "based_on": "creatures.change.based_on",
    **{kind: f"creatures.kind.{kind}" for kind in BLOCK_KINDS},
}


def _revision_note(page: Page, view: BuilderView) -> Element | None:
    """What a revision changes on which creature, or that this creature has one waiting."""
    t = page.t
    if view.original is not None:
        changed = ", ".join(t(_CHANGE_LABELS.get(f, f"creatures.{f}")) for f in view.changes)
        return div(class_="notice cb-revision", role="status")[
            icon("pencil"),
            span[
                t("creatures.revision_of"),
                " ",
                a(href=creature_url(view.original.id))[view.original.name],
                ". ",
            ],
            span[t("creatures.revision_changes", fields=changed)]
            if changed
            else span[t("creatures.revision_unchanged")],
        ]
    if view.revision is not None:
        return div(class_="notice cb-revision", role="status")[
            icon("pencil"),
            span[
                t("creatures.revision_waiting"),
                " ",
                a(href=creature_url(view.revision.id))[t("creatures.revision_open")],
            ],
        ]
    return None


def _picture(page: Page, view: BuilderView, error: str) -> Element:
    """Its picture, shown to the players in a fight's Details: uploaded here, or fetched
    by Claude from a web address."""
    t = page.t
    action = f"{creature_url(view.design.id)}/picture"
    return section(id="cb-picture", class_="panel cb-picture")[
        h2[t("creatures.picture")],
        img(src=view.picture, alt=view.design.name, class_="cb-picture-img", loading="lazy")
        if view.picture
        else None,
        p(class_="field-hint")[t("creatures.picture_hint")],
        alert(error or None),
        form(
            method="post",
            action=action,
            enctype="multipart/form-data",
            class_="cb-picture-form",
        )[
            input_el(
                type="file",
                name="picture",
                accept="image/png,image/jpeg,image/webp,image/gif,image/avif",
                required=True,
                aria_label=t("combat.picture.choose"),
            ),
            button(type="submit", class_="btn btn-sm")[t("combat.picture.upload")],
        ],
        form(method="post", action=action, class_="cb-picture-form")[
            hidden("do", "remove"),
            button(type="submit", class_="btn btn-sm btn-ghost")[
                icon("trash-2", "sm"), span[t("combat.picture.remove")]
            ],
        ]
        if view.picture
        else None,
    ]


def _number(
    name: str, caption: str, value: int | None, *, low: int, high: int, hint: str = ""
) -> Element:
    field_id = f"f-{name}"
    return div(class_="field")[
        label(for_=field_id)[caption],
        input_el(
            id=field_id,
            name=name,
            type="number",
            inputmode="numeric",
            min=str(low),
            max=str(high),
            value="" if value is None else str(value),
        ),
        p(class_="field-hint")[hint] if hint else None,
    ]


def _distance(page: Page, name: str, kind: str, feet: int | None) -> Element:
    """A speed in the reader's units: metres on the books' 1.5 m grid, else feet; the
    design keeps feet."""
    t = page.t
    metric = page.u.metric
    field_id = f"f-{name}"
    shown = "" if feet is None else f"{(feet * 3 / 10):g}" if metric else str(feet)
    return div(class_="field")[
        label(for_=field_id)[t(f"creatures.speed.{kind}"), " (", "m" if metric else "ft.", ")"],
        input_el(
            id=field_id,
            name=name,
            type="number",
            inputmode="decimal" if metric else "numeric",
            min="0",
            max="60" if metric else "200",
            step="1.5" if metric else "5",
            value=shown,
        ),
    ]


def _ready_made_label(page: Page, kind: str, template: BlockTemplate) -> str:
    """A ready-made text in the picker: its name in the reader's language where the
    table translated it, with the creature it was taken from ("Biss (Cloaker)")."""
    data = {"name": template.title, "entries": list(template.entries)}
    shown = page.t.overlay("builder", builder_key(kind, template.name), data, BUILDER_FIELDS)
    title = str(shown.data["name"])
    return (
        template.name
        if title == template.title
        else template.name.replace(template.title, title, 1)
    )


def _select(name: str, caption: str, choices: Sequence[tuple[str, str]], selected: str) -> Element:
    return select_field(name, caption, choices, selected=selected)


def _basics(page: Page, view: BuilderView) -> Element:
    t = page.t
    d = view.design
    return section(class_="panel cb-section", aria_labelledby="cb-basics")[
        h2(id="cb-basics")[t("creatures.basics")],
        div(class_="cb-grid")[
            text_field("name", t("creatures.name"), value=d.name, autocomplete="off"),
            _select(
                "world", t("creatures.world"), _world_choices(page, view.worlds), d.world_id or ""
            ),
            _select(
                "size", t("creatures.size"), [(s, t(f"creatures.size.{s}")) for s in SIZES], d.size
            ),
            _select(
                "type", t("creatures.type"), [(x, t(f"creatures.type.{x}")) for x in TYPES], d.type
            ),
            text_field(
                "type_tags",
                t("creatures.tags"),
                value=", ".join(d.type_tags),
                autocomplete="off",
                required=False,
                optional_text=t("common.optional"),
            ),
            _select(
                "alignment",
                t("creatures.alignment"),
                [(x, t(f"creatures.align.{x.replace(' ', '')}")) for x in ALIGNMENTS],
                " ".join(d.alignment),
            ),
            div(class_="cb-cr")[
                _select("cr", t("creatures.cr"), _cr_choices(view.crs), d.cr),
                button(type="submit", name="action", value="fit", class_="btn btn-sm")[
                    icon("wand-sparkles", "sm"), span[t("creatures.fit")]
                ],
            ],
        ],
        p(class_="field-hint")[t("creatures.fit_hint")],
    ]


def _comma(name: str, caption: str, values: Sequence[str], hint: str, optional: str) -> Element:
    return text_field(
        name,
        caption,
        value=", ".join(values),
        autocomplete="off",
        required=False,
        optional_text=optional,
        hint=hint,
    )


def _defence(page: Page, view: BuilderView) -> Element:
    t = page.t
    d = view.design
    optional = t("common.optional")
    hp, formula = view.hp
    return section(class_="panel cb-section", aria_labelledby="cb-defence")[
        h2(id="cb-defence")[t("creatures.defence")],
        div(class_="cb-grid")[
            _number("ac", t("creatures.ac"), d.ac, low=0, high=40),
            text_field(
                "ac_from",
                t("creatures.ac_from"),
                value=d.ac_from,
                autocomplete="off",
                required=False,
                optional_text=optional,
                hint=t("creatures.ac_from_hint"),
            ),
            _number(
                "hit_dice",
                t("creatures.hit_dice"),
                d.hit_dice,
                low=1,
                high=200,
                hint=t("creatures.hp_is", hp=hp, formula=formula),
            ),
        ],
        div(class_="cb-speeds")[[_distance(page, f"speed_{k}", k, d.speed.get(k)) for k in SPEEDS]],
        div(class_="field")[
            span(class_="field-label")[t("creatures.saves")],
            div(class_="role-choices")[
                [
                    label(class_="check-inline")[
                        input_el(type="checkbox", name="saves", value=ab, checked=ab in d.saves),
                        t(f"ability.{ab}"),
                    ]
                    for ab in ABILITIES
                ]
            ],
        ],
        div(class_="cb-grid")[
            _comma("skills", t("creatures.skills"), d.skills, t("creatures.skills_hint"), optional),
            _comma("resist", t("creatures.resist"), d.resist, t("creatures.damage_hint"), optional),
            _comma("immune", t("creatures.immune"), d.immune, t("creatures.damage_hint"), optional),
            _comma(
                "vulnerable",
                t("creatures.vulnerable"),
                d.vulnerable,
                t("creatures.damage_hint"),
                optional,
            ),
            _comma(
                "condition_immune",
                t("creatures.condition_immune"),
                d.condition_immune,
                t("creatures.condition_hint"),
                optional,
            ),
            text_field(
                "senses",
                t("creatures.senses"),
                value=senses_shown(page, d.senses),
                autocomplete="off",
                required=False,
                optional_text=optional,
                hint=t("creatures.senses_hint"),
            ),
            text_field(
                "languages",
                t("creatures.languages"),
                value=d.languages,
                autocomplete="off",
                required=False,
                optional_text=optional,
            ),
        ],
    ]


def _abilities(page: Page, view: BuilderView) -> Element:
    t = page.t
    d = view.design
    return section(class_="panel cb-section", aria_labelledby="cb-abilities")[
        h2(id="cb-abilities")[t("creatures.abilities")],
        div(class_="cb-abilities")[
            [
                _number(
                    f"ab_{ab}",
                    t(f"abbr.{ab}"),
                    d.abilities.get(ab, 10),
                    low=1,
                    high=30,
                    hint=signed(modifier(d.abilities.get(ab, 10))),
                )
                for ab in ABILITIES
            ]
        ],
    ]


def _block(page: Page, kind: str, index: int, block: CreatureBlock) -> Element:
    t = page.t
    base = f"{kind}-{index}"
    return div(class_="cb-block")[
        div(class_="cb-block-head")[
            label(for_=f"f-{base}-name", class_="sr-only")[t("creatures.block_name")],
            input_el(
                id=f"f-{base}-name",
                name=f"{kind}_name",
                value=block.name,
                autocomplete="off",
                placeholder=t("creatures.block_name"),
            ),
            button(
                type="submit",
                name="action",
                value=f"remove:{kind}:{index}",
                class_="btn btn-icon btn-ghost",
                title=t("creatures.remove", name=block.name or "…"),
                aria_label=t("creatures.remove", name=block.name or "…"),
            )[icon("x", "sm")],
        ],
        label(for_=f"f-{base}-text", class_="sr-only")[t("creatures.block_text")],
        textarea(id=f"f-{base}-text", name=f"{kind}_text", rows="3", class_="cb-text")[block.text],
    ]


def _blocks(page: Page, view: BuilderView, kind: str) -> Element:
    t = page.t
    blocks: list[CreatureBlock] = getattr(view.design, kind)
    return section(class_="panel cb-section", aria_labelledby=f"cb-{kind}")[
        div(class_="cb-section-head")[
            h2(id=f"cb-{kind}")[t(f"creatures.kind.{kind}")],
            button(type="submit", name="action", value=f"add:{kind}", class_="btn btn-sm")[
                icon("plus", "sm"), span[t("creatures.add_block")]
            ],
        ],
        [_block(page, kind, i, block) for i, block in enumerate(blocks)]
        if blocks
        else p(class_="muted")[t("creatures.no_blocks")],
    ]


def _add(page: Page, view: BuilderView) -> Element:
    t = page.t
    d = view.design
    chosen = [
        li(class_="cb-feature")[
            span[f.name, f" ({f.value})" if f.value is not None else ""],
            button(
                type="submit",
                name="action",
                value=f"unfeature:{i}",
                class_="btn btn-icon btn-ghost",
                aria_label=t("creatures.remove", name=f.name),
            )[icon("x", "sm")],
        ]
        for i, f in enumerate(d.features)
    ]
    return section(class_="panel cb-section", aria_labelledby="cb-add")[
        h2(id="cb-add")[t("creatures.add")],
        div(class_="cb-add-group")[
            b[t("creatures.attack")],
            div(class_="cb-grid")[
                text_field(
                    "attack_name",
                    t("creatures.block_name"),
                    value="",
                    autocomplete="off",
                    required=False,
                ),
                _select(
                    "attack_kind",
                    t("creatures.attack_kind"),
                    [("melee", t("creatures.melee")), ("ranged", t("creatures.ranged"))],
                    "melee",
                ),
                _select(
                    "attack_ability",
                    t("creatures.attack_ability"),
                    [(ab, t(f"ability.{ab}")) for ab in ("str", "dex")],
                    "str",
                ),
                text_field(
                    "attack_dice",
                    t("creatures.attack_dice"),
                    value="1d8",
                    autocomplete="off",
                    required=False,
                ),
                _select(
                    "attack_type",
                    t("creatures.attack_type"),
                    [(x, t(f"creatures.damage.{x}")) for x in DAMAGE_TYPES],
                    "bludgeoning",
                ),
            ],
            button(type="submit", name="action", value="attack", class_="btn btn-sm")[
                icon("plus", "sm"), span[t("creatures.add_attack")]
            ],
        ],
        div(class_="cb-add-group")[
            b[t("creatures.ready_made")],
            p(class_="field-hint")[
                t("creatures.ready_made_hint"),
                [" ", a(href=f"{BUILDER_URL}?lang={page.lang}")[t("creatures.translate_texts")]]
                if page.lang != "en"
                else None,
            ],
            div(class_="cb-pick")[
                label(for_="f-template-trait", class_="sr-only")[t("creatures.kind.traits")],
                select(id="f-template-trait", name="template_trait")[
                    [option(value=x.name)[_ready_made_label(page, "trait", x)] for x in view.traits]
                ],
                button(type="submit", name="action", value="template:trait", class_="btn btn-sm")[
                    t("creatures.add_trait")
                ],
            ],
            div(class_="cb-pick")[
                label(for_="f-template-action", class_="sr-only")[t("creatures.kind.actions")],
                select(id="f-template-action", name="template_action")[
                    [
                        option(value=x.name)[_ready_made_label(page, "action", x)]
                        for x in view.actions
                    ]
                ],
                button(type="submit", name="action", value="template:action", class_="btn btn-sm")[
                    t("creatures.add_action")
                ],
            ],
        ]
        if view.traits or view.actions
        else None,
        div(class_="cb-add-group")[
            b[t("creatures.features")],
            p(class_="field-hint")[t("creatures.features_hint")],
            ul(class_="cb-features")[chosen] if chosen else None,
            div(class_="cb-pick")[
                label(for_="f-feature", class_="sr-only")[t("creatures.features")],
                select(id="f-feature", name="feature")[
                    [
                        option(value=f.name)[
                            f"{f.name} ({f.example})" + (" — #" if f.takes_number else "")
                        ]
                        for f in view.features
                    ]
                ],
                label(for_="f-feature-value", class_="sr-only")[t("creatures.feature_value")],
                input_el(
                    id="f-feature-value",
                    name="feature_value",
                    type="number",
                    inputmode="numeric",
                    min="0",
                    max="999",
                    placeholder="#",
                    class_="cb-feature-value",
                ),
                button(type="submit", name="action", value="feature", class_="btn btn-sm")[
                    t("creatures.add_feature")
                ],
            ],
        ]
        if view.features
        else None,
        _number(
            "dpr_override",
            t("creatures.dpr_override"),
            d.dpr_override,
            low=0,
            high=2000,
            hint=t("creatures.dpr_override_hint"),
        ),
    ]


def _estimate(page: Page, view: BuilderView) -> Node:
    t = page.t
    e = view.estimate
    if e is None:
        return None
    target = e.target
    matches = e.final == view.design.cr
    rows: list[Node] = [
        dt[t("creatures.est_defence")],
        dd[
            b[t("creatures.cr_short", cr=e.defensive)],
            " · ",
            t("creatures.est_hp", hp=e.effective_hp),
            " · ",
            t("creatures.est_ac", ac=e.effective_ac),
        ],
        dt[t("creatures.est_offence")],
        dd[
            b[t("creatures.cr_short", cr=e.offensive)],
            " · ",
            t("creatures.est_dpr", dpr=e.dpr),
            " · ",
            t("creatures.est_attack", attack=signed(e.attack))
            if e.attack is not None
            else (t("creatures.est_dc", dc=e.dc) if e.dc is not None else t("creatures.est_none")),
        ],
    ]
    if target is not None:
        rows += [
            dt[t("creatures.est_table", cr=target.cr)],
            dd[
                t(
                    "creatures.est_table_values",
                    ac=target.ac,
                    hp_min=target.hp_min,
                    hp_max=target.hp_max,
                    attack=signed(target.attack),
                    dpr_min=target.dpr_min,
                    dpr_max=target.dpr_max,
                    dc=target.save_dc,
                    pb=signed(target.pb),
                )
            ],
        ]
    return section(class_="panel cb-estimate", aria_labelledby="cb-est")[
        div(class_="panel-head")[
            h2(id="cb-est")[t("creatures.estimate")],
            span(class_=f"pill {'heal' if matches else 'cond'}")[
                t("creatures.plays_at", cr=e.final)
            ],
        ],
        dl(class_="cb-est-list")[rows],
        p(class_="field-hint")[
            t("creatures.estimate_matches") if matches else t("creatures.estimate_differs")
        ],
        [p(class_="field-hint")[t(f"creatures.note.{n}")] for n in e.notes],
    ]


def builder_page(
    page: Page,
    view: BuilderView,
    ctx: RenderContext,
    *,
    notice_text: str | None = None,
    error: str | None = None,
    picture_error: str = "",
) -> Element:
    """The builder for one design."""
    t = page.t
    d = view.design
    url = creature_url(d.id)
    status = (
        span(class_="pill accent", title=t("creatures.revision_hint"))[t("creatures.revision")]
        if d.revision_of
        else span(class_="pill accent", title=t("creatures.draft_hint"))[t("creatures.draft")]
        if d.drafted_by
        else span(class_="pill cond")[t("creatures.unpublished")]
        if view.unpublished
        else span(class_="pill heal")[t("creatures.published")]
    )
    actions = div(class_="form-actions cb-actions")[
        button(type="submit", name="action", value="accept", class_="btn btn-primary")[
            icon("check"),
            span[t("creatures.accept_revision" if d.revision_of else "creatures.accept")],
        ]
        if d.drafted_by
        else None,
        button(type="submit", name="action", value="save", class_="btn")[
            icon("check"), span[t("creatures.save")]
        ],
        None
        if d.revision_of  # its creature is published when the revision is accepted
        else button(type="submit", name="action", value="publish", class_="btn btn-primary")[
            icon("upload"), span[t("creatures.publish")]
        ],
        a(href=ref_url("monster", d.name, HOMEBREW_SOURCE, d.world_id), class_="btn btn-ghost")[
            t("creatures.open_ref")
        ]
        if view.published
        else None,
    ]
    return app_shell(
        page,
        t("creatures.builder_title", name=d.name),
        div(class_="cb-page creature-builder")[
            a(href=CREATURES_URL, class_="backlink")[
                icon("arrow-left"), span[t("creatures.heading")]
            ],
            div(class_="cb-title")[
                span(class_="eyebrow")[t("creatures.eyebrow")],
                h2(class_="cb-name")[d.name],
                status,
                span(class_="muted")[t("creatures.based_on", ref=d.based_on)]
                if d.based_on
                else None,
                delete_link(
                    page,
                    f"{url}/delete",
                    t("creatures.discard_revision" if d.revision_of else "creatures.delete"),
                    text=True,
                ),
            ],
            _revision_note(page, view),
            notice(notice_text),
            alert(error),
            div(class_="cb-layout")[
                form(method="post", action=url, class_="cb-form stack-form")[
                    # Enter in a field saves rather than pressing the first button on the page.
                    button(type="submit", name="action", value="save", hidden=True, tabindex="-1"),
                    _basics(page, view),
                    _abilities(page, view),
                    _defence(page, view),
                    [_blocks(page, view, kind) for kind in BLOCK_KINDS],
                    _add(page, view),
                    actions,
                    p(class_="field-hint")[t("creatures.publish_hint")],
                ],
                div(class_="cb-side")[
                    _estimate(page, view),
                    _picture(page, view, picture_error),
                    section(class_="cb-preview", aria_label=t("creatures.preview"))[
                        statblock(page, view.preview, ctx)
                    ],
                ],
            ],
        ],
        world_id=d.world_id,
        extra_scripts=("/static/js/roll.js",),
    )


def delete_page(page: Page, design: CreatureDesign) -> Element:
    """Ask before deleting."""
    t = page.t
    url = creature_url(design.id)
    return app_shell(
        page,
        t("creatures.delete"),
        div(class_="formpage")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[t("common.cancel")]],
            div(class_="panel panel-danger")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("creatures.eyebrow")],
                    b[t("creatures.delete_heading", name=design.name)],
                ],
                p[t("creatures.delete_lede")],
                form(method="post", action=f"{url}/delete")[
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-dmg")[
                            icon("x"), span[t("creatures.delete")]
                        ],
                        a(href=url, class_="btn btn-ghost")[t("common.cancel")],
                    ]
                ],
            ],
        ],
    )


def creature_not_found_page(page: Page) -> Element:
    """The design does not exist (any more)."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "skull",
            t("adventures.not_found"),
            t("creatures.not_found_lede"),
            a(href=CREATURES_URL, class_="btn btn-primary")[
                icon("arrow-left"), span[t("creatures.heading")]
            ],
        ),
    )


__all__ = [
    "CREATURES_URL",
    "creature_not_found_page",
    "BuilderView",
    "DesignRow",
    "builder_page",
    "creature_url",
    "creatures_page",
    "delete_page",
]
