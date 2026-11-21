"""Seeds for a story, rolled on the guides' tables: a villain for an NPC, and the parts of
an adventure.

**A villain** comes from the 2014 guide's tables: what kind of villain it is (*Adventure
Villains*), its scheme (*Villain's Scheme*), its methods (*Villain's Methods*) and its
weakness (*Villain's Weakness*). The 2024 guide has none of these, so a world rolls them
only where it declares the 2014 guide. Scheme and methods are two-step tables: a row
names an objective or kind of method with a die of its own ("Immortality (d4)"), and the
rows beneath it are rolled with that die. Such a result reads "Immortality: Ascend to
godhood".

**An adventure** is rolled on the 2024 guide where the world declares it (as settlements
are): a situation for the party's level band, a hook (patron, supernatural or
happenstance), the climax and how it connects to the next adventure. The parts only the
2014 guide prints join them where the world declares that guide too: a patron, an ally,
the villain, a framing event, a moral quandary and a twist. A world on the 2014 guide
rolls its own tables: the party's goal (dungeon, wilderness, event-based or other), the
same NPCs, the introduction, the climax and the rest.

The tables stay in the user's corpus and are found by caption in the world's books
(app/services/book_tables.py). A part whose table the world lacks is left out. Each part
rolls again on its own, and nothing is kept until the gamemaster takes it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from app.services.book_tables import Rows, row_for, span
from app.services.dice import Dice
from app.services.text import strip_tags

GUIDE_2014 = "DMG"
GUIDE_2024 = "XDMG"
KINDS = ("villain", "adventure")

VILLAIN: dict[str, tuple[str, str]] = {
    "villain": (GUIDE_2014, "Adventure Villains"),
    "scheme": (GUIDE_2014, "Villain's Scheme"),
    "methods": (GUIDE_2014, "Villain's Methods"),
    "weakness": (GUIDE_2014, "Villain's Weakness"),
}
# The parts of an adventure in reading order; a caption of "" is rolled specially.
ADVENTURE_2014: dict[str, tuple[str, str]] = {
    "goal": (GUIDE_2014, ""),
    "patron": (GUIDE_2014, "Adventure Patrons"),
    "ally": (GUIDE_2014, "Adventure Allies"),
    "villain": (GUIDE_2014, "Adventure Villains"),
    "introduction": (GUIDE_2014, "Adventure Introduction"),
    "framing": (GUIDE_2014, "Framing Events"),
    "quandary": (GUIDE_2014, "Moral Quandaries"),
    "twist": (GUIDE_2014, "Twists"),
    "climax": (GUIDE_2014, "Adventure Climax"),
}
ADVENTURE_2024: dict[str, tuple[str, str]] = {
    "situation": (GUIDE_2024, ""),
    "hook": (GUIDE_2024, ""),
    "patron": (GUIDE_2014, "Adventure Patrons"),
    "ally": (GUIDE_2014, "Adventure Allies"),
    "villain": (GUIDE_2014, "Adventure Villains"),
    "framing": (GUIDE_2014, "Framing Events"),
    "quandary": (GUIDE_2014, "Moral Quandaries"),
    "twist": (GUIDE_2014, "Twists"),
    "climax": (GUIDE_2024, "Adventure Climax"),
    "connection": (GUIDE_2024, "Adventure Connections"),
}
# The 2014 party goals by kind; the 2024 hooks; the 2024 situations by level band.
GOALS = {
    "dungeon": "Dungeon Goals",
    "wilderness": "Wilderness Goals",
    "event": "Event-Based Goals",
    "other": "Other Goals",
}
HOOKS = ("Patron Hooks", "Supernatural Hooks", "Happenstance Hooks")
BANDS = ((1, 4), (5, 10), (11, 16), (17, 20))

_DIE_NOTE = re.compile(r"\s*\(\{@dice [^}]*\}\)")

# Finds a table by caption in a book the world declares: its rows, or None.
Finder = Callable[[str, Sequence[str]], Rows | None]


def band_caption(level: int) -> str:
    """The 2024 guide's situations table for a party of ``level``."""
    low, high = next((b for b in BANDS if b[0] <= level <= b[1]), BANDS[0])
    return f"Levels {low}–{high} Adventure Situations"


