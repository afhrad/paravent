"""The gamemaster's table: the live party overview, and worlds with their books.

* ``GET  /gm``                         — every character, live (``?world=`` narrows it)
* ``GET  /gm/party``                   — just the grid, refetched when a character
  appears or disappears
* ``GET  /gm/party/{id}/card``         — one card, refetched when that character changes
* ``GET  /gm/events``                  — Server-Sent Events: ``"<id> <revision>"`` for
  every character change, from any screen or a hand edit
* ``/gm/worlds…``                      — create, edit and delete worlds (a world's own
  page, with its adventures, is in :mod:`app.routes.adventures`)
* ``POST /gm/characters/{id}/world``   — place a character in a world
* ``/gm/checks…``                      — call for a check outside a fight, and close it
* ``/gm/clock…``                       — a world's time in the game: move it, rest the
  party, timers, travel, downtime
* ``/gm/shop…``                        — open a shop for a world's characters, and close it
* ``/gm/chase…``                       — run a chase: start it, Dash, turns, escapes, end
* ``/gm/traps…``                       — spring a trap or hazard on the world's characters
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from htpy import Element, Node
from starlette.datastructures import FormData

from app.dependencies import (
    CharactersDep,
    GamemasterDep,
    PageDep,
    RegistryDep,
    WorldsDep,
)
from app.routes.adventures import upload_messages
from app.routes.auth import html, redirect
from app.routes.rolls import announce_roll, new_roll
from app.services.activities import RARITIES
from app.services.auth import AuthError
from app.services.characters import CharacterService
from app.services.chase import ChaseError, Runner
from app.services.checks import CheckError
from app.services.clock import DAY, ClockError, bastions_due
from app.services.dice import Advantage
from app.services.downtime import DowntimeError
from app.services.events import PARTY, revision_stream
from app.services.play import Actor, PlayError
from app.services.player_view import player_choice, player_ticks
from app.services.references import ReferenceNotFound
from app.services.shop import ShopError
from app.services.traps import Target, TrapEffect, read_trap
from app.services.travel import CLIMATES, PACES, TRAVEL_DAY, WATER, Plan, TravelError, Vessel
from app.services.units import feet_from
from app.services.worlds import WorldInput
from app.store.encounter_tables import Environment
from app.store.registry import StoreRegistry
from app.ui.adventure_pages import images_panel
from app.ui.chase_pages import CHASE_URL, chase_panel
from app.ui.check_pages import CHECKS_URL, checks_panel
from app.ui.clock_pages import CLOCK_URL, clock_label, clock_panel
from app.ui.combat_pages import party_combats
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.gm_pages import (
    WORLDS_URL,
    party_card,
    party_grid,
    party_page,
    world_delete_page,
    world_form_page,
    worlds_page,
)
from app.ui.shop_pages import SHOP_URL, shop_panel
from app.ui.trap_pages import TRAP_CATEGORIES, TRAPS_URL, spring_form, spring_result

router = APIRouter(tags=["gamemaster"])


def _combats(page: Page, registry: StoreRegistry, world_id: str | None) -> Element:
    combats = registry.combats
    views = [combats.view(c) for c in combats.running(world_id=world_id)]
    return party_combats(page, views, world_id=world_id)


def _checks(
    page: Page, registry: StoreRegistry, characters: CharacterService, world_id: str | None
) -> Element:
    asked = [
        (m.sheet.character.id, m.sheet.character.name or "?")
        for m in characters.party(world_id)
        if not m.sheet.character.in_wizard
    ]
    return checks_panel(page, registry.checks.recent(world_id), asked, world_id)


def _clock_world(registry: StoreRegistry, world_id: str | None) -> str | None:
    """The world whose clock the party view shows: the one chosen, or the only one."""
    if world_id is not None:
        return world_id
    worlds = registry.gamestate.worlds.list()
    return worlds[0].id if len(worlds) == 1 else None


def _clock(page: Page, registry: StoreRegistry, world_id: str | None) -> Element | None:
    shown = _clock_world(registry, world_id)
    if shown is None:
        return None
    clock = registry.clock.of(shown)
    members = [
        pc
        for pc in registry.gamestate.characters.list()
        if pc.world_id == shown and not pc.in_wizard
    ]
    names = {pc.id: pc.name or "?" for pc in members}
    built = [(pc.name or "?", registry.bastions.of(pc.id)) for pc in members if pc.total_level >= 5]
    due = bastions_due(clock.minute, [(name, b.last_turn) for name, b in built if b.facilities])
    return clock_panel(
        page,
        shown,
        clock.minute,
        registry.clock.active(shown),
        names,
        due,
        journey=clock.journey,
        environments=[(e.key, f"{e.name} ({e.source})") for e in _environments(registry, shown)],
        vessels=[v.name for v in registry.vessels(shown)],
        mounted=registry.followers.mounted([pc.id for pc in members]),
        hazards=registry.hazard_links(shown),
        weather=registry.weather(shown) is not None,
        calendars=registry.calendars(shown),
        calendar=clock.calendar,
        era=clock.calendar_era,
    )


def _environments(registry: StoreRegistry, world_id: str) -> list[Environment]:
    """The random-encounter tables of a world's books (every table for a world that
    declares none)."""
    world = registry.gamestate.worlds.find(world_id)
    tables = registry.encounter_tables.all()
    if world is None or world.source_set.is_unrestricted:
        return tables
    books = set(world.source_set.normalized)
    return [e for e in tables if e.source.lower() in books]


def _travel(registry: StoreRegistry, world_id: str, form: FormData, by: str, by_name: str) -> None:
    """Set out on the road with the travel form's plan."""
    amount = str(form.get("amount", "")).strip()
    hours = (
        int(amount) * (TRAVEL_DAY if form.get("unit") == "day" else 1) if amount.isdigit() else 0
    )
    pace = str(form.get("pace", "normal"))
    checks = str(form.get("checks", "1"))
    environment = registry.encounter_tables.get(str(form.get("environment", "")))
    world = registry.gamestate.worlds.find(world_id)
    resolver = registry.references.resolver()

    def resolve(name: str, source: str | None) -> tuple[str, str] | None:
        found = resolver.resolve_parts(
            "monster", name, source, world.source_set if world else None
        ).entry
        return (str(found.name), str(found.source)) if found is not None else None

    by_ship = str(form.get("vessel", ""))
    vessel = next((v for v in registry.vessels(world_id) if v.name == by_ship), None)
    if vessel is not None and form.get("around") == "1":
        vessel = Vessel(vessel.name, vessel.mph, around_the_clock=True)
    water = str(form.get("water", "enough"))
    climate = str(form.get("climate", ""))
    registry.travel.travel(
        world_id,
        Plan(
            hours=hours,
            pace=pace if pace in PACES else "normal",  # type: ignore[arg-type]
            difficult=form.get("difficult") == "1",
            environment=environment,
            checks=int(checks) if checks.isdigit() and int(checks) <= 3 else 1,
            rations=form.get("rations") == "1",
            rest=form.get("rest") == "1",
            water=water if water in WATER else "enough",  # type: ignore[arg-type]
            mounted=by_ship == "mounted",
            vessel=vessel,
            night_check=form.get("night") == "1",
            weather=form.get("weather") == "1",
            climate=climate if climate in CLIMATES else "",  # type: ignore[arg-type]
            altitude=form.get("altitude") == "1",
        ),
        rules=registry.rules_of(world_id),
        resolve=resolve,
        by=by,
        by_name=by_name,
        weather=registry.weather(world_id),
    )


