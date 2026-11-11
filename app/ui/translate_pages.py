"""The translation editor (documentation/TRANSLATION.md §7).

English on the left, the language on the right, one passage per row: the passage's
tags sit under its field as chips that insert themselves (``translate.js``), so nobody
types ``{@...}`` by hand and a tag is never made up from the translated words (§3). A
draft carries a "reviewed" tick; a passage whose English changed since says so; one
that lost a tag of the English shows which.

Everything posts as one plain form, which works without JavaScript.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    a,
    button,
    div,
    form,
    h2,
    label,
    li,
    nav,
    p,
    span,
    textarea,
    ul,
)
from htpy import input as input_el

from app.services.i18n import Language
from app.services.translation_targets import Target
from app.services.translations import PassageRow
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.tags import RenderContext, ref_url, render

TRANSLATE_URL = "/gm/translate"
TRANSLATIONS_URL = "/gm/translations"
# Saving and switching view swap the page, so working on a record adds nothing to the
# browser's history and one Back leaves the editor.
IN_PLACE = {"hx_target": "closest .tr-page", "hx_select": ".tr-page", "hx_swap": "outerHTML"}


def translate_url(kind: str, *where: str, lang: str = "", **query: str) -> str:
    """The editor of one record: ``ref/<category>/<source>/<name>``, ``book/<id>/<n>``,
    ``screen/<dm|quick>/<n>`` or ``adventure/<id>/<section>``."""
    path = "/".join(quote(part, safe="") for part in (kind, *where))
    params = {k: v for k, v in {"lang": lang, **query}.items() if v}
    return f"{TRANSLATE_URL}/{path}" + (f"?{urlencode(params)}" if params else "")


def return_url(target: Target, back: str = "") -> str:
    """Where the editor leaves for: the page it was opened from, else where the record is
    read, else the overview."""
    return back or source_url(target) or TRANSLATIONS_URL


def source_url(target: Target) -> str:
    """The page the record is read on."""
    if target.kind in ("ref", "fluff") and target.where[0] != "subclass":
        category, source, name = target.where
        return ref_url(category, name, source)
    if target.kind == "book":
        return f"/books/{target.where[0]}/{target.where[1]}"
    if target.kind == "screen":
        return f"/screen?ref={target.where[0]}"
    return ""


def _counts(page: Page, rows: Sequence[PassageRow]) -> str:
    t = page.t
    done = sum(1 for r in rows if r.text)
    drafts = sum(1 for r in rows if r.draft)
    stale = sum(1 for r in rows if r.stale)
    parts = [t("translate.count", done=done, total=len(rows))]
    if drafts:
        parts.append(t.plural("translate.count_drafts", drafts))
    if stale:
        parts.append(t.plural("translate.count_stale", stale))
    return " · ".join(parts)


def _row(page: Page, row: PassageRow, number: int, ctx: RenderContext) -> Element:
    t = page.t
    field_id = f"tr-{number}"
    badges: list[Node] = []
    if row.draft:
        badges.append(span(class_="pill cond")[t("translate.draft")])
    if row.stale:
        badges.append(
            span(class_="pill dmg", title=t("translate.stale_hint"))[t("translate.stale")]
        )
    if row.missing:
        badges.append(
            span(class_="pill dmg tr-missing", title=" ".join(row.missing))[
                t.plural("translate.missing_tags", len(row.missing))
            ]
        )
    tags = row.tags
    state = "todo" if not row.text or row.draft or row.stale or row.missing else "done"
    return div(class_=f"tr-row tr-{state}", id=f"p-{number}")[
        div(class_="tr-en")[
            span(class_="tr-path mono")[row.path],
            div(class_="tr-en-text")[render(row.english, ctx)],
        ],
        div(class_="tr-de")[
            textarea(
                id=field_id,
                name=f"p:{row.path}",
                rows=str(min(14, max(2, len(row.english) // 70 + 1))),
                placeholder=row.english,
                aria_label=t("translate.field", path=row.path),
                data_original=row.text,
            )[row.text],
            div(class_="tr-tools")[
                badges,
                [
                    button(
                        type="button",
                        class_="tr-chip mono",
                        data_insert=tag,
                        data_for=field_id,
                        title=t("translate.insert_tag"),
                    )[tag]
                    for tag in tags
                ]
                if tags
                else None,
                label(class_="tr-confirm")[
                    input_el(type="checkbox", name=f"c:{row.path}", value="1"),
                    span[t("translate.reviewed")],
                ]
                if row.draft
                else None,
            ],
        ],
    ]


def editor_page(
    page: Page,
    target: Target,
    rows: Sequence[PassageRow],
    *,
    lang: str,
    languages: Sequence[Language],
    ctx: RenderContext,
    todo_only: bool = False,
    machine: bool = False,
    message: str | None = None,
    error: str | None = None,
    back: str = "",
) -> Element:
    """Every passage of one record, English beside the language's."""
    t = page.t
    shown = [r for r in rows if not todo_only or not r.text or r.draft or r.stale or r.missing]
    language = next((x.name for x in languages if x.code == lang), lang)
    leave = return_url(target, back)
    here = translate_url(target.kind, *target.where, lang=lang, back=leave)
    todo = translate_url(target.kind, *target.where, lang=lang, show="todo", back=leave)
    return app_shell(
        page,
        t("translate.title", name=target.title),
        div(class_="tr-page")[
            a(href=leave, class_="backlink")[icon("arrow-left"), span[target.title]],
            page_head(t("translate.eyebrow", language=language), target.title, _counts(page, rows)),
            nav(class_="tr-bar", aria_label=t("translate.languages"))[
                ul(class_="tr-langs")[
                    [
                        li[
                            a(
                                href=(
                                    url := translate_url(
                                        target.kind, *target.where, lang=x.code, back=leave
                                    )
                                ),
                                hx_get=url,
                                aria_current="page" if x.code == lang else None,
                                **IN_PLACE,
                            )[x.name]
                        ]
                        for x in languages
                    ]
                ]
                if len(languages) > 1
                else None,
                div(class_="tr-filter")[
                    a(
                        href=here,
                        hx_get=here,
                        aria_current=None if todo_only else "page",
                        **IN_PLACE,
                    )[t("translate.all")],
                    a(
                        href=todo,
                        hx_get=todo,
                        aria_current="page" if todo_only else None,
                        **IN_PLACE,
                    )[t("translate.todo")],
                ],
            ],
            form(method="post", action=here, hx_post=here, class_="tr-form", **IN_PLACE)[
                hidden("lang", lang),
                hidden("back", leave),
                hidden("show", "todo") if todo_only else None,
                [_row(page, row, rows.index(row), ctx) for row in shown]
                if shown
                else p(class_="tr-empty")[t("translate.nothing_open")],
                div(class_="tr-actions")[
                    # By the buttons: after a save the page stays where it was, on a long
                    # chapter far from the top.
                    div(class_="tr-status")[alert(error), notice(message)]
                    if error or message
                    else None,
                    button(type="submit", name="action", value="return", class_="btn btn-primary")[
                        icon("check"), span[t("translate.save_return")]
                    ],
                    button(type="submit", name="action", value="save", class_="btn")[
                        t("translate.save")
                    ],
                    button(type="submit", name="action", value="confirm_all", class_="btn")[
                        t("translate.confirm_return")
                    ]
                    if any(r.draft for r in rows)
                    else None,
                    button(
                        type="submit",
                        name="action",
                        value="machine",
                        class_="btn",
                        title=t("translate.machine_hint"),
                    )[icon("sparkles"), span[t("translate.machine")]]
                    if machine and any(not r.text for r in rows)
                    else None,
                ],
            ],
        ],
        extra_scripts=("/static/js/htmx.min.js", "/static/js/translate.js", "/static/js/roll.js"),
    )


