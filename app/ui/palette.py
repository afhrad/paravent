"""The command palette (⌘K): its form, results and trigger (documentation/DESIGN.md §4).

One form serves both. In the palette, ``palette.js`` fetches the results fragment as you
type and moves the selection with the arrow keys; without JavaScript the same form
submits to ``/search`` and the full page renders the same fragment. Results are plain
links to the reference page, carrying the world, so what opens resolves in the same books
the search was scoped to.

The same form is the **monster picker** in the encounter builder (a :class:`SearchTarget`
with a ``pick_action``): there each result is an "add" button posting to the encounter,
and the form submits to the builder page itself.

And it is the **insert pop-up** of the Markdown editors (``insert=True``): opened by ⌘K
from an editor or its "Insert reference" button, each result inserts its tag at the
cursor — ``{@creature Goblin}``, ``{@npc Maren Holt}`` — instead of opening.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from htpy import Element, Node, a, b, button, dialog, div, form, label, li, p, span, ul
from htpy import input as input_el

from app.services.search import GROUP_KEYS, Row, SearchHit, SearchResult
from app.store.projections import (
    ITEM_KIND_NAMES,
    ITEM_RARITIES,
    ItemProjection,
    MonsterProjection,
    SpellProjection,
)
from app.ui import rules_text
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.tags import ref_url

# The category's icon and its tint in the result list (documentation/DESIGN.md §5).
CATEGORY_ICON: dict[str, tuple[str, str]] = {
    "spell": ("sparkles", "magic"),
    "book": ("book-open", ""),
    "map": ("map", ""),
    "monster": ("skull", "dmg"),
    "item": ("gem", "item"),
    "baseitem": ("gem", "item"),
    "itemGroup": ("gem", "item"),
    "class": ("swords", ""),
    "race": ("users-round", ""),
    "subrace": ("users-round", ""),
    "background": ("scroll", ""),
    "feat": ("zap", ""),
    "optionalfeature": ("zap", ""),
    "condition": ("footprints", "cond"),
    "status": ("hourglass", "cond"),
    "disease": ("heart-crack", "cond"),
    "action": ("play", ""),
    "variantrule": ("book-open", ""),
    "skill": ("target", ""),
    "sense": ("eye", ""),
    "deity": ("flame", ""),
    "language": ("languages", ""),
    "trap": ("triangle-alert", ""),
    "hazard": ("triangle-alert", ""),
    "reward": ("gem", ""),
    "object": ("vault", ""),
    "table": ("dices", ""),
    "vehicle": ("map", ""),
    "vehicleUpgrade": ("map", ""),
    "deck": ("dices", ""),
    "psionic": ("wand-sparkles", ""),
    "charoption": ("circle-user", ""),
    "facility": ("vault", ""),
    "cult": ("users", ""),
    "boon": ("flame", ""),
    "recipe": ("book-open", ""),
    "crochetPattern": ("scroll", ""),
    "itemMastery": ("swords", ""),
    "itemProperty": ("swords", ""),
    "itemType": ("gem", ""),
    # The campaign's own records, in the insert pop-up.
    "npc": ("users-round", ""),
    "place": ("map", ""),
    "sound": ("volume-2", ""),
    "encounter": ("swords", "dmg"),
    "treasure": ("gem", "item"),
    "character": ("shield", ""),
    "chapter": ("book-open", ""),
}
INSERT_RESULTS_ID = "insert-results"
# The Markdown editors' toolbar buttons, whose labels the insert pop-up carries.
EDITOR_TOOLS = (
    "toolbar",
    "heading",
    "bold",
    "italic",
    "list",
    "numbered",
    "link",
    "read",
    "secret",
    "fold",
    "image",
    "table",
    "wide",
    "narrow",
)
# The insert pop-up's extra group: the world's NPCs, encounters, treasure and chapters.
CAMPAIGN = "campaign"

RESULTS_ID = "palette-results"


@dataclass(frozen=True)
class SearchTarget:
    """Where a search form lives and what its results do.

    ``action`` is the page the form submits to (and its links stay on); ``dom_id`` keeps
    two forms on one page apart. With ``pick_action`` set, results are buttons posting
    ``ref=Name|SOURCE`` there; ``pick`` is carried to the fragment endpoint so it can
    render those buttons too.
    """

    action: str = "/search"
    dom_id: str = RESULTS_ID
    pick_action: str | None = None
    pick: str | None = None
    # What the empty picker says it searches; the monster picker's words by default.
    pick_hint: str = "search.hint_pick"
    # The insert pop-up: results insert their tag (``tag(row)``) instead of opening.
    insert: bool = False
    tag: Callable[[Row], str | None] | None = None
    # Parts of the page an "add" replaces in place (``#plan #rating``): the picker then
    # posts in the background and takes them from the answer, without a reload.
    swap: str = ""


PALETTE = SearchTarget()


def search_url(
    query: str,
    *,
    group: str | None = None,
    world_id: str | None = None,
    all_books: bool = False,
    base: str = "/search",
) -> str:
    """The search page for these parameters — what a chip or the "all books" link opens."""
    params = {"q": query, "group": group or "", "world": world_id or ""}
    if all_books:
        params["all"] = "1"
    query_string = urlencode({k: v for k, v in params.items() if v})
    return f"{base}{'&' if '?' in base else '?'}{query_string}"


def _monster_line(page: Page, m: MonsterProjection) -> tuple[str, str | None]:
    t = page.t
    parts = [rules_text.kind_text(page, m.sizes, m.kind)]
    if m.ac is not None:
        parts.append(f"{t('rules.short.ac')} {m.ac}")
    if m.hp is not None:
        parts.append(f"{t('rules.short.hp')} {m.hp}")
    return " · ".join(parts), (f"{t('rules.short.cr')} {m.cr.text}" if m.cr else None)


def _spell_line(page: Page, s: SpellProjection) -> tuple[str, str]:
    t = page.t
    tags = [t("sheet.conc") if s.concentration else "", t("sheet.ritual") if s.ritual else ""]
    line = " · ".join(x for x in (rules_text.school(page, s.school), *tags) if x)
    level = t("sheet.cantrip") if s.level == 0 else t("search.level", level=s.level)
    return line, level


def _item_line(i: ItemProjection) -> tuple[str, str | None]:
    kind = ITEM_KIND_NAMES.get(i.kind, "Wondrous item" if i.wondrous else i.kind or "Item")
    rarity = i.rarity if i.magic else None
    attune = " · requires attunement" if i.attunement else ""
    # "unknown (magic)" and "varies" belong in the line, not in a rarity pill.
    pill = rarity if rarity in ITEM_RARITIES else None
    return f"{kind}{', ' + rarity if rarity else ''}{attune}", pill


def _describe(page: Page, hit: SearchHit) -> tuple[str, Node]:
    """The grey line under a result and the pill beside it."""
    row, t = hit.row, page.t
    projection: Any = row.projection
    if isinstance(projection, MonsterProjection):
        line, cr = _monster_line(page, projection)
        return line, span(class_="pill dmg")[cr] if cr else None
    if isinstance(projection, SpellProjection):
        line, level = _spell_line(page, projection)
        return line, span(class_="pill magic")[level]
    if isinstance(projection, ItemProjection):
        line, rarity = _item_line(projection)
        return line, span(class_="pill accent")[rarity] if rarity else None
    if row.category == "book" or row.context:
        return row.context or t(f"search.kind.{row.category}"), None
    return t(f"search.kind.{row.category}"), None


def _hit(
    page: Page, hit: SearchHit, index: int, world_id: str | None, target: SearchTarget
) -> Element:
    row = hit.row
    glyph, tone = CATEGORY_ICON.get(row.category, ("book-open", ""))
    line, pill = _describe(page, hit)
    inner = [
        span(class_=f"res-ico {tone}".strip())[icon(glyph)],
        div(class_="res-text")[
            b[hit.label or row.name],
            span(class_="res-en muted")[row.name] if hit.label else None,
            div(class_="rsub")[line],
        ],
        div(class_="rmeta")[
            pill,
            span(class_="res-src mono")[row.source],
            span(class_="res-add", aria_hidden="true")[icon("plus")]
            if target.pick_action
            else None,
        ],
    ]
    common = {
        "id": f"{target.dom_id}-{index}",
        "role": "option",
        "aria_selected": "true" if index == 0 else "false",
    }
    if target.insert:
        tag = target.tag(row) if target.tag is not None else None
        href = row.url or ref_url(row.category, row.name, row.source, world_id)
        if tag:
            return button(
                type="button",
                class_="res res-insert",
                data_insert=tag,
                data_href=href,
                # Where words selected in the editor go, so they stay as the link's label:
                # slot 2 of a library tag, 3 of an entity tag, 4 of a deity's.
                data_label_slot=str(_label_slot(row.category)),
                title=tag,
                **common,
            )[inner]
        return a(class_="res", href=href, target="_blank", rel="noopener", **common)[inner]
    if target.pick_action:
        return button(
            type="submit",
            class_="res res-pick",
            formaction=target.pick_action,
            formmethod="post",
            name="ref",
            value=f"{row.name}|{row.source}",
            aria_label=page.t("search.add", name=row.name, source=row.source),
            **common,
        )[inner]
    href = row.url or ref_url(row.category, row.name, row.source, world_id)
    if row.url and world_id:
        path, _hash, anchor = row.url.partition("#")
        href = f"{path}?{urlencode({'world': world_id})}" + (f"#{anchor}" if anchor else "")
    return a(class_="res", href=href, **common)[inner]


def _label_slot(category: str) -> int:
    """The tag slot (from 0) that holds the label a reader sees."""
    if category in ("npc", "encounter", "treasure", "character", "chapter", "place", "sound"):
        return 1
    return 3 if category == "deity" else 2


def results(
    page: Page,
    result: SearchResult,
    world_id: str | None,
    target: SearchTarget = PALETTE,
) -> Element:
    """The result list: grouped, best group first, plus the way out of the world's books."""
    t = page.t
    body: list[Node] = []
    if not result.query:
        hint = "search.hint_insert" if target.insert else "search.hint"
        body.append(p(class_="res-empty")[t(target.pick_hint if target.pick_action else hint)])
    elif not result.groups:
        body.append(p(class_="res-empty")[t("search.none", query=result.query)])
    index = 0
    for group in result.groups:
        body.append(
            div(class_="res-group eyebrow", role="presentation")[
                t(f"search.group.{group.key}"),
                span(class_="res-count")[str(group.total)],
            ]
        )
        for hit in group.hits:
            body.append(_hit(page, hit, index, world_id, target))
            index += 1
        if group.total > len(group.hits):
            body.append(
                a(
                    class_="res-more",
                    href=search_url(
                        result.query,
                        group=group.key,
                        world_id=world_id,
                        all_books=result.all_books,
                        base=target.action,
                    ),
                    data_group=group.key,
                )[t("search.show_all", count=group.total)]
            )
    if result.query and result.outside:
        body.append(
            p(class_="res-outside")[
                t("search.outside", count=result.outside),
                " ",
                a(
                    href=search_url(
                        result.query,
                        group=result.group,
                        world_id=world_id,
                        all_books=True,
                        base=target.action,
                    ),
                    data_all="1",
                )[t("search.all_books")],
            ]
        )
    elif result.query and result.all_books and result.restricted:
        body.append(
            p(class_="res-outside")[
                t("search.everything"),
                " ",
                a(
                    href=search_url(
                        result.query, group=result.group, world_id=world_id, base=target.action
                    ),
                    data_all="0",
                )[t("search.world_books")],
            ]
        )
    return div(
        id=target.dom_id,
        class_="results",
        role="listbox",
        aria_label=t("search.results"),
        data_count=str(index),
    )[body]


