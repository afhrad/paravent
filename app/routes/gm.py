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

from fastapi import APIRouter, Form, HTTPException, Request
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
from app.models.gamestate import EncounterMonster
from app.routes.adventures import upload_messages
from app.routes.auth import html, redirect, safe_next
from app.routes.rolls import announce_roll, new_roll
from app.services import character_rules, features
from app.services.activities import RARITIES
from app.services.auth import AuthError
from app.services.characters import CharacterService
from app.services.chase import ChaseError, Runner
from app.services.checks import CheckError
from app.services.clock import DAY, ClockError, bastions_due
from app.services.current_world import world_of_page
from app.services.delve import DelveError
from app.services.dice import Advantage
from app.services.downtime import DowntimeError
from app.services.events import PARTY, revision_stream
from app.services.hazards import KINDS, Hazard, HazardError, Scale, party_level, scale
from app.services.hazards import effect as hazard_effect
from app.services.madness import rolls_2014
from app.services.play import Actor, PlayError
from app.services.player_view import player_choice, player_ticks
from app.services.references import ReferenceNotFound
from app.services.shop import ShopError
from app.services.table import TableError
from app.services.traps import Target, TrapEffect, read_trap
from app.services.travel import (
    ABUNDANCE,
    CLIMATES,
    PACES,
    TRAVEL_DAY,
    WATER,
    Plan,
    TravelError,
    Vessel,
    terrain_of,
)
from app.services.units import feet_from
from app.services.upkeep import UpkeepError, priced
from app.services.worlds import WorldInput
from app.store.encounter_tables import Environment
from app.store.registry import StoreRegistry
from app.ui.adventure_pages import images_panel
from app.ui.chase_pages import CHASE_URL, chase_panel
from app.ui.check_pages import CHECKS_URL, checks_panel
from app.ui.clock_pages import CLOCK_URL, clock_label, clock_panel, spotting
from app.ui.combat_pages import party_combats
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.gm_pages import (
    NEWS_SEEN_URL,
    WORLDS_URL,
    news_panel,
    party_card,
    party_grid,
    party_page,
    playing_panel,
    world_delete_page,
    world_form,
    world_form_page,
)
from app.ui.hazard_pages import hazard_form, hazard_tool
from app.ui.madness_pages import madness_tool
from app.ui.nav import WORLD_SECTIONS, world_section_of
from app.ui.shop_pages import SHOP_URL, shop_panel
from app.ui.trap_pages import (
    HAZARDS_URL,
    IMPROVISED,
    TRAP_CATEGORIES,
    TRAPS_URL,
    spring_form,
    spring_result,
)
from app.ui.upkeep_pages import UPKEEP_URL, upkeep_dialog_url, upkeep_tool
from app.ui.xp_pages import XP_DONE, XP_URL, xp_tool

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
    """The world whose clock the party view shows: the one named, else the table's."""
    return world_id if world_id is not None else registry.table.world_id()


def _clock(page: Page, registry: StoreRegistry, world_id: str | None) -> Element | None:
    shown = _clock_world(registry, world_id)
    if shown is None:
        return None
    clock = registry.clock.of(shown)
    members = registry.gamestate.characters.of_world(shown, drafts=False)
    names = {pc.id: pc.name or "?" for pc in members}
    built = [(pc.name or "?", registry.bastions.of(pc.id)) for pc in members if pc.total_level >= 5]
    due = bastions_due(clock.minute, [(name, b.last_turn) for name, b in built if b.facilities])
    table = registry.encounter_tables.get(clock.delve.environment) if clock.delve else None
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
        mounted=registry.feature(shown, "followers")
        and registry.followers.mounted([pc.id for pc in members]),
        hazards=registry.hazard_links(shown),
        terrains=registry.terrains(shown),
        abundance=registry.foraging_by_abundance(shown),
        rules=registry.rules_of(shown),
        weather=registry.weather(shown) is not None,
        calendars=registry.calendars(shown),
        calendar=clock.calendar,
        era=clock.calendar_era,
        rests=_rest_minutes(registry, shown),
        delve=clock.delve,
        delve_table=table.name if table is not None else "",
        features=features.switched_on(registry.gamestate.worlds.find(shown)),
    )


