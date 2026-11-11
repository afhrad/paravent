"""What every page needs to know about the request it is rendering for."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services.auth import Identity
from app.services.i18n import Translator
from app.services.player_view import Reader
from app.services.themes import Theme
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
