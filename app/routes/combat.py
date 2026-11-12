"""The combat tracker (documentation/COMBAT.md §5). Gamemaster only.

* ``GET  /gm/combat``                         — running and finished combats
* ``POST /gm/encounters/{id}/run``            — start (or rejoin) the encounter's combat
* ``GET  /gm/combat/{id}``                    — the tracker (``?sel=`` selects, ``?q=`` searches)
* ``GET  /gm/combat/{id}/board``              — the board fragment (live updates fetch it)
* ``GET  /gm/combat/{id}/side``               — the selected combatant's statblock
* ``GET  /gm/combat/{id}/events``             — Server-Sent Events: the combat's revision
* ``POST /gm/combat/{id}/{begin|next|previous}``
* ``POST /gm/combat/{id}/c/{cid}/{action}``   — a row's button (CombatService.act)
* ``POST /gm/combat/{id}/{monsters|characters|others}`` — add to the fight
* ``/gm/combat/{id}/end``                     — confirm, then end

Every post answers an htmx request with the board, and anything else with a redirect
back to the tracker — so it all works without JavaScript too.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.models.gamestate import Combat, KnownFact
from app.models.protocol import ProtocolEntry
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import paravent_markdown
from app.routes.rolls import announce_roll
from app.services import scope
from app.services.auth import Identity
from app.services.characters import CharacterNotFound
from app.services.combat import CombatError, CombatView, Row
from app.services.events import revision_stream
from app.services.experience import progress
from app.services.knowledge import Revealable, revealable
from app.services.media import MediaError
from app.services.npc_gen import edition_for
from app.services.recap import recap, session_combats
from app.services.references import ReferenceEntry, ReferenceNotFound
from app.services.sheet import Sheet
from app.services.translation_targets import Target, TargetNotFound
from app.store.registry import StoreRegistry
from app.ui.combat_pages import (
    COMBATS_URL,
    FRIEND,
    PictureOffer,
    TranslateOffer,
    XpRow,
    add_panel,
    board,
    combat_page,
    combat_url,
    combats_page,
    side_panel,
)
from app.ui.context import Page
from app.ui.encounter_pages import ENCOUNTERS_URL, encounter_url
from app.ui.library_links import chapter_url, confirm_page, treasure_url
from app.ui.markdown import Rendered
from app.ui.play_combat_pages import play_image_url, play_picture_url
from app.ui.recap_pages import recap_page
from app.ui.tags import RenderContext, localized_context
from app.ui.translate_pages import translate_url
from app.ui.translated import table_written

router = APIRouter(tags=["combat"])

PICKER_GROUP = "bestiary"


def _by(gm: Identity) -> str:
    return gm.user.display_name or gm.user.username


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _ctx(page: Page, registry: StoreRegistry, combat: Combat) -> RenderContext:
    references = registry.references
    world_id = references.known_world(combat.world_id)
    return localized_context(
        page.t,
        resolver=registry.compendium.resolver(),
        source_set=references.source_set(world_id),
        world_id=world_id,
        units=page.u,
    )


def _entry(registry: StoreRegistry, combat: Combat, row: Row | None) -> ReferenceEntry | None:
    if row is None or row.combatant.ref is None:
        return None
    name, _, source = row.combatant.ref.partition("|")
    try:
        return registry.references.get("monster", source, name, combat.world_id)
    except ReferenceNotFound:
        return None


def _sheet(registry: StoreRegistry, row: Row | None) -> Sheet | None:
    """A selected character's derived sheet, for the side panel."""
    if row is None or row.combatant.character_id is None:
        return None
    try:
        pc = registry.characters.get_any(row.combatant.character_id)
    except CharacterNotFound:
        return None
    return registry.characters.sheet(pc)


def _known(
    registry: StoreRegistry, combat: Combat, row: Row | None
) -> tuple[dict[str, KnownFact] | None, list[Revealable]]:
    """What the players know of a creature's kind, and what its statblock could tell."""
    if row is None or row.combatant.ref is None:
        return None, []
    monster = registry.combats.monster_of(row.combatant)
    data = monster.entry.model_dump(by_alias=True, exclude_none=True) if monster else {}
    return registry.knowledge.of(combat.world_id, row.combatant.ref), revealable(data)