def _rest_minutes(registry: StoreRegistry, world_id: str) -> tuple[int, int]:
    """How long a party's short and long rest take in the world (its rest variant)."""
    world = registry.gamestate.worlds.find(world_id)
    rules = world.character_rules if world is not None else []
    return character_rules.rest_minutes(rules, "short"), character_rules.rest_minutes(rules, "long")


def _environments(registry: StoreRegistry, world_id: str) -> list[Environment]:
    """The random-encounter tables of a world's books (every table for a world that
    declares none)."""
    world = registry.gamestate.worlds.find(world_id)
    tables = registry.encounter_tables.all()
    if world is None or world.source_set.is_unrestricted:
        return tables
    books = set(world.source_set.normalized)
    return [e for e in tables if e.source.lower() in books]


def _encounter_from(
    page: Page,
    registry: StoreRegistry,
    world_id: str,
    where: str,
    minute: int,
    monsters: list[EncounterMonster],
    notes: list[str],
    name_key: str = "travel.encounter_name",
) -> str:
    """An encounter in the world made from a check that came up on the road or while
    exploring: named for where and when, its creatures planned, the row in its notes."""
    made = registry.encounters.create(
        page.t(name_key, where=where, when=clock_label(page, minute, world_id)),
        world_id,
    )
    registry.encounters.set_monsters(made.id, monsters)
    registry.encounters.update(
        made.id,
        name=made.name,
        scope=made.scope,
        notes="\n\n".join(x for x in notes if x),
        rules=made.rules,
        party=made.party,
    )
    return made.id


def _travel(registry: StoreRegistry, world_id: str, form: FormData, by: str, by_name: str) -> None:
    """Set out on the road with the travel form's plan."""
    amount = str(form.get("amount", "")).strip()
    hours = (
        int(amount) * (TRAVEL_DAY if form.get("unit") == "day" else 1) if amount.isdigit() else 0
    )
    pace = str(form.get("pace", "normal"))
    checks = str(form.get("checks", "1"))
    environment = registry.encounter_tables.get(str(form.get("environment", "")))
    by_ship = str(form.get("vessel", ""))
    vessel = next((v for v in registry.vessels(world_id) if v.name == by_ship), None)
    if vessel is not None and form.get("around") == "1":
        vessel = Vessel(vessel.name, vessel.mph, around_the_clock=True)
    water = str(form.get("water", "enough"))
    climate = str(form.get("climate", ""))
    # The terrain chosen, else the one the encounter table's environment travels through.
    abundance = str(form.get("abundance", "limited"))
    terrain = registry.terrain(
        world_id,
        str(form.get("terrain", "")) or (terrain_of(environment.name) if environment else ""),
        abundance if abundance in ABUNDANCE else "limited",
    )
    party = {pc.id for pc in registry.gamestate.characters.of_world(world_id, drafts=False)}
    navigator = str(form.get("navigator", ""))
    # Without survival the party needs no rations or water and nobody forages.
    survival = registry.feature(world_id, "survival")
    registry.travel.travel(
        world_id,
        Plan(
            hours=hours,
            pace=pace if pace in PACES else "normal",  # type: ignore[arg-type]
            difficult=form.get("difficult") == "1",
            environment=environment,
            checks=int(checks) if checks.isdigit() and int(checks) <= 3 else 1,
            rations=survival and form.get("rations") == "1",
            rest=form.get("rest") == "1",
            water=water if survival and water in WATER else "enough",  # type: ignore[arg-type]
            mounted=by_ship == "mounted",
            vessel=vessel,
            night_check=form.get("night") == "1",
            weather=form.get("weather") == "1",
            climate=climate if climate in CLIMATES else "",  # type: ignore[arg-type]
            altitude=form.get("altitude") == "1",
            terrain=terrain,
            navigator=navigator if navigator in party else "",
            helped=form.get("helped") == "1",
            foragers=tuple(str(f) for f in form.getlist("forager") if str(f) in party)
            if survival
            else (),
            stealthy=form.get("stealthy") == "1",
        ),
        rules=registry.rules_of(world_id),
        resolve=registry.monster_resolve(world_id),
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
    if shown is None or not registry.feature(shown, "shops"):
        return None
    members = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.of_world(shown, drafts=False)
    ]
    treasures = [(t.id, t.name) for t in registry.treasure.list(shown) if t.items]
    return shop_panel(page, shown, registry.shops.of(shown), members, treasures=treasures)