def _calendar(registry: StoreRegistry, world_id: str, form: FormData) -> None:
    """Date the clock's days by one of the books' calendars from today's date, or by
    plain day numbers again."""
    key = str(form.get("calendar", ""))
    held = registry.clock.of(world_id)
    if not key:
        registry.clock.dated(world_id, "", 0, "")
        return
    found = next((c for c in registry.calendars(world_id) if c.key == key), None)
    values = [str(form.get(f, "")) for f in ("cday", "cmonth", "cyear")]
    if found is None or not all(v.isdigit() for v in values):
        raise ClockError("clock.error.bad_date")
    day, month, year = (int(v) for v in values)
    if not found.has(year, month, day):
        raise ClockError("clock.error.bad_date")
    today = found.index(year, month, day)
    era = " ".join(str(form.get("era", "")).split())[:12]
    registry.clock.dated(world_id, key, today - held.minute // DAY, era)


_UNIT_MINUTES = {"minute": 1, "hour": 60, "day": DAY}


def _minutes(form: FormData) -> int:
    """The amount and unit of a clock form, in minutes (0 when unreadable)."""
    amount = str(form.get("amount", "")).strip()
    unit = _UNIT_MINUTES.get(str(form.get("unit", "")), 0)
    return int(amount) * unit if amount.isdigit() else 0


def _shop(page: Page, registry: StoreRegistry, world_id: str | None) -> Element | None:
    shown = _clock_world(registry, world_id)
    if shown is None:
        return None
    members = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.list()
        if pc.world_id == shown and not pc.in_wizard
    ]
    treasures = [(t.id, t.name) for t in registry.treasure.list(shown) if t.items]
    return shop_panel(page, shown, registry.shops.of(shown), members, treasures=treasures)


def _chase(page: Page, registry: StoreRegistry, world_id: str | None) -> Element | None:
    shown = _clock_world(registry, world_id)
    if shown is None:
        return None
    members = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.list()
        if pc.world_id == shown and not pc.in_wizard
    ]
    return chase_panel(page, shown, registry.chases.of(shown), members)


def _cards(page: Page, characters: CharacterService, world_id: str | None) -> list[Element]:
    return [
        party_card(
            page,
            m.sheet,
            owner=m.owner,
            world=None if world_id else m.world,
            world_filter=world_id or "",
        )
        for m in characters.party(world_id)
    ]


