"""The naming workshop at ``/gm/names`` (documentation/NAMES.md §5): an index of races,
styles found by race or search, each sampled, and the table's own written in a form or as
raw JSON. There can be hundreds of styles, so only what was asked for is drawn.

A form's part rows show the fields of their kind by CSS alone, and one blank row always
waits at the end, so a part is added by saving or trying and needs no script.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    div,
    fieldset,
    form,
    h2,
    label,
    legend,
    li,
    optgroup,
    option,
    p,
    section,
    select,
    small,
    span,
    textarea,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import World
from app.models.naming import SUBJECTS, NamingStyle
from app.services.naming import GENDERS, PERSON, STARTER_PREFIX, StyleEntry, sample_notes
from app.services.naming_workshop import (
    KINDS,
    PartFields,
    RaceGroup,
    StyleFields,
    SubjectGroup,
    Unserved,
)
from app.services.style_drafts import DraftRun
from app.ui.context import Page
from app.ui.forms import alert, hidden, origin_fields, text_field
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import confirm_page, crumbs, delete_link, world_url
from app.ui.names import name_text

NAMING_URL = "/gm/names"
DRAFT_ALL_URL = f"{NAMING_URL}/claude/all"
NEW_URL = f"{NAMING_URL}/new"
RAW_URL = f"{NAMING_URL}/raw"
PREVIEW_URL = f"{NAMING_URL}/preview"
SAMPLES_URL = f"{NAMING_URL}/samples"
COPY_URL = f"{NAMING_URL}/copy"
# The address of the styles of no race, which a blank would not tell from "every race".
NO_RACE = "-"
CULTURES_SHOWN = 6
MAX_ROWS = 100
RAW_EXAMPLE = """{
  "name": "",
  "summary": "",
  "subject": "person",
  "language": "",
  "race": "",
  "culture": "",
  "aliases": [],
  "sources": [],
  "pattern": "{given} {family}",
  "parts": {
    "given": {"kind": "list", "female": [], "male": []},
    "family": {"kind": "list", "names": []}
  }
}"""


def style_url(style_id: str) -> str:
    """One of the table's styles."""
    return f"{NAMING_URL}/{style_id}"


def entry_label(page: Page, entry: StyleEntry) -> str:
    """A style's name in the reader's language."""
    if entry.kind == "subject":
        return page.t("naming.subject_any")
    if entry.kind == "starter":
        return page.t(f"naming.style.{entry.label}")
    return entry.label or page.t("npcgen.any_style")


def _summary(page: Page, entry: StyleEntry) -> str:
    if entry.kind == "starter":
        return page.t(f"naming.summary.{entry.label}")
    return entry.style.summary if entry.style else ""


