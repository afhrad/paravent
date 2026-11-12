"""The translation editor (documentation/TRANSLATION.md §7).

* ``GET|POST /gm/translate/ref/{category}/{source}/{name}`` — a reference entry
* ``GET|POST /gm/translate/fluff/{category}/{source}/{name}`` — what the books say about
  an entry, a subclass's with ``/{class}/{class source}`` after it
* ``GET|POST /gm/translate/feature/{category}/{key}``       — a class or subclass feature
* ``GET|POST /gm/translate/book/{book_id}/{index}``        — a rulebook chapter
* ``GET|POST /gm/translate/adventure/{adventure_id}/{section_id}`` — an adventure section
* ``GET|POST /gm/translate/screen/{screen}/{index}``        — a reference screen's chapter
* ``GET|POST /gm/translate/builder/{kind}/{name}``          — a creature builder's text
  (``feature``, ``trait`` or ``action``); ``GET /gm/translations/builder`` lists them
* ``GET /gm/translations`` — what a language covers; ``POST /gm/translations/jobs``
  starts machine drafts in bulk, ``GET /gm/translations/jobs`` reports their progress

``?lang=`` picks the language (not English); the gamemaster's own by default;
``?embed=1`` serves the editor as a popup over another page (the combat tracker). A POST
saves the changed passages as reviewed, ticks drafts off as reviewed, or asks for
machine drafts of what is still untranslated — then comes back to the editor.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect, safe_next
from app.services.corpus_import import CorpusImportError
from app.services.i18n import FALLBACK, Translatable
from app.services.translation_coverage import MoveTaken
from app.services.translation_targets import Target, TargetNotFound
from app.store.registry import StoreRegistry
from app.ui.context import Page
from app.ui.ref_pages import reference_missing_page
from app.ui.tags import localized_context
from app.ui.translate_pages import (
    TRANSLATE_URL,
    TRANSLATIONS_URL,
    editor_fragment,
    editor_page,
    return_url,
    translate_url,
)
from app.ui.translations_pages import (
    BUILDER_URL,
    DRAFTS_URL,
    IMPORT_URL,
    UNFINISHED_URL,
    builder_page,
    drafts_page,
    import_fragment,
    jobs_fragment,
    translations_page,
    unfinished_page,
)

router = APIRouter(tags=["translate"])


def _language(registry: StoreRegistry, page: Page, lang: str) -> str:
    """The language to translate into: the one asked for, the reader's, or the first."""
    others = [x.code for x in registry.ui.languages if x.code != FALLBACK]
    if lang in others:
        return lang
    if page.lang in others:
        return page.lang
    return others[0] if others else ""


def _target(registry: StoreRegistry, kind: str, parts: tuple[str, ...]) -> Target | None:
    targets = registry.translation_targets
    try:
        if kind == "ref":
            return targets.entry(*parts)
        if kind == "feature":
            return targets.feature(*parts)
        if kind == "book":
            return targets.chapter(parts[0], int(parts[1]))
        if kind == "screen":
            return targets.screen(parts[0], int(parts[1]))
        if kind == "builder":
            return targets.builder(*parts)
        if kind == "adventure":
            return targets.section(*parts)
        if kind == "fluff":
            return targets.fluff(*parts)
    except (TargetNotFound, ValueError):
        return None
    return None


