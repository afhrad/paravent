"""The planes of existence: the plane the party is on, and its rules carried out.

The 2014 guide gives most planes an optional rule or two: a save at the end of each long
rest (Psychic Dissonance on the Upper and Lower Planes, Abyssal Corruption, Vile
Transformation, Pervasive Evil, …), one after each hour (Pandemonium's Mad Winds), one on
leaving (the Feywild's Memory Loss and Time Warp, Arborea's Intense Yearning), or an effect
that simply holds while the party is there (Mechanus's Law of Averages, Arcadia's Planar
Vitality). The 2024 guide keeps the Feywild's magic, the Shadowfell's despair and the
Astral Plane's psychic wind. The rules' text stays in the user's books; each rule here is
its trigger, its save and what a failure does, linked to where the world's guide prints
it, and offered only where the world has that guide.

The gamemaster puts the party on a plane (``WorldClock.plane``); its rules then run by
themselves — a long rest, the clock's hours, leaving — or at the gamemaster's call (a
Shadowfell despair, a psychic wind, an ether cyclone). Characters keep no alignment, so
who a rule spares (natives, those of the plane's alignment, elves leaving the Feywild) is
ticked per rule. A failure gains a level of exhaustion, rolls the rule's table, or says in
the log what befalls the character; a despair or a corruption is carried as an affliction
(``plane``) until a save at the end of a long rest shakes it off.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.afflictions import AfflictionService
from app.services.book_tables import Rows, roll_row, rolled
from app.services.clock import DAY, ClockService
from app.services.dice import Dice
from app.services.protocol import ProtocolService

HOUR = 60
# Hourly saves rolled at most in one move of the clock (a month skipped is not 720 saves).
MOST_HOURS = 24


@dataclass(frozen=True)
class PlaneRule:
    """One of a plane's rules: where the guides print it (book, heading), when it acts
    (``long_rest``, ``hour``, ``leave``, ``call`` or ``always``), the save a character
    makes (ability, DC), what a failure does (``exhaustion``, ``table``, ``note``), the
    table rolled (its caption in each book), the save at the end of a long rest that ends
    a carried effect, and whether it touches each character (``each``) or the party."""

    key: str
    printed: tuple[tuple[str, str], ...]
    when: str
    save: tuple[str, int] | None = None
    effect: str = ""
    table: tuple[tuple[str, str], ...] = ()
    cure: tuple[str, int] | None = None
    each: bool = True


DMG = "DMG"
XDMG = "XDMG"


def _dmg(heading: str) -> tuple[tuple[str, str], ...]:
    return ((DMG, f"Optional Rule: {heading}"),)


DISSONANCE = PlaneRule(
    "dissonance", _dmg("Psychic Dissonance"), "long_rest", ("con", 10), "exhaustion"
)
RULES: dict[str, PlaneRule] = {
    rule.key: rule
    for rule in (
        DISSONANCE,
        PlaneRule("blessed", _dmg("Blessed Beneficence"), "always"),
        PlaneRule("goodwill", _dmg("Pervasive Goodwill"), "long_rest", ("wis", 10), "note"),
        PlaneRule("joy", _dmg("Overwhelming Joy"), "long_rest", ("wis", 10), "note"),
        PlaneRule("hunters", _dmg("Hunter's Paradise"), "always"),
        PlaneRule("beasts", _dmg("Beast Transformation"), "always"),
        PlaneRule("yearning", _dmg("Intense Yearning"), "leave", ("cha", 5), "carry"),
        PlaneRule("wrath", _dmg("Immortal Wrath"), "always"),
        PlaneRule("mind", _dmg("Power of the Mind"), "always"),
        PlaneRule("winds", _dmg("Mad Winds"), "hour", ("wis", 10), "exhaustion"),
        PlaneRule(
            "corruption",
            _dmg("Abyssal Corruption"),
            "long_rest",
            ("cha", 10),
            "table",
            ((DMG, "Abyssal Corruption"),),
            cure=("cha", 15),
        ),
        PlaneRule("prison", _dmg("Prison Plane"), "always"),
        PlaneRule("vile", _dmg("Vile Transformation"), "long_rest", ("wis", 10), "exhaustion"),
        PlaneRule("hindrance", _dmg("Cruel Hindrance"), "always"),
        PlaneRule("evil", _dmg("Pervasive Evil"), "long_rest", ("wis", 10), "note"),
        PlaneRule("bloodlust", _dmg("Bloodlust"), "always"),
        PlaneRule("averages", _dmg("Law of Averages"), "always"),
        PlaneRule("order", _dmg("Imposing Order"), "long_rest", ("wis", 10), "note"),
        PlaneRule("vitality", _dmg("Planar Vitality"), "always"),
        PlaneRule(
            "memory", ((XDMG, "Memory Loss"), (DMG, "Memory Loss")), "leave", ("wis", 10), "note"
        ),
        PlaneRule(
            "time_warp",
            ((XDMG, "Time Warp"), (DMG, "Time Warp")),
            "leave",
            effect="table",
            table=((XDMG, "Feywild Time Warp"), (DMG, "Feywild Time Warp")),
            each=False,
        ),
        PlaneRule(
            "despair",
            ((XDMG, "Shadowfell Despair"), (DMG, "Optional Rule: Shadowfell Despair")),
            "call",
            ("wis", 10),
            "table",
            ((XDMG, "Shadowfell Despair"), (DMG, "Shadowfell Despair")),
            cure=("wis", 15),
        ),
        PlaneRule(
            "wind",
            ((XDMG, "Psychic Wind"), (DMG, "Psychic Wind")),
            "call",
            ("int", 15),
            "table",
            ((XDMG, "Psychic Wind Psychic Effects"),),
        ),
        PlaneRule(
            "cyclone",
            ((XDMG, "Ether Cyclones"), (DMG, "Ether Cyclones")),
            "call",
            effect="table",
            table=((XDMG, "Ether Cyclone"), (DMG, "Ether Cyclones")),
            each=False,
        ),
    )
}
# A psychic wind also blows the whole party somewhere: its location effect.
WIND_LOCATION = ((XDMG, "Psychic Wind Location Effects"), (DMG, "Psychic Wind Effects"))
# Each plane's rules. Psychic Dissonance is the Upper and Lower Planes' (it touches
# visitors of the opposed alignment, ticked by the gamemaster).
PLANES: dict[str, tuple[str, ...]] = {
    "feywild": ("memory", "time_warp"),
    "shadowfell": ("despair",),
    "astral": ("wind",),
    "ethereal": ("cyclone",),
    "mount_celestia": ("dissonance", "blessed"),
    "bytopia": ("dissonance", "goodwill"),
    "elysium": ("dissonance", "joy"),
    "beastlands": ("dissonance", "hunters", "beasts"),
    "arborea": ("dissonance", "yearning"),
    "ysgard": ("dissonance", "wrath"),
    "limbo": ("mind",),
    "pandemonium": ("dissonance", "winds"),
    "abyss": ("dissonance", "corruption"),
    "carceri": ("dissonance", "prison"),
    "hades": ("dissonance", "vile"),
    "gehenna": ("dissonance", "hindrance"),
    "nine_hells": ("dissonance", "evil"),
    "acheron": ("dissonance", "bloodlust"),
    "mechanus": ("averages", "order"),
    "arcadia": ("vitality",),
    "outlands": (),
}

# Finds a table by caption in the first of the books the world has: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]


def rules_of(plane: str, has: Callable[[str], bool]) -> list[PlaneRule]:
    """The plane's rules a world plays with: those printed in a book it has."""
    return [
        RULES[key]
        for key in PLANES.get(plane, ())
        if any(has(book) for book, _ in RULES[key].printed)
    ]