def _chips(page: Page, current: str | None, *, only: tuple[str, ...] = ()) -> Element:
    t = page.t
    keys = only or GROUP_KEYS
    choices = [("", t("search.group.all")), *((k, t(f"search.group.{k}")) for k in keys)]
    return div(class_="filterbar", role="radiogroup", aria_label=t("search.filter"))[
        [
            label(class_="chip")[
                input_el(
                    type="radio",
                    name="group",
                    value=value,
                    checked=(value or None) == current,
                    class_="sr-only",
                ),
                span[text],
            ]
            for value, text in choices
        ]
    ]


def search_form(
    page: Page,
    result: SearchResult,
    world_id: str | None,
    *,
    in_dialog: bool,
    target: SearchTarget = PALETTE,
    autofocus: bool | None = None,
    extra: Node = None,
) -> Element:
    """The search field, the category chips, the results and the key hints.

    ``extra`` goes in the form under the field: a picker's result buttons submit the
    whole form, so a choice placed there (friend or foe) travels with each "add".
    """
    t = page.t
    picking = target.pick_action is not None
    return form(
        class_="palette-form dialog" + (" picker" if picking else ""),
        role="search",
        action=target.action.split("#", 1)[0],
        method="get",
        autocomplete="off",
        data_pick_swap=target.swap or None,
    )[
        div(class_="dialog-search")[
            icon("search"),
            input_el(
                type="search",
                name="q",
                value=result.query,
                class_="palette-input",
                placeholder=t("search.placeholder"),
                aria_label=t("search.label"),
                aria_controls=target.dom_id,
                aria_autocomplete="list",
                autofocus=(not in_dialog) if autofocus is None else autofocus,
                spellcheck="false",
            ),
            button(type="submit", class_="btn btn-sm palette-submit")[t("search.submit")],
            span(class_="kbd palette-esc")["Esc"] if in_dialog else None,
        ],
        hidden("world", world_id or "") if world_id else None,
        hidden("all", "1") if result.all_books else None,
        hidden("pick", target.pick) if target.pick else None,
        extra,
        hidden("insert", "1") if target.insert else None,
        None
        if picking
        else _chips(
            page,
            result.group,
            only=(CAMPAIGN, *GROUP_KEYS) if target.insert else (),
        ),
        results(page, result, world_id, target),
        div(class_="dialog-foot")[
            span[span(class_="kbd")["↑↓"], t("search.keys.move")],
            span[
                span(class_="kbd")["↵"],
                t(
                    "search.keys.add"
                    if picking
                    else "search.keys.insert"
                    if target.insert
                    else "search.keys.open"
                ),
            ],
            None if picking else span[span(class_="kbd")["Esc"], t("search.keys.close")],
        ],
    ]


