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

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from htpy import Node

from app.dependencies import (
    CharactersDep,
    GamemasterDep,
    IdentityDep,
    PageDep,
    PlayersDep,
    RegistryDep,
)
from app.models.gamestate import BastionFacility, Combat, Shop
from app.models.pc import ChosenOption, PlayerCharacter
from app.routes.auth import html, redirect
from app.routes.paravent_markdown import players_markdown
from app.routes.play_combat import strip
from app.routes.rolls import announce_roll, new_roll
from app.services import features, polymorph
from app.services.activities import rarity_of
from app.services.auth import Identity
from app.services.bastion_orders import OrderOption
from app.services.bastions import BastionError, basic_facilities, facilities
from app.services.chargen.catalogue import SHELVES, TOOL_TYPES, Offer
from app.services.chargen.plan import price
from app.services.chase import ChaseError
from app.services.clock import BASTION_TURN, day_of
from app.services.combat import CombatError
from app.services.contacts import ContactError
from app.services.crafting import craft_rules, tools_for
from app.services.downtime import DowntimeError, crafting_days, materials, training_plan
from app.services.events import revision_stream
from app.services.experience import progress
from app.services.factions import granted, title_of
from app.services.followers import PLAYER_FOLLOWER_CR, FollowerError, sidekick_level
from app.services.intervention import castable, intervention_of
from app.services.magic_crafting import Crafter, Plan, plan
from app.services.play import Actor, PlayError, cast_level
from app.services.sheet import Sheet
from app.services.shop import ShopError, Ware, priced
from app.services.spellbook import SpellbookError
from app.store.errors import EntryNotFound
from app.ui.bastion_pages import bastion_page
from app.ui.character_list_pages import character_delete_page, characters_page
from app.ui.context import Page
from app.ui.downtime_pages import FactionView, FormulaView, StartOptions, downtime_page
from app.ui.follower_pages import followers_page
from app.ui.intervention_pages import intervention_page
from app.ui.names import name_text
from app.ui.sheet_pages import sheet_fragment, sheet_page
from app.ui.shop_pages import ALL_SHELVES, shop_page
from app.ui.story_pages import story_page
from app.ui.tags import localized_context, render_entries

router = APIRouter(tags=["characters"])

# Values a sheet URL may carry to restore what was on screen.
_VIEW_KEYS = ("tab", "view")


def _with(registry: RegistryDep, character: PlayerCharacter, feature: str) -> PlayerCharacter:
    """The character, where its world plays with ``feature`` (app/services/features.py);
    a page of one switched off is not there."""
    if not registry.feature(character.world_id, feature):
        raise HTTPException(status_code=404)
    return character


def _sheet(characters: CharactersDep, registry: RegistryDep, character: PlayerCharacter) -> Sheet:
    """The derived sheet, told whether the character is in a running fight."""
    sheet = characters.sheet(character)
    fights = registry.combats.joined_by([character.id])
    sheet.in_fight = bool(fights)
    sheet.allies = characters.allies(character)
    held = character.state.concentration
    if held is not None and polymorph.is_polymorph(held.name):
        _shape_choices(sheet, characters, registry, character, held.reference, fights)
    sheet.calls = registry.checks.open_for(character.id)
    world = registry.gamestate.worlds.find(character.world_id) if character.world_id else None
    # Always set: the sheet object is cached per character state.
    sheet.world_features = on = features.switched_on(world)
    sheet.shop, sheet.downtime, sheet.chase = None, None, None
    if character.world_id:
        sheet.clock = registry.clock.of(character.world_id).minute
        sheet.timers = registry.clock.of_character(character.world_id, character.id)
        shop = registry.shops.serves(character.world_id, character.id)
        sheet.shop = shop.name if shop is not None and "shops" in on else None
        sheet.lifestyle_costs = (
            registry.lifestyle_costs(character.world_id) if "lifestyle" in on else {}
        )
        sheet.milestone = world is not None and world.milestone
        chase = registry.chases.of(character.world_id)
        runs = chase is not None and any(r.character_id == character.id for r in chase.runners)
        sheet.chase = chase if runs and "chases" in on else None
    sheet.deities = [
        (f"{d.name}|{d.source}", str(d.pantheon or ""))
        for d in registry.deities(world.id if world else None)
    ]
    sheet.deity_name = character.deity.name if character.deity is not None else ""
    sheet.intervention = intervention_of(character)
    sheet.followers = len(registry.followers.of(character.id))
    spare = registry.downtime.of(character.id)
    if "downtime" in on and (spare.days or any(not x.finished for x in spare.projects)):
        sheet.downtime = spare.days
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
            flash=page.t(flash)
            if flash.startswith(("play.error.", "spellbook.", "intervention."))
            else None,
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
    """The level a cast form sent; 0 (the spell's own level) for a free or an item's
    casting at its own level (app/services/play.py, ``cast_level``)."""
    return cast_level(level)


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
            identity,
            character_id,
            spell,
            _slot_level(level),
            target,
            free=level == "free",
            item=level if level.startswith("item:") else "",
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


