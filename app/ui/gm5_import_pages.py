"""Importing a *Game Master 5e* backup at ``/gm/import/gm5`` (app/services/gm5_import.py):
the file, then a campaign and the books to match against, what that would make, and
**Import**."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import (
    Element,
    Node,
    b,
    button,
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
    ul,
)
from htpy import input as input_el

from app.services.gm5_backup import Backup
from app.services.gm5_import import CreatureMatch, Plan
from app.services.worlds import BookCatalog
from app.ui.context import Page
from app.ui.forms import alert, text_field
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.world_menu import GM5_URL


def upload_page(page: Page, error: str | None = None) -> Element:
    """The backup file to upload."""
    t = page.t
    return app_shell(
        page,
        t("gm5.title"),
        div(class_="tr-page gm5-page")[
            page_head(t("gm.eyebrow"), t("gm5.title"), t("gm5.lede")),
            alert(t(error) if error else None),
            section(class_="panel")[
                form(
                    method="post",
                    action=GM5_URL,
                    enctype="multipart/form-data",
                    class_="stack-form",
                    data_busy=t("gm5.reading"),
                )[
                    div(class_="field")[
                        label(for_="f-gm5-file")[t("gm5.file")],
                        input_el(id="f-gm5-file", type="file", name="file", required=True),
                        p(class_="field-hint")[t("gm5.file_hint")],
                    ],
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("upload"), span[t("gm5.read")]
                        ]
                    ],
                ]
            ],
        ],
    )


def _books(page: Page, catalog: BookCatalog, chosen: set[str]) -> Element:
    t = page.t
    return section(class_="field books")[
        h2(class_="eyebrow")[t("gm5.books")],
        p(class_="field-hint")[t("gm5.books_hint")],
        [
            details(class_="book-group", open=any(bk.code.upper() in chosen for bk in books))[
                summary[
                    span[t(f"worlds.group.{group}")],
                    span(class_="book-count mono")[
                        f"{sum(1 for bk in books if bk.code.upper() in chosen)}/{len(books)}"
                    ],
                ],
                div(class_="book-list")[
                    [
                        div(class_="book-row")[
                            label(class_="book", for_=f"gm5-book-{bk.code}")[
                                input_el(
                                    type="checkbox",
                                    id=f"gm5-book-{bk.code}",
                                    name="sources",
                                    value=bk.code,
                                    checked=bk.code.upper() in chosen,
                                ),
                                span(class_="book-name")[bk.name],
                                span(class_="book-code mono")[bk.code],
                                span(class_="pill accent book-ed")["2024"] if bk.is_2024 else None,
                            ]
                        ]
                        for bk in books
                    ]
                ],
            ]
            for group, books in catalog.grouped()
            if group != "adventure"
        ],
    ]


def _creature(page: Page, match: CreatureMatch) -> Element:
    t = page.t
    name, _, source = match.ref.partition("|")
    what = {
        "book": t("gm5.creature.book", name=name, source=source),
        "variant": t("gm5.creature.variant", name=name, source=source),
        "new": t("gm5.creature.new"),
        "skipped": t("gm5.creature.skipped"),
    }[match.status]
    return li(class_=f"gm5-{match.status}")[
        b[match.name],
        small(class_="muted")[f" ×{match.count}"] if match.count > 1 else None,
        " — ",
        span[what],
    ]


def _group(page: Page, key: str, matches: Sequence[Element], *, open_: bool) -> Node:
    if not matches:
        return None
    return details(class_="gm5-group", open=open_)[
        summary[b[page.t(key)], " ", span(class_="pill")[str(len(matches))]],
        ul(class_="gm5-list")[matches],
    ]


def _plan(page: Page, plan: Plan) -> Element:
    t = page.t
    left = sum(c.characters for c in plan.campaigns)
    by = {s: [_creature(page, c) for c in plan.creatures if c.status == s] for s in
          ("book", "variant", "new", "skipped")}  # fmt: skip
    items = [
        li[
            b[i.name],
            small(class_="muted")[f" ×{i.count}"] if i.count > 1 else None,
            " — ",
            span[
                t("gm5.item.coins")
                if i.coins
                else t(
                    "gm5.item.book", name=i.ref.partition("|")[0], source=i.ref.partition("|")[2]
                )
                if i.ref
                else t("gm5.item.notes")
            ],
        ]
        for i in plan.items
    ]
    return section(class_="panel gm5-plan")[
        h2[
            t("gm5.plan", campaign=plan.campaigns[0].name)
            if len(plan.campaigns) == 1
            else t.plural("gm5.plan_many", len(plan.campaigns))
        ],
        p[
            t(
                "gm5.plan_counts",
                adventures=plan.adventures,
                chapters=plan.chapters,
                encounters=plan.encounters,
                npcs=plan.npcs,
                pictures=plan.pictures,
            )
        ],
        p(class_="muted")[t.plural("gm5.characters_left", left)] if left else None,
        _group(page, "gm5.group.variant", by["variant"], open_=True),
        _group(page, "gm5.group.new", by["new"], open_=True),
        _group(page, "gm5.group.skipped", by["skipped"], open_=True),
        _group(page, "gm5.group.book", by["book"], open_=False),
        _group(page, "gm5.group.items", items, open_=False),
    ]


def choose_page(
    page: Page,
    *,
    token: str,
    backup: Backup,
    catalog: BookCatalog,
    campaigns: Sequence[int],
    sources: Sequence[str],
    name: str,
    plan: Plan | None,
    error: str | None = None,
) -> Element:
    """A campaign and the books, what they would make, and **Import**."""
    t = page.t
    action = f"{GM5_URL}/{token}"
    return app_shell(
        page,
        t("gm5.title"),
        div(class_="tr-page gm5-page")[
            page_head(t("gm.eyebrow"), t("gm5.title"), t("gm5.choose_lede")),
            alert(t(error) if error else None),
            section(class_="panel")[
                form(method="get", action=action, class_="stack-form", id="gm5-form")[
                    div(class_="field gm5-campaigns")[
                        h2(class_="eyebrow")[t("gm5.campaigns")],
                        p(class_="field-hint")[t("gm5.campaigns_hint")],
                        [
                            label(class_="field-check gm5-campaign")[
                                input_el(
                                    type="checkbox",
                                    name="campaigns",
                                    value=str(i),
                                    checked=i in campaigns,
                                ),
                                span[
                                    b[c.name],
                                    " ",
                                    small(class_="muted")[
                                        t.plural("gm5.adventures", len(c.adventures))
                                    ],
                                ],
                            ]
                            for i, c in enumerate(backup.campaigns)
                        ],
                    ],
                    text_field(
                        "name",
                        t("gm5.world_name"),
                        value=name,
                        autocomplete="off",
                        hint=t("gm5.world_name_hint"),
                        required=False,
                    ),
                    _books(page, catalog, {s.upper() for s in sources}),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary" if plan is None else "btn")[
                            icon("search"), span[t("gm5.check")]
                        ]
                    ],
                ]
            ],
            [
                _plan(page, plan),
                form(
                    method="post",
                    action=action,
                    class_="form-actions gm5-run",
                    data_busy=t("gm5.importing"),
                )[
                    [input_el(type="hidden", name="campaigns", value=str(i)) for i in campaigns],
                    input_el(type="hidden", name="name", value=name),
                    [input_el(type="hidden", name="sources", value=s) for s in sources],
                    button(type="submit", class_="btn btn-primary")[
                        icon("download"), span[t("gm5.import")]
                    ],
                ],
            ]
            if plan is not None
            else None,
        ],
    )


__all__ = ["GM5_URL", "choose_page", "upload_page"]
