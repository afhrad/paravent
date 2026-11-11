"""Login, logout, first-run setup, and switching the UI language.

Thin by design: credentials and sessions are :class:`~app.services.auth.AuthService`'s
business; these routes translate between it and forms, cookies and redirects.
"""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from htpy import Element

from app.dependencies import AuthDep, MaybeIdentityDep, PageDep, RegistryDep, SettingsDep
from app.models.gamestate import Role, User
from app.services.auth import COOKIE_NAME, AuthError, AuthService
from app.services.i18n import LANG_COOKIE
from app.services.ratelimit import login_keys
from app.store.registry import StoreRegistry
from app.ui.auth_pages import login_page, setup_page
from app.ui.context import Page

router = APIRouter(tags=["auth"])

# A language choice is a preference, not a credential: it outlives any session.
LANG_COOKIE_MAX_AGE = 365 * 24 * 3600


def safe_next(url: str | None) -> str:
    """Accept only a same-site path as a redirect destination.

    ``//evil.example`` and ``/\\evil.example`` are protocol-relative in browsers, so a
    leading slash alone is not enough.
    """
    if not url or not url.startswith("/") or url.startswith(("//", "/\\")):
        return ""
    return url


def home_for(role: Role) -> str:
    """Where a role lands after logging in."""
    return "/gm" if role is Role.GAMEMASTER else "/play"


# Set for one page when a user is sent back to the page they left, so ui.js brings back
# how far down it was. Read by script, so not HttpOnly; it carries nothing but "1".
RESUME_COOKIE = "paravent_resume"


def resume(registry: StoreRegistry, user: User) -> RedirectResponse:
    """Where a user lands after logging in: the page they left (app/services/places.py),
    marked as a return, or else their role's screen."""
    resumed = registry.places.resume(user.id, user.role)
    response = redirect(resumed or home_for(user.role))
    if resumed:
        response.set_cookie(RESUME_COOKIE, "1", max_age=60, samesite="lax", path="/")
    return response


def redirect(url: str) -> RedirectResponse:
    """A 303, so the browser follows a POST with a GET."""
    return RedirectResponse(url, status_code=303)


def html(page: object, status_code: int = 200) -> HTMLResponse:
    """Render an htpy element as a response."""
    return HTMLResponse(str(page), status_code=status_code)


def remember_language(response: Response, code: str, secure: bool) -> None:
    """Keep the language for the next visit, logged in or not."""
    response.set_cookie(
        LANG_COOKIE, code, max_age=LANG_COOKIE_MAX_AGE, samesite="lax", secure=secure, path="/"
    )


def _log_in(
    response: Response, auth: AuthService, token: str, language: str, secure: bool
) -> Response:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=auth.max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    # The account's language takes over; remembering it means the login screen
    # after a later logout is in the same language.
    remember_language(response, language, secure)
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_form(
    auth: AuthDep,
    registry: RegistryDep,
    identity: MaybeIdentityDep,
    page: PageDep,
    next: str | None = None,
    out: bool = False,
) -> Response:
    """Show the login form, or skip it when there is nothing to log in to or as."""
    if not auth.has_users():
        return redirect("/setup")
    if identity is not None:
        target = safe_next(next)
        return redirect(target) if target else resume(registry, identity.user)
    notice_text = page.t("login.logged_out") if out else None
    return html(login_page(page, notice_text=notice_text, next_url=safe_next(next)))


def _wait_text(page: Page, seconds: int) -> str:
    """ "Try again in 8 seconds" / "in 3 minutes"."""
    if seconds < 90:
        return page.t.plural("login.wait_seconds", seconds)
    return page.t.plural("login.wait_minutes", math.ceil(seconds / 60))


def _too_many(view: Element, seconds: int) -> HTMLResponse:
    response = html(view, status_code=429)
    response.headers["Retry-After"] = str(seconds)
    return response


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    auth: AuthDep,
    registry: RegistryDep,
    settings: SettingsDep,
    page: PageDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    """Check credentials; on success set the cookie and carry on where the user left
    off (app/services/places.py), or go to the role's screen.

    After repeated failures for the username or from the address, the next attempt
    waits (app/services/ratelimit.py): it is refused with 429 before the password is
    even checked, and the page says how long.
    """
    throttle = registry.login_throttle
    keys = login_keys(username, request.client.host if request.client else None)
    seconds = throttle.wait(keys)
    if seconds:
        view = login_page(
            page, error=_wait_text(page, seconds), username=username, next_url=safe_next(next)
        )
        return _too_many(view, seconds)
    user = auth.authenticate(username, password)
    if user is None:
        throttle.failed(keys)
        view = login_page(
            page, error=page.t("login.failed"), username=username, next_url=safe_next(next)
        )
        return html(view, status_code=401)
    throttle.succeeded(keys[:1])
    token = auth.start_session(user)
    target = safe_next(next)
    response = redirect(target) if target else resume(registry, user)
    return _log_in(response, auth, token, user.language, settings.cookie_secure)


@router.post("/logout")
async def logout(request: Request, auth: AuthDep) -> Response:
    """End the session server-side and drop the cookie. The language cookie stays."""
    auth.end_session(request.cookies.get(COOKIE_NAME))
    response = redirect("/login?out=1")
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/setup", response_class=HTMLResponse)
async def setup_form(auth: AuthDep, page: PageDep) -> Response:
    """First run only: the form that creates the gamemaster."""
    if auth.has_users():
        return redirect("/login")
    return html(setup_page(page))


@router.post("/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    registry: RegistryDep,
    auth: AuthDep,
    settings: SettingsDep,
    page: PageDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
) -> Response:
    """Create the gamemaster account, in the language the page is shown in, and log in.

    Refused once any user exists, so the open form cannot be used to mint a second
    gamemaster later.
    """
    if auth.has_users():
        return redirect("/login")
    throttle = registry.login_throttle
    keys = login_keys("", request.client.host if request.client else None)[1:]
    seconds = throttle.wait(keys)
    if seconds:
        view = setup_page(
            page,
            error=_wait_text(page, seconds),
            username=username,
            display_name=display_name,
        )
        return _too_many(view, seconds)
    try:
        if password != confirm:
            raise AuthError("error.password_mismatch")
        user = auth.create_user(username, password, Role.GAMEMASTER, display_name, page.lang)
    except AuthError as exc:
        throttle.failed(keys)
        view = setup_page(
            page, error=page.t.error(exc), username=username, display_name=display_name
        )
        return html(view, status_code=422)
    token = auth.start_session(user)
    response = redirect(home_for(user.role))
    return _log_in(response, auth, token, user.language, settings.cookie_secure)


@router.post("/language")
async def switch_language(
    registry: RegistryDep,
    settings: SettingsDep,
    identity: MaybeIdentityDep,
    lang: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    """Switch the UI language and return to the page it was switched on.

    Logged in, the choice is saved on the account, so it follows the user to another
    device, and sets the units with it (feet for English, metres otherwise). Either way
    it is kept in a cookie, so the login screen remembers it too. An unknown code
    changes nothing.
    """
    response = redirect(safe_next(next) or "/")
    if not registry.ui.supports(lang):
        return response
    if identity is not None:
        registry.auth.set_language(identity.user.id, lang)
    remember_language(response, lang, settings.cookie_secure)
    return response


__all__ = ["home_for", "html", "redirect", "resume", "router", "safe_next"]