# -- the party overview ------------------------------------------------------------------


@router.get("/gm", response_class=HTMLResponse)
async def party(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
) -> HTMLResponse:
    """Every character at the table — or in one world — updating live."""
    chosen = worlds.find(world)
    world_id = chosen.id if chosen else None
    return html(
        party_page(
            page,
            _cards(page, characters, world_id),
            worlds=worlds.list(),
            world=chosen,
            combats=_combats(page, registry, world_id),
            checks=_checks(page, registry, characters, world_id),
            clock=_clock(page, registry, world_id),
            shop=_shop(page, registry, world_id),
            chase=_chase(page, registry, world_id),
        )
    )


# -- the world's clock -------------------------------------------------------------------


@router.get(f"{CLOCK_URL}/panel", response_class=HTMLResponse)
async def clock_fragment(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world: str = ""
) -> HTMLResponse:
    """The clock panel, refetched when the time or a timer changes."""
    return html(_clock(page, registry, world or None))


@router.post(f"{CLOCK_URL}/{{world_id}}/{{op}}", response_class=HTMLResponse)
async def change_clock(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    characters: CharactersDep,
    world_id: str,
    op: str,
) -> Response:
    """Move the clock (``advance``, ``set``), rest the party (``rest``) or start a
    reminder (``timer``)."""
    if worlds.find(world_id) is None:
        return redirect("/gm")
    form = await request.form()
    user = gm.user
    name = user.display_name or user.username
    clock = registry.clock
    flash: str | None = None
    try:
        if op == "advance":
            raw = str(form.get("minutes", "")).strip()
            minutes = int(raw) if raw.isdigit() else _minutes(form)
            why = " ".join(str(form.get("why", "")).split())[:60].replace(":", " ")
            clock.advance(world_id, minutes, user.username, name, why)
        elif op == "set":
            day = str(form.get("day", "")).strip()
            hours, _, mins = str(form.get("time", "")).partition(":")
            if not (day.isdigit() and hours.isdigit() and mins.isdigit()):
                raise ClockError("clock.error.bad_time")
            if int(day) < 1 or int(hours) > 23 or int(mins) > 59:
                raise ClockError("clock.error.bad_time")
            clock.set(
                world_id, (int(day) - 1) * DAY + int(hours) * 60 + int(mins), user.username, name
            )
        elif op == "rest":
            kind = "long" if form.get("kind") == "long" else "short"
            characters.rest_party(world_id, kind, Actor(name, username=user.username))
            if kind == "long":  # their companions, mounts and sidekicks rest too
                for pc in registry.gamestate.characters.list():
                    if pc.world_id == world_id:
                        registry.followers.rest(pc.id)
            clock.advance(
                world_id, 480 if kind == "long" else 60, user.username, name, f"{kind}_rest"
            )
        elif op == "timer":
            clock.start(world_id, "custom", str(form.get("label", "")), _minutes(form))
        elif op == "travel":
            _travel(registry, world_id, form, user.username, name)
        elif op == "calendar":
            _calendar(registry, world_id, form)
        elif op == "travel_clear":
            clock.keep(world_id, None)
        elif op == "travel_encounter":
            journey = clock.of(world_id).journey
            if journey is None or not journey.monsters:
                raise TravelError("travel.error.bad")
            if journey.encounter_id is None:
                where = journey.environment.partition("|")[0] or page.t("travel.road")
                made = registry.encounters.create(
                    page.t(
                        "travel.encounter_name",
                        where=where,
                        when=clock_label(page, journey.ended, world_id),
                    ),
                    world_id,
                )
                registry.encounters.set_monsters(made.id, journey.monsters)
                notes = journey.encounter
                registry.encounters.update(
                    made.id,
                    name=made.name,
                    scope=made.scope,
                    notes=notes,
                    rules=made.rules,
                    party=made.party,
                )
                clock.keep(world_id, journey.model_copy(update={"encounter_id": made.id}))
                journey = clock.of(world_id).journey
            if journey is not None and journey.encounter_id:
                return redirect(f"/gm/encounters/{journey.encounter_id}")
        elif op == "downtime":
            raw = str(form.get("days", "")).strip()
            party = [
                pc
                for pc in registry.gamestate.characters.list()
                if pc.world_id == world_id and not pc.in_wizard
            ]
            days = int(raw) if raw.isdigit() else 0
            registry.downtime.grant(party, days, world_id, user.username, name)
            characters.rest_party(world_id, "long", Actor(name, username=user.username))
            clock.advance(world_id, days * DAY, user.username, name, "downtime")
            for pc in party:
                registry.events.publish(pc.id, f"downtime-{days}")
        else:
            raise ClockError("clock.error.bad_time")
    except (ClockError, PlayError, TravelError, DowntimeError) as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world_id})}")
    panel = _clock(page, registry, world_id)
    return html(panel if flash is None else [alert(flash), panel])


