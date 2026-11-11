"""Where each user left the app, so that logging in again carries on from there.

The navigation already remembers the page each section was left on (app/ui/nav.py),
but in a cookie: one browser's memory, shared by whoever uses it and lost with it. This
keeps the same memory per user on the server, in ``library/places.json``, together with
the last page read and how each character sheet was shown (tab, phone view, spell-level
filter). Logging in, or opening the installed app at ``/``, goes back to that page; the
sheet opens on the view it was left on.
"""

from __future__ import annotations

from app.models.gamestate import Place, Role, SheetView
from app.services.auth import AuthService
from app.store.gamestate import GameStateStore
from app.ui.nav import NAV, checked_memory, is_place, remember


class PlaceService:
    """Each user's :class:`Place`, read and written as they move around."""

    def __init__(self, store: GameStateStore, auth: AuthService) -> None:
        self._store = store
        self._auth = auth

    def get(self, user_id: str) -> Place:
        """The user's place; a blank one, not yet stored, for someone never seen."""
        return self._store.places.find(user_id) or Place(id=user_id)

    def nav(self, user_id: str) -> dict[str, str]:
        """The page each navigation section was left on, checked as the cookie is."""
        return checked_memory(self.get(user_id).nav)

    def resume(self, user_id: str, role: Role) -> str | None:
        """The page to carry on from, if it is still one this role can read."""
        last = self.get(user_id).last
        return last if last and is_place(NAV[role], last) else None

    def visited(self, user_id: str, role: Role, url: str, status: int) -> None:
        """Record that a page at ``url`` answered ``status`` for the user.

        The same rule as the cookie: a page that loaded is remembered, one that has gone
        is forgotten, a redirect or a step in a form changes nothing. It also becomes
        the last page when it lies in one of the role's sections.
        """
        place = self.get(user_id)
        nav = remember(place.nav, url, status)
        last = place.last
        if 200 <= status < 300 and is_place(NAV[role], url):
            last = url
        elif status >= 400 and last == url:
            last = ""
        if nav != place.nav or last != place.last:
            self._store.places.upsert(place.model_copy(update={"nav": nav, "last": last}))

    def visited_by_token(self, token: str | None, url: str, status: int) -> None:
        """:meth:`visited` for whoever the session cookie names; nobody, nothing."""
        identity = self._auth.identify(token)
        if identity is not None:
            self.visited(identity.user.id, identity.role, url, status)

    def sheet_view(self, user_id: str, character_id: str) -> SheetView:
        """How the user last saw a character's sheet; empty fields where never set."""
        return self.get(user_id).sheets.get(character_id, SheetView())

    def set_sheet_view(
        self, user_id: str, character_id: str, tab: str, view: str, filter: str
    ) -> None:
        """Keep how a character's sheet is shown, for the next time it opens."""
        place = self.get(user_id)
        shown = SheetView(tab=tab, view=view, filter=filter)
        if place.sheets.get(character_id) == shown:
            return
        sheets = {**place.sheets, character_id: shown}
        self._store.places.upsert(place.model_copy(update={"sheets": sheets}))

    def forget(self, user_id: str) -> None:
        """Drop a user's place, as when the account is deleted."""
        if self._store.places.find(user_id) is not None:
            self._store.places.delete(user_id)


__all__ = ["PlaceService"]
