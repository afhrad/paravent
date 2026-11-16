"""What a bastion's orders yield, read from each facility's text in the world's books.

A special facility's text says what its orders bring, and much of it is plain enough
to carry out: a Barrack recruits "up to four Bastion Defenders", a Gaming Hall's
winnings come off a d100 table, a Garden's harvest is "100 days' worth of Rations",
the Sanctuary crafts a Holy Symbol "and costs no money", the Arcane Study a blank book
for 10 GP, the Menagerie buys a creature at the table's price, the Armory costs 100 GP
plus 100 GP a defender (halved with a Smithy), and the Smithy and the Workshop craft
what their tools make by the crafting rules. This module reads those texts into
options (``options_of``) and carries a chosen one out on the bastion turn
(``OrderYields``): coins paid or won, items into the pack, defenders on the roster.
An item that takes longer to craft than the week arrives on a later turn. What the
text leaves to the table (lore, a charm, a magic item to choose) is shown as an
option without a yield.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.book_tables import count_word, roll_amount, span
from app.services.dice import Dice
from app.services.downtime import crafting_days

DAY = 24 * 60
# Crafting finished within the turn's week arrives with it.
WEEK_DAYS = 7
_ITEM = re.compile(r"\{@item ([^|}]+)\|?([^|}]*)\|?[^}]*\}")
_COUNTED = re.compile(
    r"(\b[\w-]+\b)\s+(?:days' worth of |vials? of |flasks? of |applications? of )?"
    r"\{@item ([^|}]+)\|?([^|}]*)"
)
# A tag in brackets after another names the same thing ("Druidic Focus (wooden staff)").
_ASIDE = re.compile(r"\s*\(\{@item [^}]*\}\)")
_COST = re.compile(r"costs you ([\d,]+) GP(?! per)", re.IGNORECASE)
_RECRUIT = re.compile(r"up to ([\w-]+) Bastion Defenders are recruited", re.IGNORECASE)
_QUARTERS = re.compile(
    r"(?:quarters for|accommodate) up to ([\w-]+) Bastion Defenders", re.IGNORECASE
)
_ARMORY = re.compile(
    r"costs you ([\d,]+) GP plus an extra ([\d,]+) GP for each Bastion Defender", re.IGNORECASE
)
_HALVED = re.compile(r"\{@facility ([^|}]+)[^}]*\}, the total cost is halved", re.IGNORECASE)
_TOOL = re.compile(
    r"crafts? anything that can be made with (?:the tools you chose|\{@item ([^|}]+))",
    re.IGNORECASE,
)
_GP_DICE = re.compile(r"\{@dice ([^}|]+)[^}]*\}\s*GP", re.IGNORECASE)


@dataclass(frozen=True)
class OrderOption:
    """One thing an order can do in a facility, and what it yields."""

    title: str
    items: tuple[tuple[str, int], ...] = ()  # (reference, count) into the pack
    cost: int = 0  # copper, paid on the turn
    per_defender: int = 0  # copper more for each Bastion Defender
    halved_with: str = ""  # a facility whose presence halves the cost
    defenders: int = 0  # Bastion Defenders recruited
    capacity: int = 0  # how many defenders one such facility houses (0: no limit)
    winnings: tuple[tuple[int, int, str], ...] = ()  # (low, high, dice) on a d100, GP
    tool: str = ""  # crafts what this tool makes ("*" the Workshop's tools)
    half_price: bool = False  # costs half the item's price ("you must pay half")

    @property
    def automatic(self) -> bool:
        """Whether the turn carries it out (else its text is the table's)."""
        return bool(self.items or self.defenders or self.winnings or self.tool or self.cost)


def _text(entries: Any) -> str:
    """Every string under ``entries``, joined."""
    if isinstance(entries, str):
        return entries
    if isinstance(entries, dict):
        return " ".join(_text(entries.get(k)) for k in ("entry", "entries", "items"))
    if isinstance(entries, list):
        return " ".join(_text(e) for e in entries)
    return ""


def _gp(text: str) -> int:
    return int(text.replace(",", "")) * 100


def _items(text: str) -> list[tuple[str, int]]:
    """The items a text yields, each with its count ("ten {@item Candle}" → 10)."""
    out: list[tuple[str, int]] = []
    counted = {(m[2].lower()): (count_word(m[1]) or 1) for m in _COUNTED.finditer(text)}
    for found in _ITEM.finditer(text):
        name, source = found[1], found[2] or ""
        ref = f"{name}|{source}" if source else name
        out.append((ref, counted.get(name.lower(), 1)))
    return out


def _cost(text: str) -> int:
    if re.search(r"costs no money", text, re.IGNORECASE):
        return 0
    found = _COST.search(text)
    return _gp(found[1]) if found else 0


def _from_text(title: str, text: str) -> list[OrderOption]:
    """The options one order's text describes: one per item it offers a choice of."""
    tool = _TOOL.search(text)
    if tool is not None:
        return [OrderOption(title, tool=tool[1] or "*")]
    recruit = _RECRUIT.search(text)
    if recruit is not None:
        return [OrderOption(title, defenders=count_word(recruit[1]) or 0)]
    armory = _ARMORY.search(text)
    if armory is not None:
        halved = _HALVED.search(text)
        return [
            OrderOption(
                title,
                cost=_gp(armory[1]),
                per_defender=_gp(armory[2]),
                halved_with=halved[1] if halved else "",
            )
        ]
    if re.search(
        r"magic item chosen by you|roll on the|which you must pay|DM decides", text, re.IGNORECASE
    ):
        return [OrderOption(title)]
    items = _items(_ASIDE.sub("", text))
    cost = _cost(text)
    half = bool(re.search(r"pay half the [\w' ]+cost", text, re.IGNORECASE))
    if len(items) > 1:
        return [
            OrderOption(f"{title}: {ref.partition('|')[0]}", (item,), cost, half_price=half)
            for item in items
            for ref in [item[0]]
        ]
    return [OrderOption(title, tuple(items), cost, half_price=half)]


def _table_rows(entry: Any) -> list[list[str]]:
    rows = entry.get("rows") if isinstance(entry, dict) else None
    return [
        [_text(c) if not isinstance(c, str) else c for c in r]
        for r in rows or []
        if isinstance(r, list)
    ]


def _walk(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for key in ("entries", "items"):
            yield from _walk(node.get(key))
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child)


_EQUIPPED = re.compile(r"equipped with (\w+) different kinds of", re.IGNORECASE)


def tool_choice(data: dict[str, Any]) -> tuple[int, tuple[str, ...]]:
    """The tools a facility is equipped with when added, chosen from a list ("six
    different kinds of Artisan's Tools, chosen from the following"): how many, and the
    item references to choose from; ``(0, ())`` for a facility without the choice."""
    for node in _walk(data.get("entries")):
        strings = [e for e in node.get("entries", []) if isinstance(e, str)]
        found = next((m for m in map(_EQUIPPED.search, strings) if m), None)
        if found is None:
            continue
        lists = [e for e in node.get("entries", []) if isinstance(e, dict) and e.get("items")]
        refs = [
            f"{m[1]}|{m[2]}" if m[2] else m[1]
            for entry in lists
            for item in entry["items"]
            if isinstance(item, str)
            for m in [_ITEM.search(item)]
            if m
        ]
        if refs:
            return count_word(found[1]) or len(refs), tuple(refs)
    return 0, ()


def options_of(data: dict[str, Any]) -> dict[str, list[OrderOption]]:
    """A facility's options per order, read from its text (empty for a facility whose
    text names none)."""
    out: dict[str, list[OrderOption]] = {}
    capacity = 0
    whole = _text(data.get("entries"))
    room = _QUARTERS.search(whole)
    if room is not None:
        capacity = count_word(room[1]) or 0
    for node in _walk(data.get("entries")):
        name = str(node.get("name", "")).strip()
        order, sep, title = name.partition(":")
        order = order.strip().lower()
        if not sep or order not in ("craft", "harvest", "recruit", "research", "trade", "empower"):
            continue
        title = title.strip()
        if node.get("type") == "item":
            # "Craft: Arcane Focus. You commission…": the title leads its text.
            text = _text(node)
            head, dot, rest = text.partition(". ")
            out.setdefault(order, []).extend(
                _from_text(head.strip() if dot else order, rest or text)
            )
            continue
        text = " ".join(e for e in node.get("entries", []) if isinstance(e, str))
        tables = [
            e for e in node.get("entries", []) if isinstance(e, dict) and e.get("type") == "table"
        ]
        found = _special(title, text, tables)
        if found is None:
            found = _from_text(title, text)
        if capacity:
            found = [
                OrderOption(**{**o.__dict__, "capacity": capacity}) if o.defenders else o
                for o in found
            ]
        out.setdefault(order, []).extend(found)
    return out


def _special(title: str, text: str, tables: Sequence[dict[str, Any]]) -> list[OrderOption] | None:
    """Orders whose yield sits in a table: winnings on a d100, a garden's harvest by
    its type, a menagerie's creatures by price."""
    for table in tables:
        rows = _table_rows(table)
        if not rows:
            continue
        if all(span(r[0]) for r in rows) and any(_GP_DICE.search(r[-1]) for r in rows):
            winnings = []
            for row in rows:
                bounds = span(row[0])
                dice = _GP_DICE.search(row[-1])
                if bounds and dice:
                    winnings.append((bounds[0], bounds[1], dice[1]))
            return [OrderOption(title, winnings=tuple(winnings))]
        if len(rows[0]) >= 3 and "{@creature" in rows[0][0]:
            out = []
            for row in rows:
                creature = re.search(r"\{@creature ([^|}]+)", row[0])
                price = re.search(r"([\d,]+)\s*GP", row[-1])
                if creature and price:
                    out.append(
                        OrderOption(
                            f"{creature[1]} ({row[-1].strip()})", cost=_gp(price[1]), defenders=1
                        )
                    )
            return out
        if (
            len(rows[0]) >= 3
            and "as noted in the" in text
            and "{@item" in " ".join(r[-1] for r in rows)
        ):
            out = []
            for row in rows:
                for part in re.split(r",? or ", row[-1]):
                    items = _items(part)
                    if items:
                        name = items[0][0].partition("|")[0]
                        out.append(
                            OrderOption(f"{row[0]}: {name} ×{items[0][1]}", tuple(items[:1]))
                        )
            return out
    return None


@dataclass(frozen=True)
class Yield:
    """What one facility's order brought on a turn, as a line for the page."""

    facility: str
    kind: str  # "defenders", "coins", "item", "pending", "unpaid", "text"
    payload: str = ""

    @property
    def line(self) -> str:
        return f"{self.facility}|{self.kind}|{self.payload}"


class OrderYields:
    """Carries out the chosen options of a bastion's orders on its turn."""

    def __init__(
        self,
        *,
        options: Callable[[str], dict[str, list[OrderOption]]],
        pay: Callable[[str, int, str], bool],
        earn: Callable[[str, int], None],
        give: Callable[[str, str, int], None],
        price: Callable[[str], int],
        dice: Dice | None = None,
    ) -> None:
        """``options(facility ref)`` reads a facility's options; ``pay(character,
        copper, why)`` takes coins (False: the purse is short); ``earn`` gives coins;
        ``give(character, item, count)`` puts items in the pack; ``price(item)`` an
        item's list price in copper."""
        self._options = options
        self._pay = pay
        self._earn = earn
        self._give = give
        self._price = price
        self._dice = dice or Dice()

    def chosen(self, ref: str, order: str, option: str) -> OrderOption | None:
        """The option a facility's order was given (its index, "" for the first)."""
        found = self._options(ref).get(order, [])
        index = int(option.partition("|")[0]) if option.partition("|")[0].isdigit() else 0
        return found[index] if 0 <= index < len(found) else None

    def run(
        self,
        character_id: str,
        orders: Sequence[tuple[str, str, str]],
        *,
        defenders: int,
        facilities: Sequence[str],
        at: int | None,
    ) -> tuple[list[Yield], int, list[str]]:
        """Each ``(facility ref, order, option)``: what it yielded, the defenders
        recruited, and crafts still under way (``"<ready minute>|<item>"``)."""
        out: list[Yield] = []
        recruited = 0
        pending: list[str] = []
        names = {f.partition("|")[0].lower() for f in facilities}
        for ref, order, option in orders:
            chosen = self.chosen(ref, order, option)
            if chosen is None or not chosen.automatic:
                out.append(Yield(ref, "text", chosen.title if chosen else order))
                continue
            cost = chosen.cost + chosen.per_defender * (defenders + recruited)
            if chosen.halved_with and chosen.halved_with.lower() in names:
                cost //= 2
            item = option.partition("|")[2] if chosen.tool else ""
            if chosen.tool:
                if not item:
                    out.append(Yield(ref, "text", chosen.title))
                    continue
                cost += self._price(item) // 2
            if chosen.half_price and chosen.items:
                cost += self._price(chosen.items[0][0]) // 2
            if cost and not self._pay(character_id, cost, f"bastion {ref.partition('|')[0]}"):
                out.append(Yield(ref, "unpaid", str(cost)))
                continue
            if chosen.defenders:
                room = chosen.defenders
                if chosen.capacity:
                    housed = sum(1 for f in facilities if f.lower() == ref.lower())
                    room = max(0, min(room, chosen.capacity * housed - defenders - recruited))
                recruited += room
                out.append(Yield(ref, "defenders", str(room)))
            if chosen.winnings:
                roll = self._dice.die(100)
                row = next((w for w in chosen.winnings if w[0] <= roll <= w[1]), None)
                won = roll_amount(row[2], self._dice) * 100 if row else 0
                if won:
                    self._earn(character_id, won)
                out.append(Yield(ref, "coins", f"{won}|{roll}"))
            for item_ref, count in chosen.items:
                self._give(character_id, item_ref, count)
                out.append(Yield(ref, "item", f"{item_ref}|{count}"))
            if item:
                days = crafting_days(self._price(item), "one")
                if days <= WEEK_DAYS or at is None:
                    self._give(character_id, item, 1)
                    out.append(Yield(ref, "item", f"{item}|1"))
                else:
                    ready = at + days * DAY
                    pending.append(f"{ready}|{item}")
                    out.append(Yield(ref, "pending", f"{item}|{ready}"))
            if cost:
                out.append(Yield(ref, "paid", str(cost)))
        return out, recruited, pending

    def deliver(
        self, character_id: str, pending: Sequence[str], at: int | None
    ) -> tuple[list[Yield], list[str]]:
        """Crafts that are ready by ``at`` go into the pack: those, and what still waits."""
        done, waiting = [], []
        for entry in pending:
            ready, _, item = entry.partition("|")
            if at is not None and ready.isdigit() and int(ready) <= at:
                self._give(character_id, item, 1)
                done.append(Yield("", "item", f"{item}|1"))
            else:
                waiting.append(entry)
        return done, waiting


__all__ = ["OrderOption", "OrderYields", "Yield", "options_of", "tool_choice"]
