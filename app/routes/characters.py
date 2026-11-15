"""The character sheet: the page, its live updates, and every action on it.

* ``GET  /characters/{id}``              — the full page
* ``GET  /characters/{id}/sheet``        — just ``#sheet``, which live updates fetch
* ``GET  /characters/{id}/events``       — Server-Sent Events: the current revision
* ``POST /characters/{id}/view``         — keep the tab, phone view and spell filter on
  screen, so the sheet opens on them next time (app/services/places.py)
* ``POST /characters/{id}/act/{action}`` — one action; returns ``#sheet`` to HTMX, or
  redirects back to the page without JavaScript (post/redirect/get)
* ``GET/POST /characters/{id}/delete``   — ask, then remove the file: a player their own
  characters, the gamemaster any

Plus the gamemaster's list at ``/gm/characters``: open any character, create a blank
one, or hand one to a player.
"""

from __future__ import annotations

from datetime import UTC, datetime
from fractions import Fraction
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from app.dependencies import (
    CharactersDep,
    GamemasterDep,
    IdentityDep,
    PageDep,
    PlayersDep,
    RegistryDep,
    WorldsDep,
)
from app.models.gamestate import Combat
from app.models.pc import PlayerCharacter
from app.routes.auth import html, redirect
from app.routes.play_combat import strip
from app.routes.rolls import announce_roll, new_roll
from app.services import polymorph
from app.services.combat import CombatError
from app.services.events import revision_stream
from app.services.play import Actor, PlayError
from app.services.sheet import Sheet
from app.ui.character_list_pages import character_delete_page, characters_page
from app.ui.context import Page
from app.ui.sheet_pages import sheet_fragment, sheet_page

router = APIRouter(tags=["characters"])

# Values a sheet URL may carry to restore what was on screen.
_VIEW_KEYS = ("tab", "view")


def _sheet(characters: CharactersDep, registry: RegistryDep, character: PlayerCharacter) -> Sheet:
    """The derived sheet, told whether the character is in a running fight."""
    sheet = characters.sheet(character)
    fights = registry.combats.joined_by([character.id])
    sheet.in_fight = bool(fights)
    sheet.allies = characters.allies(character)
    held = character.state.concentration
    if held is not None and polymorph.is_polymorph(held.name):
        _shape_choices(sheet, characters, registry, character, held.reference, fights)
    for spell in sheet.spells:
        summoned = registry.summoning_for(spell.reference)
        if summoned is not None:
            sheet.summons[spell.reference.lower()] = summoned
    return sheet


def _shape_choices(
    sheet: Sheet,
    characters: CharactersDep,
    registry: RegistryDep,
    character: PlayerCharacter,
    spell: str,
    fights: list[Combat],
) -> None:
    """Whom the caster's Polymorph may reach — the characters of its world, the
    creatures of its fight — and the shapes for the strongest of them."""
    cr = Fraction(max(1, character.total_level))
    if polymorph.self_only(spell):  # Shapechange: the caster alone
        sheet.shape_targets = [(f"character:{character.id}", character.name or "?")]
        sheet.shapes = polymorph.shapes(
            registry.compendium, characters.source_set(character), spell, cr
        )
        return
    targets = [(f"character:{cid}", name) for cid, name in sheet.allies]
    for pc in (registry.gamestate.characters.find(cid) for cid, _ in sheet.allies):
        if pc is not None:
            cr = max(cr, Fraction(max(1, pc.total_level)))
    if fights:
        combat = fights[0]
        for member in combat.combatants:
            if member.character_id is None and not member.hidden:
                targets.append((f"combatant:{member.id}", member.label))
                monster = registry.combats.monster_of(member, own=True)
                if monster is not None and monster.cr is not None:
                    cr = max(cr, monster.cr.value)
    sheet.shape_targets = targets
    sheet.shapes = polymorph.shapes(
        registry.compendium, characters.source_set(character), spell, cr
    )


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/characters/{character_id}", response_class=HTMLResponse)
async def character_page(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    tab: str | None = None,
    view: str | None = None,
    flash: str = "",
) -> HTMLResponse:
    """The sheet as a full page, on the view the user left it on unless the address
    names one."""
    character = characters.get_for(identity, character_id)
    shown = registry.places.sheet_view(identity.user.id, character_id)
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    return html(
        sheet_page(
            page,
            _sheet(characters, registry, character),
            tab=shown.tab if tab is None else tab,
            view=(shown.view or "play") if view is None else view,
            spell_filter=shown.filter,
            flash=page.t(flash) if flash.startswith("play.error.") else None,
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
            combat_strip=strip(page, registry, character_id),
        )
    )