@router.post(f"{CLOCK_URL}/{{world_id}}/timer/{{timer_id}}/stop", response_class=HTMLResponse)
async def stop_timer(
    request: Request,
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    world_id: str,
    timer_id: str,
) -> Response:
    """Put out a light, end a spell's timer or dismiss a reminder early."""
    registry.clock.stop(world_id, timer_id)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world_id})}")
    return html(_clock(page, registry, world_id))


# -- the chase --------------------------------------------------------------------------


@router.get(f"{CHASE_URL}/panel", response_class=HTMLResponse)
async def chase_fragment(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, world: str = ""
) -> HTMLResponse:
    """The chase panel, refetched when the chase changes."""
    return html(_chase(page, registry, world or None))


@router.post(f"{CHASE_URL}/{{world_id}}/{{op}}", response_class=HTMLResponse)
async def run_chase(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world_id: str,
    op: str,
) -> Response:
    """Start a chase (``start``), Dash (``dash``), end a turn (``turn``), the escape
    checks (``round``), the distance (``distance``), drop out (``drop``), or end it."""
    if worlds.find(world_id) is None:
        return redirect("/gm")
    form = await request.form()
    user = gm.user
    name = user.display_name or user.username
    chases = registry.chases
    flash: str | None = None
    index_text = str(form.get("index", ""))
    index = int(index_text) if index_text.isdigit() else -1
    try:
        if op == "start":
            runners = _chase_runners(registry, world_id, form)
            feet = feet_from(str(form.get("distance", "30")), page.u.metric)
            chases.start(
                world_id,
                runners,
                environment=str(form.get("environment", "urban")),
                distance=feet if feet is not None else -1,
                rules=registry.rules_of(world_id),
                by=user.username,
                by_name=name,
            )
        elif op == "dash":
            chases.dash(world_id, index, user.username, name)
        elif op == "turn":
            held = chases.of(world_id)
            environment = held.environment if held else "urban"
            rows, die = registry.chase_complications(world_id, environment)
            chases.end_turn(
                world_id,
                rows,
                die,
                user.username,
                name,
                registry.chase_translations(world_id, environment),
            )
        elif op == "round":
            sight = [int(str(v)) for v in form.getlist("sight") if str(v).isdigit()]
            edges = {
                int(key[4:]): Advantage.parse(str(value))
                for key, value in form.items()
                if key.startswith("adv_") and key[4:].isdigit()
            }
            chases.round_end(world_id, sight, edges, user.username, name)
        elif op == "distance":
            feet = feet_from(str(form.get("distance", "")), page.u.metric)
            chases.distance(world_id, feet if feet is not None else -1)
        elif op == "drop":
            chases.drop(world_id, index)
        elif op == "end":
            chases.end(world_id, "stopped", user.username, name)
        else:
            raise ChaseError("chase.error.bad")
    except (ChaseError, PlayError) as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world_id})}")
    panel = _chase(page, registry, world_id)
    return html(panel if flash is None else [alert(flash), panel])


def _chase_runners(registry: StoreRegistry, world_id: str, form: FormData) -> list[Runner]:
    """Who runs, from the start form: the characters given a side, with their sheet's
    numbers, and the creatures named on each side."""
    runners: list[Runner] = []
    characters = registry.characters
    for pc in registry.gamestate.characters.list():
        side = str(form.get(f"side_{pc.id}", ""))
        if pc.world_id != world_id or pc.in_wizard or side not in ("quarry", "pursuer"):
            continue
        sheet = characters.sheet(pc)
        stealth = next((s.value.value for s in sheet.skills if s.key == "stealth"), None)
        runners.append(
            Runner(
                name=pc.name or "?",
                side=side,
                character_id=pc.id,
                initiative_bonus=sheet.initiative.value or 0,
                con_mod=sheet.ability("con").modifier,
                stealth=stealth or 0,
                passive=sheet.passive_perception.value or 10,
            )
        )
    for side in ("quarry", "pursuer"):
        for line in str(form.get(side, "")).splitlines()[:20]:
            runners.extend(registry.chase_runner(world_id, line, side))
    return runners


# -- traps and hazards ------------------------------------------------------------------


def _trap(
    registry: StoreRegistry, world_id: str, category: str, name: str, source: str
) -> tuple[str, TrapEffect, list[tuple[str, str]], int] | None:
    """A trap of the world's books: its name, effect, the world's characters and their
    average level; None when the books lack it."""
    if category not in TRAP_CATEGORIES:
        return None
    try:
        entry = registry.references.get(category, source or "-", name, world_id)
    except ReferenceNotFound:
        return None
    party = [
        pc
        for pc in registry.gamestate.characters.list()
        if pc.world_id == world_id and not pc.in_wizard
    ]
    levels = [max(1, pc.total_level) for pc in party] or [1]
    return (
        str(entry.data.get("name", name)),
        read_trap(entry.data, registry.spell_data(world_id)),
        [(pc.id, pc.name or "?") for pc in party],
        round(sum(levels) / len(levels)),
    )