def _bastion(
    identity: Identity,
    page: Page,
    characters: CharactersDep,
    registry: RegistryDep,
    character: PlayerCharacter,
    *,
    error: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    level = character.total_level
    entries = registry.compendium.all("facility")
    offered = facilities(entries, characters.source_set(character), level)
    every = {f.reference.lower(): f for f in facilities(entries, None, 20)}
    bastion = registry.bastions.of(character.id)
    options = [registry.facility_options(f.ref) for f in bastion.facilities]
    readies = [f.ready for f in bastion.basic if f.ready is not None] + [
        int(p.partition("|")[0]) for p in bastion.pending if p.partition("|")[0].isdigit()
    ]
    return html(
        bastion_page(
            page,
            character_id=character.id,
            character_name=character.name or "?",
            level=level,
            world_id=character.world_id,
            bastion=bastion,
            built=[every.get(f.ref.lower()) for f in bastion.facilities],
            offered=offered,
            gamemaster=identity.is_gamemaster,
            basic_offered=basic_facilities(entries, characters.source_set(character)),
            event_text=_event_text(page, characters, registry, character, bastion.event),
            next_turn=day_of(bastion.last_turn + BASTION_TURN)
            if bastion.last_turn is not None and character.world_id
            else None,
            options=options,
            tool_items=_tool_items(
                page, registry, character, options, _workshop_tools(bastion.facilities)
            ),
            equipment=[
                (
                    most,
                    [
                        (r, str(name_text(page, ("item", "baseitem"), r.partition("|")[0])))
                        for r in refs
                    ],
                )
                for most, refs in (registry.facility_tools(f.ref) for f in bastion.facilities)
            ],
            costs=registry.basic_costs(character.world_id),
            day_of={m: str(day_of(m)) for m in readies},
            discoveries=registry.discoveries(character.world_id)
            if bastion.offer == "discovery"
            else (),
            error=error,
            message=message,
        )
    )


def _tool_items(
    page: Page,
    registry: RegistryDep,
    character: PlayerCharacter,
    options: Sequence[Mapping[str, Sequence[OrderOption]]],
    workshop: Sequence[str] = (),
) -> dict[str, list[tuple[str, str]]]:
    """What each tool a facility crafts with makes, from the world's gear: ``"*"`` (the
    Workshop) the tools it was equipped with (``workshop``), any artisan's tools while
    none are chosen."""
    equipped = {w.partition("|")[0].lower() for w in workshop}
    wanted = {o.tool for found in options for order in found.values() for o in order if o.tool}
    if not wanted:
        return {}
    catalogue = registry.catalogue(character.world_id)
    rules = craft_rules(catalogue.rows("baseitem") + catalogue.rows("item"))
    gear = catalogue.shop(registry.rules_of(character.world_id))
    out: dict[str, list[tuple[str, str]]] = {}
    for tool in wanted:
        chosen = [
            r
            for r in rules
            if (tool == "*" and (not equipped or r.tool.lower() in equipped))
            or r.tool.lower() == tool.lower()
        ]
        out[tool] = sorted(
            (
                (o.reference, str(name_text(page, ("item", "baseitem"), o.name)))
                for o in gear
                if any(r.makes(o) for r in chosen) and price(o) > 0
            ),
            key=lambda pair: pair[1].lower(),
        )
    return out


def _workshop_tools(held: Sequence[BastionFacility]) -> list[str]:
    """The tools the bastion's facilities were equipped with (its Workshop's)."""
    return [tool for f in held for tool in f.tools]


def _event_text(
    page: Page,
    characters: CharactersDep,
    registry: RegistryDep,
    character: PlayerCharacter,
    event: str,
) -> Node:
    """The books' text of a rolled bastion event (``"<roll>|<entry id>|<name>…"``),
    when the guide's body has an entry of that name under that id."""
    parts = event.split("|")
    if len(parts) < 3:
        return None
    body = registry.rulebooks.body("xdmg")
    if body is None:
        return None
    entry = _entry_by_id([s.entry for s in body.sections], parts[1])
    if entry is None or str(entry.get("name", "")).lower() != parts[2].lower():
        return None
    context = localized_context(
        page.t,
        resolver=characters.resolver(),
        source_set=characters.source_set(character),
        world_id=character.world_id,
        units=page.u,
    )
    return render_entries(entry.get("entries") or [], context)


def _entry_by_id(nodes: list[Any], wanted: str) -> dict[str, Any] | None:
    """The entry with ``id`` ``wanted`` anywhere under ``nodes``."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("id") == wanted:
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


@router.get("/characters/{character_id}/bastion", response_class=HTMLResponse)
async def bastion(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> HTMLResponse:
    """The character's bastion (2024 rules)."""
    return _bastion(
        identity,
        page,
        characters,
        registry,
        _with(registry, characters.get_for(identity, character_id), "bastions"),
    )


@router.post("/characters/{character_id}/bastion", response_class=HTMLResponse)
async def change_bastion(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Name the bastion, build or pull down a facility, give an order, or (the
    gamemaster) run a bastion turn."""
    form = await request.form()
    # Read after the form: a second press waiting on its form sees the first's result.
    character = _with(registry, characters.get_for(identity, character_id), "bastions")
    op = str(form.get("op", ""))
    service = registry.bastions
    level = character.total_level
    every = {
        f.reference.lower(): f
        for f in facilities(
            registry.compendium.all("facility"), characters.source_set(character), level
        )
    }
    raw_index = str(form.get("index", ""))
    index = int(raw_index) if raw_index.isdigit() else -1
    try:
        if op == "rename":
            service.rename(character_id, str(form.get("name", "")))
        elif op == "add":
            chosen = every.get(str(form.get("facility", "")).lower())
            if chosen is None:
                raise BastionError("bastion.error.level")
            service.add(character_id, level, chosen)
        elif op == "remove":
            service.remove(character_id, index)
        elif op == "add_basic":
            basic = {
                f.reference.lower(): f
                for f in basic_facilities(
                    registry.compendium.all("facility"), characters.source_set(character)
                )
            }.get(str(form.get("facility", "")).lower())
            if basic is None:
                raise BastionError("bastion.error.order")
            adding, _ = registry.basic_costs(character.world_id)
            service.add_basic(
                character_id,
                level,
                basic,
                str(form.get("space", "")),
                costs=adding,
                at=registry.clock.of(character.world_id).minute if character.world_id else None,
            )
        elif op == "enlarge_basic":
            _, enlarging = registry.basic_costs(character.world_id)
            service.enlarge_basic(
                character_id,
                index,
                enlarging,
                at=registry.clock.of(character.world_id).minute if character.world_id else None,
            )
        elif op == "remove_basic":
            service.remove_basic(character_id, index)
        elif op == "defenders":
            change = 1 if str(form.get("change", "")) == "1" else -1
            if change > 0 and not identity.is_gamemaster:
                # A player recruits through a Barrack's order.
                raise BastionError("bastion.error.order")
            user = identity.user
            service.defenders(
                character_id,
                change,
                character.name or "?",
                user.username,
                user.display_name or user.username,
                character.world_id,
            )
        elif op == "tools":
            held = service.of(character_id).facilities
            ref = held[index].ref if 0 <= index < len(held) else ""
            most, allowed = registry.facility_tools(ref)
            service.equip(
                character_id, index, [str(v) for v in form.getlist("tool")], allowed, most
            )
        elif op == "maintain":
            service.maintain(character_id, form.get("on") == "1")
        elif op == "order":
            held = service.of(character_id).facilities
            ref = held[index].ref.lower() if 0 <= index < len(held) else ""
            known = {
                f.reference.lower(): f
                for f in facilities(registry.compendium.all("facility"), None, 20)
            }.get(ref)
            order, _, choice = str(form.get("order", "")).partition("|")
            found = registry.facility_options(ref).get(order, []) if order else []
            n = int(choice) if choice.isdigit() else 0
            if found and not 0 <= n < len(found):
                raise BastionError("bastion.error.order")
            option = str(n) if found else ""
            if found and found[n].tool:
                item = str(form.get("item", ""))
                tool_items = _tool_items(
                    page, registry, character, [{order: [found[n]]}], _workshop_tools(held)
                )
                if item and item not in {r for r, _ in tool_items.get(found[n].tool, [])}:
                    raise BastionError("bastion.error.order")
                option = f"{n}|{item}" if item else str(n)
            service.order(character_id, index, order, known.orders if known else (), option)
        elif op == "offer":
            value = str(form.get("value", ""))
            pending = service.of(character_id)
            if (
                pending.offer == "discovery"
                and form.get("accept") == "1"
                and value not in {ref for ref, _ in registry.discoveries(character.world_id)}
            ):
                # Only an uncommon potion or scroll of the world's books.
                raise BastionError("bastion.error.order")
            user = identity.user
            service.answer(
                character_id,
                character.name or "?",
                character.world_id,
                form.get("accept") == "1",
                str(form.get("value", "")),
                user.username,
                user.display_name or user.username,
            )
        elif op == "turn" and identity.is_gamemaster:
            drawn = str(form.get("turns", ""))
            if drawn.isdigit() and int(drawn) != service.of(character_id).turns:
                # Pressed again from the same page: the turn it was drawn for is run.
                raise BastionError("bastion.error.turned")
            user = identity.user
            service.turn(
                character_id,
                character.name or "?",
                character.world_id,
                user.username,
                user.display_name or user.username,
                at=registry.clock.of(character.world_id).minute if character.world_id else None,
            )
            return _bastion(
                identity, page, characters, registry, character, message=page.t("bastion.turned")
            )
        else:
            raise BastionError("bastion.error.order")
    except BastionError as exc:
        return _bastion(identity, page, characters, registry, character, error=page.t.error(exc))
    return redirect(f"/characters/{character_id}/bastion")


def _shop(
    page: Page,
    registry: RegistryDep,
    character: PlayerCharacter,
    *,
    shelf: str = "",
    query: str = "",
    error: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    shop = registry.shops.serves(character.world_id, character.id)
    if shop is None:
        raise HTTPException(status_code=404)
    catalogue = registry.catalogue(character.world_id)
    wares = _wares(registry, character, shop)
    sellable = []
    for n, item in enumerate(character.inventory):
        if item.unidentified or item.attuned:  # unknown, or perhaps held by a curse
            continue
        price = registry.shops.buys_for(shop, catalogue.item(item.item.reference))
        if price > 0 and not item.equipped and item.quantity > 0:
            sellable.append((n, item, price))
    return html(
        shop_page(
            page,
            character_id=character.id,
            character_name=character.name or "?",
            world_id=character.world_id,
            shop=shop,
            purse=character.currency,
            wares=wares,
            sellable=sellable,
            magic=[(item, priced(item.price, shop.markup)) for item in shop.stock],
            shelf_shown=shelf if shelf in SHELVES else "",
            everything=shelf == ALL_SHELVES,
            query=query.strip()[:60],
            error=error,
            message=message,
        )
    )


def _wares(registry: RegistryDep, character: PlayerCharacter, shop: Shop) -> list[Ware]:
    """The shop's shelves for a character: the world's mundane gear in its edition."""
    catalogue = registry.catalogue(character.world_id)
    bases = catalogue.index("baseitem")
    offers = catalogue.shop(registry.rules_of(character.world_id))
    return registry.shops.wares(shop, offers, lambda o: o.reference.lower() in bases)


@router.get("/characters/{character_id}/shop", response_class=HTMLResponse)
async def shop(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    shelf: str = "",
    q: str = "",
) -> HTMLResponse:
    """The open shop of the character's world, if it serves this character."""
    character = _with(registry, characters.get_for(identity, character_id), "shops")
    return _shop(page, registry, character, shelf=shelf, query=q)


@router.post("/characters/{character_id}/shop", response_class=HTMLResponse)
async def trade(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Buy from the shelves or sell something carried, at the shop's prices."""
    form = await request.form()
    # Read after the form: a second press waiting on its form sees the first's result.
    character = _with(registry, characters.get_for(identity, character_id), "shops")
    shop = registry.shops.serves(character.world_id, character.id)
    if shop is None:
        raise HTTPException(status_code=404)
    raw_count = str(form.get("count", "1"))
    count = max(1, min(99, int(raw_count))) if raw_count.isdigit() else 1
    actor = Actor(
        identity.user.display_name or identity.user.username, username=identity.user.username
    )
    message = None
    try:
        if form.get("op") == "buy":
            wanted = str(form.get("item", "")).lower()
            ware = next(
                (w for w in _wares(registry, character, shop) if w.reference.lower() == wanted),
                None,
            )
            if ware is None:
                raise PlayError("play.error.bad_input")
            characters.act_for(
                character.id,
                "buy",
                {"item": ware.reference, "count": str(count), "price": str(ware.price)},
                actor,
            )
            message = page.t(
                "shop.bought", name=name_text(page, ("item", "baseitem"), ware.offer.name), n=count
            )
        elif form.get("op") == "buy_magic":
            wanted = str(form.get("item", "")).lower()
            stocked = next((i for i in shop.stock if i.ref.lower() == wanted), None)
            if stocked is None or stocked.count == 0:
                raise ShopError("shop.error.sold_out")
            characters.act_for(
                character.id,
                "buy",
                {
                    "item": stocked.ref,
                    "count": "1",
                    "price": str(priced(stocked.price, shop.markup)),
                },
                actor,
            )
            if character.world_id:
                registry.shops.sold(character.world_id, stocked.ref, 1)
            message = page.t(
                "shop.bought", name=name_text(page, "item", stocked.ref.partition("|")[0]), n=1
            )
        elif form.get("op") == "sell":
            raw = str(form.get("index", ""))
            index = int(raw) if raw.isdigit() else -1
            if not 0 <= index < len(character.inventory) or character.inventory[index].equipped:
                raise PlayError("play.error.bad_input")
            item = character.inventory[index]
            if str(form.get("ref", "")) != item.item.reference:
                # The pack changed since the page was drawn: sell nothing in its place.
                raise PlayError("play.error.bad_input")
            price = registry.shops.buys_for(
                shop, registry.catalogue(character.world_id).item(item.item.reference)
            )
            if price <= 0:
                raise PlayError("play.error.bad_input")
            characters.act_for(
                character.id,
                "sell",
                {
                    "index": str(index),
                    "ref": item.item.reference,
                    "count": str(min(count, item.quantity)),
                    "price": str(price),
                },
                actor,
            )
            message = page.t(
                "shop.sold", name=name_text(page, ("item", "baseitem"), item.item.name)
            )
        else:
            raise PlayError("play.error.bad_input")
    except (PlayError, ShopError) as exc:
        fresh = characters.get_for(identity, character_id)
        return _shop(page, registry, fresh, error=page.t.error(exc))
    return _shop(page, registry, characters.get_for(identity, character_id), message=message)


def _downtime(
    page: Page,
    registry: RegistryDep,
    character: PlayerCharacter,
    *,
    error: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    rules = registry.rules_of(character.world_id)
    craftable, unlisted = _craftable(registry, character)
    rule = registry.training_rule(character.world_id)
    sheet = registry.characters.sheet(character)
    training_days, training_daily = training_plan(rule, sheet.ability("int").modifier)
    craft = registry.magic_craft_rule(character.world_id)
    start = StartOptions(
        craftable=[
            (
                o.reference,
                str(name_text(page, ("item", "baseitem"), o.name)),
                crafting_days(p, rules),
                materials(p),
                ", ".join(str(name_text(page, ("item", "baseitem"), tool)) for tool in tools),
            )
            for o, tools in craftable
            if (p := price(o)) > 0
        ],
        unlisted=unlisted,
        trainable=_trainable(page, registry, character),
        training=rule,
        training_days=training_days,
        training_daily=training_daily,
        scrolls=_scribable(registry, character),
        activities=registry.has_book(character.world_id, "XGE"),
        magic=[(ref, name, rarity) for ref, name, rarity, _ in _magic_carried(registry, character)],
        formulas=[
            FormulaView(ref, str(name_text(page, "item", ref.partition("|")[0])), made)
            for ref, made in _formula_plans(registry, character)
        ],
        craft_book=craft.book if craft is not None else None,
    )
    return html(
        downtime_page(
            page,
            character_id=character.id,
            character_name=character.name or "?",
            downtime=registry.downtime.of(character.id),
            start=start,
            contacts=registry.contacts.of(character.id),
            now=registry.clock.of(character.world_id).minute if character.world_id else None,
            factions=[
                FactionView(
                    faction,
                    renown,
                    title_of(faction, renown),
                    granted(registry.renown_rewards(character.world_id, faction), renown),
                    players_markdown(page, registry, faction.world_id, faction.note)
                    if faction.note
                    else None,
                )
                for faction, renown in registry.factions.standing(character.id, character.world_id)
                if registry.feature(character.world_id, "factions")
            ],
            faction_choices=[
                (f.id, f.name) for f in registry.factions.of(character.world_id or "")
            ],
            error=error,
            message=message,
        )
    )


def _craftable(
    registry: RegistryDep, character: PlayerCharacter
) -> tuple[list[tuple[Offer, list[str]]], int]:
    """What a character may craft, with the tools that make each: the mundane gear the
    shop would sell, less what needs a tool the character lacks (how many that is)."""
    catalogue = registry.catalogue(character.world_id)
    rules = craft_rules(catalogue.rows("baseitem") + catalogue.rows("item"))
    known = [o.name for o in character.tools]
    held = {k.lower() for k in known}
    out, left_out = [], 0
    for offer in catalogue.shop(registry.rules_of(character.world_id)):
        tools = tools_for(offer, rules)
        if tools and not any(tool.lower() in held for tool in tools):
            left_out += 1
            continue
        out.append((offer, [tool for tool in tools if tool.lower() in held]))
    return out, left_out


def _formula_plans(
    registry: RegistryDep, character: PlayerCharacter
) -> list[tuple[str, Plan | None]]:
    """Each magic item formula the character holds, with how it would craft it by the
    world's guide (app/services/magic_crafting.py); None where the guide has no row."""
    rule = registry.magic_craft_rule(character.world_id)
    if not character.formulas:
        return []
    sheet = registry.characters.sheet(character)
    crafter = Crafter(
        level=character.total_level,
        arcana=any(s.key == "arcana" and s.proficient for s in sheet.skills),
        tools=[o.name for o in character.tools],
        spells=[s.name for s in sheet.spells if s.found],
        slots=any(s.total for s in sheet.slots) or bool(sheet.pact and sheet.pact.total),
    )
    out: list[tuple[str, Plan | None]] = []
    for formula in character.formulas:
        made = None
        if rule is not None:
            try:
                found = registry.compendium.find("item", formula.name, formula.source or "")
            except EntryNotFound:
                found = None
            data = found.model_dump(by_alias=True, exclude_none=True) if found else {}
            base_price = 0
            base = str(data.get("baseItem") or "")
            if base:
                name, _, source = base.partition("|")
                try:
                    item = registry.compendium.find("baseitem", name, source)
                    base_price = int(getattr(item, "value", 0) or 0)
                except (EntryNotFound, TypeError, ValueError):
                    base_price = 0
            made = plan(rule, data, crafter, base_price) if data else None
        out.append((formula.reference, made))
    return out


def _scribable(
    registry: RegistryDep, character: PlayerCharacter
) -> list[tuple[str, int, int, int]]:
    """The spells a character may put on a scroll, ``(spell, level, days, copper)``: it
    needs Arcana (or, under the 2024 rules, calligrapher's supplies) and the book's
    costs table; a spell the sheet lists, prepared or known."""
    costs = registry.scroll_costs(character.world_id)
    if not costs:
        return []
    sheet = registry.characters.sheet(character)
    arcana = any(s.key == "arcana" and s.proficient for s in sheet.skills)
    calligraphy = registry.rules_of(character.world_id) == "one" and any(
        o.name.lower() == "calligrapher's supplies" for o in character.tools
    )
    if not (arcana or calligraphy):
        return []
    out = []
    for spell in sheet.spells:
        level = getattr(spell, "level", None)
        if spell.found and isinstance(level, int) and level in costs:
            days_needed, cost, _scroll = costs[level]
            out.append((spell.reference, level, days_needed, cost))
    return sorted(set(out), key=lambda row: (row[1], row[0].lower()))


def _magic_carried(
    registry: RegistryDep, character: PlayerCharacter
) -> list[tuple[str, str, str, int]]:
    """Carried magic items that may be sold, ``(reference, name, rarity, index)``."""
    out = []
    for n, item in enumerate(character.inventory):
        if item.equipped or item.quantity <= 0 or item.unidentified or item.attuned:
            continue
        try:
            found = registry.compendium.find("item", item.item.name, item.item.source or "")
        except EntryNotFound:
            continue
        data = found.model_dump(by_alias=True, exclude_none=True) if found else {}
        rarity = rarity_of(data.get("rarity"))
        if rarity:
            out.append((item.item.reference, item.item.name, rarity, n))
    return out


def _trainable(
    page: Page, registry: RegistryDep, character: PlayerCharacter
) -> list[tuple[str, str]]:
    """Languages and tools of the world's books the character does not know yet."""
    catalogue = registry.catalogue(character.world_id)
    known = {o.name.lower() for o in [*character.languages, *character.tools]}
    out = [
        (f"language:{o.reference}", str(name_text(page, "language", o.name)))
        for o in catalogue.rows("language")
        if o.name.lower() not in known and o.data.get("type") != "secret"
    ]
    tools = TOOL_TYPES["any"]
    seen: set[str] = set()
    for category in ("baseitem", "item"):
        for o in catalogue.rows(category):
            code = str(o.data.get("type", "")).split("|")[0]
            if code in tools and o.name.lower() not in known and o.name.lower() not in seen:
                seen.add(o.name.lower())
                out.append(
                    (f"tool:{o.reference}", str(name_text(page, ("item", "baseitem"), o.name)))
                )
    return sorted(out, key=lambda pair: (not pair[0].startswith("language:"), pair[1].lower()))


def _followers(
    page: Page,
    registry: RegistryDep,
    character: PlayerCharacter,
    error: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    held = registry.followers.of(character.id)
    return html(
        followers_page(
            page,
            character_id=character.id,
            character_name=character.name or "?",
            world_id=character.world_id,
            followers=held,
            wages=registry.wages(character.world_id),
            sidekick_classes=registry.sidekick_classes(character.world_id),
            progress={
                f.id: registry.sidekick_progress(f.sidekick_class, sidekick_level(f))
                for f in held
                if f.sidekick_class
            },
            in_fight=bool(registry.combats.joined_by([character.id])),
            error=error,
            message=message,
        ),
        status_code=200 if error is None else 422,
    )


@router.post("/characters/{character_id}/deity")
async def choose_deity(
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    deity: Annotated[str, Form()] = "",
) -> Response:
    """The deity the character worships, of those its world allows; empty for none."""
    character = characters.get_for(identity, character_id)
    allowed = {ref.lower() for ref, _ in _sheet(characters, registry, character).deities}
    if deity and deity.lower() not in allowed:
        raise HTTPException(status_code=400)
    chosen = ChosenOption.model_validate(deity) if deity else None
    registry.gamestate.characters.update(character.id, deity=chosen)
    return redirect(f"/characters/{character.id}?tab=features&view=gear")


@router.get("/characters/{character_id}/intervention", response_class=HTMLResponse)
async def intervention_choice(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    error: str = "",
) -> HTMLResponse:
    """A 2024 cleric's Divine Intervention: the spells it may cast, to choose one."""
    character = characters.get_for(identity, character_id)
    held = intervention_of(character)
    if held is None or held.rules != "one":
        raise HTTPException(status_code=404)
    spells = castable(character, registry.catalogue(character.world_id), held)
    now = registry.clock.of(character.world_id).minute if character.world_id else None
    return html(
        intervention_page(
            page,
            character,
            held,
            spells,
            ready=held.ready(now),
            error=page.t(error) if error.startswith("play.error.") else None,
        )
    )


@router.post("/characters/{character_id}/intervention")
async def call_on_deity(
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    spell: Annotated[str, Form()] = "",
) -> Response:
    """The cleric calls on the deity: the 2014 roll, or the 2024 spell chosen (one of
    those the world's books allow), worked out here and applied through the play rules."""
    character = characters.get_for(identity, character_id)
    held = intervention_of(character)
    if held is None:
        raise HTTPException(status_code=404)
    now = registry.clock.of(character.world_id).minute if character.world_id else None
    params = {"now": str(now) if now is not None else "", "deity": _deity_name(character)}
    back = f"/characters/{character.id}?view=track"
    if held.rules == "one":
        chosen = next(
            (c for c in castable(character, registry.catalogue(character.world_id), held)
             if c.reference.lower() == spell.lower()),
            None,
        )  # fmt: skip
        if chosen is None:
            raise HTTPException(status_code=400)
        params.update(
            spell=chosen.reference,
            concentration="1" if chosen.concentration else "",
            wish="1" if chosen.wish else "",
        )
    user = identity.user
    try:
        characters.act_for(
            character.id,
            "intervention",
            params,
            Actor(user.display_name or user.username, username=user.username),
        )
    except PlayError as exc:
        if held.rules == "one":
            return redirect(
                f"/characters/{character.id}/intervention?{urlencode({'error': exc.key})}"
            )
        return redirect(f"{back}&{urlencode({'flash': exc.key})}")
    # What came of it, said on the sheet; the log has the roll.
    after = registry.gamestate.characters.get(character.id).state
    if held.rules == "one":
        said = "intervention.cast_done"
    elif after.intervention_until is not None:
        said = "intervention.answered"
    else:
        said = "intervention.unanswered"
    return redirect(f"{back}&{urlencode({'flash': said})}")


def _deity_name(character: PlayerCharacter) -> str:
    return character.deity.name if character.deity is not None else ""


@router.get("/characters/{character_id}/story", response_class=HTMLResponse)
async def story(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> HTMLResponse:
    """What the character has done and whom it knows."""
    character = characters.get_for(identity, character_id)
    world = registry.gamestate.worlds.find(character.world_id) if character.world_id else None
    told = registry.story.of(character, gamemaster=identity.is_gamemaster)
    # What the world plays without is left out (app/services/features.py).
    on = features.switched_on(world)
    told = replace(
        told,
        quests=told.quests if "quests" in on else [],
        factions=told.factions if "factions" in on else [],
        contacts=told.contacts if "downtime" in on else [],
        followers=told.followers if "followers" in on else [],
        bastion=told.bastion if "bastions" in on else None,
    )
    return html(
        story_page(
            page,
            character,
            told,
            progress=progress(character),
            milestone=world is not None and world.milestone,
        )
    )


@router.get("/characters/{character_id}/followers", response_class=HTMLResponse)
async def followers(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> HTMLResponse:
    """The character's hirelings, companions, mounts and sidekicks."""
    return _followers(
        page, registry, _with(registry, characters.get_for(identity, character_id), "followers")
    )


@router.post("/characters/{character_id}/followers", response_class=HTMLResponse)
async def change_followers(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Hire, add, hurt or heal, level, dismiss a follower, or bring one into the fight."""
    form = await request.form()
    # Read after the form: a second press waiting on its form sees the first's result.
    character = _with(registry, characters.get_for(identity, character_id), "followers")
    op = str(form.get("op", ""))
    service = registry.followers
    follower_id = str(form.get("follower", ""))
    message = None
    try:
        if op == "hire":
            service.hire(character, str(form.get("kind", "")), str(form.get("name", "")))
        elif op == "creature":
            service.add_creature(
                character,
                str(form.get("kind", "")),
                str(form.get("creature", "")),
                str(form.get("name", "")),
                sidekick_class=str(form.get("sidekick_class", "")),
                most_cr=None if identity.is_gamemaster else PLAYER_FOLLOWER_CR,
            )
        elif op == "hp":
            delta = str(form.get("delta", "0"))
            service.hit_points(
                character.id, follower_id, int(delta) if delta.lstrip("+-").isdigit() else 0
            )
        elif op == "level":
            if not identity.is_gamemaster:  # a sidekick levels when the gamemaster says
                raise FollowerError("followers.error.level")
            service.level_up(character.id, follower_id)
        elif op == "dismiss":
            service.dismiss(character.id, follower_id)
        elif op == "fight":
            follower = service.get(character.id, follower_id)
            fights = registry.combats.joined_by([character.id])
            if not fights or not follower.monster:
                raise FollowerError("followers.error.no_fight")
            registry.combats.add_follower(
                fights[0].id,
                follower.monster,
                follower.name,
                follower.hp,
                follower.hp_max,
                identity.user.username,
                follower_id=follower.id,
            )
            message = page.t("followers.joined", name=follower.name)
        else:
            raise FollowerError("followers.error.unknown")
    except (FollowerError, CombatError) as exc:
        return _followers(page, registry, character, error=page.t.error(exc))
    if message:
        return _followers(page, registry, character, message=message)
    return redirect(f"/characters/{character_id}/followers")


@router.get("/characters/{character_id}/downtime", response_class=HTMLResponse)
async def downtime(
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> HTMLResponse:
    """The character's free downtime days and its projects."""
    return _downtime(
        page, registry, _with(registry, characters.get_for(identity, character_id), "downtime")
    )


@router.post("/characters/{character_id}/downtime", response_class=HTMLResponse)
async def change_downtime(
    request: Request,
    identity: IdentityDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """Start a project, spend days on one, or give one up."""
    form = await request.form()
    # Read after the form: a second press waiting on its form sees the first's result.
    character = _with(registry, characters.get_for(identity, character_id), "downtime")
    op = str(form.get("op", ""))
    service = registry.downtime
    user = identity.user
    name = user.display_name or user.username
    try:
        if op == "start":
            kind = str(form.get("kind", ""))
            label = str(form.get("label", ""))
            item_price = None
            raw = str(form.get("needed", "")).strip()
            needed = int(raw) if raw.isdigit() else None
            daily = None
            if kind == "craft":
                wanted = str(form.get("item", "")).lower()
                offer = next(
                    (
                        o
                        for o, _ in _craftable(registry, character)[0]
                        if o.reference.lower() == wanted
                    ),
                    None,
                )
                if offer is None:
                    raise DowntimeError("downtime.error.item")
                label, item_price = offer.reference, price(offer)
            elif kind == "train":
                label = str(form.get("item", ""))
                if label not in {ref for ref, _ in _trainable(page, registry, character)}:
                    raise DowntimeError("downtime.error.item")
                modifier = registry.characters.sheet(character).ability("int").modifier
                needed, daily = training_plan(
                    registry.training_rule(character.world_id), modifier, needed
                )
            elif kind == "scribe":
                spell, _, level = str(form.get("spell", "")).rpartition("|")
                chosen = next(
                    (
                        row
                        for row in _scribable(registry, character)
                        if row[0] == spell and str(row[1]) == level
                    ),
                    None,
                )
                if chosen is None:
                    raise DowntimeError("downtime.error.item")
                scroll = registry.scroll_costs(character.world_id)[chosen[1]][2]
                label, needed, item_price = f"{scroll};{spell}", chosen[2], chosen[3]
            elif kind == "enchant":
                wanted = str(form.get("item", "")).lower()
                made = next(
                    (m for ref, m in _formula_plans(registry, character) if ref.lower() == wanted),
                    None,
                )
                if made is None or not made.possible:
                    raise DowntimeError("downtime.error.item")
                label = next(
                    f.reference for f in character.formulas if f.reference.lower() == wanted
                )
                needed, item_price, daily = made.days, made.paid, made.daily or None
            service.start(
                character,
                kind,
                label,
                needed=needed,
                price=item_price,
                rules=registry.rules_of(character.world_id),
                daily=daily,
                by=user.username,
                by_name=name,
            )
            message = page.t("downtime.started")
        elif op == "activity":
            if not registry.has_book(character.world_id, "XGE"):
                raise DowntimeError("downtime.error.kind")
            kind = str(form.get("kind", ""))
            options = {k: str(v) for k, v in form.items() if isinstance(v, str)}
            if kind == "sell_magic":
                ref, _, rarity = str(form.get("item", "")).rpartition("|")
                if not any(
                    r == ref and rare == rarity
                    for r, _n, rare, _i in _magic_carried(registry, character)
                ):
                    raise DowntimeError("downtime.error.item")
                options |= {"item": ref, "rarity": rarity}
            world = character.world_id
            project = registry.activities.run(
                character,
                kind,
                options,
                lambda caption: registry.book_table(world, caption, ["XGE"]),
                by=user.username,
                by_name=name,
            )
            registry.contacts.from_activity(character, kind, project.result, user.username, name)
            message = page.t("downtime.done")
        elif op == "formula_drop":
            if not identity.is_gamemaster:
                raise DowntimeError("downtime.error.item")
            registry.formulas.drop(character.id, str(form.get("item", "")), user.username, name)
            message = None
        elif op == "contact_add":
            registry.contacts.add(
                character,
                str(form.get("kind", "contact")),
                str(form.get("name", "")),
                str(form.get("note", "")),
                str(form.get("faction", "")),
            )
            message = None
        elif op == "contact_edit":
            registry.contacts.edit(
                character.id,
                str(form.get("contact", "")),
                str(form.get("name", "")),
                str(form.get("note", "")),
                str(form.get("faction", "")),
                str(form.get("attitude", "")) if "attitude" in form else None,
            )
            message = None
        elif op == "contact_seen":
            minute = registry.clock.of(character.world_id).minute if character.world_id else 0
            registry.contacts.seen(character.id, str(form.get("contact", "")), minute)
            message = None
        elif op == "contact_call":
            registry.contacts.call_on(character, str(form.get("contact", "")), user.username, name)
            message = None
        elif op == "contact_remove":
            registry.contacts.remove(character.id, str(form.get("contact", "")))
            message = None
        elif op in ("take", "refuse"):
            raw = str(form.get("offer", ""))
            index = int(raw) if raw.isdigit() else -1
            project_id = str(form.get("project", ""))
            # The offer the button was drawn for: a stale page takes nothing else.
            expected = str(form.get("expected", ""))
            if op == "take":
                registry.activities.take(character, project_id, index, expected)
                message = page.t("downtime.taken")
            else:
                registry.activities.refuse(character, project_id, index, expected)
                message = None
        elif op == "spend":
            raw = str(form.get("days", ""))
            project = service.spend(
                character,
                str(form.get("project", "")),
                int(raw) if raw.isdigit() else 0,
                user.username,
                name,
            )
            message = page.t("downtime.finished_now" if project.finished else "downtime.spent")
        elif op == "drop":
            service.drop(character.id, str(form.get("project", "")))
            message = None
        else:
            raise DowntimeError("downtime.error.project")
    except (DowntimeError, PlayError, ContactError) as exc:
        fresh = characters.get_for(identity, character_id)
        return _downtime(page, registry, fresh, error=page.t.error(exc))
    registry.events.publish(character.id, f"downtime-{datetime.now(UTC).isoformat()}")
    return _downtime(page, registry, characters.get_for(identity, character_id), message=message)


@router.post("/characters/{character_id}/timer/{timer_id}/stop")
async def stop_timer(
    request: Request,
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    timer_id: str,
) -> Response:
    """Put out one of the character's lights or end a spell's timer early; the sheet
    follows over its live stream."""
    character = characters.get_for(identity, character_id)
    if character.world_id:
        registry.clock.stop(character.world_id, timer_id, character_id=character.id)
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204)
    return redirect(f"/characters/{character_id}")


@router.post("/characters/{character_id}/chase/{op}")
async def chase_turn(
    request: Request,
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
    op: str,
) -> Response:
    """A runner's own turn in the world's chase: Dash (``dash``) or end it (``turn``);
    refused on anyone else's turn. The sheet follows over its live stream."""
    character = characters.get_for(identity, character_id)
    chases = registry.chases
    world_id = character.world_id
    chase = chases.of(world_id) if world_id else None
    user = identity.user
    name = user.display_name or user.username
    if (
        world_id
        and chase is not None
        and not chase.over
        and chase.turn < len(chase.runners)
        and chase.runners[chase.turn].character_id == character.id
    ):
        with contextlib.suppress(ChaseError, PlayError):
            if op == "dash":
                chases.dash(world_id, chase.turn, user.username, name)
            elif op == "turn":
                rows, die = registry.chase_complications(world_id, chase.environment)
                chases.end_turn(
                    world_id,
                    rows,
                    die,
                    user.username,
                    name,
                    registry.chase_translations(world_id, chase.environment),
                )
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=204)
    return redirect(f"/characters/{character_id}")


@router.post("/characters/{character_id}/scroll")
async def use_scroll(
    request: Request,
    identity: IdentityDep,
    characters: CharactersDep,
    registry: RegistryDep,
    character_id: str,
) -> Response:
    """A spell scroll: write in its spell (``inscribe``), cast it (``cast``) or copy it
    into the spellbook (``copy``); or copy a spell from another book (``book``). The
    sheet says how it went."""
    form = await request.form()
    # Read after the form: a second press waiting on its form sees the first's result.
    character = characters.get_for(identity, character_id)
    op = str(form.get("op", ""))
    index_text = str(form.get("index", ""))
    index = int(index_text) if index_text.isdigit() else -1
    # The spell the button was drawn for: a repeated press finds the pack changed and
    # must not use up another scroll at the same place.
    expected = str(form.get("held", ""))
    user = identity.user
    service = registry.spellbook_for(
        Actor(user.display_name or user.username, username=user.username)
    )
    flash = ""
    try:
        if op == "inscribe":
            service.inscribe(character, index, str(form.get("spell", "")))
        elif op == "cast":
            done = service.cast(character, index, expected)
            flash = "spellbook.cast_ok" if done.ok else "spellbook.cast_failed"
        elif op == "copy":
            done = service.copy(character, "", index, expected)
            flash = "spellbook.copied" if done.ok else "spellbook.copy_failed"
        elif op == "book":
            service.copy(character, str(form.get("spell", "")))
            flash = "spellbook.copied"
        else:
            raise SpellbookError("spellbook.error.scroll")
    except SpellbookError as exc:
        flash = exc.key
    except PlayError as exc:
        flash = exc.key
    return redirect(f"/characters/{character_id}?{urlencode({'tab': 'gear', 'flash': flash})}")


@router.post("/characters/{character_id}/item")
async def item_secret(
    request: Request,
    gm: GamemasterDep,
    characters: CharactersDep,
    character_id: str,
) -> Response:
    """The gamemaster's word on a carried item (app/services/identify.py): identify it
    (``identify``), lift its curse (``lift_curse``), or set whether it is unidentified,
    what it is shown as and whether it is cursed (``item_secret``)."""
    form = await request.form()
    op = str(form.get("op", ""))
    params = {
        key: str(form.get(key, ""))
        for key in ("index", "ref", "unidentified", "shown_as", "cursed")
        if form.get(key) is not None
    }
    flash = ""
    try:
        if op not in ("identify", "lift_curse", "item_secret"):
            raise PlayError("play.error.bad_input")
        by = Actor(gm.user.display_name or gm.user.username, username=gm.user.username)
        characters.act_for(character_id, op, params, by)
    except PlayError as exc:
        flash = exc.key
    query = {"tab": "gear", **({"flash": flash} if flash else {})}
    return redirect(f"/characters/{character_id}?{urlencode(query)}")


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
) -> HTMLResponse:
    """Every character, who plays it, and whether its file reads cleanly."""
    return html(
        characters_page(
            page,
            characters.summaries(),
            [s.user for s in players.summaries()],
            characters.unreadable_files,
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