@router.get("/characters/{character_id}/sheet", response_class=HTMLResponse)
async def character_fragment(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    tab: str = "",
    view: str = "play",
) -> HTMLResponse:
    """Just ``#sheet`` — what a live update swaps in."""
    character = characters.get_for(identity, character_id)
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    return html(
        sheet_fragment(
            page,
            _sheet(characters, registry, character),
            tab=tab,
            view=view,
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
        )
    )


# Longer than any tab, view or spell level, shorter than anything worth storing.
_VIEW_VALUE_MAX = 24


@router.post("/characters/{character_id}/view", status_code=204)
async def character_view(
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    tab: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
    filter: Annotated[str, Form()] = "",
) -> Response:
    """Keep how the sheet is shown for this user; sent by sheet.js as it changes."""
    characters.get_for(identity, character_id)
    registry.places.set_sheet_view(
        identity.user.id,
        character_id,
        tab[:_VIEW_VALUE_MAX],
        view[:_VIEW_VALUE_MAX],
        filter[:_VIEW_VALUE_MAX],
    )
    return Response(status_code=204)


@router.get("/characters/{character_id}/events")
async def character_events(
    request: Request,
    identity: IdentityDep,
    registry: RegistryDep,
    characters: CharactersDep,
    character_id: str,
) -> StreamingResponse:
    """Stream the character's revision; the browser re-fetches the sheet when it moves."""
    characters.get_for(identity, character_id)
    current = registry.gamestate.characters.revision(character_id)
    return StreamingResponse(
        revision_stream(registry.events, character_id, current, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _back(page: Page, character_id: str, state: dict[str, str], error_key: str = "") -> str:
    query = {k: state[k] for k in _VIEW_KEYS if state.get(k)}
    if error_key:
        query["flash"] = error_key
    suffix = f"?{urlencode(query)}" if query else ""
    return f"/characters/{character_id}{suffix}"


@router.post("/characters/{character_id}/act/{action}", response_class=HTMLResponse)
async def character_action(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    action: str,
) -> Response:
    """Apply one action and return the updated sheet."""
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    # The tab and view on screen ride along, so the response shows the same place.
    state = {k: request.query_params.get(k, "") or params.get(k, "") for k in _VIEW_KEYS}
    flash: str | None = None
    error_key = ""
    started = datetime.now(UTC)  # what the action logs after this, it rolled
    try:
        characters.act(identity, character_id, action, params)
        if action == "roll" and params.get("kind") == "attack":
            # In a running fight the attack also waits for the gamemaster's hit or miss.
            who = identity.user.display_name or identity.user.username
            registry.combats.player_attack(character_id, params.get("subject", ""), who)
    except PlayError as exc:
        flash = page.t.error(exc)
        error_key = exc.key
    if not _is_htmx(request):
        return redirect(_back(page, character_id, state, error_key))
    character = characters.get_for(identity, character_id)
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    response = html(
        sheet_fragment(
            page,
            _sheet(characters, registry, character),
            tab=state["tab"],
            view=state["view"] or "play",
            flash=flash,
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
        )
    )
    rolled = new_roll(characters.rolled_since(character, started))
    return announce_roll(response, rolled.outcome) if rolled else response


def _list_for(identity: IdentityDep) -> str:
    return "/gm/characters" if identity.is_gamemaster else "/play"


@router.get("/characters/{character_id}/delete", response_class=HTMLResponse)
async def confirm_delete_character(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> HTMLResponse:
    """Ask first, and say if the character is in a running fight."""
    character = characters.get_for(identity, character_id)
    in_fight = bool(registry.combats.joined_by([character_id]))
    return html(character_delete_page(page, character, _list_for(identity), in_fight))


@router.post("/characters/{character_id}/delete")
async def delete_character(
    identity: IdentityDep, characters: CharactersDep, character_id: str
) -> Response:
    """Remove the character's file and go back to the list it was chosen from."""
    characters.delete_for(identity, character_id)
    return redirect(_list_for(identity))


def _slot_level(level: str) -> int:
    """The slot level a form sent; 0 (the spell's own level) for a free casting."""
    return int(level) if level.isdigit() else 0


@router.post("/characters/{character_id}/fight-spell", response_class=HTMLResponse)
async def fight_spell(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    spell: Annotated[str, Form()],
    level: Annotated[str, Form()] = "0",
) -> Response:
    """Cast a spell that calls for saves — its slot spent, or its free use — and send it
    to the character's running fight, its damage at that slot."""
    character = characters.get_for(identity, character_id)
    flash: str | None = None
    try:
        who = identity.user.display_name or identity.user.username
        characters.act(identity, character_id, "cast", {"spell": spell, "level": level})
        registry.combats.player_spell(character_id, spell, who, _slot_level(level))
    except (CombatError, PlayError) as exc:
        flash = page.t.error(exc)
    character = characters.get_for(identity, character_id)
    if not _is_htmx(request):
        return redirect(f"/characters/{character_id}")
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    response = html(
        sheet_fragment(
            page,
            _sheet(characters, registry, character),
            flash=flash or page.t("sheet.spell_sent"),
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
        )
    )
    return response if flash else announce_roll(response)


@router.post("/characters/{character_id}/heal", response_class=HTMLResponse)
async def heal_spell(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    spell: Annotated[str, Form()],
    target: Annotated[list[str], Form()],
    level: Annotated[str, Form()] = "0",
) -> Response:
    """Cast a healing spell on one or more characters of the caster's world."""
    character = characters.get_for(identity, character_id)
    try:
        healed = characters.heal_with_spell(
            identity, character_id, spell, _slot_level(level), target, free=level == "free"
        )
        known = dict(characters.allies(character))
        names = [known.get(t, "?") for t in dict.fromkeys(target)]
        key = "sheet.healed" if len(names) == 1 else "sheet.healed_many"
        flash = page.t(key, name=", ".join(names), amount=healed)
        error = False
    except PlayError as exc:
        flash, error = page.t.error(exc), True
    if not _is_htmx(request):
        return redirect(f"/characters/{character_id}")
    character = characters.get_for(identity, character_id)
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    response = html(
        sheet_fragment(
            page,
            _sheet(characters, registry, character),
            flash=flash,
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
        )
    )
    return response if error else announce_roll(response)


@router.post("/characters/{character_id}/summon", response_class=HTMLResponse)
async def summon_creature(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    spell: Annotated[str, Form()],
    level: Annotated[str, Form()] = "0",
    variant: Annotated[str, Form()] = "",
) -> Response:
    """Cast a summoning spell in a fight: the slot is spent, and the creature joins the
    caster's fight beside it, scaled to the slot's level."""
    character = characters.get_for(identity, character_id)
    sheet = characters.sheet(character)
    view = sheet.spell(spell)
    summoned = registry.summoning_for(view.reference) if view is not None else None
    fights = registry.combats.joined_by([character_id])
    who = identity.user.username
    flash: str | None = None
    try:
        if view is None or summoned is None or not fights:
            raise PlayError("play.error.bad_input")
        cast_level = max(_slot_level(level) or view.level, view.level)
        characters.act(identity, character_id, "cast", {"spell": view.reference, "level": level})
        combat = fights[0]
        row = next(c for c in combat.combatants if c.character_id == character_id)
        attack = sheet.attack_for(view)
        dc = sheet.save_dc_for(view)
        registry.combats.summon(
            combat.id,
            row.id,
            summoned.reference,
            view.reference,
            cast_level,
            who,
            variant=variant,
            attack=attack.value if attack is not None else None,
            dc=dc.value if dc is not None else None,
        )
        flash = page.t("sheet.summoned", name=summoned.name)
    except (PlayError, CombatError) as exc:
        flash = page.t.error(exc)
    if not _is_htmx(request):
        return redirect(f"/characters/{character_id}")
    character = characters.get_for(identity, character_id)
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    return html(
        sheet_fragment(
            page,
            _sheet(characters, registry, character),
            flash=flash,
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
        )
    )


@router.post("/characters/{character_id}/polymorph", response_class=HTMLResponse)
async def polymorph_target(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    target: Annotated[str, Form()],
    shape: Annotated[str, Form()],
) -> Response:
    """The caster's Polymorph takes hold: the target (a character of the world, or a
    creature of the caster's fight) turns into ``shape``."""
    character = characters.get_for(identity, character_id)
    held = character.state.concentration
    caster = f"character:{character_id}"
    who = identity.user.username
    flash: str | None = None
    try:
        if held is None or not polymorph.is_polymorph(held.name):
            raise PlayError("play.error.bad_input")
        kind, _, target_id = target.partition(":")
        if polymorph.self_only(held.reference) and target != caster:
            raise PlayError("play.error.bad_input")  # Shapechange changes its caster alone
        if kind == "character" and target_id in dict(characters.allies(character)):
            characters.polymorph(target_id, shape, held.reference, caster, Actor(who, username=who))
        elif kind == "combatant":
            fights = registry.combats.joined_by([character_id])
            if not fights:
                raise PlayError("play.error.bad_input")
            registry.combats.polymorph(fights[0].id, target_id, shape, held.reference, caster, who)
        else:
            raise PlayError("play.error.bad_input")
    except (PlayError, CombatError) as exc:
        flash = page.t.error(exc)
    if not _is_htmx(request):
        return redirect(f"/characters/{character_id}")
    character = characters.get_for(identity, character_id)
    label = characters.file_label(character_id) if identity.is_gamemaster else None
    return html(
        sheet_fragment(
            page,
            _sheet(characters, registry, character),
            flash=flash,
            file_label=label,
            resolver=characters.resolver(),
            source_set=characters.source_set(character),
        )
    )


# -- the gamemaster's character list -----------------------------------------------------


@router.get("/gm/characters", response_class=HTMLResponse)
async def gm_characters(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    players: PlayersDep,
    worlds: WorldsDep,
) -> HTMLResponse:
    """Every character, who plays it, its world, and whether its file reads cleanly."""
    return html(
        characters_page(
            page,
            characters.summaries(),
            [s.user for s in players.summaries()],
            characters.unreadable_files,
            worlds.list(),
        )
    )


@router.post("/gm/characters")
async def gm_create_character(
    _gm: GamemasterDep,
    characters: CharactersDep,
    name: Annotated[str, Form()],
    player: Annotated[str, Form()] = "",
) -> Response:
    """Create a blank character file and open it."""
    created = characters.create(name or "New character", player or None)
    return redirect(f"/characters/{created.id}")


@router.post("/gm/characters/{character_id}/assign")
async def gm_assign_character(
    _gm: GamemasterDep,
    characters: CharactersDep,
    character_id: str,
    player: Annotated[str, Form()] = "",
) -> Response:
    """Hand a character to a player, or leave it unassigned."""
    characters.assign(character_id, player or None)
    return redirect("/gm/characters")


__all__ = ["router"]