def search_trigger(page: Page) -> Element:
    """The bar's search field look-alike: opens the palette, or the search page without JS."""
    t = page.t
    return a(
        class_="search shell-search",
        href="/search",
        data_palette_open="",
        aria_label=t("search.open"),
        aria_keyshortcuts="Control+K Meta+K",
    )[
        icon("search"),
        span(class_="search-label")[t("search.placeholder")],
        span(class_="kbd search-kbd")["⌘K"],
    ]


def suggestions(
    page: Page,
    hits: Sequence[SearchHit],
    completion: Callable[[Row], str | None],
    *,
    create: tuple[str, str | None] = ("", None),
    where: str = "w",
) -> Element:
    """The editor's drop-down at the cursor (``::: spell F``, ``{@spell F``): each row
    completes the reference with ``completion(row)`` — ``Fireball``, ``Fireball|PHB``.
    ``create`` is ``(kind, name)`` of an NPC, encounter or treasure to offer to create
    where the editor's record is, when nothing has the name yet."""
    rows: list[Element] = []
    for index, hit in enumerate(hits):
        text = completion(hit.row)
        if not text:
            continue
        row = hit.row
        glyph, tone = CATEGORY_ICON.get(row.category, ("book-open", ""))
        line, pill = _describe(page, hit)
        rows.append(
            li(
                class_="ac-item",
                role="option",
                id=f"ac-{index}",
                data_complete=text,
                aria_selected="true" if not rows else "false",
            )[
                span(class_=f"res-ico {tone}".strip())[icon(glyph)],
                span(class_="ac-text")[
                    b[hit.label or row.name],
                    span(class_="rsub")[line],
                ],
                pill,
                span(class_="res-src mono")[row.source] if row.source else None,
            ]
        )
    kind, name = create
    if name:
        rows.append(
            li(
                class_="ac-item ac-create",
                role="option",
                id="ac-create",
                data_complete=name,
                data_create=kind,
                aria_selected="true" if not rows else "false",
            )[
                span(class_="res-ico")[icon("plus")],
                span(class_="ac-text")[
                    b[page.t(f"search.create.{kind}", name=name)],
                    span(class_="rsub")[page.t(f"search.create_in.{where}")],
                ],
            ]
        )
    return ul(class_="ac-list", role="listbox", aria_label=page.t("search.results"))[
        rows or li(class_="ac-empty")[page.t("search.no_suggestions")]
    ]