def _chase(page: Page, registry: StoreRegistry, world_id: str | None) -> Element | None:
    shown = _clock_world(registry, world_id)
    if shown is None or not registry.feature(shown, "chases"):
        return None
    members = [
        (pc.id, pc.name or "?")
        for pc in registry.gamestate.characters.of_world(shown, drafts=False)
    ]
    return chase_panel(page, shown, registry.chases.of(shown), members)


def _playing(page: Page, registry: StoreRegistry, world_id: str | None) -> Element:
    """The adventures and chapters set running, in the world shown (or every world)."""
    adventures = registry.adventures
    shown = _clock_world(registry, world_id)
    worlds = [w for w in registry.gamestate.worlds.list() if shown is None or w.id == shown]
    playing = []
    for world in worlds:
        for adventure in adventures.adventures(world.id):
            every = adventures.chapters(adventure.id)
            chapters = [c for c in every if c.status == "running"]
            if adventure.status == "running" or chapters:
                # With no chapter running, the next one still planned, ready to start.
                upcoming = (
                    next((c for c in every if c.status == "planned"), None)
                    if not chapters
                    else None
                )
                playing.append((world.name, adventure, chapters, upcoming))
    return playing_panel(page, playing, world_id=shown)


def _card_world(registry: StoreRegistry, name: str | None, world_filter: str) -> str | None:
    """The world a card names: only on the overview of every world, and only where the
    table's characters are in more than one."""
    if world_filter:
        return None
    worlds = {pc.world_id for pc in registry.gamestate.characters.list() if not pc.in_wizard}
    return name if len(worlds) > 1 else None


def _cards(page: Page, characters: CharacterService, world_id: str | None) -> list[Element]:
    party = characters.party(world_id)
    # A card names its world only where the table shows more than one.
    several = len({m.world for m in party}) > 1
    return [
        party_card(
            page,
            m.sheet,
            owner=m.owner,
            world=m.world if several and not world_id else None,
            world_filter=world_id or "",
        )
        for m in party
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
    upkeep: str = "",
) -> HTMLResponse:
    """Every character at the table, updating live, in the table's world
    (documentation/CURRENT-WORLD.md); ``world`` names another, as its own forms do."""
    chosen = worlds.find(world) or registry.table.world()
    world_id = chosen.id if chosen else None
    members = _party_names(characters, world_id)
    return html(
        party_page(
            page,
            _cards(page, characters, world_id),
            world=chosen,
            combats=_combats(page, registry, world_id),
            checks=_checks(page, registry, characters, world_id),
            clock=_clock(page, registry, world_id),
            shop=_shop(page, registry, world_id),
            chase=_chase(page, registry, world_id),
            playing=_playing(page, registry, world_id),
            news=news_panel(page, registry.news.unseen(registry.places.get(_gm.user.id).news_seen)),
            xp=xp_tool(page, chosen, _party_names(characters, world_id)),
            madness=madness_tool(
                page,
                chosen,
                _party_names(characters, world_id),
                tables=rolls_2014(lambda c, b: registry.book_table(world_id, c, b)),
            )
            if registry.feature(world_id, "madness")
            else None,
            hazard=hazard_tool(page, world_id, *_scale(registry, world_id))
            if world_id is not None and registry.feature(world_id, "hazards") and members
            else None,
            upkeep=upkeep_tool(
                page,
                world_id,
                registry.upkeep.of_world(world_id),
                dict(members),
                registry.upkeep_costs(world_id),
                refused=upkeep == "bad",
            )
            if world_id is not None and registry.feature(world_id, "upkeep")
            else None,
        )
    )


def _party_names(characters: CharacterService, world_id: str | None) -> list[tuple[str, str]]:
    return [
        (m.sheet.character.id, m.sheet.character.name or "?")
        for m in characters.party(world_id)
        if not m.sheet.character.in_wizard
    ]


