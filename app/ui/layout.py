"""The page skeleton every screen shares: document head, theme, language, and the shell.

Styling is the token CSS from the design reference (``static/css/tokens.css`` +
``components.css``), served as-is, plus ``app.css`` for what the app adds. Every color
comes from a token, so light and dark need no per-component rules
(documentation/DESIGN.md §2).

**The language selector is on every page**, signed in or not — the login screen of a
German table should not have to be read in English first. It is a ``<details>`` menu of
submit buttons: no JavaScript needed, and it scales past two languages.
"""

from __future__ import annotations

from htpy import (
    Element,
    Node,
    a,
    b,
    body,
    button,
    details,
    div,
    form,
    head,
    header,
    html,
    link,
    main,
    meta,
    nav,
    p,
    script,
    span,
    summary,
    title,
)
from markupsafe import Markup

from app import __version__
from app.config import STATIC_DIR
from app.models.gamestate import Role
from app.services.protocol_view import Filter
from app.services.themes import Theme
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon, sprite
from app.ui.nav import NAV, nav_href
from app.ui.palette import insert_palette, palette, search_trigger
from app.ui.protocol_pages import log_button, log_dialog, toast_layer
from app.ui.sound_panel import sound_button, sound_dialog

STYLESHEETS = (
    "/static/css/tokens.css",
    "/static/css/components.css",
    "/static/css/app.css",
    "/static/css/sheet.css",
)


def asset(href: str) -> str:
    """A ``/static/`` URL with the file's modification time, so a changed file is fetched.

    Without it a browser may keep using a cached stylesheet or script after an update,
    since the static files are served without cache headers.
    """
    try:
        version = (STATIC_DIR / href.removeprefix("/static/")).stat().st_mtime_ns
    except OSError:
        return href
    return f"{href}?v={version:x}"


# Runs before first paint so a stored dark preference never flashes light. Storage can
# throw (private mode, blocked site data); the page must render either way. A theme that
# draws one mode only fixes the page to it (``data-theme-fixed``).
_THEME_BOOT = Markup(
    "try{var r=document.documentElement,t=localStorage.getItem('pv-theme');"
    "if(!r.hasAttribute('data-theme-fixed')&&(t==='light'||t==='dark'))"
    "r.setAttribute('data-theme',t)}"
    "catch(e){}"
)
PARAVENT_COLOR = "#4560DC"


def _theme_head(theme: Theme | None) -> Node:
    """The browser's frame colour and the theme's stylesheets, after Paravent's own."""
    if theme is None:
        return meta(name="theme-color", content=PARAVENT_COLOR)
    colours = theme.manifest.theme_color
    return [
        meta(name="theme-color", content=colours.light, media="(prefers-color-scheme: light)"),
        meta(name="theme-color", content=colours.dark, media="(prefers-color-scheme: dark)"),
        [link(rel="stylesheet", href=theme.url(name)) for name in theme.stylesheets],
    ]


def _sprite(theme: Theme | None) -> Markup:
    """The icon sprite: the theme's when it has a complete one."""
    text = theme.sprite() if theme is not None else None
    return Markup(text) if text is not None else sprite()


ROLE_KEY = {Role.GAMEMASTER: "role.gamemaster", Role.PLAYER: "role.player"}


def document(
    page: Page,
    page_title: str,
    content: Node,
    *,
    body_class: str = "",
    scripts: tuple[str, ...] = (),
) -> Element:
    """A complete HTML document around ``content``, in the page's language and the
    reader's theme (documentation/THEMES.md)."""
    theme = page.theme
    fixed = theme.fixed_mode if theme is not None else None
    return html(
        lang=page.lang,
        data_look=theme.id if theme is not None else None,
        data_theme=fixed,
        data_theme_fixed="" if fixed else None,
    )[
        head[
            meta(charset="utf-8"),
            meta(
                name="viewport",
                content="width=device-width, initial-scale=1, viewport-fit=cover",
            ),
            meta(name="color-scheme", content="light dark"),
            title[f"{page_title} · Paravent" if page_title else "Paravent"],
            script[_THEME_BOOT],
            link(rel="icon", href="/favicon.ico", sizes="48x48"),
            link(rel="icon", href="/favicon.svg", type="image/svg+xml"),
            link(rel="apple-touch-icon", href="/apple-touch-icon.png"),
            link(rel="manifest", href="/manifest.webmanifest"),
            # Full screen from the home screen; iOS still reads its own tags for that.
            meta(name="mobile-web-app-capable", content="yes"),
            meta(name="apple-mobile-web-app-capable", content="yes"),
            meta(name="apple-mobile-web-app-title", content="Paravent"),
            meta(name="apple-mobile-web-app-status-bar-style", content="default"),
            [link(rel="stylesheet", href=asset(href)) for href in STYLESHEETS],
            _theme_head(theme),
            script(src=asset("/static/js/ui.js"), defer=True),
            script(src=asset("/static/js/sound.js"), defer=True),
            script(src=asset("/static/js/tooltips.js"), defer=True),
            script(src=asset("/static/js/palette.js"), defer=True),
            [script(src=asset(src), defer=True) for src in scripts],
        ],
        body(class_=body_class or None)[_sprite(theme), content],
    ]


