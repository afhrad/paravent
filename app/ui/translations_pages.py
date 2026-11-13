"""The translation overview at ``/gm/translations`` (documentation/TRANSLATION.md §7).

What a language covers, what to translate next (the most-linked untranslated entries),
what needs a second look (outdated or orphaned passages), translations whose entry is
gone, to move to where it went, and machine drafts of many records at once, with their
progress.
"""

from __future__ import annotations

from collections.abc import Sequence

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    code,
    div,
    form,
    h2,
    label,
    li,
    nav,
    optgroup,
    option,
    p,
    section,
    select,
    small,
    span,
    table,
    tbody,
    td,
    th,
    thead,
    tr,
    ul,
)
from htpy import input as input_el

from app.services.corpus_import import ImportRun
from app.services.i18n import Language
from app.services.translation_coverage import (
    BookCount,
    BuilderText,
    CategoryCount,
    Drafted,
    Job,
    Lost,
    Problem,
    Unfinished,
    WantedEntry,
)
from app.store.fluff import FLUFF_SUFFIX
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice, text_field
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text
from app.ui.ref_pages import CATEGORY_KEY
from app.ui.translate_pages import TRANSLATIONS_URL, translate_url

DRAFTS_URL = f"{TRANSLATIONS_URL}/drafts"
IMPORT_URL = f"{TRANSLATIONS_URL}/import"
UNFINISHED_URL = f"{TRANSLATIONS_URL}/unfinished"
BUILDER_URL = f"{TRANSLATIONS_URL}/builder"
# Jobs named by a scope rather than a book: their label's key.
JOB_LABELS = {"players": "translations.players_job", "unfinished": "translations.unfinished_job"}


def _kind(page: Page, category: str) -> str:
    """A category as the reference pages name it ("Zauber"), its code if unnamed."""
    if category.endswith(FLUFF_SUFFIX):
        return page.t(
            "translations.fluff_of", kind=_kind(page, category.removesuffix(FLUFF_SUFFIX))
        )
    key = CATEGORY_KEY.get(category)
    return page.t(key) if key else category


def _jobs(page: Page, jobs: Sequence[Job], lang: str) -> Element:
    """The machine runs, refreshing themselves while one is still going."""
    t = page.t
    running = any(not j.finished for j in jobs)
    return div(
        id="tr-jobs",
        class_="tr-jobs",
        hx_get=f"{TRANSLATIONS_URL}/jobs?lang={lang}" if running else None,
        hx_trigger="every 3s" if running else None,
        hx_swap="outerHTML" if running else None,
    )[
        ul(class_="tr-job-list")[
            [
                li(class_="tr-job" + (" is-done" if j.finished else ""))[
                    b[t(JOB_LABELS[j.label]) if j.label in JOB_LABELS else j.label],
                    span(class_="mono")[f" {j.done}/{j.total}"],
                    span(class_="muted")[
                        " · ",
                        t(
                            "translations.job_counts",
                            passages=j.passages,
                            rejected=j.rejected,
                            failed=j.failed,
                        ),
                    ],
                    span(class_="pill heal")[t("translations.job_done")]
                    if j.finished
                    else span(class_="pill accent")[t("translations.job_running")],
                    small(class_="tr-job-error")[j.error] if j.error else None,
                ]
                for j in jobs
            ]
        ]
        if jobs
        else None
    ]


def _import_kind(page: Page, kind: str) -> str:
    if kind == "book":
        return page.t("translations.rulebooks")
    if kind == "adventure":
        return page.t("translations.adventures")
    return _kind(page, kind)