def _show(
    page: Page,
    registry: StoreRegistry,
    target: Target,
    lang: str,
    *,
    todo_only: bool,
    back: str,
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    rows = registry.translations.rows(lang, target.category, target.key, target.data, target.fields)
    references = registry.references
    ctx = localized_context(
        page.t, resolver=references.resolver(), source_set=references.source_set(None)
    )
    return html(
        editor_page(
            page,
            target,
            rows,
            lang=lang,
            languages=[x for x in registry.ui.languages if x.code != FALLBACK],
            ctx=ctx,
            todo_only=todo_only,
            machine=registry.machine_translator is not None,
            message=message,
            error=error,
            back=safe_next(back) if back else "",
        )
    )


async def _editor(
    request: Request,
    gm: GamemasterDep,
    page: Page,
    registry: StoreRegistry,
    kind: str,
    parts: tuple[str, ...],
) -> Response:
    target = _target(registry, kind, parts)
    if target is None:
        return html(reference_missing_page(page), status_code=404)
    query = request.query_params
    lang = _language(registry, page, query.get("lang", ""))
    if not lang:
        return html(reference_missing_page(page), status_code=404)
    if query.get("embed") == "1":
        return await _popup(request, gm, page, registry, target, lang)
    if request.method == "GET":
        message = page.t(query["done"]) if query.get("done", "").startswith("translate.") else None
        error = page.t(query["error"]) if query.get("error", "").startswith("translate.") else None
        return _show(
            page,
            registry,
            target,
            lang,
            todo_only=query.get("show") == "todo",
            back=query.get("back", ""),
            message=message,
            error=error,
        )

    form = {k: str(v) for k, v in (await request.form()).items()}
    by = gm.user.username
    store = registry.translations
    texts = {k[2:]: v for k, v in form.items() if k.startswith("p:")}
    confirmed = {k[2:] for k, v in form.items() if k.startswith("c:") and v}
    action = form.get("action", "save")
    done, error = "translate.saved", ""
    store.save(lang, target.category, target.key, target.data, texts, by=by, fields=target.fields)
    if confirmed:
        store.confirm(lang, target.category, target.key, by=by, paths=confirmed)
    if action == "confirm_all":
        store.confirm(lang, target.category, target.key, by=by)
        done = "translate.confirmed"
    elif action == "machine":
        machine = registry.machine_translator
        if machine is None:
            error = "translate.error.no_machine"
        else:
            try:
                count = await machine.draft(lang, target, by=by)
            except Translatable as exc:
                error = exc.key
            else:
                done = "translate.machine_done" if count else "translate.machine_none"
    back = safe_next(form.get("back", ""))
    if action in ("return", "confirm_all"):  # confirming all is the review done: leave
        leave = return_url(target, back)
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": leave})
        return redirect(leave)
    return redirect(
        translate_url(
            kind,
            *target.where,
            lang=lang,
            show=form.get("show", ""),
            back=back,
            **({"error": error} if error else {"done": done}),
        )
    )


async def _popup(
    request: Request,
    gm: GamemasterDep,
    page: Page,
    registry: StoreRegistry,
    target: Target,
    lang: str,
) -> Response:
    """The editor in a popup (``?embed=1``): the entry's name in the glossary too, and
    after a save an ``HX-Trigger`` that closes the popup and reloads the page's entry."""
    named = target.kind == "ref"
    message = error = None
    action = ""
    if request.method == "POST":
        form = await request.form()
        fields = {k: str(v) for k, v in form.items()}
        by = gm.user.username
        store = registry.translations
        texts = {k[2:]: v for k, v in fields.items() if k.startswith("p:")}
        confirmed = {k[2:] for k, v in fields.items() if k.startswith("c:") and v}
        action = fields.get("action", "save")
        store.save(
            lang, target.category, target.key, target.data, texts, by=by, fields=target.fields
        )
        if confirmed:
            store.confirm(lang, target.category, target.key, by=by, paths=confirmed)
        if named and "name" in fields:
            registry.glossary.set(lang, target.category, target.title, fields["name"])
        if action == "confirm_all":
            store.confirm(lang, target.category, target.key, by=by)
            message = page.t("translate.confirmed")
        elif action == "machine":
            machine = registry.machine_translator
            if machine is None:
                error = page.t("translate.error.no_machine")
            else:
                try:
                    count = await machine.draft(lang, target, by=by)
                except Translatable as exc:
                    error = page.t.error(exc)
                else:
                    message = page.t(
                        "translate.machine_done" if count else "translate.machine_none"
                    )
    if action in ("save", "confirm_all"):
        response = Response(status_code=200)
        response.headers["HX-Trigger"] = json.dumps({"paravent-translated": {"close": True}})
        return response
    rows = registry.translations.rows(lang, target.category, target.key, target.data, target.fields)
    references = registry.references
    ctx = localized_context(
        page.t, resolver=references.resolver(), source_set=references.source_set(None)
    )
    language = next((x.name for x in registry.ui.languages if x.code == lang), lang)
    response = html(
        editor_fragment(
            page,
            target,
            rows,
            lang=lang,
            language=language,
            ctx=ctx,
            name=(page.t.glossary_term(lang, target.category, target.title) or "")
            if named
            else None,
            machine=registry.machine_translator is not None,
            message=message,
            error=error,
            back=safe_next(request.query_params.get("back", "")),
        )
    )
    if action:  # drafts or confirmations: the entry beneath shows them already
        response.headers["HX-Trigger"] = json.dumps({"paravent-translated": {"close": False}})
    return response


@router.api_route(f"{TRANSLATE_URL}/ref/{{category}}/{{source}}/{{name}}", methods=["GET", "POST"])
async def translate_entry(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    category: str,
    source: str,
    name: str,
) -> Response:
    """A reference entry's text."""
    return await _editor(request, gm, page, registry, "ref", (category, source, name))


@router.api_route(
    f"{TRANSLATE_URL}/fluff/{{category}}/{{source}}/{{name}}", methods=["GET", "POST"]
)
async def translate_fluff(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    category: str,
    source: str,
    name: str,
) -> Response:
    """What the books say about a creature, as the players read it in Details."""
    return await _editor(request, gm, page, registry, "fluff", (category, source, name))


@router.api_route(
    f"{TRANSLATE_URL}/fluff/{{category}}/{{source}}/{{name}}/{{class_name}}/{{class_source}}",
    methods=["GET", "POST"],
)
async def translate_subclass_fluff(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    category: str,
    source: str,
    name: str,
    class_name: str,
    class_source: str,
) -> Response:
    """What the books say about a subclass, which only its class can tell apart."""
    parts = (category, source, name, class_name, class_source)
    return await _editor(request, gm, page, registry, "fluff", parts)


@router.api_route(f"{TRANSLATE_URL}/feature/{{category}}/{{key}}", methods=["GET", "POST"])
async def translate_feature(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    category: str,
    key: str,
) -> Response:
    """A class feature, subclass feature or subclass, by its composite key."""
    return await _editor(request, gm, page, registry, "feature", (category, key))


@router.api_route(f"{TRANSLATE_URL}/book/{{book_id}}/{{index}}", methods=["GET", "POST"])
async def translate_chapter(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    book_id: str,
    index: str,
) -> Response:
    """A rulebook chapter's text."""
    return await _editor(request, gm, page, registry, "book", (book_id, index))


@router.api_route(
    f"{TRANSLATE_URL}/adventure/{{adventure_id}}/{{section_id}}", methods=["GET", "POST"]
)
async def translate_section(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    adventure_id: str,
    section_id: str,
) -> Response:
    """A published adventure section's text."""
    return await _editor(request, gm, page, registry, "adventure", (adventure_id, section_id))


@router.api_route(f"{TRANSLATE_URL}/screen/{{screen}}/{{index}}", methods=["GET", "POST"])
async def translate_screen(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    screen: str,
    index: str,
) -> Response:
    """A chapter of the DM screen or the quick reference."""
    return await _editor(request, gm, page, registry, "screen", (screen, index))


@router.api_route(f"{TRANSLATE_URL}/builder/{{kind}}/{{name}}", methods=["GET", "POST"])
async def translate_builder(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    kind: str,
    name: str,
) -> Response:
    """One of the creature builder's ready-made texts."""
    return await _editor(request, gm, page, registry, "builder", (kind, name))


@router.get(TRANSLATIONS_URL, response_class=HTMLResponse)
async def overview(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    lang: str = "",
    done: str = "",
    error: str = "",
    taken: str = "",
    entry: str = "",
) -> HTMLResponse:
    """What a language covers, what to translate next, and machine drafts in bulk."""
    chosen = _language(registry, page, lang)
    if not chosen:
        return html(reference_missing_page(page), status_code=404)
    coverage = registry.translation_coverage
    books = coverage.books(chosen)
    return html(
        translations_page(
            page,
            lang=chosen,
            languages=[x for x in registry.ui.languages if x.code != FALLBACK],
            categories=coverage.categories(chosen),
            books=books,
            wanted=coverage.wanted(chosen),
            problems=coverage.problems(chosen),
            lost=coverage.lost(chosen),
            drafts=len(coverage.drafts(chosen)),
            unfinished=coverage.unfinished(chosen),
            taken=taken,
            jobs=registry.translation_jobs.jobs(chosen),
            corpus_import=registry.corpus_imports.latest(chosen),
            machine=registry.translation_jobs.available,
            message=page.t(done) if done.startswith("translations.") else None,
            error=page.t(error, entry=entry) if error.startswith("translations.") else None,
            entry=entry,
        )
    )


@router.get(DRAFTS_URL, response_class=HTMLResponse)
async def drafts(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, lang: str = "", done: str = ""
) -> HTMLResponse:
    """Every record with drafts still to read."""
    chosen = _language(registry, page, lang)
    if not chosen:
        return html(reference_missing_page(page), status_code=404)
    return html(
        drafts_page(
            page,
            lang=chosen,
            languages=[x for x in registry.ui.languages if x.code != FALLBACK],
            drafts=registry.translation_coverage.drafts(chosen),
            message=page.t.plural("translations.drafts_confirmed", int(done))
            if done.isdigit()
            else None,
        )
    )


@router.get(BUILDER_URL, response_class=HTMLResponse)
async def builder_texts(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, lang: str = ""
) -> HTMLResponse:
    """The creature builder's ready-made texts and how far each is translated."""
    chosen = _language(registry, page, lang)
    if not chosen:
        return html(reference_missing_page(page), status_code=404)
    return html(
        builder_page(
            page,
            lang=chosen,
            languages=[x for x in registry.ui.languages if x.code != FALLBACK],
            texts=registry.translation_coverage.builder_texts(chosen),
            machine=registry.translation_jobs.available,
        )
    )


@router.get(UNFINISHED_URL, response_class=HTMLResponse)
async def unfinished(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, lang: str = ""
) -> HTMLResponse:
    """Every record translated in part, with its passages still English."""
    chosen = _language(registry, page, lang)
    if not chosen:
        return html(reference_missing_page(page), status_code=404)
    return html(
        unfinished_page(
            page,
            lang=chosen,
            languages=[x for x in registry.ui.languages if x.code != FALLBACK],
            unfinished=registry.translation_coverage.unfinished(chosen),
            machine=registry.translation_jobs.available,
        )
    )


@router.post(f"{DRAFTS_URL}/confirm")
async def confirm_drafts(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> Response:
    """Confirm every draft of the language at once."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    chosen = _language(registry, page, form.get("lang", ""))
    count = registry.translation_coverage.confirm_drafts(chosen, by=gm.user.username)
    return redirect(f"{DRAFTS_URL}?" + urlencode({"lang": chosen, "done": count}))


@router.post(f"{TRANSLATIONS_URL}/move")
async def move(
    request: Request, _gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> Response:
    """Move a translation whose entry is gone onto the entry it goes by now."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    chosen = _language(registry, page, form.get("lang", ""))
    key = form.get("key", "")
    outcome = {"done": "translations.moved"}
    try:
        registry.translation_coverage.move(
            chosen,
            form.get("category", ""),
            key,
            form.get("to", ""),
            replace=form.get("replace") == "1",
        )
    except MoveTaken as exc:
        outcome = {"error": exc.key, "taken": key, **exc.params}
    except Translatable as exc:
        outcome = {"error": exc.key, **exc.params}
    return redirect(f"{TRANSLATIONS_URL}?" + urlencode({"lang": chosen, **outcome}) + "#lost")


@router.post(IMPORT_URL)
async def start_import(
    request: Request, _gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> Response:
    """Check a translated corpus in a folder on the server, or import it, in the
    background (TRANSLATION.md §7)."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    chosen = _language(registry, page, form.get("lang", ""))
    try:
        registry.corpus_imports.start(
            chosen,
            form.get("folder", ""),
            dry_run=form.get("mode") != "import",
            reviewed=bool(form.get("reviewed")),
            replace=bool(form.get("replace")),
        )
    except CorpusImportError as exc:
        outcome = {"error": exc.key, "entry": str(exc.params.get("entry", ""))}
    else:
        outcome = {"done": "translations.import.started"}
    return redirect(f"{TRANSLATIONS_URL}?" + urlencode({"lang": chosen, **outcome}) + "#tr-import")


@router.get(IMPORT_URL, response_class=HTMLResponse)
async def import_state(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, lang: str = ""
) -> HTMLResponse:
    """The newest import's state, for the panel's polling."""
    chosen = _language(registry, page, lang)
    return html(import_fragment(page, registry.corpus_imports.latest(chosen), chosen))


@router.get(f"{TRANSLATIONS_URL}/jobs", response_class=HTMLResponse)
async def jobs(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, lang: str = ""
) -> HTMLResponse:
    """The machine runs' progress, for the overview's polling."""
    chosen = _language(registry, page, lang)
    return html(jobs_fragment(page, registry.translation_jobs.jobs(chosen), chosen))


@router.post(f"{TRANSLATIONS_URL}/jobs")
async def start_job(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep
) -> Response:
    """Start machine drafts for a scope: the most-linked entries, a book, an adventure, a
    reference screen."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    chosen = _language(registry, page, form.get("lang", ""))
    try:
        job = registry.translation_jobs.start(chosen, form.get("scope", ""), by=gm.user.username)
    except ValueError:
        job = None
    outcome = {"done": "translations.started"} if job else {"error": "translations.nothing"}
    return redirect(f"{TRANSLATIONS_URL}?" + urlencode({"lang": chosen, **outcome}))
