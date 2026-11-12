"""The corpus check at ``/gm/corpus``: which 5eTools data this is, and whether Paravent
reads all of it (app/services/corpus_check.py).

The newest report is shown, whether this server ran it or ``python -m app.cli corpus
check`` did; a run starts in the background and the page reloads when it is done.
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
    details,
    div,
    form,
    h2,
    label,
    li,
    p,
    section,
    small,
    span,
    summary,
    table,
    tbody,
    td,
    th,
    thead,
    tr,
    ul,
)
from htpy import input as input_el

from app.services.corpus_check import (
    CategoryCount,
    Changes,
    CorpusReport,
    CorpusVersion,
    FindingGroup,
    KeyUse,
    Severity,
    UnreadFile,
)
from app.ui.context import Page
from app.ui.forms import alert
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

CORPUS_URL = "/gm/corpus"

_PILL = {Severity.ERROR: "dmg", Severity.WARNING: "cond", Severity.INFO: "accent"}


def _when(stamp: str) -> str:
    """An ISO timestamp as "2026-10-26 14:02" (UTC, as stored)."""
    return stamp.replace("T", " ")[:16]


def version_line(page: Page, version: CorpusVersion | None) -> str:
    """ "5eTools 2.29.0 · 2026-05-30", or that the data does not say."""
    if version is None:
        return page.t("corpus.version_unknown")
    return page.t("corpus.version", version=version.version, date=version.date or "?")


def status_fragment(page: Page, running: bool, failure: str | None) -> Element:
    """Whether a check is running; polls itself while it is, the page reloads after."""
    t = page.t
    return div(
        id="corpus-status",
        class_="corpus-status",
        role="status",
        hx_get=f"{CORPUS_URL}/status" if running else None,
        hx_trigger="every 2s" if running else None,
        hx_swap="outerHTML" if running else None,
    )[
        span(class_="pill accent")[t("corpus.running")]
        if running
        else alert(t("corpus.failed", error=failure))
        if failure
        else None
    ]


def _run_form(page: Page, running: bool, references: bool) -> Element:
    t = page.t
    return form(method="post", action=f"{CORPUS_URL}/check", class_="corpus-run")[
        label(class_="corpus-check-refs")[
            input_el(type="checkbox", name="references", value="1", checked=references),
            span[t("corpus.references")],
        ],
        button(type="submit", class_="btn btn-primary", disabled=running)[
            icon("refresh-cw"), span[t("corpus.run")]
        ],
    ]


def _summary(page: Page, report: CorpusReport) -> Element:
    t = page.t
    verdict = (
        span(class_="pill heal")[icon("check", "xs"), t("corpus.ok")]
        if report.ok
        else span(class_="pill dmg")[
            icon("triangle-alert", "xs"), t("corpus.failed_count", count=report.errors)
        ]
    )
    tiles = (
        (t("corpus.files_read"), f"{report.files_read} / {report.files}"),
        (t("corpus.entries"), t.number(report.entries)),
        (t("corpus.fluff"), t.number(report.fluff)),
        (t("corpus.errors"), str(report.errors)),
        (t("corpus.warnings"), str(report.warnings)),
        *(((t("corpus.library"), str(report.library_findings)),) if report.library else ()),
    )
    return section(class_="panel corpus-summary")[
        div(class_="corpus-verdict")[
            verdict,
            small(class_="muted")[
                t(
                    "corpus.checked",
                    when=_when(report.checked_at),
                    seconds=f"{report.seconds:.1f}",
                ),
                " · ",
                t("corpus.with_references") if report.references else t("corpus.no_references"),
            ],
        ],
        ul(class_="corpus-tiles")[
            [
                li(class_="corpus-tile")[span(class_="muted")[name], b[value]]
                for name, value in tiles
            ]
        ],
        p(class_="muted")[t("corpus.vtt_skipped", count=report.vtt_files)]
        if report.vtt_files
        else None,
    ]


def _group(page: Page, group: FindingGroup) -> Element:
    t = page.t
    kind = group.kind.value
    return details(
        class_=f"panel corpus-group is-{group.severity.value}",
        open=group.severity is not Severity.INFO,
    )[
        summary(class_="corpus-group-head")[
            span(class_=f"pill {_PILL[group.severity]}")[t(f"corpus.severity.{group.severity}")],
            b[t(f"corpus.kind.{kind}")],
            span(class_="mono")[str(group.count)],
        ],
        p(class_="muted")[t(f"corpus.hint.{kind}")],
        div(class_="corpus-scroll")[
            table(class_="tr-table corpus-table")[
                thead[tr[th[t("corpus.file")], th[t("corpus.subject")], th[t("corpus.detail")]]],
                tbody[
                    [
                        tr[
                            td(class_="mono")[x.file],
                            td[a(href=x.href)[x.subject] if x.href else x.subject],
                            td(class_="mono corpus-detail")[x.detail],
                        ]
                        for x in group.items
                    ]
                ],
            ]
        ],
        p(class_="muted")[t("corpus.more", count=group.count - len(group.items))]
        if group.count > len(group.items)
        else None,
    ]


def _changes(page: Page, changes: Changes | None, report: CorpusReport) -> Node:
    t = page.t
    if changes is None:
        return None
    version = report.version.version if report.version else ""
    lines: list[Node] = []
    if changes.version_before and version and changes.version_before != version:
        lines.append(li[t("corpus.change.version", before=changes.version_before, after=version)])
    lines += [li[t("corpus.change.new_key"), " ", code[k]] for k in changes.new_keys]
    lines += [li[t("corpus.change.gone_key"), " ", code[k]] for k in changes.gone_keys]
    lines += [
        li[t("corpus.change.rows", category=c, before=before, after=after)]
        for c, (before, after) in changes.rows.items()
    ]
    lines += [
        li[t("corpus.change.fields", category=c), " ", code[", ".join(fields)]]
        for c, fields in changes.new_fields.items()
    ]
    lines += [
        li[t("corpus.change.library", kind=t(f"corpus.kind.{k}"), before=before, after=after)]
        for k, (before, after) in changes.library.items()
    ]
    return section(class_="panel")[
        h2[t("corpus.changes", when=_when(changes.since))],
        ul(class_="corpus-list")[lines] if lines else p(class_="muted")[t("corpus.no_changes")],
    ]


def _counts(page: Page, counts: Sequence[CategoryCount]) -> Element:
    t = page.t
    return details(class_="panel corpus-group")[
        summary(class_="corpus-group-head")[b[t("corpus.counts")]],
        p(class_="muted")[t("corpus.counts_hint")],
        div(class_="corpus-scroll")[
            table(class_="tr-table corpus-table")[
                thead[
                    tr[
                        th[t("corpus.category")],
                        th[t("corpus.files")],
                        th[t("corpus.rows")],
                        th[t("corpus.rejected")],
                        th[t("corpus.repeated")],
                        th[t("corpus.made")],
                        th[t("corpus.loaded")],
                        th,
                    ]
                ],
                tbody[
                    [
                        tr[
                            td[c.category],
                            td(class_="mono")[str(c.files)],
                            td(class_="mono")[str(c.rows)],
                            td(class_="mono")[
                                str(c.invalid + c.unresolved) if c.invalid + c.unresolved else "–"
                            ],
                            td(class_="mono")[str(c.repeated) if c.repeated else "–"],
                            td(class_="mono")[str(c.made) if c.made else "–"],
                            td(class_="mono")[str(c.loaded)],
                            td[
                                span(class_="pill heal")[t("corpus.match")]
                                if c.matches
                                else span(class_="pill dmg")[t("corpus.mismatch")]
                            ],
                        ]
                        for c in counts
                    ]
                ],
            ]
        ],
    ]


def _not_loaded(page: Page, keys: Sequence[KeyUse], unread: Sequence[UnreadFile]) -> Element:
    t = page.t
    planned = [k for k in keys if k.status == "planned"]
    skipped = [k for k in keys if k.status == "skipped"]
    return details(class_="panel corpus-group")[
        summary(class_="corpus-group-head")[b[t("corpus.not_loaded")]],
        p(class_="muted")[t("corpus.not_loaded_hint")],
        ul(class_="corpus-list")[
            [
                li[code[k.key], " ", small(class_="muted")[t("corpus.key_rows", count=k.rows)]]
                for k in planned
            ]
        ]
        if planned
        else None,
        h2[t("corpus.skipped_keys")] if skipped else None,
        ul(class_="corpus-list")[
            [
                li[code[k.key], " ", small(class_="muted")[t(f"corpus.reason.{k.reason}")]]
                for k in skipped
            ]
        ]
        if skipped
        else None,
        h2[t("corpus.unread_files")],
        ul(class_="corpus-list")[
            [
                li[
                    code[f.file],
                    " ",
                    small(class_="muted")[t(f"corpus.reason.{f.reason}")],
                ]
                for f in unread
            ]
        ],
    ]


def _fields(page: Page, fields: dict[str, dict[str, int]]) -> Node:
    t = page.t
    if not fields:
        return None
    return details(class_="panel corpus-group")[
        summary(class_="corpus-group-head")[b[t("corpus.fields")]],
        p(class_="muted")[t("corpus.fields_hint")],
        ul(class_="corpus-list")[
            [
                li[
                    b[category],
                    " ",
                    span(class_="mono corpus-detail")[
                        ", ".join(f"{name} ({rows})" for name, rows in found.items())
                    ],
                ]
                for category, found in fields.items()
            ]
        ],
    ]


def corpus_page(
    page: Page,
    *,
    configured: bool,
    root: str,
    version: CorpusVersion | None,
    report: CorpusReport | None,
    running: bool,
    failure: str | None,
) -> Element:
    """The corpus's version, the newest check, and a button to run another."""
    t = page.t
    head = page_head(t("settings.menu"), t("corpus.title"), t("corpus.lede"))
    if not configured:
        body: Node = empty_state(
            "book-open", t("corpus.unconfigured"), t("corpus.unconfigured_hint")
        )
        return app_shell(page, t("corpus.title"), div(class_="tr-page")[head, body])
    references = report.references if report is not None else False
    return app_shell(
        page,
        t("corpus.title"),
        div(class_="tr-page corpus-page")[
            head,
            section(class_="panel corpus-head")[
                div[
                    h2[version_line(page, version)],
                    p(class_="muted")[t("corpus.root"), " ", code[root]],
                ],
                _run_form(page, running, references),
                status_fragment(page, running, failure),
            ],
            [
                _summary(page, report),
                [_group(page, g) for g in report.groups],
                _changes(page, report.changes, report),
                _counts(page, report.counts),
                _not_loaded(page, report.keys, report.unread),
                _fields(page, report.fields),
            ]
            if report is not None
            else empty_state("shield-check", t("corpus.never"), t("corpus.never_hint")),
            p(class_="muted")[t("corpus.cli"), " ", code["python -m app.cli corpus check"]],
        ],
        extra_scripts=("/static/js/htmx.min.js",),
    )


__all__ = ["CORPUS_URL", "corpus_page", "status_fragment", "version_line"]