def import_fragment(page: Page, run: ImportRun | None, lang: str) -> Element:
    """The newest import's state: its phase while it runs (the box reloads itself), then
    what it found, and after a check, a button to import as checked."""
    t = page.t
    running = run is not None and not run.finished
    body: list[Node] = []
    if run is not None and running:
        body.append(
            p(role="status")[
                span(class_="pill accent")[t("translations.job_running")],
                " ",
                t(f"translations.import.phase.{run.phase or 'start'}"),
            ]
        )
    elif run is not None and run.error:
        body.append(p(class_="tr-job-error")[t("translations.import.failed", error=run.error)])
    elif run is not None and run.report is not None:
        report = run.report
        if not report.kinds:
            body.append(p(class_="muted")[t("translations.import.nothing", folder=run.folder)])
        else:
            heads = ("records", "passages", "kept", "links", "unmatched")
            body.append(
                p[
                    t(
                        "translations.import.checked"
                        if run.dry_run
                        else "translations.import.done_reviewed"
                        if run.reviewed
                        else "translations.import.done",
                        folder=run.folder,
                    )
                ]
            )
            body.append(
                div(class_="tr-table-wrap")[
                    div(class_="tr-scroll")[
                        table(class_="tr-table")[
                            thead[
                                tr[
                                    th[t("translations.import.kind")],
                                    [
                                        th(class_="num")[t(f"translations.import.{h}")]
                                        for h in heads
                                    ],
                                ]
                            ],
                            tbody[
                                [
                                    tr[
                                        td[_import_kind(page, kind)],
                                        [
                                            td(class_="num mono")[str(getattr(report, h)[kind])]
                                            for h in heads
                                        ],
                                    ]
                                    for kind in report.kinds
                                ]
                            ],
                        ]
                    ]
                ]
            )
            body.append(
                p(class_="muted")[t("translations.import.names", n=sum(report.names.values()))]
            )
            if report.misaligned:
                body.append(
                    p(class_="muted")[
                        t("translations.import.misaligned", books=", ".join(report.misaligned))
                    ]
                )
            if run.dry_run:
                body.append(
                    form(method="post", action=IMPORT_URL)[
                        hidden("lang", lang),
                        hidden("folder", run.folder),
                        hidden("mode", "import"),
                        hidden("reviewed", "1") if run.reviewed else None,
                        hidden("replace", "1") if run.replace else None,
                        button(type="submit", class_="btn btn-primary")[
                            icon("upload"), span[t("translations.import.again")]
                        ],
                    ]
                )
    return div(
        id="tr-import",
        hx_get=f"{IMPORT_URL}?lang={lang}" if running else None,
        hx_trigger="every 3s" if running else None,
        hx_swap="outerHTML" if running else None,
    )[body]


def _corpus_import(page: Page, lang: str, run: ImportRun | None) -> Element:
    """A translated corpus on the server, checked first or imported at once."""
    t = page.t
    return section(class_="panel tr-import")[
        h2[t("translations.import.title")],
        p(class_="muted")[t("translations.import.hint")],
        form(method="post", action=IMPORT_URL, class_="tr-import-form")[
            hidden("lang", lang),
            text_field(
                "folder",
                t("translations.import.folder"),
                value=run.folder if run is not None else "",
                autocomplete="off",
            ),
            label(class_="check")[
                input_el(type="checkbox", name="reviewed", value="1"),
                " ",
                t("translations.import.reviewed"),
            ],
            label(class_="check")[
                input_el(type="checkbox", name="replace", value="1"),
                " ",
                t("translations.import.replace"),
            ],
            div(class_="cb-line")[
                button(type="submit", name="mode", value="check", class_="btn")[
                    icon("search"), span[t("translations.import.check")]
                ],
                button(type="submit", name="mode", value="import", class_="btn btn-primary")[
                    icon("upload"), span[t("translations.import.start")]
                ],
            ],
        ],
        import_fragment(page, run, lang),
    ]


def jobs_fragment(page: Page, jobs: Sequence[Job], lang: str) -> Element:
    """The job list alone, for the page's polling."""
    return _jobs(page, jobs, lang)