def roll_on(
    tables: tuple[tuple[str, str], ...], find: Finder, dice: Dice
) -> tuple[int, str] | None:
    """A roll on the first of ``tables`` the world has: the roll and its row's text with
    its dice rolled; None without one."""
    for book, caption in tables:
        rows = find(caption, [book])
        if rows:
            total, row = roll_row(rows, dice)
            return total, " ".join(rolled(row[-1], dice).split()) if row else ""
    return None


@dataclass(frozen=True)
class Befell:
    """What a rule did to one character (or, with no character, to the party): its save
    (None without one) against ``dc``, whether it was struck, and the table's roll and
    text where one was rolled."""

    rule: str
    character_id: str = ""
    name: str = ""
    roll: int | None = None
    dc: int | None = None
    struck: bool = True
    table_roll: int | None = None
    text: str = ""


class PlaneService:
    """The plane each world's party is on, and its rules carried out."""

    def __init__(
        self,
        clock: ClockService,
        protocol: ProtocolService | None,
        afflictions: AfflictionService,
        *,
        party: Callable[[str], list[PlayerCharacter]],
        save: Callable[[str, str], int],
        tire: Callable[[str], None],
        find: Callable[[str], Finder],
        has: Callable[[str], Callable[[str], bool]],
        dice: Dice | None = None,
    ) -> None:
        """``party(world)`` its characters; ``save(character, ability)`` rolls a save on
        the sheet; ``tire(character)`` adds a level of exhaustion; ``find(world)`` finds
        the world's tables; ``has(world)(book)`` says whether it has a book."""
        self._clock = clock
        self._protocol = protocol
        self._afflictions = afflictions
        self._party = party
        self._save = save
        self._tire = tire
        self._find = find
        self._has = has
        self._dice = dice or Dice()

    # -- where the party is ---------------------------------------------------------

    def of(self, world_id: str | None) -> str:
        """The plane the world's party is on; "" for the Material Plane."""
        return self._clock.of(world_id).plane if world_id else ""

    def rules(self, world_id: str | None) -> list[PlaneRule]:
        """The rules of the plane the party is on, as the world's books print them."""
        if world_id is None:
            return []
        return rules_of(self.of(world_id), self._has(world_id))

    def spared(self, world_id: str, rule: str) -> list[str]:
        """The characters ``rule`` spares."""
        return list(self._clock.of(world_id).plane_spared.get(rule, []))

    def enter(self, world_id: str, plane: str, by: str, by_name: str) -> list[Befell]:
        """The party goes to ``plane`` ("" the Material Plane): what the plane it leaves
        does on leaving is carried out first."""
        if plane and plane not in PLANES:
            raise ValueError(plane)
        clock = self._clock.of(world_id)
        if clock.plane == plane:
            return []
        done: list[Befell] = []
        if clock.plane:
            days = max(0, clock.minute - clock.plane_since) // DAY
            for rule in self.rules(world_id):
                if rule.when == "leave" and (rule.key != "time_warp" or days >= 1):
                    done.extend(self._carry(world_id, rule, by, by_name, days=days))
        self._clock.planar(
            world_id,
            plane=plane,
            plane_since=clock.minute,
            plane_hour=clock.minute,
            plane_spared={},
        )
        self._log(world_id, by, by_name, None, f"plane:{plane}")
        return done

    def spare(self, world_id: str, rule: str, character_ids: Sequence[str]) -> None:
        """Who ``rule`` spares on this plane, as the gamemaster ticked them."""
        spared = dict(self._clock.of(world_id).plane_spared)
        spared[rule] = list(dict.fromkeys(character_ids))
        self._clock.planar(world_id, plane_spared=spared)

    # -- the rules acting -----------------------------------------------------------

    def rested(
        self, character_id: str, world_id: str | None, by: str, by_name: str
    ) -> list[Befell]:
        """A character finished a long rest: the plane's long-rest rules on it."""
        if not world_id:
            return []
        done: list[Befell] = []
        for rule in self.rules(world_id):
            if rule.when == "long_rest":
                done.extend(self._carry(world_id, rule, by, by_name, only=character_id))
        return done

    def passed(self, world_id: str, before: int, now: int) -> list[Befell]:
        """The clock moved: each whole hour on a plane with an hourly rule (Mad Winds)
        has every character save, a day's worth at most in one move."""
        hourly = [r for r in self.rules(world_id) if r.when == "hour"]
        clock = self._clock.of(world_id)
        if not hourly or now <= before:
            if now < clock.plane_hour:
                self._clock.planar(world_id, plane_hour=now)
            return []
        start = max(clock.plane_hour, clock.plane_since)
        hours = max(0, now - start) // HOUR
        if not hours:
            return []
        self._clock.planar(world_id, plane_hour=start + hours * HOUR)
        done: list[Befell] = []
        for _ in range(min(hours, MOST_HOURS)):
            for rule in hourly:
                done.extend(self._carry(world_id, rule, "", "Paravent"))
        return done

    def call(self, world_id: str, rule_key: str, by: str, by_name: str) -> list[Befell]:
        """A rule the gamemaster calls for (a despair, a psychic wind, an ether cyclone);
        nothing when the plane has no such rule."""
        rule = next((r for r in self.rules(world_id) if r.key == rule_key), None)
        if rule is None or rule.when != "call":
            return []
        done: list[Befell] = []
        if rule.key == "wind":
            found = roll_on(WIND_LOCATION, self._find(world_id), self._dice)
            if found is not None:
                done.append(Befell("wind_location", table_roll=found[0], text=found[1]))
                self._log(
                    world_id,
                    by,
                    by_name,
                    None,
                    f"plane_rule:wind_location::::{found[0]}:{found[1]}",
                )
        return done + self._carry(world_id, rule, by, by_name)

    def _carry(
        self,
        world_id: str,
        rule: PlaneRule,
        by: str,
        by_name: str,
        *,
        only: str | None = None,
        days: int = 0,
    ) -> list[Befell]:
        find = self._find(world_id)
        if not rule.each:
            found = roll_on(rule.table, find, self._dice)
            if found is None:
                return []
            self._log(
                world_id, by, by_name, None, f"plane_rule:{rule.key}::::{found[0]}:{found[1]}"
            )
            return [Befell(rule.key, table_roll=found[0], text=found[1])]
        spared = set(self.spared(world_id, rule.key))
        done: list[Befell] = []
        for pc in self._party(world_id):
            if pc.id in spared or (only is not None and pc.id != only) or pc.in_wizard:
                continue
            roll = dc = None
            struck = True
            if rule.save is not None:
                ability, dc = rule.save
                if rule.key == "yearning":
                    dc += days
                roll = self._save(pc.id, ability)
                struck = roll < dc
            table_roll, text = None, ""
            if struck:
                if rule.effect == "exhaustion":
                    self._tire(pc.id)
                elif rule.effect == "table" and (found := roll_on(rule.table, find, self._dice)):
                    table_roll, text = found
                if rule.cure is not None or rule.effect == "carry":
                    ability, cure_dc = rule.cure or (rule.save[0] if rule.save else "", dc or 0)
                    self._afflictions.befall(
                        world_id,
                        pc.id,
                        rule.key,
                        text,
                        ability=ability,
                        dc=cure_dc if ability else None,
                    )
            befell = Befell(rule.key, pc.id, pc.name or "?", roll, dc, struck, table_roll, text)
            done.append(befell)
            self._log(
                world_id,
                by,
                by_name,
                pc,
                f"plane_rule:{rule.key}:{_n(roll)}:{_n(dc)}:{int(struck)}:{_n(table_roll)}:{text}",
            )
        return done

    def _log(
        self, world_id: str, by: str, by_name: str, pc: PlayerCharacter | None, what: str
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{pc.id}" if pc is not None else "",
                    subject_name=(pc.name or "?") if pc is not None else "",
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=Visibility.PUBLIC,
                    scope=Scope(world=world_id),
                )
            ]
        )


def befell_of(what: str, subject: str, name: str) -> Befell | None:
    """A rule's doing read back from the log; None for anything else."""
    head, _, rest = what.partition(":")
    if head != "plane_rule":
        return None
    parts = rest.split(":", 5)
    if len(parts) < 6:
        return None
    rule, roll, dc, struck, table_roll, text = parts

    def number(value: str) -> int | None:
        return int(value) if value.isdigit() else None

    return Befell(
        rule,
        subject.removeprefix("character:"),
        name,
        number(roll),
        number(dc),
        struck != "0",
        number(table_roll),
        text,
    )


def _n(value: int | None) -> str:
    return "" if value is None else str(value)


__all__ = [
    "PLANES",
    "RULES",
    "Befell",
    "befell_of",
    "PlaneRule",
    "PlaneService",
    "roll_on",
    "rules_of",
]