def _translation(
    page: Page, registry: StoreRegistry, row: Row | None, back: str
) -> TranslateOffer | None:
    """For a creature not yet translated into the reader's language — no name in the
    glossary, passages open, or the books' description (the players' Details) open —
    the popup editors' addresses and what is missing. ``back`` is the tracker, where the
    full editor returns to."""
    if row is None or row.combatant.ref is None or page.lang == "en":
        return None
    name, _, source = row.combatant.ref.partition("|")
    if table_written("monster", source):
        return None
    targets = registry.translation_targets
    try:
        target = targets.entry("monster", source, name)
    except (TargetNotFound, ReferenceNotFound):
        return None
    still = _still_open(page, registry, target)
    unnamed = not page.t.glossary_term(page.lang, "monster", target.title)
    try:
        fluff: Target | None = targets.fluff("monster", source, name)
    except TargetNotFound:
        fluff = None
    told = _still_open(page, registry, fluff) if fluff is not None else 0
    if not still and not unnamed and not told:
        return None
    url = translate_url("ref", *target.where, lang=page.lang, embed="1", back=back)
    full = translate_url("ref", *target.where, lang=page.lang, back=back)
    offer = TranslateOffer(url, still, unnamed, full)
    if fluff is not None and told:
        offer = replace(
            offer,
            fluff_url=translate_url("fluff", *fluff.where, lang=page.lang, embed="1", back=back),
            fluff_page_url=translate_url("fluff", *fluff.where, lang=page.lang, back=back),
            fluff_still=told,
        )
    return offer


def _still_open(page: Page, registry: StoreRegistry, target: Target) -> int:
    """Passages of ``target`` untranslated, a draft, or translated from older English."""
    rows = registry.translations.rows(
        page.lang, target.category, target.key, target.data, target.fields
    )
    return sum(1 for r in rows if not r.text or r.draft or r.stale)


def _picture(
    registry: StoreRegistry, combat: Combat, row: Row | None, error: str = ""
) -> PictureOffer | None:
    """A creature's picture as Details shows it: the table's own, else the books';
    ``error`` says why an upload was refused."""
    if row is None or row.combatant.ref is None or row.monster is None:
        return None
    action = f"{COMBATS_URL}/{combat.id}/picture/{row.combatant.id}"
    version = registry.creature_pictures.version(combat.world_id, row.combatant.ref)
    own = (
        f"{play_picture_url(combat.id, row.combatant.id)}?v={version}"
        if version is not None
        else None
    )
    book = registry._sight(row.monster).image
    return PictureOffer(action, own, play_image_url(book) if book else None, error)


def _side(
    page: Page, registry: StoreRegistry, view: CombatView, sel: str, picture_error: str = ""
) -> HTMLResponse:
    row = view.row(sel) if sel else None
    known, offer = _known(registry, view.combat, row)
    return html(
        side_panel(
            page,
            view,
            row,
            _entry(registry, view.combat, row),
            _ctx(page, registry, view.combat),
            _sheet(registry, row),
            known,
            offer,
            _translation(
                page, registry, row, combat_url(view.combat.id, sel=row.combatant.id if row else "")
            ),
            _picture(registry, view.combat, row, picture_error),
        )
    )


def _answer(
    request: Request,
    page: Page,
    registry: StoreRegistry,
    combat_id: str,
    sel: str,
    error: CombatError | None = None,
    q: str = "",
    side: str = "",
) -> Response:
    """The board for htmx, a redirect back to the tracker otherwise."""
    if _is_htmx(request):
        view = registry.combats.view(registry.combats.get(combat_id))
        flash = page.t.error(error) if error is not None else None
        ctx = _ctx(page, registry, view.combat)
        return html(board(page, view, sel=sel, flash=flash, ctx=ctx))
    return redirect(
        combat_url(combat_id, sel=sel, q=q, side=side, error=error.key if error is not None else "")
        + ("#add" if q else "")
    )


def _last_logged(registry: StoreRegistry, combat_id: str) -> datetime | None:
    """The moment before an action: what the fight logs after it, the action rolled."""
    return datetime.now(UTC)