@router.get(f"{TRAPS_URL}/form", response_class=HTMLResponse)
async def trap_form(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
    category: str = "trap",
    name: str = "",
    source: str = "",
) -> HTMLResponse:
    """Whom a trap catches and what it does, to spring it."""
    found = _trap(registry, world, category, name, source) if worlds.find(world) else None
    if found is None:
        return html(alert(page.t("trap.error.unknown")))
    shown, effect, members, level = found
    return html(spring_form(page, world, category, shown, source, effect, members, level))


@router.post(f"{TRAPS_URL}/spring", response_class=HTMLResponse)
async def spring_trap(
    request: Request, gm: GamemasterDep, page: PageDep, worlds: WorldsDep, registry: RegistryDep
) -> Response:
    """Spring the trap on the ticked characters (``op=spring``), or call for one of its
    checks from them (``op=call:<skill>:<dc>``). ``op=ask`` lets the players roll its
    saves on their sheets; ``wait`` shows them coming in, ``apply`` springs it with them
    (rolling for whoever has not), ``cancel`` takes the calls back."""
    form = await request.form()
    world_id = str(form.get("world", ""))
    category, name = str(form.get("category", "")), str(form.get("name", ""))
    source = str(form.get("source", ""))
    found = _trap(registry, world_id, category, name, source) if worlds.find(world_id) else None
    if found is None:
        return html(alert(page.t("trap.error.unknown")))
    shown, effect, members, level = found
    known = {cid for cid, _ in members}
    chosen = [str(c) for c in form.getlist("character") if str(c) in known]
    user = gm.user
    by_name = user.display_name or user.username
    op = str(form.get("op", "spring"))
    calls = registry.gamestate.checks
    # Only the open calls of this world: a closed one (sprung or taken back) never
    # springs the trap again.
    waiting = [
        (n, call)
        for n in range(len(effect.saves))
        if (call := calls.find(str(form.get(f"call_{n}", "")))) is not None
        and call.open
        and call.world_id == world_id
    ]
    scaled = effect.at_level(level)
    result: Node = None
    if op == "wait" and waiting:
        return html(
            spring_form(
                page, world_id, category, shown, source, effect, members, level, waiting=waiting
            )
        )
    if op in ("wait", "apply") and not waiting:
        # The calls are gone (closed elsewhere): nothing is sprung without them.
        return html(spring_form(page, world_id, category, shown, source, effect, members, level))
    if op == "cancel":
        for _n, call in waiting:
            if call.open:
                registry.checks.close(call.id)
        return html(spring_form(page, world_id, category, shown, source, effect, members, level))
    if op == "apply" and waiting:
        # The players' saves as they rolled them; whoever has not rolled is rolled for.
        chosen = list(
            dict.fromkeys(cid for _n, call in waiting for cid in call.characters if cid in known)
        )
        totals = {(cid, n): total for n, call in waiting for cid, total in call.results.items()}
        for _n, call in waiting:
            if call.open:
                registry.checks.close(call.id)
        op = "spring"
    else:
        totals = {}
    if not chosen:
        result = alert(page.t("trap.error.no_one"))
    elif op == "ask":
        if scaled.attack is not None or not scaled.saves:
            result = alert(page.t("trap.error.ask"))
        else:
            asked = [
                (
                    n,
                    registry.checks.call(
                        world_id,
                        "skill" if save.skill else "save",
                        save.skill or save.ability,
                        chosen,
                        user.username,
                        dc=save.dc,
                        purpose=shown,
                    ),
                )
                for n, save in enumerate(scaled.saves)
                if not save.on_hit
            ]
            return html(
                spring_form(
                    page, world_id, category, shown, source, effect, members, level, waiting=asked
                )
            )
        result = alert(page.t("trap.error.no_one"))
    elif op.startswith("call:"):
        _call, skill, dc = op.split(":", 2)
        try:
            registry.checks.call(
                world_id,
                "skill",
                skill,
                chosen,
                user.username,
                dc=int(dc) if dc.isdigit() else None,
            )
            result = notice(page.t("trap.called", skill=page.t(f"skill.{skill}")))
        except CheckError as exc:
            result = alert(page.t.error(exc))
    else:
        characters = registry.characters
        targets = []
        for cid in chosen:
            pc = registry.gamestate.characters.find(cid)
            if pc is None:
                continue
            sheet = characters.sheet(pc)
            targets.append(
                Target(
                    cid,
                    pc.name or "?",
                    ac=sheet.armor_class.value or 10,
                    resist=tuple(sheet.defended("resist")),
                    immune=tuple(sheet.defended("immune")),
                    vulnerable=tuple(sheet.defended("vulnerable")),
                )
            )
        caught = registry.traps.spring(
            world_id, shown, scaled, targets, user.username, by_name, totals=totals
        )
        result = spring_result(page, shown, caught)
    return html(
        spring_form(page, world_id, category, shown, source, effect, members, level, result=result)
    )