@router.post(XP_URL)
async def award_xp(
    request: Request,
    gm: GamemasterDep,
    characters: CharactersDep,
    worlds: WorldsDep,
) -> Response:
    """Experience for a challenge that was no fight, to the characters ticked: each the
    amount, or the amount shared out evenly (rounded down); logged with its reason."""
    form = await request.form()
    world = worlds.find(str(form.get("world", "")))
    present = dict(_party_names(characters, world.id if world else None))
    chosen = [str(c) for c in form.getlist("to") if str(c) in present]
    raw = str(form.get("xp", ""))
    amount = int(raw) if raw.isdigit() else 0
    if world is None or world.milestone or amount < 1 or not chosen:
        return redirect("/gm?tool=xp")
    each = amount // len(chosen) if form.get("how") == "split" else amount
    reason = " ".join(str(form.get("reason", "")).split())[:200]
    by = gm.user.display_name or gm.user.username
    for character_id in chosen:
        characters.award_xp(character_id, min(each, 1_000_000), reason, by)
    return redirect(f"/gm?{urlencode({XP_DONE: f'{each}:{len(chosen)}'})}")


@router.post(NEWS_SEEN_URL)
async def news_seen(
    _gm: GamemasterDep, registry: RegistryDep, at: Annotated[str, Form()] = ""
) -> Response:
    """The gamemaster has read what's new up to ``at``; it shows no more."""
    if at:
        registry.places.mark_news(_gm.user.id, at)
    return redirect("/gm")


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
    needs = {"travel": "travel", "travel_encounter": "travel", "downtime": "downtime"}
    if op.startswith("delve"):
        needs[op] = "dungeon"
    try:
        if op in needs and not registry.feature(world_id, needs[op]):
            raise ClockError("clock.error.switched_off")
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
                for pc in registry.gamestate.characters.of_world(world_id):
                    registry.followers.rest(pc.id)
            rested = registry.gamestate.worlds.find(world_id)
            rules = rested.character_rules if rested is not None else []
            clock.advance(
                world_id, character_rules.rest_minutes(rules, kind), user.username, name,
                f"{kind}_rest",
            )  # fmt: skip
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
                made_id = _encounter_from(
                    page,
                    registry,
                    world_id,
                    journey.environment.partition("|")[0] or page.t("travel.road"),
                    journey.ended,
                    journey.monsters,
                    [journey.encounter, *spotting(page, journey)],
                )
                clock.keep(world_id, journey.model_copy(update={"encounter_id": made_id}))
                journey = clock.of(world_id).journey
            if journey is not None and journey.encounter_id:
                return redirect(f"/gm/encounters/{journey.encounter_id}")
        elif op == "delve":
            every = str(form.get("every", "")).strip()
            on = str(form.get("on", "")).strip()
            registry.delve.start(
                world_id,
                str(form.get("environment", "")),
                int(every) if every.isdigit() else 0,
                int(on) if on.isdigit() else 0,
                user.username,
                name,
            )
        elif op == "delve_check":
            registry.delve.check(world_id, user.username, name)
        elif op == "delve_leave":
            registry.delve.leave(world_id, user.username, name)
        elif op == "delve_clear":
            registry.delve.dismiss(world_id)
        elif op == "delve_encounter":
            delve = registry.delve.of(world_id)
            found = delve.found if delve is not None else None
            if delve is None or found is None or not found.monsters:
                raise DelveError("delve.error.bad")
            if found.encounter_id is None:
                table = registry.encounter_tables.get(delve.environment)
                made_id = _encounter_from(
                    page,
                    registry,
                    world_id,
                    table.name if table is not None else page.t("delve.dungeon"),
                    found.at,
                    found.monsters,
                    [found.encounter],
                    "delve.encounter_name",
                )
                registry.delve.made(world_id, made_id)
                return redirect(f"/gm/encounters/{made_id}")
            return redirect(f"/gm/encounters/{found.encounter_id}")
        elif op == "downtime":
            raw = str(form.get("days", "")).strip()
            party = registry.gamestate.characters.of_world(world_id, drafts=False)
            days = int(raw) if raw.isdigit() else 0
            registry.downtime.grant(party, days, world_id, user.username, name)
            characters.rest_party(world_id, "long", Actor(name, username=user.username))
            clock.advance(world_id, days * DAY, user.username, name, "downtime")
            for pc in party:
                registry.events.publish(pc.id, f"downtime-{days}")
        else:
            raise ClockError("clock.error.bad_time")
    except (ClockError, PlayError, TravelError, DowntimeError, DelveError) as exc:
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


def _scale(registry: StoreRegistry, world_id: str) -> tuple[Scale | None, int]:
    """The world's guide's prices for harm at its party's level, and that level."""
    party = registry.gamestate.characters.of_world(world_id, drafts=False)
    level = party_level([pc.total_level for pc in party])
    rules = registry.rules_of(world_id)
    return scale(rules, level, lambda c, b: registry.book_table(world_id, c, [b])), level