def editor_fragment(
    page: Page,
    target: Target,
    rows: Sequence[PassageRow],
    *,
    lang: str,
    language: str,
    ctx: RenderContext,
    name: str | None = None,
    machine: bool = False,
    message: str | None = None,
    error: str | None = None,
    back: str = "",
) -> Element:
    """The editor in a popup over another page (the combat tracker): the entry's name
    in the language (the glossary, ``name`` the one given so far; None for a record the
    glossary does not name), every passage, and the machine's drafts. It posts to
    itself; saving closes the popup and the page beneath reloads the entry. ``back`` is
    that page, where the full editor returns to."""
    t = page.t
    url = translate_url(target.kind, *target.where, lang=lang, embed="1", back=back)
    full = translate_url(target.kind, *target.where, lang=lang, back=back)
    return div(class_="tr-popup")[
        div(class_="tr-popup-head")[
            div[
                span(class_="eyebrow")[t("translate.eyebrow", language=language)],
                h2[target.title],
                p(class_="muted")[_counts(page, rows)],
            ],
            button(
                type="button",
                class_="btn btn-sm btn-ghost",
                data_dialog_close=True,
                aria_label=t("translate.close"),
                title=t("translate.close"),
            )[icon("x")],
        ],
        alert(error),
        notice(message),
        form(
            method="post",
            action=url,
            hx_post=url,
            hx_target="#tr-dialog-body",
            hx_swap="innerHTML",
            hx_disabled_elt="find .tr-actions button",
            class_="tr-form",
        )[
            hidden("lang", lang),
            label(class_="tr-name")[
                span[t("translate.name")],
                input_el(
                    type="text", name="name", value=name, placeholder=target.title, maxlength="120"
                ),
            ]
            if name is not None
            else None,
            [_row(page, row, number, ctx) for number, row in enumerate(rows)],
            div(class_="tr-actions")[
                button(type="submit", name="action", value="save", class_="btn btn-primary")[
                    icon("check"), span[t("translate.save")]
                ],
                button(
                    type="submit",
                    name="action",
                    value="machine",
                    class_="btn",
                    title=t("translate.machine_hint"),
                )[icon("sparkles"), span[t("translate.machine")]]
                if machine and any(not r.text for r in rows)
                else None,
                button(type="submit", name="action", value="confirm_all", class_="btn")[
                    t("translate.confirm_all")
                ]
                if any(r.draft for r in rows)
                else None,
                a(href=full, class_="btn btn-ghost")[t("translate.full_editor")],
                span(class_="tr-busy muted")[t("translate.working")],
            ],
        ],
    ]


__all__ = [
    "TRANSLATE_URL",
    "TRANSLATIONS_URL",
    "editor_fragment",
    "editor_page",
    "return_url",
    "source_url",
    "translate_url",
]