# -- the shop ---------------------------------------------------------------------------


@router.post(f"{SHOP_URL}/{{world_id}}/{{op}}", response_class=HTMLResponse)
async def change_shop(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world_id: str,
    op: str,
) -> Response:
    """Open or change the world's shop (``open``), close it (``close``), or stock its
    magic items: one by name (``add``), rolled by rarity (``roll``), a treasure's items
    (``treasure``), repriced (``price``) or taken off (``remove``)."""
    if worlds.find(world_id) is None:
        return redirect("/gm")
    form = await request.form()
    user = gm.user
    name = user.display_name or user.username
    flash: str | None = None
    try:
        if op == "open":
            members = {
                pc.id for pc in registry.gamestate.characters.list() if pc.world_id == world_id
            }

            def number(key: str, default: int) -> int:
                raw = str(form.get(key, "")).strip()
                return int(raw) if raw.isdigit() else default

            registry.shops.open(
                world_id,
                name=str(form.get("name", "")),
                characters=[str(c) for c in form.getlist("characters") if str(c) in members],
                markup=number("markup", 100),
                buys=number("buys", 50),
                by=user.username,
                by_name=name,
            )
        elif op == "add":
            world = worlds.find(world_id)
            found = (
                registry.compendium.resolver()
                .resolve_parts(
                    "item",
                    " ".join(str(form.get("item", "")).split()),
                    None,
                    world.source_set if world else None,
                )
                .entry
            )
            if found is None:
                raise ShopError("shop.error.unknown_item")
            item_name, source = str(found.name), str(found.source)
            raw_price = str(form.get("price", "")).strip()
            cost = (
                int(raw_price) * 100
                if raw_price.isdigit()
                else registry.magic_price(world_id, item_name, source)
            )
            registry.shops.stock(world_id, [(f"{item_name}|{source}", cost, _count(form))])
        elif op == "roll":
            rarity = str(form.get("rarity", ""))
            if rarity not in RARITIES:
                raise ShopError("shop.error.prices")
            many = _count(form) or 1
            registry.shops.stock(
                world_id,
                [(ref, cost, 1) for ref, cost in registry.magic_roll(world_id, rarity, many)],
            )
        elif op == "treasure":
            hoard = registry.gamestate.treasures.find(str(form.get("treasure", "")))
            if hoard is None or hoard.scope.world_id != world_id:
                raise ShopError("shop.error.unknown_item")
            registry.shops.stock(
                world_id,
                [
                    (i.ref, registry.magic_price(world_id, i.name, i.source), i.count)
                    for i in hoard.items
                ],
            )
        elif op in ("price", "remove"):
            raw = str(form.get("index", ""))
            index = int(raw) if raw.isdigit() else -1
            if op == "remove":
                registry.shops.unstock(world_id, index)
            else:
                raw_price = str(form.get("price", "")).strip()
                registry.shops.reprice(
                    world_id,
                    index,
                    int(raw_price) * 100 if raw_price.isdigit() else -1,
                    _count(form),
                )
        elif op == "close":
            registry.shops.close(world_id, user.username, name)
        else:
            raise ShopError("shop.error.prices")
    except ShopError as exc:
        flash = page.t.error(exc)
    for pc in registry.gamestate.characters.list():
        if pc.world_id == world_id:
            registry.events.publish(pc.id, f"shop-{datetime.now(UTC).isoformat()}")
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world_id})}")
    panel = _shop(page, registry, world_id)
    return html(panel if flash is None else [alert(flash), panel])


def _count(form: FormData) -> int | None:
    """A shop form's count: None for no end of them (left empty)."""
    raw = str(form.get("count", "")).strip()
    return max(1, min(999, int(raw))) if raw.isdigit() else None


# -- checks outside a fight --------------------------------------------------------------


@router.get(f"{CHECKS_URL}/panel", response_class=HTMLResponse)
async def checks_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
) -> HTMLResponse:
    """The call panel with its results, refetched as answers come in."""
    chosen = worlds.find(world)
    return html(_checks(page, registry, characters, chosen.id if chosen else None))


@router.post(CHECKS_URL, response_class=HTMLResponse)
async def call_check(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    registry: RegistryDep,
) -> Response:
    """Ask the ticked characters for a check, or roll it secretly for them."""
    form = await request.form()
    chosen = worlds.find(str(form.get("world", "")))
    world_id = chosen.id if chosen else None
    kind, _, subject = str(form.get("what", "")).partition(":")
    dc_text = str(form.get("dc", "")).strip()
    flash: str | None = None
    try:
        registry.checks.call(
            world_id,
            kind,
            subject,
            [str(c) for c in form.getlist("character")],
            gm.user.username,
            dc=int(dc_text) if dc_text.isdigit() else None,
            group=form.get("group") == "1",
            secret=form.get("secret") == "1",
        )
    except CheckError as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world_id})}" if world_id else "/gm")
    panel = _checks(page, registry, characters, world_id)
    return html(panel if flash is None else [alert(flash), panel])