def _machine(
    page: Page,
    lang: str,
    available: bool,
    books: Sequence[BookCount],
    jobs: Sequence[Job],
) -> Element:
    t = page.t
    if not available:
        return section(class_="panel")[
            h2[t("translations.machine")],
            p(class_="muted")[t("translations.machine_off")],
        ]
    rulebooks = [x for x in books if x.kind == "book"]
    adventures = [x for x in books if x.kind == "adventure"]
    screens = [x for x in books if x.kind == "screen"]
    return section(class_="panel tr-machine")[
        h2[t("translations.machine")],
        p(class_="muted")[t("translations.machine_hint")],
        form(method="post", action=f"{TRANSLATIONS_URL}/jobs", class_="cb-line")[
            hidden("lang", lang),
            select(name="scope", aria_label=t("translations.scope"))[
                optgroup(label=t("translations.scope_top"))[
                    option(value="top:25")[t("translations.top", count=25)],
                    option(value="top:100")[t("translations.top", count=100)],
                    option(value="players")[t("translations.players_job")],
                    option(value="unfinished")[t("translations.unfinished_job")],
                    option(value="builder:creatures")[t("translations.builder_job")],
                ],
                optgroup(label=t("translations.rulebooks"))[
                    [option(value=f"book:{x.id}")[x.name] for x in rulebooks]
                ],
                optgroup(label=t("screen.tabs"))[
                    [option(value=f"screen:{x.id}")[t(f"screen.{x.id}")] for x in screens]
                ],
                optgroup(label=t("translations.adventures"))[
                    [option(value=f"adventure:{x.id}")[x.name] for x in adventures]
                ],
            ],
            button(type="submit", class_="btn btn-primary")[
                icon("sparkles"), span[t("translations.start")]
            ],
        ],
        _jobs(page, jobs, lang),
    ]


def _book_link(page: Page, count: BookCount) -> Node:
    if count.kind == "builder":
        return a(href=BUILDER_URL)[page.t("translations.builder")]
    if count.kind == "screen":
        return a(href=f"/screen?ref={count.id}")[page.t(f"screen.{count.id}")]
    return a(href=f"/books/{count.id}")[count.name] if count.kind == "book" else count.name


def _wanted(page: Page, wanted: Sequence[WantedEntry], lang: str) -> Element:
    t = page.t
    return section(class_="panel")[
        h2[t("translations.wanted")],
        p(class_="muted")[t("translations.wanted_hint")],
        div(class_="tr-scroll")[
            table(class_="tr-table")[
                thead[
                    tr[
                        th[t("translations.entry")],
                        th[t("translations.category")],
                        th[t("translations.links")],
                        th,
                    ]
                ],
                tbody[
                    [
                        tr[
                            td[name_text(page, w.category, w.name)],
                            td(class_="muted")[_kind(page, w.category)],
                            td(class_="mono")[str(w.links)],
                            td[
                                a(
                                    href=translate_url(
                                        "ref",
                                        w.category,
                                        w.source,
                                        w.name,
                                        lang=lang,
                                        back=page.path,
                                    ),
                                    class_="btn btn-sm",
                                )[t("translate.open")]
                            ],
                        ]
                        for w in wanted
                    ]
                ],
            ]
        ]
        if wanted
        else p[t("translations.wanted_none")],
    ]


def _categories(page: Page, counts: Sequence[CategoryCount]) -> Node:
    t = page.t
    if not counts:
        return None
    return section(class_="panel")[
        h2[t("translations.categories")],
        div(class_="tr-scroll")[
            table(class_="tr-table")[
                thead[
                    tr[
                        th[t("translations.category")],
                        th[t("translations.started_col")],
                        th[t("translations.drafts")],
                        th[t("translations.stale")],
                    ]
                ],
                tbody[
                    [
                        tr[
                            td[_kind(page, c.category)],
                            td(class_="mono")[f"{c.translated} / {c.entries}"],
                            td(class_="mono")[str(c.drafts) if c.drafts else "–"],
                            td(class_="mono")[str(c.stale) if c.stale else "–"],
                        ]
                        for c in counts
                    ]
                ],
            ]
        ],
    ]


def _books(page: Page, books: Sequence[BookCount]) -> Node:
    t = page.t
    # The builder's texts have no page of their own to start from: always listed.
    begun = [x for x in books if x.started or x.kind == "builder"]
    if not begun:
        return None
    return section(class_="panel")[
        h2[t("translations.books")],
        div(class_="tr-scroll")[
            table(class_="tr-table")[
                thead[
                    tr[
                        th[t("translations.book")],
                        th[t("translations.chapters")],
                        th[t("translations.drafts")],
                        th[t("translations.stale")],
                    ]
                ],
                tbody[
                    [
                        tr[
                            td[_book_link(page, x)],
                            td(class_="mono")[f"{x.started} / {x.chapters}"],
                            td(class_="mono")[str(x.drafts) if x.drafts else "–"],
                            td(class_="mono")[str(x.stale) if x.stale else "–"],
                        ]
                        for x in begun
                    ]
                ],
            ]
        ],
    ]


