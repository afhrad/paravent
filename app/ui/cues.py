"""Sound cues on a page (static/js/sound.js): an element that should be heard when it
appears carries ``data-cue="<sound>|<key>"``. The page's script plays a cue the first time
its key appears after the page was loaded — never for what was there already, never twice
— and only the most urgent of several arriving together.

The sounds are the players': a handout, their turn, a check or save called for them, a
level granted, a shop opened or a chase begun. The gamemaster hears only the X-card's tick,
so a sheet or fight view open on the gamemaster's screen stays quiet.
"""

from __future__ import annotations

from app.ui.context import Page

# The sounds the script knows, most urgent first.
PLAYER_SOUNDS = ("turn", "check", "level", "handout", "bell")
GAMEMASTER_SOUNDS = ("xcard",)


def cue(page: Page, sound: str, key: str) -> str | None:
    """The ``data-cue`` value for an element, or None where this reader hears no such
    sound."""
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    if page.identity is None:
        return None
    allowed = GAMEMASTER_SOUNDS if gamemaster else PLAYER_SOUNDS
    return f"{sound}|{sound}:{key}" if sound in allowed else None


__all__ = ["GAMEMASTER_SOUNDS", "PLAYER_SOUNDS", "cue"]