@router.post(f"{CHECKS_URL}/{{call_id}}/close", response_class=HTMLResponse)
async def close_check(
    request: Request,
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    call_id: str,
    world: str = "",
) -> Response:
    """The gamemaster is done with a call."""
    registry.checks.close(call_id)
    chosen = worlds.find(world)
    world_id = chosen.id if chosen else None
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world_id})}" if world_id else "/gm")
    return html(_checks(page, registry, characters, world_id))


@router.get("/gm/party/combats", response_class=HTMLResponse)
async def party_combats_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
) -> HTMLResponse:
    """Just the running fights."""
    chosen = worlds.find(world)
    return html(_combats(page, registry, chosen.id if chosen else None))


@router.get("/gm/party", response_class=HTMLResponse)
async def party_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    world: str = "",
) -> HTMLResponse:
    """Just the grid."""
    chosen = worlds.find(world)
    world_id = chosen.id if chosen else None
    return html(party_grid(page, _cards(page, characters, world_id), world_id=world_id))


@router.get("/gm/party/{character_id}/card", response_class=HTMLResponse)
async def party_card_fragment(
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    character_id: str,
    world: str = "",
) -> HTMLResponse:
    """One character's card."""
    member = characters.party_member(character_id)
    return html(
        party_card(
            page,
            member.sheet,
            owner=member.owner,
            world=None if world else member.world,
            world_filter=world,
        )
    )


@router.post("/gm/party/{character_id}/act/{action}", response_class=HTMLResponse)
async def party_action(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    character_id: str,
    action: str,
) -> Response:
    """Apply a sheet action from the overview and return that character's new card.

    The same rules as on the sheet — temporary HP first, concentration saves, death
    saves — and the log names the gamemaster as the one who acted. Every other screen
    showing the character updates through its live stream.
    """
    params = {k: str(v) for k, v in (await request.form()).items()}
    world = params.pop("world", "")
    started = datetime.now(UTC)  # what the action logs after this, it rolled
    flash: str | None = None
    try:
        characters.act(gm, character_id, action, params)
    except PlayError as exc:
        flash = page.t.error(exc)
    if request.headers.get("HX-Request") != "true":
        return redirect(f"/gm?{urlencode({'world': world})}" if world else "/gm")
    member = characters.party_member(character_id)
    response = html(
        party_card(
            page,
            member.sheet,
            owner=member.owner,
            world=None if world else member.world,
            world_filter=world,
            flash=flash,
        )
    )
    rolled = new_roll(characters.rolled_since(member.sheet.character, started))
    return announce_roll(response, rolled.outcome) if rolled else response