def style_options(
    page: Page, entries: Sequence[StyleEntry], selected: str, subject: str = PERSON
) -> list[Node]:
    """The groups of a style ``<select>``: one per race, "any culture" first, then the
    styles of no race; styles of people unless ``subject`` names a kind of place."""
    t = page.t
    entries = [e for e in entries if e.subject == subject]

    def row(entry: StyleEntry) -> Element:
        caption = t("naming.any_culture") if entry.kind == "race" else entry_label(page, entry)
        return option(value=entry.key, selected=entry.key == selected)[caption]

    groups: dict[str, list[StyleEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.race.casefold(), []).append(entry)

    def ordered(rows: list[StyleEntry]) -> list[StyleEntry]:
        return sorted(rows, key=lambda e: (e.kind != "race", entry_label(page, e).casefold()))

    races_first = sorted(groups, key=lambda k: (not k, k))
    return [
        optgroup(label=groups[k][0].race or t("naming.no_race"))[
            [row(e) for e in ordered(groups[k])]
        ]
        for k in races_first
    ]


@dataclass(frozen=True)
class WorldScope:
    """The world the workshop keeps to, if any: every world to choose from, and the
    races of the chosen one's people that none of its styles serves."""

    worlds: Sequence[tuple[str, str]] = ()
    world: World | None = None
    unserved: Sequence[Unserved] = ()
    # Claude's drafts for the world's peoples, by lower-case people.
    runs: Mapping[str, DraftRun] = field(default_factory=dict)
    can_draft: bool = False

    def href(self, **params: str) -> str:
        """The workshop's address with ``params``, in this world."""
        if self.world is not None:
            params = {**params, "world": self.world.id}
        return f"{NAMING_URL}?{urlencode(params)}" if params else NAMING_URL

    def name_of(self, world_id: str | None) -> str:
        """A world's name; empty when it is gone."""
        return next((name for wid, name in self.worlds if wid == world_id), "")


def _shell(page: Page, title: str, *body: Node) -> Element:
    return app_shell(
        page, title, div(class_="tr-page")[body], extra_scripts=("/static/js/htmx.min.js",)
    )


def _trail(page: Page, *rest: tuple[str, str | None]) -> Element:
    return crumbs(page, (page.t("naming.title"), NAMING_URL), *rest)


def samples_list(
    page: Page, names: Sequence[str], style_id: str | None = None, *, wanted: int = 0
) -> Element:
    """The samples, under what they say about the style when ``wanted`` were asked for;
    with ``style_id`` each can be rejected, and goes in place."""
    t = page.t
    if not names:
        missing = "naming.check.none" if wanted else "naming.no_samples"
        return p(id="naming-samples", class_="muted")[t(missing)]

    def row(name: str) -> Element:
        reject: Node = None
        if style_id:
            url = f"{style_url(style_id)}/reject"
            reject = form(
                method="post",
                action=url,
                hx_post=url,
                hx_target="closest li",
                hx_swap="delete",
            )[
                hidden("name", name),
                button(
                    type="submit",
                    class_="btn btn-sm btn-ghost",
                    aria_label=t("naming.reject_name", name=name),
                    title=t("naming.reject"),
                )[icon("x", "sm")],
            ]
        return li[span[name], reject]

    notes = [t(key, **params) for key, params in sample_notes(names, wanted)] if wanted else []
    return div(id="naming-samples")[
        [p(class_="muted")[icon("triangle-alert", "sm"), " ", note] for note in notes],
        ul(class_="naming-samples")[[row(n) for n in names]],
    ]


def _gender_form(page: Page, action: str, gender: str, *extra: Node) -> Element:
    t = page.t
    return form(method="get", action=action, class_="cg-suggest")[
        extra,
        label(class_="field", for_="f-gender")[
            span(class_="field-label")[t("npcgen.gender")],
            select(id="f-gender", name="gender")[
                [option(value=g, selected=g == gender)[t(f"npcgen.gender.{g}")] for g in GENDERS]
            ],
        ],
        button(type="submit", class_="btn btn-sm")[
            icon("dices", "sm"), span[t("naming.new_samples")]
        ],
    ]


def _toolbar(
    page: Page,
    query: str,
    race: str | None,
    subject: str | None = None,
    scope: WorldScope | None = None,
) -> Element:
    t = page.t
    new = NEW_URL
    if subject:
        new = f"{NEW_URL}?{urlencode({'subject': subject})}"
    elif race:
        new = f"{NEW_URL}?{urlencode({'race': race})}"
    return div(class_="naming-toolbar")[
        form(method="get", action=NAMING_URL, class_="naming-search", role="search")[
            hidden("race", race) if race else None,
            hidden("subject", subject) if subject else None,
            input_el(
                type="search",
                class_="cg-filter",
                name="q",
                value=query,
                placeholder=t("naming.search"),
                aria_label=t("naming.search"),
                autocomplete="off",
            ),
            _world_menu(page, scope) if scope and scope.worlds else None,
            button(type="submit", class_="btn")[t("naming.search_button")],
        ],
        a(href=new, class_="btn btn-primary")[icon("plus"), span[t("naming.new")]],
        a(href=RAW_URL, class_="btn")[t("naming.raw")],
    ]


def _world_menu(page: Page, scope: WorldScope) -> Element:
    chosen = scope.world.id if scope.world else ""
    return select(name="world", aria_label=page.t("naming.world.filter"))[
        option(value="", selected=not chosen)[page.t("naming.world.all")],
        [option(value=wid, selected=wid == chosen)[name] for wid, name in scope.worlds],
    ]


def _race_href(race: str, scope: WorldScope | None = None) -> str:
    return (scope or WorldScope()).href(race=race or NO_RACE)


def draftable(scope: WorldScope) -> list[Unserved]:
    """The peoples "draft all" asks Claude for: none drafted or drafting yet, and none a
    style of the table's would serve given another name (that offer is quicker)."""
    out = []
    for people in scope.unserved:
        run = scope.runs.get(people.race.casefold())
        busy = run is not None and (run.running or (run.style_id and not run.error))
        if people.alias_style is None and not busy:
            out.append(people)
    return out


def _people_row(page: Page, scope: WorldScope, people: Unserved) -> Element:
    """A people no style serves, with what can be done about it."""
    t = page.t
    world_id = scope.world.id if scope.world else ""
    new = {
        "race": people.race,
        **({"aliases": ", ".join(people.aliases)} if people.aliases else {}),
    }
    alias: Node = None
    if people.alias_style is not None:
        style_id, style_name = people.alias_style
        alias = form(method="post", action=f"{style_url(style_id)}/alias")[
            hidden("alias", people.alias),
            hidden("world", world_id),
            button(type="submit", class_="btn btn-sm btn-primary")[
                icon("check", "sm"),
                span[t("naming.world.alias_add", alias=people.alias, style=style_name)],
            ],
        ]
    run = scope.runs.get(people.race.casefold())
    claude: Node = None
    if run is not None and run.running:
        claude = span(class_="muted", role="status")[
            t("naming.world.drafting" if run.started else "naming.world.queued")
        ]
    elif run is not None and run.style_id and not run.error:
        claude = a(href=style_url(run.style_id), class_="btn btn-sm")[t("naming.world.drafted")]
    elif scope.can_draft:
        claude = form(method="post", action=f"{NAMING_URL}/claude")[
            hidden("world", world_id),
            hidden("race", people.race),
            [hidden("aliases", other) for other in people.aliases],
            small(class_="muted")[t("naming.world.draft_failed", error=run.error), " "]
            if run is not None and run.error
            else None,
            button(type="submit", class_="btn btn-sm")[
                icon("sparkles", "sm"),
                span[t("naming.world.draft_again" if run and run.error else "naming.world.draft")],
            ],
        ]
    return li(class_="naming-row naming-people")[
        div[
            b[people.race],
            " ",
            small(class_="muted")[t("naming.world.also", names=", ".join(people.aliases))]
            if people.aliases
            else None,
        ],
        div(class_="naming-people-actions")[
            alias,
            a(href=f"{NEW_URL}?{urlencode(new)}", class_="btn btn-sm")[
                icon("plus", "sm"), span[t("naming.new")]
            ],
            claude,
        ],
    ]


def _draft_all(page: Page, scope: WorldScope) -> Node:
    """One button for every people still without a draft, once there are two or more."""
    waiting = draftable(scope)
    if not scope.can_draft or scope.world is None or len(waiting) < 2:
        return None
    t = page.t
    return form(method="post", action=DRAFT_ALL_URL, class_="naming-row naming-draft-all")[
        hidden("world", scope.world.id),
        small(class_="muted")[t("naming.world.draft_all_hint")],
        button(type="submit", class_="btn btn-sm btn-primary")[
            icon("sparkles", "sm"), span[t("naming.world.draft_all", n=len(waiting))]
        ],
    ]


def _world_panel(page: Page, scope: WorldScope) -> Node:
    """What the chosen world uses, and the peoples none of its styles names; it reloads
    itself while Claude drafts for it."""
    world = scope.world
    if world is None:
        return None
    t = page.t
    state = (
        t.plural("naming.world.chosen", len(world.naming_styles))
        if world.naming_styles
        else t("naming.world.every")
    )
    drafting = any(run.running for run in scope.runs.values())
    return section(
        id="naming-world",
        class_="panel naming-world",
        hx_get=scope.href() if drafting else None,
        hx_trigger="every 4s" if drafting else None,
        hx_select="#naming-world" if drafting else None,
        hx_swap="outerHTML" if drafting else None,
    )[
        div(class_="naming-row")[
            div[span(class_="eyebrow")[icon("dices", "sm"), world.name], " ", b[state]],
            a(href=f"{world_url(world.id)}/names", class_="btn btn-sm")[t("naming.world.choose")],
        ],
        div[
            p(class_="muted")[t("naming.world.unserved", world=world.name)],
            _draft_all(page, scope),
            ul(class_="naming-peoples")[[_people_row(page, scope, u) for u in scope.unserved]],
        ]
        if scope.unserved
        else p(class_="muted")[t("naming.world.all_served")],
    ]


def _style_row(page: Page, entry: StyleEntry, examples: Sequence[str]) -> Element:
    t = page.t
    href = f"{SAMPLES_URL}?{urlencode({'key': entry.key})}"
    if entry.kind == "own" and entry.style is not None:
        href = style_url(entry.style.id)
    copy: Node = None
    if entry.kind == "starter":
        copy = form(method="post", action=COPY_URL)[
            hidden("key", entry.key),
            button(type="submit", class_="btn btn-sm")[icon("plus", "sm"), span[t("naming.copy")]],
        ]
    text = _summary(page, entry)
    return li(class_="panel naming-row")[
        div[
            a(href=href)[b[entry_label(page, entry)]],
            " ",
            small(class_="muted")[
                entry.culture, " · " if entry.culture else "", _origin(page, entry)
            ],
            " ",
            span(class_="pill naming-lang", title=t("naming.language"))[entry.language.upper()]
            if entry.language
            else None,
            p(class_="muted")[text] if text else None,
            small(class_="muted")[" · ".join(examples)],
        ],
        copy,
    ]


def _origin(page: Page, entry: StyleEntry) -> str:
    own = entry.kind in ("own", "starter")
    return page.t(f"naming.origin.{entry.kind}" if own else "naming.origin.books")


def overview_page(
    page: Page,
    groups: Sequence[RaceGroup],
    drafts: Sequence[NamingStyle],
    examples: Mapping[str, Sequence[str]],
    places: Sequence[SubjectGroup] = (),
    scope: WorldScope | None = None,
) -> Element:
    """The index: what waits to be accepted, every race with its cultures, then the
    kinds of place; in a world, only its styles."""
    t = page.t
    scope = scope or WorldScope()

    def place(group: SubjectGroup) -> Element:
        shown = ", ".join(group.cultures[:CULTURES_SHOWN])
        return li[
            a(
                href=scope.href(subject=group.subject),
                class_="panel naming-race",
            )[
                b[t(f"naming.subject.{group.subject}")],
                small(class_="muted")[t.plural("naming.style_count", group.styles)],
                small(class_="muted")[shown],
            ]
        ]

    def card(group: RaceGroup) -> Element:
        shown = ", ".join(group.cultures[:CULTURES_SHOWN])
        more = len(group.cultures) - CULTURES_SHOWN
        return li[
            a(href=_race_href(group.race, scope), class_="panel naming-race")[
                # A people by the glossary's name where it has one: "Zwerg" for Dwarf.
                b[
                    name_text(page, ("race", "monster"), group.race)
                    if group.race
                    else t("naming.no_race")
                ],
                small(class_="muted")[t.plural("naming.style_count", group.styles)],
                small(class_="muted")[shown, f" +{more}" if more > 0 else ""],
            ]
        ]

    proposed = [
        li(class_="panel naming-row")[
            div[
                a(href=style_url(s.id))[b[s.name]],
                " ",
                small(class_="muted")[
                    " · ".join(
                        x
                        for x in (
                            t(f"naming.subject.{s.subject}") if s.subject != PERSON else "",
                            s.race,
                            s.culture,
                            s.language.upper(),
                            t("naming.world.for", world=scope.name_of(s.for_world))
                            if scope.name_of(s.for_world)
                            else "",
                        )
                        if x
                    )
                ],
                p(class_="muted")[s.summary] if s.summary else None,
                small(class_="muted")[" · ".join(examples.get(s.id, ()))],
            ]
        ]
        for s in drafts
    ]
    return _shell(
        page,
        t("naming.title"),
        page_head(t("settings.menu"), t("naming.title"), t("naming.lede")),
        _toolbar(page, "", None, scope=scope),
        _world_panel(page, scope),
        section[
            div(class_="naming-row")[
                h2(class_="section-title")[t("naming.drafts")],
                form(method="post", action=f"{NAMING_URL}/accept-all")[
                    button(type="submit", class_="btn btn-sm")[
                        icon("check", "sm"), span[t("naming.accept_all")]
                    ]
                ]
                if len(proposed) > 1
                else None,
            ],
            p(class_="muted")[t("naming.drafts_hint")],
            ul(class_="naming-list")[proposed],
        ]
        if proposed
        else None,
        section[
            h2(class_="section-title")[t("naming.races")],
            ul(class_="naming-races")[[card(g) for g in groups]],
        ],
        section[
            h2(class_="section-title")[t("naming.places")],
            ul(class_="naming-races")[[place(g) for g in places]],
        ]
        if places
        else None,
    )


def results_page(
    page: Page,
    entries: Sequence[StyleEntry],
    examples: Mapping[str, Sequence[str]],
    *,
    query: str,
    race: str | None,
    subject: str | None = None,
    scope: WorldScope | None = None,
) -> Element:
    """The styles of a race or kind of place, or what a search found; at most
    ``MAX_ROWS`` are drawn."""
    t = page.t
    scope = scope or WorldScope()
    title = t("naming.results", query=query) if query else race or t("naming.no_race")
    if race is None and not query:
        title = t(f"naming.subject.{subject}") if subject else t("naming.title")
    shown = entries[:MAX_ROWS]
    return _shell(
        page,
        title,
        crumbs(page, (t("naming.title"), scope.href()), (title, None)),
        page_head(
            scope.world.name if scope.world else t("naming.title"),
            title,
            t.plural("naming.style_count", len(entries)),
        ),
        _toolbar(
            page,
            query,
            race if race != "" else NO_RACE if race is not None else None,
            subject,
            scope,
        ),
        ul(class_="naming-list")[[_style_row(page, e, examples.get(e.key, ())) for e in shown]]
        if shown
        else p(class_="muted")[t("naming.nothing_found")],
        p(class_="muted")[t("naming.more_rows", shown=len(shown), total=len(entries))]
        if len(entries) > len(shown)
        else None,
    )


def samples_page(page: Page, entry: StyleEntry, names: Sequence[str], gender: str) -> Element:
    """A style that cannot be edited here, with its samples; a starter can be copied."""
    t = page.t
    title = entry_label(page, entry)
    copy: Node = None
    if entry.key.startswith(STARTER_PREFIX):
        copy = form(method="post", action=COPY_URL)[
            hidden("key", entry.key),
            button(type="submit", class_="btn btn-primary")[icon("plus"), span[t("naming.copy")]],
        ]
    return _shell(
        page,
        title,
        _trail(page, (entry.race or t("naming.no_race"), _race_href(entry.race)), (title, None)),
        page_head(t("naming.title"), title, _summary(page, entry), copy),
        section(class_="panel")[
            _gender_form(page, SAMPLES_URL, gender, hidden("key", entry.key)),
            samples_list(page, names, wanted=30),
        ],
    )


def _sources(page: Page, style: NamingStyle) -> Node:
    """Where a style's research came from; only web addresses become links."""
    if not style.sources:
        return None

    def row(source: str) -> Node:
        if source.startswith(("https://", "http://")):
            return li[a(href=source, rel="noreferrer", target="_blank")[source]]
        return li[source]

    return section(class_="panel")[
        h2[page.t("naming.sources")], ul[[row(source) for source in style.sources]]
    ]


def rejected_section(page: Page, style: NamingStyle) -> Node:
    """The names turned down, each to take back."""
    t = page.t
    if not style.rejected:
        return None
    return section(class_="panel")[
        h2[t("naming.rejected")],
        ul(class_="naming-samples")[
            [
                li[
                    span[name],
                    form(method="post", action=f"{style_url(style.id)}/restore")[
                        hidden("name", name),
                        button(type="submit", class_="btn btn-sm btn-ghost")[t("naming.restore")],
                    ],
                ]
                for name in style.rejected
            ]
        ],
    ]


def style_page(page: Page, style: NamingStyle, names: Sequence[str], gender: str) -> Element:
    """One of the table's styles: thirty samples to judge, and the ways to change it."""
    t = page.t
    url = style_url(style.id)
    origin = " · ".join(x for x in (style.race, *style.aliases, style.culture) if x)
    return _shell(
        page,
        style.name,
        _trail(
            page, (style.race or t("naming.no_race"), _race_href(style.race)), (style.name, None)
        ),
        page_head(
            origin or t("naming.title"),
            style.name,
            style.summary,
            div(class_="form-actions")[
                a(href=f"{url}/edit", class_="btn btn-primary")[
                    icon("pencil"), span[t("players.edit")]
                ],
                a(href=f"{url}/raw", class_="btn")[t("naming.raw")],
                delete_link(page, f"{url}/delete", t("naming.delete"), text=True),
            ],
        ),
        form(method="post", action=f"{url}/accept", class_="panel")[
            p[t("naming.draft_note")],
            button(type="submit", class_="btn btn-primary")[
                icon("check"), span[t("naming.accept")]
            ],
        ]
        if style.draft
        else None,
        section(class_="panel")[
            p(class_="muted")[t("naming.reject_hint")],
            _gender_form(page, url, gender),
            samples_list(page, names, style.id, wanted=30),
        ],
        rejected_section(page, style),
        _sources(page, style),
    )


def _area(row: PartFields, index: int, attr: str, caption: str, kind: str) -> Element:
    return div(class_=f"field for-{kind}")[
        label(for_=f"p{index}-{attr}")[caption],
        textarea(id=f"p{index}-{attr}", name=f"part-{index}-{attr}", rows="4")[getattr(row, attr)],
    ]


def _line(row: PartFields, index: int, attr: str, caption: str, kind: str) -> Element:
    return div(class_=f"field for-{kind}")[
        label(for_=f"p{index}-{attr}")[caption],
        input_el(
            id=f"p{index}-{attr}",
            name=f"part-{index}-{attr}",
            type="text",
            value=getattr(row, attr),
            autocomplete="off",
        ),
    ]


def _part_row(page: Page, row: PartFields, index: int) -> Element:
    t = page.t
    return div(class_="naming-part")[
        _line(row, index, "key", t("naming.part_key"), "any"),
        div(class_="field")[
            label(for_=f"p{index}-kind")[t("naming.part_kind")],
            select(id=f"p{index}-kind", name=f"part-{index}-kind")[
                [option(value=k, selected=k == row.kind)[t(f"naming.kind.{k}")] for k in KINDS]
            ],
        ],
        _area(row, index, "names", t("naming.names"), "list for-learned"),
        _area(row, index, "female", t("naming.female"), "list for-learned"),
        _area(row, index, "male", t("naming.male"), "list for-learned"),
        _line(row, index, "order", t("naming.order"), "learned"),
        p(class_="field-hint for-learned")[t("naming.learned_hint")],
        _line(row, index, "kin_part", t("naming.kin_part"), "kin"),
        _line(row, index, "fallback", t("naming.fallback"), "kin"),
        p(class_="field-hint for-kin")[t("naming.kin_hint")],
        _area(row, index, "first", t("naming.first"), "elements"),
        _area(row, index, "second", t("naming.second"), "elements"),
        _area(row, index, "second_female", t("naming.second_female"), "elements"),
        _area(row, index, "second_male", t("naming.second_male"), "elements"),
        _line(row, index, "fixed_female", t("naming.female"), "by_gender"),
        _line(row, index, "fixed_male", t("naming.male"), "by_gender"),
        _area(row, index, "groups", t("naming.groups"), "sounds"),
        _area(row, index, "syllables", t("naming.syllables"), "sounds"),
        _line(row, index, "min_syllables", t("naming.min_syllables"), "sounds"),
        _line(row, index, "max_syllables", t("naming.max_syllables"), "sounds"),
        _area(row, index, "ending_female", t("naming.ending_female"), "sounds"),
        _area(row, index, "ending_male", t("naming.ending_male"), "sounds"),
        p(class_="field-hint for-sounds")[t("naming.sounds_hint")],
    ]


def _try_button(page: Page, url: str = PREVIEW_URL) -> Element:
    return button(
        type="button",
        class_="btn",
        hx_post=url,
        hx_include="closest form",
        hx_target="#naming-samples",
        hx_swap="outerHTML",
    )[icon("dices"), span[page.t("naming.try")]]


def style_form_page(
    page: Page,
    fields: StyleFields,
    *,
    action: str,
    back: str,
    title: str,
    races: Sequence[str] = (),
    cultures: Sequence[str] = (),
    error: str | None = None,
) -> Element:
    """A style in the form, with a blank part row at the end."""
    t = page.t
    rows = [*[row for row in fields.parts if not row.blank], PartFields()]
    return _shell(
        page,
        title,
        _trail(page, (title, None)),
        page_head(t("naming.title"), title, t("naming.form_lede")),
        alert(error),
        section(class_="panel")[
            form(method="post", action=action, class_="stack-form")[
                text_field("name", t("naming.name"), value=fields.name, autocomplete="off"),
                div(class_="naming-origin")[
                    div(class_="field")[
                        label(for_="f-subject")[t("naming.subject")],
                        select(id="f-subject", name="subject")[
                            [
                                option(value=s, selected=s == fields.subject)[
                                    t(f"naming.subject.{s}")
                                ]
                                for s in SUBJECTS
                            ]
                        ],
                    ],
                    div(class_="field")[
                        label(for_="f-language")[t("naming.language")],
                        select(
                            id="f-language", name="language", aria_describedby="f-language-hint"
                        )[
                            option(value="", selected=not fields.language)[
                                t("naming.language_any")
                            ],
                            [
                                option(value=lang.code, selected=lang.code == fields.language)[
                                    lang.name
                                ]
                                for lang in t.languages
                            ],
                        ],
                    ],
                ],
                p(class_="field-hint", id="f-language-hint")[t("naming.language_hint")],
                origin_fields(page, fields.race, fields.culture, races, cultures),
                text_field(
                    "aliases",
                    t("naming.aliases"),
                    value=fields.aliases,
                    autocomplete="off",
                    required=False,
                    hint=t("naming.aliases_hint"),
                ),
                text_field(
                    "summary",
                    t("naming.summary"),
                    value=fields.summary,
                    autocomplete="off",
                    required=False,
                ),
                text_field(
                    "pattern",
                    t("naming.pattern"),
                    value=fields.pattern,
                    autocomplete="off",
                    hint=t("naming.pattern_hint"),
                ),
                fieldset(class_="stack-form naming-parts")[
                    legend[t("naming.parts")],
                    p(class_="field-hint")[t("naming.parts_hint")],
                    [_part_row(page, row, i) for i, row in enumerate(rows)],
                ],
                div(class_="field")[
                    label(for_="f-rewrites")[t("naming.rewrites")],
                    p(class_="field-hint")[t("naming.rewrites_hint")],
                    textarea(id="f-rewrites", name="rewrites", rows="3")[fields.rewrites],
                ],
                div(class_="field")[
                    label(for_="f-banned")[t("naming.banned")],
                    p(class_="field-hint")[t("naming.banned_hint")],
                    textarea(id="f-banned", name="banned", rows="2")[fields.banned],
                ],
                text_field(
                    "max_cluster",
                    t("naming.max_cluster"),
                    value=fields.max_cluster,
                    autocomplete="off",
                    required=False,
                    hint=t("naming.max_cluster_hint"),
                ),
                div(class_="field")[
                    label(for_="f-sources")[t("naming.sources")],
                    p(class_="field-hint")[t("naming.sources_hint")],
                    textarea(id="f-sources", name="sources", rows="2")[fields.sources],
                ],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("check"), span[t("worlds.save")]
                    ],
                    _try_button(page),
                    a(href=back, class_="btn btn-ghost")[t("common.cancel")],
                ],
                samples_list(page, ()),
            ]
        ],
    )