def _icon_button(label: str, icon_name: str, **attrs: str) -> Element:
    """A toggle button that shows only its icon, named for screen readers and on hover."""
    return button(type="button", aria_label=label, title=label, **attrs)[icon(icon_name)]


def theme_toggle(page: Page) -> Element:
    """Light / Dark as a sun and a moon. ``ui.js`` wires it up and remembers the choice;
    until one is made the device's own setting shows."""
    t = page.t
    return div(class_="themetoggle icontoggle", role="group", aria_label=t("theme.label"))[
        _icon_button(t("theme.light"), "sun", data_set="light", aria_pressed="false"),
        _icon_button(t("theme.dark"), "moon", data_set="dark", aria_pressed="false"),
    ]


def sound_toggle(page: Page) -> Element:
    """Dice sounds on or off, for this device. ``sound.js`` remembers the choice."""
    t = page.t
    return div(
        class_="themetoggle icontoggle soundtoggle", role="group", aria_label=t("sound.label")
    )[
        _icon_button(t("sound.on"), "volume-2", data_sound="on", aria_pressed="true"),
        _icon_button(t("sound.off"), "volume-x", data_sound="off", aria_pressed="false"),
    ]


def _toast_switch(page: Page) -> Element | None:
    """Which log entries pop up (documentation/PROTOCOL.md §6): mine, all, or none.
    Saved on the account; three icons like the theme, posted without JavaScript too."""
    if page.identity is None:
        return None
    t = page.t
    current = page.identity.user.toasts
    mine = "toasts.mine_gm" if page.identity.is_gamemaster else "toasts.mine"
    choices = (
        ("mine", "bell", mine),
        ("all", "users", "toasts.all"),
        ("off", "bell-off", "toasts.off"),
    )
    return form(
        method="post",
        action="/log/toasts",
        class_="themetoggle icontoggle toastswitch",
        role="group",
        aria_label=t("toasts.label"),
    )[
        hidden("next", page.path),
        [
            button(
                type="submit",
                name="toasts",
                value=value,
                aria_label=t(key),
                title=t(key),
                aria_pressed="true" if value == current else "false",
            )[icon(icon_name)]
            for value, icon_name, key in choices
        ],
    ]


def _language_switch(page: Page) -> Element:
    """The languages as their codes (DE / EN) side by side; the units follow the choice."""
    t = page.t
    return form(
        method="post",
        action="/language",
        class_="themetoggle langswitch",
        role="group",
        aria_label=t("language.label"),
    )[
        hidden("next", page.path),
        [
            button(
                type="submit",
                name="lang",
                value=lang.code,
                lang=lang.code,
                title=lang.name,
                aria_label=lang.name,
                aria_pressed="true" if lang.code == page.lang else "false",
            )[lang.code.upper()]
            for lang in t.languages
        ],
    ]


def _language_choices(page: Page, *, cls: str) -> Element:
    """Every language as a submit button; posts the choice and returns to this page."""
    t = page.t
    return form(method="post", action="/language", class_=cls)[
        hidden("next", page.path),
        span(class_="eyebrow langmenu-head")[t("language.label")],
        [
            button(
                type="submit",
                name="lang",
                value=lang.code,
                lang=lang.code,
                aria_current="true" if lang.code == page.lang else None,
            )[
                span[lang.name],
                span(class_="langmenu-tag")[lang.code.upper()],
            ]
            for lang in t.languages
        ],
    ]