@router.get("/gm/events")
async def party_events(
    request: Request, _gm: GamemasterDep, registry: RegistryDep
) -> StreamingResponse:
    """Every character change, as ``"<id> <revision>"`` (an empty revision: deleted).

    A combat's change arrives as ``"*combats <id> <revision>"``.
    """
    return StreamingResponse(
        revision_stream(registry.events, PARTY, None, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- worlds ------------------------------------------------------------------------------


@router.get(WORLDS_URL, response_class=HTMLResponse)
async def list_worlds(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    created: str = "",
    updated: str = "",
    deleted: int | None = None,
) -> HTMLResponse:
    """Every world."""
    t = page.t
    notice_text = None
    for world_id, key in ((created, "worlds.created"), (updated, "worlds.updated")):
        found = worlds.find(world_id) if world_id else None
        if found is not None:
            notice_text = t(key, name=found.name)
    if deleted is not None:
        notice_text = t("worlds.deleted")
    all_worlds = worlds.list()
    counts = {w.id: worlds.character_count(w.id) for w in all_worlds}
    return html(worlds_page(page, all_worlds, counts, notice_text=notice_text))


def _languages(registry: StoreRegistry) -> list[tuple[str, str]]:
    """The languages a world may be written in: those Paravent speaks."""
    return [(lang.code, lang.name) for lang in registry.ui.languages]


def _language(registry: StoreRegistry, code: str) -> str:
    """The language a form chose, English when it names none Paravent speaks."""
    return code if registry.ui.supports(code) else "en"


def _groups(registry: StoreRegistry) -> dict[str, str]:
    """Every loaded book's picker group, by code."""
    return {b.code.upper(): b.group for b in registry.books.books}


def _world_input(
    registry: StoreRegistry,
    name: str,
    description: str,
    sources: list[str] | None,
    players: list[str] | None,
    rerolls: str,
    language: str,
    encumbrance: str = "",
    milestone: str = "",
) -> WorldInput:
    """What the world form posted; the players' ticks stored only where they differ from
    the defaults."""
    declared = tuple(sources or ())
    chosen = player_choice(players or (), declared, _groups(registry))
    return WorldInput(
        name,
        description,
        declared,
        allow_rerolls=bool(rerolls),
        variant_encumbrance=bool(encumbrance),
        milestone=bool(milestone),
        language=_language(registry, language),
        player_sources=tuple(chosen) if chosen is not None else None,
    )


@router.get(f"{WORLDS_URL}/new", response_class=HTMLResponse)
async def new_world(_gm: GamemasterDep, page: PageDep, registry: RegistryDep) -> HTMLResponse:
    """The empty world form."""
    return html(
        world_form_page(
            page,
            registry.books,
            world=None,
            name="",
            description="",
            sources=(),
            players=player_ticks(None, _groups(registry)),
            language=page.lang,
            languages=_languages(registry),
        )
    )


@router.post(WORLDS_URL, response_class=HTMLResponse)
async def create_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    sources: Annotated[list[str] | None, Form()] = None,
    players: Annotated[list[str] | None, Form()] = None,
    rerolls: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    encumbrance: Annotated[str, Form()] = "",
    milestone: Annotated[str, Form()] = "",
) -> Response:
    """Create a world, or show the form again with the reason it was refused."""
    data = _world_input(
        registry, name, description, sources, players, rerolls, language, encumbrance, milestone
    )
    try:
        world = worlds.create(data)
    except AuthError as exc:
        view = world_form_page(
            page,
            registry.books,
            world=None,
            name=name,
            description=description,
            sources=data.sources,
            players=frozenset(p.upper() for p in players or ()),
            rerolls=data.allow_rerolls,
            encumbrance=data.variant_encumbrance,
            milestone=data.milestone,
            language=data.language,
            languages=_languages(registry),
            error=page.t.error(exc),
        )
        return html(view, status_code=422)
    return redirect(f"{WORLDS_URL}/{world.id}?created=1")


@router.get(f"{WORLDS_URL}/{{world_id}}/edit", response_class=HTMLResponse)
async def edit_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    world_id: str,
    uploaded: str = "",
    error: str = "",
) -> HTMLResponse:
    """The world form, filled in, with the world's images below it."""
    world = worlds.get(world_id)
    notice_text, message = upload_messages(page, uploaded, error)
    images = images_panel(
        page,
        world.id,
        world.cover,
        registry.adventures.images(world.id),
        f"{WORLDS_URL}/{world.id}/edit",
    )
    return html(
        world_form_page(
            page,
            registry.books,
            world=world,
            name=world.name,
            description=world.description,
            sources=world.source_set.sources,
            players=player_ticks(world, _groups(registry)),
            rerolls=world.allow_rerolls,
            encumbrance=world.variant_encumbrance,
            milestone=world.milestone,
            language=world.language,
            languages=_languages(registry),
            extra=[notice(notice_text), alert(message), images],
        )
    )


@router.post(f"{WORLDS_URL}/{{world_id}}", response_class=HTMLResponse)
async def update_world(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    worlds: WorldsDep,
    world_id: str,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    sources: Annotated[list[str] | None, Form()] = None,
    players: Annotated[list[str] | None, Form()] = None,
    rerolls: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    encumbrance: Annotated[str, Form()] = "",
    milestone: Annotated[str, Form()] = "",
) -> Response:
    """Save a world's name, description and books."""
    world = worlds.get(world_id)
    data = _world_input(
        registry, name, description, sources, players, rerolls, language, encumbrance, milestone
    )
    try:
        worlds.update(world_id, data)
    except AuthError as exc:
        view = world_form_page(
            page,
            registry.books,
            world=world,
            name=name,
            description=description,
            sources=data.sources,
            players=frozenset(p.upper() for p in players or ()),
            rerolls=data.allow_rerolls,
            encumbrance=data.variant_encumbrance,
            milestone=data.milestone,
            language=data.language,
            languages=_languages(registry),
            error=page.t.error(exc),
        )
        return html(view, status_code=422)
    return redirect(f"{WORLDS_URL}/{world_id}?updated=1")


@router.get(f"{WORLDS_URL}/{{world_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_world(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, world_id: str
) -> HTMLResponse:
    """Ask first, saying what happens to the world's characters."""
    world = worlds.get(world_id)
    return html(world_delete_page(page, world, worlds.character_count(world_id)))


@router.post(f"{WORLDS_URL}/{{world_id}}/delete", response_class=HTMLResponse)
async def delete_world(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, world_id: str
) -> Response:
    """Delete a world; its characters stay, without one."""
    world = worlds.get(world_id)
    try:
        moved = worlds.delete(world_id)
    except AuthError as exc:
        view = world_delete_page(
            page, world, worlds.character_count(world_id), error=page.t.error(exc)
        )
        return html(view, status_code=409)
    return redirect(f"{WORLDS_URL}?deleted={moved}")


@router.post("/gm/characters/{character_id}/world")
async def place_character(
    _gm: GamemasterDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    character_id: str,
    world: Annotated[str, Form()] = "",
) -> Response:
    """Put a character in a world, or in none."""
    characters.get_any(character_id)
    worlds.place(character_id, world or None)
    return redirect("/gm/characters")


__all__ = ["router"]