class SeedGenerator:
    """Rolls a villain's or an adventure's parts on the world's guides."""

    def __init__(
        self,
        find: Finder,
        dice: Dice,
        *,
        kind: str,
        new_rules: bool,
        level: int = 1,
        goal_label: Callable[[str], str] | None = None,
    ) -> None:
        """``kind`` is "villain" or "adventure"; ``new_rules`` when the world plays the
        2024 guide; ``level`` the party's, for the 2024 situations; ``goal_label`` names
        a 2014 goal's kind ("dungeon") in the reader's words."""
        self._find = find
        self._dice = dice
        self._kind = kind
        self._level = level
        self._goal_label = goal_label or (lambda key: key.capitalize())
        self._tables = (
            VILLAIN if kind == "villain" else ADVENTURE_2024 if new_rules else ADVENTURE_2014
        )
        self._rows: dict[tuple[str, str], Rows | None] = {}

    def _table(self, book: str, caption: str) -> Rows | None:
        if (book, caption) not in self._rows:
            self._rows[(book, caption)] = self._find(caption, [book])
        return self._rows[(book, caption)]

    def _choices(self, part: str) -> list[tuple[str, Rows]]:
        """The tables a part may be rolled on, each with its label ("" for none)."""
        book, caption = self._tables[part]
        if part == "goal":
            found = [(k, self._table(book, c)) for k, c in GOALS.items()]
        elif part == "hook":
            found = [("", self._table(book, c)) for c in HOOKS]
        elif part == "situation":
            found = [("", self._table(book, band_caption(self._level)))]
        else:
            found = [("", self._table(book, caption))]
        return [(label, rows) for label, rows in found if rows]

    def parts(self) -> list[str]:
        """The parts the world's books have a table for, in reading order."""
        return [p for p in self._tables if self._choices(p)]

    def source_of(self, part: str) -> str:
        """The book a part is rolled in."""
        return self._tables[part][0]

    def roll_part(self, part: str, current: str = "") -> str:
        """One part rolled (again): not what it is now, where the tables offer another."""
        choices = self._choices(part)
        if not choices:
            return ""
        text = ""
        for _ in range(12):
            label, rows = choices[self._dice.die(len(choices)) - 1]
            text = _roll(rows, self._dice)
            if label:
                text = f"{self._goal_label(label)}: {text}"
            if text and text != current:
                return text
        return text

    def roll(self, keep: Mapping[str, str] | None = None) -> dict[str, str]:
        """Every part rolled, but those in ``keep``."""
        kept = dict(keep or {})
        return {p: kept[p] if kept.get(p) else self.roll_part(p) for p in self.parts()}


def _roll(rows: Rows, dice: Dice) -> str:
    """A row by the die its first column reads; a row that names a die of its own rolls
    the rows beneath it (``["", "1", "Blight"]``) and reads "Kind: result"."""
    groups: list[tuple[list[str], list[list[str]]]] = []
    for row in rows:
        if row and row[0].strip():
            groups.append((row, []))
        elif groups and len(row) > 2:
            groups[-1][1].append(row[1:])
    if not groups:
        return ""
    heads = [g[0] for g in groups]
    head = _pick(heads, dice)
    subs = next(s for h, s in groups if h is head)
    label = _text(head)
    if subs:
        sub = _pick(subs, dice)
        kind = strip_tags(_DIE_NOTE.sub("", label)).strip()
        return f"{kind}: {strip_tags(_text(sub))}".strip()
    return strip_tags(label)


def _pick(rows: Sequence[list[str]], dice: Dice) -> list[str]:
    ends = [end for r in rows if r and (found := span(r[0])) for end in found]
    die = max((e for e in ends if 0 < e < 10_000), default=len(rows))
    total = dice.die(die)
    return row_for(list(rows), total) or list(rows)[(total - 1) % len(rows)]


def _text(row: Sequence[str]) -> str:
    """A row's text: its last cell that says anything."""
    return next((c for c in reversed(row[1:]) if c.strip()), "")


__all__ = ["BANDS", "GOALS", "KINDS", "SeedGenerator", "band_caption"]