def language_menu(page: Page) -> Element:
    """The language selector for pages seen before logging in."""
    t = page.t
    languages = t.languages
    current = next((lang for lang in languages if lang.code == page.lang), languages[0])
    return details(class_="langmenu")[
        summary(class_="btn btn-ghost btn-sm", aria_label=t("language.current", name=current.name))[
            icon("languages"),
            span(class_="langmenu-code")[current.code.upper()],
        ],
        _language_choices(page, cls="langmenu-list"),
    ]


# The gamemaster's settings: (label key, href, icon). Pages that set the game up rather
# than run it, kept out of the bar so it holds only what is used at the table.
SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("nav.players", "/gm/players", "users"),
    ("sound.library", "/gm/sounds", "volume-2"),
    ("naming.title", "/gm/names", "quote"),
    ("glossary.title", "/gm/glossary", "languages"),
    ("translations.title", "/gm/translations", "scroll"),
    ("corpus.title", "/gm/corpus", "shield-check"),
    ("backup.title", "/gm/backups", "vault"),
    ("mcp.title", "/gm/mcp", "sparkles"),
    ("themes.title", "/themes", "palette"),
)


def settings_menu(page: Page) -> Element | None:
    """The gear between search and the user menu; the gamemaster's only, since a
    player has nothing to set up. Styled and closed like the user menu."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    t = page.t
    path = page.path.split("?", 1)[0]

    def current(href: str) -> bool:
        return path == href or path.startswith(href + "/")

    inside = any(current(href) for _, href, _ in SETTINGS)
    return details(class_="usermenu settingsmenu")[
        summary(
            class_="btn btn-ghost usermenu-button" + (" is-current" if inside else ""),
            aria_label=t("settings.menu"),
            title=t("settings.menu"),
        )[icon("settings", "md")],
        div(class_="usermenu-panel")[
            div(class_="usermenu-section usermenu-first")[
                [
                    a(
                        href=href,
                        class_="usermenu-link",
                        aria_current="page" if current(href) else None,
                    )[icon(icon_name, "sm"), span[t(key)]]
                    for key, href, icon_name in SETTINGS
                ]
            ],
        ],
    ]


def user_menu(page: Page, name: str, username: str) -> Element:
    """Who is logged in, as a menu: one row of icon switches (language, which sets the
    units too, colour theme, dice sounds), a new character, and log out.

    A ``<details>`` like the language menu: it opens without JavaScript, and
    ``ui.js`` only adds closing on an outside click or Escape.
    """
    t = page.t
    return details(class_="usermenu")[
        summary(class_="btn btn-ghost usermenu-button", aria_label=t("user.menu", name=name))[
            icon("circle-user", "md"),
            span(class_="usermenu-name")[name],
            icon("chevron-down", "sm", muted=True),
        ],
        div(class_="usermenu-panel")[
            div(class_="usermenu-who")[
                span(class_="avatar", aria_hidden="true")[initials(name)],
                div[b[name], span[f"@{username}"]],
            ],
            div(class_="usermenu-section usermenu-switches")[
                _language_switch(page),
                theme_toggle(page),
                sound_toggle(page),
                _toast_switch(page),
            ],
            div(class_="usermenu-section")[
                a(href="/chargen/new", class_="usermenu-link")[
                    icon("user-plus", "sm"), span[t("chargen.title")]
                ],
                a(href="/themes", class_="usermenu-link")[
                    icon("palette", "sm"), span[t("themes.title")]
                ],
                a(href="/gm/creatures", class_="usermenu-link")[
                    icon("skull", "sm"), span[t("creatures.heading")]
                ]
                if page.identity is not None and page.identity.is_gamemaster
                else None,
            ],
            form(method="post", action="/logout", class_="usermenu-section usermenu-logout")[
                button(type="submit", class_="btn btn-ghost")[
                    icon("log-out"), span[t("shell.logout")]
                ]
            ],
            version_line(page),
        ],
    ]


def version_line(page: Page) -> Element:
    """ "Paravent 0.1.0" — which server this is, small and out of the way."""
    return p(class_="app-version mono", title=page.t("shell.version_title"))[
        f"Paravent {__version__}"
    ]


def brand() -> Element:
    """The wordmark: Paravent's mark in the text colour, so it fits every theme."""
    return span(class_="brand")[icon("paravent", "md"), span["Paravent"]]


def initials(name: str) -> str:
    """Up to two initials for the avatar."""
    parts = [p for p in name.split() if p]
    letters = "".join(p[0] for p in parts[:2]) or name[:1]
    return letters.upper()


