"""The term glossary (documentation/TRANSLATION.md §4).

* ``GET  /gm/glossary``   — the names given, and the most-linked entries still unnamed
* ``GET|POST /gm/glossary/open`` — every name still ``"-"``, a page at a time, saved
  together
* ``POST /gm/glossary/open/suggest`` — ask the Claude API for names for those in view;
  ``GET /gm/glossary/open/progress`` reports how the run goes
* ``POST /glossary/term`` — name one entry in one language (empty makes it ``"-"``)

Only the gamemaster names entries: the glossary is the table's, like its worlds.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect, safe_next
from app.services.glossary import is_name
from app.services.i18n import FALLBACK
from app.services.player_view import readable_names
from app.ui.glossary_pages import (
    GLOSSARY_URL,
    OPEN_URL,
    glossary_page,
    in_view,
    open_page,
    open_url,
    suggest_status,
)

router = APIRouter(tags=["glossary"])


def _language(registry: RegistryDep, lang: str) -> str | None:
    """A language the glossary may hold: supported, and not English."""
    return lang if lang != FALLBACK and registry.ui.supports(lang) else None


@router.get(GLOSSARY_URL, response_class=HTMLResponse)
async def show(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    lang: str = "",
    category: str = "",
) -> HTMLResponse:
    """What is named, and what to name next."""
    chosen = _chosen(registry, page, lang)
    named = registry.ui.translator(chosen, registry.glossary).term if chosen else None
    wanted = registry.link_counts.wanted(named, category=category, limit=60) if named else []
    terms = registry.glossary.terms(chosen) if chosen else []
    if category:
        terms = [term for term in terms if term.category == category]
    still = sum(len(n) for n in registry.glossary.still_open(chosen).values()) if chosen else 0
    return html(glossary_page(page, chosen, category, terms, wanted, still))


def _still_open(registry: RegistryDep, lang: str, players: bool) -> dict[str, list[str]]:
    """The open names of a language; with ``players``, those a player of any world reads
    (documentation/PLAYER-VIEW.md)."""
    still = registry.glossary.still_open(lang)
    reader = registry.player_reader(None, as_player=True) if players else None
    if reader is None:
        return still
    readable = readable_names(reader, registry.compendium)
    kept = {
        category: [n for n in names if n in readable.get(category, ())]
        for category, names in still.items()
    }
    return {category: names for category, names in kept.items() if names}


def _chosen(registry: RegistryDep, page: PageDep, lang: str) -> str:
    """The language asked for, else the reader's, else the first besides English."""
    others = [x.code for x in registry.ui.languages if x.code != FALLBACK]
    return _language(registry, lang) or (
        page.lang if page.lang != FALLBACK else (others[0] if others else "")
    )


@router.get(OPEN_URL, response_class=HTMLResponse)
async def still_open(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    lang: str = "",
    category: str = "",
    q: str = "",
    page_number: Annotated[int, Query(alias="page")] = 1,
    saved: int | None = None,
    players: bool = False,
) -> HTMLResponse:
    """The names the glossary file still marks ``"-"``, to type in by hand; with
    ``players``, only those players meet."""
    chosen = _chosen(registry, page, lang)
    still = _still_open(registry, chosen, players) if chosen else {}
    suggestions = registry.name_suggestions
    return html(
        open_page(
            page,
            chosen,
            category,
            q,
            page_number,
            still,
            saved,
            suggestions=suggestions.all(chosen) if chosen else {},
            available=suggestions.available,
            run=suggestions.progress(chosen) if chosen else None,
            players=players,
        )
    )


@router.post(f"{OPEN_URL}/suggest")
async def ask_for_suggestions(
    _gm: GamemasterDep,
    registry: RegistryDep,
    lang: Annotated[str, Form()],
    category: Annotated[str, Form()] = "",
    q: Annotated[str, Form()] = "",
    players: Annotated[bool, Form()] = False,
) -> Response:
    """Ask the Claude API for names for every open entry in view that has no suggestion
    yet, in the background; the page shows how it goes."""
    code = _language(registry, lang)
    if code is not None:
        view = in_view(_still_open(registry, code, players), category, q)
        registry.name_suggestions.start(code, view)
    return redirect(open_url(lang, category, q, players=players))


@router.get(f"{OPEN_URL}/progress", response_class=HTMLResponse)
async def suggestion_progress(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    lang: str = "",
    category: str = "",
    q: str = "",
    players: bool = False,
) -> HTMLResponse:
    """The run's status line alone, for the page's polling."""
    code = _language(registry, lang) or ""
    run = registry.name_suggestions.progress(code) if code else None
    return html(suggest_status(page, code, category, q, run, players=players))


@router.post(OPEN_URL)
async def give_names(_gm: GamemasterDep, registry: RegistryDep, request: Request) -> Response:
    """Save the names typed into the open list; empty fields stay ``"-"``. "take" saves
    one entry's suggestion, "take_all" every suggestion of the page whose field is
    empty (a typed name wins). Back to the same page, where the names given are gone
    and the next ones show."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    code = _language(registry, form.get("lang", ""))
    saved = 0
    if code is not None:
        categories = set(registry.compendium.categories())
        suggestions = registry.name_suggestions
        take_all = form.get("action") == "take_all"
        take = form.get("take", "")
        given: dict[str, dict[str, str]] = {}
        for key, value in form.items():
            category, sep, english = key.removeprefix("n:").partition(":")
            if not key.startswith("n:") or not sep or category not in categories:
                continue
            offered = suggestions.of(code, category, english)
            wanted = take == f"{category}:{english}" or (take_all and not is_name(value.strip()))
            if offered and wanted:
                value = offered
            if is_name(value.strip()) and english.strip():
                given.setdefault(category, {})[english] = value
        for category, names in given.items():
            registry.glossary.set_many(code, category, names)
            suggestions.drop(code, category, list(names))
            saved += len(names)
    try:
        number = int(form.get("page", "1"))
    except ValueError:
        number = 1
    return redirect(
        open_url(
            form.get("lang", ""),
            form.get("category", ""),
            form.get("q", ""),
            number,
            players=form.get("players") == "1",
            **({"saved": str(saved)} if saved else {}),
        )
    )


@router.post("/glossary/term")
async def set_term(
    _gm: GamemasterDep,
    registry: RegistryDep,
    lang: Annotated[str, Form()],
    category: Annotated[str, Form()],
    english: Annotated[str, Form(max_length=200)],
    value: Annotated[str, Form(max_length=200)] = "",
    next: Annotated[str, Form()] = "",  # noqa: A002 — where to return
) -> Response:
    """Name an entry in a language; an empty name removes it."""
    code = _language(registry, lang)
    if code is not None and category in registry.compendium.categories() and english.strip():
        registry.glossary.set(code, category, english.strip(), value)
    return redirect(safe_next(next) or f"{GLOSSARY_URL}?lang={lang}")


__all__ = ["router"]