def raw_page(
    page: Page,
    text: str,
    *,
    action: str,
    back: str,
    title: str,
    error: str | None = None,
) -> Element:
    """A style as JSON, for what the form does not reach."""
    t = page.t
    return _shell(
        page,
        title,
        _trail(page, (title, None)),
        page_head(t("naming.title"), title, t("naming.raw_lede")),
        alert(error),
        section(class_="panel")[
            form(method="post", action=action, class_="stack-form")[
                textarea(
                    name="raw",
                    class_="naming-raw",
                    rows="24",
                    spellcheck="false",
                    aria_label=t("naming.raw"),
                )[text],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("check"), span[t("worlds.save")]
                    ],
                    _try_button(page, f"{PREVIEW_URL}/raw"),
                    a(href=back, class_="btn btn-ghost")[t("common.cancel")],
                ],
                samples_list(page, ()),
            ]
        ],
    )


def delete_page(page: Page, style: NamingStyle) -> Element:
    """Ask before a style goes."""
    t = page.t
    url = style_url(style.id)
    return confirm_page(
        page,
        title=t("naming.delete_title", name=style.name),
        heading=t("naming.delete_title", name=style.name),
        lede=t("naming.delete_lede"),
        action=f"{url}/delete",
        back=url,
        submit=t("naming.delete"),
    )


__all__ = [
    "COPY_URL",
    "DRAFT_ALL_URL",
    "MAX_ROWS",
    "NAMING_URL",
    "NEW_URL",
    "NO_RACE",
    "PREVIEW_URL",
    "RAW_EXAMPLE",
    "RAW_URL",
    "SAMPLES_URL",
    "delete_page",
    "draftable",
    "entry_label",
    "overview_page",
    "raw_page",
    "results_page",
    "samples_list",
    "samples_page",
    "style_form_page",
    "style_options",
    "style_page",
    "style_url",
    "WorldScope",
]