def public_shell(page: Page, page_title: str, content: Node) -> Element:
    """The frame for pages seen before logging in: language and theme only."""
    return document(
        page,
        page_title,
        div(class_="auth")[
            div(class_="auth-top")[language_menu(page), theme_toggle(page)],
            content,
            version_line(page),
        ],
        body_class="auth-body",
    )


def _player_bar(page: Page) -> Element:
    """A player's bottom bar on a phone: the sections. A new character is in the user
    menu."""
    t = page.t
    path = page.path.split("?", 1)[0]
    return nav(class_="player-bar", aria_label=t("nav.label"))[
        [
            a(
                href=nav_href(item, path, page.nav_memory),
                aria_current="page" if item.contains(path) else None,
            )[icon(item.icon, "lg"), span[t(item.key)]]
            for item in NAV[Role.PLAYER]
        ]
    ]


def _nav(page: Page, role: Role) -> Element:
    t = page.t
    path = page.path.split("?", 1)[0]
    return nav(class_="shell-nav", aria_label=t("nav.label"))[
        [
            a(
                href=nav_href(item, path, page.nav_memory),
                aria_current="page" if item.contains(path) else None,
            )[icon(item.icon), span[t(item.key)]]
            for item in NAV[role]
        ]
    ]


def app_shell(
    page: Page,
    page_title: str,
    content: Node,
    *,
    extra_scripts: tuple[str, ...] = (),
    main_class: str = "shell-main",
    world_id: str | None = None,
    palette_layer: bool = True,
    player_bar: bool = True,
    log_scope: Filter | None = None,
    insert_layer: bool = False,
) -> Element:
    """The signed-in frame: navigation on the left, search, the user menu on the far right.

    Role decides the composition, not the components (documentation/DESIGN.md §8):
    the gamemaster and player screens share this shell and differ in what they put in it.
    ``world_id`` scopes the ⌘K palette to that world's books, on pages that have one.
    ``palette_layer=False`` leaves the layer out, for the search page that is one already.
    On a phone a player's sections move to a bottom bar;
    ``player_bar=False`` leaves it out for pages with a bottom bar of their own (the sheet,
    the wizard). The log's button sits in the top bar of every page, before search
    (documentation/PROTOCOL.md §5); ``log_scope`` is what its panel opens on — the
    page's fight, or the character whose sheet it is. ``insert_layer`` adds the Markdown
    editors' insert pop-up (app/ui/palette.py), on pages with an editor.
    """
    identity = page.identity
    if identity is None:
        raise ValueError("app_shell needs a logged-in identity")
    t = page.t
    user = identity.user
    name = user.display_name or user.username
    # The role is shown as the colour of the bar's top edge — orange for the gamemaster,
    # blue for a player — and spoken to screen readers, since colour alone is not enough.
    role_class = "role-gm" if identity.is_gamemaster else "role-player"
    bar = player_bar and not identity.is_gamemaster
    return document(
        page,
        page_title,
        div(class_=f"shell {role_class}-shell" + (" with-player-bar" if bar else ""))[
            header(class_=f"topbar shell-bar {role_class}")[
                span(class_="sr-only")[t(ROLE_KEY[user.role])],
                # On a phone, where it shows: the logo alone, to leave the row room.
                a(href="/play", class_="shell-home", aria_label=t("nav.characters"))[
                    icon("paravent", "xl")
                ]
                if not identity.is_gamemaster
                else None,
                _nav(page, user.role),
                div(class_="shell-spacer"),
                sound_button(page),
                log_button(page),
                search_trigger(page),
                settings_menu(page),
                user_menu(page, name, user.username),
            ],
            main(class_=main_class, id="main")[content],
            _player_bar(page) if bar else None,
            palette(page, world_id) if palette_layer else None,
            insert_palette(page, world_id) if insert_layer else None,
            log_dialog(page, log_scope or Filter()),
            sound_dialog(page),
            toast_layer(page),
        ],
        scripts=(
            *extra_scripts,
            *(("/static/js/complete.js",) if insert_layer else ()),
            "/static/js/protocol.js",
            *(("/static/js/sound-panel.js",) if identity.is_gamemaster else ()),
        ),
    )


__all__ = [
    "NAV",
    "ROLE_KEY",
    "app_shell",
    "asset",
    "brand",
    "document",
    "initials",
    "language_menu",
    "public_shell",
    "settings_menu",
    "theme_toggle",
    "user_menu",
]