def insert_palette(page: Page, world_id: str | None) -> Element:
    """The editors' insert pop-up: the same search, its results inserting their tag."""
    target = SearchTarget(dom_id=INSERT_RESULTS_ID, insert=True)
    return dialog(
        class_="palette insert-palette",
        id="insert-palette",
        aria_label=page.t("search.insert"),
        data_button=page.t("search.insert_button"),
        data_button_title=page.t("search.insert_title"),
        # The editors' toolbar (static/js/complete.js), in the reader's language.
        data_tools=json.dumps({key: page.t(f"editor.{key}") for key in EDITOR_TOOLS}),
    )[search_form(page, SearchResult("", None, ()), world_id, in_dialog=True, target=target)]


def palette(page: Page, world_id: str | None) -> Element:
    """The ⌘K layer: an empty search form in a native dialog, filled by ``palette.js``."""
    return dialog(class_="palette", id="palette", aria_label=page.t("search.label"))[
        search_form(page, SearchResult("", None, ()), world_id, in_dialog=True)
    ]


__all__ = [
    "CATEGORY_ICON",
    "CAMPAIGN",
    "INSERT_RESULTS_ID",
    "PALETTE",
    "SearchTarget",
    "insert_palette",
    "suggestions",
    "palette",
    "results",
    "search_form",
    "search_trigger",
    "search_url",
]