def _problems(page: Page, problems: Sequence[Problem], lang: str) -> Node:
    t = page.t
    if not problems:
        return None
    return section(class_="panel")[
        h2[t("translations.problems")],
        p(class_="muted")[t("translations.problems_hint")],
        ul(class_="tr-problems")[
            [
                li[
                    a(
                        href=translate_url(
                            x.target.kind, *x.target.where, lang=lang, show="todo", back=page.path
                        )
                    )[x.target.title],
                    span(class_="pill cond")[t("translations.stale_count", count=x.stale)]
                    if x.stale
                    else None,
                    span(class_="pill dmg")[t("translations.orphans", count=x.orphans)]
                    if x.orphans
                    else None,
                ]
                for x in problems
            ]
        ],
    ]


def _drafts_link(page: Page, drafts: int, lang: str) -> Node:
    """How many records wait for their drafts to be read, and the list of them."""
    t = page.t
    if not drafts:
        return None
    return section(class_="panel")[
        h2[t("translations.drafts_title")],
        p(class_="muted")[
            t.plural("translations.drafts_waiting", drafts),
            " ",
            a(href=f"{DRAFTS_URL}?lang={lang}")[t("translations.drafts_review")],
        ],
    ]


def _unfinished_link(page: Page, unfinished: Sequence[Unfinished], lang: str) -> Node:
    """How many records are translated only in part, and the list of them."""
    t = page.t
    if not unfinished:
        return None
    return section(class_="panel")[
        h2[t("translations.unfinished_title")],
        p(class_="muted")[
            t.plural("translations.unfinished_waiting", len(unfinished)),
            " ",
            t.plural("translations.unfinished_open", sum(u.open for u in unfinished)),
            " ",
            a(href=f"{UNFINISHED_URL}?lang={lang}")[t("translations.unfinished_show")],
        ],
    ]


def unfinished_page(
    page: Page,
    *,
    lang: str,
    languages: Sequence[Language],
    unfinished: Sequence[Unfinished],
    machine: bool,
) -> Element:
    """Every record translated in part, by category, each opening its editor at the
    passages still English and coming back here; and one run trying them all again."""
    t = page.t
    language = next((x.name for x in languages if x.code == lang), lang)
    groups: dict[str, list[Unfinished]] = {}
    for x in unfinished:
        groups.setdefault(x.target.category, []).append(x)
    back = f"{UNFINISHED_URL}?lang={lang}"
    return app_shell(
        page,
        t("translations.unfinished_title"),
        div(class_="tr-page")[
            page_head(
                t("translations.eyebrow"),
                t("translations.unfinished_title_in", language=language),
                t("translations.unfinished_lede"),
            ),
            p[a(href=f"{TRANSLATIONS_URL}?lang={lang}")[t("translations.title")]],
            form(method="post", action=f"{TRANSLATIONS_URL}/jobs", class_="cb-line")[
                hidden("lang", lang),
                hidden("scope", "unfinished"),
                button(type="submit", class_="btn btn-primary")[
                    icon("sparkles"),
                    span[
                        t.plural("translations.unfinished_retry", sum(u.open for u in unfinished))
                    ],
                ],
                small(class_="muted")[t("translations.unfinished_retry_hint")],
            ]
            if unfinished and machine
            else None,
            [
                section(class_="panel")[
                    h2[_kind(page, category), " ", small(class_="muted")[str(len(items))]],
                    ul(class_="tr-problems")[
                        [
                            li[
                                a(
                                    href=translate_url(
                                        x.target.kind,
                                        *x.target.where,
                                        lang=lang,
                                        show="todo",
                                        back=back,
                                    )
                                )[x.target.title],
                                span(class_="pill cond")[
                                    t("translations.unfinished_count", open=x.open, total=x.total)
                                ],
                            ]
                            for x in items
                        ]
                    ],
                ]
                for category, items in groups.items()
            ]
            if unfinished
            else p(class_="muted")[t("translations.unfinished_none")],
        ],
    )