def _trap(
    registry: StoreRegistry, world_id: str, category: str, name: str, source: str
) -> tuple[str, TrapEffect, list[tuple[str, str]], int] | None:
    """A trap of the world's books, or a hazard improvised on the party view (its choice
    in ``source``): its name, effect, the world's characters and their average level;
    None when the books lack it, the hazard does not read or the world plays without
    hazards."""
    if category == IMPROVISED:
        if not registry.feature(world_id, "hazards"):
            return None
        party = registry.gamestate.characters.of_world(world_id, drafts=False)
        scaled, level = _scale(registry, world_id)
        try:
            made = hazard_effect(Hazard.from_spec(source), scaled)
        except HazardError:
            return None
        return name, made, [(pc.id, pc.name or "?") for pc in party], level
    if category not in TRAP_CATEGORIES:
        return None
    try:
        entry = registry.references.get(category, source or "-", name, world_id)
    except ReferenceNotFound:
        return None
    party = registry.gamestate.characters.of_world(world_id, drafts=False)
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


@router.post(f"{UPKEEP_URL}/{{world_id}}/{{op}}")
async def change_upkeep(
    request: Request,
    gm: GamemasterDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world_id: str,
    op: str,
) -> Response:
    """Give a character of the world a property (``give``: priced by the guide's table,
    or by hand in gold) or take one back (``remove``); back to the dialog."""
    if worlds.find(world_id) is None or not registry.feature(world_id, "upkeep"):
        raise HTTPException(status_code=404)
    form = await request.form()
    user = gm.user
    if op == "give":
        kind = str(form.get("kind", ""))
        typed = str(form.get("gp", "")).strip().replace(",", ".")
        try:
            per_day = round(float(typed) * 100) if typed else None
        except ValueError:
            per_day = None
        if per_day is None:
            per_day = priced(registry.upkeep_costs(world_id), kind) if kind else None
        try:
            registry.upkeep.give(
                world_id,
                str(form.get("character", "")),
                str(form.get("name", "")),
                per_day if per_day is not None else -1,
                kind,
                user.username,
                user.display_name or user.username,
            )
        except UpkeepError:
            return redirect(f"{upkeep_dialog_url(world_id)}&upkeep=bad")
    elif op == "remove":
        registry.upkeep.remove(world_id, str(form.get("property", "")))
    else:
        raise HTTPException(status_code=404)
    return redirect(upkeep_dialog_url(world_id))


@router.get(f"{HAZARDS_URL}/form", response_class=HTMLResponse)
async def hazard_choice(
    _gm: GamemasterDep,
    page: PageDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
    fresh: str = "",
    kind: str = "collapse",
    severity: str = "",
    ability: str = "",
    type: str = "",  # noqa: A002 — the form field's name
    half: str = "",
    dc: str = "",
    dice: str = "",
    feet: str = "",
) -> HTMLResponse:
    """The hazard chosen, as the form that springs it; with ``fresh``, the choice again
    (for another hazard). A choice that does not work out comes back with the reason."""
    if worlds.find(world) is None or not registry.feature(world, "hazards"):
        return html(alert(page.t("trap.error.unknown")))
    scaled, level = _scale(registry, world)
    if fresh or kind not in KINDS:
        return html(hazard_form(page, world, scaled, level))
    suggested_type, suggested_save = KINDS[kind]
    hazard = Hazard(
        kind=kind,
        severity=severity,
        ability="" if ability == "none" else (ability or suggested_save),
        damage_type=type or suggested_type,
        half=half == "1",
        dc=int(dc) if dc.isdigit() else None,
        # German dice read "3W10"; the height is in the reader's units.
        dice=dice.replace(" ", "").lower().replace("w", "d"),
        feet=feet_from(feet, page.u.metric) or 0 if feet.strip() else 0,
    )
    try:
        made = hazard_effect(hazard, scaled)
    except HazardError as exc:
        return html(hazard_form(page, world, scaled, level, error=page.t(f"hazard.error.{exc}")))
    t = page.t
    shown = (
        t("hazard.fall_name", distance=page.u.distance(hazard.feet))
        if kind == "falling"
        else t(
            "hazard.name",
            kind=t(f"hazard.kind.{kind}"),
            severity=t(f"hazard.severity.{severity}") if severity and not dice else dice,
        )
    )
    party = registry.gamestate.characters.of_world(world, drafts=False)
    members = [(pc.id, pc.name or "?") for pc in party]
    return html(spring_form(page, world, IMPROVISED, shown, hazard.spec(), made, members, level))


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
            members = {pc.id for pc in registry.gamestate.characters.of_world(world_id)}

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
            ref = str(form.get("ref", ""))
            if op == "remove":
                registry.shops.unstock(world_id, index, ref)
            else:
                raw_price = str(form.get("price", "")).strip()
                registry.shops.reprice(
                    world_id,
                    index,
                    int(raw_price) * 100 if raw_price.isdigit() else -1,
                    _count(form),
                    ref,
                )
        elif op == "close":
            registry.shops.close(world_id, user.username, name)
        else:
            raise ShopError("shop.error.prices")
    except ShopError as exc:
        flash = page.t.error(exc)
    for pc in registry.gamestate.characters.of_world(world_id):
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


