"""Downtime activities of *Xanathar's Guide to Everything*, for a world that has it.

Each takes a workweek (5 days) of a character's free downtime days, costs what the
book says, and is resolved on the spot: the checks are rolled on the character's
sheet into its log, the total is looked up in the book's own table, money changes
hands in the purse, and a complication may follow (a 10% chance each workweek, or
when the activity says). The book's tables stay in the corpus and are read through
``tables(caption)``; only the numbers that drive the rolls are written here.

* **Carousing** with the lower, middle or upper class (10, 50 or 250 gp): Persuasion
  on the Carousing table; complications by class.
* **Crime** (25 gp): Stealth, Dexterity with thieves' tools, and Investigation,
  Perception or Deception against a DC of 10, 15, 20 or 25 chosen for the loot; none
  succeeds: caught, fined the loot (paid as far as the purse goes) and jailed a week
  per 25 gp; one: escaped, with a complication; two: half the loot; three: all of it.
* **Gambling** for a stake of 10–1000 gp: Insight, Deception and Intimidation, each
  against 5 + 2d10; the successes decide what the stake wins or loses.
* **Pit fighting**: Athletics, Acrobatics and a Constitution check with a roll of the
  largest Hit Die added, each against 5 + 2d10; winnings by successes.
* **Religious service**: Religion or Persuasion on the Religious Service table.
* **Research** (50 gp, up to 300 gp more for +1 per 50): an Intelligence check on the
  Research Outcomes table.
* **Work**: Athletics, Acrobatics, Performance or an Intelligence check with tools on
  the Wages table (its "+ 25 gp" paid).
* **Selling a magic item** (25 gp): Persuasion on the Magic Item Offer table against the
  item's base price by rarity; the offer waits for the character to take or refuse.
* **Buying a magic item** (100 gp, more weeks and gold for a better seller): Persuasion
  on the Buying Magic Items table names a magic item table of the 2014 guide to roll
  on; the items come up with asking prices by rarity (halved for consumables) and wait
  to be bought.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.models.gamestate import DowntimeProject
from app.models.pc import PlayerCharacter
from app.services.book_tables import Rows, copper, roll_amount, rolled, row_for
from app.services.dice import Dice
from app.services.downtime import DowntimeError, DowntimeService

ACTIVITIES = (
    "carouse",
    "crime",
    "gamble",
    "pit_fight",
    "service",
    "study",
    "job",
    "sell_magic",
    "buy_magic",
)
WORKWEEK = 5
# Complications come on a d100 of this or less each workweek.
COMPLICATION_ON = 10
CAROUSING = {"lower": 1000, "middle": 5000, "upper": 25000}
CRIME_DCS = (10, 15, 20, 25)
CRIME_COST = 2500
CRIME_THIRD = ("investigation", "perception", "deception")
STAKES = (1000, 100000)
RESEARCH_COST = 5000
RESEARCH_MOST = 30000
SELLING_COST = 2500
BUYING_COST = 10000
BUYING_WEEKS = 9
JOBS = ("athletics", "acrobatics", "performance", "tools")
SERVICE = ("religion", "persuasion")
RARITIES = ("common", "uncommon", "rare", "very rare", "legendary")
_RARITY_WORDS = {r: r for r in RARITIES} | {"very-rare": "very rare"}
_ROLL_ON = re.compile(r"\{@dice ([^}|]+)[^}]*\}\s*times? on \{@table Magic Item Table ([A-I])")

# A table by its caption, from the world's copy of the book.
Tables = Callable[[str], Rows | None]


@dataclass(frozen=True)
class Offered:
    """A magic item that came up for sale, and its asking price in copper."""

    name: str
    source: str
    rarity: str
    consumable: bool = False


@dataclass
class Outcome:
    """What an activity came to: the lines to show, the copper won (or lost), and any
    offers waiting."""

    lines: list[str] = field(default_factory=list)
    earned: int = 0
    offers: list[str] = field(default_factory=list)


class Activities:
    """Runs the book's downtime activities against a character's sheet and purse."""

    def __init__(
        self,
        downtime: DowntimeService,
        *,
        check: Callable[[PlayerCharacter, str, str], int],
        proficiency: Callable[[PlayerCharacter], int],
        hit_die: Callable[[PlayerCharacter], int],
        pay: Callable[[str, int, str], None],
        earn: Callable[[str, int, str], None],
        purse: Callable[[str], int],
        magic_items: Callable[[str, int], list[Offered]],
        give: Callable[[str, str], None],
        sell: Callable[[str, str, int], None],
        dice: Dice | None = None,
    ) -> None:
        """``check(pc, kind, subject)`` rolls a check on the sheet into its log ("skill"
        and a skill, "check" and an ability) and gives the total; ``proficiency`` its
        bonus; ``hit_die`` its largest Hit Die's faces; ``pay`` and ``earn`` move coins
        (``pay`` raising when the purse is short); ``purse`` what it holds in copper;
        ``magic_items(table letter, times)`` rolls the 2014 guide's magic item table;
        ``give(character, item)`` puts a bought item in the pack and ``sell(character,
        item, price)`` sells a carried one."""
        self._downtime = downtime
        self._check = check
        self._proficiency = proficiency
        self._hit_die = hit_die
        self._pay = pay
        self._earn = earn
        self._purse = purse
        self._magic_items = magic_items
        self._give = give
        self._sell = sell
        self._dice = dice or Dice()

    def take(self, pc: PlayerCharacter, project_id: str, index: int, expected: str = "") -> str:
        """Buy an item offered to a buying project, or take the offer a selling one
        found: the coins change hands and the offer is gone. The item's reference.
        ``expected`` is the offer the button was drawn for, refused if another is there."""
        project = next((p for p in self._downtime.of(pc.id).projects if p.id == project_id), None)
        if project is None or project.kind not in ("buy_magic", "sell_magic"):
            raise DowntimeError("downtime.error.project")
        if not 0 <= index < len(project.offers) or expected not in ("", project.offers[index]):
            raise DowntimeError("downtime.error.project")
        offer = project.offers[index]
        ref, _, price = offer.rpartition("|")
        if not price.isdigit():
            raise DowntimeError("downtime.error.project")
        if project.kind == "buy_magic":
            self._pay(pc.id, int(price), f"buy {ref}")
            self._give(pc.id, ref)
        else:
            self._sell(pc.id, ref, int(price))
        self._downtime.take_offer(pc.id, project_id, index)
        return ref

    def refuse(self, pc: PlayerCharacter, project_id: str, index: int, expected: str = "") -> None:
        """Let an offer go (``expected``: as for :meth:`take`)."""
        project = next((p for p in self._downtime.of(pc.id).projects if p.id == project_id), None)
        if project is None or not 0 <= index < len(project.offers):
            raise DowntimeError("downtime.error.project")
        if expected not in ("", project.offers[index]):
            raise DowntimeError("downtime.error.project")
        self._downtime.take_offer(pc.id, project_id, index)

    def run(
        self,
        pc: PlayerCharacter,
        kind: str,
        options: Mapping[str, str],
        tables: Tables,
        *,
        by: str,
        by_name: str,
    ) -> DowntimeProject:
        """Do an activity now: its workweeks come off the free days, its cost off the
        purse; the outcome is kept as a finished project and logged."""
        if kind not in ACTIVITIES:
            raise DowntimeError("downtime.error.kind")
        weeks = 1
        if kind == "buy_magic":
            weeks += _bounded(options.get("weeks"), 0, BUYING_WEEKS - 1)
        days = WORKWEEK * weeks
        if self._downtime.of(pc.id).days < days:
            raise DowntimeError("downtime.error.days")
        handler = getattr(self, f"_{kind}")
        cost, label = self._cost(pc, kind, options)
        if cost > self._purse(pc.id):
            raise DowntimeError("play.error.no_money")
        # The activity first: one its options refuse costs nothing.
        outcome: Outcome = handler(pc, options, tables, weeks)
        if cost:
            self._pay(pc.id, cost, f"{kind} {label}".strip())
        if outcome.earned > 0:
            self._earn(pc.id, outcome.earned, kind)
        elif outcome.earned < 0:
            owed = -outcome.earned
            paid = min(owed, self._purse(pc.id))
            if paid:
                self._pay(pc.id, paid, kind)
            if owed > paid:
                outcome.lines.append(f"debt:{owed - paid}")
        return self._downtime.record(
            pc,
            kind,
            label,
            days=days,
            paid=cost,
            result=outcome.lines,
            offers=outcome.offers,
            by=by,
            by_name=by_name,
        )

    # -- costs ---------------------------------------------------------------------

    def _cost(self, pc: PlayerCharacter, kind: str, options: Mapping[str, str]) -> tuple[int, str]:
        """What the activity costs up front (copper) and the label it is kept under."""
        if kind == "carouse":
            social = options.get("class", "lower")
            if social not in CAROUSING:
                raise DowntimeError("downtime.error.option")
            return CAROUSING[social], social
        if kind == "crime":
            return CRIME_COST, ""
        if kind == "gamble":
            stake = _bounded(options.get("stake"), STAKES[0] // 100, STAKES[1] // 100) * 100
            return stake, str(stake)
        if kind == "study":
            extra = _bounded(options.get("extra"), 0, RESEARCH_MOST // 100) * 100
            topic = " ".join(str(options.get("label", "")).split())[:200]
            if not topic:
                raise DowntimeError("downtime.error.label")
            return RESEARCH_COST + extra - extra % 5000, topic
        if kind == "sell_magic":
            return SELLING_COST, str(options.get("item", ""))
        if kind == "buy_magic":
            extra = _bounded(options.get("extra"), 0, 1000) * 10000
            return BUYING_COST + extra, ""
        return 0, ""

    # -- the activities ------------------------------------------------------------

    def _carouse(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        social = options.get("class", "lower")
        total = self._check(pc, "skill", "persuasion")
        out = Outcome([f"check:persuasion:{total}"])
        self._row(out, tables("Carousing"), total)
        self._chance(out, tables(f"{social.title()}-Class Carousing Complications"))
        return out

    def _crime(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        dc = _bounded(options.get("dc"), 10, 25)
        if dc not in CRIME_DCS:
            raise DowntimeError("downtime.error.option")
        third = options.get("third", "investigation")
        if third not in CRIME_THIRD:
            raise DowntimeError("downtime.error.option")
        totals = [
            ("stealth", self._check(pc, "skill", "stealth")),
            ("thieves_tools", self._check(pc, "tool", "dex|Thieves' Tools")),
            (third, self._check(pc, "skill", third)),
        ]
        out = Outcome([f"check:{name}:{total}:{dc}:{int(total >= dc)}" for name, total in totals])
        wins = sum(1 for _, total in totals if total >= dc)
        loot_row = next(
            (r for r in tables("Crime; Loot Value") or [] if r and r[0] == str(dc)), None
        )
        loot = copper(loot_row[1]) if loot_row else 0
        if wins == 0:
            out.lines.append(f"caught:{loot}:{loot // 2500}")
            out.earned = -loot
        elif wins == 1:
            out.lines.append("escaped")
            self._complication(out, tables("Crime Complications"))
        else:
            out.earned = loot if wins == 3 else loot // 2
            out.lines.append(f"won:{out.earned}")
        return out

    def _gamble(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        stake = _bounded(options.get("stake"), STAKES[0] // 100, STAKES[1] // 100) * 100
        out = Outcome()
        wins = 0
        for skill in ("insight", "deception", "intimidation"):
            dc = 5 + self._dice.roll("2d10").total
            total = self._check(pc, "skill", skill)
            wins += total >= dc
            out.lines.append(f"check:{skill}:{total}:{dc}:{int(total >= dc)}")
        self._row(out, tables("Gambling Results"), wins, by_successes=True)
        # The stake is paid already: what comes back.
        back = {0: 0, 1: stake // 2, 2: stake + stake * 3 // 2, 3: stake * 3}[wins]
        if wins == 0:
            out.lines.append(f"debt:{stake}")
        out.earned = back
        if back:
            out.lines.append(f"won:{back}")
        self._chance(out, tables("Gambling Complications"))
        return out

    def _pit_fight(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        out = Outcome()
        wins = 0
        die = self._hit_die(pc)
        for name, kind, subject in (
            ("athletics", "skill", "athletics"),
            ("acrobatics", "skill", "acrobatics"),
            ("con", "check", "con"),
        ):
            dc = 5 + self._dice.roll("2d10").total
            total = self._check(pc, kind, subject)
            if name == "con":
                total += self._dice.die(die)
            wins += total >= dc
            out.lines.append(f"check:{name}:{total}:{dc}:{int(total >= dc)}")
        row = self._row(out, tables("Pit Fighting Results"), wins, by_successes=True)
        out.earned = copper(row[1]) if row else 0
        if out.earned:
            out.lines.append(f"won:{out.earned}")
        self._chance(out, tables("Pit Fighting Complications"))
        return out

    def _service(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        skill = options.get("skill", "religion")
        if skill not in SERVICE:
            raise DowntimeError("downtime.error.option")
        total = self._check(pc, "skill", skill)
        out = Outcome([f"check:{skill}:{total}"])
        self._row(out, tables("Religious Service"), total)
        self._chance(out, tables("Religious Service Complications"))
        return out

    def _study(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        extra = _bounded(options.get("extra"), 0, RESEARCH_MOST // 100) * 100
        bonus = min(6, extra // 5000)
        total = self._check(pc, "check", "int") + bonus
        out = Outcome([f"check:int:{total}" + (f":+{bonus}" if bonus else "")])
        self._row(out, tables("Research Outcomes"), total)
        self._chance(out, tables("Research Complications"))
        return out

    def _job(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        how = options.get("skill", "athletics")
        if how not in JOBS:
            raise DowntimeError("downtime.error.option")
        if how == "tools":
            # An Intelligence check with a tool the character knows (the one asked for,
            # else the first): its proficiency is part of the roll.
            known = [o.name for o in pc.tools]
            wanted = str(options.get("tool", ""))
            tool = next((k for k in known if k.lower() == wanted.lower()), None)
            tool = tool or next(iter(known), "")
            if not tool:
                raise DowntimeError("downtime.error.no_tool")
            total = self._check(pc, "tool", f"int|{tool}")
        else:
            total = self._check(pc, "skill", how)
        out = Outcome([f"check:{how}:{total}"])
        row = self._row(out, tables("Work; Wages"), total)
        if row is not None and "+" in row[1]:
            out.earned = copper(row[1].partition("+")[2])
        self._chance(out, tables("Work Complications"))
        return out

    def _sell_magic(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, _w: int
    ) -> Outcome:
        ref = str(options.get("item", ""))
        rarity = str(options.get("rarity", "")).lower()
        if rarity not in RARITIES:
            raise DowntimeError("downtime.error.item")
        base_row = next(
            (
                r
                for r in tables("Selling a Magic Item; Magic Item Base Prices") or []
                if r and r[0].lower() == rarity
            ),
            None,
        )
        base = copper(base_row[1]) if base_row else 0
        total = self._check(pc, "skill", "persuasion")
        out = Outcome([f"check:persuasion:{total}"])
        row = row_for(tables("Selling a Magic Item; Magic Item Offer") or [], total)
        share = int(re.sub(r"\D", "", row[1].split("%")[0]) or 0) if row else 0
        offer = base * share // 100
        out.lines.append(f"offer:{offer}")
        out.offers = [f"{ref}|{offer}"]
        self._chance(out, tables("Magic Item Sale Complications"))
        return out

    def _buy_magic(
        self, pc: PlayerCharacter, options: Mapping[str, str], tables: Tables, weeks: int
    ) -> Outcome:
        extra = _bounded(options.get("extra"), 0, 1000)
        bonus = min(10, (weeks - 1) + extra)
        total = self._check(pc, "skill", "persuasion") + bonus
        out = Outcome([f"check:persuasion:{total}" + (f":+{bonus}" if bonus else "")])
        row = row_for(tables("Buying a Magic Item; Buying Magic Items") or [], total)
        found = _ROLL_ON.search(row[1]) if row and len(row) > 1 else None
        prices = tables("Buying a Magic Item; Magic Item Price") or []
        if row is not None and found is not None:
            times = max(1, roll_amount(found[1], self._dice))
            # The row as read, with the number of rolls it came to.
            counted = row[1].replace(
                found[0], f"{times} times on {{@table Magic Item Table {found[2]}"
            )
            out.lines.append(f"row:{rolled(counted, self._dice)}")
            for item in self._magic_items(found[2], times):
                price_row = next((r for r in prices if r and r[0].lower() == item.rarity), None)
                price = roll_amount(_dice_of(price_row[1]), self._dice) * 100 if price_row else 0
                if item.consumable:
                    price //= 2
                if price > 0:
                    out.offers.append(f"{item.name}|{item.source}|{price}")
        if not out.offers:
            out.lines.append("nothing")
        for _ in range(weeks):
            if self._dice.die(100) <= COMPLICATION_ON:
                self._complication(out, tables("Magic Item Purchase Complications"))
                break
        return out

    # -- helpers -------------------------------------------------------------------

    def _row(
        self, out: Outcome, rows: Rows | None, total: int, *, by_successes: bool = False
    ) -> list[str] | None:
        """The table's row for ``total`` (or for that many successes), as a line."""
        if not rows:
            return None
        if by_successes:
            row = next((r for r in rows if r and r[0].strip().startswith(str(total))), None)
        else:
            row = row_for(rows, total)
        if row is not None and len(row) > 1:
            out.lines.append(f"row:{rolled(row[1], self._dice)}")
        return row

    def _chance(self, out: Outcome, rows: Rows | None) -> None:
        """A complication on a d100 of 10 or less."""
        if self._dice.die(100) <= COMPLICATION_ON:
            self._complication(out, rows)

    def _complication(self, out: Outcome, rows: Rows | None) -> None:
        if not rows:
            out.lines.append("complication:")
            return
        row = rows[self._dice.die(len(rows)) - 1]
        text = rolled(row[-1], self._dice)
        out.lines.append(f"complication:{text.rstrip('*')}")
        if text.rstrip().endswith("*"):
            out.lines.append("rival:")  # the book marks those a rival is behind