def builder_page(
    page: Page,
    *,
    lang: str,
    languages: Sequence[Language],
    texts: Sequence[BuilderText],
    machine: bool,
) -> Element:
    """The creature builder's ready-made texts — monster features, traits and actions —
    each with how far it is translated, opening its editor and coming back here; and one
    run drafting all of them."""
    t = page.t
    language = next((x.name for x in languages if x.code == lang), lang)
    back = f"{BUILDER_URL}?lang={lang}"
    groups: dict[str, list[BuilderText]] = {}
    for x in texts:
        groups.setdefault(x.target.where[0], []).append(x)
    open_texts = sum(1 for x in texts if x.translated < x.total)

    def state(x: BuilderText) -> Node:
        if x.translated == 0:
            return span(class_="muted")[t("translations.builder_open")]
        pills: list[Node] = []
        if x.translated < x.total:
            pills.append(
                span(class_="pill cond")[
                    t("translations.unfinished_count", open=x.total - x.translated, total=x.total)
                ]
            )
        if x.drafts:
            pills.append(span(class_="pill cond")[t("translate.draft")])
        if x.stale:
            pills.append(span(class_="pill dmg")[t("translate.stale")])
        return pills or span(class_="pill")[t("translations.builder_done")]

    return app_shell(
        page,
        t("translations.builder"),
        div(class_="tr-page")[
            page_head(
                t("translations.eyebrow"),
                t("translations.builder_in", language=language),
                t("translations.builder_lede"),
            ),
            p[a(href=f"{TRANSLATIONS_URL}?lang={lang}")[t("translations.title")]],
            form(method="post", action=f"{TRANSLATIONS_URL}/jobs", class_="cb-line")[
                hidden("lang", lang),
                hidden("scope", "builder:creatures"),
                button(type="submit", class_="btn btn-primary")[
                    icon("sparkles"), span[t.plural("translations.builder_run", open_texts)]
                ],
            ]
            if open_texts and machine
            else None,
            [
                section(class_="panel")[
                    h2[
                        t(f"translations.builder_{kind}"),
                        " ",
                        small(class_="muted")[str(len(items))],
                    ],
                    ul(class_="tr-problems")[
                        [
                            li[
                                a(
                                    href=translate_url(
                                        "builder", *x.target.where, lang=lang, back=back
                                    )
                                )[x.target.title],
                                " ",
                                state(x),
                            ]
                            for x in items
                        ]
                    ],
                ]
                for kind, items in groups.items()
            ]
            if texts
            else p(class_="muted")[t("translations.builder_none")],
        ],
    )


def drafts_page(
    page: Page,
    *,
    lang: str,
    languages: Sequence[Language],
    drafts: Sequence[Drafted],
    message: str | None = None,
) -> Element:
    """Every record with unconfirmed drafts, by category, each opening its editor at the
    passages still to read and coming back here."""
    t = page.t
    language = next((x.name for x in languages if x.code == lang), lang)
    groups: dict[str, list[Drafted]] = {}
    for x in drafts:
        groups.setdefault(x.target.category, []).append(x)
    back = f"{DRAFTS_URL}?lang={lang}"
    return app_shell(
        page,
        t("translations.drafts_title"),
        div(class_="tr-page")[
            page_head(
                t("translations.eyebrow"),
                t("translations.drafts_title_in", language=language),
                t("translations.drafts_lede"),
            ),
            p[a(href=f"{TRANSLATIONS_URL}?lang={lang}")[t("translations.title")]],
            notice(message),
            form(method="post", action=f"{DRAFTS_URL}/confirm", class_="cb-line")[
                hidden("lang", lang),
                button(type="submit", class_="btn btn-primary")[
                    icon("check"),
                    span[t.plural("translations.drafts_confirm_all", len(drafts))],
                ],
                small(class_="muted")[t("translations.drafts_confirm_hint")],
            ]
            if drafts
            else None,
            [
                section(class_="panel")[
                    h2[_kind(page, category), " ", small(class_="muted")[str(len(items))]],
                    ul(class_="tr-problems")[
                        [
                            li[
                                a(
                                    href=translate_url(
                                        x.target.kind,
                                        *x.target.where,
                                        lang=lang,
                                        show="todo",
                                        back=back,
                                    )
                                )[x.target.title],
                                span(class_="pill cond")[
                                    t.plural("translations.drafts_count", x.drafts)
                                ],
                                small(class_="muted")[
                                    " · ".join(v for v in (x.by, x.at[:10]) if v)
                                ],
                            ]
                            for x in items
                        ]
                    ],
                ]
                for category, items in groups.items()
            ]
            if drafts
            else p(class_="muted")[t("translations.drafts_none")],
        ],
    )