@router.post(f"{CHECKS_URL}/clear", response_class=HTMLResponse)
async def clear_checks(
    request: Request,
    _gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    worlds: WorldsDep,
    registry: RegistryDep,
    world: str = "",
) -> Response:
    """The list of calls starts afresh."""
    chosen = worlds.find(world)
    world_id = chosen.id if chosen else None
    registry.checks.clear(world_id)
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
    registry: RegistryDep,
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
            world=_card_world(registry, member.world, world),
            world_filter=world,
        )
    )


@router.post("/gm/party/{character_id}/act/{action}", response_class=HTMLResponse)
async def party_action(
    request: Request,
    gm: GamemasterDep,
    page: PageDep,
    characters: CharactersDep,
    registry: RegistryDep,
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
            world=_card_world(registry, member.world, world),
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


@router.get(WORLDS_URL)
async def list_worlds(_gm: GamemasterDep) -> Response:
    """The worlds are the world menu's (documentation/CURRENT-WORLD.md §3): their old list
    leads to the table's world, its adventures."""
    return redirect("/gm/adventures")


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
    combat_rules: list[str] | None = None,
    character_rules: list[str] | None = None,
    features: list[str] | None = None,
    features_set: str = "",
    pantheons: list[str] | None = None,
    pantheons_set: str = "",
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
        combat_rules=tuple(combat_rules or ()),
        character_rules=tuple(character_rules or ()),
        language=_language(registry, language),
        player_sources=tuple(chosen) if chosen is not None else None,
        # A form without the features (the world menu's short New world) leaves them.
        features=tuple(features or ()) if features_set else None,
        pantheons=tuple(pantheons or ()) if pantheons_set else None,
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
    combat_rules: Annotated[list[str] | None, Form()] = None,
    character_rules: Annotated[list[str] | None, Form()] = None,
    features: Annotated[list[str] | None, Form()] = None,
    features_set: Annotated[str, Form()] = "",
) -> Response:
    """Create a world, or show the form again with the reason it was refused. The world
    menu's dialog names no language: the world is written in the maker's."""
    data = _world_input(
        registry,
        name,
        description,
        sources,
        players,
        rerolls,
        language or page.lang,
        encumbrance,
        milestone,
        combat_rules,
        character_rules,
        features,
        features_set,
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
            combat_rules=data.combat_rules,
            character_rules=data.character_rules,
            features=data.features,
            language=data.language,
            languages=_languages(registry),
            error=page.t.error(exc),
        )
        return html(view, status_code=422)
    # A world made is the one the table plays in, its settings opened to fill in the rest
    # (documentation/CURRENT-WORLD.md §3).
    registry.table.select(world.id)
    registry.places.forget_sections(_gm.user.id, WORLD_SECTIONS)
    return redirect(f"{WORLDS_URL}/{world.id}?created=1&settings=1")


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
            combat_rules=world.combat_rules,
            character_rules=world.character_rules,
            pantheon_options=registry.pantheons(world.id),
            pantheons=world.pantheons,
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
    combat_rules: Annotated[list[str] | None, Form()] = None,
    character_rules: Annotated[list[str] | None, Form()] = None,
    features: Annotated[list[str] | None, Form()] = None,
    features_set: Annotated[str, Form()] = "",
    pantheons: Annotated[list[str] | None, Form()] = None,
    pantheons_set: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Save a world's name, description and books, then back to ``next`` (the page the
    settings dialog was opened on) or the world."""
    world = worlds.get(world_id)
    data = _world_input(
        registry,
        name,
        description,
        sources,
        players,
        rerolls,
        language,
        encumbrance,
        milestone,
        combat_rules,
        character_rules,
        features,
        features_set,
        pantheons,
        pantheons_set,
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
            combat_rules=data.combat_rules,
            character_rules=data.character_rules,
            features=data.features,
            language=data.language,
            languages=_languages(registry),
            error=page.t.error(exc),
            next_url=safe_next(next) or "",
        )
        return html(view, status_code=422)
    registry.features_changed(
        world_id, _gm.user.username, _gm.user.display_name or _gm.user.username
    )
    return redirect(safe_next(next) or f"{WORLDS_URL}/{world_id}?updated=1")


@router.post("/gm/world/select")
async def select_world(
    _gm: GamemasterDep,
    registry: RegistryDep,
    world: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",  # noqa: A002 — the form field's name
) -> Response:
    """Make ``world`` the one the whole table plays in, then back to where it was chosen."""
    before = registry.table.world_id()
    try:
        registry.table.select(world)
    except TableError as exc:
        raise HTTPException(status_code=404) from exc
    back = safe_next(next) or "/gm"
    if world != before:
        # The world's sections were left on the old world's pages; a page of one of them
        # gives way to the same section of the new world.
        registry.places.forget_sections(_gm.user.id, WORLD_SECTIONS)
        path = back.split("?", 1)[0]
        section = world_section_of(path)
        # Stay on a page of the world just chosen (the banner's switch); one of another
        # world gives way to its section's list in the new one.
        if section is not None and world_of_page(registry.gamestate, path) != world:
            back = section
    return redirect(back)


def _settings_again(url: str) -> str:
    """``url`` with the world's settings dialog open (``settings=1``)."""
    path, _, anchor = url.partition("#")
    return f"{path}{'&' if '?' in path else '?'}settings=1" + (f"#{anchor}" if anchor else "")


