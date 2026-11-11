"""Experience points and the levels they reach (documentation/COMBAT.md §6).

The thresholds are the Player's Handbook's *Character Advancement* table, read out of the
rendered book table into ``data/rules/encounterdifficulty.json`` (``advancement``) like
the encounter tables; the 2024 table is the same.

XP **suggests** a level, it never grants one: the gamemaster grants levels
(documentation/CHARGEN-WIZARD.md §9), and is shown when a character's XP has reached a
level beyond what they have and have been granted.

A character whose file has no ``experience`` is not tracked — a hand-written level 11
character is not 11 levels behind. The first award starts them at their level's
threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.models.pc import PlayerCharacter
from app.store.rulestables import load

MAX_LEVEL = 20


@lru_cache(maxsize=1)
def thresholds() -> tuple[int, ...]:
    """The XP each level starts at: ``thresholds()[0]`` is level 1 (0 XP)."""
    table = load()["advancement"]
    return tuple(int(table[str(level)]) for level in range(1, MAX_LEVEL + 1))


def start_of(level: int) -> int:
    """The XP a level starts at (level 5: 6,500)."""
    return thresholds()[max(1, min(MAX_LEVEL, level)) - 1]


def level_for(xp: int) -> int:
    """The level ``xp`` reaches (6,500: level 5)."""
    return max(level for level, start in enumerate(thresholds(), start=1) if xp >= start)


@dataclass(frozen=True)
class Progress:
    """A character's experience against the table."""

    xp: int
    level: int  # the character's level, as played
    granted: int  # levels granted and not yet taken
    floor: int  # the XP the current level starts at
    next: int | None  # the XP the next level starts at; None at 20

    @property
    def reached(self) -> int:
        """The level the XP reaches."""
        return level_for(self.xp)

    @property
    def due(self) -> int:
        """Levels the XP has reached beyond those played and granted."""
        return max(0, self.reached - self.level - self.granted)

    @property
    def fraction(self) -> float:
        """How far through the current level, 0–1 (1 at or past the next threshold)."""
        if self.next is None or self.next <= self.floor:
            return 1.0
        return max(0.0, min(1.0, (self.xp - self.floor) / (self.next - self.floor)))


def progress(pc: PlayerCharacter) -> Progress | None:
    """The character's progress, or None when the table does not track XP for them."""
    if pc.experience is None or not pc.classes:
        return None
    level = pc.total_level
    return Progress(
        pc.experience,
        level,
        pc.pending_levels,
        start_of(level),
        start_of(level + 1) if level < MAX_LEVEL else None,
    )


def award(pc: PlayerCharacter, xp: int) -> None:
    """Add ``xp``; an untracked character starts at their current level's threshold.
    The award's entry in the log is the caller's (it knows who and why)."""
    start = pc.experience if pc.experience is not None else start_of(max(1, pc.total_level))
    pc.experience = start + max(0, xp)


__all__ = ["MAX_LEVEL", "Progress", "award", "level_for", "progress", "start_of", "thresholds"]