def _lost(page: Page, lost: Sequence[Lost], lang: str, taken: str, entry: str) -> Node:
    t = page.t
    if not lost:
        return None
    return section(id="lost", class_="panel")[
        h2[t("translations.lost")],
        p(class_="muted")[t("translations.lost_hint")],
        ul(class_="tr-problems")[
            [
                li[
                    span(class_="muted")[_kind(page, x.category)],
                    code[x.key],
                    span(class_="pill cond")[t.plural("translations.passages", x.passages)],
                    form(method="post", action=f"{TRANSLATIONS_URL}/move", class_="cb-line")[
                        hidden("lang", lang),
                        hidden("category", x.category),
                        hidden("key", x.key),
                        input_el(
                            name="to",
                            value=entry if x.key == taken else x.near,
                            required=True,
                            aria_label=t("translations.move_to"),
                            placeholder=x.key,
                        ),
                        label[
                            input_el(type="checkbox", name="replace", value="1"),
                            " ",
                            t("translations.move_replace"),
                        ]
                        if x.key == taken
                        else None,
                        button(type="submit", class_="btn btn-sm")[t("translations.move")],
                    ],
                ]
                for x in lost
            ]
        ],
    ]


def translations_page(
    page: Page,
    *,
    lang: str,
    languages: Sequence[Language],
    categories: Sequence[CategoryCount],
    books: Sequence[BookCount],
    wanted: Sequence[WantedEntry],
    problems: Sequence[Problem],
    lost: Sequence[Lost],
    jobs: Sequence[Job],
    corpus_import: ImportRun | None = None,
    drafts: int = 0,
    unfinished: Sequence[Unfinished] = (),
    machine: bool,
    message: str | None = None,
    error: str | None = None,
    taken: str = "",
    entry: str = "",
) -> Element:
    """The language's coverage, what to do next, and machine drafts in bulk."""
    t = page.t
    language = next((x.name for x in languages if x.code == lang), lang)
    return app_shell(
        page,
        t("translations.title"),
        div(class_="tr-page")[
            page_head(
                t("translations.eyebrow"),
                t("translations.title_in", language=language),
                t("translations.lede"),
            ),
            nav(class_="tr-bar", aria_label=t("translate.languages"))[
                ul(class_="tr-langs")[
                    [
                        li[
                            a(
                                href=f"{TRANSLATIONS_URL}?lang={x.code}",
                                aria_current="page" if x.code == lang else None,
                            )[x.name]
                        ]
                        for x in languages
                    ]
                ]
                if len(languages) > 1
                else None,
            ],
            alert(error),
            notice(message),
            _machine(page, lang, machine, books, jobs),
            _corpus_import(page, lang, corpus_import),
            _lost(page, lost, lang, taken, entry),
            _drafts_link(page, drafts, lang),
            _unfinished_link(page, unfinished, lang),
            _problems(page, problems, lang),
            _wanted(page, wanted, lang),
            _categories(page, categories),
            _books(page, books),
        ],
        extra_scripts=("/static/js/htmx.min.js",),
    )


__all__ = [
    "DRAFTS_URL",
    "IMPORT_URL",
    "TRANSLATIONS_URL",
    "UNFINISHED_URL",
    "drafts_page",
    "import_fragment",
    "jobs_fragment",
    "translations_page",
    "unfinished_page",
]
