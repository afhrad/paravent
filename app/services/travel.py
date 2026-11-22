"""The party on the road: a stretch of travel against the world's clock.

The gamemaster says how long the party travels (hours, or days of eight travelling
hours), at what pace, through what, and how often to check for an encounter. The pace
gives the distance: 2, 3 or 4 miles an hour (18, 24 or 30 miles a day), half that in
difficult terrain. Mounted, the party covers twice the distance in the first hour of
each day, after which the mounts need a rest. On board a vessel there is no pace: the
vessel's speed is the party's, and with a crew working in shifts it sails around the
clock. Each check is a d20 that comes up on 18 or more; when it does and an
environment is chosen, its random-encounter table is rolled for the party's level and
the journey stops there, the clock at that moment, the rest of the way still ahead.
One more check may fall in the night; one that comes up wakes the party.

A stretch that ends before the day's travelling hours are done leaves the rest of that
day for the next stretch, so supper comes at the end of the day either way.

At the end of each travelling day every character eats a day's rations from their
pack (when supplies are kept); one with none goes hungry. Hunger follows the world's
rules: under the 2014 rules a character manages 3 + its Constitution modifier days
(at least one) and gains a level of exhaustion each further day; under the 2024 rules
the fifth day without food brings one, and each day after another. A day of eating
ends the count. Water is enough, half or none for the day: under the 2014 rules half
calls for a DC 15 Constitution save, failed or with none a level of exhaustion (two
for a character already exhausted); under the 2024 rules only less than half costs a
level. A night between two travelling days is a long rest, when chosen. Exhaustion
from hunger or thirst stays through any rest until the character has eaten (or drunk
its fill: a day with enough water). The weather may be rolled for each day on the
books' table.

The gamemaster may say the road runs through extreme cold or heat: each character
then makes a Constitution save at the end of every travelling hour (cold DC 10; heat
DC 5 for the first hour and one more for each hour after, under the 2014 rules only
without enough water), a failure costing a level of exhaustion; resistance or
immunity to cold or fire (or, against cold, cold weather gear) spares it. At high
altitude each hour of travel counts as two: the day covers half the way.

The terrain gives the rest, from the world's guide: the 2024 *Travel Terrain* table (the
fastest pace it allows, the encounter distance, the foraging and navigation DCs), or
the 2014 guide's *Wilderness Navigation* and *Foraging DCs* tables. Off a path, the
party's navigator makes a Wisdom (Survival) check at the start of each travelling day:
under the 2014 rules +5 at a slow pace and -5 at a fast one, with advantage given a map
or the sky, and a failure sends the party astray for 1d6 hours before the next check;
under the 2024 rules advantage at a slow pace and disadvantage at a fast one (a helper
gives advantage), and a failure lengthens the day's way by 1d6 × 10 percent. Each
forager makes the same check at the end of the day (under the 2014 rules not at a fast
pace): a success finds 1d6 + its Wisdom modifier pounds of food and as many gallons of
water. A pound of food is a character's meal instead of a ration; the water found
counts toward the day's (a character needs a gallon, two in extreme heat). When an
encounter comes up, the terrain's encounter distance is rolled, and so is surprise, for
the gamemaster to use or not: which characters would be surprised if the creatures lay
in wait (their lowest Stealth against each watcher's passive Perception, -5 at a fast
pace; under the 2014 rules the navigator and the foragers are not watching), and which
creatures a party moving stealthily would catch unaware (2014: only at a slow pace;
2024: with disadvantage at a normal or fast one).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.models.gamestate import EncounterMonster, Journey
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import roll_amount
from app.services.clock import DAY, ClockService
from app.services.dice import Dice
from app.services.encounter_roll import EncounterRoller, Resolve
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.encounter_tables import Environment

Pace = Literal["slow", "normal", "fast"]
Water = Literal["enough", "half", "none"]
# Miles an hour by pace.
PACES: dict[str, int] = {"slow": 2, "normal": 3, "fast": 4}
WATER = ("enough", "half", "none")
# Travelling hours in a day.
TRAVEL_DAY = 8
# An encounter check comes up on a d20 roll of this or more.
ENCOUNTER_ON = 18
# The most one stretch may cover: thirty days on the road.
MOST_HOURS = 30 * TRAVEL_DAY
# The save against thirst on half water (2014 rules).
THIRST_DC = 15
# Extreme cold: a DC 10 Constitution save at the end of each hour. Extreme heat: DC 5
# for the first hour, one more for each hour after.
COLD_DC = 10
HEAT_DC = 5
CLIMATES = ("", "cold", "heat")
# The books' hazards a journey may meet, by the English names they go by.
ROAD_HAZARDS = (
    "Extreme Cold",
    "Extreme Heat",
    "High Altitude",
    "Strong Wind",
    "Heavy Precipitation",
)


# The terrains of the 2024 *Travel Terrain* table, by the English names its rows go by.
TERRAINS = (
    "Arctic",
    "Coastal",
    "Desert",
    "Forest",
    "Grassland",
    "Hill",
    "Mountain",
    "Swamp",
    "Underdark",
    "Urban",
    "Waterborne",
)
# How much food and water each forager may find: the 2014 guide's three rows.
ABUNDANCE = ("abundant", "limited", "scarce")
# A character's water for a day, in gallons (twice that in extreme heat).
GALLONS_A_DAY = 1
_PACE_ORDER = ("slow", "normal", "fast")


def terrain_of(environment: str) -> str:
    """The terrain an encounter table's environment travels through ("Sylvan Forest"
    Forest, "At Sea" Waterborne); "" when none fits."""
    text = environment.lower()
    for name in TERRAINS:
        if re.search(rf"\b{name.lower()}", text):
            return name
    if "under" not in text and re.search(r"\b(sea|water|ship)", text):
        return "Waterborne"
    return ""


class TravelError(Translatable):
    """A journey the rules refuse."""


@dataclass(frozen=True)
class Vessel:
    """A ship or airship the party travels on: its name and speed in miles an hour."""

    name: str
    mph: float
    around_the_clock: bool = False


@dataclass(frozen=True)
class Terrain:
    """What the world's guide says of a terrain: the navigation and foraging DCs, the
    encounter distance as dice ("6d6 × 10", in feet) and the fastest pace it allows
    (2024 rules; "" for any)."""

    name: str
    navigation_dc: int | None = None
    foraging_dc: int | None = None
    distance: str = ""
    max_pace: str = ""


@dataclass(frozen=True)
class Plan:
    """What the gamemaster set out."""

    hours: int
    pace: Pace = "normal"
    difficult: bool = False
    environment: Environment | None = None
    checks: int = 1  # encounter checks per travelling day
    rations: bool = True
    rest: bool = True
    water: Water = "enough"
    mounted: bool = False
    vessel: Vessel | None = None
    night_check: bool = False
    weather: bool = False
    climate: Literal["", "cold", "heat"] = ""
    altitude: bool = False
    terrain: Terrain | None = None
    navigator: str = ""  # a character's id; "" on a path or with a landmark in sight
    helped: bool = False  # a map, the sky or a helper: advantage on navigating
    foragers: tuple[str, ...] = ()  # characters' ids
    stealthy: bool = False


def hunger_limit(con_mod: int, rules: str) -> int:
    """How many days without food pass before the first level of exhaustion."""
    return 4 if rules == "one" else max(1, 3 + con_mod)


class TravelService:
    """Moves the party along: the clock, rations, water, rests and encounter checks."""

    def __init__(
        self,
        clock: ClockService,
        protocol: ProtocolService | None,
        *,
        party: Callable[[str], Sequence[PlayerCharacter]],
        eat: Callable[[PlayerCharacter], bool],
        tire: Callable[[PlayerCharacter, str], None],
        con_mod: Callable[[PlayerCharacter], int],
        rest: Callable[[str], None],
        con_save: Callable[[PlayerCharacter], int] | None = None,
        quench: Callable[[PlayerCharacter], None] | None = None,
        sheltered: Callable[[PlayerCharacter, str], bool] | None = None,
        skill: Callable[[PlayerCharacter, str], int] | None = None,
        wis_mod: Callable[[PlayerCharacter], int] | None = None,
        passive: Callable[[PlayerCharacter], int] | None = None,
        lurk: Callable[[str, str], tuple[int, int]] | None = None,
        dice: Dice | None = None,
    ) -> None:
        """``party(world)`` the characters on the road; ``eat(pc)`` takes a day's
        rations from the pack (False: there were none); ``tire(pc, cause)`` adds a
        level of exhaustion from ``hunger`` or ``thirst``; ``quench(pc)`` marks a day
        with enough water, which ends thirst's hold; ``con_mod(pc)`` its Constitution
        modifier and ``con_save(pc)`` its Constitution saving throw bonus (the modifier
        when not given); ``rest(world)`` a long rest for the party;
        ``sheltered(pc, climate)`` whether the character weathers extreme cold or heat
        unharmed (resistance or immunity to it, or cold weather gear);
        ``skill(pc, key)`` its bonus to a skill (``survival``, ``stealth``),
        ``wis_mod(pc)`` its Wisdom modifier and ``passive(pc)`` its passive Perception;
        ``lurk(name, source)`` a creature's Stealth bonus and passive Perception."""
        self._clock = clock
        self._protocol = protocol
        self._party = party
        self._eat = eat
        self._tire = tire
        self._con_mod = con_mod
        self._con_save = con_save or con_mod
        self._rest = rest
        self._quench = quench
        self._sheltered = sheltered or (lambda _pc, _climate: False)
        self._skill = skill or (lambda _pc, _key: 0)
        self._wis_mod = wis_mod or (lambda _pc: 0)
        self._passive = passive or (lambda _pc: 10)
        self._lurk = lurk
        self._dice = dice or Dice()

    def travel(
        self,
        world_id: str,
        plan: Plan,
        *,
        rules: str,
        resolve: Resolve,
        by: str,
        by_name: str,
        weather: Callable[[], str] | None = None,
    ) -> Journey:
        """Travel until the time is up or an encounter stops the party; the journey is
        kept on the world's clock for the party view and logged. ``weather()`` rolls
        a day's weather on the books' table ("temperature|wind|precipitation")."""
        if not 0 < plan.hours <= MOST_HOURS or plan.pace not in PACES:
            raise TravelError("travel.error.bad")
        if plan.water not in WATER or (plan.vessel is not None and plan.vessel.mph <= 0):
            raise TravelError("travel.error.bad")
        if plan.climate not in CLIMATES:
            raise TravelError("travel.error.bad")
        party = [pc for pc in self._party(world_id) if not pc.in_wizard]
        levels = [max(1, pc.total_level) for pc in party] or [1]
        level = round(sum(levels) / len(levels))
        pace = plan.pace
        terrain = plan.terrain
        # Under the 2024 rules the terrain allows no faster than its pace.
        slowed = bool(
            rules == "one"
            and plan.vessel is None
            and terrain is not None
            and terrain.max_pace in _PACE_ORDER
            and _PACE_ORDER.index(pace) > _PACE_ORDER.index(terrain.max_pace)
        )
        if slowed and terrain is not None:
            pace = terrain.max_pace  # type: ignore[assignment]
        if plan.vessel is not None:
            mph = plan.vessel.mph
        else:
            mph = PACES[pace] / (2 if plan.difficult else 1)
            # High in the mountains each hour counts as two: half the way in a day.
            mph /= 2 if plan.altitude else 1
        day_length = (
            DAY if plan.vessel is not None and plan.vessel.around_the_clock else TRAVEL_DAY * 60
        )
        held = self._clock.of(world_id)
        start = held.minute
        hungry = dict(held.hungry)
        journey = Journey(
            started=start,
            ended=start,
            pace=pace,
            slowed=slowed,
            terrain=terrain.name if terrain is not None else "",
            planned=plan.hours,
            difficult=plan.difficult and plan.vessel is None,
            environment=plan.environment.key if plan.environment else "",
            water=plan.water,
            mounted=plan.mounted and plan.vessel is None,
            vessel=plan.vessel.name if plan.vessel else "",
            climate=plan.climate,
            altitude=plan.altitude and plan.vessel is None,
        )
        exposure: dict[str, int] = {}
        navigator = next((pc for pc in party if pc.id == plan.navigator), None)
        if navigator is not None and terrain is not None and terrain.navigation_dc:
            journey.navigator = navigator.name or "?"
        # The road already covered today, when the last stretch stopped partway.
        travelled = held.road if held.road_day == start // DAY and held.road < day_length else 0
        offset = travelled
        every = TRAVEL_DAY * 60 // plan.checks if plan.checks > 0 else 0
        elapsed = 0  # minutes on the clock, nights included
        miles = 0.0
        left = plan.hours * 60
        stopped = False
        if plan.weather and weather is not None and offset == 0:
            journey.weather.append(weather())
        while left > 0 and not stopped:
            # Read afresh each day: exhaustion gained on the road counts from here on.
            party = self._fresh(world_id, party)
            into = travelled % day_length
            today = min(left, day_length - into)
            moved = today
            # The navigator's check as the day's road begins (one stretch a day).
            astray = (0, 0)
            if into == 0 and journey.navigator and navigator is not None and terrain:
                astray = self._navigate(journey, navigator, plan, pace, rules, today)
            if every:
                check_at = (travelled // every + 1) * every
                while check_at <= travelled + today:
                    roll = self._dice.die(20)
                    if roll >= ENCOUNTER_ON:
                        moved = check_at - travelled
                        stopped = True
                        self._meet(journey, roll, plan.environment, level, resolve)
                        self._spot(journey, plan, party, pace, rules, on_road=True)
                        break
                    check_at += every
            miles += moved / 60 * mph
            lost_minutes, percent = astray
            journey.astray = round(
                journey.astray
                + min(lost_minutes, moved) / 60 * mph
                + moved / 60 * mph * percent / 100,
                1,
            )
            if journey.mounted:
                miles += max(0, min(60, into + moved) - into) / 60 * mph
            for pc in self._expose(party, plan, into, moved, rules, exposure):
                self._tire(pc, plan.climate)
            travelled += moved
            elapsed += moved
            left -= moved
            if travelled % day_length:
                break
            # The end of a travelling day, though an encounter came with it: supper,
            # then the night if the road goes on.
            party = self._fresh(world_id, party)
            food, gallons = self._forage(journey, party, plan, pace, rules)
            tired = self._supper(journey, party, hungry, plan.rations, rules, meals=food)
            tired += self._drink(journey, party, _water_with(plan, gallons, len(party)), rules)
            if left > 0 and not stopped:
                night = DAY - day_length
                if plan.night_check and night > 0:
                    roll = self._dice.die(20)
                    if roll >= ENCOUNTER_ON:
                        # Woken partway through the night: no rest, and the road stops.
                        stopped = True
                        journey.night = True
                        self._meet(journey, roll, plan.environment, level, resolve)
                        self._spot(journey, plan, party, pace, rules, on_road=False)
                        elapsed += self._dice.die(max(1, night // 60)) * 60
                if not stopped:
                    if plan.rest:
                        self._rest(world_id)
                    elapsed += night
                    if plan.weather and weather is not None:
                        journey.weather.append(weather())
            for pc, cause in tired:
                self._tire(pc, cause)
                journey.exhausted.append(pc.name or "?")
        names = {pc.id: pc.name or "?" for pc in party}
        journey.exposed = [f"{names.get(cid, '?')}:{n}" for cid, n in exposure.items() if n]
        journey.hours = (travelled - offset) // 60
        journey.miles = round(miles, 1)
        if elapsed:
            self._clock.advance(world_id, elapsed, by, by_name, "travel")
        clock = self._clock.of(world_id)
        journey.ended = clock.minute
        part = travelled % day_length
        self._clock.keep(
            world_id,
            journey,
            hungry,
            road=(part, clock.minute // DAY) if part and not journey.night else (0, None),
        )
        self._log(world_id, journey, by, by_name)
        return journey

    def _fresh(self, world_id: str, party: Sequence[PlayerCharacter]) -> list[PlayerCharacter]:
        """The same characters as they are now."""
        now = {pc.id: pc for pc in self._party(world_id)}
        return [now.get(pc.id, pc) for pc in party]

    def _meet(
        self,
        journey: Journey,
        check: int,
        environment: Environment | None,
        level: int,
        resolve: Resolve,
    ) -> None:
        journey.check = check
        if environment is None:
            return
        rolled = EncounterRoller(resolve, self._dice).roll(environment, level)
        if rolled is None:
            return
        journey.encounter = rolled.text
        journey.encounter_roll = rolled.roll
        journey.monsters = [
            EncounterMonster(name=n, source=s, count=c) for n, s, c in rolled.monsters
        ]
        journey.unresolved = rolled.unresolved

    def _supper(
        self,
        journey: Journey,
        party: Sequence[PlayerCharacter],
        hungry: dict[str, int],
        rations: bool,
        rules: str,
        *,
        meals: int = 0,
    ) -> list[tuple[PlayerCharacter, str]]:
        """Everyone eats (``meals`` of them what was foraged, the rest rations) or goes
        hungry; those whose hunger now costs them a level of exhaustion (applied after
        the night's rest, which cannot take it away)."""
        tired: list[tuple[PlayerCharacter, str]] = []
        if not rations:
            return tired
        for pc in party:
            if meals > 0:
                meals -= 1
                journey.foraged_meals += 1
                hungry.pop(pc.id, None)
                continue
            if self._eat(pc):
                journey.rations += 1
                hungry.pop(pc.id, None)
                continue
            days = hungry.get(pc.id, 0) + 1
            hungry[pc.id] = days
            if (pc.name or "?") not in journey.hungry:
                journey.hungry.append(pc.name or "?")
            if days > hunger_limit(self._con_mod(pc), rules):
                tired.append((pc, "hunger"))
        return tired

    def _d20(self, edge: int) -> int:
        """A d20, the higher of two with advantage (``edge`` > 0), the lower with
        disadvantage (< 0)."""
        first = self._dice.die(20)
        if edge == 0:
            return first
        second = self._dice.die(20)
        return max(first, second) if edge > 0 else min(first, second)

    def _edge(self, plan: Plan, pace: str, rules: str, *, helped: bool = False) -> int:
        """Advantage (1), disadvantage (-1) or neither on a Perception or Survival check
        on the road: a slow pace gives advantage and a fast one disadvantage under the
        2024 rules (not aboard a vessel); ``helped`` advantage either way."""
        edge = 1 if helped else 0
        if rules == "one" and plan.vessel is None:
            edge += {"slow": 1, "fast": -1}.get(pace, 0)
        return max(-1, min(1, edge))

    def _navigate(
        self,
        journey: Journey,
        navigator: PlayerCharacter,
        plan: Plan,
        pace: str,
        rules: str,
        today: int,
    ) -> tuple[int, int]:
        """The navigator's Wisdom (Survival) checks for a day: the minutes spent astray
        (2014: 1d6 hours for each failure, until a check succeeds) and the percentage
        the way grows by (2024: 1d6 × 10 on a failure)."""
        terrain = plan.terrain
        dc = terrain.navigation_dc if terrain is not None else None
        if not dc:
            return (0, 0)
        bonus = self._skill(navigator, "survival")
        if rules != "one" and plan.vessel is None:
            bonus += {"slow": 5, "fast": -5}.get(pace, 0)
        edge = self._edge(plan, pace, rules, helped=plan.helped)
        total = self._d20(edge) + bonus
        journey.navigation.append(f"{total}/{dc}")
        if total >= dc:
            return (0, 0)
        journey.lost_days += 1
        if rules == "one":
            return (0, self._dice.die(6) * 10)
        lost = 0
        while total < dc:
            lost += self._dice.die(6) * 60
            if lost >= today:
                break
            total = self._d20(edge) + bonus
            journey.navigation.append(f"{total}/{dc}")
        return (min(lost, today), 0)

    def _forage(
        self,
        journey: Journey,
        party: Sequence[PlayerCharacter],
        plan: Plan,
        pace: str,
        rules: str,
    ) -> tuple[int, int]:
        """Each forager's Wisdom (Survival) check at the day's end: the pounds of food
        and gallons of water found (1d6 + its Wisdom modifier each on a success). None
        aboard a vessel, nor at a fast pace under the 2014 rules."""
        terrain = plan.terrain
        dc = terrain.foraging_dc if terrain is not None else None
        if not dc or not plan.foragers or plan.vessel is not None:
            return (0, 0)
        if rules != "one" and pace == "fast":
            return (0, 0)
        edge = self._edge(plan, pace, rules)
        food = water = 0
        for pc in party:
            if pc.id not in plan.foragers:
                continue
            if self._d20(edge) + self._skill(pc, "survival") < dc:
                continue
            wis = self._wis_mod(pc)
            food += max(0, self._dice.die(6) + wis)
            water += max(0, self._dice.die(6) + wis)
        journey.foraged_food += food
        journey.foraged_water += water
        return (food, water)

    def _spot(
        self,
        journey: Journey,
        plan: Plan,
        party: Sequence[PlayerCharacter],
        pace: str,
        rules: str,
        *,
        on_road: bool,
    ) -> None:
        """The encounter's distance by the terrain, and surprise for the gamemaster to
        use: who would be surprised if the creatures lay in wait, and which creatures a
        party moving stealthily would catch unaware."""
        terrain = plan.terrain
        if terrain is not None and terrain.distance:
            journey.distance = roll_amount(terrain.distance, self._dice) or None
        if self._lurk is None or not journey.monsters or not party:
            return
        kinds = [(m.name, *self._lurk(m.name, m.source)) for m in journey.monsters]
        walking = on_road and plan.vessel is None
        # Watching for danger: -5 at a fast pace; under the 2024 rules +5 at a slow one.
        shift = {"fast": -5, "slow": 5 if rules == "one" else 0}.get(pace, 0) if walking else 0
        busy = {plan.navigator, *plan.foragers} if rules != "one" and on_road else set()
        stealth = min(self._d20(0) + bonus for _name, bonus, _passive in kinds)
        journey.lurk_stealth = stealth
        journey.ambush = [
            pc.name or "?" for pc in party if pc.id in busy or self._passive(pc) + shift < stealth
        ]
        if not plan.stealthy or not on_road:
            return
        if rules != "one" and walking and pace != "slow":
            return  # 2014: only a slow pace lets the party move stealthily
        edge = -1 if rules == "one" and walking and pace != "slow" else 0
        sneak = min(self._d20(edge) + self._skill(pc, "stealth") for pc in party)
        journey.party_stealth = sneak
        journey.unaware = [name for name, _bonus, passive in kinds if passive < sneak]

    def _expose(
        self,
        party: Sequence[PlayerCharacter],
        plan: Plan,
        into: int,
        moved: int,
        rules: str,
        exposure: dict[str, int],
    ) -> list[PlayerCharacter]:
        """The travelling hours just covered in extreme cold or heat: each character
        makes a Constitution save at the end of each hour (cold DC 10; heat DC 5, one
        more for each hour on the day's road), a failure a level of exhaustion. Under
        the 2014 rules heat harms only a character without enough water. Who failed,
        once for each level."""
        if not plan.climate:
            return []
        first, last = into // 60 + 1, (into + moved) // 60
        if last < first:
            return []
        out: list[PlayerCharacter] = []
        for pc in party:
            if self._sheltered(pc, plan.climate):
                continue
            if plan.climate == "heat" and rules != "one" and plan.water == "enough":
                continue
            for hour in range(first, last + 1):
                dc = COLD_DC if plan.climate == "cold" else HEAT_DC + hour - 1
                if self._dice.die(20) + self._con_save(pc) < dc:
                    exposure[pc.id] = exposure.get(pc.id, 0) + 1
                    out.append(pc)
        return out

    def _drink(
        self,
        journey: Journey,
        party: Sequence[PlayerCharacter],
        water: str,
        rules: str,
    ) -> list[tuple[PlayerCharacter, str]]:
        """Each character short of water at the day's end: those it costs a level of
        exhaustion, once for each level (applied after the night's rest)."""
        tired: list[tuple[PlayerCharacter, str]] = []
        if water == "enough":
            for pc in party:
                if self._quench is not None:
                    self._quench(pc)
            return tired
        for pc in party:
            if rules == "one":
                levels = 1 if water == "none" else 0
            else:
                failed = water == "none" or (self._dice.die(20) + self._con_save(pc) < THIRST_DC)
                levels = (2 if pc.state.exhaustion > 0 else 1) if failed else 0
            if levels and (pc.name or "?") not in journey.thirsty:
                journey.thirsty.append(pc.name or "?")
            tired.extend([(pc, "thirst")] * levels)
        return tired

    def _log(self, world_id: str, journey: Journey, by: str, by_name: str) -> None:
        """``travel:<pace>:<hours>:<miles>:<environment>:<hungry names>:<thirsty
        names>:<vessel>`` for everyone, and the encounter's row for the gamemaster."""
        if self._protocol is None:
            return
        entries = [
            ProtocolEntry(
                actor=by,
                actor_name=by_name,
                kind=EntryKind.CUSTOM,
                what=(
                    f"travel:{journey.pace}:{journey.hours}:{journey.miles}:"
                    f"{journey.environment.partition('|')[0]}:"
                    f"{_names(journey.hungry)}:{_names(journey.thirsty)}:"
                    f"{journey.vessel.replace(':', ' ')}"
                ),
                scope=Scope(world=world_id),
            )
        ]
        if journey.check is not None:
            entries.append(
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    kind=EntryKind.CUSTOM,
                    what=f"travel_encounter:{journey.encounter}",
                    visibility=Visibility.GM,
                    scope=Scope(world=world_id),
                )
            )
        self._protocol.append(entries)


def _water_with(plan: Plan, gallons: int, people: int) -> Water:
    """The day's water once what was foraged is added to what the party carries: enough,
    half or none, a character needing a gallon (two in extreme heat)."""
    if plan.water == "enough" or gallons <= 0 or people <= 0:
        return plan.water
    need = people * GALLONS_A_DAY * (2 if plan.climate == "heat" else 1)
    have = {"enough": 1.0, "half": 0.5, "none": 0.0}[plan.water] * need + gallons
    return "enough" if have >= need else "half" if have >= need / 2 else "none"


def _names(names: Sequence[str]) -> str:
    return ",".join(n.replace(":", " ").replace(",", " ") for n in names)


__all__ = [
    "ABUNDANCE",
    "ENCOUNTER_ON",
    "PACES",
    "THIRST_DC",
    "TERRAINS",
    "TRAVEL_DAY",
    "WATER",
    "Plan",
    "Terrain",
    "TravelError",
    "TravelService",
    "Vessel",
    "hunger_limit",
    "terrain_of",
]