@router.get("/gm/world/settings", response_class=HTMLResponse)
async def world_settings(
    _gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    next: str = "",  # noqa: A002 — the query parameter's name
) -> HTMLResponse:
    """The current world's form and its cover and pictures, for the settings dialog the
    world menu opens."""
    world = registry.table.world()
    if world is None:
        raise HTTPException(status_code=404)
    back = safe_next(next) or ""
    return html(
        [
            world_form(
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
                combat_rules=world.combat_rules,
                character_rules=world.character_rules,
                pantheon_options=registry.pantheons(world.id),
                pantheons=world.pantheons,
                language=world.language,
                languages=_languages(registry),
                next_url=back,
                in_dialog=True,
            ),
            # Its cover and pictures; their forms come back to the page the dialog was
            # opened on, the dialog open again.
            images_panel(
                page,
                world.id,
                world.cover,
                registry.adventures.images(world.id),
                _settings_again(back or "/gm/adventures"),
            ),
        ]
    )


@router.get(f"{WORLDS_URL}/{{world_id}}/delete", response_class=HTMLResponse)
async def confirm_delete_world(
    _gm: GamemasterDep, page: PageDep, worlds: WorldsDep, world_id: str
) -> HTMLResponse:
    """Ask first, saying that the characters stay (they belong to no world)."""
    world = worlds.get(world_id)
    return html(world_delete_page(page, world))


@router.post(f"{WORLDS_URL}/{{world_id}}/delete", response_class=HTMLResponse)
async def delete_world(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, worlds: WorldsDep, world_id: str
) -> Response:
    """Delete a world; the characters, belonging to none, stay as they are."""
    world = worlds.get(world_id)
    before = registry.table.world_id()
    try:
        kept = worlds.delete(world_id)
    except AuthError as exc:
        return html(world_delete_page(page, world, error=page.t.error(exc)), status_code=409)
    if registry.table.world_id() != before:
        registry.table.announce()  # open pages follow the table into the next world
    # The table plays in the next world, if any; the page offers Undo.
    return redirect(f"/gm/adventures?undo={kept}")


__all__ = ["router"]