def _bounded(raw: str | None, low: int, high: int) -> int:
    text = str(raw or "").strip()
    value = int(text) if text.isdigit() else low
    return max(low, min(high, value))


def _dice_of(text: str) -> str:
    """``"{@dice 1d6 × 100} gp"`` → ``"1d6 × 100"``; a plain amount stays."""
    found = re.search(r"\{@dice ([^}|]+)", text)
    if found is not None:
        return found[1].replace(",", "")
    return re.sub(r"[^\dd×x+() ]", "", text.replace(",", ""))


def rarity_of(raw: object) -> str:
    """An item's rarity as the activities read it ("" for none or unknown)."""
    return _RARITY_WORDS.get(str(raw or "").lower(), "")


def offers_of(project: DowntimeProject) -> Sequence[tuple[str, str, int]]:
    """A buying project's offers as (name, source, price)."""
    out = []
    for offer in project.offers:
        name, _, rest = offer.partition("|")
        source, _, price = rest.partition("|")
        if price.isdigit():
            out.append((name, source, int(price)))
    return out


__all__ = [
    "ACTIVITIES",
    "CAROUSING",
    "CRIME_DCS",
    "CRIME_THIRD",
    "JOBS",
    "RARITIES",
    "SERVICE",
    "WORKWEEK",
    "Activities",
    "Offered",
    "Outcome",
    "offers_of",
    "rarity_of",
]
