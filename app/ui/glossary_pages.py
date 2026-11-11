"""The term glossary page (documentation/TRANSLATION.md §4).

Two lists for one language: the entries the rules text links to most that have no name
yet — translate what the table actually meets — and the names given so far. Each row
saves on its own, so a session of naming is a run of small edits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlencode

from htpy import Element, a, button, div, form, h2, li, nav, p, section, span, ul
from htpy import input as input_el

from app.services.glossary import Term, Wanted
from app.services.name_suggestions import Progress
from app.ui.context import Page
from app.ui.home_pages import page_head
from app.ui.layout import app_shell
from app.ui.tags import ref_url

GLOSSARY_URL = "/gm/glossary"
OPEN_URL = f"{GLOSSARY_URL}/open"
# Names to give per page of the open list.
PER_PAGE = 100

# The categories worth naming first, in the order the filter offers them.
CATEGORIES = (
    "spell",
    "monster",
    "item",
    "condition",
    "status",
    "action",
    "variantrule",
    "feat",
    "race",
    "class",
    "background",
    "optionalfeature",
    "sense",
    "language",
)


def _url(lang: str, category: str = "") -> str:
    query = {"lang": lang, **({"category": category} if category else {})}
    return f"{GLOSSARY_URL}?{urlencode(query)}"


def _name_form(
    page: Page, lang: str, category: str, english: str, value: str, here: str
) -> Element:
    t = page.t
    return form(method="post", action="/glossary/term", class_="gl-form")[
        input_el(type="hidden", name="lang", value=lang),
        input_el(type="hidden", name="category", value=category),
        input_el(type="hidden", name="english", value=english),
        input_el(type="hidden", name="next", value=here),
        input_el(
            type="text",
            name="value",
            value=value,
            maxlength="120",
            placeholder=english,
            aria_label=t("glossary.name_of", name=english),
        ),
        button(type="submit", class_="btn btn-sm")[t("glossary.save")],
    ]


def _kind(page: Page, cat: str) -> str:
    label = page.t(f"glossary.category.{cat}")
    return cat if label == f"glossary.category.{cat}" else label


def open_url(
    lang: str,
    category: str = "",
    q: str = "",
    number: int = 1,
    *,
    players: bool = False,
    **extra: str,
) -> str:
    """The list of names still to give, for one language, category, search and page;
    ``players`` keeps to the names players meet."""
    query = {
        "lang": lang,
        **({"category": category} if category else {}),
        **({"q": q} if q else {}),
        **({"players": "1"} if players else {}),
        **({"page": str(number)} if number > 1 else {}),
        **extra,
    }
    return f"{OPEN_URL}?{urlencode(query)}"


def in_view(still: Mapping[str, Sequence[str]], category: str, q: str) -> dict[str, list[str]]:
    """The open names a category and search show: category → English names."""
    needle = q.strip().lower()
    return {
        cat: shown
        for cat, names in still.items()
        if not category or cat == category
        for shown in [[n for n in names if needle in n.lower()]]
        if shown
    }


def suggest_status(
    page: Page, lang: str, category: str, q: str, run: Progress | None, *, players: bool = False
) -> Element:
    """How the latest run of suggestions is going; it reloads itself while it runs."""
    t = page.t
    running = run is not None and not run.finished
    here = open_url(lang, category, q, players=players)
    polled = {"lang": lang, "category": category, "q": q, **({"players": "1"} if players else {})}
    return div(
        id="gl-suggest-status",
        class_="gl-suggest-status",
        role="status",
        hx_get=f"{OPEN_URL}/progress?{urlencode(polled)}" if running else None,
        hx_trigger="every 3s" if running else None,
        hx_swap="outerHTML" if running else None,
    )[
        None
        if run is None
        else span[t("glossary.suggest.running", done=run.done, total=run.total)]
        if running
        else [
            span[t("glossary.suggest.finished", suggested=run.suggested, failed=run.failed)],
            span(class_="muted")[f" — {run.error}"] if run.error else None,
            " ",
            a(href=here)[t("glossary.suggest.reload")],
        ]
    ]


def _suggest_panel(
    page: Page,
    lang: str,
    category: str,
    q: str,
    available: bool,
    asking: int,
    run: Progress | None,
    players: bool = False,
) -> Element:
    t = page.t
    if not available:
        return p(class_="muted")[t("glossary.suggest.off")]
    running = run is not None and not run.finished
    return div(class_="gl-suggest")[
        form(method="post", action=f"{OPEN_URL}/suggest", class_="gl-search")[
            input_el(type="hidden", name="lang", value=lang),
            input_el(type="hidden", name="category", value=category),
            input_el(type="hidden", name="q", value=q),
            input_el(type="hidden", name="players", value="1") if players else None,
            button(type="submit", class_="btn btn-sm", disabled=running or not asking)[
                t("glossary.suggest.ask")
            ],
            span(class_="muted")[t.plural("glossary.suggest.hint", asking)],
        ],
        suggest_status(page, lang, category, q, run, players=players),
    ]


def _player_switch(page: Page, lang: str, category: str, q: str, players: bool) -> Element:
    """Every open name, or only those players meet: in the books they read, of the kinds
    they read (documentation/PLAYER-VIEW.md)."""
    t = page.t
    return div(class_="gl-filters", role="group", aria_label=t("glossary.open.scope"))[
        a(
            href=open_url(lang, category, q),
            class_="chip",
            aria_current=None if players else "true",
        )[t("glossary.open.scope_all")],
        a(
            href=open_url(lang, category, q, players=True),
            class_="chip",
            aria_current="true" if players else None,
        )[t("glossary.open.scope_players")],
    ]


def open_page(
    page: Page,
    lang: str,
    category: str,
    q: str,
    number: int,
    still: Mapping[str, Sequence[str]],
    saved: int | None = None,
    *,
    suggestions: Mapping[str, Mapping[str, str]] | None = None,
    available: bool = False,
    run: Progress | None = None,
    players: bool = False,
) -> Element:
    """Every name the glossary file still marks ``"-"``, a page at a time, to type in
    and save together. Empty fields stay ``"-"``. A suggested name shows under its
    field, to take one by one or for the whole page; it is a name only once saved.
    ``players``: ``still`` holds only the names players meet, and links keep to them."""
    t = page.t
    suggested = suggestions or {}
    others = [x for x in t.languages if x.code != "en"]
    view = in_view(still, category, q)
    rows = [
        (cat, english)
        for cat in sorted(view, key=lambda c: _kind(page, c).lower())
        for english in view[cat]
    ]
    asking = sum(1 for cat, english in rows if english not in suggested.get(cat, {}))
    pages = max(1, -(-len(rows) // PER_PAGE))
    number = min(max(1, number), pages)
    shown = rows[(number - 1) * PER_PAGE : number * PER_PAGE]
    total = sum(len(names) for names in still.values())

    chips = div(class_="gl-filters", role="group", aria_label=t("glossary.filter"))[
        [
            a(
                href=open_url(x.code, category, q, players=players),
                class_="chip",
                aria_current="true" if x.code == lang else None,
            )[x.name]
            for x in others
        ]
        if len(others) > 1
        else None,
        a(
            href=open_url(lang, "", q, players=players),
            class_="chip",
            aria_current=None if category else "true",
        )[f"{t('glossary.all')} · {total}"],
        [
            a(
                href=open_url(lang, cat, q, players=players),
                class_="chip",
                aria_current="true" if cat == category else None,
            )[f"{_kind(page, cat)} · {len(still[cat])}"]
            for cat in sorted(still, key=lambda c: (-len(still[c]), _kind(page, c).lower()))
        ],
    ]
    search = form(method="get", action=OPEN_URL, class_="gl-search")[
        input_el(type="hidden", name="lang", value=lang),
        input_el(type="hidden", name="category", value=category) if category else None,
        input_el(type="hidden", name="players", value="1") if players else None,
        input_el(
            type="search",
            name="q",
            value=q,
            placeholder=t("glossary.open.search"),
            aria_label=t("glossary.open.search"),
        ),
        button(type="submit", class_="btn btn-sm")[t("glossary.open.search_button")],
    ]

    def pager() -> Element | None:
        if pages == 1:
            return None
        return nav(class_="gl-pager", aria_label=t("glossary.open.pages"))[
            a(
                href=open_url(lang, category, q, number - 1, players=players),
                class_="btn btn-sm btn-ghost",
            )[t("glossary.open.prev")]
            if number > 1
            else None,
            span(class_="muted")[t("glossary.open.page", page=number, pages=pages)],
            a(
                href=open_url(lang, category, q, number + 1, players=players),
                class_="btn btn-sm btn-ghost",
            )[t("glossary.open.next")]
            if number < pages
            else None,
        ]

    save = button(type="submit", class_="btn btn-sm btn-primary")[t("glossary.open.save")]
    take_all = (
        button(type="submit", name="action", value="take_all", class_="btn btn-sm")[
            t("glossary.suggest.take_all")
        ]
        if any(english in suggested.get(cat, {}) for cat, english in shown)
        else None
    )

    def offer(cat: str, english: str) -> Element | None:
        name = suggested.get(cat, {}).get(english)
        if name is None:
            return None
        return button(
            type="submit",
            name="take",
            value=f"{cat}:{english}",
            class_="chip gl-take",
            title=t("glossary.suggest.take"),
        )[t("glossary.suggest.offer", name=name)]

    if not shown:
        body: Element = p(class_="muted")[t("glossary.open.none")]
    else:
        body = form(method="post", action=OPEN_URL, class_="gl-open")[
            input_el(type="hidden", name="lang", value=lang),
            input_el(type="hidden", name="category", value=category),
            input_el(type="hidden", name="q", value=q),
            input_el(type="hidden", name="page", value=str(number)),
            input_el(type="hidden", name="players", value="1") if players else None,
            div(class_="gl-open-bar")[save, take_all, pager()],
            ul(class_="gl-list")[
                [
                    li(class_="gl-row")[
                        None if category else span(class_="pill neutral")[_kind(page, cat)],
                        a(
                            href=ref_url(cat, english, "-"),
                            class_="gl-en",
                            target="_blank",
                            rel="noopener",
                        )[english],
                        input_el(
                            type="text",
                            name=f"n:{cat}:{english}",
                            maxlength="120",
                            placeholder=english,
                            aria_label=t("glossary.name_of", name=english),
                            class_="gl-open-input",
                        ),
                        offer(cat, english),
                    ]
                    for cat, english in shown
                ]
            ],
            div(class_="gl-open-bar")[save, take_all, pager()],
        ]
    return app_shell(
        page,
        t("glossary.open.title"),
        div(class_="gl-page")[
            a(href=_url(lang, category), class_="backlink")[t("glossary.title")],
            page_head(t("gm.eyebrow"), t("glossary.open.title"), t("glossary.open.lede")),
            div(class_="alert ok", role="status")[t.plural("glossary.open.saved", saved)]
            if saved
            else None,
            chips,
            search,
            _player_switch(page, lang, category, q, players),
            _suggest_panel(page, lang, category, q, available, asking, run, players),
            p(class_="muted")[t("glossary.open.hint")],
            body,
        ],
    )


def glossary_page(
    page: Page,
    lang: str,
    category: str,
    terms: Sequence[Term],
    wanted: Sequence[Wanted],
    still: int = 0,
) -> Element:
    """The glossary of one language; ``still`` is how many names the file lists as
    ``"-"``, linked to the list to give them."""
    t = page.t
    here = _url(lang, category)
    others = [x for x in t.languages if x.code != "en"]
    language = next((x.name for x in others if x.code == lang), lang)

    filters = div(class_="gl-filters", role="group", aria_label=t("glossary.filter"))[
        [
            a(
                href=_url(x.code, category),
                class_="chip",
                aria_current="true" if x.code == lang else None,
            )[x.name]
            for x in others
        ]
        if len(others) > 1
        else None,
        a(href=_url(lang), class_="chip", aria_current=None if category else "true")[
            t("glossary.all")
        ],
        [
            a(
                href=_url(lang, cat),
                class_="chip",
                aria_current="true" if cat == category else None,
            )[_kind(page, cat)]
            for cat in CATEGORIES
        ],
    ]
    if not lang:
        body: Element = p(class_="muted")[t("glossary.no_language")]
    else:
        body = div(class_="gl-lists")[
            section(class_="panel")[
                div(class_="panel-head")[
                    h2[t("glossary.wanted", language=language)],
                    p(class_="muted")[t("glossary.wanted_hint")],
                ],
                ul(class_="gl-list")[
                    [
                        li(class_="gl-row")[
                            span(class_="pill neutral")[_kind(page, w.category)],
                            a(href=ref_url(w.category, w.english, "-"), class_="gl-en")[w.english],
                            span(class_="muted mono gl-links")[t.plural("glossary.links", w.links)],
                            _name_form(page, lang, w.category, w.english, "", here),
                        ]
                        for w in wanted
                    ]
                ]
                if wanted
                else p(class_="muted")[t("glossary.nothing_wanted")],
            ],
            section(class_="panel")[
                div(class_="panel-head")[
                    h2[t.plural("glossary.named", len(terms), language=language)],
                    p(class_="muted")[t("glossary.named_hint")],
                ],
                ul(class_="gl-list")[
                    [
                        li(class_="gl-row")[
                            span(class_="pill neutral")[_kind(page, term.category)],
                            a(href=ref_url(term.category, term.english, "-"), class_="gl-en")[
                                term.english
                            ],
                            _name_form(
                                page, lang, term.category, term.english, term.localized, here
                            ),
                        ]
                        for term in terms
                    ]
                ]
                if terms
                else p(class_="muted")[t("glossary.nothing_named")],
            ],
        ]
    return app_shell(
        page,
        t("glossary.title"),
        div(class_="gl-page")[
            page_head(
                t("gm.eyebrow"),
                t("glossary.title"),
                t("glossary.lede"),
                a(href=open_url(lang, category), class_="btn")[
                    t.plural("glossary.open.link", still)
                ]
                if lang and still
                else None,
            ),
            filters,
            body,
        ],
    )


__all__ = [
    "GLOSSARY_URL",
    "OPEN_URL",
    "glossary_page",
    "in_view",
    "open_page",
    "open_url",
    "suggest_status",
]