def _sounding(
    request: Request,
    response: Response,
    registry: StoreRegistry,
    combat_id: str,
    since: datetime | None,
) -> Response:
    """Let the browser rattle the dice when the action rolled any (app/routes/rolls.py)."""
    if not _is_htmx(request):
        return response
    try:
        combat = registry.combats.get(combat_id)
    except CombatError:
        return response
    for entry in reversed(registry.protocol_reader.fight(combat_id, combat.world_id)):
        if since is not None and entry.at <= since:
            break
        if entry.roll is not None or entry.damage_rolls or entry.what.startswith("recharge:"):
            # An attack's natural 20 or 1 gets its own sound; other rolls just rattle.
            natural = entry.outcome if entry.outcome in ("crit", "fumble") else ""
            return announce_roll(response, natural)
    return response


def _place(registry: StoreRegistry, combat: Combat) -> tuple[list[str], tuple[str, str]]:
    """Where the combat belongs (for the eyebrow), and where "back" goes."""
    encounter = (
        registry.gamestate.encounters.find(combat.encounter_id) if combat.encounter_id else None
    )
    if encounter is None:
        return [], (COMBATS_URL, "")
    place = scope.label(registry.adventures, encounter.scope) if encounter.scope else []
    if encounter.chapter_id and registry.gamestate.chapters.find(encounter.chapter_id):
        return place, (chapter_url(encounter.chapter_id), place[-1] if place else "")
    return place, (encounter_url(encounter.id), encounter.name)


def _characters_to_add(registry: StoreRegistry, combat: Combat) -> list[tuple[str, str]]:
    present = {c.character_id for c in combat.combatants}
    return sorted(
        (
            (pc.id, pc.name or "?")
            for pc in registry.gamestate.characters.list()
            if pc.id not in present and (combat.world_id is None or pc.world_id == combat.world_id)
        ),
        key=lambda item: item[1].casefold(),
    )


# -- pages ---------------------------------------------------------------------------


