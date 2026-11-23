"""What every page needs to know about the request it is rendering for."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services.auth import Identity
from app.services.calendar import Calendar
from app.services.i18n import Translator
from app.services.player_view import Reader
from app.services.themes import Theme
from app.services.tokens import Placement, Token
from app.services.units import UnitFormat


@dataclass(frozen=True)
class Page:
    """Rendering context: the language, where we are, and who is looking.

    ``path`` is the current URL (path and query), so the language selector can bring
    the user straight back to the page they switched on.
    """

    t: Translator
    path: str = "/"
    identity: Identity | None = None
    # Where each navigation section was left: section root → page (app/ui/nav.py).
    nav_memory: Mapping[str, str] = field(default_factory=dict)
    # What the reader may read (documentation/PLAYER-VIEW.md); None reads everything.
    reader: Reader | None = None
    # The gamemaster looking at a page as a player would (``?as=player``).
    as_player: bool = False
    # The look the reader sees (documentation/THEMES.md); None is Paravent's own.
    theme: Theme | None = None
    # The table's own token of an element (documentation/TOKENS.md): ``(kind, world,
    # id)`` → Token; without it every element shows its kind's disc.
    tokens: Callable[[str, str | None, str], Token] | None = None
    # Where an element's picture sits in its token's frame: ``(world, kind, id)``.
    token_placement: Callable[[str | None, str, str], Placement] | None = None
    # The calendar a world dates its days by: ``world`` → (calendar, the calendar's day
    # of the clock's first day, era), None for plain day numbers (app/services/calendar.py).
    calendars: Callable[[str | None], tuple[Calendar, int, str] | None] | None = None
    # The world the whole table plays in, ``(id, name)``, or None without one
    # (documentation/CURRENT-WORLD.md); and, for the gamemaster's world menu, every
    # world as ``(id, name)`` by name.
    world: tuple[str, str] | None = None
    worlds: tuple[tuple[str, str], ...] = ()
    # The world this page belongs to, ``(id, name)``, when it is a page of one (an NPC,
    # a chapter, a world's list); the gamemaster's banner offers to switch to it when it
    # is not the table's (documentation/CURRENT-WORLD.md §5).
    page_world: tuple[str, str] | None = None
    # How many worlds were deleted lately, for the world menu's way back to them.
    deleted_worlds: int = 0
    # A player's handouts not opened yet, counted on the journal's links.
    unread_handouts: int = 0

    def token_of(self, kind: str, world_id: str | None, ident: str) -> Token:
        """An NPC's, character's, place's, encounter's or treasure's token."""
        return self.tokens(kind, world_id, ident) if self.tokens else Token(kind)

    @property
    def readable(self) -> Callable[[str, Any], bool] | None:
        """The test links and lists hold entries to; None when everything is readable."""
        return self.reader.readable if self.reader is not None else None

    @property
    def home_world(self) -> str:
        """A player's one world, where their pages resolve when the address names none
        (documentation/PLAYER-VIEW.md); "" for the gamemaster or a player of several."""
        return (self.reader.home or "") if self.reader is not None else ""

    def can_read(self, category: str, entry: Any) -> bool:
        """Whether this reader may open ``entry``."""
        return self.reader is None or self.reader.readable(category, entry)

    def reads_book(self, code: str) -> bool:
        """Whether this reader may open the rulebook ``code``."""
        return self.reader is None or self.reader.reads_book(code)

    @property
    def lang(self) -> str:
        """The active UI language code."""
        return self.t.code

    @property
    def u(self) -> UnitFormat:
        """Distances and weights as the reader wants them, in the page's language."""
        units = self.identity.user.units if self.identity is not None else "feet"
        return UnitFormat(units, self.lang)


__all__ = ["Page"]