@router.get(COMBATS_URL, response_class=HTMLResponse)
async def list_combats(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """Running combats first, then the finished ones."""
    combats = registry.combats.list()
    return html(
        combats_page(page, [c for c in combats if c.running], [c for c in combats if not c.running])
    )


@router.post(f"{ENCOUNTERS_URL}/{{encounter_id}}/run")
async def run_encounter(gm: GamemasterDep, registry: RegistryDep, encounter_id: str) -> Response:
    """Start the encounter's combat, or go to the one already running."""
    encounter = registry.encounters.get(encounter_id)
    running = any(c.running for c in registry.combats.list(encounter_id=encounter.id))
    combat = registry.combats.start(encounter, _by(gm))
    if encounter.sound and not running:
        registry.sound_desk.trigger(encounter.sound)
    return redirect(combat_url(combat.id))


def _notes(page: Page, registry: StoreRegistry, combat: Combat) -> Rendered | None:
    """The notes of the encounter the fight was run from, as Paravent Markdown resolved
    where the encounter is placed; None without an encounter, notes or world."""
    encounter = (
        registry.gamestate.encounters.find(combat.encounter_id) if combat.encounter_id else None
    )
    if encounter is None or not encounter.notes.strip() or encounter.scope is None:
        return None
    return paravent_markdown(page, registry, encounter.scope, encounter.notes)


@router.get(f"{COMBATS_URL}/{{combat_id}}", response_class=HTMLResponse)
async def tracker(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    sel: str = "",
    q: str = "",
    all: str = "",  # noqa: A002 — the query parameter's name
    error: str = "",
    side: str = "",
) -> HTMLResponse:
    """The tracker: board, statblock, and the panel for adding to the fight.

    ``side=friend`` keeps the add panel's choice across a search without JavaScript.
    """
    service = registry.combats
    combat = service.get(combat_id)
    view = service.view(combat)
    row = view.row(sel) if sel else None
    add = None
    if combat.running:
        world_id = registry.references.known_world(combat.world_id)
        result = registry.search.search(
            q[:100],
            registry.references.source_set(world_id),
            group=PICKER_GROUP,
            all_books=all == "1",
            term=page.t.terms(),
        )
        add = add_panel(
            page, view, result, _characters_to_add(registry, combat), ally=side == FRIEND
        )
    return html(
        combat_page(
            page,
            view,
            side=side_panel(
                page,
                view,
                row,
                _entry(registry, combat, row),
                _ctx(page, registry, combat),
                _sheet(registry, row),
                *_known(registry, combat, row),
                _translation(
                    page,
                    registry,
                    row,
                    combat_url(view.combat.id, sel=row.combatant.id if row else ""),
                ),
                _picture(registry, view.combat, row),
            ),
            add=add,
            summary_=None if combat.running else service.summary(combat),
            progress=None if combat.running else _progress(registry, combat),
            ctx=_ctx(page, registry, combat),
            notes=_notes(page, registry, combat),
            sel=row.combatant.id if row else "",
            error=page.t(error) if error.startswith("combat.error.") else None,
        )
    )


@router.get(f"{COMBATS_URL}/{{combat_id}}/board", response_class=HTMLResponse)
async def board_fragment(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str, sel: str = ""
) -> HTMLResponse:
    """The board alone, for live updates."""
    view = registry.combats.view(registry.combats.get(combat_id))
    return html(board(page, view, sel=sel, ctx=_ctx(page, registry, view.combat)))


@router.get(f"{COMBATS_URL}/{{combat_id}}/side", response_class=HTMLResponse)
async def side_fragment(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str, sel: str = ""
) -> HTMLResponse:
    """The selected combatant's statblock, or a character's fighting stats."""
    return _side(page, registry, registry.combats.view(registry.combats.get(combat_id)), sel)


@router.get(f"{COMBATS_URL}/{{combat_id}}/events")
async def combat_events(
    request: Request, _gm: GamemasterDep, registry: RegistryDep, combat_id: str
) -> StreamingResponse:
    """The combat's revision, now and on every change (its characters' included)."""
    combat = registry.combats.get(combat_id)
    return StreamingResponse(
        revision_stream(
            registry.events, combat.id, combat.updated_at.isoformat(), request.is_disconnected
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- the round -----------------------------------------------------------------------


async def _round(
    request: Request,
    gm: Identity,
    page: Page,
    registry: StoreRegistry,
    combat_id: str,
    step: str,
    sel: str,
) -> Response:
    service = registry.combats
    actions = {
        "begin": service.begin,
        "next": service.next_turn,
        "previous": service.previous_turn,
        "lair": service.toggle_lair,
    }
    since = _last_logged(registry, combat_id)
    try:
        actions[step](combat_id, _by(gm))
    except CombatError as exc:
        return _answer(request, page, registry, combat_id, sel, exc)
    answer = _answer(request, page, registry, combat_id, sel)
    return _sounding(request, answer, registry, combat_id, since)


@router.post(f"{COMBATS_URL}/{{combat_id}}/lair")
async def toggle_lair(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    sel: Annotated[str, Form()] = "",
) -> Response:
    """Lair actions on or off: off when the fight is not in the creature's lair."""
    return await _round(request, gm, page, registry, combat_id, "lair", sel)


@router.post(f"{COMBATS_URL}/{{combat_id}}/begin")
async def begin(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    sel: Annotated[str, Form()] = "",
) -> Response:
    """Round 1: the first in the order acts."""
    return await _round(request, gm, page, registry, combat_id, "begin", sel)


@router.post(f"{COMBATS_URL}/{{combat_id}}/next")
async def next_turn(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    sel: Annotated[str, Form()] = "",
) -> Response:
    """The next turn; past the last, a new round."""
    return await _round(request, gm, page, registry, combat_id, "next", sel)


@router.post(f"{COMBATS_URL}/{{combat_id}}/previous")
async def previous_turn(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    sel: Annotated[str, Form()] = "",
) -> Response:
    """Back one turn."""
    return await _round(request, gm, page, registry, combat_id, "previous", sel)


@router.post(f"{COMBATS_URL}/{{combat_id}}/picture/{{combatant_id}}")
async def creature_picture(
    request: Request,
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    combatant_id: str,
) -> Response:
    """Set the world's own picture of a creature's kind, or remove it."""
    form = await request.form()
    combat = registry.combats.get(combat_id)
    view = registry.combats.view(combat)
    row = view.row(combatant_id)
    if row is None or row.combatant.ref is None:
        raise HTTPException(status_code=404)
    pictures = registry.creature_pictures
    error: str | None = None
    if form.get("do") == "remove":
        pictures.remove(combat.world_id, row.combatant.ref)
    else:
        upload = form.get("picture")
        if isinstance(upload, UploadFile) and upload.filename:
            try:
                pictures.set(
                    combat.world_id, row.combatant.ref, upload.filename, await upload.read()
                )
            except MediaError as exc:
                error = page.t.error(exc)
        else:
            error = page.t("combat.picture.none_chosen")
    if not _is_htmx(request):
        return redirect(combat_url(combat_id, sel=combatant_id))
    return _side(page, registry, view, combatant_id, picture_error=error or "")


@router.post(f"{COMBATS_URL}/{{combat_id}}/known/{{combatant_id}}")
async def creature_knowledge(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    combatant_id: str,
) -> Response:
    """Tell the players facts about a creature's kind, or make them forget one."""
    form = await request.form()
    combat = registry.combats.get(combat_id)
    view = registry.combats.view(combat)
    row = view.row(combatant_id)
    if row is None or row.combatant.ref is None:
        raise HTTPException(status_code=404)
    ref = row.combatant.ref
    if form.get("do") == "forget":
        registry.knowledge.forget(combat.world_id, ref, str(form.get("fact", "")))
    else:
        chosen = {str(v) for v in form.getlist("fact")}
        _known_now, offer = _known(registry, combat, row)
        registry.knowledge.reveal(
            combat.world_id,
            ref,
            [r for r in offer if r.id in chosen],
            note=str(form.get("note", "")),
        )
    if not _is_htmx(request):
        return redirect(combat_url(combat_id, sel=combatant_id))
    return _side(page, registry, view, combatant_id)


def _edition(registry: StoreRegistry, combat_id: str) -> str:
    """The rules edition names are rolled for: the fight's world's books."""
    world_id = registry.combats.get(combat_id).world_id
    world = registry.gamestate.worlds.find(world_id) if world_id else None
    return edition_for(world.source_set if world else None, registry.npc_tables)


# Row actions whose effect shows in the selected creature's side panel.
_SIDE_ACTIONS = frozenset({"cast", "restore_spells"})
SIDE_EVENT = "paravent-side"


@router.post(f"{COMBATS_URL}/{{combat_id}}/c/{{combatant_id}}/{{action}}")
async def combatant_action(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    combatant_id: str,
    action: str,
) -> Response:
    """One of a row's buttons, by name."""
    params = {k: str(v) for k, v in (await request.form()).items()}
    sel = params.pop("sel", "")
    if action == "remove" and sel == combatant_id:
        sel = ""
    since = _last_logged(registry, combat_id)
    try:
        if action == "name":
            registry.combats.roll_names(
                combat_id,
                combatant_id,
                registry.naming_for(registry.combats.get(combat_id).world_id),
                _edition(registry, combat_id),
                _by(gm),
                group=params.get("each") == "1",
                style=params.get("style", ""),
                language=page.lang,
            )
        else:
            registry.combats.act(combat_id, combatant_id, action, params, _by(gm))
    except CombatError as exc:
        return _answer(request, page, registry, combat_id, sel, exc)
    answer = _sounding(
        request, _answer(request, page, registry, combat_id, sel), registry, combat_id, since
    )
    if action in _SIDE_ACTIONS and _is_htmx(request):
        # The board is the answer; the side panel shows what the action changed there
        # (the spells left), so the page reloads it too.
        triggers = json.loads(answer.headers.get("HX-Trigger") or "{}")
        answer.headers["HX-Trigger"] = json.dumps({**triggers, SIDE_EVENT: True})
    return answer


async def _add(
    request: Request, gm: Identity, page: Page, registry: StoreRegistry, combat_id: str, what: str
) -> Response:
    """Add monsters (``ref``), a character (``character_id``) or someone ad hoc."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    service = registry.combats
    by = _by(gm)
    q = form.get("q", "")
    sel = form.get("sel", "")
    ally = form.get("side") == FRIEND
    side = FRIEND if ally else ""
    try:
        if what == "monsters":
            service.add_monster(
                combat_id, form.get("ref", ""), _int(form, "count") or 1, by, ally=ally
            )
        elif what == "characters":
            service.add_character(combat_id, form.get("character_id", ""), by)
        else:
            service.add_other(
                combat_id,
                form.get("label", ""),
                by,
                ac=_int(form, "ac"),
                hp=_int(form, "hp") or 0,
                initiative=_int(form, "initiative"),
                ally=ally,
            )
    except CombatError as exc:
        return _answer(request, page, registry, combat_id, sel, exc, q=q, side=side)
    return _answer(request, page, registry, combat_id, sel, q=q, side=side)


@router.post(f"{COMBATS_URL}/{{combat_id}}/monsters")
async def add_monsters(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> Response:
    """Monsters from the picker (``ref=Name|SOURCE``, ``count``), initiative rolled."""
    return await _add(request, gm, page, registry, combat_id, "monsters")


@router.post(f"{COMBATS_URL}/{{combat_id}}/characters")
async def add_character(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> Response:
    """A character from the world."""
    return await _add(request, gm, page, registry, combat_id, "characters")


@router.post(f"{COMBATS_URL}/{{combat_id}}/others")
async def add_other(
    request: Request, gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> Response:
    """Someone without a statblock."""
    return await _add(request, gm, page, registry, combat_id, "others")


def _int(form: dict[str, str], key: str) -> int | None:
    raw = form.get(key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise CombatError("combat.error.bad_input") from None


# -- ending --------------------------------------------------------------------------


@router.get(f"{COMBATS_URL}/{{combat_id}}/end", response_class=HTMLResponse)
async def confirm_end(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> HTMLResponse:
    """Ask once before ending."""
    t = page.t
    combat = registry.combats.get(combat_id)
    return html(
        confirm_page(
            page,
            title=t("combat.end"),
            heading=t("combat.end_heading", name=combat.name),
            lede=t("combat.end_lede"),
            action=f"{COMBATS_URL}/{combat.id}/end",
            back=combat_url(combat.id),
            submit=t("combat.end"),
        )
    )


def _fight_entries(registry: StoreRegistry) -> Callable[[Combat], list[ProtocolEntry]]:
    """A fight's entries in the log, oldest first, for the recap."""
    return lambda combat: registry.protocol_reader.fight(combat.id, combat.world_id)


@router.get(f"{COMBATS_URL}/{{combat_id}}/recap", response_class=HTMLResponse)
async def combat_recap(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> HTMLResponse:
    """One fight's recap, with a link to the whole session's when there were more."""
    combat = registry.combats.get(combat_id)
    day = combat.created_at.astimezone().date().isoformat()
    same_day = session_combats(registry.combats.list(), combat.world_id, day)
    session = (
        f"/gm/recap?{urlencode({'world': combat.world_id or '', 'day': day})}"
        if len(same_day) > 1
        else None
    )
    return html(
        recap_page(
            page,
            combat.name,
            recap([combat], registry.combats, _fight_entries(registry)),
            (combat_url(combat_id), combat.name),
            session,
        )
    )


@router.get("/gm/recap", response_class=HTMLResponse)
async def session_recap(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world: str = "", day: str = ""
) -> HTMLResponse:
    """Every fight of one world on one day, recapped together."""
    fights = session_combats(registry.combats.list(), world or None, day)
    if not fights:
        raise HTTPException(status_code=404)
    title = page.t("recap.session_title", day=day)
    return html(
        recap_page(
            page,
            title,
            recap(fights, registry.combats, _fight_entries(registry)),
            (COMBATS_URL, page.t("combat.list_title")),
        )
    )


@router.get(f"{COMBATS_URL}/{{combat_id}}/delete", response_class=HTMLResponse)
async def confirm_delete(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> HTMLResponse:
    """Ask before deleting a finished fight for good."""
    t = page.t
    combat = registry.combats.get(combat_id)
    return html(
        confirm_page(
            page,
            title=t("combat.delete"),
            heading=t("combat.delete_heading", name=combat.name),
            lede=t("combat.delete_lede"),
            action=f"{COMBATS_URL}/{combat.id}/delete",
            back=combat_url(combat.id),
            submit=t("combat.delete"),
        )
    )


@router.post(f"{COMBATS_URL}/{{combat_id}}/attack")
async def resolve_attack(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
    at: Annotated[str, Form()],
    target: Annotated[str, Form()] = "",
    miss: Annotated[str, Form()] = "",
    crit: Annotated[str, Form()] = "",
    sel: Annotated[str, Form()] = "",
) -> Response:
    """A rolled attack: its damage applied to the target it hit (as a critical, if the
    gamemaster says so), or set aside as a miss."""
    try:
        registry.combats.resolve_attack(
            combat_id, at, target or None, _by(gm), hit=not miss, crit=bool(crit)
        )
    except CombatError as exc:
        return _answer(request, page, registry, combat_id, sel, exc)
    return _answer(request, page, registry, combat_id, sel)


@router.post(f"{COMBATS_URL}/{{combat_id}}/save")
async def resolve_save(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    combat_id: str,
) -> Response:
    """A save effect settled per target: ``v.<combatant id>`` = fail, save or roll."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    sel = form.get("sel", "")
    verdicts = {k.removeprefix("v."): v for k, v in form.items() if k.startswith("v.")}
    since = _last_logged(registry, combat_id)
    try:
        registry.combats.resolve_save(combat_id, form.get("at", ""), verdicts, _by(gm))
    except CombatError as exc:
        return _answer(request, page, registry, combat_id, sel, exc)
    answer = _answer(request, page, registry, combat_id, sel)
    return _sounding(request, answer, registry, combat_id, since)


@router.post(f"{COMBATS_URL}/{{combat_id}}/delete")
async def delete_combat(_gm: GamemasterDep, registry: RegistryDep, combat_id: str) -> Response:
    """Delete a finished fight; a running one must be ended first."""
    combat = registry.combats.get(combat_id)
    if combat.running:
        return redirect(combat_url(combat_id, error="combat.error.still_running"))
    _, back = _place(registry, combat)
    registry.combats.delete(combat_id)
    return redirect(back[0])


def _progress(registry: StoreRegistry, combat: Combat) -> list[XpRow]:
    """Each character who took part, with their experience against the table."""
    rows = []
    for combatant in combat.combatants:
        if not combatant.character_id:
            continue
        try:
            pc = registry.characters.get_any(combatant.character_id)
        except LookupError:
            continue
        rows.append(XpRow(pc.id, pc.name or "?", progress(pc)))
    return rows


@router.post(f"{COMBATS_URL}/{{combat_id}}/treasure")
async def combat_treasure(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, combat_id: str
) -> Response:
    """A treasure for the fight, opened ready to roll a hoard at its highest CR."""
    combat = registry.combats.get(combat_id)
    if not combat.world_id:
        return redirect(combat_url(combat_id, error="combat.error.treasure_world"))
    crs = [
        monster.cr.value
        for c in combat.combatants
        if not c.is_character
        and (monster := registry.combats.monster_of(c)) is not None
        and monster.cr is not None
    ]
    treasure = registry.treasure.create(
        page.t("combat.treasure_name", name=combat.name), f"w:{combat.world_id}", combat.world_id
    )
    cr = int(max(crs, default=0))
    return redirect(f"{treasure_url(treasure.id)}?{urlencode({'kind': 'hoard', 'cr': cr})}#roll-h")


@router.post(f"{COMBATS_URL}/{{combat_id}}/award")
async def award_xp(
    gm: GamemasterDep,
    registry: RegistryDep,
    combat_id: str,
    xp: Annotated[int, Form(ge=0, le=1_000_000)] = 0,
) -> Response:
    """Give every character who took part ``xp`` each — once (COMBAT.md §6)."""
    try:
        registry.combats.award(combat_id, xp, _by(gm))
    except CombatError as exc:
        return redirect(combat_url(combat_id, error=exc.key))
    return redirect(combat_url(combat_id))


@router.post(f"{COMBATS_URL}/{{combat_id}}/end")
async def end_combat(gm: GamemasterDep, registry: RegistryDep, combat_id: str) -> Response:
    """End the combat; the tracker then shows the summary."""
    # Ending twice is harmless: the summary is what the gamemaster wants to see anyway.
    with contextlib.suppress(CombatError):
        registry.combats.end(combat_id, _by(gm))
    return redirect(combat_url(combat_id))
